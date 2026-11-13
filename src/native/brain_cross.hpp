#pragma once

#include "calendar_window.hpp"
#include <cstdint>
#include <deque>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace factorpool::operators {
struct BrainCrossSpec {
  std::string op;
  std::map<std::string, double> numbers;
  std::map<std::string, std::string> strings;
};

// One native node over the complete, sorted security axis. Numeric vector
// arguments are columns: vec_* aggregates components within each security.
class BrainCross {
public:
  explicit BrainCross(BrainCrossSpec spec);
  void set_axis(const std::vector<std::string> &symbols);
  CalendarResult update(std::int64_t time,
                        const std::vector<std::vector<double>> &args,
                        const std::vector<std::vector<std::uint8_t>> &source_ready,
                        const std::vector<std::uint8_t> &present);
  static bool supports(const std::string &op);
private:
  struct Observation { double value, group; bool ready; };
  BrainCrossSpec spec_;
  std::vector<std::string> symbols_;
  std::vector<double> held_;
  std::vector<std::deque<Observation>> history_;
  std::optional<std::int64_t> last_time_;
  std::vector<double> cutoffs_;
  std::size_t period_ = 1;
  bool skip_begin_ = false, skip_end_ = false, nan_group_ = false, range_mode_ = false;
  double number(const std::string &key, double fallback) const;
};
} // namespace factorpool::operators
