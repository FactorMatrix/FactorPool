"""Read-only inputs. Fixture data is explicitly synthetic."""
from datetime import date, timedelta


def fixture():
    frames = []
    day = date(2024, 1, 2)
    for i in range(240):
        while day.weekday() > 4:
            day += timedelta(days=1)
        rows = {}
        for symbol, volume, base, growth in (("A", 10, 10, 1.1), ("B", 20, 20, 1), ("C", 30, 30, .9)):
            price = base * growth ** i
            rows[symbol] = dict(open=price, high=price, low=price, close=price,
                                prev_close=base * growth ** (i-1), value=price, volume=volume)
        frames.append(dict(date=day.isoformat(), session_index=i, symbols=["A", "B", "C"], rows=rows))
        day += timedelta(days=1)
    return dict(provider="fixture", adjustment="raw", symbols=["A", "B", "C"], frames=frames,
                snapshot_id="synthetic-ohlcv-prev-close-v3", synthetic=True)
