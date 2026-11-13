#include "alpha_targets.hpp"
#include "parallel.hpp"
#include "python_values.hpp"
#include <algorithm>
#include <cstddef>
#include <cstring>
#include <string_view>

namespace {
using namespace fp;

// Preserve the reduction tree used by the pinned NumPy 1.26.4 baseline,
// including its 128-element blocks and eight accumulator lanes. This is a
// numerical compatibility constraint, not a dependency on NumPy arithmetic.
// Reduction ordering adapted from NumPy, Copyright (c) 2005-2023, NumPy
// Developers. BSD-3-Clause terms and disclaimer are retained in NUMPY-LICENSE.
// https://github.com/numpy/numpy/blob/v1.26.4/numpy/core/src/umath/loops_utils.h.src
template<class T, class Get> T pairwise(std::size_t first, std::size_t count, Get get) {
  if (count < 8) {
    T result = -T{0};
    for (std::size_t i = 0; i < count; ++i) result += get(first + i);
    return result;
  }
  if (count > 128) {
    const auto left = count / 2 - (count / 2) % 8;
    return pairwise<T>(first, left, get) + pairwise<T>(first + left, count - left, get);
  }
  T lanes[8];
  for (std::size_t i = 0; i < 8; ++i) lanes[i] = get(first + i);
  std::size_t i = 8;
  for (; i < count - count % 8; i += 8)
    for (std::size_t lane = 0; lane < 8; ++lane) lanes[lane] += get(first + i + lane);
  T result = ((lanes[0] + lanes[1]) + (lanes[2] + lanes[3])) +
             ((lanes[4] + lanes[5]) + (lanes[6] + lanes[7]));
  for (; i < count; ++i) result += get(first + i);
  return result;
}
template<class T, class Get> T sum(std::size_t count, Get get) {
  // The baseline ufunc reduction iterator publishes successive 8192-element
  // inner loops; each inner tree adds to the same scalar accumulator.
  T result = 0;
  for (std::size_t first = 0; first < count; first += 8192)
    result += pairwise<T>(first, std::min(std::size_t{8192}, count - first), get);
  return result;
}
struct Constraint {
  std::size_t count = 0;
  double capacity = 0, gross = missing, net = missing;
  bool feasible = false;
};
struct Trace {
  std::size_t row;
  std::vector<double> neutralized;
};
std::vector<Trace> selected_traces(PyObject* object, std::size_t rows, std::size_t columns) {
  Owned items{PySequence_Fast(object, "Alpha trace indices must be integers")};
  if (!items.p) throw std::invalid_argument("Alpha trace indices must be integers");
  std::vector<std::size_t> indices;
  for (Py_ssize_t i = 0; i < PySequence_Fast_GET_SIZE(items.p); ++i) {
    auto* item = PySequence_Fast_GET_ITEM(items.p, i);
    if (!PyLong_CheckExact(item)) throw std::invalid_argument("Alpha trace indices must be integers");
    const auto row = PyLong_AsUnsignedLongLong(item);
    if (PyErr_Occurred()) {
      PyErr_Clear();
      throw std::invalid_argument("Alpha trace index is outside the factor axis");
    }
    if (row >= rows) throw std::invalid_argument("Alpha trace index is outside the factor axis");
    indices.push_back(static_cast<std::size_t>(row));
  }
  std::sort(indices.begin(), indices.end());
  indices.erase(std::unique(indices.begin(), indices.end()), indices.end());
  if (indices.size() > 5) throw std::invalid_argument("Alpha traces support at most five signal rows");
  std::vector<Trace> result;
  for (auto row : indices) result.push_back({row, std::vector<double>(columns, missing)});
  return result;
}
void normalize(const Matrix& factors, const Matrix& members, bool market, double cap,
               char* output, std::vector<Trace>& traces, std::vector<Constraint>& constraints) {
  std::vector<std::ptrdiff_t> trace_slot(factors.size(), -1);
  for (std::size_t slot = 0; slot < traces.size(); ++slot) trace_slot[traces[slot].row] = slot;
  parallel_for(factors.size(), factors.columns(), [&](std::size_t t) {
    std::vector<std::size_t> selected;
    std::vector<long double> values;
    selected.reserve(factors.columns()); values.reserve(factors.columns());
    for (std::size_t n = 0; n < factors.columns(); ++n)
      if (std::isfinite(members[t][n]) && members[t][n] > 0 && std::isfinite(factors[t][n])) {
        selected.push_back(n); values.push_back(factors[t][n]);
      }
    Trace* trace = trace_slot[t] < 0 ? nullptr : &traces[static_cast<std::size_t>(trace_slot[t])];
    Constraint constraint;
    constraint.count = values.size(); constraint.capacity = static_cast<double>(values.size()) * cap;
    constraint.feasible = constraint.capacity >= 1 - 1e-12;
    std::vector<double> targets(factors.columns(), missing), weights;
    const bool constant = market && !values.empty() &&
        std::all_of(values.begin(), values.end(), [&](auto value) { return value == values.front(); });
    if (constant) {
      if (trace) for (auto n : selected) trace->neutralized[n] = 0;
    } else if (!values.empty()) {
      if (market) {
        const auto mean = sum<long double>(values.size(), [&](auto n) { return values[n]; }) /
                          static_cast<long double>(values.size());
        for (auto& value : values) value -= mean;
      }
      if (trace)
        for (std::size_t slot = 0; slot < selected.size(); ++slot)
          trace->neutralized[selected[slot]] = static_cast<double>(values[slot]);
      const auto norm = sum<long double>(values.size(), [&](auto n) { return std::abs(values[n]); });
      if (std::isfinite(norm) && norm > 0) {
        weights.reserve(values.size());
        std::fill(targets.begin(), targets.end(), 0.);
        for (std::size_t slot = 0; slot < values.size(); ++slot) {
          double weight = static_cast<double>(values[slot] / norm);
          if (cap > 0) weight = std::clamp(weight, -cap, cap);
          weights.push_back(weight); targets[selected[slot]] = weight;
        }
        if (!constraints.empty()) {
          constraint.gross = sum<double>(weights.size(), [&](auto n) { return std::abs(weights[n]); });
          constraint.net = sum<double>(weights.size(), [&](auto n) { return weights[n]; });
        }
      }
    }
    if (!targets.empty()) std::memcpy(output + t * factors.columns() * sizeof(double),
                                     targets.data(), targets.size() * sizeof(double));
    if (!constraints.empty()) constraints[t] = constraint;
  });
}
PyObject* constraints_object(const std::vector<Constraint>& values) {
  Owned result{PyList_New(values.size())};
  if (!result.p) throw std::bad_alloc();
  for (std::size_t t = 0; t < values.size(); ++t) {
    const auto& value = values[t];
    Owned item{PyDict_New()};
    if (!item.p) throw std::bad_alloc();
    put(item.p, "index", PyLong_FromSize_t(t));
    put(item.p, "valid_count", PyLong_FromSize_t(value.count));
    put(item.p, "capacity", number(value.capacity));
    put(item.p, "full_gross_feasible", PyBool_FromLong(value.feasible));
    put(item.p, "target_gross", number(value.gross));
    put(item.p, "target_net", number(value.net));
    put(item.p, "unallocated_gross", number(std::isfinite(value.gross) ? std::max(0., 1 - value.gross) : missing));
    put(item.p, "status", PyUnicode_FromString(!std::isfinite(value.gross) ? "no_valid_weights" :
        !value.feasible ? "unit_gross_capacity_infeasible" : value.gross < 1 - 1e-12 ? "unallocated_gross" : "ok"));
    PyList_SET_ITEM(result.p, t, Py_NewRef(item.p));
  }
  return Py_NewRef(result.p);
}
} // namespace

