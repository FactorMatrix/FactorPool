#pragma once
#include <Python.h>

// Small numerical transforms used by row fixtures and report presentation.
// Arithmetic remains in the prebuilt research module; factors stay Python.
PyObject* price_returns(PyObject*, PyObject*);
PyObject* prefix_ic_sum(PyObject*, PyObject*);
