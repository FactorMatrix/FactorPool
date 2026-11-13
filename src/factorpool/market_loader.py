"""Task-local matrix assembly over the bounded data-service/Redis client."""
from copy import deepcopy
from datetime import date
import numpy as np
from .data_client.contract import FIELDS, FORMAT

MAX_CELLS = 2_000_000
_ROW_FIELDS = tuple(field for field in FIELDS if field not in ("member", "return"))
_ROW_COLUMNS = np.array([FIELDS.index(field) for field in _ROW_FIELDS])


def _bar_rows(values, symbols):
    """Convert one native matrix row at a time, preserving missing-bar rules."""
    present = np.flatnonzero(np.any(np.isfinite(values[:7]), axis=0))
    selected = values[_ROW_COLUMNS][:, present].T
    converted = np.where(np.isfinite(selected), selected, None).tolist()
    rows = {}
    for n, cells in zip(present, converted):
        row = dict(zip(_ROW_FIELDS, cells))
        row["value"] = row["close"]
        rows[symbols[n]] = row
    return rows


def _quote_view(values, dates, symbols, research_symbols):
    """Historical raw matrix for task-local, as-of signal evaluation."""
    historical = values[:, :len(dates)].view()
    historical.setflags(write=False)
    lookup = {symbol: n for n, symbol in enumerate(symbols)}
    return dict(fields=FIELDS, values=historical, dates=list(dates), symbols=list(symbols),
                research_columns=np.array([lookup.get(symbol, -1) for symbol in research_symbols],
                                          dtype=np.intp))


def _axis(values, label):
    if (not isinstance(values, list) or not values
            or any(not isinstance(value, str) or not value for value in values)
            or any(a >= b for a, b in zip(values, values[1:]))):
        raise ValueError(f"Invalid market {label} axis")
    return values


