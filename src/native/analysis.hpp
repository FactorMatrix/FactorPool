#pragma once
#include <Python.h>
PyObject *analyze(PyObject *, PyObject *);

PyObject *series_stats(PyObject *, PyObject *);
// Optional trace indices request bounded, fixed-book Alpha contributions.
PyObject *portfolio_report(PyObject *, PyObject *);
PyObject *ic_decay(PyObject *, PyObject *);
PyObject *compound_return(PyObject *, PyObject *);
PyObject *performance_stats(PyObject *, PyObject *);
PyObject *brain_summary(PyObject *, PyObject *);
PyObject *series_mean(PyObject *, PyObject *);
PyObject *report_rolling_mean(PyObject *, PyObject *);
