// Ownership/conversion only. Numerical extensions are in operator_kernels;
// shared arithmetic/CS and Graph state are called by operator_program.
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "operator_program.hpp"
#include <algorithm>
#include <climits>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace {
namespace pool = factorpool::operators;
struct Owned {
    PyObject* value;
    explicit Owned(PyObject* value): value(value) {}
    ~Owned() { Py_XDECREF(value); }
    Owned(const Owned&) = delete;
    PyObject* release() { auto* result = value; value = nullptr; return result; }
};
constexpr const char* tag = "factorpool.operators.v1";
PyObject* failure(const std::exception& error) {
    if (!PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, error.what());
    return nullptr;
}
PyObject* required(PyObject* object, const char* key) {
    if (!PyDict_Check(object)) throw std::invalid_argument("Pool IR rows must be dictionaries");
    auto* result = PyDict_GetItemString(object, key);
    if (!result) throw std::invalid_argument(std::string("Missing Pool IR field: ") + key);
    return result;
}
std::string text(PyObject* object) {
    if (!PyUnicode_Check(object)) throw std::invalid_argument("Expected a string");
    Py_ssize_t length = 0;
    const auto* value = PyUnicode_AsUTF8AndSize(object, &length);
    if (!value || std::memchr(value, 0, length)) throw std::invalid_argument("Invalid string");
    return {value, static_cast<std::size_t>(length)};
}
std::size_t index(PyObject* object) {
    if (!PyLong_CheckExact(object)) throw std::invalid_argument("Pool IR index must be an integer");
    const auto result = PyLong_AsSize_t(object);
    if (PyErr_Occurred()) throw std::invalid_argument("Pool IR index out of range");
    return result;
}
int integer(PyObject* object) {
    if (!PyLong_CheckExact(object)) throw std::invalid_argument("Pool IR period must be an integer");
    const auto result = PyLong_AsLong(object);
    if (PyErr_Occurred() || result < INT_MIN || result > INT_MAX) throw std::invalid_argument("Pool IR period out of range");
    return static_cast<int>(result);
}
double number(PyObject* object) {
    const double value = PyFloat_AsDouble(object);
    if (PyErr_Occurred()) throw std::invalid_argument("Expected a numeric value");
    return value;
}
template<class F> void each(PyObject* object, F function) {
    if (PyUnicode_Check(object) || PyBytes_Check(object)) throw std::invalid_argument("Expected a sequence, not text");
    Owned sequence(PySequence_Fast(object, "Expected a sequence"));
    if (!sequence.value) throw std::invalid_argument("Expected a sequence");
    for (Py_ssize_t i = 0; i < PySequence_Fast_GET_SIZE(sequence.value); ++i)
        function(PySequence_Fast_GET_ITEM(sequence.value, i));
}
std::vector<std::string> strings(PyObject* object) {
    std::vector<std::string> result; each(object, [&](auto* item) { result.push_back(text(item)); }); return result;
}
std::vector<std::size_t> indices(PyObject* object) {
    std::vector<std::size_t> result; each(object, [&](auto* item) { result.push_back(index(item)); }); return result;
}
pool::graph::Spec graph_spec(PyObject* object) {
    if (integer(required(object, "version")) != 1) throw std::invalid_argument("Unsupported temporal IR version");
    pool::graph::Spec result;
    each(required(object, "nodes"), [&](auto* item) {
        pool::graph::Node node;
        node.op = pool::graph::parse_op(text(required(item, "op")));
        node.field = text(required(item, "field")); node.inputs = indices(required(item, "inputs"));
        node.period = integer(required(item, "period")); node.ddof = integer(required(item, "ddof"));
        node.value = number(required(item, "value")); result.nodes.push_back(std::move(node));
    });
    result.output = index(required(object, "output")); result.readiness = index(required(object, "ready"));
    return result;
}
pool::Spec read_spec(PyObject* object) {
    if (text(required(object, "version")) != "pool-operators-v1") throw std::invalid_argument("Unsupported Pool operator IR version");
    pool::Spec result;
    each(required(object, "nodes"), [&](auto* item) {
        pool::Node node;
        node.op = text(required(item, "op")); node.field = text(required(item, "field"));
        node.inputs = indices(required(item, "inputs")); node.value = number(required(item, "value"));
        node.dependency = index(required(item, "dependency")); result.nodes.push_back(std::move(node));
    });
    result.output = index(required(object, "output")); result.ready = index(required(object, "ready"));
    each(required(object, "temporals"), [&](auto* item) {
        auto* deferred = required(item, "deferred");
        if (!PyBool_Check(deferred)) throw std::invalid_argument("Temporal deferred flag must be bool");
        result.temporals.emplace_back(graph_spec(item), strings(required(item, "bindings")), deferred == Py_True);
    });
    return result;
}
pool::Program& handle(PyObject* capsule) {
    auto* value = static_cast<pool::Program*>(PyCapsule_GetPointer(capsule, tag));
    if (!value) throw std::invalid_argument("Invalid Pool operator handle");
    return *value;
}
void drop(PyObject* capsule) { delete static_cast<pool::Program*>(PyCapsule_GetPointer(capsule, tag)); }
PyObject* create(PyObject*, PyObject* args) {
    try {
        PyObject *spec, *symbols;
        if (!PyArg_ParseTuple(args, "OO:create", &spec, &symbols)) return nullptr;
        auto owned = std::make_unique<pool::Program>(read_spec(spec), strings(symbols));
        auto* result = PyCapsule_New(owned.get(), tag, drop);
        if (result) owned.release();
        return result;
    } catch (const std::exception& error) { return failure(error); }
}
PyObject* set_symbols(PyObject*, PyObject* args) {
    try {
        PyObject *capsule, *symbols;
        if (!PyArg_ParseTuple(args, "OO:set_symbols", &capsule, &symbols)) return nullptr;
        handle(capsule).set_symbols(strings(symbols)); Py_RETURN_NONE;
    } catch (const std::exception& error) { return failure(error); }
}
PyObject* update(PyObject*, PyObject* args) {
    try {
        PyObject *capsule, *columns, *present; long long time;
        if (!PyArg_ParseTuple(args, "OLOO:update", &capsule, &time, &columns, &present)) return nullptr;
        auto& program = handle(capsule);
        if (!PyDict_Check(columns)) throw std::invalid_argument("Pool columns must be a dictionary");
        std::vector<std::string> fields; std::vector<std::vector<double>> values;
        Py_ssize_t position = 0; PyObject *key, *value;
        while (PyDict_Next(columns, &position, &key, &value)) {
            fields.push_back(text(key)); values.emplace_back();
            each(value, [&](auto* item) { values.back().push_back(number(item)); });
        }
        std::vector<pool::graph::Column> input;
        for (std::size_t i = 0; i < fields.size(); ++i) input.push_back({fields[i], values[i]});
        std::vector<std::uint8_t> mask;
        each(present, [&](auto* item) {
            const auto value = number(item);
            if (!std::isfinite(value) || (value != 0 && value != 1)) throw std::invalid_argument("Pool present mask must contain 0/1");
            mask.push_back(value != 0);
        });
        // All Python inputs are copied before releasing the GIL; no Python
        // object or mutable borrowed buffer crosses into the numerical loop.
        PyThreadState* thread = PyEval_SaveThread();
        try { program.update(time, input, mask); }
        catch (...) { PyEval_RestoreThread(thread); throw; }
        PyEval_RestoreThread(thread); Py_RETURN_NONE;
    } catch (const std::exception& error) { return failure(error); }
}
bool put(PyObject* target, const char* key, PyObject* value) {
    Owned owned(value); return value && PyDict_SetItemString(target, key, value) == 0;
}
template<class T> PyObject* bytes(const std::vector<T>& values) {
    return PyBytes_FromStringAndSize(reinterpret_cast<const char*>(values.data()), values.size() * sizeof(T));
}
PyObject* snapshot(PyObject*, PyObject* args) {
    try {
        PyObject* capsule;
        if (!PyArg_ParseTuple(args, "O:snapshot", &capsule)) return nullptr;
        auto& program = handle(capsule);
        Owned result(PyDict_New()), symbols(PyTuple_New(program.symbols().size()));
        if (!result.value || !symbols.value) return nullptr;
        for (std::size_t i = 0; i < program.symbols().size(); ++i) {
            const auto& symbol = program.symbols()[i];
            auto* item = PyUnicode_FromStringAndSize(symbol.data(), symbol.size());
            if (!item || PyTuple_SetItem(symbols.value, i, item) < 0) return nullptr;
        }
        if (!put(result.value, "symbols", symbols.release()) || !put(result.value, "current", bytes(program.current())) ||
            !put(result.value, "valid", bytes(program.valid())) || !put(result.value, "ready", bytes(program.ready())) ||
            !put(result.value, "batch_time", PyLong_FromLongLong(program.time())) || !put(result.value, "complete", PyBool_FromLong(1)) ||
            !put(result.value, "tradable", PyBool_FromLong(std::any_of(program.valid().begin(), program.valid().end(), [](auto value) { return value != 0; })))) return nullptr;
        return result.release();
    } catch (const std::exception& error) { return failure(error); }
}
PyMethodDef methods[] = {
    {"create", create, METH_VARARGS, "Create a Pool native expression plan using pinned shared kernels."},
    {"set_symbols", set_symbols, METH_VARARGS, "Set the active sorted axis; removed temporal state is released."},
    {"update", update, METH_VARARGS, "Evaluate one complete daily frame with independent column masks."},
    {"snapshot", snapshot, METH_VARARGS, "Copy the current numerical publication into owned buffers."},
    {nullptr, nullptr, 0, nullptr}};
PyModuleDef module = {PyModuleDef_HEAD_INIT, "_factorpool_operators", nullptr, -1, methods, nullptr, nullptr, nullptr, nullptr};
}
PyMODINIT_FUNC PyInit__factorpool_operators() { return PyModule_Create(&module); }