PyObject* alpha_targets(PyObject*, PyObject* args) {
  try {
    PyObject *factor_object, *member_object, *trace_object;
    const char* mode;
    double cap;
    int include_constraints;
    if (!PyArg_ParseTuple(args, "OOsdOp:alpha_targets", &factor_object, &member_object, &mode,
                          &cap, &trace_object, &include_constraints)) return nullptr;
    const std::string_view neutralization(mode);
    if (neutralization != "NONE" && neutralization != "MARKET")
      throw std::invalid_argument("Unsupported Alpha neutralization");
    if (!std::isfinite(cap) || cap < 0 || cap > 1)
      throw std::invalid_argument("Alpha truncation must be between zero and one");
    const auto factors = matrix(factor_object), members = matrix(member_object);
    if (factors.size() != members.size() || factors.columns() != members.columns())
      throw std::invalid_argument("Alpha factors and membership must have the same date x symbol shape");
    auto traces = selected_traces(trace_object, factors.size(), factors.columns());
    if (factors.columns() && factors.size() > static_cast<std::size_t>(PY_SSIZE_T_MAX) / sizeof(double) / factors.columns())
      throw std::invalid_argument("Alpha target publication is too large");
    const auto size = factors.size() * factors.columns() * sizeof(double);
    Owned bytes{PyBytes_FromStringAndSize(nullptr, static_cast<Py_ssize_t>(size))};
    if (!bytes.p) throw std::bad_alloc();
    std::vector<Constraint> constraints(include_constraints && cap > 0 ? factors.size() : 0);
    PyThreadState* thread = PyEval_SaveThread();
    try { normalize(factors, members, neutralization == "MARKET", cap,
                    PyBytes_AS_STRING(bytes.p), traces, constraints); }
    catch (...) { PyEval_RestoreThread(thread); throw; }
    PyEval_RestoreThread(thread);
    Owned output{PyDict_New()}, trace_output{PyDict_New()};
    if (!output.p || !trace_output.p) throw std::bad_alloc();
    put(output.p, "targets", matrix_array(bytes.p, factors.size(), factors.columns()));
    for (const auto& trace : traces) {
      Owned key{PyLong_FromSize_t(trace.row)}, raw{PyBytes_FromStringAndSize(
          reinterpret_cast<const char*>(trace.neutralized.data()), trace.neutralized.size() * sizeof(double))};
      if (!key.p || !raw.p) throw std::bad_alloc();
      Owned row{matrix_array(raw.p, 1, factors.columns())};
      Owned flat{PyObject_CallMethod(row.p, "reshape", "n", static_cast<Py_ssize_t>(factors.columns()))};
      if (!flat.p || PyDict_SetItem(trace_output.p, key.p, flat.p) < 0) throw std::bad_alloc();
    }
    put(output.p, "neutralized", Py_NewRef(trace_output.p));
    put(output.p, "constraints", constraints_object(constraints));
    return Py_NewRef(output.p);
  } catch (const std::bad_alloc&) { return PyErr_NoMemory(); }
    catch (const std::exception& error) {
      if (!PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, error.what());
      return nullptr;
    }
}
