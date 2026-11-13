// Ownership/conversion only. Numerical extensions are in operator_kernels;
// shared arithmetic/CS and Graph state are called by operator_program.
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "operator_program.hpp"
#include "parallel.hpp"
#include "python_values.hpp"
#include <algorithm>
#include <climits>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <set>

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
    if (PyUnicode_Check(object) || PyBytes_Check(object)) throw std::invalid_argument("Expected a sequence, not text");
    Owned sequence(PySequence_Fast(object, "Expected a sequence"));
    if (!sequence.value) throw std::invalid_argument("Expected a sequence");
    const auto count = PySequence_Fast_GET_SIZE(sequence.value);
    std::vector<std::string> result;
    result.reserve(static_cast<std::size_t>(count));
    for (Py_ssize_t i = 0; i < count; ++i)
        result.push_back(text(PySequence_Fast_GET_ITEM(sequence.value, i)));
    return result;
}
std::vector<std::size_t> indices(PyObject* object) {
    std::vector<std::size_t> result; each(object, [&](auto* item) { result.push_back(index(item)); }); return result;
}
struct Buffer {
    Py_buffer view{};
    bool acquired = false;
    explicit Buffer(PyObject* object) {
        if (PyObject_CheckBuffer(object)) {
            if (PyObject_GetBuffer(object, &view, PyBUF_FULL_RO) < 0)
                throw std::invalid_argument("Invalid numerical column buffer");
            acquired = true;
        }
    }
    ~Buffer() { if (acquired) PyBuffer_Release(&view); }
    bool vector() const {
        return acquired && view.ndim == 1 && view.shape && view.shape[0] >= 0
               && PyBuffer_IsContiguous(&view, 'C');
    }
};
std::vector<double> numbers(PyObject* object) {
    Buffer buffer(object);
    const auto& view = buffer.view;
    const std::string format = view.format ? view.format : "";
    if (buffer.vector() && view.itemsize == sizeof(double)
            && (format == "d" || format == "=d" || format == "@d")) {
        std::vector<double> result(static_cast<std::size_t>(view.shape[0]));
        const auto size = result.size() * sizeof(double);
        if (view.len < 0 || static_cast<std::size_t>(view.len) != size)
            throw std::invalid_argument("Numerical column shape differs from its buffer");
        if (size) std::memcpy(result.data(), view.buf, size);
        return result;
    }
    std::vector<double> result;
    each(object, [&](auto* value) { result.push_back(number(value)); });
    return result;
}
std::vector<std::uint8_t> presence(PyObject* object) {
    Buffer buffer(object);
    const auto& view = buffer.view;
    const std::string format = view.format ? view.format : "";
    std::vector<std::uint8_t> result;
    if (buffer.vector() && view.itemsize == 1 && (format == "?" || format == "B")) {
        const auto size = static_cast<std::size_t>(view.shape[0]);
        if (view.len < 0 || static_cast<std::size_t>(view.len) != size)
            throw std::invalid_argument("Presence column shape differs from its buffer");
        const auto* data = static_cast<const std::uint8_t*>(view.buf);
        result.resize(size);
        for (std::size_t i = 0; i < size; ++i) {
            if (format == "B" && data[i] > 1)
                throw std::invalid_argument("Pool present mask must contain 0/1");
            result[i] = data[i] != 0;
        }
    } else {
        each(object, [&](auto* item) {
            const auto value = number(item);
            if (!std::isfinite(value) || (value != 0 && value != 1))
                throw std::invalid_argument("Pool present mask must contain 0/1");
            result.push_back(value != 0);
        });
    }
    return result;
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
        node.dependency = index(required(item, "dependency"));
        if (auto* options = PyDict_GetItemString(item, "options")) {
            if (!PyDict_Check(options)) throw std::invalid_argument("Pool options must be a dictionary");
            PyObject *key, *value; Py_ssize_t position = 0;
            while (PyDict_Next(options, &position, &key, &value)) {
                const auto name = text(key);
                static const std::set<std::string> textual{"driver", "ignore", "range", "buckets", "clock"};
                static const std::set<std::string> boolean{"filter", "dense", "useStd", "skipBegin", "skipEnd", "skipBoth", "NaNGroup"};
                static const std::set<std::string> integral{"period", "k", "lag", "rettype", "ddof", "rate"};
                if (textual.contains(name)) {
                    if (!PyUnicode_Check(value)) throw std::invalid_argument("Core text option has wrong type: " + name);
                    node.strings.emplace(name, text(value));
                } else {
                    if (PyUnicode_Check(value) || (boolean.contains(name) && !PyBool_Check(value)) ||
                        (integral.contains(name) && !PyLong_CheckExact(value)))
                        throw std::invalid_argument("Core numeric option has wrong type: " + name);
                    const auto numeric = number(value);
                    if (!std::isfinite(numeric)) throw std::invalid_argument("Core option must be finite");
                    node.numbers.emplace(name, numeric);
                }
            }
        }
        result.nodes.push_back(std::move(node));
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
            fields.push_back(text(key)); values.push_back(numbers(value));
        }
        std::vector<pool::graph::Column> input;
        for (std::size_t i = 0; i < fields.size(); ++i) input.push_back({fields[i], values[i]});
        auto mask = presence(present);
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
PyObject* snapshot_columns(PyObject*, PyObject* args) {
    try {
        PyObject* capsule;
        if (!PyArg_ParseTuple(args, "O:snapshot_columns", &capsule)) return nullptr;
        auto& program = handle(capsule);
        Owned result(PyDict_New());
        if (!result.value || !put(result.value, "current", bytes(program.current())) ||
                !put(result.value, "valid", bytes(program.valid())) ||
                !put(result.value, "ready", bytes(program.ready()))) return nullptr;
        return result.release();
    } catch (const std::exception& error) { return failure(error); }
}
std::shared_ptr<pool::graph::Graph> shared_graph(PyObject* capsule) {
    constexpr auto name = "factortrader.indicator.graph.v1";
    auto* pointer = static_cast<std::shared_ptr<pool::graph::Graph>*>(
        PyCapsule_GetPointer(capsule, name));
    if (!pointer) throw std::invalid_argument("Invalid pinned shared Graph handle");
    return *pointer;
}
PyObject* shared_set_symbols(PyObject*, PyObject* args) {
    try {
        PyObject *capsule, *symbols;
        if (!PyArg_ParseTuple(args, "OO:shared_set_symbols", &capsule, &symbols)) return nullptr;
        auto graph = shared_graph(capsule);
        auto axis = strings(symbols);
        PyThreadState* thread = PyEval_SaveThread();
        try { graph->set_symbols(std::move(axis)); }
        catch (...) { PyEval_RestoreThread(thread); throw; }
        PyEval_RestoreThread(thread); Py_RETURN_NONE;
    } catch (const std::exception& error) { return failure(error); }
}
PyObject* shared_update(PyObject*, PyObject* args) {
    try {
        PyObject *capsule, *columns, *present; long long time; int fresh = 0;
        if (!PyArg_ParseTuple(args, "OLOO|p:shared_update", &capsule, &time, &columns, &present, &fresh)) return nullptr;
        auto graph = shared_graph(capsule);
        if (!PyDict_Check(columns)) throw std::invalid_argument("Shared columns must be a dictionary");
        std::vector<std::string> fields; std::vector<std::vector<double>> values;
        Py_ssize_t position = 0; PyObject *key, *value;
        while (PyDict_Next(columns, &position, &key, &value)) {
            fields.push_back(text(key)); values.push_back(numbers(value));
        }
        std::vector<pool::graph::Column> input;
        for (std::size_t i = 0; i < fields.size(); ++i) input.push_back({fields[i], values[i]});
        auto mask = presence(present);
        // This is only a buffer bridge to the original pinned Graph. Numeric
        // inputs are owned before its existing update runs without the GIL.
        PyThreadState* thread = PyEval_SaveThread();
        try {
            // A stateless signal previously owned a brand-new Graph. Its
            // missing rows started unready; retain that exact publication
            // policy while reusing the compiled numerical plan.
            if (fresh) graph->invalidate_missing(mask);
            graph->update(time, input, mask);
        }
        catch (...) { PyEval_RestoreThread(thread); throw; }
        PyEval_RestoreThread(thread); Py_RETURN_NONE;
    } catch (const std::exception& error) { return failure(error); }
}
PyObject* shared_snapshot_columns(PyObject*, PyObject* args) {
    try {
        PyObject* capsule;
        if (!PyArg_ParseTuple(args, "O:shared_snapshot_columns", &capsule)) return nullptr;
        const auto& state = shared_graph(capsule)->current_snapshot();
        Owned result(PyDict_New());
        if (!result.value || !put(result.value, "current", bytes(state.current)) ||
                !put(result.value, "valid", bytes(state.valid)) ||
                !put(result.value, "ready", bytes(state.ready))) return nullptr;
        return result.release();
    } catch (const std::exception& error) { return failure(error); }
}

