#include "market.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace factorpool::market {
namespace {
constexpr double missing = std::numeric_limits<double>::quiet_NaN();
bool positive(double value) { return std::isfinite(value) && value > 0; }

void validate(const Inputs& in, bool adjustment) {
  const auto rows = in.close.rows, columns = in.close.columns;
  if (columns && rows > std::numeric_limits<std::size_t>::max() / columns)
    throw std::invalid_argument("Market matrix is too large");
  const auto check = [&](const MatrixView& value) {
    if (value.rows != rows || value.columns != columns || value.values.size() != rows * columns)
      throw std::invalid_argument("Market matrix axes differ");
  };
  check(in.close); check(in.previous); check(in.volume);
  check(in.turnover); check(in.member);
  if (adjustment) check(in.suspended);
}

AdjustmentState initial_state(std::size_t columns, const AdjustmentState* source) {
  if (!source) {
    AdjustmentState result;
    result.columns = columns;
    result.last_actual_close.assign(columns, missing);
    result.cumulative.assign(columns, missing);
    result.segment.assign(columns, -1);
    return result;
  }
  if (source->columns != columns || source->last_actual_close.size() != columns ||
      source->cumulative.size() != columns || source->segment.size() != columns ||
      (source->last_date && (source->last_input.size() != columns * 6 ||
       source->last_factor.size() != columns || source->last_q.size() != columns ||
       source->last_segment.size() != columns || source->last_known.size() != columns)))
    throw std::invalid_argument("Adjustment state differs from the symbol axis");
  return *source;
}

std::vector<double> input_row(const Inputs& in, std::size_t row) {
  std::vector<double> values;
  values.reserve(in.close.columns * 6);
  for (const auto* field : {&in.close, &in.previous, &in.volume, &in.turnover, &in.member, &in.suspended}) {
    const auto first = row * field->columns;
    values.insert(values.end(), field->values.begin() + first, field->values.begin() + first + field->columns);
  }
  return values;
}
} // namespace

std::vector<double> daily_returns(const Inputs& in) {
  validate(in, false);
  std::vector<double> result(in.close.values.size(), missing);
  for (std::size_t cell = 0; cell < result.size(); ++cell) {
    const auto close = in.close.values[cell], previous = in.previous.values[cell],
               volume = in.volume.values[cell], amount = in.turnover.values[cell],
               member = in.member.values[cell];
    if (positive(volume) && std::isfinite(close) && close >= 0 && positive(previous)) {
      // Deliberately preserve the v1 division followed by subtraction order.
      double value = close / previous;
      value -= 1;
      if (std::isfinite(value)) result[cell] = value;
    }
    if (std::isfinite(volume) && volume == 0 && std::isfinite(amount) && amount == 0 && positive(member))
      result[cell] = 0;
  }
  return result;
}

std::vector<double> adjust_values(std::span<const double> values,
                                 std::span<const double> historical_q,
                                 std::span<const double> basis_q,
                                 std::span<const std::int64_t> historical_segment,
                                 std::span<const std::int64_t> basis_segment,
                                 bool inverse) {
  const auto size = values.size();
  if (historical_q.size() != size || basis_q.size() != size ||
      historical_segment.size() != size || basis_segment.size() != size)
    throw std::invalid_argument("Adjustment row symbol axes differ");
  std::vector<double> result(size, missing);
  for (std::size_t n = 0; n < size; ++n) {
    if (historical_segment[n] < 0 || historical_segment[n] != basis_segment[n] ||
        !positive(historical_q[n]) || !positive(basis_q[n])) continue;
    const double scale = basis_q[n] / historical_q[n];
    const double value = inverse ? values[n] / scale : values[n] * scale;
    if (std::isfinite(value)) result[n] = value;
  }
  return result;
}

AdjustmentResult derive_adjustment(std::span<const std::int64_t> dates,
                                   const Inputs& in, const AdjustmentState* previous_state) {
  validate(in, true);
  if (dates.size() != in.close.rows)
    throw std::invalid_argument("Adjustment dates differ from the matrix time axis");
  AdjustmentResult out;
  out.rows = in.close.rows; out.columns = in.close.columns;
  const auto cells = out.rows * out.columns;
  out.factor.assign(cells, missing); out.q.assign(cells, missing);
  out.segment.assign(cells, -1); out.known.assign(cells, 0);
  out.state = initial_state(out.columns, previous_state);
  for (std::size_t row = 0; row < out.rows; ++row) {
    auto raw = input_row(in, row);
    auto& state = out.state;
    const auto offset = row * out.columns;
    if (state.last_date && dates[row] < *state.last_date)
      throw std::invalid_argument("Adjustment dates must be chronological");
    if (state.last_date && dates[row] == *state.last_date) {
      if (raw.size() != state.last_input.size() ||
          (!raw.empty() && std::memcmp(raw.data(), state.last_input.data(), raw.size() * sizeof(double))))
        throw std::invalid_argument("Same-date market revision requires a new derived version and replay");
      std::copy(state.last_factor.begin(), state.last_factor.end(), out.factor.begin() + offset);
      std::copy(state.last_q.begin(), state.last_q.end(), out.q.begin() + offset);
      std::copy(state.last_segment.begin(), state.last_segment.end(), out.segment.begin() + offset);
      std::copy(state.last_known.begin(), state.last_known.end(), out.known.begin() + offset);
      continue;
    }
    for (std::size_t n = 0; n < out.columns; ++n) {
      const auto cell = offset + n;
      const auto close = in.close(row, n), previous = in.previous(row, n),
                 volume = in.volume(row, n), amount = in.turnover(row, n),
                 member = in.member(row, n), suspended = in.suspended(row, n);
      const bool no_trade = std::isfinite(volume) && volume == 0 && positive(member) &&
          ((std::isfinite(amount) && amount == 0) || (positive(suspended) && std::isnan(amount)));
      if (no_trade) {
        // Filled reference quotes are not actual closes. Publish no new Q
        // observation, but retain the last actual anchor for one resume bridge.
        continue;
      }
      const bool amount_consistent = std::isnan(amount) || positive(amount);
      if (!positive(volume) || !std::isfinite(close) || close < 0 || !amount_consistent) {
        state.last_actual_close[n] = state.cumulative[n] = missing;
        continue;
      }
      const bool anchor = positive(state.last_actual_close[n]) && positive(state.cumulative[n]);
      const double factor = anchor && positive(previous) ? previous / state.last_actual_close[n] : missing;
      const double cumulative = positive(factor) ? state.cumulative[n] * factor : missing;
      if (positive(factor) && positive(cumulative)) {
        out.factor[cell] = factor; out.q[cell] = cumulative;
        out.segment[cell] = state.segment[n]; out.known[cell] = 1;
        state.cumulative[n] = cumulative;
      } else {
        if (state.segment[n] == std::numeric_limits<std::int64_t>::max())
          throw std::invalid_argument("Adjustment segment counter overflow");
        ++state.segment[n];
        state.cumulative[n] = 1;
        out.q[cell] = 1; out.segment[cell] = state.segment[n];
      }
      state.last_actual_close[n] = close;
    }
    state.last_date = dates[row]; state.last_input = std::move(raw);
    state.last_factor.assign(out.factor.begin() + offset, out.factor.begin() + offset + out.columns);
    state.last_q.assign(out.q.begin() + offset, out.q.begin() + offset + out.columns);
    state.last_segment.assign(out.segment.begin() + offset, out.segment.begin() + offset + out.columns);
    state.last_known.assign(out.known.begin() + offset, out.known.begin() + offset + out.columns);
  }
  return out;
}
} // namespace factorpool::market
