"""AR aging: days past due, bucketing, and the aging summary/detail tables."""

from __future__ import annotations

import numpy as np
import pandas as pd

# Bucket edges are the single source of truth. ``(label, lower, upper)`` where bounds
# are inclusive days-past-due and ``None`` means unbounded.
BUCKETS: tuple[tuple[str, int | None, int | None], ...] = (
    ("Current", None, 0),
    ("1-30", 1, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("91-120", 91, 120),
    ("120+", 121, None),
)

BUCKET_LABELS = [b[0] for b in BUCKETS]
PAST_DUE_LABELS = [b[0] for b in BUCKETS if b[0] != "Current"]


def bucket_of(dpd: float) -> str:
    """Map a days-past-due value to its bucket label."""
    if pd.isna(dpd):
        return "Current"
    for label, lo, hi in BUCKETS:
        if (lo is None or dpd >= lo) and (hi is None or dpd <= hi):
            return label
    return BUCKET_LABELS[-1]


def assign_buckets(dpd: pd.Series) -> pd.Series:
    """Vectorised bucketing; returns an ordered categorical."""
    edges = [-np.inf] + [hi for _, _, hi in BUCKETS if hi is not None] + [np.inf]
    labels = BUCKET_LABELS
    cut = pd.cut(dpd.fillna(0), bins=edges, labels=labels, right=True,
                 include_lowest=True, ordered=True)
    return cut.astype(pd.CategoricalDtype(categories=labels, ordered=True))


def resolve_due_dates(invoices: pd.DataFrame, default_terms: int = 30) -> tuple[pd.DataFrame, list[str]]:
    """Fill missing due dates from terms, then from invoice date.

    Returns the frame plus human-readable notes describing every fallback applied,
    so the assumption is visible on the report rather than buried in code.
    """
    df = invoices.copy()
    notes: list[str] = []

    if "due_date" not in df.columns:
        df["due_date"] = pd.NaT
    df["due_date"] = pd.to_datetime(df["due_date"], errors="coerce")
    df["due_date_source"] = np.where(df["due_date"].notna(), "file", "")

    missing = df["due_date"].isna()
    if missing.any() and "terms_days" in df.columns:
        terms = pd.to_numeric(df["terms_days"], errors="coerce")
        can_derive = missing & terms.notna() & df["invoice_date"].notna()
        if can_derive.any():
            df.loc[can_derive, "due_date"] = (
                df.loc[can_derive, "invoice_date"]
                + pd.to_timedelta(terms[can_derive], unit="D")
            )
            df.loc[can_derive, "due_date_source"] = "terms"
            notes.append(
                f"{int(can_derive.sum()):,} invoices had no due date; derived as "
                f"invoice date + terms."
            )

    missing = df["due_date"].isna()
    if missing.any():
        can_derive = missing & df["invoice_date"].notna()
        if can_derive.any():
            df.loc[can_derive, "due_date"] = (
                df.loc[can_derive, "invoice_date"] + pd.Timedelta(days=default_terms)
            )
            df.loc[can_derive, "due_date_source"] = f"default net {default_terms}"
            notes.append(
                f"{int(can_derive.sum()):,} invoices had neither due date nor terms; "
                f"assumed Net {default_terms} from the invoice date."
            )
    return df, notes


def resolve_balances(invoices: pd.DataFrame, payments: pd.DataFrame | None
                     ) -> tuple[pd.DataFrame, list[str]]:
    """Ensure an ``open_balance`` exists, deriving it from payments when absent."""
    df = invoices.copy()
    notes: list[str] = []

    if "open_balance" in df.columns and df["open_balance"].notna().any():
        df["open_balance"] = df["open_balance"].fillna(0.0)
        return df, notes

    if payments is not None and not payments.empty and "invoice_no" in payments.columns:
        paid = (payments.dropna(subset=["invoice_no"])
                        .groupby("invoice_no")["payment_amount"].sum())
        df["open_balance"] = (df["invoice_amount"].fillna(0.0)
                              - df["invoice_no"].map(paid).fillna(0.0))
        notes.append("Open balance was not in the file; derived as invoice amount "
                     "less payments applied to each invoice.")
    else:
        df["open_balance"] = df["invoice_amount"].fillna(0.0)
        notes.append("Open balance was not in the file and payments could not be matched "
                     "to invoices; the full invoice amount is treated as outstanding.")
    return df, notes


def build_aging(invoices: pd.DataFrame, as_of: pd.Timestamp,
                payments: pd.DataFrame | None = None,
                default_terms: int = 30,
                open_only: bool = True) -> tuple[pd.DataFrame, list[str]]:
    """Invoice-level aging detail: days past due, bucket, and past-due flag."""
    df, notes = resolve_due_dates(invoices, default_terms=default_terms)
    df, bal_notes = resolve_balances(df, payments)
    notes += bal_notes

    as_of = pd.Timestamp(as_of).normalize()
    df["days_past_due"] = (as_of - df["due_date"]).dt.days
    df["days_outstanding"] = (as_of - df["invoice_date"]).dt.days
    df["aging_bucket"] = assign_buckets(df["days_past_due"])
    df["is_past_due"] = df["days_past_due"] > 0
    df["is_credit"] = df["open_balance"] < 0
    df["as_of_date"] = as_of

    if open_only:
        # Settled invoices carry a zero balance and would otherwise dilute every ratio.
        settled = df["open_balance"].abs() < 0.005
        if settled.any():
            notes.append(f"{int(settled.sum()):,} fully settled invoices excluded from aging.")
            df = df.loc[~settled].copy()

    return df.reset_index(drop=True), notes


def aging_summary(detail: pd.DataFrame, by: str = "customer_id") -> pd.DataFrame:
    """Aging pivot: one row per entity, one column per bucket, plus totals and mix."""
    if detail.empty:
        return pd.DataFrame(columns=[by, *BUCKET_LABELS, "Total", "Past Due", "% Past Due"])

    group = by if by in detail.columns else "customer_id"
    positive = detail.loc[~detail["is_credit"]]

    pivot = (positive.pivot_table(index=group, columns="aging_bucket",
                                  values="open_balance", aggfunc="sum",
                                  observed=False, fill_value=0.0))
    for label in BUCKET_LABELS:
        if label not in pivot.columns:
            pivot[label] = 0.0
    pivot = pivot[BUCKET_LABELS]

    credits = (detail.loc[detail["is_credit"]].groupby(group)["open_balance"].sum())
    pivot["Credits"] = credits.reindex(pivot.index).fillna(0.0)

    pivot["Total"] = pivot[BUCKET_LABELS].sum(axis=1) + pivot["Credits"]
    pivot["Past Due"] = pivot[PAST_DUE_LABELS].sum(axis=1)
    gross = pivot[BUCKET_LABELS].sum(axis=1)
    pivot["% Past Due"] = np.where(gross > 0, pivot["Past Due"] / gross * 100.0, 0.0)

    if "customer_name" in detail.columns and group == "customer_id":
        names = detail.groupby(group)["customer_name"].first()
        pivot.insert(0, "customer_name", names.reindex(pivot.index))

    return pivot.reset_index().sort_values("Total", ascending=False).reset_index(drop=True)


def bucket_totals(detail: pd.DataFrame) -> pd.DataFrame:
    """Portfolio-level total and mix per bucket -- the headline aging table."""
    if detail.empty:
        return pd.DataFrame({"Bucket": BUCKET_LABELS, "Amount": 0.0, "% of AR": 0.0,
                             "Invoices": 0})

    positive = detail.loc[~detail["is_credit"]]
    amounts = (positive.groupby("aging_bucket", observed=False)["open_balance"]
                       .sum().reindex(BUCKET_LABELS).fillna(0.0))
    counts = (positive.groupby("aging_bucket", observed=False)["open_balance"]
                      .size().reindex(BUCKET_LABELS).fillna(0).astype(int))
    total = amounts.sum()
    return pd.DataFrame({
        "Bucket": BUCKET_LABELS,
        "Amount": amounts.to_numpy(),
        "% of AR": (amounts / total * 100.0).to_numpy() if total else np.zeros(len(BUCKET_LABELS)),
        "Invoices": counts.to_numpy(),
    })


def weighted_average_days_delinquent(detail: pd.DataFrame) -> float:
    """ADD: balance-weighted days past due across past-due invoices only.

    Weighting by balance is the point -- a single large 120-day invoice matters far
    more than a dozen small ones a week late.
    """
    past_due = detail.loc[detail["is_past_due"] & ~detail["is_credit"]]
    weight = past_due["open_balance"].sum()
    if weight <= 0:
        return 0.0
    return float((past_due["open_balance"] * past_due["days_past_due"]).sum() / weight)


def weighted_average_days_outstanding(detail: pd.DataFrame) -> float:
    """Balance-weighted age of the whole book, past due or not."""
    open_ar = detail.loc[~detail["is_credit"]]
    weight = open_ar["open_balance"].sum()
    if weight <= 0:
        return 0.0
    return float((open_ar["open_balance"] * open_ar["days_outstanding"]).sum() / weight)
