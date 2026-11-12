"""Small shared validators; report amounts only scale a proportional book."""
from datetime import date
import math
import re


def monetary_options(analysis):
    book, unit = analysis.get("book_size", 1), analysis.get("pnl_unit", "BOOK")
    if type(book) not in (int, float) or not 0 < book <= 1e15 or not math.isfinite(book):
        raise ValueError("analysis.book_size must be finite and in (0, 1e15]")
    if not isinstance(unit, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,11}", unit):
        raise ValueError("analysis.pnl_unit must be a 1..12 character uppercase unit label; no currency conversion is performed")
    return book, unit


def trace_dates(analysis, start, end):
    values = analysis.get("trace_dates", [])
    if not isinstance(values, list) or len(values) > 5:
        raise ValueError("analysis.trace_dates must contain at most five signal dates")
    normalized = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("analysis.trace_dates must contain ISO signal dates")
        try:
            day = date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError("analysis.trace_dates must contain ISO signal dates") from None
        if day < start or day > end:
            raise ValueError("analysis.trace_dates must be within the requested factor dates")
        normalized.append(day)
    if len(set(normalized)) != len(normalized):
        raise ValueError("analysis.trace_dates must contain unique signal dates")
    return sorted(normalized)
