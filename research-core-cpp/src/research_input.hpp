#pragma once
#include "python_values.hpp"
#include <memory>

namespace fp {
// Owned, immutable task input. Sorting depends only on factor/membership, so it
// is shared across horizons, portfolios and lagged return masks.
struct ResearchInput {
  Matrix factors, prices, members;
  std::vector<std::vector<std::size_t>> order;
  ResearchInput(PyObject *factor, PyObject *opens, PyObject *membership);
  bool eligible(std::size_t t, std::size_t n) const {
    return members.empty() || members[t][n] > 0;
  }
};
struct InputRef {
  std::unique_ptr<ResearchInput> owned;
  const ResearchInput *value;
  InputRef(PyObject *factor, PyObject *opens, PyObject *membership);
};
std::vector<double> paired_factor_ranks(const ResearchInput &, std::size_t,
                                      const std::vector<std::size_t> &);
} // namespace fp
PyObject *prepare_research(PyObject *, PyObject *);
PyObject *ic_series(PyObject *, PyObject *);