def load(data, warmup, future, *, source=None, pasteurization="ON", matrix_only=False):
    """Read full daily R; optionally expose quotes without per-stock bar dicts.

    matrix_only keeps the full frame/calendar and universe metadata, with empty
    rows. Its quotes matrix contains history through factor_end only; future
    valuations remain available solely on the independent returns matrix.
    """
    if type(matrix_only) is not bool:
        raise ValueError("matrix_only must be bool")
    if pasteurization not in ("ON", "OFF"):
        raise ValueError("pasteurization must be ON or OFF")
    snapshot_id = data.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("A fixed market snapshot_id is required")
    if (type(warmup) is not int or not 1 <= warmup <= 10000
            or type(future) is not int or not 0 <= future <= 2521):
        raise ValueError("Invalid warmup or future date count")
    first = date.fromisoformat(data["factor_start"]).isoformat()
    last = date.fromisoformat(data["factor_end"]).isoformat()
    if last < first:
        raise ValueError("End precedes start")
    from .data_client import DataClient
    owned = source is None
    source = DataClient() if owned else source
    try:
        snapshot = source.snapshot(snapshot_id)
        manifest, all_symbols, dates, fields = (snapshot[key] for key in ("manifest", "symbols", "dates", "fields"))
        if (manifest.get("format") != FORMAT or tuple(fields) != FIELDS):
            raise ValueError("Unsupported market snapshot format")
        return_policy = manifest.get("return_policy", dict(version="legacy-price-ratio-v1", formula="close/prev_close-1"))
        if not isinstance(return_policy, dict):
            raise ValueError("Market snapshot return_policy must be an object")
        all_symbols = _axis(all_symbols, "symbol")
        dates = _axis(dates, "date")
        date_axis = np.asarray(dates)
        begin = int(np.searchsorted(date_axis, first, side="left"))
        finish = int(np.searchsorted(date_axis, last, side="right"))
        if begin == finish:
            raise ValueError("No market dates in requested range")
        initial = max(0, begin - warmup + 1)
        stop = min(len(dates), finish + future)
        universe = data.get("universe")
        dynamic = universe == "all_a_dynamic"
        if not dynamic:
            if (not isinstance(universe, list) or not universe
                    or any(not isinstance(s, str) or not s for s in universe)
                    or len(set(universe)) != len(universe)):
                raise ValueError("Specify unique symbols or all_a_dynamic")
            selected = sorted(universe)
            lookup = {symbol: i for i, symbol in enumerate(all_symbols)}
            if any(symbol not in lookup for symbol in selected):
                raise ValueError("Universe includes a symbol absent from this snapshot")
            columns = np.array([lookup[symbol] for symbol in selected], dtype=np.intp)
        else:
            lifetimes = manifest.get("instrument_lifetimes")
            if (not isinstance(lifetimes, list) or len(lifetimes) != len(all_symbols)
                    or any(not isinstance(pair, list) or len(pair) != 2
                           or any(type(day) is not int for day in pair) for pair in lifetimes)):
                raise ValueError("Snapshot is missing historical instrument lifetimes")
            lifetimes = np.asarray(lifetimes, dtype=np.int64)
            day_keys = np.array([int(day.replace("-", "")) for day in dates])
            listed = np.searchsorted(day_keys, lifetimes[:, 0], side="left")
            delisted = np.searchsorted(day_keys, lifetimes[:, 1], side="left")
            # The same [listed, delisted) boundary as stored membership. Only
            # dates actually sent to the Indicator can add a symbol to its axis.
            mask = np.maximum(listed, initial) < np.minimum(delisted, finish)
            columns = np.flatnonzero(mask)
            selected = [symbol for symbol, keep in zip(all_symbols, mask) if keep]
            if not selected:
                raise ValueError("Universe has no instruments in the requested range")
        if len(columns) * (stop - initial) > MAX_CELLS:
            raise ValueError("Research input exceeds 2,000,000 cells; narrow dates/universe")
        if pasteurization == "OFF":
            return _load_unpasteurized(
                source, snapshot, snapshot_id, manifest, all_symbols, dates, fields,
                selected, columns, dynamic, initial, finish, stop, return_policy, matrix_only,
            )
        matrices = np.full((len(FIELDS), stop - initial, len(columns)), np.nan)
        covered = initial
        seen = False
        for start, count, block in source.selected_blocks(snapshot, initial, stop, columns):
            end = start + count
            if (start < 0 or count < 1 or end > len(dates)
                    or (start != covered if seen else not start <= covered < end)):
                raise ValueError("Market matrix blocks overlap or have missing dates")
            left, right = max(initial, start), min(stop, end)
            matrices[:, left - initial:right - initial] = block[:, left - start:right - start]
            covered = right
            seen = True
        if covered != stop:
            raise ValueError("Market matrix snapshot has missing blocks")
    finally:
        if owned:
            source.close()
    if dynamic:
        membership = matrices[FIELDS.index("member"), :finish - initial]
        mask = np.any(membership > 0, axis=0)
        if not np.any(mask):
            raise ValueError("Universe has no instruments in the requested range")
        matrices = np.ascontiguousarray(matrices[:, :, mask])
        selected = [symbol for symbol, keep in zip(selected, mask) if keep]
    frames = []
    for t, day in enumerate(dates[initial:stop]):
        if day > last:
            frame = dict(date=day, session_index=initial+t, symbols=[], rows={})
            if matrix_only:
                frame["research_symbols"] = []
            frames.append(frame)
            continue
        axis = ([s for n, s in enumerate(selected) if matrices[8, t, n] > 0]
                if dynamic else selected.copy())
        rows = {} if matrix_only else _bar_rows(matrices[:, t], selected)
        frame = dict(date=day, session_index=initial+t, symbols=axis, rows=rows)
        if matrix_only:
            frame["research_symbols"] = axis.copy()
        frames.append(frame)
    result = dict(provider="market_matrix", adjustment="raw", symbols=selected,
                  frames=frames, returns=np.ascontiguousarray(matrices[7]),
                  snapshot_id=snapshot_id, synthetic=False,
                  return_policy=deepcopy(return_policy))
    if matrix_only:
        result["quotes"] = _quote_view(matrices, dates[initial:finish], selected, selected)
        result["returns"] = result["returns"].copy(order="C")
    return result


