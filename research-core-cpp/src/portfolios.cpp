#include "analysis.hpp"
#include "statistics.hpp"
#include "research_input.hpp"
#include <algorithm>
#include <string>
namespace {
using namespace fp;
struct Holding {
  std::size_t symbol;
  double weight, growth = 1;
};
struct Strategy {
  std::string name;
  std::vector<Holding> previous;
  std::vector<double> pnl, book_pnl, turnover, positions, concentration;
  std::vector<double> period_returns, period_turnover;
  bool seen = false, previous_valid = false;
  explicit Strategy(std::string value) : name(std::move(value)) {}
};
} // namespace
PyObject *portfolio_report(PyObject *, PyObject *args) {
  try {
    PyObject *factor, *returns, *members;
    int groups, h, annual_days;
    if (!PyArg_ParseTuple(args, "OOOiii", &factor, &returns, &members, &groups,
                          &h, &annual_days))
      return nullptr;
    if (groups < 1 || groups > 100 || h < 1 || h > 252 || annual_days < 1 ||
        annual_days > 366)
      throw std::invalid_argument("Invalid portfolio options");
    InputRef holder(factor, returns, members);
    const auto &input = *holder.value;
    const auto &f = input.factors, &r = input.returns;
    const std::size_t count = f.empty() ? 0 : f[0].size();
    std::vector<Strategy> strategies;
    for (int g = 0; g < groups; ++g)
      strategies.emplace_back("long_G" + std::to_string(g + 1));
    for (int g = 0; g < groups; ++g)
      strategies.emplace_back("short_G" + std::to_string(g + 1));
    strategies.emplace_back("high_low");
    if (groups >= 4)
      strategies.emplace_back("inner_high_low");
    strategies.emplace_back("alpha");
    std::vector<double> indices, daily_indices;
    for (std::size_t t = 0; t < f.size(); t += h) {
      indices.push_back(double(t));
      const auto end = std::min(t + h + 1, r.size());
      for (auto d = t + 1; d < end; ++d)
        daily_indices.push_back(double(d));
      const auto &order = input.order[t];
      std::vector<std::size_t> labels(count, groups), sizes(groups, 0);
      long double mean = 0;
      for (std::size_t k = 0; k < order.size(); ++k) {
        auto n = order[k], g = k * groups / order.size();
        labels[n] = g;
        ++sizes[g];
        mean += (static_cast<long double>(f[t][n]) - mean) / (k + 1);
      }
      long double norm = 0;
      for (auto n : order)
        norm += std::abs(static_cast<long double>(f[t][n]) - mean);
      for (auto &s : strategies) {
        std::vector<double> weights(count, 0);
        bool usable = true;
        if (s.name == "alpha") {
          if (norm > 0)
            for (auto n : order)
              weights[n] = double((static_cast<long double>(f[t][n]) - mean) / norm);
          else
            usable = false;
        } else if (s.name == "high_low" || s.name == "inner_high_low") {
          int lo = s.name == "high_low" ? 0 : 1,
              hi = s.name == "high_low" ? groups - 1 : groups - 2;
          if (lo >= hi || !sizes[lo] || !sizes[hi])
            usable = false;
          else
            for (auto n : order) {
              if (labels[n] == std::size_t(lo))
                weights[n] = -.5 / sizes[lo];
              if (labels[n] == std::size_t(hi))
                weights[n] = .5 / sizes[hi];
            }
        } else {
          const bool short_side = s.name.starts_with("short_");
          const int g = std::stoi(s.name.substr(s.name.find('G') + 1)) - 1;
          if (!sizes[g])
            usable = false;
          else
            for (auto n : order)
              if (labels[n] == std::size_t(g))
                weights[n] = (short_side ? -1. : 1.) / sizes[g];
        }
        std::vector<Holding> held;
        double concentration = 0;
        for (auto n : order)
          if (weights[n] != 0) {
            held.push_back({n, weights[n]});
            concentration = std::max(concentration, std::abs(weights[n]));
          }
        // Both books are expressed as fractions of the same unit gross book.
        // The prior weights drift with returns; no shares, lots or cash engine.
        bool trade_valid = usable && (!s.seen || s.previous_valid);
        for (const auto &old : s.previous)
          weights[old.symbol] -= old.weight * old.growth;
        long double traded = 0;
        if (trade_valid)
          for (auto w : weights)
            traded += std::abs(static_cast<long double>(w));
        const double turnover = trade_valid ? double(traded) : missing;
        s.period_turnover.push_back(turnover);
        double previous_wealth = 1, wealth = missing;
        bool valid = usable;
        for (auto d = t + 1; d < end; ++d) {
          long double delta = 0;
          for (auto &position : held) {
            const auto value = r[d][position.symbol];
            if (!std::isfinite(value) || value < -1)
              position.growth = missing;
            else
              position.growth *= 1 + value;
            if (!std::isfinite(position.growth))
              valid = false;
            delta += position.weight * (static_cast<long double>(position.growth) - 1);
          }
          wealth = valid ? double(1 + delta) : missing;
          if (!std::isfinite(wealth))
            valid = false;
          s.pnl.push_back(valid && previous_wealth > 0 && wealth >= 0
                              ? wealth / previous_wealth - 1 : missing);
          s.book_pnl.push_back(valid && std::isfinite(previous_wealth)
                                  ? wealth - previous_wealth : missing);
          s.turnover.push_back(d == t + 1 ? turnover : 0);
          s.positions.push_back(usable ? double(held.size()) : missing);
          s.concentration.push_back(usable ? concentration : missing);
          previous_wealth = wealth;
        }
        s.period_returns.push_back(valid && t + h < r.size() ? wealth - 1 : missing);
        s.previous = std::move(held);
        s.previous_valid = valid && t + h < r.size();
        s.seen = true;
      }
    }
    Owned result{PyDict_New()}, out{PyDict_New()};
    for (auto &s : strategies) {
      Owned v{performance(s.pnl, s.turnover, 1, annual_days, &s.book_pnl)};
      put(v.p, "returns", list(s.pnl));
      put(v.p, "book_pnl_series", list(s.book_pnl));
      put(v.p, "turnover_series", list(s.turnover));
      put(v.p, "positions", list(s.positions));
      put(v.p, "max_weight_series", list(s.concentration));
      put(v.p, "period_returns", list(s.period_returns));
      put(v.p, "period_turnover_series", list(s.period_turnover));
      put(out.p, s.name.c_str(), Py_NewRef(v.p));
    }
    put(result.p, "signal_indices", list(indices));
    put(result.p, "daily_indices", list(daily_indices));
    put(result.p, "strategies", Py_NewRef(out.p));
    return Py_NewRef(result.p);
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
