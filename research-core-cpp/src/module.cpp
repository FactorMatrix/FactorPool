#include "analysis.hpp"
#include "research_input.hpp"
static PyMethodDef methods[] = {
    {"prepare_research", prepare_research, METH_VARARGS,
     "Own and validate matrices once; share stable factor order within one task."},
    {"ic_series", ic_series, METH_VARARGS,
     "Requested IC/Rank IC series without materializing unused value/group arrays."},
    {"analyze", analyze, METH_VARARGS,
     "Batch IC, Rank IC, ICIR, groups and T+1 -> T+2 open returns."},
    {"read_mmap", read_mmap, METH_VARARGS,
     "Read the existing FactorTrader daily Data Mod without writes."},
    {"series_stats", series_stats, METH_VARARGS,
     "Moments, ICIR and IID/HAC mean inference."},
    {"portfolio_report", portfolio_report, METH_VARARGS,
     "Non-overlapping matrix portfolio diagnostics."},
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
