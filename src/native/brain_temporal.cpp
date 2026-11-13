#include "brain_temporal.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <numbers>
#include <set>
#include <sstream>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace factorpool::operators {
namespace {
constexpr double missing = std::numeric_limits<double>::quiet_NaN();
const std::set<std::string> operations{
    "days_from_last_change", "hump", "kth_element", "last_diff_value",
    "ts_arg_max", "ts_arg_min", "ts_av_diff", "ts_backfill", "ts_corr",
    "ts_count_nans", "ts_covariance", "ts_decay_linear", "ts_delay", "ts_delta",
    "ts_mean", "ts_product", "ts_quantile", "ts_rank", "ts_regression", "ts_scale",
    "ts_std_dev", "ts_step", "ts_sum", "ts_zscore"};
std::string canonical(std::string op) {
    if (op.starts_with("brain_ts_")) op.erase(0, 9);
    if (operations.contains(op)) return op;
    if (operations.contains("ts_" + op)) return "ts_" + op;
    return op;
}
bool paired(const std::string& op) { return op == "ts_corr" || op == "ts_covariance" || op == "ts_regression"; }
struct Moments {
    std::size_t n = 0;
    long double mean = 0, squared = 0;
    void add(long double x) { const auto delta = x - mean; mean += delta / ++n; squared += delta * (x - mean); }
};
}

bool is_brain_temporal(const std::string& op) { return operations.contains(canonical(op)); }

double brain_distribution_quantile(double p, const std::string& driver) {
    if (!std::isfinite(p) || p < 0 || p > 1) return missing;
    if (driver == "uniform") return p;
    if (driver != "gaussian" && driver != "cauchy") throw std::invalid_argument("quantile driver must be gaussian, uniform or cauchy");
    if (p == 0 || p == 1) return missing;
    if (driver == "cauchy") return std::tan(std::numbers::pi * (p - .5));
    // Invert the standard normal CDF by monotonic bisection. erfc retains
    // tail precision, and the bounded work does not require another dependency.
    if (p == .5) return 0;
    const bool negative = p < .5;
    const double tail = negative ? p : 1 - p;
    double lower = 0, upper = 40;
    for (int i = 0; i != 90; ++i) {
        const double mid = (lower + upper) / 2;
        if (mid == lower || mid == upper) break;
        if (.5 * std::erfc(mid / std::numbers::sqrt2) > tail) lower = mid;
        else upper = mid;
    }
    return (negative ? -1 : 1) * ((lower + upper) / 2);
}

BrainTemporal::BrainTemporal(BrainTemporalSpec spec): spec_(std::move(spec)) {
    spec_.op = canonical(spec_.op);
    if (!operations.contains(spec_.op)) throw std::invalid_argument("Unknown BRAIN temporal operator: " + spec_.op);
    if (!std::isfinite(spec_.constant) || !std::isfinite(spec_.hump) || spec_.hump < 0)
        throw std::invalid_argument("BRAIN temporal constants must be finite; hump must be nonnegative");
    if (spec_.ddof < 0 || (spec_.period && static_cast<std::size_t>(spec_.ddof) >= spec_.period))
        throw std::invalid_argument("std ddof must be nonnegative and smaller than period");
    if (spec_.rettype < 0 || spec_.rettype > 9) throw std::invalid_argument("regression rettype must be 0..9");
    if (!spec_.k) throw std::invalid_argument("k must be positive");
    if (spec_.driver != "gaussian" && spec_.driver != "uniform" && spec_.driver != "cauchy")
        throw std::invalid_argument("quantile driver must be gaussian, uniform or cauchy");
    capacity_ = spec_.period;
    if (spec_.op == "ts_delay" || spec_.op == "ts_delta") {
        if (capacity_ == std::numeric_limits<std::size_t>::max()) throw std::invalid_argument("Lag is too large");
        ++capacity_;
    } else if (spec_.op == "ts_regression") {
        if (!spec_.period || spec_.lag > std::numeric_limits<std::size_t>::max() - capacity_) throw std::invalid_argument("Regression window is invalid");
        capacity_ += spec_.lag;
    } else if (!capacity_) throw std::invalid_argument("BRAIN temporal windows must be positive");
    std::string ignore = spec_.ignore;
    std::replace(ignore.begin(), ignore.end(), ',', ' ');
    std::istringstream tokens(ignore); std::string token;
    while (tokens >> token) {
        if (token == "NaN" || token == "nan" || token == "NAN") { ignore_nan_ = true; continue; }
        std::size_t consumed = 0; double value;
        try { value = std::stod(token, &consumed); } catch (...) { throw std::invalid_argument("ignore requires NaN or finite numbers"); }
        if (consumed != token.size() || !std::isfinite(value)) throw std::invalid_argument("ignore requires NaN or finite numbers");
        ignored_.push_back(value);
    }
}