PyObject* evaluate_matrix(PyObject*, PyObject* args) {
    try {
        PyObject *capsule, *symbol_axis, *field_objects, *present_object,
                 *requested_object, *axes_object, *times_object;
        int chronological = 0;
        if (!PyArg_ParseTuple(args, "OOOOOOO|p:evaluate_matrix", &capsule, &symbol_axis,
                              &field_objects, &present_object, &requested_object,
                              &axes_object, &times_object, &chronological)) return nullptr;
        const bool pool_mode = PyCapsule_IsValid(capsule, tag);
        pool::Spec pool_spec;
        pool::graph::Spec scalar_spec;
        pool::Program* pool_target = nullptr;
        std::shared_ptr<pool::graph::Graph> graph_target;
        if (pool_mode) {
            pool_target = &handle(capsule);
            const auto& original = *pool_target;
            if (!chronological && !original.independent_frames()) Py_RETURN_NONE;
            pool_spec = original.spec();
        } else {
            graph_target = shared_graph(capsule);
            const auto& original = graph_target;
            if (original->has_batch_program() || original->has_temporal_ports() ||
                (!chronological && std::any_of(original->spec().nodes.begin(), original->spec().nodes.end(), [](const auto& node) {
                    return node.op >= pool::graph::Op::Delay;
                }))) Py_RETURN_NONE;
            scalar_spec = original->spec();
        }
        const auto symbols = strings(symbol_axis);
        if (!std::is_sorted(symbols.begin(), symbols.end()) ||
            std::adjacent_find(symbols.begin(), symbols.end()) != symbols.end())
            throw std::invalid_argument("Matrix symbols must be sorted and unique");
        const auto requested = indices(requested_object);
        if (std::adjacent_find(requested.begin(), requested.end(), std::greater_equal<>()) != requested.end())
            throw std::invalid_argument("Matrix indices must be strictly increasing");
        if (!requested.empty() && requested.back() == std::numeric_limits<std::size_t>::max())
            throw std::invalid_argument("Matrix index is outside the quote history");
        const auto steps = chronological && !requested.empty() ? requested.back() + 1 : requested.size();
        std::vector<std::int64_t> times;
        each(times_object, [&](auto* item) {
            if (!PyLong_CheckExact(item)) throw std::invalid_argument("Matrix times must be integers");
            const auto time = PyLong_AsLongLong(item);
            if (PyErr_Occurred()) throw std::invalid_argument("Matrix time is out of range");
            times.push_back(time);
        });
        if (times.size() != steps || !std::is_sorted(times.begin(), times.end()))
            throw std::invalid_argument("Matrix times must follow the requested chronological axis");
        std::size_t source_rows = 0;
        std::vector<std::uint8_t> present;
        {
            Buffer buffer(present_object);
            const auto& view = buffer.view;
            const std::string format = view.format ? view.format : "";
            if (!buffer.acquired || view.ndim != 2 || !view.shape || view.shape[0] < 0 ||
                view.shape[1] != static_cast<Py_ssize_t>(symbols.size()) || view.itemsize != 1 ||
                (format != "?" && format != "B") || !PyBuffer_IsContiguous(&view, 'C'))
                throw std::invalid_argument("Matrix presence must be a contiguous boolean matrix on the quote axis");
            source_rows = static_cast<std::size_t>(view.shape[0]);
            if (symbols.size() && source_rows > present.max_size() / symbols.size())
                throw std::invalid_argument("Matrix presence is too large");
            present.resize(source_rows * symbols.size());
            if (view.len < 0 || static_cast<std::size_t>(view.len) != present.size())
                throw std::invalid_argument("Matrix presence shape differs from its buffer");
            if (!present.empty()) std::memcpy(present.data(), view.buf, present.size());
            if (std::any_of(present.begin(), present.end(), [](auto value) { return value > 1; }))
                throw std::invalid_argument("Matrix presence must contain only boolean values");
        }
        if (!requested.empty() && requested.back() >= source_rows)
            throw std::invalid_argument("Matrix index is outside the quote history");
        if (!PyDict_Check(field_objects)) throw std::invalid_argument("Matrix fields must be a dictionary");
        std::vector<std::string> fields;
        std::vector<fp::Matrix> data;
        Py_ssize_t position = 0; PyObject *key, *value;
        while (PyDict_Next(field_objects, &position, &key, &value)) {
            fields.push_back(text(key)); data.push_back(fp::matrix(value));
            if (data.back().size() != source_rows || data.back().columns() != symbols.size())
                throw std::invalid_argument("Matrix fields differ from the quote history axes");
        }
        std::vector<std::vector<std::size_t>> axes;
        each(axes_object, [&](auto* object) {
            Buffer buffer(object);
            const auto& view = buffer.view;
            std::string format = view.format ? view.format : "";
            if (!format.empty() && (format.front() == '@' || format.front() == '=')) format.erase(0, 1);
            const bool native_signed = format == "n" ||
                (format == "l" && sizeof(long) == sizeof(std::intptr_t)) ||
                (format == "q" && sizeof(long long) == sizeof(std::intptr_t)) ||
                (format == "i" && sizeof(int) == sizeof(std::intptr_t));
            if (!buffer.vector() || view.itemsize != sizeof(std::intptr_t) || !native_signed)
                throw std::invalid_argument("Matrix active columns must be contiguous native intp vectors");
            axes.emplace_back(); auto& axis = axes.back();
            const auto length = static_cast<std::size_t>(view.shape[0]);
            if (view.len < 0 || static_cast<std::size_t>(view.len) != length * sizeof(std::intptr_t))
                throw std::invalid_argument("Matrix active column shape differs from its buffer");
            axis.reserve(length);
            const auto* bytes = static_cast<const char*>(view.buf);
            for (std::size_t slot = 0; slot < length; ++slot) {
                std::intptr_t column;
                std::memcpy(&column, bytes + slot * sizeof(column), sizeof(column));
                if (column < 0 || static_cast<std::size_t>(column) >= symbols.size() ||
                    (!axis.empty() && static_cast<std::size_t>(column) <= axis.back()))
                    throw std::invalid_argument("Matrix active columns must be ordered unique quote positions");
                axis.push_back(static_cast<std::size_t>(column));
            }
        });
        if (axes.size() != steps) throw std::invalid_argument("Matrix active axes differ from evaluated dates");
        if (symbols.size() && requested.size() > static_cast<std::size_t>(PY_SSIZE_T_MAX) / sizeof(double) / symbols.size())
            throw std::invalid_argument("Matrix publication is too large");
        const auto cells = requested.size() * symbols.size();
        Owned output(PyDict_New()), current(PyBytes_FromStringAndSize(nullptr, cells * sizeof(double))),
              valid(PyBytes_FromStringAndSize(nullptr, cells)), ready(PyBytes_FromStringAndSize(nullptr, cells)),
              reason(PyBytes_FromStringAndSize(nullptr, cells));
        if (!output.value || !current.value || !valid.value || !ready.value || !reason.value) return nullptr;
        auto *out_current = PyBytes_AS_STRING(current.value), *out_valid = PyBytes_AS_STRING(valid.value),
             *out_ready = PyBytes_AS_STRING(ready.value), *out_reason = PyBytes_AS_STRING(reason.value);
        const auto missing = fp::missing;
        for (std::size_t cell = 0; cell < cells; ++cell)
            std::memcpy(out_current + cell * sizeof(double), &missing, sizeof(double));
        std::memset(out_valid, 0, cells); std::memset(out_ready, 0, cells); std::memset(out_reason, 6, cells);
        // Every Python buffer/axis is owned above before the GIL is released.
        // Each worker creates the original numerical plan on its own thread.
        // Temporal frames use one plan on the caller thread, in time order.
        const auto workers = std::min(steps, chronological ? std::size_t{1} : fp::compute_threads());
        PyThreadState* thread = PyEval_SaveThread();
        try {
            fp::parallel_for(workers, workers ? cells / workers : 0, [&](std::size_t worker) {
                std::unique_ptr<pool::Program> program;
                std::unique_ptr<pool::graph::Graph> graph;
                if (pool_mode) program = std::make_unique<pool::Program>(pool_spec, std::vector<std::string>{});
                else graph = std::make_unique<pool::graph::Graph>(scalar_spec, std::vector<std::string>{});
                const auto first = worker * steps / workers, last = (worker + 1) * steps / workers;
                std::size_t emitted = 0;
                for (auto step = first; step < last; ++step) {
                    const auto source = chronological ? step : requested[step]; const auto& axis = axes[step];
                    std::vector<std::string> selected; selected.reserve(axis.size());
                    for (auto column : axis) selected.push_back(symbols[column]);
                    if (program) program->set_symbols(std::move(selected)); else graph->set_symbols(std::move(selected));
                    std::vector<std::uint8_t> observed(axis.size()), source_missing(axis.size(), 0);
                    std::vector<std::vector<double>> columns(fields.size(), std::vector<double>(axis.size()));
                    for (std::size_t slot = 0; slot < axis.size(); ++slot) {
                        observed[slot] = present[source * symbols.size() + axis[slot]];
                        for (std::size_t field = 0; field < fields.size(); ++field) {
                            const auto raw = data[field][source][axis[slot]];
                            source_missing[slot] |= !std::isfinite(raw);
                            columns[field][slot] = std::isfinite(raw) ? raw : missing;
                        }
                    }
                    std::vector<pool::graph::Column> input;
                    for (std::size_t field = 0; field < fields.size(); ++field) input.push_back({fields[field], columns[field]});
                    if (program) program->update(times[step], input, observed);
                    else {
                        auto mask = observed;
                        for (std::size_t slot = 0; slot < axis.size(); ++slot) mask[slot] &= !source_missing[slot];
                        if (!chronological) graph->invalidate_missing(mask);
                        graph->update(times[step], input, mask);
                    }
                    if (chronological && (emitted >= requested.size() || source != requested[emitted])) continue;
                    const auto row = chronological ? emitted++ : step;
                    const auto* state = graph ? &graph->current_snapshot() : nullptr;
                    const auto& values = program ? program->current() : state->current;
                    const auto& validity = program ? program->valid() : state->valid;
                    const auto& readiness = program ? program->ready() : state->ready;
                    for (std::size_t slot = 0; slot < axis.size(); ++slot) {
                        const auto target = row * symbols.size() + axis[slot];
                        const bool usable = validity[slot] && readiness[slot] && std::isfinite(values[slot]) && observed[slot];
                        const double numeric = usable ? values[slot] : missing;
                        const auto code = usable ? 0 : !observed[slot] ? 1 : source_missing[slot] ? 2 :
                                          validity[slot] && !readiness[slot] ? 3 : 4;
                        std::memcpy(out_current + target * sizeof(double), &numeric, sizeof(double));
                        out_valid[target] = usable; out_ready[target] = readiness[slot]; out_reason[target] = code;
                    }
                }
                // Chronological work runs on this caller thread. Retain its
                // exact final state for subsequent ordinary daily updates.
                if (chronological) {
                    if (program) *pool_target = std::move(*program);
                    else *graph_target = std::move(*graph);
                }
            });
        } catch (...) { PyEval_RestoreThread(thread); throw; }
        PyEval_RestoreThread(thread);
        if (!put(output.value, "current", current.release()) || !put(output.value, "valid", valid.release()) ||
            !put(output.value, "ready", ready.release()) || !put(output.value, "reason", reason.release())) return nullptr;
        return output.release();
    } catch (const std::exception& error) { return failure(error); }
}
PyMethodDef methods[] = {
    {"evaluate_matrix", evaluate_matrix, METH_VARARGS, "Evaluate actual per-date axes with independent dates or one chronological temporal plan."},
    {"create", create, METH_VARARGS, "Create a Pool native expression plan using pinned shared kernels."},
    {"set_symbols", set_symbols, METH_VARARGS, "Set the active sorted axis; removed temporal state is released."},
    {"update", update, METH_VARARGS, "Evaluate one complete daily frame with independent column masks."},
    {"snapshot", snapshot, METH_VARARGS, "Copy the current numerical publication into owned buffers."},
    {"snapshot_columns", snapshot_columns, METH_VARARGS, "Copy numerical columns without rebuilding the unchanged symbol axis."},
    {"shared_set_symbols", shared_set_symbols, METH_VARARGS, "Convert an active axis and call the original pinned Graph reconciliation."},
    {"shared_update", shared_update, METH_VARARGS, "Copy numerical buffers into the original pinned shared Graph update."},
    {"shared_snapshot_columns", shared_snapshot_columns, METH_VARARGS, "Copy original pinned shared Graph numerical output columns."},
    {nullptr, nullptr, 0, nullptr}};
PyModuleDef module = {PyModuleDef_HEAD_INIT, "_factorpool_operators", nullptr, -1, methods, nullptr, nullptr, nullptr, nullptr};
}
PyMODINIT_FUNC PyInit__factorpool_operators() { return PyModule_Create(&module); }
