"""File loading and type coercion.

Handles the two things that break naive ERP-export readers: title/logo rows above
the real header, and money formatted as text ("$1,234.50", "(500.00)").
"""

from __future__ import annotations

import csv as _csv
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd
from dateutil import parser as dateparser

from . import quickbooks

MAX_HEADER_SCAN = 15
_MONEY_STRIP = re.compile(r"[^\d\.\-\(\)]")
_NUM_LIKE = re.compile(r"^-?[\d,]*\.?\d+$")


def _is_headerish(value: object) -> bool:
    """True when a cell looks like a column title rather than data."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return False
    if isinstance(value, (int, float, pd.Timestamp)):
        return False
    text = str(value).strip()
    if not text or text.lower().startswith("unnamed:"):
        return False
    return not _NUM_LIKE.match(text)


def sniff_header_row(raw: pd.DataFrame) -> int:
    """Pick the row index most likely to be the real header.

    Scores each candidate row by how many header-looking cells it has, tie-broken
    toward the earliest row. ERP exports routinely put a report title, a company
    name and a date stamp above the grid.
    """
    best_row, best_score = 0, -1
    for i in range(min(MAX_HEADER_SCAN, len(raw))):
        row = raw.iloc[i]
        score = sum(_is_headerish(v) for v in row)
        # A header row should also be followed by something.
        if i + 1 >= len(raw):
            score -= 1
        if score > best_score:
            best_row, best_score = i, score
    return best_row


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n} ({seen[n]})")
        else:
            seen[n] = 0
            out.append(n)
    return out


def _read_text(source) -> str:
    if hasattr(source, "read"):
        data = source.read()
        if hasattr(source, "seek"):
            source.seek(0)
        return data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    return Path(source).read_text(encoding="utf-8-sig", errors="replace")


def _read_delimited(source, name: str) -> pd.DataFrame:
    """Read a delimited file whose rows may have differing field counts.

    Report titles above the grid have fewer fields than the data, and pandas fixes
    the column count from the first row it sees -- so the widest row is measured
    up front and used as the column count.
    """
    text = _read_text(source)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return pd.DataFrame()

    if name.endswith(".tsv"):
        sep = "\t"
    else:
        try:
            sep = _csv.Sniffer().sniff("\n".join(lines[:30]), delimiters=",;\t|").delimiter
        except _csv.Error:
            sep = ","

    widest = max(len(row) for row in _csv.reader(lines[:500], delimiter=sep)) or 1
    return pd.read_csv(io.StringIO(text), header=None, dtype=object, sep=sep,
                       engine="python", names=range(widest), skip_blank_lines=False)


def read_table(source: str | Path | io.BytesIO, filename: str | None = None,
               sheet: str | None = None) -> pd.DataFrame:
    """Read a CSV/Excel file into a DataFrame with the real header applied."""
    name = (filename or str(source)).lower()

    if name.endswith((".csv", ".txt", ".tsv")):
        raw = _read_delimited(source, name)
    else:
        engine = "xlrd" if name.endswith(".xls") else "openpyxl"
        raw = pd.read_excel(source, header=None, dtype=object,
                            sheet_name=sheet or 0, engine=engine)

    if raw.empty:
        return pd.DataFrame()

    hdr = sniff_header_row(raw)
    header = [str(v).strip() if _is_headerish(v) else f"column_{i + 1}"
              for i, v in enumerate(raw.iloc[hdr])]
    df = raw.iloc[hdr + 1:].copy()
    df.columns = _dedupe(header)
    df = df.reset_index(drop=True)
    # Drop rows and columns that are entirely blank.
    df = df.dropna(how="all").dropna(axis=1, how="all")

    # QuickBooks-style grouped reports need their group headings pushed down onto
    # the rows and their subtotal rows removed before anything downstream sees them.
    df, meta = quickbooks.normalize(df)
    if meta.get("grouped"):
        df.attrs["quickbooks"] = meta
    return df


def list_sheets(source: str | Path | io.BytesIO, filename: str | None = None) -> list[str]:
    name = (filename or str(source)).lower()
    if name.endswith((".csv", ".txt", ".tsv")):
        return []
    try:
        return pd.ExcelFile(source).sheet_names
    except Exception:
        return []


# --------------------------------------------------------------------------------------
# Coercion
# --------------------------------------------------------------------------------------

def to_money(series: pd.Series) -> pd.Series:
    """Coerce a column to float, honouring accounting conventions.

    Handles currency symbols, thousands separators, parentheses for negatives
    (``$(1,200.00)`` -> ``-1200.0``) and the trailing-minus form (``1200-``).
    """
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)

    text = series.astype(str).str.strip()
    # Parentheses may sit inside the symbol ("$(1,200.00)"), so look anywhere
    # rather than only at the start.
    negative = (text.str.contains(r"\(", regex=True) & text.str.contains(r"\)", regex=True))
    negative |= text.str.endswith("-")

    cleaned = text.str.replace(_MONEY_STRIP, "", regex=True)
    cleaned = cleaned.str.replace(r"[()]", "", regex=True)
    # Strip ONLY a trailing minus. Removing every hyphen would turn a plain
    # negative ("-1200000") into a positive and silently inflate the totals.
    cleaned = cleaned.str.replace(r"-+$", "", regex=True)
    cleaned = cleaned.replace({"": None, ".": None, "-": None})

    value = pd.to_numeric(cleaned, errors="coerce")
    return pd.Series(np.where(negative, -value.abs(), value),
                     index=series.index, dtype="float64").astype(float)


def to_number(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    cleaned = series.astype(str).str.extract(r"(-?\d+\.?\d*)", expand=False)
    return pd.to_numeric(cleaned, errors="coerce")


# Excel stores dates as days since 1899-12-30. This window spans 1954-2064, which
# comfortably covers any AR date while excluding plausible non-date integers.
_SERIAL_MIN, _SERIAL_MAX = 20000, 60000


def _from_excel_serial(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, unit="D", origin="1899-12-30", errors="coerce")


def to_date(series: pd.Series, dayfirst: bool = False) -> pd.Series:
    """Coerce to datetime, tolerating mixed formats and Excel serial numbers."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")

    # Bare numbers must be tested as serials FIRST: pandas would otherwise read
    # 45000 as 45000 nanoseconds and silently return 1970-01-01.
    numeric = pd.to_numeric(series, errors="coerce")
    serial_mask = numeric.between(_SERIAL_MIN, _SERIAL_MAX)
    if serial_mask.any():
        out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
        out.loc[serial_mask] = _from_excel_serial(numeric[serial_mask])
        remaining = ~serial_mask & series.notna()
        if remaining.any():
            out.loc[remaining] = pd.to_datetime(series[remaining], errors="coerce",
                                                dayfirst=dayfirst, format="mixed")
        return pd.to_datetime(out, errors="coerce")

    out = pd.to_datetime(series, errors="coerce", dayfirst=dayfirst, format="mixed")

    # Last resort: per-value dateutil for stubborn mixed formats.
    if out.isna().any():
        stubborn = out.isna() & series.notna()
        for idx in series.index[stubborn]:
            try:
                out.loc[idx] = dateparser.parse(str(series.loc[idx]), dayfirst=dayfirst)
            except (ValueError, TypeError, OverflowError):
                continue
    return pd.to_datetime(out, errors="coerce")


def to_string(series: pd.Series) -> pd.Series:
    out = series.astype(str).str.strip()
    # Excel turns numeric-looking IDs into floats; "1024.0" must match "1024".
    out = out.str.replace(r"^(\d+)\.0$", r"\1", regex=True)
    return out.replace({"nan": None, "None": None, "NaT": None, "": None})


COERCERS = {"money": to_money, "number": to_number, "date": to_date, "string": to_string}


def coerce(series: pd.Series, dtype: str, dayfirst: bool = False) -> pd.Series:
    if dtype == "date":
        return to_date(series, dayfirst=dayfirst)
    return COERCERS[dtype](series)
