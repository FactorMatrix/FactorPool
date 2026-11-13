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
    import _factorpool_market
    arrays = tuple(
        np.asarray(values, dtype=np.float64)
        for values in (close, previous, volume, turnover, member)
    )
    shape = arrays[0].shape
    if any(values.shape != shape for values in arrays[1:]):
        raise ValueError("Market return matrix axes differ")
    buffers = [np.ascontiguousarray(value if value.ndim == 2 else value.reshape(1, -1))
               for value in arrays]
    return np.frombuffer(_factorpool_market.daily_returns(*buffers), dtype=np.float64).reshape(shape)
