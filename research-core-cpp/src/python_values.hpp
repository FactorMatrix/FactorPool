#pragma once
#include <Python.h>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <vector>
namespace fp {
constexpr double missing = std::numeric_limits<double>::quiet_NaN();
struct Owned {
  PyObject *p;
  ~Owned() { Py_XDECREF(p); }
};
using Matrix = std::vector<std::vector<double>>;
inline Matrix matrix(PyObject *object) {
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
inline PyObject *number(double x) {
  if (std::isfinite(x))
    return PyFloat_FromDouble(x);
  Py_RETURN_NONE;
}
inline void put(PyObject *obj, const char *key, PyObject *value) {
  Owned v{value};
  if (!value || PyDict_SetItemString(obj, key, value) < 0)
    throw std::runtime_error("Allocation failure");
}

inline PyObject *list(const std::vector<double> &values) {
  auto *out = PyList_New(values.size());
  if (!out)
    throw std::bad_alloc();
  for (std::size_t i = 0; i < values.size(); ++i)
    PyList_SET_ITEM(out, i, number(values[i]));
  return out;
}
} // namespace fp
