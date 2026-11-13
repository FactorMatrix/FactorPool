#include "series_transforms.hpp"
#include "python_values.hpp"

PyObject* price_returns(PyObject*, PyObject* args) {
  try {
    PyObject *close_object, *previous_object;
    if (!PyArg_ParseTuple(args, "OO:price_returns", &close_object, &previous_object))
      return nullptr;
    const auto closes = fp::matrix(close_object), previous = fp::matrix(previous_object);
    if (closes.size() != previous.size() || closes.columns() != previous.columns())
      throw std::invalid_argument("Price return matrix axes differ");
    fp::Matrix output(closes.size(), closes.columns());
    PyThreadState* thread = PyEval_SaveThread();
    for (std::size_t t = 0; t < closes.size(); ++t)
      for (std::size_t n = 0; n < closes.columns(); ++n) {
        const double close = closes[t][n], before = previous[t][n];
        if (!std::isfinite(close) || close < 0 || !std::isfinite(before) || before <= 0)
          continue;
        // Preserve two separate binary64 operations from the legacy contract.
        double value = close / before;
        value -= 1;
        if (std::isfinite(value)) output[t][n] = value;
      }
    PyEval_RestoreThread(thread);
    const auto cells = output.size() * output.columns();
    if (cells > static_cast<std::size_t>(PY_SSIZE_T_MAX) / sizeof(double))
      throw std::invalid_argument("Price return publication is too large");
    fp::Owned bytes{PyBytes_FromStringAndSize(reinterpret_cast<const char*>(output.data()),
                                            static_cast<Py_ssize_t>(cells * sizeof(double)))};
    if (!bytes.p) return nullptr;
    return fp::matrix_array(bytes.p, output.size(), output.columns());
  } catch (const std::bad_alloc&) {
    return PyErr_NoMemory();
  } catch (const std::exception& error) {
    if (!PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, error.what());
    return nullptr;
  }
}

PyObject* prefix_ic_sum(PyObject*, PyObject* args) {
  try {
    PyObject* values;
    if (!PyArg_ParseTuple(args, "O:prefix_ic_sum", &values)) return nullptr;
    fp::Owned sequence{PySequence_Fast(values, "Expected an IC sequence")};
    if (!sequence.p) return nullptr;
    const auto count = PySequence_Fast_GET_SIZE(sequence.p);
    fp::Owned output{PyList_New(count)};
    if (!output.p) return nullptr;
    double total = 0;
    bool complete = true;
    for (Py_ssize_t i = 0; i < count; ++i) {
      auto* value = PySequence_Fast_GET_ITEM(sequence.p, i);
      if (!complete || value == Py_None) {
        complete = false;
        PyList_SET_ITEM(output.p, i, Py_NewRef(Py_None));
        continue;
      }
      const double number = PyFloat_AsDouble(value);
      if (PyErr_Occurred()) return nullptr;
      total += number;
      // Explicit NaN/Inf and arithmetic overflow retain the old float result.
      // Upstream IC statistics produce finite values or None; JSON validation
      // still rejects a caller's nonfinite cumulative series as before.
      auto* item = PyFloat_FromDouble(total);
      if (!item) return nullptr;
      PyList_SET_ITEM(output.p, i, item);
    }
    return Py_NewRef(output.p);
  } catch (const std::bad_alloc&) {
    return PyErr_NoMemory();
  } catch (const std::exception& error) {
    if (!PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, error.what());
    return nullptr;
  }
}
