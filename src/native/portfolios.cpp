#include "analysis.hpp"
#include "statistics.hpp"
#include "research_input.hpp"
#include <algorithm>
#include <memory>
#include <string>
namespace {
using namespace fp;
struct Holding {
  std::size_t symbol;
  double weight, growth = 1;
};
struct Strategy {
  std::string name;
  std::vector<Holding> previous;
  std::vector<double> pnl, book_pnl, turnover, positions, concentration;
  std::vector<double> period_returns, period_turnover;
  bool seen = false, previous_valid = false;
  explicit Strategy(std::string value) : name(std::move(value)) {}
};

std::vector<std::size_t> trace_signals(PyObject *requested,
                                     std::size_t factor_rows,
                                     std::size_t return_rows,
                                     std::size_t symbols, int horizon) {
  std::vector<std::size_t> indices;
  if (!requested || requested == Py_None)
    return indices;
  if (!PyList_Check(requested) || PyList_GET_SIZE(requested) > 5)
    throw std::invalid_argument(
        "trace_signal_indices must be a list of at most 5 integers");
  const auto length = PyList_GET_SIZE(requested);
  if (length)
    indices.reserve(static_cast<std::size_t>(length));
  for (Py_ssize_t i = 0; i < length; ++i) {
    auto *item = PyList_GET_ITEM(requested, i);
    if (!PyLong_Check(item) || PyBool_Check(item))
      throw std::invalid_argument("trace signal indices must be integers");
    const auto index = PyLong_AsUnsignedLongLong(item);
    if (PyErr_Occurred()) {
      PyErr_Clear();
      throw std::invalid_argument("trace signal index is outside the factor axis");
    }
    if (index >= factor_rows)
      throw std::invalid_argument("trace signal index is outside the factor axis");
    indices.push_back(static_cast<std::size_t>(index));
  }
  std::sort(indices.begin(), indices.end());
  indices.erase(std::unique(indices.begin(), indices.end()), indices.end());
  constexpr std::size_t limit = 50000;
  std::size_t rows = 0;
  for (auto signal : indices) {
    if (signal % static_cast<std::size_t>(horizon) != 0)
      continue;
    const auto days = std::min(static_cast<std::size_t>(horizon),
                               return_rows - signal - 1);
    if (days && symbols > (limit - rows) / days)
      throw std::invalid_argument(
          "Requested PnL trace exceeds 50,000 symbol-day rows; narrow the signals, horizon or universe");
    rows += days * symbols;
  }
  return indices;
}

// Allocated only for a selected Alpha rebalance. Growth and contribution values
// are captured in the existing holding loop; this is not another valuation pass.
struct TraceState {
  std::vector<double> target, before, contribution;
  std::vector<const char *> errors;
  double gross = 0, net = 0;
  explicit TraceState(const std::vector<double> &weights)
      : target(weights), before(weights.size(), missing),
        contribution(weights.size(), missing), errors(weights.size(), nullptr) {
    long double absolute = 0, signed_sum = 0;
    for (auto weight : target) {
      absolute += std::abs(static_cast<long double>(weight));
      signed_sum += weight;
    }
    gross = static_cast<double>(absolute);
    net = static_cast<double>(signed_sum);
  }
};

PyObject *trace_day(const ResearchInput &input, std::size_t signal,
                    std::size_t valuation, long double mean, bool usable,
                    const TraceState &state, double book_pnl) {
  Owned day{PyDict_New()}, rows{PyList_New(state.target.size())};
  if (!day.p || !rows.p)
    throw std::bad_alloc();
  const char *day_status = usable ? "ok" : "no_valid_weights";
  if (usable && !std::isfinite(book_pnl)) {
    day_status = "nonfinite_pnl";
    for (std::size_t n = 0; n < state.target.size(); ++n)
      if (state.target[n] != 0 && state.errors[n]) {
        day_status = state.errors[n];
        break;
      }
  }
  for (std::size_t n = 0; n < state.target.size(); ++n) {
    const auto raw = input.factors[signal][n], weight = state.target[n];
    const bool member = input.eligible(signal, n);
    double effective = missing, contribution = missing;
    const char *status = "no_valid_weights";
    if (usable && weight == 0) {
      effective = contribution = 0;
      status = !member ? "outside_universe"
               : input.alpha_weights.empty() && !std::isfinite(raw)
                   ? "nonfinite_alpha" : "zero_weight";
    } else if (usable) {
      effective = static_cast<double>(static_cast<long double>(weight) * state.before[n]);
      contribution = state.contribution[n];
      status = state.errors[n] ? state.errors[n] : "ok";
    }
    Owned row{PyDict_New()};
    put(row.p, "symbol_index", PyLong_FromSize_t(n));
    put(row.p, "raw_alpha", number(raw));
    put(row.p, "member", PyBool_FromLong(member));
    put(row.p, "neutralized",
        number(input.alpha_weights.empty() && member && std::isfinite(raw)
                   ? static_cast<double>(static_cast<long double>(raw) - mean)
                   : missing));
    put(row.p, "target_weight", number(usable ? weight : missing));
    put(row.p, "effective_weight_before_return", number(effective));
    put(row.p, "return", number(input.returns[valuation][n]));
    put(row.p, "pnl_fraction", number(contribution));
    put(row.p, "status", PyUnicode_FromString(status));
    PyList_SET_ITEM(rows.p, n, Py_NewRef(row.p));
  }
  put(day.p, "signal_index", PyLong_FromSize_t(signal));
  put(day.p, "valuation_index", PyLong_FromSize_t(valuation));
  put(day.p, "pnl_fraction", number(book_pnl));
  put(day.p, "target_gross", number(usable ? state.gross : missing));
  put(day.p, "target_net", number(usable ? state.net : missing));
  put(day.p, "status", PyUnicode_FromString(day_status));
  put(day.p, "rows", Py_NewRef(rows.p));
  return Py_NewRef(day.p);
}
} // namespace
PyObject *portfolio_report(PyObject *, PyObject *args) {
  try {
    PyObject *factor, *returns, *members, *requested_trace = Py_None;
    int groups, h, annual_days;
    if (!PyArg_ParseTuple(args, "OOOiii|O", &factor, &returns, &members, &groups,
                          &h, &annual_days, &requested_trace))
      return nullptr;
    if (groups < 1 || groups > 100 || h < 1 || h > 252 || annual_days < 1 ||
        annual_days > 366)
      throw std::invalid_argument("Invalid portfolio options");
    InputRef holder(factor, returns, members);
    const auto &input = *holder.value;
    const auto &f = input.factors, &r = input.returns;
    const std::size_t count = f.empty() ? 0 : f[0].size();
    const auto selected_trace = trace_signals(requested_trace, f.size(), r.size(), count, h);
    Owned traces{selected_trace.empty() ? nullptr : PyList_New(0)};
    if (!selected_trace.empty() && !traces.p)
      throw std::bad_alloc();
    std::vector<Strategy> strategies;
    for (int g = 0; g < groups; ++g)
      strategies.emplace_back("long_G" + std::to_string(g + 1));
    for (int g = 0; g < groups; ++g)
      strategies.emplace_back("short_G" + std::to_string(g + 1));
    strategies.emplace_back("high_low");
    if (groups >= 4)
      strategies.emplace_back("inner_high_low");
    strategies.emplace_back("alpha");
    std::vector<double> indices, daily_indices;
    for (std::size_t t = 0; t < f.size(); t += h) {
      indices.push_back(double(t));
      const auto end = std::min(t + h + 1, r.size());
      for (auto d = t + 1; d < end; ++d)
        daily_indices.push_back(double(d));
      const auto &order = input.order[t];
      std::vector<std::size_t> labels(count, groups), sizes(groups, 0);
      long double mean = 0;
      for (std::size_t k = 0; k < order.size(); ++k) {
        auto n = order[k], g = k * groups / order.size();
        labels[n] = g;
        ++sizes[g];
        mean += (static_cast<long double>(f[t][n]) - mean) / (k + 1);
      }
      long double norm = 0;
      for (auto n : order)
        norm += std::abs(static_cast<long double>(f[t][n]) - mean);
      for (auto &s : strategies) {
        std::vector<double> weights(count, 0);
        bool usable = true;
        if (s.name == "alpha") {
          if (!input.alpha_weights.empty()) {
            usable = false;
            for (std::size_t n = 0; n < count; ++n) {
              const auto value = input.alpha_weights[t][n];
              if (std::isfinite(value)) {
                weights[n] = value;
                usable = usable || value != 0;
              }
            }
          } else if (norm > 0)
            for (auto n : order)
              weights[n] = double((static_cast<long double>(f[t][n]) - mean) / norm);
          else
            usable = false;
        } else if (s.name == "high_low" || s.name == "inner_high_low") {
          int lo = s.name == "high_low" ? 0 : 1,
              hi = s.name == "high_low" ? groups - 1 : groups - 2;
          if (lo >= hi || !sizes[lo] || !sizes[hi])
            usable = false;
          else
            for (auto n : order) {
              if (labels[n] == std::size_t(lo))
                weights[n] = -.5 / sizes[lo];
              if (labels[n] == std::size_t(hi))
                weights[n] = .5 / sizes[hi];
            }
        } else {
          const bool short_side = s.name.starts_with("short_");
          const int g = std::stoi(s.name.substr(s.name.find('G') + 1)) - 1;
          if (!sizes[g])
            usable = false;
          else
            for (auto n : order)
              if (labels[n] == std::size_t(g))
                weights[n] = (short_side ? -1. : 1.) / sizes[g];
        }
        std::vector<Holding> held;
        double concentration = 0;
        auto hold = [&](std::size_t n) {
          if (weights[n] != 0) {
            held.push_back({n, weights[n]});
            concentration = std::max(concentration, std::abs(weights[n]));
          }
        };
        if (s.name == "alpha" && !input.alpha_weights.empty()) {
          for (std::size_t n = 0; n < count; ++n)
            hold(n);
        } else
          for (auto n : order)
            hold(n);
        std::unique_ptr<TraceState> tracing;
        if (traces.p && end > t + 1 && s.name == "alpha" &&
            std::binary_search(selected_trace.begin(), selected_trace.end(), t))
          tracing = std::make_unique<TraceState>(weights);
        // Both books are expressed as fractions of the same unit gross book.
        // The prior weights drift with returns; no shares, lots or cash engine.
        bool trade_valid = usable && (!s.seen || s.previous_valid);
        for (const auto &old : s.previous)
          weights[old.symbol] -= old.weight * old.growth;
        long double traded = 0;
        if (trade_valid)
          for (auto w : weights)
            traded += std::abs(static_cast<long double>(w));
        const double turnover = trade_valid ? double(traded) : missing;
        s.period_turnover.push_back(turnover);
        double previous_wealth = 1, wealth = missing;
        bool valid = usable;
        for (auto d = t + 1; d < end; ++d) {
          long double delta = 0;
          for (auto &position : held) {
            const auto previous_growth = position.growth;
            const auto value = r[d][position.symbol];
            if (!std::isfinite(value) || value < -1)
              position.growth = missing;
            else
              position.growth *= 1 + value;
            if (!std::isfinite(position.growth))
              valid = false;
            delta += position.weight * (static_cast<long double>(position.growth) - 1);
            if (tracing) {
              const auto n = position.symbol;
              tracing->before[n] = previous_growth;
              const char *error = nullptr;
              double contribution = missing;
              if (!std::isfinite(value) || value < -1)
                error = "missing_return";
              else if (!std::isfinite(previous_growth))
                error = "prior_missing_return";
              else if (!std::isfinite(position.growth))
                error = "nonfinite_growth";
              else {
                contribution = static_cast<double>(
                    static_cast<long double>(position.weight) * previous_growth * value);
                if (!std::isfinite(contribution))
                  error = "nonfinite_pnl";
              }
              tracing->contribution[n] = contribution;
              tracing->errors[n] = error;
            }
          }
          wealth = valid ? double(1 + delta) : missing;
          if (!std::isfinite(wealth))
            valid = false;
          s.pnl.push_back(valid && previous_wealth > 0 && wealth >= 0
                              ? wealth / previous_wealth - 1 : missing);
          s.book_pnl.push_back(valid && std::isfinite(previous_wealth)
                                  ? wealth - previous_wealth : missing);
          if (tracing) {
            Owned traced{trace_day(input, t, d, mean, usable, *tracing, s.book_pnl.back())};
            if (PyList_Append(traces.p, traced.p) < 0)
              throw std::runtime_error("Trace allocation failure");
          }
          s.turnover.push_back(d == t + 1 ? turnover : 0);
          s.positions.push_back(usable ? double(held.size()) : missing);
          s.concentration.push_back(usable ? concentration : missing);
          previous_wealth = wealth;
        }
        s.period_returns.push_back(valid && t + h < r.size() ? wealth - 1 : missing);
        s.previous = std::move(held);
        s.previous_valid = valid && t + h < r.size();
        s.seen = true;
      }
    }
    Owned result{PyDict_New()}, out{PyDict_New()};
    for (auto &s : strategies) {
      Owned v{performance(s.pnl, s.turnover, 1, annual_days, &s.book_pnl)};
      put(v.p, "returns", list(s.pnl));
      put(v.p, "book_pnl_series", list(s.book_pnl));
      put(v.p, "turnover_series", list(s.turnover));
      put(v.p, "positions", list(s.positions));
      put(v.p, "max_weight_series", list(s.concentration));
      put(v.p, "period_returns", list(s.period_returns));
      put(v.p, "period_turnover_series", list(s.period_turnover));
      put(out.p, s.name.c_str(), Py_NewRef(v.p));
    }
    put(result.p, "signal_indices", list(indices));
    put(result.p, "daily_indices", list(daily_indices));
    put(result.p, "strategies", Py_NewRef(out.p));
    if (traces.p)
      put(result.p, "trace", Py_NewRef(traces.p));
    return Py_NewRef(result.p);
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
