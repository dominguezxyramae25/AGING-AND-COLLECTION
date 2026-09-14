"""Small shared helpers."""

from __future__ import annotations

import pandas as pd


def month_end(series: pd.Series) -> pd.Series:
    """Normalise any date to the last day of its month."""
    return pd.to_datetime(series, errors="coerce").dt.to_period("M").dt.to_timestamp("M")
