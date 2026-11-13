#include "analysis.hpp"
#include "research_input.hpp"
#include "parallel.hpp"
static PyObject* compute_threads(PyObject*, PyObject*) {
  try { return PyLong_FromSize_t(fp::compute_threads()); }
  catch (const std::exception& error) {
    PyErr_SetString(PyExc_ValueError, error.what());
    return nullptr;
  }
}
static PyMethodDef methods[] = {
    {"compute_threads", compute_threads, METH_NOARGS,
     "Effective numeric thread limit bounded by configured threads, CPU affinity and cgroup quota."},
    {"prepare_research", prepare_research, METH_VARARGS,
     "Own factor, daily-return, membership and optional target-alpha matrices; share sorted factors and compounded horizons."},
    {"ic_series", ic_series, METH_VARARGS,
     "Requested IC/Rank IC series without materializing unused value/group arrays."},
    {"analyze", analyze, METH_VARARGS,
     "Batch IC, Rank IC, ICIR and groups against compounded future daily returns."},
    {"series_stats", series_stats, METH_VARARGS,
     "Moments, ICIR and IID/HAC mean inference."},
    {"portfolio_report", portfolio_report, METH_VARARGS,
     "Non-overlapping matrix portfolios with daily holding returns; optional seventh trace_signal_indices list selects bounded Alpha PnL traces."},
    {"performance_stats", performance_stats, METH_VARARGS,
     "Daily return and fixed-book performance using the same portfolio statistics."},
    {"compound_return", compound_return, METH_VARARGS,
     "Compound complete non-overlapping interval returns."},
    {"ic_decay", ic_decay, METH_VARARGS,
     "Fixed-signal forward prediction decay."},
    {nullptr, nullptr, 0, nullptr}};
static PyModuleDef module = {PyModuleDef_HEAD_INIT,
                             "_factorpool_native",
                             nullptr,
                             -1,
                             methods,
                             nullptr,
                             nullptr,
                             nullptr,
                             nullptr};
PyMODINIT_FUNC PyInit__factorpool_native() { return PyModule_Create(&module); }
