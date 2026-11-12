#pragma once
#include <Python.h>
PyObject *analyze(PyObject *, PyObject *);

PyObject *series_stats(PyObject *, PyObject *);
PyObject *portfolio_report(PyObject *, PyObject *);
PyObject *ic_decay(PyObject *, PyObject *);
PyObject *compound_return(PyObject *, PyObject *);
PyObject *performance_stats(PyObject *, PyObject *);
