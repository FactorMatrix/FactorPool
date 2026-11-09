#include "analysis.hpp"
static PyMethodDef methods[] = {
    {"analyze", analyze, METH_VARARGS,
     "Batch IC, Rank IC, ICIR, groups and T+1 -> T+2 open returns."},
    {"read_mmap", read_mmap, METH_VARARGS,
     "Read the existing FactorTrader daily Data Mod without writes."},
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
