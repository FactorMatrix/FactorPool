#pragma once
#include <Python.h>

// Fixed research kernel. Python factors supply values through ordinary buffers;
// no source compilation occurs when a factor or its configuration changes.
PyObject* alpha_targets(PyObject*, PyObject*);
