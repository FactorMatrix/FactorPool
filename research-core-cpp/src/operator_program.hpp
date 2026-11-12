#pragma once
#include "value.hpp"
#include "calendar_window.hpp"
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
};
struct Spec {
    std::vector<Node> nodes;
    std::size_t output = 0, ready = 0;
    std::vector<graph::TemporalSpec> temporals;
};
// Only extension operations live in Pool. Ordinary arithmetic/cross sections
// and every stateful temporal calculation call the pinned shared kernels.
bool is_extension(const std::string& op);
shared::Value extension(const std::string& op, const std::vector<const shared::Value*>& args);
class Program {
    Spec spec_;
    std::vector<std::string> symbols_;
    std::vector<std::unique_ptr<graph::Graph>> ports_;
    std::unordered_map<std::size_t, std::unique_ptr<CalendarState>> calendar_;
    std::vector<double> current_;
    std::vector<std::uint8_t> valid_, ready_;
    std::int64_t time_ = 0;
public:
    Program(Spec, std::vector<std::string>);
    void set_symbols(std::vector<std::string>);
    void update(std::int64_t, std::span<const graph::Column>, std::span<const std::uint8_t>);
    const auto& symbols() const { return symbols_; }
    const auto& current() const { return current_; }
    const auto& valid() const { return valid_; }
    const auto& ready() const { return ready_; }
    std::int64_t time() const { return time_; }
};
}
