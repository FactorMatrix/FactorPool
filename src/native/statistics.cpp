#include "statistics.hpp"
#include "analysis.hpp"
#include <algorithm>
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
namespace {
struct BasicMoments {
  std::size_t n = 0;
  double mean = missing, sd = missing;
};
BasicMoments basic_moments(const std::vector<double>& values) {
  // Match moments()' finite sample order, long-double mean recurrence and
  // second-pass variance exactly; performance has no use for higher moments.
  BasicMoments result;
  long double mean = 0;
  for (auto value : values)
    if (std::isfinite(value)) mean += (value - mean) / ++result.n;
  long double m2 = 0;
  for (auto value : values)
    if (std::isfinite(value)) {
      const long double difference = value - mean;
      m2 += difference * difference;
    }
  if (result.n) result.mean = double(mean);
  if (result.n > 1) result.sd = std::sqrt(double(m2 / (result.n - 1)));
  return result;
}
struct BookPerformance {
  BasicMoments sample;
  long double traded = 0, total_pnl = 0;
  double max_drawdown = 0;
  bool all_trades = true, broken = false;
  std::vector<double> cumulative;
};
BookPerformance book_performance(const std::vector<double>& book,
                                 const std::vector<double>& turnover,
                                 bool timeline,
                                 const BasicMoments* sample = nullptr) {
  BookPerformance result;
  result.sample = sample ? *sample : basic_moments(book);
  if (timeline) result.cumulative.reserve(book.size());
  long double cumulative = 0, peak = 0;
  for (std::size_t i = 0; i < book.size(); ++i) {
    if (!std::isfinite(turnover[i])) result.all_trades = false;
    else result.traded += turnover[i];
    if (!std::isfinite(book[i])) result.broken = true;
    else result.total_pnl += book[i];
    if (!result.broken) {
      cumulative += book[i];
      peak = std::max(peak, cumulative);
      result.max_drawdown = std::max(result.max_drawdown, double((peak - cumulative) / .5L));
    }
    if (timeline) result.cumulative.push_back(result.broken ? missing : double(cumulative));
  }
  return result;
}
PyObject* brain_statistics(const BookPerformance& book, std::size_t size,
                           int horizon, int annual_days) {
  const double annual = double(annual_days) / horizon;
  const double sharpe = book.sample.sd > 0
      ? book.sample.mean / book.sample.sd * std::sqrt(annual) : missing;
  const double turnover = book.all_trades && size
      ? double(book.traded) / (size * horizon) : missing;
  const double returns = 2 * book.sample.mean * annual;
  Owned result{PyDict_New()};
  put(result.p, "sharpe", number(sharpe));
  put(result.p, "returns", number(returns));
  put(result.p, "turnover", number(turnover));
  put(result.p, "drawdown", number(!book.broken && book.sample.n ? book.max_drawdown : missing));
  put(result.p, "fitness",
      number(std::isfinite(sharpe) && std::isfinite(turnover)
          ? sharpe * std::sqrt(std::abs(returns) / std::max(turnover, .125)) : missing));
  put(result.p, "margin", number(book.all_trades && book.sample.n == size && book.traded > 0
      ? double(book.total_pnl / book.traded) : missing));
  return Py_NewRef(result.p);
}
void performance_axes(const std::vector<double>& pnl,
                       const std::vector<double>& turnover,
                       const std::vector<double>& book, int h, int annual_days) {
  if (pnl.size() != turnover.size() || pnl.size() != book.size() || h < 1 || annual_days < 1)
    throw std::invalid_argument("Performance axes/options differ");
}
} // namespace
PyObject *performance(const std::vector<double> &pnl,
                      const std::vector<double> &turnover, int h,
                      int annual_days, const std::vector<double> *book_source,
                      bool include_book) {
  const auto &book = book_source ? *book_source : pnl;
  performance_axes(pnl, turnover, book, h, annual_days);
  const auto sample = basic_moments(pnl);
  BookPerformance book_stats;
  if (include_book)
    book_stats = book_performance(book, turnover, true, &book == &pnl ? &sample : nullptr);
  const double annual = double(annual_days) / h;
  const double ann = sample.mean * annual, vol = sample.sd * std::sqrt(annual),
               sharpe = vol > 0 ? ann / vol : missing;
  std::vector<double> nav, dd;
  nav.reserve(pnl.size()); dd.reserve(pnl.size());
  long double wealth = 1, peak = 1, wins = 0, losses = 0;
  bool broken = false;
  double maxdd = 0;
  std::size_t nw = 0, nl = 0;
  for (auto r : pnl) {
    if (std::isfinite(r)) {
      if (r > 0) { wins += r; ++nw; }
      if (r < 0) { losses -= r; ++nl; }
    }
    if (!std::isfinite(r) || 1 + r < 0) broken = true;
    if (!broken) {
      wealth *= 1 + r;
      peak = std::max(peak, wealth);
      const double drawdown = double(wealth / peak - 1);
      maxdd = std::min(maxdd, drawdown);
      nav.push_back(double(wealth)); dd.push_back(drawdown);
    } else {
      nav.push_back(missing); dd.push_back(missing);
    }
  }
  Owned result{PyDict_New()};
  put(result.p, "n", PyLong_FromSize_t(sample.n));
  put(result.p, "annual_return", number(ann));
  put(result.p, "annual_volatility", number(vol));
  put(result.p, "sharpe", number(sharpe));
  put(result.p, "cagr", number(!broken && sample.n
      ? std::expm1(double(std::log(wealth) * annual / sample.n)) : missing));
  put(result.p, "max_drawdown", number(!broken && sample.n ? maxdd : missing));
  put(result.p, "win_rate", number(sample.n ? double(nw) / sample.n : missing));
  put(result.p, "payoff_ratio", number(nw && nl && losses > 0
      ? double((wins / nw) / (losses / nl)) : missing));
  if (include_book)
    put(result.p, "brain_style", brain_statistics(book_stats, pnl.size(), h, annual_days));
  put(result.p, "nav", list(nav));
  put(result.p, "drawdown_series", list(dd));
  if (include_book) put(result.p, "constant_book_pnl", list(book_stats.cumulative));
  return Py_NewRef(result.p);
}
PyObject *brain_summary(const std::vector<double>& book,
                        const std::vector<double>& turnover, int annual_days) {
  if (book.size() != turnover.size())
    throw std::invalid_argument("Performance axes/options differ");
  return brain_statistics(book_performance(book, turnover, false), book.size(), 1, annual_days);
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

PyObject *performance_stats(PyObject *, PyObject *args) {
  try {
    PyObject *returns, *turnover, *book;
    int annual_days;
    if (!PyArg_ParseTuple(args, "OOOi", &returns, &turnover, &book, &annual_days))
      return nullptr;
    if (annual_days < 1 || annual_days > 366)
      throw std::invalid_argument("annual_days must be 1..366");
    const auto daily_returns = fp::numbers(returns),
               traded = fp::numbers(turnover), book_pnl = fp::numbers(book);
    return fp::performance(daily_returns, traded, 1, annual_days, &book_pnl);
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}

PyObject *brain_summary(PyObject *, PyObject *args) {
  try {
    PyObject *turnover, *book;
    int annual_days;
    if (!PyArg_ParseTuple(args, "OOi", &book, &turnover, &annual_days))
      return nullptr;
    if (annual_days < 1 || annual_days > 366)
      throw std::invalid_argument("annual_days must be 1..366");
    const auto traded = fp::numbers(turnover), book_pnl = fp::numbers(book);
    return fp::brain_summary(book_pnl, traded, annual_days);
  } catch (const std::exception &e) {
    if (!PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}

PyObject *series_mean(PyObject *, PyObject *args) {
  try {
    PyObject *source;
    if (!PyArg_ParseTuple(args, "O", &source)) return nullptr;
    const auto values = fp::numbers(source);
    long double mean = 0;
    std::size_t n = 0;
    for (auto value : values)
      if (std::isfinite(value)) mean += (value - mean) / ++n;
    return fp::number(n ? double(mean) : fp::missing);
  } catch (const std::exception& error) {
    if (!PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, error.what());
    return nullptr;
  }
}

PyObject *report_rolling_mean(PyObject *, PyObject *args) {
  try {
    PyObject *source;
    int window;
    if (!PyArg_ParseTuple(args, "Oi", &source, &window)) return nullptr;
    if (window < 1) throw std::invalid_argument("Rolling window must be positive");
    const auto values = fp::numbers(source);
    std::vector<double> means(values.size(), fp::missing);
    const auto width = static_cast<std::size_t>(window);
    for (std::size_t end = width; end <= values.size(); ++end) {
      long double mean = 0;
      bool complete = true;
      for (std::size_t slot = 0; slot < width; ++slot) {
        const auto value = values[end - width + slot];
        if (!std::isfinite(value)) { complete = false; break; }
        mean += (value - mean) / (slot + 1);
      }
      if (complete) means[end - 1] = double(mean);
    }
    return fp::list(means);
  } catch (const std::exception& error) {
    if (!PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, error.what());
    return nullptr;
  }
}
