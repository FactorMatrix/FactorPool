#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <string>
#include <vector>

namespace factorpool::operators {

enum class CalendarOp { Delay, Delta, Mean, Sum, Min, Max, Std, DecayLinear };

struct CalendarResult {
  std::vector<double> values;
  std::vector<std::uint8_t> valid, ready;
};

// One state per calendar-clock DAG node, owned by one task. Every completed
// frame advances one slot for every current symbol, including known missing
// inputs. Upstream warmup is retained separately from a completed NaN input.
class CalendarState {
public:
  CalendarState(CalendarOp op, std::size_t period, int ddof = 0);
  void set_axis(const std::vector<std::string> &symbols);
  CalendarResult update(std::int64_t time, const std::vector<double> &source,
                        const std::vector<std::uint8_t> &source_ready,
                        const std::vector<std::uint8_t> &present);

private:
  struct Slot {
    double value;
    bool ready;
  };
  CalendarOp op_;
  std::size_t period_, capacity_;
  int ddof_;
  std::vector<std::string> symbols_;
  std::vector<std::deque<Slot>> history_;
  std::optional<std::int64_t> last_time_;
};

} // namespace factorpool::operators
