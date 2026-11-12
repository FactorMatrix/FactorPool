#pragma once
#include "python_values.hpp"
namespace fp {
std::vector<double> rank_values(const std::vector<double> &);
double correlation(const std::vector<double> &, const std::vector<double> &,
                   int);
PyObject *moments(const std::vector<double> &, int ddof, int hac_lag);
PyObject *performance(const std::vector<double> &, const std::vector<double> &,
                      int horizon, int annual_days,
                      const std::vector<double> *book_pnl = nullptr);
std::vector<double> numbers(PyObject *);
} // namespace fp
