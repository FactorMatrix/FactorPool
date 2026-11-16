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
    PyObject *prepared;
    int groups, minimum, ddof, horizon = 1, signal_stride = 1;
    if (!PyArg_ParseTuple(args, "Oiii|ii", &prepared, &groups, &minimum,
                          &ddof, &horizon, &signal_stride))
      return nullptr;
    if (horizon < 1 || horizon > 252 || groups < 1 || groups > 100 ||
        minimum < 2 || ddof < 0 || ddof > 1 || signal_stride < 1)
      throw std::invalid_argument("Invalid analysis options");
    const auto &input = research_input(prepared);
    const auto &f = input.factors, &forward = input.forward(horizon);
    input.prepare_correlations(horizon, minimum);
    const auto output_rows = f.size() ? 1 + (f.size() - 1) / signal_stride : 0;
    const auto columns = f.columns();
    if (output_rows && columns > static_cast<std::size_t>(PY_SSIZE_T_MAX) / sizeof(double) / output_rows)
      throw std::invalid_argument("Research publication is too large");
    const auto output_bytes = static_cast<Py_ssize_t>(output_rows * columns * sizeof(double));
    Owned result{PyDict_New()}, days{PyList_New(0)},
        returns{PyBytes_FromStringAndSize(nullptr, output_bytes)},
        assignments{PyBytes_FromStringAndSize(nullptr, output_bytes)};
    if (!result.p || !days.p || !returns.p || !assignments.p) throw std::bad_alloc();
    std::vector<double> ics, rankics;
    std::size_t covered = 0, total = 0, pairs = 0;
    struct DayValues {
      std::size_t covered = 0, total = 0, pairs = 0;
      double ic = missing, ric = missing;
    };
    // Allocate Python storage on the caller, then give workers disjoint owned
    // byte ranges. No Python API or mutable input cache allocation runs there.
    auto *return_data = PyBytes_AS_STRING(returns.p);
    auto *group_data = PyBytes_AS_STRING(assignments.p);
    constexpr std::size_t block_rows = 256;
    for (std::size_t first = 0; first < output_rows; first += block_rows) {
      const auto length = std::min(block_rows, output_rows - first);
      std::vector<DayValues> values(length);
      parallel_for(length, columns, [&](std::size_t row) {
        const auto ordinal = first + row, t = ordinal * signal_stride;
        auto &value = values[row];
        const auto r = forward[t];
        std::vector<double> labels(f[t].size(), missing);
        const auto &order = input.order[t];
        for (std::size_t n = 0; n < f[t].size(); ++n) {
          if (input.eligible(t, n))
            ++value.total;
          if (std::isfinite(f[t][n]) && input.eligible(t, n)) {
            ++value.covered;
          }
        }
        const auto &metrics = input.correlation_row(horizon, minimum, t);
        value.ic = metrics.ic;
        value.ric = metrics.rank_ic;
        value.pairs = metrics.pairs;
        for (std::size_t k = 0; k < order.size(); ++k) {
          const auto g = k * groups / order.size(), n = order[k];
          labels[n] = g + 1;
        }
        const auto offset = ordinal * columns * sizeof(double);
        if (columns) {
          std::memcpy(return_data + offset, r.data(), columns * sizeof(double));
          std::memcpy(group_data + offset, labels.data(), columns * sizeof(double));
        }
      });
      // Sample vectors and summaries retain their chronological reduction
      // order. Held group books are evaluated once by portfolio_report.
      for (const auto &value : values) {
        const auto ic = value.ic, ric = value.ric;
        covered += value.covered;
        total += value.total;
        pairs += value.pairs;
        if (std::isfinite(ic)) ics.push_back(ic);
        if (std::isfinite(ric)) rankics.push_back(ric);
        Owned day{PyDict_New()};
        put(day.p, "ic", number(ic));
        put(day.p, "rank_ic", number(ric));
        put(day.p, "pairs", PyLong_FromSize_t(value.pairs));
        put(day.p, "reason",
            PyUnicode_FromString(value.pairs < static_cast<std::size_t>(minimum)
                                     ? "insufficient_pairs"
                                 : !std::isfinite(ic) ? "constant_cross_section"
                                                      : "ok"));
        PyList_Append(days.p, day.p);
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
    put(result.p, "forward_returns", matrix_array(returns.p, output_rows, columns));
    put(result.p, "groups", matrix_array(assignments.p, output_rows, columns));
    Py_INCREF(result.p);
    return result.p;
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
