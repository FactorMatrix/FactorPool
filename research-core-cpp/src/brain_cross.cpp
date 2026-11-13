#include "brain_cross.hpp"
#include "brain_temporal.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <set>
#include <sstream>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace factorpool::operators {
namespace {
constexpr double missing = std::numeric_limits<double>::quiet_NaN();
bool finite(double value) { return std::isfinite(value); }
struct Moments {
  std::size_t count = 0;
  long double mean = 0, m2 = 0;
  void add(double value) {
    const long double delta = static_cast<long double>(value) - mean;
    mean += delta / ++count;
    m2 += delta * (static_cast<long double>(value) - mean);
  }
  double stddev() const { return count ? static_cast<double>(std::sqrt(std::max(0.0L, m2 / count))) : missing; }
};
std::vector<double> split_numbers(const std::string &text) {
  std::vector<double> values;
  std::istringstream source(text); std::string token;
  while (std::getline(source, token, ',')) {
    std::size_t used = 0; double value;
    try { value = std::stod(token, &used); } catch (...) { throw std::invalid_argument("Bucket bounds must be comma-separated finite numbers"); }
    if (token.find_first_not_of(" \t\r\n", used) != std::string::npos || !finite(value))
      throw std::invalid_argument("Bucket bounds must be comma-separated finite numbers");
    values.push_back(value);
  }
  if (values.empty()) throw std::invalid_argument("Bucket bounds cannot be empty");
  return values;
}
std::vector<double> ranks(const std::vector<double> &values) {
  std::vector<double> result(values.size(), missing);
  std::vector<std::size_t> order;
  for (std::size_t n = 0; n < values.size(); ++n) if (finite(values[n])) order.push_back(n);
  std::stable_sort(order.begin(), order.end(), [&](auto a, auto b) { return values[a] < values[b]; });
  for (std::size_t first = 0; first < order.size();) {
    std::size_t end = first + 1;
    while (end < order.size() && values[order[first]] == values[order[end]]) ++end;
    const double rank = order.size() <= 1 ? 0.5 : (static_cast<double>(first + end - 1) / 2) / static_cast<double>(order.size() - 1);
    for (std::size_t n = first; n < end; ++n) result[order[n]] = rank;
    first = end;
  }
  return result;
}
}

bool BrainCross::supports(const std::string &op) {
  static const std::set<std::string> known{
    "add", "subtract", "multiply", "divide", "abs", "reverse", "inverse", "min", "max", "densify", "normalize",
    "rank", "quantile", "winsorize", "zscore", "scale", "vec_avg", "vec_sum", "bucket", "trade_when",
    "group_mean", "group_scale", "group_zscore", "group_neutralize", "group_rank", "group_backfill"};
  return known.contains(op);
}

double BrainCross::number(const std::string &key, double fallback) const {
  const auto found = spec_.numbers.find(key);
  return found == spec_.numbers.end() ? fallback : found->second;
}

