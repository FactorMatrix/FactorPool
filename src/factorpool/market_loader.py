"""Task-local matrix assembly over the bounded data-service/Redis client."""
from copy import deepcopy
from bisect import bisect_left
from datetime import date
import os
from pathlib import Path
import numpy as np
from .data_client.contract import (FIELDS, FORMAT, PRESENT_FIELD, MEMBERSHIP_FIELD,
                                   FIELD_BLOCK_FORMAT, field_catalog)

_ROW_FIELDS = tuple(field for field in FIELDS if field not in ("member", "return"))
# Indicator input adjustment consumes only these persisted matrices. Daily
# multipliers and known-status columns remain available through the data API.
_ADJUSTMENT_INPUTS = (('q', '__q__', '<f8'), ('segment', '__segment__', '<i8'))


def _with_terminal_rows(payload, manifest, all_symbols, dates, initial):
    """Carry the existing exclusive lifetime boundary, without changing R."""
    lifetimes = manifest.get("instrument_lifetimes")
    if lifetimes is None:
        return payload
    lookup = {symbol: n for n, symbol in enumerate(all_symbols)}
    calendar = [int(day.replace("-", "")) for day in dates]
    payload["terminal_exit_rows"] = [
        max(0, bisect_left(calendar, lifetimes[lookup[symbol]][1]) - initial)
        if 0 < lifetimes[lookup[symbol]][1] <= calendar[-1] else -1
        for symbol in payload["symbols"]
    ]
    return payload


def _axis(values, label):
    if (not isinstance(values, list) or not values
            or any(not isinstance(value, str) or not value for value in values)
            or any(a >= b for a, b in zip(values, values[1:]))):
        raise ValueError(f"Invalid market {label} axis")
    return values


