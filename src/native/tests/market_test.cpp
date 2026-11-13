#include "market.hpp"
#include <cassert>
#include <cmath>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace m = factorpool::market;
constexpr auto missing = std::numeric_limits<double>::quiet_NaN();
struct Data {
  std::size_t rows, columns;
  std::vector<double> close, previous, volume, amount, member, suspended;
  Data(std::size_t rows, std::size_t columns)
      : rows(rows), columns(columns), close(rows*columns, 100), previous(rows*columns, 100),
        volume(rows*columns, 10), amount(rows*columns, 1000), member(rows*columns, 1), suspended(rows*columns, 0) {}
  m::Inputs input(std::size_t first, std::size_t count) const {
    const auto view = [&](const auto& value) -> m::MatrixView {
      return {std::span(value).subspan(first * columns, count * columns), count, columns};
    };
    return {view(close), view(previous), view(volume), view(amount), view(member), view(suspended)};
  }
  m::Inputs input() const { return input(0, rows); }
};
template<class T> bool same(const std::vector<T>& a, const std::vector<T>& b) {
  return a.size() == b.size() && (a.empty() || !std::memcmp(a.data(), b.data(), a.size()*sizeof(T)));
}
void returns_contract() {
  Data data(1, 8);
  data.close = {12, missing, 0, 12, 12, 12, 12, 1e308};
  data.previous = {10, missing, 10, 10, 10, 10, 10, 1e-308};
  data.volume = {5, 0, 5, missing, 0, 0, -1, 5};
  data.amount = {0, 0, missing, 0, 0, 2, 0, missing}; data.member[4] = 0;
  const auto copy = data.close;
  const auto result = m::daily_returns(data.input());
  assert(result[0] == 12./10 - 1 && result[1] == 0 && result[2] == -1);
  for (auto n : {3, 4, 5, 6, 7}) assert(std::isnan(result[n]));
  assert(same(copy, data.close));
}
void suspend_split_dividend_zero() {
  Data data(8, 1);
  data.close = {100, 102, 98, 98, 51, 38.25, 0, 3};
  data.previous = {99, 100, 99, 99, 51, 38.25, 38.25, 3};
  data.volume[2] = data.volume[3] = 0;
  data.amount[2] = data.amount[3] = 0;
  data.suspended[2] = data.suspended[3] = 1;
  const std::vector<std::int64_t> dates{1,2,3,4,5,6,7,8};
  const auto result = m::derive_adjustment(dates, data.input());
  assert(std::isnan(result.factor[0]) && result.q[0] == 1 && result.segment[0] == 0);
  assert(result.factor[1] == 1 && result.known[1]);
  for (auto n : {2,3}) assert(std::isnan(result.factor[n]) && std::isnan(result.q[n]) && result.segment[n] == -1 && !result.known[n]);
  assert(result.factor[4] == .5 && result.q[4] == .5 && result.segment[4] == 0);
  assert(result.factor[5] == .75 && result.q[5] == .375);
  assert(result.factor[6] == 1 && result.q[6] == .375 && result.known[6]);
  assert(std::isnan(result.factor[7]) && result.q[7] == 1 && result.segment[7] == 1);
  const auto first = m::derive_adjustment(std::span(dates).first(3), data.input(0,3));
  const auto frozen = first.state.last_actual_close;
  const auto second = m::derive_adjustment(std::span(dates).subspan(3), data.input(3,5), &first.state);
  assert(same(first.state.last_actual_close, frozen));
  assert(same(second.state.last_actual_close, result.state.last_actual_close));
  for (std::size_t row = 0; row < 5; ++row) {
    assert((std::isnan(second.factor[row]) && std::isnan(result.factor[row+3])) || second.factor[row] == result.factor[row+3]);
    assert(second.segment[row] == result.segment[row+3]);
  }
}
void retries_and_unknowns() {
  Data data(4, 1); data.close = {100,50,50,25}; data.previous = {100,50,50,25};
  const std::vector<std::int64_t> dates{1,2,2,3};
  const auto all = m::derive_adjustment(dates, data.input());
  assert(all.q[1] == .5 && all.q[2] == .5 && all.q[3] == .25);
  const auto first = m::derive_adjustment(std::span(dates).first(2), data.input(0,2));
  const auto retry = m::derive_adjustment(std::span(dates).subspan(2,1), data.input(2,1), &first.state);
  assert(retry.factor[0] == .5 && retry.q[0] == .5);
  auto changed = data; changed.previous[2] = 49;
  bool rejected = false;
  try { (void)m::derive_adjustment(std::span(dates).subspan(2,1), changed.input(2,1), &first.state); }
  catch (const std::invalid_argument&) { rejected = true; }
  assert(rejected && first.state.last_actual_close[0] == 50);
  data.volume[1] = missing;
  const std::vector<std::int64_t> unique{1,2,3,4};
  const auto gap = m::derive_adjustment(unique, data.input());
  assert(std::isnan(gap.factor[1]) && std::isnan(gap.q[1]) && gap.segment[1] == -1);
  assert(std::isnan(gap.factor[2]) && gap.q[2] == 1 && gap.segment[2] == 1);
  assert(gap.factor[3] == .5 && gap.q[3] == .5 && gap.segment[3] == 1);
}
void price_and_volume_views() {
  const std::vector<double> values{100, 0, 70, 40, missing};
  const std::vector<double> history{1, 1, 1, 1, 1}, basis{.5, .5, .5, .5, .5};
  const std::vector<std::int64_t> hs{0, 0, 0, -1, 0}, bs{0, 0, 1, -1, 0};
  const auto price = m::adjust_values(values, history, basis, hs, bs, false);
  const auto volume = m::adjust_values(values, history, basis, hs, bs, true);
  assert(price[0] == 50 && price[1] == 0 && volume[0] == 200 && volume[1] == 0);
  for (auto n : {2, 3, 4}) assert(std::isnan(price[n]) && std::isnan(volume[n]));
}
int main() {
  returns_contract(); suspend_split_dividend_zero(); retries_and_unknowns(); price_and_volume_views();
  std::cout << "market core: return v1, last-actual Q, suspension, zero, chunk and retry checks passed\n";
}
