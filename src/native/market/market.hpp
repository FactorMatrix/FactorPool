#pragma once
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <vector>

namespace factorpool::market {
inline constexpr auto return_version = "volume-zero-v1";
inline constexpr auto adjustment_version = "prev-close-last-actual-v2";

struct MatrixView {
  std::span<const double> values;
  std::size_t rows = 0, columns = 0;
  double operator()(std::size_t row, std::size_t column) const {
    return values[row * columns + column];
  }
};

// Inputs remain raw. Membership is instrument lifetime, never a research pool.
struct Inputs {
  MatrixView close, previous, volume, turnover, member, suspended;
};

std::vector<double> daily_returns(const Inputs&);

std::vector<double> adjust_values(std::span<const double> values,
                                 std::span<const double> historical_q,
                                 std::span<const double> basis_q,
                                 std::span<const std::int64_t> historical_segment,
                                 std::span<const std::int64_t> basis_segment,
                                 bool inverse);

// Immutable input to each call; the result owns the next streaming state.
// Last-row storage makes a same-date retry idempotent, without reapplying Q.
struct AdjustmentState {
  std::size_t columns = 0;
  std::vector<double> last_actual_close, cumulative;
  std::vector<std::int64_t> segment;
  std::optional<std::int64_t> last_date;
  std::vector<double> last_input, last_factor, last_q;
  std::vector<std::int64_t> last_segment;
  std::vector<std::uint8_t> last_known;
};

struct AdjustmentResult {
  std::size_t rows = 0, columns = 0;
  std::vector<double> factor, q;
  std::vector<std::int64_t> segment;
  std::vector<std::uint8_t> known;
  AdjustmentState state;
};

AdjustmentResult derive_adjustment(std::span<const std::int64_t> dates,
                                   const Inputs&,
                                   const AdjustmentState* state = nullptr);
} // namespace factorpool::market
