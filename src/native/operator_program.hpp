#pragma once
#include "value.hpp"
#include "calendar_window.hpp"
#include "brain_temporal.hpp"
#include "brain_cross.hpp"
#include <map>
#include "factortrader/mods/sys_indicator/graph/graph.hpp"
#include <memory>
#include <string>
#include <unordered_map>

namespace factorpool::operators {
namespace shared = factortrader::mod::indicator::batch_program;
namespace graph = factortrader::mod::indicator::graph;
struct Node {
    std::string op, field;
    std::vector<std::size_t> inputs;
    double value = 0;
    std::size_t dependency = 0;
    std::map<std::string, double> numbers;
    std::map<std::string, std::string> strings;
};
struct Spec {
    std::vector<Node> nodes;
    std::size_t output = 0, ready = 0;
    std::vector<graph::TemporalSpec> temporals;
};
// Read-only temporal publications supplied by a Pool-owned cohort evaluator.
// valid already includes the Graph publication's current-time/as-of mask;
// ready preserves the original numerical window flag even when valid is false.
struct TemporalFrame {
    std::span<const double> current;
    std::span<const std::uint8_t> valid, ready;
};
bool independent_temporals(const Spec&);
// Only extension operations live in Pool. Ordinary arithmetic/cross sections
// and every stateful temporal calculation call the pinned shared kernels.
bool is_extension(const std::string& op);
shared::Value extension(const std::string& op, const std::vector<const shared::Value*>& args);
class Program {
    Spec spec_;
    std::vector<std::string> symbols_;
    std::vector<std::unique_ptr<graph::Graph>> ports_;
    std::unordered_map<std::size_t, std::unique_ptr<CalendarState>> calendar_;
    std::unordered_map<std::size_t, std::unique_ptr<BrainTemporal>> brain_ts_;
    std::unordered_map<std::size_t, std::unique_ptr<BrainCross>> brain_cs_;
    std::vector<double> current_;
    std::vector<std::uint8_t> valid_, ready_;
    std::int64_t time_ = 0;
    bool prepared_only_ = false;
    void update_impl(std::int64_t, std::span<const graph::Column>,
                     std::span<const std::uint8_t>, const std::span<const TemporalFrame>*);
public:
    Program(Spec, std::vector<std::string>, bool prepared_only = false);
    void set_symbols(std::vector<std::string>);
    void update(std::int64_t, std::span<const graph::Column>, std::span<const std::uint8_t>);
    void update_prepared(std::int64_t, std::span<const graph::Column>,
                         std::span<const std::uint8_t>, std::span<const TemporalFrame>);
    const Spec& spec() const { return spec_; }
    bool independent_frames() const;
    const auto& symbols() const { return symbols_; }
    const auto& current() const { return current_; }
    const auto& valid() const { return valid_; }
    const auto& ready() const { return ready_; }
    std::int64_t time() const { return time_; }
};
}
