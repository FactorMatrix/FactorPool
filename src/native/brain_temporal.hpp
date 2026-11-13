#pragma once
#include "calendar_window.hpp"
#include <deque>
#include <optional>
#include <string>
#include <vector>

namespace factorpool::operators {
// Options are supplied by the typed compiler, never evaluated from user Python.
struct BrainTemporalSpec {
    std::string op;
    std::size_t period = 1, k = 1, lag = 0;
    int rettype = 0, ddof = 0;
    double constant = 0, hump = .01;
    bool dense = false, valid_observations = false;
    std::string driver = "gaussian", ignore = "NaN";
};
// Uniform maps p to p. Other endpoints are undefined, rather than finite caps.
double brain_distribution_quantile(double p, const std::string& driver);
bool is_brain_temporal(const std::string& op);

// A single DAG node, retaining bounded per-symbol histories. Dynamic removal
// drops that symbol's state; a later re-entry starts a fresh observation series.
class BrainTemporal {
public:
    explicit BrainTemporal(BrainTemporalSpec spec);
    void set_axis(const std::vector<std::string>& symbols);
    CalendarResult update(std::int64_t time,
        const std::vector<std::vector<double>>& arguments,
        const std::vector<std::vector<std::uint8_t>>& source_ready,
        const std::vector<std::uint8_t>& present);
private:
    struct Slot { double x, y; bool x_ready, y_ready; };
    struct State {
        std::deque<Slot> slots;
        std::optional<double> previous;
        std::size_t unchanged = 0;
    };
    BrainTemporalSpec spec_;
    std::size_t capacity_ = 0, frames_ = 0;
    std::vector<std::string> symbols_;
    std::vector<State> state_;
    std::optional<std::int64_t> last_time_;
    bool ignore_nan_ = false;
    std::vector<double> ignored_;
};
} // namespace factorpool::operators
