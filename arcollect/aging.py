"""AR aging: days past due, bucketing, and the aging summary/detail tables."""

from __future__ import annotations

import numpy as np
import pandas as pd

# Bucket schemes. Each entry is ``(label, lower, upper)`` where the bounds are
# inclusive days-past-due and ``None`` means unbounded.
SCHEMES: dict[str, tuple[tuple[str, int | None, int | None], ...]] = {
    # The common US convention.
    "standard": (
        ("Current", None, 0),
        ("1-30", 1, 30),
        ("31-60", 31, 60),
        ("61-90", 61, 90),
        ("91-120", 91, 120),
        ("120+", 121, None),
    ),
    # What QuickBooks' A/R Ageing Summary prints, so its reports tie line for line.
    "quickbooks": (
        ("Current", None, 0),
        ("1-30", 1, 30),
        ("31-60", 31, 60),
        ("61-90", 61, 90),
        ("91-120", 91, 120),
        ("121-150", 121, 150),
        ("151+", 151, None),
    ),
}

DEFAULT_SCHEME = "standard"


def scheme_buckets(scheme: str = DEFAULT_SCHEME):
    return SCHEMES.get(scheme, SCHEMES[DEFAULT_SCHEME])


def bucket_labels(scheme: str = DEFAULT_SCHEME) -> list[str]:
    return [b[0] for b in scheme_buckets(scheme)]


def past_due_labels(scheme: str = DEFAULT_SCHEME) -> list[str]:
    return [b[0] for b in scheme_buckets(scheme) if b[0] != "Current"]


# Kept for callers that only ever use the default scheme.
BUCKETS = SCHEMES[DEFAULT_SCHEME]
BUCKET_LABELS = bucket_labels()
PAST_DUE_LABELS = past_due_labels()


def bucket_of(dpd: float, scheme: str = DEFAULT_SCHEME) -> str:
    """Map a days-past-due value to its bucket label."""
    buckets = scheme_buckets(scheme)
    if pd.isna(dpd):
        return "Current"
    for label, lo, hi in buckets:
        if (lo is None or dpd >= lo) and (hi is None or dpd <= hi):
            return label
    return buckets[-1][0]


def assign_buckets(dpd: pd.Series, scheme: str = DEFAULT_SCHEME) -> pd.Series:
    """Vectorised bucketing; returns an ordered categorical."""
    buckets = scheme_buckets(scheme)
    edges = [-np.inf] + [hi for _, _, hi in buckets if hi is not None] + [np.inf]
    labels = [b[0] for b in buckets]
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
        gaps = df["open_balance"].isna()
        if gaps.any():
            # Rows from a second file that carries no balance column: derive rather
            # than assume zero, which would silently write the invoice off.
            derived = df.loc[gaps, "invoice_amount"].fillna(0.0)
            if (payments is not None and not payments.empty
                    and "invoice_no" in payments.columns):
                paid = (payments.dropna(subset=["invoice_no"])
                                .groupby("invoice_no")["payment_amount"].sum())
                derived = derived - df.loc[gaps, "invoice_no"].map(paid).fillna(0.0)
            df.loc[gaps, "open_balance"] = derived.clip(lower=0.0)
            notes.append(f"{int(gaps.sum()):,} invoices had no open balance in their "
                         f"file; derived from the invoice amount less payments applied.")
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
                open_only: bool = True,
                scheme: str = DEFAULT_SCHEME) -> tuple[pd.DataFrame, list[str]]:
    """Invoice-level aging detail: days past due, bucket, and past-due flag."""
    df, notes = resolve_due_dates(invoices, default_terms=default_terms)
    df, bal_notes = resolve_balances(df, payments)
    notes += bal_notes

    as_of = pd.Timestamp(as_of).normalize()
    df["days_past_due"] = (as_of - df["due_date"]).dt.days
    df["days_outstanding"] = (as_of - df["invoice_date"]).dt.days
    df["aging_bucket"] = assign_buckets(df["days_past_due"], scheme)
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


def aging_summary(detail: pd.DataFrame, by: str = "customer_id",
                  scheme: str = DEFAULT_SCHEME, net_credits: bool = True) -> pd.DataFrame:
    """Aging pivot: one row per entity, one column per bucket, plus totals and mix.

    ``net_credits`` ages credit balances alongside invoices, which is what an ERP
    aging report does. Either way the credit total is reported in its own column so
    it stays visible rather than disappearing into a bucket.
    """
    labels = bucket_labels(scheme)
    pd_labels = past_due_labels(scheme)
    if detail.empty:
        return pd.DataFrame(columns=[by, *labels, "Credits", "Total",
                                     "Past Due", "% Past Due"])

    group = by if by in detail.columns else "customer_id"
    source = detail if net_credits else detail.loc[~detail["is_credit"]]

    pivot = source.pivot_table(index=group, columns="aging_bucket",
                               values="open_balance", aggfunc="sum",
                               observed=False, fill_value=0.0)
    for label in labels:
        if label not in pivot.columns:
            pivot[label] = 0.0
    pivot = pivot[labels]

    credits = detail.loc[detail["is_credit"]].groupby(group)["open_balance"].sum()
    pivot["Credits"] = credits.reindex(pivot.index).fillna(0.0)

    if net_credits:
        # Credits are already inside the buckets; adding them again would double-count.
        pivot["Total"] = pivot[labels].sum(axis=1)
    else:
        pivot["Total"] = pivot[labels].sum(axis=1) + pivot["Credits"]

    pivot["Past Due"] = pivot[pd_labels].sum(axis=1)
    basis = pivot[labels].sum(axis=1)
    pivot["% Past Due"] = np.where(basis != 0, pivot["Past Due"] / basis * 100.0, 0.0)

    if "customer_name" in detail.columns and group == "customer_id":
        names = detail.groupby(group)["customer_name"].first()
        pivot.insert(0, "customer_name", names.reindex(pivot.index))

    return pivot.reset_index().sort_values("Total", ascending=False).reset_index(drop=True)


def bucket_totals(detail: pd.DataFrame, scheme: str = DEFAULT_SCHEME,
                  net_credits: bool = True) -> pd.DataFrame:
    """Portfolio-level total and mix per bucket -- the headline aging table."""
    labels = bucket_labels(scheme)
    if detail.empty:
        return pd.DataFrame({"Bucket": labels, "Amount": 0.0, "% of AR": 0.0,
                             "Invoices": 0})

    source = detail if net_credits else detail.loc[~detail["is_credit"]]
    amounts = (source.groupby("aging_bucket", observed=False)["open_balance"]
                     .sum().reindex(labels).fillna(0.0))
    counts = (source.groupby("aging_bucket", observed=False)["open_balance"]
                    .size().reindex(labels).fillna(0).astype(int))
    total = amounts.sum()
    return pd.DataFrame({
        "Bucket": labels,
        "Amount": amounts.to_numpy(),
        "% of AR": ((amounts / total * 100.0).to_numpy() if total
                    else np.zeros(len(labels))),
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
