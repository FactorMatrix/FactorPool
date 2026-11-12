#include "calendar_window.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace factorpool::operators {
namespace {
constexpr double missing = std::numeric_limits<double>::quiet_NaN();
}

CalendarState::CalendarState(CalendarOp op, std::size_t period, int ddof)
    : op_(op), period_(period), capacity_(period), ddof_(ddof) {
  switch (op_) {
  case CalendarOp::Delay:
  case CalendarOp::Delta:
    if (period_ == std::numeric_limits<std::size_t>::max())
      throw std::invalid_argument("Calendar lag is too large");
    capacity_ = period_ + 1;
    break;
  case CalendarOp::Mean:
  case CalendarOp::Sum:
  case CalendarOp::Min:
  case CalendarOp::Max:
  case CalendarOp::Std:
  case CalendarOp::DecayLinear:
    if (!period_) throw std::invalid_argument("Calendar window must be positive");
    break;
  default:
    throw std::invalid_argument("Unknown calendar window operation");
  }
  if (ddof_ != 0 && ddof_ != 1)
    throw std::invalid_argument("Calendar std ddof must be 0 or 1");
  if (op_ != CalendarOp::Std && ddof_ != 0)
    throw std::invalid_argument("Calendar ddof applies to std only");
}

void CalendarState::set_axis(const std::vector<std::string> &symbols) {
  std::unordered_set<std::string> unique;
  for (const auto &symbol : symbols)
    if (symbol.empty() || !unique.insert(symbol).second)
      throw std::invalid_argument("Calendar symbols must be nonempty and unique");
  if (symbols == symbols_) return;
  std::unordered_map<std::string, std::size_t> old;
  for (std::size_t n = 0; n < symbols_.size(); ++n) old.emplace(symbols_[n], n);
  std::vector<std::deque<Slot>> next(symbols.size());
  for (std::size_t n = 0; n < symbols.size(); ++n)
    if (const auto found = old.find(symbols[n]); found != old.end())
      next[n] = std::move(history_[found->second]);
  history_ = std::move(next);
  symbols_ = symbols;
}

CalendarResult CalendarState::update(std::int64_t time,
                                   const std::vector<double> &source,
                                   const std::vector<std::uint8_t> &source_ready,
                                   const std::vector<std::uint8_t> &present) {
  const auto count = symbols_.size();
  if (source.size() != count || source_ready.size() != count || present.size() != count)
    throw std::invalid_argument("Calendar input columns must match the symbol axis");
  if (std::any_of(source_ready.begin(), source_ready.end(), [](auto v) { return v > 1; }) ||
      std::any_of(present.begin(), present.end(), [](auto v) { return v > 1; }))
    throw std::invalid_argument("Calendar readiness and presence must be boolean masks");
  if (last_time_ && time <= *last_time_)
    throw std::invalid_argument("Calendar input times must be unique and strictly increasing");
  CalendarResult result{std::vector<double>(count, missing),
                        std::vector<std::uint8_t>(count, 0),
                        std::vector<std::uint8_t>(count, 0)};
  for (std::size_t n = 0; n < count; ++n) {
    auto &slots = history_[n];
    // A missing Bar cannot be made into a real sample by an accidentally finite
    // upstream value. Known missing still has ready=true and occupies its day.
    slots.push_back({present[n] && source_ready[n] ? source[n] : missing,
                     source_ready[n] != 0});
    if (slots.size() > capacity_) slots.pop_front();
    if (slots.size() < capacity_) continue;
    long double value = 0;
    if (op_ == CalendarOp::Delay || op_ == CalendarOp::Delta) {
      const auto &prior = slots.front();
      const auto &current = slots.back();
      const bool ready = prior.ready && (op_ == CalendarOp::Delay || current.ready);
      result.ready[n] = ready;
      if (!ready || !std::isfinite(prior.value) ||
          (op_ == CalendarOp::Delta && !std::isfinite(current.value))) continue;
      value = op_ == CalendarOp::Delay ? static_cast<long double>(prior.value)
                                      : static_cast<long double>(current.value) - prior.value;
    } else {
      const bool ready = std::all_of(slots.begin(), slots.end(), [](const auto &v) { return v.ready; });
      result.ready[n] = ready;
      if (!ready || std::any_of(slots.begin(), slots.end(),
                               [](const auto &v) { return !std::isfinite(v.value); })) continue;
      switch (op_) {
      case CalendarOp::Min:
      case CalendarOp::Max:
        value = slots.front().value;
        for (const auto &slot : slots)
          value = op_ == CalendarOp::Min ? std::min(value, static_cast<long double>(slot.value))
                                        : std::max(value, static_cast<long double>(slot.value));
        break;
      case CalendarOp::Std: {
        if (period_ <= static_cast<std::size_t>(ddof_)) continue;
        long double mean = 0, squared = 0;
        std::size_t seen = 0;
        for (const auto &slot : slots) {
          const long double delta = slot.value - mean;
          mean += delta / ++seen;
          squared += delta * (slot.value - mean);
        }
        value = std::sqrt(std::max(0.0L, squared / (period_ - ddof_)));
        break;
      }
      case CalendarOp::DecayLinear: {
        std::size_t weight = 0;
        for (const auto &slot : slots) value += static_cast<long double>(++weight) * slot.value;
        value /= static_cast<long double>(period_) * (static_cast<long double>(period_) + 1) / 2;
        break;
      }
      default:
        for (const auto &slot : slots) value += static_cast<long double>(slot.value);
        if (op_ == CalendarOp::Mean) value /= period_;
        break;
      }
    }
    const double converted = static_cast<double>(value);
    if (present[n] && std::isfinite(value) && std::isfinite(converted)) {
      result.values[n] = converted;
      result.valid[n] = 1;
    }
  }
  last_time_ = time;
  return result;
}

} // namespace factorpool::operators
