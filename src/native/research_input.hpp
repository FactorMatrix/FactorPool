#pragma once
#include "python_values.hpp"
#include <cstdint>
#include <map>
#include <memory>

namespace fp {
// Owned, immutable task input. Sorting depends only on factor/membership, so it
// is shared across horizons, portfolios and lagged return masks.
struct ResearchInput {
  Matrix factors, returns, members, alpha_weights;
  std::vector<std::vector<std::size_t>> order;
  ResearchInput(PyObject *factor, PyObject *daily_returns, PyObject *membership,
                PyObject *weights = nullptr);
  // Row t compounds days t+1..t+h. Cached intervals include every daily
  // observation; neither a missing day nor a total loss can be skipped.
  const Matrix &forward(int h) const;
  // Sort a forward-return row once, then rerank only the actual paired sample.
  std::vector<double> paired_return_ranks(
      int h, std::size_t row, const std::vector<std::size_t> &symbols) const;
  void prepare_return_orders(int h) const;
  bool eligible(std::size_t t, std::size_t n) const {
    return members.empty() || members[t][n] > 0;
  }

private:
  struct ReturnOrders {
    std::vector<std::vector<std::size_t>> rows;
    std::vector<unsigned char> ready;
  };
  mutable std::map<int, Matrix> forwards_;
  mutable std::map<int, ReturnOrders> return_orders_;
  mutable std::vector<long double> log_prefix_;
  mutable std::vector<std::size_t> missing_prefix_, zero_prefix_;
  void prepare_prefixes() const;
};
struct InputRef {
  std::unique_ptr<ResearchInput> owned;
  const ResearchInput *value;
  InputRef(PyObject *factor, PyObject *daily_returns, PyObject *membership);
};
std::vector<double> paired_factor_ranks(const ResearchInput &, std::size_t,
                                      const std::vector<std::size_t> &);
} // namespace fp
PyObject *prepare_research(PyObject *, PyObject *);
PyObject *ic_series(PyObject *, PyObject *);
