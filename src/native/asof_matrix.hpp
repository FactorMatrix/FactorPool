#pragma once
#include "operator_program.hpp"
#include <span>

namespace factorpool::operators {
struct AsOfField {
    std::string name;
    std::span<const double> values;
};
struct AsOfHistory {
    std::span<const std::string> symbols;
    std::size_t rows;
    std::span<const AsOfField> fields;
    std::span<const std::uint8_t> present;
    std::span<const double> q;
    std::span<const std::int64_t> segment;
    std::span<const std::size_t> requested;
    std::span<const std::vector<std::size_t>> axes;
    std::span<const std::int64_t> times;
};
struct AsOfResult {
    std::vector<double> current;
    std::vector<std::uint8_t> valid, ready, reason;
    std::size_t graph_updates = 0, replay_cells = 0, advance_cells = 0, cohorts = 0;
};
// Both entries call the original pinned Graph's streaming arithmetic. Pool's
// independent temporal ports are assembled before its unchanged CS suffix.
AsOfResult evaluate_asof(const Spec&, const AsOfHistory&);
AsOfResult evaluate_asof(const graph::Spec&, const AsOfHistory&);
} // namespace factorpool::operators