void BrainTemporal::set_axis(const std::vector<std::string>& symbols) {
    std::unordered_set<std::string> unique;
    for (const auto& symbol : symbols) if (symbol.empty() || !unique.insert(symbol).second) throw std::invalid_argument("BRAIN temporal symbols must be nonempty and unique");
    if (symbols == symbols_) return;
    std::unordered_map<std::string, std::size_t> old;
    for (std::size_t i = 0; i < symbols_.size(); ++i) old.emplace(symbols_[i], i);
    std::vector<State> next(symbols.size());
    for (std::size_t i = 0; i < symbols.size(); ++i) if (const auto found = old.find(symbols[i]); found != old.end()) next[i] = std::move(state_[found->second]);
    state_ = std::move(next); symbols_ = symbols;
}

CalendarResult BrainTemporal::update(std::int64_t time,
    const std::vector<std::vector<double>>& arguments,
    const std::vector<std::vector<std::uint8_t>>& source_ready,
    const std::vector<std::uint8_t>& present) {
    const auto width = symbols_.size(); const auto& op = spec_.op;
    const std::size_t expected = op == "ts_step" ? 0 : paired(op) ? 2 : 1;
    if (arguments.size() != expected || source_ready.size() != expected || present.size() != width)
        throw std::invalid_argument("BRAIN temporal inputs do not match its arity or symbol axis");
    if (last_time_ && time <= *last_time_) throw std::invalid_argument("BRAIN temporal frames must be unique and chronological");
    if (std::any_of(present.begin(), present.end(), [](auto value) { return value > 1; })) throw std::invalid_argument("Presence must be a boolean mask");
    for (std::size_t arg = 0; arg < expected; ++arg)
        if (arguments[arg].size() != width || source_ready[arg].size() != width || std::any_of(source_ready[arg].begin(), source_ready[arg].end(), [](auto value) { return value > 1; }))
            throw std::invalid_argument("BRAIN temporal input columns/masks do not match the axis");
    CalendarResult result{std::vector<double>(width, missing), std::vector<std::uint8_t>(width, 0), std::vector<std::uint8_t>(width, 0)};
    const auto publish = [&](std::size_t row, long double value) {
        const double converted = static_cast<double>(value);
        if (present[row] && std::isfinite(value) && std::isfinite(converted)) { result.values[row] = converted; result.valid[row] = 1; }
    };
    long double limit = 0;
    if (op == "hump") {
        for (std::size_t row = 0; row < width; ++row)
            if (present[row] && source_ready[0][row] && std::isfinite(arguments[0][row])) limit += std::abs(static_cast<long double>(arguments[0][row]));
        limit *= spec_.hump;
    }
    ++frames_;
    for (std::size_t row = 0; row < width; ++row) {
        if (op == "ts_step") { result.ready[row] = 1; publish(row, static_cast<long double>(frames_) * spec_.period); continue; }
        auto& state = state_[row];
        const bool x_ready = source_ready[0][row], y_ready = expected == 1 || source_ready[1][row];
        const double x = present[row] && x_ready ? arguments[0][row] : missing;
        const double y = expected == 2 && present[row] && y_ready ? arguments[1][row] : missing;
        if (op == "hump") {
            result.ready[row] = x_ready;
            if (!std::isfinite(x)) continue;
            long double value = x;
            if (state.previous) {
                const long double change = static_cast<long double>(x) - *state.previous;
                value = *state.previous;
                if (std::abs(change) > limit) value += std::copysign(limit, change);
            }
            publish(row, value);
            if (result.valid[row]) state.previous = result.values[row];
            continue;
        }
        if (op == "days_from_last_change") {
            result.ready[row] = x_ready;
            if (!std::isfinite(x)) {
                if (!spec_.valid_observations) { state.previous.reset(); state.unchanged = 0; }
                continue;
            }
            state.unchanged = state.previous && x == *state.previous ? state.unchanged + 1 : 0;
            state.previous = x; publish(row, state.unchanged); continue;
        }
        if (spec_.valid_observations && (!present[row] || !x_ready || !std::isfinite(x) || (expected == 2 && (!y_ready || !std::isfinite(y))))) {
            result.ready[row] = x_ready && y_ready && state.slots.size() >= capacity_;
            continue;
        }
        auto& slots = state.slots; slots.push_back({x, y, x_ready, y_ready});
        if (slots.size() > capacity_) slots.pop_front();
        if (slots.size() < capacity_) continue;
        if (op == "ts_delay" || op == "ts_delta") {
            result.ready[row] = slots.front().x_ready && (op == "ts_delay" || slots.back().x_ready);
            if (!result.ready[row] || !std::isfinite(slots.front().x) || (op == "ts_delta" && !std::isfinite(x))) continue;
            publish(row, op == "ts_delay" ? static_cast<long double>(slots.front().x) : static_cast<long double>(x) - slots.front().x);
            continue;
        }
        bool ready = true;
        if (op == "ts_regression") {
            for (std::size_t n = spec_.lag; n < slots.size(); ++n) ready &= slots[n].x_ready && slots[n-spec_.lag].y_ready;
        } else for (const auto& slot : slots) ready &= slot.x_ready && (!paired(op) || slot.y_ready);
        result.ready[row] = ready;
        if (!ready) continue;
        if (paired(op)) {
            std::vector<long double> a, b;
            const auto offset = op == "ts_regression" ? spec_.lag : 0;
            bool infinite = false;
            for (std::size_t n = offset; n < slots.size(); ++n) {
                const double first = slots[n].x, second = slots[n-offset].y;
                infinite |= std::isinf(first) || std::isinf(second);
                if (std::isfinite(first) && std::isfinite(second)) { a.push_back(first); b.push_back(second); }
            }
            if (infinite || a.size() < (op == "ts_covariance" ? 1U : 2U)) continue;
            Moments ma, mb;
            for (std::size_t n = 0; n < a.size(); ++n) { ma.add(a[n]); mb.add(b[n]); }
            long double cross = 0;
            for (std::size_t n = 0; n < a.size(); ++n) cross += (a[n] - ma.mean) * (b[n] - mb.mean);
            if (op == "ts_covariance") { publish(row, cross / a.size()); continue; }
            if (op == "ts_corr") {
                if (ma.squared > 0 && mb.squared > 0) publish(row, std::clamp(cross / std::sqrt(ma.squared * mb.squared), -1.0L, 1.0L));
                continue;
            }
            // Input order is y,x. Lag pairs current y with x from lag frames ago.
            if (!(mb.squared > 0)) continue;
            const long double beta = cross / mb.squared, alpha = ma.mean - beta * mb.mean;
            long double sse = 0;
            for (std::size_t n = 0; n < a.size(); ++n) { const auto residual = a[n] - alpha - beta * b[n]; sse += residual * residual; }
            const double current_y = slots.back().x, current_x = slots[slots.size()-1-spec_.lag].y;
            const long double fit = alpha + beta * current_x;
            switch (spec_.rettype) {
            case 0: if (std::isfinite(current_y) && std::isfinite(current_x)) publish(row, current_y - fit); break;
            case 1: publish(row, alpha); break;
            case 2: publish(row, beta); break;
            case 3: if (std::isfinite(current_x)) publish(row, fit); break;
            case 4: publish(row, sse); break;
            case 5: publish(row, ma.squared); break;
            case 6: if (ma.squared > 0) publish(row, 1 - sse / ma.squared); break;
            case 7: if (a.size() > 2) publish(row, sse / (a.size() - 2)); break;
            case 8: if (a.size() > 2) publish(row, std::sqrt(sse / (a.size() - 2) / mb.squared)); break;
            case 9: if (a.size() > 2) publish(row, std::sqrt(sse / (a.size() - 2) * (1.0L / a.size() + mb.mean * mb.mean / mb.squared))); break;
            }
            continue;
        }
        if (std::any_of(slots.begin(), slots.end(), [](const auto& slot) { return std::isinf(slot.x); })) continue;
        if (op == "ts_count_nans") {
            publish(row, std::count_if(slots.begin(), slots.end(), [](const auto& slot) { return std::isnan(slot.x); })); continue;
        }
        if (op == "ts_backfill" || op == "kth_element") {
            std::size_t seen = 0;
            for (auto it = slots.rbegin(); it != slots.rend(); ++it) {
                const auto value = it->x;
                const bool skip = op == "ts_backfill" ? !std::isfinite(value) : (std::isnan(value) && ignore_nan_) || std::find(ignored_.begin(), ignored_.end(), value) != ignored_.end();
                if (!skip && ++seen == spec_.k) { publish(row, value); break; }
            }
            continue;
        }
        if (op == "last_diff_value") {
            if (!std::isfinite(x)) continue;
            for (auto it = slots.rbegin(); it != slots.rend(); ++it) if (std::isfinite(it->x) && it->x != x) { publish(row, it->x); break; }
            continue;
        }
        if (op == "ts_decay_linear") {
            long double total = 0, weights = 0; std::size_t weight = 0;
            for (const auto& slot : slots) {
                ++weight;
                if (std::isfinite(slot.x)) { total += static_cast<long double>(weight) * slot.x; weights += weight; }
                else if (!spec_.dense) weights += weight;
            }
            if (weights > 0) publish(row, total / weights);
            continue;
        }
        Moments moments; double minimum = std::numeric_limits<double>::infinity(), maximum = -minimum;
        long double total = 0, product = 1; bool all_finite = true;
        for (const auto& slot : slots) {
            if (!std::isfinite(slot.x)) { all_finite = false; continue; }
            moments.add(slot.x); total += slot.x; product *= slot.x;
            minimum = std::min(minimum, slot.x); maximum = std::max(maximum, slot.x);
        }
        if (!moments.n) continue;
        if (op == "ts_mean") publish(row, moments.mean);
        else if (op == "ts_sum") publish(row, total);
        else if (op == "ts_product") { if (all_finite) publish(row, product); }
        else if (op == "ts_std_dev") { if (moments.n > static_cast<std::size_t>(spec_.ddof)) publish(row, std::sqrt(std::max(0.0L, moments.squared / (moments.n - spec_.ddof)))); }
        else if (op == "ts_av_diff") { if (std::isfinite(x)) publish(row, static_cast<long double>(x) - moments.mean); }
        else if (op == "ts_zscore") { if (std::isfinite(x) && moments.squared > 0 && moments.n > static_cast<std::size_t>(spec_.ddof)) publish(row, (x - moments.mean) / std::sqrt(moments.squared / (moments.n - spec_.ddof))); }
        else if (op == "ts_scale") { if (std::isfinite(x) && maximum > minimum) publish(row, (static_cast<long double>(x) - minimum) / (static_cast<long double>(maximum) - minimum) + spec_.constant); }
        else if (op == "ts_arg_max" || op == "ts_arg_min") {
            const double extreme = op == "ts_arg_max" ? maximum : minimum;
            std::size_t distance = 0;
            for (auto it = slots.rbegin(); it != slots.rend(); ++it, ++distance) if (it->x == extreme) { publish(row, distance); break; }
        } else if (op == "ts_rank" || op == "ts_quantile") {
            if (!std::isfinite(x)) continue;
            std::size_t lower = 0, equal = 0;
            for (const auto& slot : slots) if (std::isfinite(slot.x)) { lower += slot.x < x; equal += slot.x == x; }
            const long double midpoint = lower + (equal - 1) / 2.0L;
            if (op == "ts_rank") publish(row, (moments.n == 1 ? .5L : midpoint / (moments.n - 1)) + spec_.constant);
            else publish(row, brain_distribution_quantile(static_cast<double>((midpoint + .5L) / moments.n), spec_.driver));
        }
    }
    last_time_ = time;
    return result;
}
} // namespace factorpool::operators
