#include "research_input.hpp"
#include "statistics.hpp"
#include "parallel.hpp"
#include <algorithm>

namespace {
constexpr const char *capsule_name = "factorpool.research_input.v3";
void destroy_input(PyObject *capsule) {
  delete static_cast<fp::ResearchInput *>(
      PyCapsule_GetPointer(capsule, capsule_name));
}
} // namespace
namespace fp {
ResearchInput::ResearchInput(PyObject *factor, PyObject *daily_returns,
                             PyObject *membership, PyObject *weights)
    : factors(matrix(factor)), returns(matrix(daily_returns)),
      members(membership && membership != Py_None ? matrix(membership, true) : Matrix{}),
      alpha_weights(weights && weights != Py_None ? matrix(weights) : Matrix{}) {
  if (factors.size() > returns.size() ||
      factors.columns() != returns.columns() ||
      (membership && membership != Py_None &&
       (members.size() != factors.size() ||
        members.columns() != factors.columns())))
    throw std::invalid_argument("Research matrix axes differ");
  if (weights && weights != Py_None) {
    if (alpha_weights.size() != factors.size() ||
        alpha_weights.columns() != factors.columns())
      throw std::invalid_argument("Alpha weight matrix axes differ");
    for (std::size_t t = 0; t < alpha_weights.size(); ++t) {
      std::size_t nan_count = 0;
      long double gross = 0;
      for (std::size_t n = 0; n < alpha_weights.columns(); ++n) {
        const auto value = alpha_weights[t][n];
        if (std::isnan(value)) {
          ++nan_count;
          continue;
        }
        if (!std::isfinite(value))
          throw std::invalid_argument("Alpha weights must be finite or an entirely NaN row");
        if (value != 0 && !eligible(t, n))
          throw std::invalid_argument("Alpha weights must be zero outside the research universe");
        gross += std::abs(static_cast<long double>(value));
      }
      if (nan_count && nan_count != alpha_weights.columns())
        throw std::invalid_argument("Partial NaN alpha weight rows are not supported");
      if (gross > 1.L + 1e-12L)
        throw std::invalid_argument("Alpha weights exceed unit gross exposure");
    }
  }
  parallel_for(returns.size(), returns.columns(), [&](std::size_t t) {
    for (auto &r : returns[t])
      if (!std::isfinite(r) || r < -1)
        r = missing;
  });
  order.resize(factors.size());
  parallel_for(factors.size(), factors.columns(), [&](std::size_t t) {
    auto &sorted = order[t];
    sorted.reserve(factors[t].size());
    for (std::size_t n = 0; n < factors[t].size(); ++n)
      if (std::isfinite(factors[t][n]) && eligible(t, n))
        sorted.push_back(n);
    std::stable_sort(sorted.begin(), sorted.end(), [&](auto a, auto b) {
      return factors[t][a] < factors[t][b];
    });
  });
}
void ResearchInput::prepare_prefixes() const {
  if (!log_prefix_.empty())
    return;
  const auto rows = returns.size(), columns = returns.columns();
  if (rows == std::numeric_limits<std::size_t>::max() ||
      (columns && rows + 1 > log_prefix_.max_size() / columns))
    throw std::invalid_argument("Research matrix is too large");
  const auto cells = (rows + 1) * columns;
  std::vector<long double> logs(cells, 0);
  std::vector<std::size_t> gaps(cells, 0), zeros(cells, 0);
  constexpr std::size_t tile = 256;
  parallel_for((columns + tile - 1) / tile, rows * tile, [&](std::size_t block) {
    const auto first = block * tile, last = std::min(columns, first + tile);
    for (std::size_t t = 0; t < rows; ++t)
      for (auto n = first; n < last; ++n) {
      const auto before = t * columns + n, after = before + columns;
      const auto r = returns[t][n];
      logs[after] = logs[before];
      gaps[after] = gaps[before];
      zeros[after] = zeros[before];
      if (!std::isfinite(r))
        ++gaps[after];
      else if (r == -1)
        ++zeros[after];
      else
        logs[after] += std::log1p(static_cast<long double>(r));
      }
  });
  log_prefix_ = std::move(logs);
  missing_prefix_ = std::move(gaps);
  zero_prefix_ = std::move(zeros);
}
const Matrix &ResearchInput::forward(int h) const {
  if (h < 1 || h > 252)
    throw std::invalid_argument("Horizon must be 1..252");
  if (const auto it = forwards_.find(h); it != forwards_.end())
    return it->second;
  Matrix result(returns.size(), returns.columns());
  const auto days = static_cast<std::size_t>(h), columns = returns.columns();
  if (days < returns.size()) {
    if (h == 1) {
      parallel_for(returns.size() - 1, columns, [&](std::size_t t) {
        std::copy(returns[t + 1].begin(), returns[t + 1].end(), result[t].begin());
      });
    } else {
      prepare_prefixes();
      parallel_for(returns.size() - days, columns, [&](std::size_t t) {
        for (std::size_t n = 0; n < columns; ++n) {
          const auto begin = (t + 1) * columns + n,
                     end = (t + 1 + days) * columns + n;
          if (missing_prefix_[end] != missing_prefix_[begin])
            continue;
          if (zero_prefix_[end] != zero_prefix_[begin])
            result[t][n] = -1;
          else {
            const auto r = static_cast<double>(
                std::expm1(log_prefix_[end] - log_prefix_[begin]));
            if (std::isfinite(r))
              result[t][n] = r;
          }
        }
      });
    }
  }
  return forwards_.emplace(h, std::move(result)).first->second;
}
void ResearchInput::prepare_return_orders(int h) const {
  const auto &values = forward(h);
  auto [entry, inserted] = return_orders_.try_emplace(h);
  auto &cache = entry->second;
  if (inserted) {
    cache.rows.resize(values.size());
    cache.ready.resize(values.size(), 0);
  }
}
void ResearchInput::prepare_correlations(int h, int minimum) const {
  prepare_return_orders(h);
  auto [entry, inserted] = correlations_.try_emplace(std::pair{h, minimum});
  if (inserted) {
    entry->second.rows.resize(factors.size());
    entry->second.ready.resize(factors.size(), 0);
  }
}
const ResearchInput::CorrelationValues &ResearchInput::correlation_row(
    int h, int minimum, std::size_t row) const {
  auto &cache = correlations_.at(std::pair{h, minimum});
  if (!cache.ready.at(row)) {
    const auto &values = forward(h);
    std::vector<double> a, b;
    std::vector<std::size_t> symbols;
    a.reserve(factors.columns());
    b.reserve(factors.columns());
    symbols.reserve(factors.columns());
    for (std::size_t n = 0; n < factors.columns(); ++n) {
      if (std::isfinite(factors[row][n]) && eligible(row, n) &&
          std::isfinite(values[row][n])) {
        a.push_back(factors[row][n]);
        b.push_back(values[row][n]);
        symbols.push_back(n);
      }
    }
    auto &result = cache.rows[row];
    result.pairs = symbols.size();
    result.ic = correlation(a, b, minimum);
    result.rank_ic = a.size() >= static_cast<std::size_t>(minimum)
        ? correlation(paired_factor_ranks(*this, row, symbols),
                      paired_return_ranks(h, row, symbols), minimum)
        : missing;
    // Each parallel worker owns a different factor row. Publication and cache
    // allocation remain on the Python caller, like the return-order cache.
    cache.ready[row] = 1;
  }
  return cache.rows[row];
}
std::vector<double> ResearchInput::paired_return_ranks(
    int h, std::size_t row, const std::vector<std::size_t> &symbols) const {
  if (symbols.empty())
    return {};
  const auto &values = forward(h);
  // The parent cache is allocated before any independent row work starts.
  // Each parallel caller below owns a different return row in this phase.
  auto &cache = return_orders_.at(h);
  auto &order = cache.rows[row];
  if (!cache.ready[row]) {
    order.reserve(values.columns());
    for (std::size_t n = 0; n < values.columns(); ++n)
      if (std::isfinite(values[row][n]))
        order.push_back(n);
    std::sort(order.begin(), order.end(),
              [&](auto a, auto b) { return values[row][a] < values[row][b]; });
    cache.ready[row] = 1;
  }
  // The membership/factor mask belongs to the original signal, not this
  // possibly lagged return row. Filtering before assigning ranks preserves
  // sample-dependent ties and the original paired-symbol order exactly.
  const auto absent = std::numeric_limits<std::size_t>::max();
  std::vector<std::size_t> positions(values.columns(), absent), filtered;
  filtered.reserve(symbols.size());
  for (std::size_t i = 0; i < symbols.size(); ++i)
    positions[symbols[i]] = i;
  for (auto n : order)
    if (positions[n] != absent)
      filtered.push_back(n);
  std::vector<double> ranks(symbols.size());
  for (std::size_t i = 0; i < filtered.size();) {
    auto j = i + 1;
    while (j < filtered.size() && values[row][filtered[i]] == values[row][filtered[j]])
      ++j;
    for (auto k = i; k < j; ++k)
      ranks[positions[filtered[k]]] = (i + j - 1) / 2.0 + 1;
    i = j;
  }
  return ranks;
}
InputRef::InputRef(PyObject *factor, PyObject *daily_returns, PyObject *membership) {
  if (PyCapsule_IsValid(factor, capsule_name)) {
    if (daily_returns != Py_None || (membership && membership != Py_None))
      throw std::invalid_argument(
          "Prepared research input already owns all matrices");
    value = static_cast<ResearchInput *>(PyCapsule_GetPointer(factor, capsule_name));
  } else {
    owned = std::make_unique<ResearchInput>(factor, daily_returns, membership);
    value = owned.get();
  }
}
std::vector<double> paired_factor_ranks(const ResearchInput &input, std::size_t t,
                                      const std::vector<std::size_t> &symbols) {
  // Filter the cached order, then rerank the actual paired sample. A missing
  // future return changes tie sizes, so reusing full-universe ranks is incorrect.
  const auto absent = std::numeric_limits<std::size_t>::max();
  std::vector<std::size_t> positions(input.factors[t].size(), absent), filtered;
  filtered.reserve(symbols.size());
  for (std::size_t i = 0; i < symbols.size(); ++i)
    positions[symbols[i]] = i;
  for (auto n : input.order[t])
    if (positions[n] != absent)
      filtered.push_back(n);
  std::vector<double> ranks(symbols.size());
  for (std::size_t i = 0; i < filtered.size();) {
    auto j = i + 1;
    while (j < filtered.size() &&
           input.factors[t][filtered[i]] == input.factors[t][filtered[j]])
      ++j;
    for (auto k = i; k < j; ++k)
      ranks[positions[filtered[k]]] = (i + j - 1) / 2.0 + 1;
    i = j;
  }
  return ranks;
}
} // namespace fp
PyObject *prepare_research(PyObject *, PyObject *args) {
  try {
    PyObject *factor, *daily_returns, *members, *weights = nullptr;
    if (!PyArg_ParseTuple(args, "OOO|O", &factor, &daily_returns, &members, &weights))
      return nullptr;
    auto input = std::make_unique<fp::ResearchInput>(factor, daily_returns, members, weights);
    auto *capsule = PyCapsule_New(input.get(), capsule_name, destroy_input);
    if (capsule)
      input.release();
    return capsule;
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
PyObject *ic_series(PyObject *, PyObject *args) {
  using namespace fp;
  try {
    PyObject *prepared;
    int h, minimum, signal_stride = 1;
    if (!PyArg_ParseTuple(args, "Oii|i", &prepared, &h, &minimum, &signal_stride))
      return nullptr;
    if (h < 1 || h > 252 || minimum < 2 || signal_stride < 1)
      throw std::invalid_argument("Invalid correlation options");
    InputRef holder(prepared, Py_None, nullptr);
    const auto &input = *holder.value;
    const auto &f = input.factors;
    input.prepare_correlations(h, minimum);
    const auto rows = f.size() ? 1 + (f.size() - 1) / signal_stride : 0;
    std::vector<double> ics(rows), rankics(rows);
    parallel_for(rows, f.columns(), [&](std::size_t row) {
      const auto t = row * signal_stride;
      const auto &metrics = input.correlation_row(h, minimum, t);
      ics[row] = metrics.ic;
      rankics[row] = metrics.rank_ic;
    });
    Owned out{PyDict_New()};
    put(out.p, "ic", list(ics));
    put(out.p, "rank_ic", list(rankics));
    return Py_NewRef(out.p);
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
