#include "analysis.hpp"
#include "statistics.hpp"
#include "research_input.hpp"
#include "parallel.hpp"
namespace {
using namespace fp;
}
PyObject *ic_decay(PyObject *, PyObject *args) {
  try {
    PyObject *prepared;
    int h, steps, minimum, signal_stride = 1;
    if (!PyArg_ParseTuple(args, "Oiii|i", &prepared, &h, &steps,
                          &minimum, &signal_stride))
      return nullptr;
    if (h < 1 || h > 252 || steps < 1 || steps > 10 || minimum < 2 || signal_stride < 1)
      throw std::invalid_argument("Invalid decay options");
    const auto &input = research_input(prepared);
    const auto &f = input.factors;
    input.prepare_correlations(h, minimum);
    const auto rows = f.size() ? 1 + (f.size() - 1) / signal_stride : 0;
    Owned out{PyList_New(0)};
    // Several signal/lag pairs can read the same future row. Give each
    // requested return row one writer before the calculation reads it.
    if (steps > 1) {
      std::vector<unsigned char> needed(input.returns.size());
      for (std::size_t row = 0; row < rows; ++row) {
        const auto t = row * signal_stride;
        for (int k = 1; k < steps; ++k) {
          const auto signal = t + static_cast<std::size_t>(k) * h;
          if (signal < needed.size()) needed[signal] = 1;
        }
      }
      parallel_for(needed.size(), f.columns(), [&](std::size_t row) {
        if (needed[row]) input.prepare_return_order(h, row);
      });
    }
    // Each row is evaluated for all requested entry lags in one worker pass.
    // This keeps the per-row scratch buffers alive across lags and avoids
    // repeatedly creating and joining the numeric worker set.
    std::vector<std::vector<double>> ics(steps, std::vector<double>(rows));
    std::vector<std::vector<double>> rankics(steps, std::vector<double>(rows));
    parallel_for(rows, f.columns(), [&](std::size_t row) {
      const auto t = row * signal_stride;
      const auto &metrics = input.correlation_row(h, minimum, t);
      ics[0][row] = metrics.ic;
      rankics[0][row] = metrics.rank_ic;
      if (steps == 1) return;
      std::vector<double> a, b;
      std::vector<std::size_t> symbols;
      a.reserve(f.columns());
      b.reserve(f.columns());
      symbols.reserve(f.columns());
      for (int k = 1; k < steps; ++k) {
        a.clear();
        b.clear();
        symbols.clear();
        const auto signal = t + k * h;
        if (signal < input.returns.size()) {
          const auto forward = input.forward_row(h, signal);
          for (std::size_t n = 0; n < f[t].size(); ++n) {
            if (input.eligible(t, n) && std::isfinite(f[t][n]) &&
                std::isfinite(forward[n])) {
              symbols.push_back(n);
              a.push_back(f[t][n]);
              b.push_back(forward[n]);
            }
          }
        }
        ics[k][row] = correlation(a, b, minimum);
        rankics[k][row] = a.size() >= static_cast<std::size_t>(minimum)
          ? correlation(paired_factor_ranks(input, t, symbols),
                        input.paired_return_ranks(h, signal, symbols), minimum)
          : missing;
      }
    });
    for (int k = 0; k < steps; ++k) {
      Owned item{PyDict_New()};
      put(item.p, "step", PyLong_FromLong(k + 1));
      put(item.p, "entry_lag", PyLong_FromLong(k * h));
      const int overlap_lag = (h - 1) / signal_stride;
      put(item.p, "ic", moments(ics[k], 1, overlap_lag));
      put(item.p, "rank_ic", moments(rankics[k], 1, overlap_lag));
      PyList_Append(out.p, item.p);
    }
    return Py_NewRef(out.p);
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
