#include "analysis.hpp"
#include "python_values.hpp"
#include "statistics.hpp"
#include "research_input.hpp"
#include "parallel.hpp"
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
    PyObject *factor, *daily_returns, *eligible = nullptr;
    int groups, minimum, ddof, horizon = 1, signal_stride = 1, array_output = 0;
    if (!PyArg_ParseTuple(args, "OOiii|Oiip", &factor, &daily_returns, &groups, &minimum,
                          &ddof, &eligible, &horizon, &signal_stride, &array_output))
      return nullptr;
    if (horizon < 1 || horizon > 252 || groups < 1 || groups > 100 ||
        minimum < 2 || ddof < 0 || ddof > 1 || signal_stride < 1)
      throw std::invalid_argument("Invalid analysis options");
    InputRef holder(factor, daily_returns, eligible);
    const auto &input = *holder.value;
    const auto &f = input.factors, &forward = input.forward(horizon);
    input.prepare_correlations(horizon, minimum);
    const auto output_rows = f.size() ? 1 + (f.size() - 1) / signal_stride : 0;
    const auto columns = f.columns();
    if (output_rows && columns > static_cast<std::size_t>(PY_SSIZE_T_MAX) / sizeof(double) / output_rows)
      throw std::invalid_argument("Research publication is too large");
    const auto output_bytes = static_cast<Py_ssize_t>(output_rows * columns * sizeof(double));
    Owned result{PyDict_New()}, days{PyList_New(0)},
        returns{array_output ? PyBytes_FromStringAndSize(nullptr, output_bytes) : PyList_New(0)},
        assignments{array_output ? PyBytes_FromStringAndSize(nullptr, output_bytes) : PyList_New(0)};
    if (!result.p || !days.p || !returns.p || !assignments.p) throw std::bad_alloc();
    std::vector<double> ics, rankics, cumulative(groups, 1.0);
    std::vector<bool> started(groups, false), broken(groups, false);
    std::size_t covered = 0, total = 0, pairs = 0;
    struct DayValues {
      std::vector<double> returns, labels, means, counts, members;
      std::size_t covered = 0, total = 0, pairs = 0;
      double ic = missing, ric = missing;
    };
    // Allocate Python storage on the caller, then give workers disjoint owned
    // byte ranges. No Python API or mutable input cache allocation runs there.
    auto *return_data = array_output ? PyBytes_AS_STRING(returns.p) : nullptr;
    auto *group_data = array_output ? PyBytes_AS_STRING(assignments.p) : nullptr;
    constexpr std::size_t block_rows = 256;
    for (std::size_t first = 0; first < output_rows; first += block_rows) {
      const auto length = std::min(block_rows, output_rows - first);
      std::vector<DayValues> values(length);
      parallel_for(length, columns, [&](std::size_t row) {
        const auto ordinal = first + row, t = ordinal * signal_stride;
        auto &value = values[row];
        std::vector<double> r(f[t].size(), missing),
            labels(f[t].size(), missing);
        const auto &order = input.order[t];
        for (std::size_t n = 0; n < f[t].size(); ++n) {
          if (input.eligible(t, n))
            ++value.total;
          r[n] = forward[t][n];
          if (std::isfinite(f[t][n]) && input.eligible(t, n)) {
            ++value.covered;
          }
        }
        const auto &metrics = input.correlation_row(horizon, minimum, t);
        value.ic = metrics.ic;
        value.ric = metrics.rank_ic;
        value.pairs = metrics.pairs;
        std::vector<double> sums(groups, 0);
        value.counts.assign(groups, 0);
        value.members.assign(groups, 0);
        value.means.assign(groups, missing);
        for (std::size_t k = 0; k < order.size(); ++k) {
          const auto g = k * groups / order.size(), n = order[k];
          labels[n] = g + 1;
          ++value.members[g];
          if (std::isfinite(r[n])) {
            sums[g] += r[n];
            ++value.counts[g];
          }
        }
        for (int g = 0; g < groups; ++g)
          if (value.counts[g] > 0)
            value.means[g] = sums[g] / value.counts[g];
        if (array_output) {
          const auto offset = ordinal * columns * sizeof(double);
          if (columns) {
            std::memcpy(return_data + offset, r.data(), columns * sizeof(double));
            std::memcpy(group_data + offset, labels.data(), columns * sizeof(double));
          }
        } else {
          value.returns = std::move(r);
          value.labels = std::move(labels);
        }
      });
      // NAV, sample vectors and summaries retain the original chronological
      // reduction order. Python row/list publication stays on this thread.
      for (const auto &value : values) {
        const auto ic = value.ic, ric = value.ric;
        const auto &means = value.means, &counts = value.counts,
                   &members = value.members;
        covered += value.covered;
        total += value.total;
        pairs += value.pairs;
        if (std::isfinite(ic)) ics.push_back(ic);
        if (std::isfinite(ric)) rankics.push_back(ric);
        std::vector<double> nav(groups, missing);
        for (int g = 0; g < groups; ++g) {
          if (counts[g] > 0) {
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
        put(day.p, "pairs", PyLong_FromSize_t(value.pairs));
        put(day.p, "reason",
            PyUnicode_FromString(value.pairs < static_cast<std::size_t>(minimum)
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
        if (!array_output) {
          Owned rr{list(value.returns)}, gg{list(value.labels)};
          PyList_Append(returns.p, rr.p);
          PyList_Append(assignments.p, gg.p);
        }
      }
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
    put(result.p, "forward_returns", array_output ? matrix_array(returns.p, output_rows, columns) : Py_NewRef(returns.p));
    put(result.p, "groups", array_output ? matrix_array(assignments.p, output_rows, columns) : Py_NewRef(assignments.p));
    Py_INCREF(result.p);
    return result.p;
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