BrainCross::BrainCross(BrainCrossSpec spec): spec_(std::move(spec)) {
  if (!supports(spec_.op)) throw std::invalid_argument("Unknown core cross-section operator: " + spec_.op);
  for (const auto &[key, value] : spec_.numbers) {
    (void)key;
    if (!finite(value)) throw std::invalid_argument("Core operator numeric parameters must be finite");
  }
  const auto boolean = [&](const std::string &key, bool fallback = false) {
    const auto value = number(key, fallback);
    if (value != 0 && value != 1) throw std::invalid_argument(key + " must be boolean");
    return value != 0;
  };
  if (spec_.op == "add" || spec_.op == "subtract" || spec_.op == "multiply") (void)boolean("filter");
  if (spec_.op == "normalize") {
    (void)boolean("useStd");
    if (number("limit", 0) < 0) throw std::invalid_argument("Normalize limit must be nonnegative");
    if (number("ddof", 0) != 0 && number("ddof", 0) != 1) throw std::invalid_argument("Normalize ddof must be 0 or 1");
  }
  if (spec_.op == "winsorize" || spec_.op == "group_backfill")
    if (number("std", 4) < 0) throw std::invalid_argument("Winsorization std must be nonnegative");
  if (spec_.op == "quantile") {
    const auto driver = spec_.strings.contains("driver") ? spec_.strings.at("driver") : "gaussian";
    if (driver != "gaussian" && driver != "uniform" && driver != "cauchy") throw std::invalid_argument("Unknown quantile driver");
    if (number("sigma", 1) < 0) throw std::invalid_argument("Quantile sigma must be nonnegative");
  }
  if (spec_.op == "rank")
    if (number("rate", 2) < 0 || std::trunc(number("rate", 2)) != number("rate", 2)) throw std::invalid_argument("Rank rate must be a nonnegative integer");
  if (spec_.op == "scale")
    for (const auto &key : {"scale", "longscale", "shortscale"}) if (number(key, 1) < 0) throw std::invalid_argument("Scale parameters must be nonnegative");
  if (spec_.op == "group_backfill") {
    const auto period = number("period", number("d", 1));
    if (period < 1 || std::trunc(period) != period || period > INT32_MAX) throw std::invalid_argument("Group backfill period must be a positive integer");
    period_ = static_cast<std::size_t>(period);
  }
  if (spec_.op == "bucket") {
    const bool range = spec_.strings.contains("range"), buckets = spec_.strings.contains("buckets");
    if (range == buckets) throw std::invalid_argument("Bucket requires exactly one of range or buckets");
    range_mode_ = range;
    if (range) {
      const auto bounds = split_numbers(spec_.strings.at("range"));
      if (bounds.size() != 3 || bounds[2] <= 0 || bounds[1] <= bounds[0]) throw std::invalid_argument("Bucket range is start,end,positive_step");
      const long double count = std::ceil((static_cast<long double>(bounds[1]) - bounds[0]) / bounds[2]);
      if (!std::isfinite(count) || count > 1000000) throw std::invalid_argument("Bucket range has too many intervals");
      for (std::size_t n = 0; n < static_cast<std::size_t>(count); ++n) {
        const double value = static_cast<double>(static_cast<long double>(bounds[0]) + n * static_cast<long double>(bounds[2]));
        if (value < bounds[1]) cutoffs_.push_back(value);
      }
      cutoffs_.push_back(bounds[1]);
    } else cutoffs_ = split_numbers(spec_.strings.at("buckets"));
    if (!std::is_sorted(cutoffs_.begin(), cutoffs_.end()) || std::adjacent_find(cutoffs_.begin(), cutoffs_.end()) != cutoffs_.end())
      throw std::invalid_argument("Bucket cutoffs must be strictly increasing");
    skip_begin_ = boolean("skipBoth") || boolean("skipBegin", range);
    skip_end_ = boolean("skipBoth") || boolean("skipEnd", range);
    nan_group_ = boolean("NaNGroup");
  }
}

void BrainCross::set_axis(const std::vector<std::string> &symbols) {
  std::unordered_set<std::string> unique;
  for (const auto &symbol : symbols) if (symbol.empty() || !unique.insert(symbol).second) throw std::invalid_argument("Core symbols must be nonempty and unique");
  if (symbols == symbols_) return;
  std::unordered_map<std::string, std::size_t> old;
  for (std::size_t n = 0; n < symbols_.size(); ++n) old.emplace(symbols_[n], n);
  std::vector<double> held(symbols.size(), missing);
  std::vector<std::deque<Observation>> history(symbols.size());
  for (std::size_t n = 0; n < symbols.size(); ++n) if (const auto found = old.find(symbols[n]); found != old.end()) {
    held[n] = held_[found->second]; history[n] = std::move(history_[found->second]);
  }
  held_ = std::move(held); history_ = std::move(history); symbols_ = symbols;
}