def _input_budget():
    """Keep input, axes and research workspaces within the task's actual limit."""
    try:
        budget = int(os.environ.get("FACTORPOOL_INPUT_MAX_BYTES", str(2 * 1024**3)))
    except (TypeError, ValueError) as exc:
        raise ValueError("FACTORPOOL_INPUT_MAX_BYTES must be a positive byte count") from exc
    if not 1 <= budget <= 64 * 1024**3:
        raise ValueError("FACTORPOOL_INPUT_MAX_BYTES must be in 1..64 GiB")
    try:
        maximum = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if maximum != "max":
            budget = min(budget, int(maximum) // 2)
    except (OSError, ValueError):
        pass
    return budget


def _check_memory(history, total, calculation, research, field_count):
    # Date/security counts alone do not describe input memory: a projected
    # turnover task needs one quote field, while a full quote view needs nine.
    quote_bytes = field_count * history * calculation * 8
    status_bytes = history * calculation * 2 + total * research
    return_bytes = total * research * 8
    axis_bytes = history * (calculation + research) * 8
    workspace_bytes = history * max(calculation, research) * 32
    estimated = quote_bytes + status_bytes + return_bytes + axis_bytes + workspace_bytes + 128 * 1024**2
    budget = _input_budget()
    if estimated > budget:
        raise ValueError(f"Research input and workspaces need approximately {estimated / 1024**2:.1f} MiB; "
                         f"input memory budget is {budget / 1024**2:.1f} MiB. Narrow dates/universe or configure task memory.")


def _projection_fields(fields, *, catalog=None):
    if not isinstance(fields, (tuple, list, set, frozenset)) or any(not isinstance(field, str) for field in fields):
        raise ValueError("fields must contain supported input field names")
    requested = {"close" if field == "value" else field for field in fields}
    available = set(_ROW_FIELDS)
    if catalog is not None:
        available.update(field for field, record in catalog.items()
                         if record.get('table') == 'cn_stock_factor_input')
    missing = requested - available
    if missing:
        raise ValueError(f"Provider has no point-in-time fields: {sorted(missing)}")
    return (*tuple(field for field in FIELDS if field in requested), *tuple(sorted(requested - set(FIELDS))))


def load(data, warmup, future, *, source=None, pasteurization="ON", fields=None):
    """Read typed quote columns through factor_end and the independent daily R.

    Frames contain calendar/universe axes only. Future valuations request R,
    never future quote columns or per-security bar dictionaries.
    """
    if pasteurization not in ("ON", "OFF"):
        raise ValueError("pasteurization must be ON or OFF")
    requested_fields = fields
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
        if (manifest.get("format") != FORMAT or tuple(fields) != FIELDS
                or manifest.get("storage_format") != FIELD_BLOCK_FORMAT):
            raise ValueError("Research requires a current field-blocks-v1 market snapshot")
        projected_fields = (FIELDS if requested_fields is None else _projection_fields(
            requested_fields, catalog=field_catalog(snapshot)))
        return_policy = manifest.get("return_policy")
        if not isinstance(return_policy, dict) or return_policy.get("version") != "volume-zero-v1":
            raise ValueError("Research requires the current volume-zero-v1 return policy")
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
        return _load_projected(source, snapshot, snapshot_id, manifest, all_symbols, dates,
                               selected, columns, dynamic, initial, finish, stop, return_policy,
                               projected_fields, pasteurization)
    finally:
        if owned:
            source.close()


def _column_selection(columns):
    """Contiguous column positions can be borrowed as a view during assembly."""
    if len(columns) and (len(columns) == 1 or np.all(np.diff(columns) == 1)):
        return slice(int(columns[0]), int(columns[-1]) + 1)
    return columns


def _load_projected(source, snapshot, snapshot_id, manifest, all_symbols, dates,
                    selected, columns, dynamic, initial, finish, stop, return_policy,
                    quote_fields, pasteurization):
    """Read only factor dependencies and status; future input is exclusively R."""
    if pasteurization == "OFF":
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
    else:
        calculation_columns = columns
    if not len(calculation_columns):
        raise ValueError("Pasteurization OFF has no active market instruments")
    calculation_symbols = [all_symbols[int(column)] for column in calculation_columns]
    history, total = finish-initial, stop-initial
    stored_adjustment = bool(set(quote_fields) & {'open', 'high', 'low', 'close', 'prev_close', 'volume'})
    _check_memory(history, total, len(calculation_columns), len(columns), len(quote_fields)+(len(_ADJUSTMENT_INPUTS) if stored_adjustment else 0))
    values = np.empty((len(quote_fields), history, len(calculation_columns)), dtype="<f8")
    present = np.empty((history, len(calculation_columns)), dtype=bool)
    calculation_members = np.empty(present.shape, dtype=bool)
    adjustment = ({key: np.empty(present.shape, dtype=dtype)
                   for key, _, dtype in _ADJUSTMENT_INPUTS}
                  if stored_adjustment else None)
    returns = np.empty((total, len(columns)), dtype="<f8")
    read_columns = np.union1d(calculation_columns, columns)
    input_columns = _column_selection(np.searchsorted(read_columns, calculation_columns))
    return_columns = _column_selection(np.searchsorted(read_columns, columns))
    historical_fields = tuple(dict.fromkeys((*quote_fields, MEMBERSHIP_FIELD, PRESENT_FIELD, "return",
                         *(tuple(field for _, field, _ in _ADJUSTMENT_INPUTS) if stored_adjustment else ()))))
    covered, seen = initial, False
    for start, count, block in source.selected_columns(snapshot, initial, finish, read_columns, fields=historical_fields):
        end = start+count
        if (start < 0 or count < 1 or end > len(dates)
                or (start != covered if seen else not start <= covered < end)):
            raise ValueError("Market matrix blocks overlap or have missing dates")
        left, right = max(initial, start), min(finish, end)
        target, current = slice(left-initial, right-initial), slice(left-start, right-start)
        for index, field in enumerate(quote_fields):
            values[index, target] = block[field][current][:, input_columns]
        present[target] = block[PRESENT_FIELD][current][:, input_columns]
        calculation_members[target] = block[MEMBERSHIP_FIELD][current][:, input_columns]
        returns[target] = block["return"][current][:, return_columns]
        if adjustment is not None:
            for key, field, _ in _ADJUSTMENT_INPUTS:
                adjustment[key][target] = block[field][current][:, input_columns]
        covered, seen = right, True
    if covered != finish:
        raise ValueError("Market matrix snapshot has missing blocks")
    # An original block can cross factor_end, but none of its future quote
    # columns enters the Indicator view. Future requests ask only for returns.
    if finish < stop:
        covered, seen = finish, False
        for start, count, block in source.selected_columns(snapshot, finish, stop, columns, fields=("return",)):
            end = start+count
            if (start < 0 or count < 1 or end > len(dates)
                    or (start != covered if seen else not start <= covered < end)):
                raise ValueError("Market matrix blocks overlap or have missing dates")
            left, right = max(finish, start), min(stop, end)
            returns[left-initial:right-initial] = block["return"][left-start:right-start]
            covered, seen = right, True
        if covered != stop:
            raise ValueError("Market matrix snapshot has missing blocks")
    lookup = {int(column): n for n, column in enumerate(calculation_columns)}
    research_columns = np.array([lookup.get(int(column), -1) for column in columns], dtype=np.intp)
    if dynamic:
        mask = np.any(calculation_members[:, _column_selection(research_columns)], axis=0)
        if not np.any(mask):
            raise ValueError("Universe has no instruments in the requested range")
        complete = bool(np.all(mask))
        if not complete:
            returns = np.ascontiguousarray(returns[:, mask])
        selected = [symbol for symbol, keep in zip(selected, mask) if keep]
        research_columns = research_columns[mask]
        if pasteurization == "ON" and not complete:
            values = np.ascontiguousarray(values[:, :, mask])
            present = np.ascontiguousarray(present[:, mask])
            calculation_members = np.ascontiguousarray(calculation_members[:, mask])
            if adjustment is not None:
                adjustment = {key: np.ascontiguousarray(array[:, mask]) for key, array in adjustment.items()}
            calculation_symbols = selected.copy()
            research_columns = np.arange(len(selected), dtype=np.intp)
    membership = np.zeros((total, len(selected)), dtype=bool)
    membership[:history] = calculation_members[:, _column_selection(research_columns)] if dynamic else True
    complete_axis = np.arange(len(calculation_symbols), dtype=np.intp)
    complete_axis.setflags(write=False)
    calculation_objects = np.asarray(calculation_symbols, dtype=object)
    research_objects = np.asarray(selected, dtype=object)
    same_axis = selected == calculation_symbols
    frames = []
    for t, day in enumerate(dates[initial:stop]):
        if t >= history:
            frames.append(dict(date=day, session_index=initial+t, symbols=[], rows={}, research_symbols=[]))
            continue
        active_columns = (np.flatnonzero(calculation_members[t])
                          if dynamic or pasteurization == "OFF" else complete_axis)
        active_columns.setflags(write=False)
        axis = (calculation_symbols if active_columns is complete_axis
                else calculation_objects[active_columns].tolist())
        if dynamic:
            research_axis = axis if same_axis else research_objects[membership[t]].tolist()
        else:
            research_axis = selected
        frames.append(dict(date=day, session_index=initial+t, symbols=axis, rows={},
                           research_symbols=research_axis, symbol_columns=active_columns))
    for matrix in (values, present, calculation_members):
        matrix.setflags(write=False)
    quotes = dict(fields=quote_fields, values=values, present=present, membership=calculation_members,
                  source_member=calculation_members,
                  dates=list(dates[initial:finish]), symbols=calculation_symbols,
                  research_columns=research_columns)
    if adjustment is not None:
        for array in adjustment.values():
            array.setflags(write=False)
        adjustment['policy'] = deepcopy(manifest.get('adjustment_policy', {}))
        quotes['adjustment'] = adjustment
    return _with_terminal_rows(dict(provider="market_matrix", adjustment="raw", symbols=selected, frames=frames,
                returns=np.ascontiguousarray(returns), snapshot_id=snapshot_id, synthetic=False,
                return_policy=deepcopy(return_policy), membership=membership,
                quotes=quotes), manifest, all_symbols, dates, initial)
