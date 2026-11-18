#pragma once
#include "python_values.hpp"
namespace fp {
double correlation(const std::vector<double> &, const std::vector<double> &,
                   int);
PyObject *moments(const std::vector<double> &, int ddof, int hac_lag);
PyObject *performance(const std::vector<double> &, const std::vector<double> &,
                      int horizon, int annual_days,
                      const std::vector<double> *book_pnl = nullptr,
                      bool include_book = true);
PyObject *brain_summary(const std::vector<double>& book_pnl,
                       const std::vector<double>& turnover, int annual_days);
std::vector<double> numbers(PyObject *);
} // namespace fp
