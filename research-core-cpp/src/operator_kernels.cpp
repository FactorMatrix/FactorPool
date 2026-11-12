#include "operator_program.hpp"
#include <algorithm>
#include <cmath>
#include <set>

namespace factorpool::operators {
bool is_extension(const std::string& op) {
    static const std::set<std::string> names{"sqrt", "log", "power", "signed_power", "sign", "is_nan", "if_else", "mod"};
    return names.contains(op);
}
namespace {
double real_power(double base, double exponent) {
    if ((base == 0 && exponent < 0) || (base < 0 && std::trunc(exponent) != exponent)) return shared::missing;
    return std::pow(base, exponent);
}
double calculate(const std::string& op, double a, double b) {
    if (op == "sqrt") return a >= 0 ? std::sqrt(a) : shared::missing;
    if (op == "log") return a > 0 ? std::log(a) : shared::missing;
    if (op == "sign") return a > 0 ? 1 : a < 0 ? -1 : 0;
    if (op == "power") return real_power(a, b);
    if (op == "signed_power") {
        if (a == 0) return b < 0 ? shared::missing : 0;
        return std::copysign(real_power(std::abs(a), b), a);
    }
    if (op == "mod") {
        if (b == 0) return shared::missing;
        double result = std::fmod(a, b);
        if (result != 0 && std::signbit(result) != std::signbit(b)) result += b;
        return result == 0 ? std::copysign(0.0, b) : result;
    }
    throw std::invalid_argument("Unknown Pool numeric operator: " + op);
}
bool valid_at(const shared::Value& value, std::size_t slot) {
    return value.kind == shared::Value::Kind::Scalar ? value.scalar_valid : value.valid.at(slot);
}
double number_at(const shared::Value& value, std::size_t slot) {
    return value.kind == shared::Value::Kind::Scalar ? value.number : value.values.at(slot);
}
}
shared::Value extension(const std::string& op, const std::vector<const shared::Value*>& args) {
    const shared::Value* axis = nullptr;
    for (const auto* arg : args) {
        if (arg->kind == shared::Value::Kind::Indices) throw std::invalid_argument("Pool numeric operators do not accept positions");
        if (axis) shared::require_axis(*axis, *arg);
        if (arg->kind == shared::Value::Kind::Vector) axis = arg;
    }
    const auto apply = [&](std::size_t slot) -> double {
        const auto& first = *args.at(0);
        const double a = number_at(first, slot);
        if (op == "is_nan") {
            // An observed NaN can be inspected even though its numeric mask
            // is false. Infinity remains a distinct invalid input.
            if (std::isnan(a)) return 1;
            return valid_at(first, slot) && std::isfinite(a) ? 0 : shared::missing;
        }
        if (!valid_at(first, slot) || !std::isfinite(a)) return shared::missing;
        if (op == "if_else") {
            const auto& selected = *args.at(a != 0 ? 1 : 2);
            return valid_at(selected, slot) ? number_at(selected, slot) : shared::missing;
        }
        double b = 0;
        if (args.size() > 1) {
            if (!valid_at(*args[1], slot)) return shared::missing;
            b = number_at(*args[1], slot);
        }
        return calculate(op, a, b);
    };
    if (!axis) return shared::Value::scalar(apply(0), op == "is_nan");
    auto result = shared::Value::vector_on_axis(axis->axis, op == "is_nan");
    for (std::size_t row = 0; row < result.values.size(); ++row) {
        const double value = apply(row);
        if (std::isfinite(value)) { result.values[row] = value; result.valid[row] = 1; }
    }
    return result;
}
}
