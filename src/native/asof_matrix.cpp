#include "asof_matrix.hpp"
#include "market.hpp"
#include "parallel.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>

namespace factorpool::operators {
namespace {
constexpr double missing = std::numeric_limits<double>::quiet_NaN();
bool adjusted(const std::string& field) {
    return field == "open" || field == "high" || field == "low" || field == "close" ||
           field == "prev_close" || field == "value" || field == "volume";
}
bool positive(double value) { return std::isfinite(value) && value > 0; }
bool stateful(graph::Op op) { return op >= graph::Op::Delay; }
struct Port {
    graph::Spec spec;
    std::vector<std::size_t> fields;
    std::vector<std::uint8_t> kinds;
    bool rebased = false, missing_prefix_inert = false, completed = true;
};
Port prepare_port(const graph::Spec& spec, const AsOfHistory& history, bool completed) {
    Port port{spec, {}, {}, false, false, completed};
    std::vector<bool> blocked(spec.nodes.size(), false);
    bool all_state_blocked = true;
    for (std::size_t i = 0; i < spec.nodes.size(); ++i) {
        const auto& node = spec.nodes[i];
        if (node.op == graph::Op::Input) {
            const auto field = std::find_if(history.fields.begin(), history.fields.end(),
                [&](const auto& field) { return field.name == node.field; });
            if (field == history.fields.end()) throw std::invalid_argument("As-of temporal input field is unavailable");
            const auto index = static_cast<std::size_t>(field - history.fields.begin());
            if (std::find(port.fields.begin(), port.fields.end(), index) == port.fields.end()) {
                port.fields.push_back(index);
                port.kinds.push_back(adjusted(node.field) ? node.field == "volume" ? 2 : 1 : 0);
            }
            blocked[i] = adjusted(node.field); port.rebased |= blocked[i];
        } else if (node.op != graph::Op::Constant && node.op != graph::Op::Ready && node.op != graph::Op::Select) {
            // These public scalar Graph operations require every input valid.
            // Ready/Select are deliberately conservative: missing input may
            // become a finite control value or select a constant branch.
            blocked[i] = std::any_of(node.inputs.begin(), node.inputs.end(), [&](auto source) { return blocked.at(source); });
        }
        if (stateful(node.op)) {
            const bool input_blocked = std::any_of(node.inputs.begin(), node.inputs.end(), [&](auto source) { return blocked.at(source); });
            all_state_blocked &= input_blocked;
        }
    }
    port.missing_prefix_inert = port.rebased && all_state_blocked;
    return port;
}
struct Publication {
    std::vector<double> current;
    std::vector<std::uint8_t> valid, ready;
    explicit Publication(std::size_t cells): current(cells, missing), valid(cells), ready(cells) {}
};
struct Counts { std::size_t updates = 0, replay = 0, advance = 0, cohorts = 0; };
struct Cohort {
    std::vector<std::size_t> columns, axis;
    std::unique_ptr<graph::Graph> graph;
    std::vector<std::size_t> selected;
    std::vector<std::vector<double>> values;
    std::vector<std::vector<std::uint8_t>> masks;
    std::vector<std::uint8_t> present;
    std::vector<double> hq, bq;
    std::vector<std::int64_t> hs, bs;
    std::vector<graph::Column> input;
    Cohort(std::vector<std::size_t> value, const graph::Spec& spec)
        : columns(std::move(value)), graph(std::make_unique<graph::Graph>(spec, std::vector<std::string>{})) {}
};
void set_axis(Cohort& cohort, const std::vector<std::size_t>& axis, const AsOfHistory& history) {
    if (cohort.axis == axis) return;
    std::vector<std::string> symbols; symbols.reserve(axis.size());
    for (auto n : axis) symbols.push_back(history.symbols[n]);
    cohort.graph->set_symbols(std::move(symbols)); cohort.axis = axis;
}
void update_frame(Cohort& cohort, const Port& port, const AsOfHistory& history,
                  std::span<const std::uint8_t> active, std::size_t day, std::size_t basis,
                  bool replay, Counts& counts) {
    const auto width = history.symbols.size(), offset = day * width, base = basis * width;
    auto& axis = cohort.selected; axis.clear(); axis.reserve(cohort.columns.size());
    for (auto n : cohort.columns) if (active[offset + n]) axis.push_back(n);
    set_axis(cohort, axis, history);
    if (axis.empty()) return;
    const auto size = axis.size();
    auto& values = cohort.values; values.resize(port.fields.size());
    auto& masks = cohort.masks; masks.resize(port.fields.size());
    auto& present = cohort.present; present.resize(size);
    auto& hq = cohort.hq; auto& bq = cohort.bq; auto& hs = cohort.hs; auto& bs = cohort.bs;
    if (port.rebased && day != basis) {
        hq.resize(size); bq.resize(size); hs.resize(size); bs.resize(size);
        for (std::size_t slot = 0; slot < size; ++slot) {
            const auto n = axis[slot];
            hq[slot] = history.q[offset + n]; bq[slot] = history.q[base + n];
            hs[slot] = history.segment[offset + n]; bs[slot] = history.segment[base + n];
        }
    }
    for (std::size_t slot = 0; slot < size; ++slot) present[slot] = history.present[offset + axis[slot]];
    auto& input = cohort.input; input.resize(port.fields.size());
    for (std::size_t field = 0; field < port.fields.size(); ++field) {
        const auto& source = history.fields[port.fields[field]];
        auto& column = values[field]; auto& mask = masks[field];
        column.resize(size); mask.resize(size);
        for (std::size_t slot = 0; slot < size; ++slot) column[slot] = source.values[offset + axis[slot]];
        if (day != basis && port.kinds[field])
            market::adjust_values_into(column, column, hq, bq, hs, bs, port.kinds[field] == 2);
        for (std::size_t slot = 0; slot < size; ++slot) {
            mask[slot] = std::isfinite(column[slot]);
            if (!mask[slot]) column[slot] = missing;
            if (!port.completed) present[slot] &= mask[slot];
        }
        if (input[field].field.empty()) input[field].field = source.name;
        input[field].values = column;
        input[field].valid = port.completed ? std::span<const std::uint8_t>(mask) : std::span<const std::uint8_t>{};
    }
    if (port.completed) cohort.graph->update_completed(history.times[day], input, present);
    else cohort.graph->update(history.times[day], input, present);
    ++counts.updates;
    (replay ? counts.replay : counts.advance) += size;
}
struct Task {
    std::size_t port, born, limit;
    bool inert;
    std::vector<std::size_t> columns, ends;
    std::size_t work = 0;
};
std::vector<Task> plan_tasks(const std::vector<Port>& ports, const AsOfHistory& history,
                             std::span<const std::uint8_t> active) {
    const auto width = history.symbols.size(), rows = history.requested.size();
    const auto absent = std::numeric_limits<std::size_t>::max();
    std::vector<Task> tasks;
    for (std::size_t port_index = 0; port_index < ports.size(); ++port_index) {
        const auto& port = ports[port_index];
        std::vector<std::size_t> owner(width, absent), slots(width);
        std::vector<double> prior_q(width, missing);
        std::vector<std::int64_t> prior_segment(width, -1);
        for (std::size_t row = 0; row < rows; ++row) {
            const auto basis = history.requested[row], offset = basis * width;
            for (std::size_t n = 0; n < width; ++n) {
                if (owner[n] == absent) continue;
                if (!active[offset + n] || (port.rebased && (!positive(history.q[offset + n]) ||
                    history.segment[offset + n] < 0 || history.q[offset + n] != prior_q[n] ||
                    history.segment[offset + n] != prior_segment[n]))) {
                    tasks[owner[n]].ends[slots[n]] = row;
                    owner[n] = absent;
                }
            }
            // A task owns the complete independent column cohort born at this
            // basis. Worker partitioning never fragments that shared Graph.
            for (bool inert : {false, true}) {
                Task task{port_index, row, row + 1, inert, {}, {}, 0};
                for (auto n : history.axes[basis]) {
                    if (owner[n] != absent) continue;
                    const bool no_basis = port.rebased && (!positive(history.q[offset + n]) || history.segment[offset + n] < 0);
                    if (inert != (no_basis && port.missing_prefix_inert)) continue;
                    owner[n] = tasks.size(); slots[n] = task.columns.size();
                    task.columns.push_back(n); task.ends.push_back(rows);
                }
                if (!task.columns.empty()) tasks.push_back(std::move(task));
            }
            for (auto n : history.axes[basis]) {
                prior_q[n] = history.q[offset + n]; prior_segment[n] = history.segment[offset + n];
            }
        }
    }
    for (auto& task : tasks) {
        const auto basis = history.requested[task.born];
        task.work = task.columns.size() * (task.inert ? 1 : basis + 1);
        for (auto end : task.ends) {
            task.limit = std::max(task.limit, end);
            task.work += history.requested[end - 1] - basis;
        }
    }
    // Tasks never share a (port, signal, security) publication cell. Their
    // Graph state stays on the worker that constructs and destroys it.
    std::stable_sort(tasks.begin(), tasks.end(), [](const auto& a, const auto& b) { return a.work > b.work; });
    return tasks;
}
void evaluate_task(const Task& task, const Port& port, const AsOfHistory& history,
                   std::span<const std::uint8_t> active, Publication& output, Counts& counts) {
    Cohort cohort(task.columns, port.spec);
    auto ends = task.ends;
    ++counts.cohorts;
    const auto width = history.symbols.size();
    std::size_t previous = history.requested[task.born];
    for (std::size_t row = task.born; row < task.limit; ++row) {
        const auto basis = history.requested[row];
        if (row == task.born) {
            for (std::size_t day = task.inert ? basis : 0; day <= basis; ++day)
                update_frame(cohort, port, history, active, day, basis, true, counts);
        } else {
            std::size_t size = 0;
            for (std::size_t slot = 0; slot < cohort.columns.size(); ++slot) if (ends[slot] > row) {
                cohort.columns[size] = cohort.columns[slot]; ends[size] = ends[slot]; ++size;
            }
            cohort.columns.resize(size); ends.resize(size);
            if (cohort.columns.empty()) break;
            set_axis(cohort, cohort.columns, history);
            for (auto day = previous + 1; day <= basis; ++day)
                update_frame(cohort, port, history, active, day, basis, false, counts);
        }
        const auto& state = cohort.graph->current_snapshot();
        for (std::size_t slot = 0; slot < cohort.axis.size(); ++slot) {
            const auto cell = row * width + cohort.axis[slot];
            const bool valid = state.valid[slot] && state.end_time[slot] == history.times[basis];
            output.current[cell] = valid ? state.current[slot] : missing;
            output.valid[cell] = valid; output.ready[cell] = state.ready[slot];
        }
        previous = basis;
    }
}
AsOfResult evaluate(const Spec* pool_spec, const graph::Spec* scalar_spec, const AsOfHistory& history) {
    const auto width = history.symbols.size(), rows = history.requested.size();
    if (width && rows > std::numeric_limits<std::size_t>::max() / width) throw std::invalid_argument("As-of publication is too large");
    const auto cells = rows * width;
    AsOfResult result{std::vector<double>(cells, missing), std::vector<std::uint8_t>(cells),
        std::vector<std::uint8_t>(cells), std::vector<std::uint8_t>(cells, 6)};
    if (!rows || !width) return result;
    std::vector<Port> ports;
    if (pool_spec) {
        if (!independent_temporals(*pool_spec)) throw std::invalid_argument("As-of plan has coupled temporal dependencies");
        for (const auto& port : pool_spec->temporals) ports.push_back(prepare_port(port.spec, history, true));
    } else ports.push_back(prepare_port(*scalar_spec, history, false));
    std::vector<std::uint8_t> active(history.rows * width, 0);
    for (std::size_t day = 0; day < history.axes.size(); ++day)
        for (auto n : history.axes[day]) active[day * width + n] = 1;
    std::vector<Publication> publications; publications.reserve(ports.size());
    for (std::size_t i = 0; i < ports.size(); ++i) publications.emplace_back(cells);
    const auto workers = std::min(width, fp::compute_threads());
    const auto tasks = plan_tasks(ports, history, active);
    std::vector<Counts> counts(tasks.size());
    fp::parallel_for(tasks.size(), 65536, [&](auto task) {
        evaluate_task(tasks[task], ports[tasks[task].port], history, active, publications[tasks[task].port], counts[task]);
    }, true);
    for (const auto& value : counts) {
        result.graph_updates += value.updates; result.replay_cells += value.replay;
        result.advance_cells += value.advance; result.cohorts += value.cohorts;
    }
    // After independent state is prepared, output-only CS/arithmetic can be
    // evaluated per signal in parallel on the original complete active axis.
    fp::parallel_for(workers, cells / workers, [&](auto worker) {
      std::unique_ptr<Program> program;
      if (pool_spec) program = std::make_unique<Program>(*pool_spec, std::vector<std::string>{}, true);
      std::vector<std::size_t> previous_axis;
      for (auto row = worker * rows / workers; row < (worker + 1) * rows / workers; ++row) {
        const auto basis = history.requested[row], offset = basis * width;
        const auto& axis = history.axes[basis];
        std::vector<std::uint8_t> observed(axis.size()), source_missing(axis.size(), 0);
        std::vector<std::vector<double>> columns(history.fields.size(), std::vector<double>(axis.size()));
        std::vector<graph::Column> input;
        for (std::size_t slot = 0; slot < axis.size(); ++slot) {
            observed[slot] = history.present[offset + axis[slot]];
            for (std::size_t field = 0; field < history.fields.size(); ++field) {
                const auto value = history.fields[field].values[offset + axis[slot]];
                columns[field][slot] = std::isfinite(value) ? value : missing;
                source_missing[slot] |= !std::isfinite(value);
            }
        }
        for (std::size_t field = 0; field < history.fields.size(); ++field) input.push_back({history.fields[field].name, columns[field]});
        std::vector<double> values(axis.size()); std::vector<std::uint8_t> valid(axis.size()), ready(axis.size());
        if (pool_spec) {
            if (axis != previous_axis) {
                std::vector<std::string> symbols; symbols.reserve(axis.size());
                for (auto n : axis) symbols.push_back(history.symbols[n]);
                program->set_symbols(std::move(symbols)); previous_axis = axis;
            }
            std::vector<std::vector<double>> port_values(ports.size(), std::vector<double>(axis.size()));
            std::vector<std::vector<std::uint8_t>> port_valid(ports.size(), std::vector<std::uint8_t>(axis.size())), port_ready(port_valid);
            std::vector<TemporalFrame> frames;
            for (std::size_t port = 0; port < ports.size(); ++port) {
                for (std::size_t slot = 0; slot < axis.size(); ++slot) {
                    const auto cell = row * width + axis[slot];
                    port_values[port][slot] = publications[port].current[cell];
                    port_valid[port][slot] = publications[port].valid[cell];
                    port_ready[port][slot] = publications[port].ready[cell];
                }
                frames.push_back({port_values[port], port_valid[port], port_ready[port]});
            }
            program->update_prepared(history.times[basis], input, observed, frames);
            values = program->current(); valid = program->valid(); ready = program->ready();
        } else {
            for (std::size_t slot = 0; slot < axis.size(); ++slot) {
                const auto cell = row * width + axis[slot];
                values[slot] = publications[0].current[cell];
                valid[slot] = publications[0].valid[cell]; ready[slot] = publications[0].ready[cell];
            }
        }
        for (std::size_t slot = 0; slot < axis.size(); ++slot) {
            const auto cell = row * width + axis[slot];
            const bool usable = valid[slot] && ready[slot] && std::isfinite(values[slot]) && observed[slot];
            result.current[cell] = usable ? values[slot] : missing;
            result.valid[cell] = usable; result.ready[cell] = ready[slot];
            result.reason[cell] = usable ? 0 : !observed[slot] ? 1 : source_missing[slot] ? 2 : valid[slot] && !ready[slot] ? 3 : 4;
        }
      }
    });
    return result;
}
} // namespace
AsOfResult evaluate_asof(const Spec& spec, const AsOfHistory& history) { return evaluate(&spec, nullptr, history); }
AsOfResult evaluate_asof(const graph::Spec& spec, const AsOfHistory& history) { return evaluate(nullptr, &spec, history); }
} // namespace factorpool::operators
