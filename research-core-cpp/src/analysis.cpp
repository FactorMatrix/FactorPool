#include "analysis.hpp"
#include "python_values.hpp"
#include "statistics.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <vector>
namespace {
using namespace fp;
} // namespace
PyObject *analyze(PyObject *, PyObject *args) {
  try {
    PyObject *factor, *opens, *eligible = nullptr;
    int groups, minimum, ddof, horizon = 1;
    if (!PyArg_ParseTuple(args, "OOiii|Oi", &factor, &opens, &groups, &minimum,
                          &ddof, &eligible, &horizon))
      return nullptr;
    if (horizon < 1 || horizon > 252 || groups < 1 || groups > 100 ||
        minimum < 2 || ddof < 0 || ddof > 1)
      throw std::invalid_argument("Invalid analysis options");
    auto f = matrix(factor), p = matrix(opens);
    auto membership = eligible ? matrix(eligible) : Matrix{};
    if (eligible && (membership.size() != f.size() ||
                     (!f.empty() && membership[0].size() != f[0].size())))
      throw std::invalid_argument("Membership axes differ");
    if (f.size() > p.size() || (!f.empty() && f[0].size() != p[0].size()))
      throw std::invalid_argument("Factor/price axes differ");
    Owned result{PyDict_New()}, days{PyList_New(0)}, returns{PyList_New(0)},
        assignments{PyList_New(0)};
    std::vector<double> ics, rankics, cumulative(groups, 1.0);
    std::vector<bool> started(groups, false), broken(groups, false);
    std::size_t covered = 0, total = 0, pairs = 0;
    for (std::size_t t = 0; t < f.size(); ++t) {
      std::vector<double> r(f[t].size(), missing), a, b,
          labels(f[t].size(), missing);
      std::vector<std::size_t> order;
      for (std::size_t n = 0; n < f[t].size(); ++n) {
        if (!eligible || membership[t][n] > 0)
          ++total;
        if (t + 1 + horizon < p.size() && std::isfinite(p[t + 1][n]) &&
            std::isfinite(p[t + 1 + horizon][n]) && p[t + 1][n] > 0 &&
            p[t + 1 + horizon][n] > 0)
          r[n] = p[t + 1 + horizon][n] / p[t + 1][n] - 1;
        if (std::isfinite(f[t][n]) && (!eligible || membership[t][n] > 0)) {
          ++covered;
          order.push_back(n);
          if (std::isfinite(r[n])) {
            a.push_back(f[t][n]);
            b.push_back(r[n]);
            ++pairs;
          }
        }
      }
      const double ic = correlation(a, b, minimum),
                   ric = correlation(rank_values(a), rank_values(b), minimum);
      if (std::isfinite(ic))
        ics.push_back(ic);
      if (std::isfinite(ric))
        rankics.push_back(ric);
      std::stable_sort(order.begin(), order.end(),
                       [&](auto x, auto y) { return f[t][x] < f[t][y]; });
      std::vector<double> sums(groups, 0), counts(groups, 0),
          members(groups, 0), means(groups, missing), nav(groups, missing);
      for (std::size_t k = 0; k < order.size(); ++k) {
        const auto g = k * groups / order.size(), n = order[k];
        labels[n] = g + 1;
        ++members[g];
        if (std::isfinite(r[n])) {
          sums[g] += r[n];
          ++counts[g];
        }
      }
      for (int g = 0; g < groups; ++g) {
        if (counts[g] > 0) {
          means[g] = sums[g] / counts[g];
          if (!broken[g] && horizon == 1) {
            started[g] = true;
            cumulative[g] *= 1 + means[g];
            nav[g] = cumulative[g] - 1;
          }
        } else if (started[g])
          broken[g] = true;
      }
      Owned day{PyDict_New()};
      put(day.p, "ic", number(ic));
      put(day.p, "rank_ic", number(ric));
      put(day.p, "pairs", PyLong_FromSize_t(a.size()));
      put(day.p, "reason",
          PyUnicode_FromString(a.size() < static_cast<std::size_t>(minimum)
                                   ? "insufficient_pairs"
                               : !std::isfinite(ic) ? "constant_cross_section"
                                                    : "ok"));
      put(day.p, "group_returns", list(means));
      put(day.p, "group_counts", list(counts));
      put(day.p, "group_members", list(members));
      put(day.p, "cumulative", list(nav));
      put(day.p, "high_low",
          number(std::isfinite(means.front()) && std::isfinite(means.back())
                     ? means.back() - means.front()
                     : missing));
      PyList_Append(days.p, day.p);
      Owned rr{list(r)}, gg{list(labels)};
      PyList_Append(returns.p, rr.p);
      PyList_Append(assignments.p, gg.p);
    }
    auto average = [](const std::vector<double> &v) {
      long double s = 0;
      for (auto x : v)
        s += x;
      return v.empty() ? missing : static_cast<double>(s / v.size());
    };
    const double mean = average(ics);
    long double m2 = 0;
    for (auto x : ics)
      m2 += (x - mean) * (x - mean);
    double sd = ics.size() > static_cast<std::size_t>(ddof)
                    ? std::sqrt(static_cast<double>(m2 / (ics.size() - ddof)))
                    : missing;
    Owned summary{PyDict_New()};
    put(summary.p, "ic_mean", number(mean));
    put(summary.p, "rank_ic_mean", number(average(rankics)));
    put(summary.p, "icir", number(sd > 0 ? mean / sd : missing));
    put(summary.p, "valid_dates", PyLong_FromSize_t(ics.size()));
    put(summary.p, "coverage",
        number(total ? double(covered) / total : missing));
    put(summary.p, "paired_samples", PyLong_FromSize_t(pairs));
    put(result.p, "summary", Py_NewRef(summary.p));
    put(result.p, "daily", Py_NewRef(days.p));
    put(result.p, "forward_returns", Py_NewRef(returns.p));
    put(result.p, "groups", Py_NewRef(assignments.p));
    Py_INCREF(result.p);
    return result.p;
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
