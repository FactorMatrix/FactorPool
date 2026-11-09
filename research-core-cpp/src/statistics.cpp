#include "statistics.hpp"
#include "analysis.hpp"
#include <algorithm>
#include <numeric>
namespace fp {
std::vector<double> numbers(PyObject *obj) {
  Owned seq{PySequence_Fast(obj, "Expected sequence")};
  if (!seq.p)
    throw std::invalid_argument("Expected sequence");
  std::vector<double> values;
  for (Py_ssize_t i = 0; i < PySequence_Fast_GET_SIZE(seq.p); ++i) {
    auto *x = PySequence_Fast_GET_ITEM(seq.p, i);
    values.push_back(x == Py_None ? missing : PyFloat_AsDouble(x));
    if (PyErr_Occurred())
      throw std::invalid_argument("Expected number/null");
  }
  return values;
}
std::vector<double> rank_values(const std::vector<double> &v) {
  std::vector<std::size_t> order(v.size());
  std::iota(order.begin(), order.end(), 0);
  // Equal values all receive the same average rank; their internal order is unobserved.
  std::sort(order.begin(), order.end(),
                   [&](auto a, auto b) { return v[a] < v[b]; });
  std::vector<double> out(v.size());
  for (std::size_t i = 0; i < order.size();) {
    auto j = i + 1;
    while (j < order.size() && v[order[i]] == v[order[j]])
      ++j;
    for (auto k = i; k < j; ++k)
      out[order[k]] = (i + j - 1) / 2.0 + 1;
    i = j;
  }
  return out;
}
double correlation(const std::vector<double> &a, const std::vector<double> &b,
                   int minimum) {
  if (a.size() < static_cast<std::size_t>(minimum))
    return missing;
  long double x = 0, y = 0, xx = 0, yy = 0, xy = 0;
  for (std::size_t i = 0; i < a.size(); ++i) {
    long double dx = a[i] - x, dy = b[i] - y;
    x += dx / (i + 1);
    y += dy / (i + 1);
    xx += dx * (a[i] - x);
    yy += dy * (b[i] - y);
    xy += dx * (b[i] - y);
  }
  return xx > 0 && yy > 0 ? std::clamp(double(xy / std::sqrt(xx * yy)), -1., 1.)
                          : missing;
}
PyObject *moments(const std::vector<double> &values, int ddof, int lag) {
  if (ddof < 0 || ddof > 1 || lag < 0 || lag > 251)
    throw std::invalid_argument("Invalid moments options");
  std::vector<double> valid;
  for (auto v : values)
    if (std::isfinite(v))
      valid.push_back(v);
  const auto n = valid.size();
  long double mean = 0;
  for (std::size_t i = 0; i < n; ++i)
    mean += (valid[i] - mean) / (i + 1);
  long double m2 = 0, m3 = 0, m4 = 0;
  std::size_t positive = 0;
  for (auto v : valid) {
    long double d = v - mean;
    m2 += d * d;
    m3 += d * d * d;
    m4 += d * d * d * d;
    positive += v > 0;
  }
  double sd = n > static_cast<std::size_t>(ddof)
                  ? std::sqrt(double(m2 / (n - ddof)))
                  : missing;
  double sample_sd = n > 1 ? std::sqrt(double(m2 / (n - 1))) : missing;
  double skew = n > 2 && m2 > 0
                    ? double(std::sqrt(static_cast<long double>(n) * (n - 1)) /
                             (n - 2) * (m3 / n) / std::pow(m2 / n, 1.5L))
                    : missing;
  double kurt =
      n > 3 && m2 > 0
          ? double((static_cast<long double>(n) - 1) / ((n - 2) * (n - 3)) *
                   ((n + 1) * (n * m4 / (m2 * m2) - 3) + 6))
          : missing;
  double hac_t = missing;
  // Keep actual spacing. Do not compress missing dates into adjacent samples.
  if (n > 1 && n == values.size() && m2 > 0 &&
      n > static_cast<std::size_t>(lag + 1)) {
    long double variance = m2 / n;
    for (int k = 1; k <= lag; ++k) {
      long double covariance = 0;
      for (std::size_t i = k; i < n; ++i)
        covariance += (values[i] - mean) * (values[i - k] - mean);
      variance +=
          2 * (1 - static_cast<long double>(k) / (lag + 1)) * covariance / n;
    }
    if (variance > 0)
      hac_t = double(mean / std::sqrt(variance / n));
  }
  Owned result{PyDict_New()};
  put(result.p, "n", PyLong_FromSize_t(n));
  put(result.p, "missing", PyLong_FromSize_t(values.size() - n));
  put(result.p, "mean", number(n ? double(mean) : missing));
  put(result.p, "std", number(sd));
  put(result.p, "ddof", PyLong_FromLong(ddof));
  put(result.p, "ir", number(sd > 0 ? double(mean) / sd : missing));
  put(result.p, "positive_ratio", number(n ? double(positive) / n : missing));
  put(result.p, "skewness", number(skew));
  put(result.p, "excess_kurtosis", number(kurt));
  put(result.p, "t_iid",
      number(sample_sd > 0 ? double(mean) / sample_sd * std::sqrt(double(n))
                           : missing));
  put(result.p, "t_hac", number(hac_t));
  put(result.p, "hac_lag", PyLong_FromLong(lag));
  return Py_NewRef(result.p);
}
PyObject *performance(const std::vector<double> &pnl,
                      const std::vector<double> &turnover, int h,
                      int annual_days) {
  if (pnl.size() != turnover.size() || h < 1 || annual_days < 1)
    throw std::invalid_argument("Performance axes/options differ");
  Owned result{moments(pnl, 1, 0)};
  const auto n = PyLong_AsSize_t(PyDict_GetItemString(result.p, "n"));
  const auto mean = PyDict_GetItemString(result.p, "mean"),
             sd = PyDict_GetItemString(result.p, "std");
  double mu = mean == Py_None ? missing : PyFloat_AsDouble(mean),
         sigma = sd == Py_None ? missing : PyFloat_AsDouble(sd);
  const double annual = double(annual_days) / h;
  double ann = mu * annual, vol = sigma * std::sqrt(annual),
         sharpe = vol > 0 ? ann / vol : missing;
  std::vector<double> nav, dd, book_pnl, book_drawdown;
  long double wealth = 1, peak = 1, cumulative = 0, pnl_peak = 0;
  bool broken = false, book_broken = false;
  double maxdd = 0, brain_dd = 0;
  long double wins = 0, losses = 0, traded = 0, total_pnl = 0;
  std::size_t nw = 0, nl = 0;
  bool all_trades = true;
  for (std::size_t i = 0; i < pnl.size(); ++i) {
    auto r = pnl[i];
    if (std::isfinite(r)) {
      if (r > 0) {
        wins += r;
        ++nw;
      }
      if (r < 0) {
        losses -= r;
        ++nl;
      }
      total_pnl += r;
    }
    if (!std::isfinite(turnover[i]))
      all_trades = false;
    else
      traded += turnover[i];
    if (!std::isfinite(r))
      book_broken = true;
    if (!book_broken) {
      cumulative += r;
      pnl_peak = std::max(pnl_peak, cumulative);
      brain_dd = std::max(brain_dd, double((pnl_peak - cumulative) / .5L));
      book_pnl.push_back(double(cumulative));
      book_drawdown.push_back(double((cumulative - pnl_peak) / .5L));
    } else {
      book_pnl.push_back(missing);
      book_drawdown.push_back(missing);
    }
    if (!std::isfinite(r) || 1 + r <= 0)
      broken = true;
    if (!broken) {
      wealth *= 1 + r;
      peak = std::max(peak, wealth);
      const double drawdown = double(wealth / peak - 1);
      maxdd = std::min(maxdd, drawdown);
      nav.push_back(double(wealth));
      dd.push_back(drawdown);
    } else {
      nav.push_back(missing);
      dd.push_back(missing);
    }
  }
  const double daily_turnover = all_trades && !turnover.empty()
                                    ? double(traded) / (pnl.size() * h)
                                    : missing;
  const double returns = 2 * ann; // Defined half-book convention, not CAGR.
  put(result.p, "annual_return", number(ann));
  put(result.p, "annual_volatility", number(vol));
  put(result.p, "sharpe", number(sharpe));
  put(result.p, "cagr",
      number(!broken && n ? std::expm1(double(std::log(wealth) * annual / n))
                          : missing));
  put(result.p, "max_drawdown", number(!broken && n ? maxdd : missing));
  put(result.p, "win_rate", number(n ? double(nw) / n : missing));
  put(result.p, "payoff_ratio",
      number(nw && nl && losses > 0 ? double((wins / nw) / (losses / nl))
                                    : missing));
  Owned brain{PyDict_New()};
  put(brain.p, "sharpe", number(sharpe));
  put(brain.p, "returns", number(returns));
  put(brain.p, "turnover", number(daily_turnover));
  put(brain.p, "drawdown", number(!book_broken && n ? brain_dd : missing));
  put(brain.p, "fitness",
      number(std::isfinite(sharpe) && std::isfinite(daily_turnover)
                 ? sharpe * std::sqrt(std::abs(returns) /
                                      std::max(daily_turnover, .125))
                 : missing));
  put(brain.p, "margin",
      number(all_trades && n == pnl.size() && traded > 0
                 ? double(total_pnl / traded)
                 : missing));
  put(result.p, "brain_style", Py_NewRef(brain.p));
  put(result.p, "nav", list(nav));
  put(result.p, "drawdown_series", list(dd));
  put(result.p, "constant_book_pnl", list(book_pnl));
  put(result.p, "constant_book_drawdown", list(book_drawdown));
  return Py_NewRef(result.p);
}
} // namespace fp
PyObject *series_stats(PyObject *, PyObject *args) {
  try {
    PyObject *values;
    int ddof = 1, lag = 0;
    if (!PyArg_ParseTuple(args, "O|ii", &values, &ddof, &lag))
      return nullptr;
    return fp::moments(fp::numbers(values), ddof, lag);
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}

PyObject *compound_return(PyObject *, PyObject *args) {
  try {
    PyObject *source;
    if (!PyArg_ParseTuple(args, "O", &source))
      return nullptr;
    auto values = fp::numbers(source);
    long double nav = 1;
    if (values.empty())
      return fp::number(fp::missing);
    for (auto x : values) {
      if (!std::isfinite(x))
        return fp::number(fp::missing);
      nav *= 1 + static_cast<long double>(x);
    }
    return fp::number(double(nav - 1));
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
