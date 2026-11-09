#include "analysis.hpp"
#include "statistics.hpp"
#include <algorithm>
#include <numeric>
#include <string>
namespace {
using namespace fp;
struct Strategy {
  std::string name;
  std::vector<double> previous, entry, pnl, turnover, positions, concentration;
  bool seen = false;
  Strategy(std::string value) : name(std::move(value)) {}
};
} // namespace
PyObject *portfolio_report(PyObject *, PyObject *args) {
  try {
    PyObject *factor, *opens, *members;
    int groups, h, annual_days;
    if (!PyArg_ParseTuple(args, "OOOiii", &factor, &opens, &members, &groups,
                          &h, &annual_days))
      return nullptr;
    if (groups < 1 || groups > 100 || h < 1 || h > 252 || annual_days < 1 ||
        annual_days > 366)
      throw std::invalid_argument("Invalid portfolio options");
    auto f = matrix(factor), p = matrix(opens), m = matrix(members);
    if (f.size() > p.size() || f.size() != m.size() ||
        (!f.empty() &&
         (f[0].size() != p[0].size() || f[0].size() != m[0].size())))
      throw std::invalid_argument("Portfolio axes differ");
    const std::size_t count = f.empty() ? 0 : f[0].size();
    std::vector<Strategy> strategies;
    for (int g = 0; g < groups; ++g)
      strategies.push_back({"long_G" + std::to_string(g + 1)});
    for (int g = 0; g < groups; ++g)
      strategies.push_back({"short_G" + std::to_string(g + 1)});
    strategies.push_back({"high_low"});
    if (groups >= 4)
      strategies.push_back({"inner_high_low"});
    strategies.push_back({"alpha"});
    for (auto &s : strategies) {
      s.previous.assign(count, 0);
      s.entry.assign(count, missing);
    }
    std::vector<double> indices;
    for (std::size_t t = 0; t < f.size(); t += h) {
      indices.push_back(double(t));
      std::vector<std::size_t> order;
      for (std::size_t n = 0; n < count; ++n)
        if (std::isfinite(f[t][n]) && m[t][n] > 0)
          order.push_back(n);
      std::stable_sort(order.begin(), order.end(),
                       [&](auto a, auto b) { return f[t][a] < f[t][b]; });
      std::vector<std::size_t> labels(count, groups), sizes(groups, 0);
      long double mean = 0;
      for (std::size_t k = 0; k < order.size(); ++k) {
        auto n = order[k], g = k * groups / order.size();
        labels[n] = g;
        ++sizes[g];
        mean += (f[t][n] - mean) / (k + 1);
      }
      std::vector<double> alpha(count, 0);
      long double norm = 0;
      for (auto n : order)
        norm += std::abs(static_cast<long double>(f[t][n]) - mean);
      for (auto &s : strategies) {
        std::vector<double> w(count, 0);
        bool usable = true;
        if (s.name == "alpha") {
          if (norm > 0)
            for (auto n : order)
              w[n] = double((static_cast<long double>(f[t][n]) - mean) / norm);
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
                w[n] = -.5 / sizes[lo];
              if (labels[n] == std::size_t(hi))
                w[n] = .5 / sizes[hi];
            }
        } else {
          const bool short_side = s.name.starts_with("short_");
          const int g = std::stoi(s.name.substr(s.name.find('G') + 1)) - 1;
          if (!sizes[g])
            usable = false;
          else
            for (auto n : order)
              if (labels[n] == std::size_t(g))
                w[n] = (short_side ? -1. : 1.) / sizes[g];
        }
        long double pnl = 0, traded = 0;
        std::size_t positions = 0;
        double concentration = 0;
        bool return_valid = usable && t + 1 + h < p.size(),
             trade_valid = usable && t + 1 < p.size();
        for (std::size_t n = 0; n < count; ++n) {
          if (w[n] != 0) {
            ++positions;
            concentration = std::max(concentration, std::abs(w[n]));
            if (return_valid) {
              const double entry = p[t + 1][n], exit = p[t + 1 + h][n];
              if (!std::isfinite(entry) || !std::isfinite(exit) || entry <= 0 ||
                  exit <= 0)
                return_valid = false;
              else
                pnl += w[n] * (exit / entry - 1);
            }
          }
          if (trade_valid) {
            double old = 0;
            if (s.seen && s.previous[n] != 0) {
              const auto current = p[t + 1][n], old_entry = s.entry[n];
              if (!std::isfinite(current) || !std::isfinite(old_entry) ||
                  current <= 0 || old_entry <= 0)
                trade_valid = false;
              else
                old = s.previous[n] * current / old_entry;
            }
            if (w[n] != 0 && (!std::isfinite(p[t + 1][n]) || p[t + 1][n] <= 0))
              trade_valid = false;
            traded += std::abs(static_cast<long double>(w[n]) - old);
          }
        }
        s.pnl.push_back(return_valid ? double(pnl) : missing);
        s.turnover.push_back(trade_valid ? double(traded) : missing);
        s.positions.push_back(usable ? double(positions) : missing);
        s.concentration.push_back(usable ? concentration : missing);
        s.previous = std::move(w);
        s.entry =
            t + 1 < p.size() ? p[t + 1] : std::vector<double>(count, missing);
        s.seen = true;
      }
    }
    Owned result{PyDict_New()}, out{PyDict_New()};
    for (auto &s : strategies) {
      Owned v{performance(s.pnl, s.turnover, h, annual_days)};
      put(v.p, "returns", list(s.pnl));
      put(v.p, "turnover_series", list(s.turnover));
      put(v.p, "positions", list(s.positions));
      put(v.p, "max_weight_series", list(s.concentration));
      put(out.p, s.name.c_str(), Py_NewRef(v.p));
    }
    put(result.p, "signal_indices", list(indices));
    put(result.p, "strategies", Py_NewRef(out.p));
    return Py_NewRef(result.p);
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
