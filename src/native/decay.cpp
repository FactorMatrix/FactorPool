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
      ResearchInput::PairedSample sample;
      for (int k = 1; k < steps; ++k) {
        const auto lagged = input.correlate_pair(h, minimum, t, t + k * h, sample);
        ics[k][row] = lagged.ic;
        rankics[k][row] = lagged.rank_ic;
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
