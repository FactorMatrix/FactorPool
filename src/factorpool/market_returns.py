"""Derive research returns during ingestion without changing source quotes."""
import numpy as np


def return_policy():
    # This definition is part of the immutable market snapshot identity.
    return dict(
        version="volume-zero-v1",
        formula="close/prev_close-1",
        normal="finite positive volume and valid close/prev_close use the price formula",
        no_trade="finite zero volume and zero turnover inside the instrument lifetime produce zero return, even if a quote is missing",
        unknown="missing, nonfinite or negative volume; missing bar; zero volume with nonzero or unknown turnover remain missing",
        suspension="source status is retained; it never overrides positive volume or supplies missing volume",
        lifetime="no-trade zeros require finite positive historical membership",
        raw_fields="OHLC, prev_close, volume, turnover and status are retained unchanged",
    )


def daily_returns(close, previous, volume, turnover, member):
    """Positive-volume price returns, explicit no-trade zeros, unknowns as NaN."""
    close, previous, volume, turnover, member = (
        np.asarray(values, dtype=np.float64)
        for values in (close, previous, volume, turnover, member)
    )
    if any(values.shape != close.shape
           for values in (previous, volume, turnover, member)):
        raise ValueError("Market return matrix axes differ")
    result = np.full(close.shape, np.nan, dtype=np.float64)
    traded = (np.isfinite(volume) & (volume > 0) & np.isfinite(close)
              & (close >= 0) & np.isfinite(previous) & (previous > 0))
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        np.divide(close, previous, out=result, where=traded)
        np.subtract(result, 1, out=result, where=traded)
    result[~np.isfinite(result)] = np.nan
    # A raw reference quote may change on a day with no transactions. Deriving
    # a price return then would create PnL from an untraded quote. Do not repair
    # that quote or infer a zero from absent volume or a suspension flag alone.
    no_trade = (np.isfinite(volume) & (volume == 0)
                & np.isfinite(turnover) & (turnover == 0)
                & np.isfinite(member) & (member > 0))
    result[no_trade] = 0
    return result