CalendarResult BrainCross::update(std::int64_t time,
                                const std::vector<std::vector<double>> &args,
                                const std::vector<std::vector<std::uint8_t>> &source_ready,
                                const std::vector<std::uint8_t> &present) {
  const auto width = symbols_.size();
  if (present.size() != width || args.size() != source_ready.size()) throw std::invalid_argument("Core cross-section argument masks differ from axis");
  for (std::size_t a = 0; a < args.size(); ++a) {
    if (args[a].size() != width || source_ready[a].size() != width) throw std::invalid_argument("Core argument column differs from axis");
    if (std::any_of(source_ready[a].begin(), source_ready[a].end(), [](auto x) { return x > 1; })) throw std::invalid_argument("Core readiness must be boolean");
  }
  if (std::any_of(present.begin(), present.end(), [](auto x) { return x > 1; })) throw std::invalid_argument("Core presence must be boolean");
  const bool stateful = spec_.op == "trade_when" || spec_.op == "group_backfill";
  if (last_time_ && (time < *last_time_ || (stateful && time == *last_time_))) throw std::invalid_argument("Core stateful frames must have unique increasing times");
  const auto op = spec_.op;
  const auto exact_arity = [&](std::size_t expected) { if (args.size() != expected) throw std::invalid_argument(op + " has incorrect input count"); };
  if (op == "trade_when" || op == "group_mean") exact_arity(3);
  else if (op.starts_with("group_")) exact_arity(2);
  else if (op == "divide") exact_arity(2);
  else if (op == "add" || op == "subtract" || op == "multiply" || op == "min" || op == "max") {
    if (args.size() < 2) throw std::invalid_argument(op + " requires at least two inputs");
  } else if (op == "vec_avg" || op == "vec_sum") {
    if (args.empty()) throw std::invalid_argument(op + " requires a nonempty vector");
  } else exact_arity(1);
  CalendarResult result{std::vector<double>(width, missing), std::vector<std::uint8_t>(width, 0), std::vector<std::uint8_t>(width, 1)};
  auto ready = [&](std::size_t a, std::size_t row) { return present[row] && source_ready[a][row]; };
  auto usable = [&](std::size_t a, std::size_t row) { return ready(a, row) && finite(args[a][row]); };
  auto publish = [&](std::size_t row, long double value) {
    const double converted = static_cast<double>(value);
    if (present[row] && result.ready[row] && std::isfinite(value) && finite(converted)) { result.values[row] = converted; result.valid[row] = 1; }
  };
  for (std::size_t row = 0; row < width; ++row) {
    result.ready[row] = present[row] != 0;
    for (std::size_t a = 0; a < args.size(); ++a) result.ready[row] &= source_ready[a][row];
  }
  if (op == "trade_when") {
    for (std::size_t row = 0; row < width; ++row) {
      result.ready[row] = ready(2, row);
      if (!ready(2, row) || !finite(args[2][row])) continue;
      if (args[2][row] > 0) { held_[row] = missing; continue; }
      result.ready[row] &= ready(0, row);
      if (!ready(0, row) || !finite(args[0][row])) continue;
      if (args[0][row] > 0) {
        result.ready[row] &= ready(1, row);
        if (!ready(1, row)) continue;
        held_[row] = finite(args[1][row]) ? args[1][row] : missing;
      }
      publish(row, held_[row]);
    }
  } else if (op == "group_backfill") {
    for (std::size_t row = 0; row < width; ++row) {
      history_[row].push_back({usable(0, row) ? args[0][row] : missing, usable(1, row) ? args[1][row] : missing, ready(0, row) && ready(1, row)});
      if (history_[row].size() > period_) history_[row].pop_front();
    }
    // Gather each historical group once; missing members reuse its clipped mean.
    // Work scales with stock-days, rather than missing members * stock-days.
    std::map<double, std::vector<double>> by_group;
    for (const auto& security : history_) for (const auto& slot : security)
      if (slot.ready && finite(slot.group) && finite(slot.value)) by_group[slot.group].push_back(slot.value);
    std::map<double, double> fills;
    for (const auto& [group, candidates] : by_group) {
      Moments moments; for (const auto value : candidates) moments.add(value);
      const long double span = number("std", 4) * static_cast<long double>(moments.stddev());
      long double total = 0;
      for (const auto value : candidates) total += std::clamp(static_cast<long double>(value), moments.mean - span, moments.mean + span);
      fills.emplace(group, static_cast<double>(total / candidates.size()));
    }
    for (std::size_t row = 0; row < width; ++row) {
      if (usable(0, row)) { result.ready[row] = ready(0, row); publish(row, args[0][row]); continue; }
      if (!result.ready[row] || !usable(1, row)) continue;
      if (auto value = fills.find(args[1][row]); value != fills.end()) publish(row, value->second);
    }
  } else if (op.starts_with("group_")) {
    const auto grouping = op == "group_mean" ? 2U : 1U;
    std::map<double, std::vector<std::size_t>> groups;
    for (std::size_t row = 0; row < width; ++row) if (usable(grouping, row)) groups[args[grouping][row]].push_back(row);
    for (const auto &[group, members] : groups) {
      (void)group;
      Moments moments; long double numerator = 0, denominator = 0;
      double low = std::numeric_limits<double>::infinity(), high = -low;
      std::vector<double> values; values.reserve(members.size());
      for (const auto row : members) {
        const auto value = usable(0, row) ? args[0][row] : missing;
        values.push_back(value);
        if (!finite(value)) continue;
        moments.add(value); low = std::min(low, value); high = std::max(high, value);
        if (op == "group_mean" && usable(1, row) && args[1][row] >= 0) { numerator += static_cast<long double>(value) * args[1][row]; denominator += args[1][row]; }
      }
      const auto ranked = op == "group_rank" ? ranks(values) : std::vector<double>{};
      for (std::size_t n = 0; n < members.size(); ++n) {
        const auto row = members[n];
        if (!result.ready[row]) continue;
        if (op == "group_mean") { if (denominator > 0) publish(row, numerator / denominator); continue; }
        if (!usable(0, row)) continue;
        if (op == "group_neutralize") publish(row, static_cast<long double>(args[0][row]) - moments.mean);
        else if (op == "group_rank") publish(row, ranked[n]);
        else if (op == "group_scale") { if (high > low) publish(row, (static_cast<long double>(args[0][row]) - low) / (static_cast<long double>(high) - low)); }
        else if (op == "group_zscore") { const auto deviation = moments.stddev(); if (deviation > 0) publish(row, (static_cast<long double>(args[0][row]) - moments.mean) / deviation); }
      }
    }
  } else if (op == "densify") {
    std::set<double> distinct; for (std::size_t row = 0; row < width; ++row) if (usable(0, row)) distinct.insert(args[0][row]);
    std::map<double, double> codes; std::size_t code = 0; for (auto value : distinct) codes.emplace(value, static_cast<double>(code++));
    for (std::size_t row = 0; row < width; ++row) if (usable(0, row)) publish(row, codes.at(args[0][row]));
  } else if (op == "bucket") {
    const auto retained = cutoffs_.size() + 1 - static_cast<std::size_t>(skip_begin_) - static_cast<std::size_t>(skip_end_);
    for (std::size_t row = 0; row < width; ++row) {
      if (!result.ready[row]) continue;
      if (!finite(args[0][row])) { if (nan_group_ && std::isnan(args[0][row])) publish(row, static_cast<double>(retained)); continue; }
      auto bucket = static_cast<std::size_t>(std::lower_bound(cutoffs_.begin(), cutoffs_.end(), args[0][row]) - cutoffs_.begin());
      if (range_mode_ && args[0][row] == cutoffs_.front()) bucket = 1;
      if ((skip_begin_ && bucket == 0) || (skip_end_ && bucket == cutoffs_.size())) continue;
      publish(row, static_cast<double>(bucket - static_cast<std::size_t>(skip_begin_)));
    }
  } else if (op == "normalize" || op == "winsorize" || op == "zscore") {
    Moments moments; for (std::size_t row = 0; row < width; ++row) if (usable(0, row)) moments.add(args[0][row]);
    const auto ddof = op == "normalize" ? static_cast<std::size_t>(number("ddof", 0)) : 0U;
    const double deviation = moments.count > ddof ? static_cast<double>(std::sqrt(std::max(0.0L, moments.m2 / (moments.count - ddof)))) : missing;
    for (std::size_t row = 0; row < width; ++row) {
      if (!usable(0, row)) continue;
      if (op == "winsorize") { const long double span = number("std", 4) * static_cast<long double>(deviation); publish(row, std::clamp(static_cast<long double>(args[0][row]), moments.mean - span, moments.mean + span)); }
      else {
        long double value = static_cast<long double>(args[0][row]) - moments.mean;
        if (op == "zscore" || number("useStd", 0) != 0) { if (!(deviation > 0)) continue; value /= deviation; }
        const auto limit = op == "normalize" ? number("limit", 0) : 0;
        if (limit > 0) value = std::clamp(value, -static_cast<long double>(limit), static_cast<long double>(limit));
        publish(row, value);
      }
    }
  } else if (op == "rank" || op == "quantile") {
    std::vector<double> values(width, missing); std::size_t count = 0;
    for (std::size_t row = 0; row < width; ++row) if (usable(0, row)) { values[row] = args[0][row]; ++count; }
    const auto ranked = ranks(values);
    const auto driver = spec_.strings.contains("driver") ? spec_.strings.at("driver") : "gaussian";
    for (std::size_t row = 0; row < width; ++row) if (finite(ranked[row])) {
      if (op == "rank") publish(row, ranked[row]);
      else { const double probability = 1.0 / count + ranked[row] * (1.0 - 2.0 / count); publish(row, brain_distribution_quantile(probability, driver) * number("sigma", 1)); }
    }
  } else if (op == "scale") {
    long double absolute = 0, positive = 0, negative = 0;
    for (std::size_t row = 0; row < width; ++row) if (usable(0, row)) { absolute += std::abs(static_cast<long double>(args[0][row])); if (args[0][row] > 0) positive += args[0][row]; else negative -= args[0][row]; }
    const bool sides = spec_.numbers.contains("longscale") || spec_.numbers.contains("shortscale");
    for (std::size_t row = 0; row < width; ++row) if (usable(0, row)) {
      const long double value = args[0][row];
      const long double denominator = sides ? (value > 0 ? positive : value < 0 ? negative : absolute) : absolute;
      const auto multiplier = number("scale", 1) * (sides ? number(value >= 0 ? "longscale" : "shortscale", 1) : 1);
      if (denominator > 0) publish(row, value / denominator * multiplier);
      else if (value == 0) publish(row, 0);
    }
  } else {
    for (std::size_t row = 0; row < width; ++row) {
      if (!result.ready[row]) continue;
      long double value = op == "multiply" ? 1 : 0;
      bool valid = true; std::size_t count = 0;
      for (std::size_t a = 0; a < args.size(); ++a) {
        double input = args[a][row];
        if (!finite(input)) {
          if ((op == "add" || op == "subtract" || op == "multiply") && number("filter", 0) != 0 && std::isnan(input)) input = op == "multiply" ? 1 : 0;
          else if ((op == "vec_avg" || op == "vec_sum") && std::isnan(input)) continue;
          else { valid = false; break; }
        }
        if (op == "abs") value = std::abs(input);
        else if (op == "reverse") value = -input;
        else if (op == "inverse") { if (input == 0) valid = false; else value = 1.0L / input; }
        else if (op == "add" || op == "vec_avg" || op == "vec_sum") value += input;
        else if (op == "multiply") value *= input;
        else if (op == "subtract") value = a == 0 ? input : value - input;
        else if (op == "divide") { if (a == 0) value = input; else if (input == 0) valid = false; else value /= input; }
        else if (op == "min") value = count == 0 ? input : std::min(value, static_cast<long double>(input));
        else if (op == "max") value = count == 0 ? input : std::max(value, static_cast<long double>(input));
        ++count;
      }
      if ((op == "vec_avg" || op == "vec_sum") && count == 0) valid = false;
      if (op == "vec_avg" && count) value /= count;
      if (valid) publish(row, value);
    }
  }
  last_time_ = time;
  return result;
}
} // namespace factorpool::operators
