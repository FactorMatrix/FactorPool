#pragma once
#include "python_values.hpp"
#include <cstdint>
#include <map>
#include <memory>
#include <utility>

namespace fp {
// Owned, immutable task input. Sorting depends only on factor/membership, so it
// is shared across horizons, portfolios and lagged return masks.
struct ResearchInput {
  Matrix factors, returns, members, alpha_weights;
  std::vector<std::int64_t> terminal_exit_rows;
  std::vector<std::vector<std::size_t>> order;
  ResearchInput(PyObject *factor, PyObject *daily_returns, PyObject *membership,
                PyObject *weights = nullptr, PyObject *exits = nullptr);
  void validate_alpha_weights(const Matrix &, int holding_horizon = 0) const;
  // 1D borrows R[t+1]. Longer windows compound every daily observation and
  // materialize only the rows visited by the signal calendar.
  std::span<const double> forward_row(int h, std::size_t row) const;
  // Materialize a sorted finite-return order before independent paired-rank
  // reads. Decay batches use this to avoid concurrent first-writer cache work.
  void prepare_return_order(int h, std::size_t row) const;
  struct CorrelationValues {
    double ic = missing, rank_ic = missing;
    std::size_t pairs = 0;
  };
  struct PairedSample {
    std::vector<double> factor, returns;
    std::vector<std::size_t> positions, sorted_pairs;
  };
  CorrelationValues correlate_pair(int h, int minimum, std::size_t signal,
                                   std::size_t return_row, PairedSample &) const;
  // Allocate on the caller before parallel row work. A row depends on the
  // immutable paired sample and minimum, not report stride, ddof or HAC lag.
  void prepare_correlations(int h, int minimum) const;
  const CorrelationValues &correlation_row(int h, int minimum,
                                           std::size_t row) const;
  bool eligible(std::size_t t, std::size_t n) const {
    return members.empty() || members[t][n] > 0;
  }
  bool holding_eligible(std::size_t t, std::size_t n, int horizon) const {
    return eligible(t, n) &&
        (terminal_exit_rows.empty() || terminal_exit_rows[n] < 0 ||
         static_cast<std::uint64_t>(terminal_exit_rows[n]) >
             static_cast<std::uint64_t>(t) + static_cast<unsigned>(horizon));
  }

private:
  struct ReturnOrders {
    std::vector<std::vector<std::size_t>> rows;
    std::vector<unsigned char> ready;
  };
  struct CorrelationRows {
    std::vector<CorrelationValues> rows;
    std::vector<unsigned char> ready;
  };
  struct ForwardRows {
    Matrix values;
    std::vector<unsigned char> ready;
  };
  mutable std::map<int, ForwardRows> forwards_;
  mutable std::map<int, ReturnOrders> return_orders_;
  mutable std::map<std::pair<int, int>, CorrelationRows> correlations_;
  mutable std::vector<long double> log_prefix_;
  mutable std::vector<std::size_t> missing_prefix_, zero_prefix_;
  const std::vector<double> missing_forward_;
  void prepare_prefixes() const;
  void prepare_forward(int h) const;
  void prepare_return_orders(int h) const;
};
const ResearchInput& research_input(PyObject *prepared);
} // namespace fp
PyObject *prepare_research(PyObject *, PyObject *);
PyObject *ic_series(PyObject *, PyObject *);
