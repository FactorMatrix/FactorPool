#pragma once
#include <Python.h>
#include <cmath>
#include <cstring>
#include <limits>
#include <span>
#include <stdexcept>
#include <string_view>
#include <vector>
namespace fp {
constexpr double missing = std::numeric_limits<double>::quiet_NaN();
struct Owned {
  PyObject *p;
  ~Owned() { Py_XDECREF(p); }
};
class Matrix {
  std::size_t rows_ = 0, columns_ = 0;
  std::vector<double> values_;

public:
  Matrix() = default;
  Matrix(std::size_t rows, std::size_t columns, double fill = missing)
      : rows_(rows), columns_(columns) {
    if (columns && rows > values_.max_size() / columns)
      throw std::invalid_argument("Research matrix is too large");
    values_.assign(rows * columns, fill);
  }
  std::size_t size() const { return rows_; }
  std::size_t columns() const { return columns_; }
  bool empty() const { return rows_ == 0; }
  double *data() { return values_.data(); }
  const double *data() const { return values_.data(); }
  std::span<double> operator[](std::size_t row) {
    return {columns_ ? values_.data() + row * columns_ : values_.data(), columns_};
  }
  std::span<const double> operator[](std::size_t row) const {
    return {columns_ ? values_.data() + row * columns_ : values_.data(), columns_};
  }
};
inline Matrix matrix(PyObject *object) {
  if (PyObject_CheckBuffer(object)) {
    struct Buffer {
      Py_buffer view{};
      bool acquired = false;
      ~Buffer() {
        if (acquired)
          PyBuffer_Release(&view);
      }
    } buffer;
    if (PyObject_GetBuffer(object, &buffer.view, PyBUF_FULL_RO) < 0)
      throw std::invalid_argument("Expected matrix buffer");
    buffer.acquired = true;
    const auto &view = buffer.view;
    const std::string_view format = view.format ? view.format : "";
    if (view.ndim != 2 || !view.shape || view.shape[0] < 0 ||
        view.shape[1] < 0 || view.itemsize != sizeof(double) ||
        (format != "d" && format != "=d" && format != "@d") ||
        !PyBuffer_IsContiguous(&buffer.view, 'C'))
      throw std::invalid_argument(
          "Expected a C-contiguous two-dimensional native float64 matrix");
    Matrix result(static_cast<std::size_t>(view.shape[0]),
                  static_cast<std::size_t>(view.shape[1]));
    const auto bytes = result.size() * result.columns() * sizeof(double);
    if (view.len < 0 || static_cast<std::size_t>(view.len) != bytes)
      throw std::invalid_argument("Research buffer shape differs from length");
    if (bytes)
      std::memcpy(result.data(), view.buf, bytes);
    return result;
  }
  Owned rows{PySequence_Fast(object, "Expected matrix")};
  if (!rows.p)
    throw std::invalid_argument("Expected matrix");
  Matrix result;
  for (Py_ssize_t i = 0; i < PySequence_Fast_GET_SIZE(rows.p); ++i) {
    Owned cells{
        PySequence_Fast(PySequence_Fast_GET_ITEM(rows.p, i), "Expected row")};
    if (!cells.p)
      throw std::invalid_argument("Expected row");
    if (i == 0)
      result = Matrix(static_cast<std::size_t>(PySequence_Fast_GET_SIZE(rows.p)),
                      static_cast<std::size_t>(PySequence_Fast_GET_SIZE(cells.p)));
    if (static_cast<std::size_t>(PySequence_Fast_GET_SIZE(cells.p)) != result.columns())
      throw std::invalid_argument("Ragged matrix");
    for (Py_ssize_t j = 0; j < PySequence_Fast_GET_SIZE(cells.p); ++j) {
      auto *cell = PySequence_Fast_GET_ITEM(cells.p, j);
      double x = cell == Py_None ? missing : PyFloat_AsDouble(cell);
      if (PyErr_Occurred())
        throw std::invalid_argument("Expected numbers or null");
      result[static_cast<std::size_t>(i)][static_cast<std::size_t>(j)] = x;
    }
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
