#include "analysis.hpp"
#include "statistics.hpp"
#include "research_input.hpp"
namespace {
using namespace fp;
}
PyObject *ic_decay(PyObject *, PyObject *args) {
  try {
    PyObject *factor, *daily_returns, *members;
    int h, steps, minimum;
    if (!PyArg_ParseTuple(args, "OOOiii", &factor, &daily_returns, &members, &h, &steps,
                          &minimum))
      return nullptr;
    if (h < 1 || h > 252 || steps < 1 || steps > 10 || minimum < 2)
      throw std::invalid_argument("Invalid decay options");
    InputRef holder(factor, daily_returns, members);
    const auto &input = *holder.value;
    const auto &f = input.factors, &forward = input.forward(h);
    Owned out{PyList_New(0)};
    for (int k = 0; k < steps; ++k) {
      std::vector<double> ics, rankics;
      for (std::size_t t = 0; t < f.size(); ++t) {
        const auto signal = t + k * h;
        std::vector<double> a, b;
        std::vector<std::size_t> symbols;
        if (signal < forward.size())
          for (std::size_t n = 0; n < f[t].size(); ++n) {
            if (input.eligible(t, n) && std::isfinite(f[t][n]) &&
                std::isfinite(forward[signal][n])) {
              symbols.push_back(n);
              a.push_back(f[t][n]);
              b.push_back(forward[signal][n]);
            }
          }
        ics.push_back(correlation(a, b, minimum));
        rankics.push_back(a.size() >= static_cast<std::size_t>(minimum)
          ? correlation(paired_factor_ranks(input, t, symbols),
                        input.paired_return_ranks(h, signal, symbols), minimum)
          : missing);
      }
      Owned item{PyDict_New()};
      put(item.p, "step", PyLong_FromLong(k + 1));
      put(item.p, "entry_lag", PyLong_FromLong(k * h));
      put(item.p, "ic", moments(ics, 1, h - 1));
      put(item.p, "rank_ic", moments(rankics, 1, h - 1));
      PyList_Append(out.p, item.p);
    }
    return Py_NewRef(out.p);
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
