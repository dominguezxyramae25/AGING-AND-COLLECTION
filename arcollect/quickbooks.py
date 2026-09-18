"""Normaliser for QuickBooks-style grouped reports.

QuickBooks exports its A/R reports as a *visual* layout rather than a table:

    |                          | Date       | Type    | Amount |
    | Abbott Laboratories Inc. |            |         |        |   <- group header
    |                          | 12/12/2022 | Invoice | 171360 |   <- data row
    | Total for Abbott Lab...  |            |         |  13725 |   <- subtotal
    | TOTAL                    |            |         | 9975621|   <- grand total
    | Monday, 14 September...  |            |         |        |   <- print stamp

Read naively that produces null customers on every data row and double-counts the
subtotals. This module turns it back into a real table: the group label is pushed
down onto the rows it owns, and subtotal, total and print-stamp rows are dropped.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

# Rows whose label begins this way are subtotals or grand totals, never data.
_TOTAL_RE = re.compile(r"^\s*(total\b|sub\s*total\b|grand\s+total\b)", re.I)

# Group labels that name a report section (an aging band) rather than a customer.
_SECTION_RE = re.compile(
    r"(^\s*current\s*$)"
    r"|(days?\s+past\s+due)"
    r"|(^\s*\d+\s*-\s*\d+\s*$)"
    r"|(\d+\s+or\s+more)"
    r"|(and\s+over)"
    r"|(^\s*overdue)"
    r"|(not\s+due)",
    re.I,
)

# A trailing "Monday, 14 September 2026 12:27 PM GMT+08:00" print stamp.
_STAMP_RE = re.compile(
    r"^\s*(?:mon|tues|wednes|thurs|fri|satur|sun)day,"
    r"|\d{1,2}:\d{2}\s*(?:am|pm)"
    r"|GMT[+-]", re.I)

CUSTOMER_ID_COL = "Customer ID"
CUSTOMER_NAME_COL = "Customer name"
SECTION_COL = "Report section"

# Header spellings that already identify the customer on each row.
_NAME_HEADERS = ("customer full name", "customer name", "customer", "client",
                 "account name", "name", "customer/supplier")


def _norm(text: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _is_placeholder(column: object) -> bool:
    """True for the unnamed first column QuickBooks puts its group labels in."""
    text = str(column).strip().lower()
    return (not text) or text.startswith(("column_", "unnamed:", "nan"))


def _classify_rows(df: pd.DataFrame, label_col: str) -> dict[str, pd.Series]:
    """Split a grouped report's rows into their structural kinds."""
    others = [c for c in df.columns if c != label_col]
    label = df[label_col]
    label_text = label.astype(str)
    others_blank = df[others].isna().all(axis=1) if others else pd.Series(True, index=df.index)

    is_total = label.notna() & label_text.str.match(_TOTAL_RE)
    is_stamp = label.notna() & others_blank & label_text.str.contains(_STAMP_RE, regex=True)
    is_header = label.notna() & others_blank & ~is_total & ~is_stamp
    is_blank = label.isna() & others_blank
    is_data = ~(is_header | is_total | is_stamp | is_blank)
    return {"total": is_total, "stamp": is_stamp, "header": is_header,
            "blank": is_blank, "data": is_data, "others": others}


def detect_label_column(df: pd.DataFrame) -> str | None:
    """Find the unnamed first column QuickBooks writes its labels into."""
    if df.empty or not len(df.columns) or not _is_placeholder(df.columns[0]):
        return None
    first = df.columns[0]
    if len(df.columns) < 2 or df[first].notna().sum() == 0:
        return None
    return first


def layout_of(df: pd.DataFrame) -> str:
    """Classify the sheet: ``grouped``, ``labelled`` or ``plain``.

    ``grouped``  - labels sit on their own rows above the rows they own.
    ``labelled`` - the unnamed first column holds a real value on every data row
                   (QuickBooks' A/R Ageing Summary does this), so it is a column
                   that simply lost its heading, not a grouping.
    """
    label_col = detect_label_column(df)
    if label_col is None:
        return "plain"
    kinds = _classify_rows(df, label_col)
    if not kinds["data"].any():
        return "plain"
    # Are the data rows relying on a label from above?
    orphan_share = df.loc[kinds["data"], label_col].isna().mean()
    if kinds["header"].any() and orphan_share > 0.5:
        return "grouped"
    if df.loc[kinds["data"], label_col].notna().any():
        return "labelled"
    return "plain"


def is_grouped_report(df: pd.DataFrame) -> bool:
    return layout_of(df) in ("grouped", "labelled")


