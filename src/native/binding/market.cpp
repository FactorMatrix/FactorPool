// Buffer ownership and capsule transport only; all R/Q mathematics is C++ core.
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "market.hpp"
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string_view>
#include <type_traits>

namespace {
namespace market = factorpool::market;
constexpr auto state_tag = "factorpool.market.adjustment.prev-close-last-actual-v2";
struct Object {
  PyObject* value;
  explicit Object(PyObject* value) : value(value) {}
  ~Object() { Py_XDECREF(value); }
  PyObject* release() { auto* result = value; value = nullptr; return result; }
};
struct Buffer {
  Py_buffer view{};
  explicit Buffer(PyObject* value) {
    if (PyObject_GetBuffer(value, &view, PyBUF_FULL_RO) < 0)
      throw std::invalid_argument("Expected a contiguous native matrix buffer");
  }
  ~Buffer() { PyBuffer_Release(&view); }
};
struct Matrix {
  std::vector<double> values;
  std::size_t rows, columns;
  explicit Matrix(PyObject* object) {
    Buffer buffer(object);
    const auto& view = buffer.view;
    const std::string_view format = view.format ? view.format : "";
    if (view.ndim != 2 || !view.shape || view.shape[0] < 0 || view.shape[1] < 0 ||
        view.itemsize != sizeof(double) || (format != "d" && format != "=d" && format != "@d") ||
        !PyBuffer_IsContiguous(&view, 'C'))
      throw std::invalid_argument("Expected a C-contiguous two-dimensional native float64 matrix");
    rows = static_cast<std::size_t>(view.shape[0]); columns = static_cast<std::size_t>(view.shape[1]);
    if (columns && rows > values.max_size() / columns)
      throw std::invalid_argument("Market matrix is too large");
    values.resize(rows * columns);
    if (view.len < 0 || static_cast<std::size_t>(view.len) != values.size() * sizeof(double))
      throw std::invalid_argument("Market buffer shape differs from its length");
    if (!values.empty()) std::memcpy(values.data(), view.buf, view.len);
  }
  market::MatrixView span() const { return {values, rows, columns}; }
};
template<class T> std::vector<T> vector(PyObject* object) {
  Buffer buffer(object);
  const auto& view = buffer.view;
  const std::string_view format = view.format ? view.format : "";
  const bool native = std::is_same_v<T, double>
      ? (format == "d" || format == "=d" || format == "@d")
      : (format == "q" || format == "=q" || format == "@q" ||
         ((format == "l" || format == "=l" || format == "@l") && sizeof(long) == sizeof(T)));
  if (view.ndim != 1 || !view.shape || view.shape[0] < 0 || view.itemsize != sizeof(T) ||
      !native || !PyBuffer_IsContiguous(&view, 'C'))
    throw std::invalid_argument("Expected a contiguous one-dimensional native numeric vector");
  std::vector<T> result(static_cast<std::size_t>(view.shape[0]));
  if (view.len < 0 || static_cast<std::size_t>(view.len) != result.size() * sizeof(T))
    throw std::invalid_argument("Vector buffer shape differs from its length");
  if (!result.empty()) std::memcpy(result.data(), view.buf, view.len);
  return result;
}
template<class T> PyObject* bytes(const std::vector<T>& values) {
  if (values.size() > static_cast<std::size_t>(PY_SSIZE_T_MAX) / sizeof(T))
    throw std::invalid_argument("Market publication is too large");
  return PyBytes_FromStringAndSize(reinterpret_cast<const char*>(values.data()), values.size() * sizeof(T));
}
bool put(PyObject* target, const char* key, PyObject* value) {
  Object owned(value);
  return value && PyDict_SetItemString(target, key, value) == 0;
}
PyObject* failure(const std::exception& error) {
  if (!PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, error.what());
  return nullptr;
}
void drop_state(PyObject* capsule) {
  auto* state = static_cast<market::AdjustmentState*>(PyCapsule_GetPointer(capsule, state_tag));
  if (state) delete state; else PyErr_Clear();
}
PyObject* returns(PyObject*, PyObject* args) {
  try {
    PyObject *close, *previous, *volume, *turnover, *member;
    if (!PyArg_ParseTuple(args, "OOOOO:daily_returns", &close, &previous, &volume, &turnover, &member)) return nullptr;
    const Matrix c(close), p(previous), v(volume), a(turnover), m(member);
    const market::Inputs input{c.span(), p.span(), v.span(), a.span(), m.span(), {}};
    std::vector<double> result;
    PyThreadState* thread = PyEval_SaveThread();
    try { result = market::daily_returns(input); }
    catch (...) { PyEval_RestoreThread(thread); throw; }
    PyEval_RestoreThread(thread);
    return bytes(result);
  } catch (const std::bad_alloc&) { return PyErr_NoMemory(); }
    catch (const std::exception& error) { return failure(error); }
}
PyObject* adjustment(PyObject*, PyObject* args) {
  try {
    PyObject *date_object, *close, *previous, *volume, *turnover, *member, *suspended, *state_object = Py_None;
    if (!PyArg_ParseTuple(args, "OOOOOOO|O:adjustment", &date_object, &close, &previous, &volume,
                          &turnover, &member, &suspended, &state_object)) return nullptr;
    const auto day = vector<std::int64_t>(date_object);
    const Matrix c(close), p(previous), v(volume), a(turnover), m(member), s(suspended);
    const market::Inputs input{c.span(), p.span(), v.span(), a.span(), m.span(), s.span()};
    const market::AdjustmentState* state = nullptr;
    if (state_object != Py_None) {
      state = static_cast<market::AdjustmentState*>(PyCapsule_GetPointer(state_object, state_tag));
      if (!state) throw std::invalid_argument("Invalid market adjustment state");
    }
    // Copy the state as well as every buffer before releasing the GIL. This
    // call never mutates an earlier result's capsule or shares mutable state.
    const std::optional<market::AdjustmentState> initial = state ? std::optional(*state) : std::nullopt;
    market::AdjustmentResult result;
    PyThreadState* thread = PyEval_SaveThread();
    try { result = market::derive_adjustment(day, input, initial ? &*initial : nullptr); }
    catch (...) { PyEval_RestoreThread(thread); throw; }
    PyEval_RestoreThread(thread);
    auto owned = std::make_unique<market::AdjustmentState>(std::move(result.state));
    Object capsule(PyCapsule_New(owned.get(), state_tag, drop_state));
    if (!capsule.value) return nullptr;
    owned.release();
    Object output(PyDict_New());
    if (!output.value || !put(output.value, "factor", bytes(result.factor)) ||
        !put(output.value, "q", bytes(result.q)) || !put(output.value, "segment", bytes(result.segment)) ||
        !put(output.value, "known", bytes(result.known)) || !put(output.value, "state", capsule.release())) return nullptr;
    return output.release();
  } catch (const std::bad_alloc&) { return PyErr_NoMemory(); }
    catch (const std::exception& error) { return failure(error); }
}
PyObject* adjust_values(PyObject*, PyObject* args) {
  try {
    PyObject *row, *historical_q, *basis_q, *historical_segment, *basis_segment;
    int inverse;
    if (!PyArg_ParseTuple(args, "OOOOOp:adjust_values", &row, &historical_q, &basis_q,
                          &historical_segment, &basis_segment, &inverse)) return nullptr;
    const auto values = vector<double>(row), hq = vector<double>(historical_q), bq = vector<double>(basis_q);
    const auto hs = vector<std::int64_t>(historical_segment), bs = vector<std::int64_t>(basis_segment);
    std::vector<double> result;
    PyThreadState* thread = PyEval_SaveThread();
    try { result = market::adjust_values(values, hq, bq, hs, bs, inverse != 0); }
    catch (...) { PyEval_RestoreThread(thread); throw; }
    PyEval_RestoreThread(thread);
    return bytes(result);
  } catch (const std::bad_alloc&) { return PyErr_NoMemory(); }
    catch (const std::exception& error) { return failure(error); }
}
PyMethodDef methods[] = {
    {"daily_returns", returns, METH_VARARGS, "Owned R matrix using the volume-zero-v1 contract."},
    {"adjustment", adjustment, METH_VARARGS, "Owned factor/Q/segment/known matrices and isolated next state."},
    {"adjust_values", adjust_values, METH_VARARGS, "Owned price or inverse volume row on one signal-close basis."},
    {nullptr, nullptr, 0, nullptr}};
PyModuleDef module = {PyModuleDef_HEAD_INIT, "_factorpool_market", nullptr, -1, methods,
                     nullptr, nullptr, nullptr, nullptr};
} // namespace
PyMODINIT_FUNC PyInit__factorpool_market() {
  auto* value = PyModule_Create(&module);
  if (!value) return nullptr;
  if (PyModule_AddStringConstant(value, "RETURN_POLICY_VERSION", market::return_version) < 0 ||
      PyModule_AddStringConstant(value, "ADJUSTMENT_POLICY_VERSION", market::adjustment_version) < 0) {
    Py_DECREF(value); return nullptr;
  }
  return value;
}
