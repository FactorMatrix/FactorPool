#include "analysis.hpp"
#include <algorithm>
#include <cmath>
#include <factortrader/mods/data_mmap/mmap_data_source.hpp>
#include <set>
#include <stdexcept>
namespace {
struct Owned {
  PyObject *p;
  ~Owned() { Py_XDECREF(p); }
};
void put(PyObject *d, const char *key, PyObject *v) {
  Owned p{v};
  if (!v || PyDict_SetItemString(d, key, v) < 0)
    throw std::bad_alloc();
}
PyObject *number(double n) {
  if (std::isfinite(n))
    return PyFloat_FromDouble(n);
  Py_RETURN_NONE;
}
} // namespace
PyObject *read_mmap(PyObject *, PyObject *args) {
  try {
    const char *root, *start, *end;
    PyObject *requested;
    int warmup, dynamic;
    if (!PyArg_ParseTuple(args, "Osssii", &requested, &root, &start, &end,
                          &warmup, &dynamic))
      return nullptr;
    if (warmup < 1 || warmup > 10000)
      throw std::invalid_argument("Warmup must be 1..10000");
    std::vector<std::string> symbols;
    Owned seq{PySequence_Fast(requested, "Expected symbols")};
    if (!seq.p)
      return nullptr;
    for (Py_ssize_t i = 0; i < PySequence_Fast_GET_SIZE(seq.p); ++i) {
      const char *id = PyUnicode_AsUTF8(PySequence_Fast_GET_ITEM(seq.p, i));
      if (!id)
        return nullptr;
      symbols.emplace_back(id);
    }
    factortrader::mod::MmapDataSource source(root);
    auto first = factortrader::Date::parse(start),
         last = factortrader::Date::parse(end);
    if (last < first)
      throw std::invalid_argument("End precedes start");
    auto calendar = source.raw_trading_calendar();
    auto begin = std::lower_bound(calendar.begin(), calendar.end(), first),
         finish = std::upper_bound(calendar.begin(), calendar.end(), last);
    if (begin == finish)
      throw std::invalid_argument("No trading dates in requested range");
    auto initial =
        std::max<std::ptrdiff_t>(0, begin - calendar.begin() - (warmup - 1));
    auto stop = std::min<std::ptrdiff_t>(calendar.size(),
                                         finish - calendar.begin() + 2);
    if (stop - initial > 10000)
      throw std::invalid_argument("Too many dates");
    std::set<std::string> union_symbols(symbols.begin(), symbols.end());
    std::vector<std::vector<std::string>> axes;
    for (auto t = initial; t < stop; ++t) {
      auto axis = symbols;
      if (dynamic) {
        axis.clear();
        for (const auto &instrument : source.raw_all_instruments(
                 factortrader::InstrumentType::CS, calendar[t]))
          axis.push_back(instrument.order_book_id);
      }
      std::sort(axis.begin(), axis.end());
      axis.erase(std::unique(axis.begin(), axis.end()), axis.end());
      union_symbols.insert(axis.begin(), axis.end());
      axes.push_back(std::move(axis));
    }
    if (union_symbols.empty())
      throw std::invalid_argument("Universe has no instruments");
    if (union_symbols.size() * static_cast<std::size_t>(stop - initial) >
        2000000)
      throw std::invalid_argument(
          "Research input exceeds 2,000,000 cells; narrow dates/universe");
    for (const auto &id : union_symbols)
      if (!source.raw_instrument(id))
        throw std::invalid_argument("Unknown instrument: " + id);
    Owned out{PyDict_New()}, frames{PyList_New(0)}, all{PyList_New(0)};
    for (const auto &id : union_symbols) {
      Owned v{PyUnicode_FromString(id.c_str())};
      PyList_Append(all.p, v.p);
    }
    for (auto t = initial; t < stop; ++t) {
      Owned frame{PyDict_New()}, rows{PyDict_New()}, axis{PyList_New(0)};
      for (const auto &id : axes[t - initial]) {
        Owned name{PyUnicode_FromString(id.c_str())};
        PyList_Append(axis.p, name.p);
        auto bar = source.raw_get_bar(id, {calendar[t], 15 * 3600}, "1d");
        if (!bar)
          continue;
        Owned row{PyDict_New()};
        put(row.p, "open", number(bar->open));
        put(row.p, "high", number(bar->high));
        put(row.p, "low", number(bar->low));
        put(row.p, "close", number(bar->close));
        put(row.p, "value", number(bar->close));
        put(row.p, "volume", number(bar->volume));
        put(row.p, "is_st",
            PyBool_FromLong(source.raw_is_st_stock(id, calendar[t])));
        put(row.p, "is_suspended",
            PyBool_FromLong(source.raw_is_suspended(id, calendar[t])));
        if (PyDict_SetItemString(rows.p, id.c_str(), row.p) < 0)
          throw std::bad_alloc();
      }
      put(frame.p, "date", PyUnicode_FromString(calendar[t].str().c_str()));
      put(frame.p, "symbols", Py_NewRef(axis.p));
      put(frame.p, "rows", Py_NewRef(rows.p));
      PyList_Append(frames.p, frame.p);
    }
    put(out.p, "symbols", Py_NewRef(all.p));
    put(out.p, "frames", Py_NewRef(frames.p));
    put(out.p, "provider", PyUnicode_FromString("ft_daily_mmap"));
    put(out.p, "adjustment", PyUnicode_FromString("raw"));
    return Py_NewRef(out.p);
  } catch (const std::exception &e) {
    if (!PyErr_Occurred())
      PyErr_SetString(PyExc_ValueError, e.what());
    return nullptr;
  }
}
