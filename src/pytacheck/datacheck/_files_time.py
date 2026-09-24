"""R ``POSIXct`` (double seconds since 1970, UTC) as a pandas datetime column."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def posixct_series(secs: Sequence[float] | np.ndarray | pd.Series) -> pd.Series:
    """Seconds since the epoch as a UTC ``datetime64`` Series (``NaT`` for NA).

    R's times are doubles and span any year; nanosecond timestamps stop in 2262,
    so fractional seconds outside that range use microseconds and anything
    beyond that whole seconds, instead of failing.
    """
    arr = np.asarray(secs, dtype="float64")
    try:
        return pd.Series(pd.to_datetime(arr, unit="s", utc=True))
    except (OverflowError, pd.errors.OutOfBoundsDatetime):
        pass
    finite = np.isfinite(arr)
    safe = np.where(finite, arr, 0.0)
    if np.all(np.abs(safe) < 9.2e12):
        vals = np.round(safe * 1e6).astype("int64").view("datetime64[us]")
    else:
        vals = np.round(safe).astype("int64").view("datetime64[s]")
    vals = np.where(finite, vals, np.datetime64("NaT"))
    return pd.Series(vals).dt.tz_localize("UTC")