def _load_unpasteurized(source, snapshot, snapshot_id, manifest, all_symbols, dates, fields,
                       selected, columns, dynamic, initial, finish, stop, return_policy, matrix_only):
    """Expand only Indicator inputs; research and future-return axes stay fixed."""
    lifetimes = manifest.get("instrument_lifetimes")
    if (not isinstance(lifetimes, list) or len(lifetimes) != len(all_symbols)
            or any(not isinstance(pair, list) or len(pair) != 2
                   or any(type(day) is not int for day in pair) for pair in lifetimes)):
        raise ValueError("Pasteurization OFF needs historical instrument lifetimes")
    lifetimes = np.asarray(lifetimes, dtype=np.int64)
    day_keys = np.array([int(day.replace("-", "")) for day in dates])
    listed = np.searchsorted(day_keys, lifetimes[:, 0], side="left")
    delisted = np.searchsorted(day_keys, lifetimes[:, 1], side="left")
    active = np.maximum(listed, initial) < np.minimum(delisted, finish)
    calculation_columns = np.flatnonzero(active)
    calculation_symbols = [symbol for symbol, keep in zip(all_symbols, active) if keep]
    if not calculation_symbols:
        raise ValueError("Pasteurization OFF has no active market instruments")
    historical_days, total_days = finish - initial, stop - initial
    # Count distinct required source cells. Historical market inputs already
    # include research columns; only their future tail and any historically
    # inactive explicit research columns add source cells to that budget.
    inactive_research = np.count_nonzero(~active[columns])
    required_cells = (historical_days * len(calculation_columns)
                      + (stop - finish) * len(columns)
                      + historical_days * inactive_research)
    if required_cells > MAX_CELLS:
        raise ValueError(
            "Pasteurization OFF calculation and research input exceeds 2,000,000 cells; narrow dates/universe")
    calculation = np.full((len(FIELDS), historical_days, len(calculation_columns)), np.nan)
    returns = np.full((total_days, len(columns)), np.nan)
    read_columns = np.union1d(calculation_columns, columns)
    return_columns = np.searchsorted(read_columns, columns)
    input_columns = np.searchsorted(read_columns, calculation_columns)
    covered, seen = initial, False
    for start, count, block in source.selected_blocks(snapshot, initial, stop, read_columns):
        end = start + count
        if (start < 0 or count < 1 or end > len(dates)
                or (start != covered if seen else not start <= covered < end)):
            raise ValueError("Market matrix blocks overlap or have missing dates")
        left, right = max(initial, start), min(stop, end)
        returns[left-initial:right-initial] = block[7, left-start:right-start, :][:, return_columns]
        historical_right = min(finish, end)
        if left < historical_right:
            calculation[:, left-initial:historical_right-initial] = (
                block[:, left-start:historical_right-start, :][:, :, input_columns])
        covered, seen = right, True
    if covered != stop:
        raise ValueError("Market matrix snapshot has missing blocks")
    index = {int(column): n for n, column in enumerate(calculation_columns)}
    if dynamic:
        research_columns = np.array([index[int(column)] for column in columns], dtype=np.intp)
        mask = np.any(calculation[8][:, research_columns] > 0, axis=0)
        if not np.any(mask):
            raise ValueError("Universe has no instruments in the requested range")
        returns = np.ascontiguousarray(returns[:, mask])
        selected = [symbol for symbol, keep in zip(selected, mask) if keep]
        research_columns = research_columns[mask]
    frames = []
    for t, day in enumerate(dates[initial:stop]):
        if t >= historical_days:
            frames.append(dict(date=day, session_index=initial+t, symbols=[], rows={}, research_symbols=[]))
            continue
        axis = [symbol for n, symbol in enumerate(calculation_symbols) if calculation[8, t, n] > 0]
        research_axis = ([symbol for symbol, column in zip(selected, research_columns)
                          if calculation[8, t, column] > 0]
                         if dynamic else selected.copy())
        rows = {} if matrix_only else _bar_rows(calculation[:, t], calculation_symbols)
        frames.append(dict(date=day, session_index=initial+t, symbols=axis, rows=rows, research_symbols=research_axis))
    result = dict(provider="market_matrix", adjustment="raw", symbols=selected,
                  frames=frames, returns=np.ascontiguousarray(returns),
                  snapshot_id=snapshot_id, synthetic=False,
                  return_policy=deepcopy(return_policy))
    if matrix_only:
        result["quotes"] = _quote_view(calculation, dates[initial:finish], calculation_symbols, selected)
    return result
