#include "analysis.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <vector>
namespace {
constexpr double missing = std::numeric_limits<double>::quiet_NaN();
struct Owned {
  PyObject *p;
  ~Owned() { Py_XDECREF(p); }
};
using Matrix = std::vector<std::vector<double>>;
Matrix matrix(PyObject *object) {
  Owned rows{PySequence_Fast(object, "Expected matrix")};
  if (!rows.p)
    throw std::invalid_argument("Expected matrix");
  Matrix result;
  for (Py_ssize_t i = 0; i < PySequence_Fast_GET_SIZE(rows.p); ++i) {
    Owned cells{
        PySequence_Fast(PySequence_Fast_GET_ITEM(rows.p, i), "Expected row")};
    if (!cells.p)
      throw std::invalid_argument("Expected row");
    std::vector<double> row;
    for (Py_ssize_t j = 0; j < PySequence_Fast_GET_SIZE(cells.p); ++j) {
      auto *cell = PySequence_Fast_GET_ITEM(cells.p, j);
      double x = cell == Py_None ? missing : PyFloat_AsDouble(cell);
      if (PyErr_Occurred())
        throw std::invalid_argument("Expected numbers or null");
      row.push_back(x);
    }
    if (!result.empty() && row.size() != result[0].size())
      throw std::invalid_argument("Ragged matrix");
    result.push_back(std::move(row));
  }
  return result;
}
PyObject *number(double x) {
  if (std::isfinite(x))
    return PyFloat_FromDouble(x);
  Py_RETURN_NONE;
}
void put(PyObject *obj, const char *key, PyObject *value) {
  Owned v{value};
  if (!value || PyDict_SetItemString(obj, key, value) < 0)
    throw std::runtime_error("Allocation failure");
}
std::vector<double> ranks(const std::vector<double> &values) {
  std::vector<std::size_t> indices(values.size());
  std::iota(indices.begin(), indices.end(), 0);
  std::stable_sort(indices.begin(), indices.end(),
                   [&](auto a, auto b) { return values[a] < values[b]; });
  std::vector<double> r(values.size());
  for (std::size_t i = 0; i < indices.size();) {
    auto j = i + 1;
    while (j < indices.size() && values[indices[i]] == values[indices[j]])
      ++j;
    for (auto k = i; k < j; ++k)
      r[indices[k]] = (i + j - 1) / 2.0 + 1;
    i = j;
  }
  return r;
}
double corr(const std::vector<double> &a, const std::vector<double> &b,
            int minimum) {
  if (a.size() < static_cast<std::size_t>(minimum))
    return missing;
  long double ma = 0, mb = 0, aa = 0, bb = 0, ab = 0;
  for (std::size_t i = 0; i < a.size(); ++i) {
    long double da = a[i] - ma, db = b[i] - mb;
    ma += da / (i + 1);
    mb += db / (i + 1);
    aa += da * (a[i] - ma);
    bb += db * (b[i] - mb);
    ab += da * (b[i] - mb);
  }
  if (aa <= 0 || bb <= 0)
    return missing;
  return std::clamp(static_cast<double>(ab / std::sqrt(aa * bb)), -1.0, 1.0);
}
PyObject *list(const std::vector<double> &values) {
  auto *out = PyList_New(values.size());
  if (!out)
    throw std::bad_alloc();
  for (std::size_t i = 0; i < values.size(); ++i)
    PyList_SET_ITEM(out, i, number(values[i]));
  return out;
}
} // namespace
PyObject *analyze(PyObject *, PyObject *args) {
  try {
    PyObject *factor, *opens, *eligible = nullptr;
    int groups, minimum, ddof;
    if (!PyArg_ParseTuple(args, "OOiii|O", &factor, &opens, &groups, &minimum,
                          &ddof, &eligible))
      return nullptr;
    if (groups < 1 || groups > 100 || minimum < 2 || ddof < 0 || ddof > 1)
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
        if (t + 2 < p.size() && std::isfinite(p[t + 1][n]) &&
            std::isfinite(p[t + 2][n]) && p[t + 1][n] > 0 && p[t + 2][n] > 0)
          r[n] = p[t + 2][n] / p[t + 1][n] - 1;
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
      const double ic = corr(a, b, minimum),
                   ric = corr(ranks(a), ranks(b), minimum);
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
          if (!broken[g]) {
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
