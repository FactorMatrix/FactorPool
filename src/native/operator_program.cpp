#include "operator_program.hpp"
#include <algorithm>
#include <map>
#include <set>

namespace factorpool::operators {
namespace {
using Kind = shared::Value::Kind;
struct Cell {
    shared::Value value;
    // Readiness is separate from a known missing numeric value. Only the
    // former prevents an explicit is_nan/if_else from handling current data.
    std::vector<std::uint8_t> ready;
};
const std::map<std::string, CalendarOp> calendar_ops{
    {"calendar_delay", CalendarOp::Delay}, {"calendar_delta", CalendarOp::Delta},
    {"calendar_mean", CalendarOp::Mean}, {"calendar_sum", CalendarOp::Sum},
    {"calendar_min", CalendarOp::Min}, {"calendar_max", CalendarOp::Max},
    {"calendar_std", CalendarOp::Std}, {"calendar_decay_linear", CalendarOp::DecayLinear}};
std::size_t arity(const std::string& op) {
    if (op == "input" || op == "constant" || op == "valid") return 0;
    if (op == "brain_ready") return 1;
    if (op == "if_else" || op == "select" || op == "scatter") return 3;
    if (calendar_ops.contains(op) || op == "sqrt" || op == "log" || op == "sign" || op == "is_nan" ||
        op == "neg" || op == "not" || op == "rank" || op == "rank_unit" || op == "zscore" ||
        op == "normalize" || op == "scale" || op == "scale_down" || op == "size" || op == "count_nonzero") return 1;
    return 2;
}
bool known(const std::string& op) {
    if (is_extension(op) || calendar_ops.contains(op) || op == "temporal" || op == "brain_ready") return true;
    if (op.starts_with("brain_ts_")) return is_brain_temporal(op.substr(9));
    if (op.starts_with("brain_cs_")) return BrainCross::supports(op.substr(9));
    try { shared::parse_op(op); return op != "dependency" && op != "value"; }
    catch (const std::invalid_argument&) { return false; }
}
void check_symbols(const std::vector<std::string>& symbols) {
    if (!std::is_sorted(symbols.begin(), symbols.end()) || std::adjacent_find(symbols.begin(), symbols.end()) != symbols.end() ||
            std::any_of(symbols.begin(), symbols.end(), [](const auto& value) { return value.empty(); }))
        throw std::invalid_argument("Pool operator symbols must be sorted, nonempty and unique");
}
bool valid_at(const shared::Value& value, std::size_t slot) {
    return value.kind == Kind::Scalar ? value.scalar_valid : value.valid.at(slot);
}
double number_at(const shared::Value& value, std::size_t slot) {
    return value.kind == Kind::Scalar ? value.number : value.values.at(slot);
}
void mask_unready(Cell& cell) {
    if (cell.value.kind == Kind::Vector)
        for (std::size_t slot = 0; slot < cell.value.axis.size(); ++slot)
            cell.value.valid[slot] &= cell.ready.at(cell.value.axis[slot]);
}
}
Program::Program(Spec spec, std::vector<std::string> symbols): spec_(std::move(spec)), symbols_(std::move(symbols)) {
    check_symbols(symbols_);
    if (spec_.nodes.empty() || spec_.output >= spec_.nodes.size() || spec_.ready >= spec_.nodes.size())
        throw std::invalid_argument("Pool operator IR requires valid output/readiness nodes");
    for (std::size_t index = 0; index < spec_.nodes.size(); ++index) {
        const auto& node = spec_.nodes[index];
        if (!known(node.op)) throw std::invalid_argument("Unknown Pool operator: " + node.op);
        if (!std::isfinite(node.value)) throw std::invalid_argument("Pool IR constants must be finite");
        if (node.op != "temporal" && !node.op.starts_with("brain_ts_") && !node.op.starts_with("brain_cs_") && node.inputs.size() != arity(node.op))
            throw std::invalid_argument("Pool operator node has incorrect arity");
        for (auto source : node.inputs) if (source >= index) throw std::invalid_argument("Pool operator IR must be topologically ordered");
        if (node.op == "input" && node.field.empty()) throw std::invalid_argument("Pool input field must be named");
        if (calendar_ops.contains(node.op)) {
            if (node.value < 0 || node.value > INT32_MAX || std::trunc(node.value) != node.value || node.dependency > 1)
                throw std::invalid_argument("Invalid Pool calendar window period/ddof");
            auto state = std::make_unique<CalendarState>(calendar_ops.at(node.op), static_cast<std::size_t>(node.value), static_cast<int>(node.dependency));
            state->set_axis(symbols_); calendar_.emplace(index, std::move(state));
        }
        if (node.op.starts_with("brain_ts_")) {
            for (const auto& [key, value] : node.numbers) {
                if (!std::isfinite(value)) throw std::invalid_argument("Core temporal options must be finite");
                if ((key == "period" || key == "k" || key == "lag") && (value < 0 || value > 100000 || std::trunc(value) != value))
                    throw std::invalid_argument("Core temporal index/period exceeds its bounded integer range");
            }
            const auto number = [&](const std::string& key, double fallback) { auto found = node.numbers.find(key); return found == node.numbers.end() ? fallback : found->second; };
            const auto text = [&](const std::string& key, const std::string& fallback) { auto found = node.strings.find(key); return found == node.strings.end() ? fallback : found->second; };
            BrainTemporalSpec plan; plan.op = node.op.substr(9);
            plan.period = static_cast<std::size_t>(number("period", node.value));
            plan.k = static_cast<std::size_t>(number("k", 1)); plan.lag = static_cast<std::size_t>(number("lag", 0));
            plan.rettype = static_cast<int>(number("rettype", 0)); plan.ddof = static_cast<int>(number("ddof", 0));
            plan.constant = number("constant", 0); plan.hump = number("hump", .01); plan.dense = number("dense", 0) != 0;
            plan.valid_observations = text("clock", "trading_days") == "valid_observations";
            plan.driver = text("driver", "gaussian"); plan.ignore = text("ignore", "NaN");
            auto state = std::make_unique<BrainTemporal>(std::move(plan)); state->set_axis(symbols_); brain_ts_.emplace(index, std::move(state));
        }
        if (node.op.starts_with("brain_cs_")) {
            auto state = std::make_unique<BrainCross>(BrainCrossSpec{node.op.substr(9), node.numbers, node.strings});
            state->set_axis(symbols_); brain_cs_.emplace(index, std::move(state));
        }
        if (node.op == "temporal") {
            if (node.dependency >= spec_.temporals.size()) throw std::invalid_argument("Unknown Pool temporal port");
            const auto& port = spec_.temporals[node.dependency];
            if (node.inputs.size() != (port.deferred ? 1 + port.bindings.size() : 0))
                throw std::invalid_argument("Pool temporal bindings differ from prepared plan");
            if (node.field != "current" && node.field != "ready") throw std::invalid_argument("Unknown Pool temporal projection");
        }
    }
    for (const auto& port : spec_.temporals) ports_.push_back(std::make_unique<graph::Graph>(port.spec, symbols_));
    current_.assign(symbols_.size(), shared::missing); valid_.resize(symbols_.size()); ready_.resize(symbols_.size());
}
void Program::set_symbols(std::vector<std::string> symbols) {
    check_symbols(symbols);
    if (symbols == symbols_) return;
    for (auto& port : ports_) port->set_symbols(symbols);
    for (auto& [index, state] : calendar_) { (void)index; state->set_axis(symbols); }
    for (auto& [index, state] : brain_ts_) { (void)index; state->set_axis(symbols); }
    for (auto& [index, state] : brain_cs_) { (void)index; state->set_axis(symbols); }
    symbols_ = std::move(symbols);
    current_.assign(symbols_.size(), shared::missing); valid_.assign(symbols_.size(), 0); ready_.assign(symbols_.size(), 0);
}
bool Program::independent_frames() const {
    return ports_.empty() && calendar_.empty() && brain_ts_.empty() &&
        std::none_of(spec_.nodes.begin(), spec_.nodes.end(), [](const auto& node) {
            return node.op == "brain_cs_trade_when" || node.op == "brain_cs_group_backfill";
        });
}
void Program::update(std::int64_t time, std::span<const graph::Column> columns, std::span<const std::uint8_t> present) {
    const auto width = symbols_.size();
    if (time < time_ || (!calendar_.empty() && time <= time_))
        throw std::invalid_argument("Pool operator updates must be chronological; calendar dates must be unique");
    if (present.size() != width) throw std::invalid_argument("Pool present mask differs from symbol axis");
    std::map<std::string, const graph::Column*> by_field;
    std::vector<std::vector<std::uint8_t>> column_masks(columns.size());
    std::vector<graph::Column> finite_columns;
    for (std::size_t i = 0; i < columns.size(); ++i) {
        const auto& column = columns[i];
        if (column.values.size() != width || (!column.valid.empty() && column.valid.size() != width))
            throw std::invalid_argument("Pool input column differs from symbol axis");
        if (!by_field.emplace(column.field, &column).second) throw std::invalid_argument("Duplicate Pool input field");
        auto& mask = column_masks[i]; mask.resize(width);
        for (std::size_t row = 0; row < width; ++row)
            mask[row] = std::isfinite(column.values[row]) && (column.valid.empty() || column.valid[row]);
        finite_columns.push_back({column.field, column.values, mask});
    }
    std::vector<std::uint8_t> evaluated(ports_.size());
    const auto update_port = [&](std::size_t slot, const std::vector<Cell>& cells, const Node& node) {
        if (evaluated[slot]) return;
        const auto& port = spec_.temporals[slot];
        std::vector<graph::Column> combined(finite_columns);
        std::vector<std::vector<std::uint8_t>> masks;
        std::vector<std::uint8_t> active(present.begin(), present.end());
        if (port.deferred) {
            const auto& guard = cells.at(node.inputs.at(0));
            if (guard.value.kind != Kind::Scalar) throw std::invalid_argument("Pool temporal guard must be scalar");
            const bool enabled = guard.value.scalar_valid && guard.value.number != 0;
            for (std::size_t row = 0; row < width; ++row) active[row] &= enabled && guard.ready[row];
            masks.resize(port.bindings.size());
            for (std::size_t i = 0; i < port.bindings.size(); ++i) {
                const auto& source = cells.at(node.inputs.at(i + 1));
                shared::require_vector(source.value);
                if (source.value.axis != shared::RowAxis::identity(width)) throw std::invalid_argument("Pool temporal input must preserve the full axis");
                auto& mask = masks[i]; mask.resize(width);
                for (std::size_t row = 0; row < width; ++row) mask[row] = source.value.valid[row] && source.ready[row];
                std::erase_if(combined, [&](const auto& column) { return column.field == port.bindings[i]; });
                combined.push_back({port.bindings[i], source.value.values, mask});
            }
        }
        ports_[slot]->update_completed(time, combined, active);
        evaluated[slot] = 1;
    };
    std::vector<Cell> cells; cells.reserve(spec_.nodes.size());
    for (const auto& node : spec_.nodes) {
        Cell cell; cell.ready.assign(width, 1);
        std::vector<const shared::Value*> arguments;
        for (auto source : node.inputs) {
            const auto& input = cells.at(source);
            arguments.push_back(&input.value);
            for (std::size_t row = 0; row < width; ++row) cell.ready[row] &= input.ready[row];
        }
        const auto& op = node.op;
        if (op.starts_with("brain_ts_") || op.starts_with("brain_cs_")) {
            std::vector<std::vector<double>> data(arguments.size(), std::vector<double>(width, shared::missing));
            std::vector<std::vector<std::uint8_t>> ready(arguments.size(), std::vector<std::uint8_t>(width));
            for (std::size_t a = 0; a < arguments.size(); ++a) {
                const auto& value = *arguments[a];
                if (value.kind != Kind::Scalar && (value.kind != Kind::Vector || value.axis != shared::RowAxis::identity(width)))
                    throw std::invalid_argument("Core operators require the complete security axis");
                for (std::size_t row = 0; row < width; ++row) {
                    const auto number = number_at(value, row);
                    data[a][row] = valid_at(value, row) || !std::isfinite(number) ? number : shared::missing;
                    ready[a][row] = cells.at(node.inputs[a]).ready[row];
                }
            }
            auto result = op.starts_with("brain_ts_")
                ? brain_ts_.at(cells.size())->update(time, data, ready, std::vector<std::uint8_t>(present.begin(), present.end()))
                : brain_cs_.at(cells.size())->update(time, data, ready, std::vector<std::uint8_t>(present.begin(), present.end()));
            cell.value = shared::Value::vector(width); cell.value.values = std::move(result.values);
            cell.value.valid = std::move(result.valid); cell.ready = std::move(result.ready);
        } else if (op == "brain_ready") {
            cell.value = shared::Value::vector(width, true); cell.ready.assign(width, 1);
            for (std::size_t row = 0; row < width; ++row) {
                cell.value.values[row] = cells.at(node.inputs[0]).ready[row]; cell.value.valid[row] = present[row];
            }
        } else if (op == "input") {
            cell.value = shared::Value::vector(width);
            if (const auto found = by_field.find(node.field); found != by_field.end()) {
                const auto& source = *found->second;
                cell.value.values.assign(source.values.begin(), source.values.end());
                for (std::size_t row = 0; row < width; ++row)
                    cell.value.valid[row] = present[row] && std::isfinite(source.values[row]) && (source.valid.empty() || source.valid[row]);
            }
        } else if (op == "constant") cell.value = shared::Value::scalar(node.value);
        else if (op == "valid") {
            cell.value = shared::Value::vector(width, true);
            for (std::size_t row = 0; row < width; ++row) {
                bool usable = present[row];
                for (const auto& required : spec_.nodes) if (required.op == "input") {
                    auto found = by_field.find(required.field);
                    usable &= found != by_field.end() && std::isfinite(found->second->values[row]) &&
                        (found->second->valid.empty() || found->second->valid[row]);
                }
                cell.value.values[row] = usable; cell.value.valid[row] = 1;
            }
        } else if (op == "temporal") {
            update_port(node.dependency, cells, node);
            const auto& state = ports_[node.dependency]->current_snapshot();
            cell.value = shared::Value::vector(width, node.field == "ready");
            for (std::size_t row = 0; row < width; ++row) {
                if (node.field == "ready") {
                    cell.value.values[row] = state.ready[row] && state.valid[row] && state.end_time[row] == time;
                    cell.value.valid[row] = present[row]; cell.ready[row] = 1;
                } else {
                    cell.value.values[row] = state.valid[row] && state.end_time[row] == time ? state.current[row] : shared::missing;
                    cell.value.valid[row] = present[row] && state.valid[row] && state.ready[row] && state.end_time[row] == time;
                    cell.ready[row] = state.ready[row];
                }
            }
        } else if (calendar_ops.contains(op)) {
            const auto& source = cells.at(node.inputs[0]); shared::require_vector(source.value);
            if (source.value.axis != shared::RowAxis::identity(width)) throw std::invalid_argument("Calendar input must preserve full axis");
            auto values = source.value.values;
            for (std::size_t row = 0; row < width; ++row) if (!source.value.valid[row]) values[row] = shared::missing;
            auto result = calendar_.at(cells.size())->update(time, values, source.ready, std::vector<std::uint8_t>(present.begin(), present.end()));
            cell.value = shared::Value::vector(width); cell.value.values = std::move(result.values);
            cell.value.valid = std::move(result.valid); cell.ready = std::move(result.ready);
        } else if (op == "if_else" || op == "select") {
            // Pool's explicit selector uses one scalar/vector missing rule.
            cell.value = extension("if_else", arguments);
            const auto& condition = cells[node.inputs[0]];
            const auto slots = condition.value.kind == Kind::Scalar ? width : condition.value.axis.size();
            cell.ready.assign(width, 0);
            for (std::size_t slot = 0; slot < slots; ++slot) {
                const auto row = condition.value.kind == Kind::Scalar ? slot : condition.value.axis[slot];
                const bool valid = valid_at(condition.value, slot);
                const auto branch = valid && number_at(condition.value, slot) != 0 ? 1 : 2;
                cell.ready[row] = condition.ready[row] && cells[node.inputs[branch]].ready[row];
            }
        } else if (is_extension(op)) cell.value = extension(op, arguments);
        else if (op == "size") cell.value = shared::Value::scalar(arguments[0]->kind == Kind::Indices ? arguments[0]->indices.size() : arguments[0]->values.size());
        else if (op == "count_nonzero") {
            shared::require_vector(*arguments[0]); std::size_t count = 0;
            for (std::size_t row = 0; row < arguments[0]->values.size(); ++row)
                count += arguments[0]->valid[row] && arguments[0]->values[row] != 0;
            cell.value = shared::Value::scalar(static_cast<double>(count));
        } else {
            const auto code = shared::parse_op(op);
            if (op == "neg" || op == "not") cell.value = shared::unary(code, *arguments[0]);
            else if (op == "where") cell.value = shared::where(*arguments[0], *arguments[1]);
            else if (op == "filter") cell.value = shared::filter(*arguments[0], *arguments[1]);
            else if (op == "rank") cell.value = shared::rank(*arguments[0], node.field == "descending", node.value != 0);
            else if (op == "bottomk") cell.value = shared::bottomk(*arguments[0], *arguments[1]);
            else if (op == "scatter") cell.value = shared::scatter(*arguments[0], *arguments[1], *arguments[2]);
            else if (code >= shared::Op::RankUnit && code <= shared::Op::ScaleDown) cell.value = shared::cross_section(code, *arguments[0]);
            else if (code >= shared::Op::GroupNeutralize && code <= shared::Op::GroupMedian) cell.value = shared::grouped(code, *arguments[0], *arguments[1]);
            else cell.value = shared::binary(code, *arguments[0], *arguments[1]);
        }
        mask_unready(cell);
        if (cell.value.kind == Kind::Vector)
            for (std::size_t slot = 0; slot < cell.value.axis.size(); ++slot)
                cell.value.valid[slot] &= present[cell.value.axis[slot]];
        cells.push_back(std::move(cell));
    }
    const auto& output = cells.at(spec_.output); const auto& readiness = cells.at(spec_.ready);
    if ((output.value.kind != Kind::Scalar && (output.value.kind != Kind::Vector || output.value.axis != shared::RowAxis::identity(width))) ||
        (readiness.value.kind != Kind::Scalar && (readiness.value.kind != Kind::Vector || readiness.value.axis != shared::RowAxis::identity(width))))
        throw std::invalid_argument("Pool output and readiness must preserve the full symbol axis");
    current_.assign(width, shared::missing); valid_.assign(width, 0); ready_.assign(width, 0);
    for (std::size_t row = 0; row < width; ++row) {
        ready_[row] = present[row] && output.ready[row] && readiness.ready[row] && valid_at(readiness.value, row) && number_at(readiness.value, row) != 0;
        valid_[row] = ready_[row] && valid_at(output.value, row) && std::isfinite(number_at(output.value, row));
        if (valid_[row]) current_[row] = number_at(output.value, row);
    }
    time_ = time;
}
}