def _looks_like_sections(values: pd.Series) -> bool:
    """Group labels are aging bands (A/R Ageing Detail) rather than customers."""
    distinct = pd.Series(values).dropna().astype(str).unique()
    if len(distinct) == 0:
        return False
    hits = sum(bool(_SECTION_RE.search(v)) for v in distinct)
    return hits >= max(1, len(distinct) // 2)


def _ensure_customer_columns(out: pd.DataFrame, values, meta: dict, kind: str) -> None:
    """QuickBooks carries no customer code -- the name is the identity, so emit
    both an ID and a name column pointing at it."""
    out[CUSTOMER_ID_COL] = values
    out[CUSTOMER_NAME_COL] = values
    meta["group_kind"] = kind


def normalize(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Turn a QuickBooks report into a flat table.

    Returns the cleaned frame plus metadata describing exactly what was removed,
    so the app can show the user what happened rather than silently changing totals.
    """
    meta: dict = {"grouped": False, "layout": "plain", "dropped_totals": 0,
                  "dropped_stamps": 0, "group_kind": None, "groups": 0, "notes": []}

    layout = layout_of(df)
    if layout == "plain":
        return df, meta

    label_col = detect_label_column(df)
    kinds = _classify_rows(df, label_col)
    others = kinds["others"]

    meta.update(grouped=True, layout=layout,
                dropped_totals=int(kinds["total"].sum()),
                dropped_stamps=int(kinds["stamp"].sum()))
    if kinds["total"].any():
        meta["notes"].append(
            f"Removed {int(kinds['total'].sum())} subtotal/total row(s) from the "
            f"QuickBooks layout so they are not counted as transactions.")

    keep = kinds["data"]
    out = df.loc[keep, others].copy()

    if layout == "grouped":
        group = df[label_col].where(kinds["header"]).ffill().loc[keep]
        meta["groups"] = int(df.loc[kinds["header"], label_col].nunique())
        if _looks_like_sections(group):
            out[SECTION_COL] = group.to_numpy()
            meta["group_kind"] = "section"
            meta["notes"].append(
                f"Report is grouped into {meta['groups']} aging sections; the section "
                f"name was kept as a column and aging is recomputed from the dates.")
            name_col = next((c for c in others if _norm(c) in
                             {_norm(h) for h in _NAME_HEADERS}), None)
            if name_col is not None:
                out[CUSTOMER_ID_COL] = out[name_col]
        else:
            _ensure_customer_columns(out, group.to_numpy(), meta, "customer")
            meta["notes"].append(
                f"Customer name was a group heading rather than a column; it was "
                f"applied to the {len(out):,} transaction rows beneath it "
                f"({meta['groups']} customers).")
    else:  # labelled
        values = df.loc[keep, label_col].to_numpy()
        meta["groups"] = int(pd.Series(values).nunique())
        _ensure_customer_columns(out, values, meta, "customer")
        meta["notes"].append(
            f"The first column had no heading; it holds the customer name for each "
            f"of the {len(out):,} rows and was named accordingly.")

    return out.reset_index(drop=True), meta


# --------------------------------------------------------------------------------------
# Transaction-type splitting
# --------------------------------------------------------------------------------------

_TYPE_HEADERS = ("transaction type", "type", "txn type")
_INVOICE_TYPES = {"invoice", "credit memo", "journal entry", "journal", "charge",
                  "statement charge", "debit memo", "sales receipt"}
_PAYMENT_TYPES = {"payment", "receive payment", "payment receipt", "deposit",
                  "customer payment", "cash receipt"}


def transaction_type_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if _norm(col) in {_norm(h) for h in _TYPE_HEADERS}:
            return col
    return None


def transaction_mix(df: pd.DataFrame) -> dict[str, int]:
    """Count how many invoice-like and payment-like rows a frame holds."""
    col = transaction_type_column(df)
    if col is None:
        return {}
    kinds = df[col].astype(str).str.strip().str.lower()
    return {
        "invoice": int(kinds.isin(_INVOICE_TYPES).sum()),
        "payment": int(kinds.isin(_PAYMENT_TYPES).sum()),
        "other": int((~kinds.isin(_INVOICE_TYPES | _PAYMENT_TYPES) & kinds.ne("nan")).sum()),
    }


def is_mixed_ledger(df: pd.DataFrame) -> bool:
    """True when one file holds both invoices and payments, as QuickBooks'
    'Invoices and Received Payments' report does."""
    mix = transaction_mix(df)
    return bool(mix) and mix.get("invoice", 0) > 0 and mix.get("payment", 0) > 0


def split_transactions(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a mixed ledger into (invoice-like rows, payment-like rows)."""
    col = transaction_type_column(df)
    if col is None:
        return df, pd.DataFrame()
    kinds = df[col].astype(str).str.strip().str.lower()
    invoices = df.loc[kinds.isin(_INVOICE_TYPES)].reset_index(drop=True)
    payments = df.loc[kinds.isin(_PAYMENT_TYPES)].reset_index(drop=True)
    return invoices, payments


def strip_payment_due_dates(df: pd.DataFrame) -> pd.DataFrame:
    """QuickBooks repeats the payment date in the Due date column on payment rows.

    Left alone it would be mapped as a real due date and pollute the aging, so it
    is cleared.
    """
    out = df.copy()
    due = next((c for c in out.columns if _norm(c) in {"duedate", "netduedate"}), None)
    date = next((c for c in out.columns if _norm(c) == "date"), None)
    if due and date:
        same = out[due].astype(str) == out[date].astype(str)
        out.loc[same, due] = np.nan
    return out
