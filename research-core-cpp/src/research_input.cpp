#include "research_input.hpp"
#include "statistics.hpp"
#include <algorithm>

namespace {
constexpr const char *capsule_name = "factorpool.research_input.v1";
void destroy_input(PyObject *capsule) {
  delete static_cast<fp::ResearchInput *>(
      PyCapsule_GetPointer(capsule, capsule_name));
}
} // namespace
namespace fp {
ResearchInput::ResearchInput(PyObject *factor, PyObject *opens,
                             PyObject *membership)
    : factors(matrix(factor)), prices(matrix(opens)),
      members(membership && membership != Py_None ? matrix(membership) : Matrix{}) {
  if (factors.size() > prices.size() ||
      (membership && membership != Py_None && members.size() != factors.size()) ||
      (!factors.empty() &&
       (factors[0].size() != prices[0].size() ||
        (!members.empty() && factors[0].size() != members[0].size()))))
    throw std::invalid_argument("Research matrix axes differ");
  order.resize(factors.size());
  for (std::size_t t = 0; t < factors.size(); ++t) {
    auto &sorted = order[t];
    sorted.reserve(factors[t].size());
    for (std::size_t n = 0; n < factors[t].size(); ++n)
      if (std::isfinite(factors[t][n]) && eligible(t, n))
        sorted.push_back(n);
    std::stable_sort(sorted.begin(), sorted.end(), [&](auto a, auto b) {
      return factors[t][a] < factors[t][b];
    });
  }
}
InputRef::InputRef(PyObject *factor, PyObject *opens, PyObject *membership) {
  if (PyCapsule_IsValid(factor, capsule_name)) {
    if (opens != Py_None || (membership && membership != Py_None))
      throw std::invalid_argument(
          "Prepared research input already owns all matrices");
    value = static_cast<ResearchInput *>(PyCapsule_GetPointer(factor, capsule_name));
  } else {
    owned = std::make_unique<ResearchInput>(factor, opens, membership);
    value = owned.get();
  }
}
std::vector<double> paired_factor_ranks(const ResearchInput &input, std::size_t t,
                                      const std::vector<std::size_t> &symbols) {
  // Filter the cached order, then rerank the actual paired sample. A missing
  // future price changes tie sizes, so reusing full-universe ranks is incorrect.
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
    PyObject *factor, *opens, *members;
    if (!PyArg_ParseTuple(args, "OOO", &factor, &opens, &members))
      return nullptr;
    auto input = std::make_unique<fp::ResearchInput>(factor, opens, members);
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
    int h, minimum;
    if (!PyArg_ParseTuple(args, "Oii", &prepared, &h, &minimum))
      return nullptr;
    if (h < 1 || h > 252 || minimum < 2)
      throw std::invalid_argument("Invalid correlation options");
    InputRef holder(prepared, Py_None, nullptr);
    const auto &input = *holder.value;
    const auto &f = input.factors, &p = input.prices;
    std::vector<double> ics, rankics;
    for (std::size_t t = 0; t < f.size(); ++t) {
      std::vector<double> a, b;
      std::vector<std::size_t> symbols;
      if (t + 1 + h < p.size())
        for (std::size_t n = 0; n < f[t].size(); ++n) {
          if (std::isfinite(f[t][n]) && input.eligible(t, n) &&
              std::isfinite(p[t + 1][n]) && std::isfinite(p[t + 1 + h][n]) &&
              p[t + 1][n] > 0 && p[t + 1 + h][n] > 0) {
            const double r = p[t + 1 + h][n] / p[t + 1][n] - 1;
            if (std::isfinite(r)) {
              a.push_back(f[t][n]);
              b.push_back(r);
              symbols.push_back(n);
            }
          }
        }
      ics.push_back(correlation(a, b, minimum));
      rankics.push_back(a.size() >= static_cast<std::size_t>(minimum)
        ? correlation(paired_factor_ranks(input, t, symbols), rank_values(b), minimum)
        : missing);
    }
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
