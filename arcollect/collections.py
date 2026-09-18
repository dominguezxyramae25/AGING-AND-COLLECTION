"""Collection effectiveness: CEI, payment behaviour, and delinquency risk."""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import aging, allocate
from .dso import monthly_sales, open_balances_by_period
from .util import month_end


def cei_trend(invoices: pd.DataFrame, sales: pd.DataFrame | None,
              payments: pd.DataFrame | None = None,
              default_terms: int = 30) -> tuple[pd.DataFrame, list[str]]:
    """Collection Effectiveness Index by month.

        CEI = (Beginning AR + Credit Sales - Ending Total AR)
              / (Beginning AR + Credit Sales - Ending Current AR) x 100

    It measures what you collected against what was actually collectible, so unlike
    DSO it is not distorted by a change in sales volume. 100% is perfect.
    """
    sales_m, notes = monthly_sales(sales, invoices)
    if sales_m.empty:
        return pd.DataFrame(), notes

    ar, _ = open_balances_by_period(invoices, sales_m["period"], payments, default_terms)
    df = sales_m.merge(ar, on="period", how="left").sort_values("period")

    df["beginning_ar"] = df["total_ar"].shift(1)
    df["ending_ar"] = df["total_ar"]
    df["ending_current_ar"] = df["current_ar"]

    collectible = df["beginning_ar"] + df["credit_sales"] - df["ending_current_ar"]
    collected = df["beginning_ar"] + df["credit_sales"] - df["ending_ar"]

    df["collected"] = collected
    df["collectible"] = collectible
    df["cei"] = np.where(collectible > 0, collected / collectible * 100.0, np.nan)
    # CEI is a percentage of a bounded quantity; values outside 0-100 mean the inputs
    # disagree (e.g. prior-period adjustments), so clip rather than report nonsense.
    df["cei"] = df["cei"].clip(lower=0, upper=100)

    out = df.loc[df["beginning_ar"].notna(),
                 ["period", "beginning_ar", "credit_sales", "ending_ar",
                  "ending_current_ar", "collected", "collectible", "cei"]]
    if out.empty:
        notes.append("CEI needs at least two months of history; only one period was found.")
    return out.reset_index(drop=True), notes


def match_payments(invoices: pd.DataFrame, payments: pd.DataFrame,
                   default_terms: int = 30) -> tuple[pd.DataFrame, list[str]]:
    """Attach each payment to an invoice so days-to-pay can be measured."""
    return allocate.allocate_payments(invoices, payments, default_terms)


def payment_behavior(matched: pd.DataFrame) -> pd.DataFrame:
    """Per-customer days-to-pay, on-time rate, and slippage against terms."""
    if matched is None or matched.empty:
        return pd.DataFrame()

    valid = matched.loc[matched["days_to_pay"].notna()].copy()
    if valid.empty:
        return pd.DataFrame()

    def wavg(group: pd.DataFrame, col: str) -> float:
        w = group["allocation"].abs()
        return float((group[col] * w).sum() / w.sum()) if w.sum() > 0 else float(group[col].mean())

    rows = []
    for customer, group in valid.groupby("customer_id"):
        rows.append({
            "customer_id": customer,
            "payments": int(len(group)),
            "amount_paid": float(group["allocation"].sum()),
            "avg_days_to_pay": wavg(group, "days_to_pay"),
            "median_days_to_pay": float(group["days_to_pay"].median()),
            "avg_terms_days": wavg(group, "terms_days"),
            "avg_days_late": wavg(group, "days_late"),
            "on_time_rate": float(group["on_time"].mean() * 100.0),
        })
    out = pd.DataFrame(rows)
    out["slippage_vs_terms"] = out["avg_days_to_pay"] - out["avg_terms_days"]
    return out.sort_values("amount_paid", ascending=False).reset_index(drop=True)


def payment_trend(matched: pd.DataFrame) -> pd.DataFrame:
    """Days-to-pay and on-time rate by month -- is behaviour improving or decaying?"""
    if matched is None or matched.empty:
        return pd.DataFrame()
    df = matched.loc[matched["days_to_pay"].notna()].copy()
    if df.empty:
        return pd.DataFrame()
    df["period"] = month_end(df["payment_date"])
    return (df.groupby("period", as_index=False)
              .agg(avg_days_to_pay=("days_to_pay", "mean"),
                   on_time_rate=("on_time", lambda s: float(s.mean() * 100.0)),
                   amount_collected=("allocation", "sum"),
                   payments=("allocation", "size"))
              .sort_values("period").reset_index(drop=True))


def _scale(series: pd.Series, cap: float | None = None) -> pd.Series:
    """Scale to 0-100 against a cap, so scores stay comparable between runs."""
    s = pd.to_numeric(series, errors="coerce").fillna(0.0).clip(lower=0)
    if cap is None:
        cap = float(s.max())
    if not cap or cap <= 0:
        return pd.Series(0.0, index=s.index)
    return (s / cap * 100.0).clip(upper=100.0)


def risk_ranking(detail: pd.DataFrame, behavior: pd.DataFrame | None = None,
                 customers: pd.DataFrame | None = None, top_n: int | None = None,
                 net_credits: bool = True,
                 scheme: str = aging.DEFAULT_SCHEME) -> pd.DataFrame:
    """Rank customers by collection risk, showing the drivers behind each score.

    The composite weights five signals. Every component is kept in the output --
    an unexplained score is not actionable for a collector working the list.
    """
    if detail.empty:
        return pd.DataFrame()

    # Follow the same basis as the aging table. Ignoring credits here would rank a
    # clearing account whose debits and credits cancel as the largest exposure.
    open_ar = detail if net_credits else detail.loc[~detail["is_credit"]]
    grp = open_ar.groupby("customer_id")

    out = pd.DataFrame({"customer_id": grp.size().index})
    out["total_ar"] = grp["open_balance"].sum().to_numpy()
    past_due = (open_ar.loc[open_ar["is_past_due"]].groupby("customer_id")["open_balance"]
                .sum().reindex(out["customer_id"]).fillna(0.0))
    out["past_due_ar"] = past_due.to_numpy()
    out["pct_past_due"] = np.where(out["total_ar"] > 0,
                                   out["past_due_ar"] / out["total_ar"] * 100.0, 0.0)
    out["open_invoices"] = grp.size().to_numpy()

    labels = aging.bucket_labels(scheme)
    for label in labels:
        amounts = (open_ar.loc[open_ar["aging_bucket"] == label]
                   .groupby("customer_id")["open_balance"].sum()
                   .reindex(out["customer_id"]).fillna(0.0))
        out[label] = amounts.to_numpy()

    # Everything past the 61-90 band, whatever the scheme calls those buckets.
    over_90_labels = [l for l in labels
                      if l not in ("Current", "1-30", "31-60", "61-90")]
    out["over_90"] = out[over_90_labels].sum(axis=1)
    out["add_days"] = [
        aging.weighted_average_days_delinquent(open_ar.loc[open_ar["customer_id"] == c])
        for c in out["customer_id"]
    ]
    out["oldest_dpd"] = grp["days_past_due"].max().to_numpy()

    total_ar = out["total_ar"].sum()
    out["pct_of_total_ar"] = out["total_ar"] / total_ar * 100.0 if total_ar else 0.0

    if "customer_name" in detail.columns:
        names = detail.groupby("customer_id")["customer_name"].first()
        out.insert(1, "customer_name", out["customer_id"].map(names))

    out["credit_limit"] = np.nan
    if customers is not None and "credit_limit" in customers.columns:
        limits = customers.set_index("customer_id")["credit_limit"]
        out["credit_limit"] = out["customer_id"].map(limits)
    out["limit_utilization"] = np.where(
        out["credit_limit"].notna() & (out["credit_limit"] > 0),
        out["total_ar"] / out["credit_limit"] * 100.0, np.nan)
    out["over_limit"] = out["limit_utilization"] > 100.0

    out["avg_days_to_pay"] = np.nan
    out["on_time_rate"] = np.nan
    out["slippage_vs_terms"] = np.nan
    if behavior is not None and not behavior.empty:
        b = behavior.set_index("customer_id")
        for col in ("avg_days_to_pay", "on_time_rate", "slippage_vs_terms"):
            if col in b.columns:
                out[col] = out["customer_id"].map(b[col])

    # --- composite score -------------------------------------------------------
    s_past_due = out["pct_past_due"].clip(0, 100)
    s_age = _scale(out["add_days"], cap=120.0)
    s_over90 = np.where(out["total_ar"] > 0, out["over_90"] / out["total_ar"] * 100.0, 0.0)
    s_limit = _scale(out["limit_utilization"].fillna(0.0), cap=150.0)
    s_behavior = _scale(out["slippage_vs_terms"].fillna(0.0), cap=60.0)

    out["risk_score"] = (0.30 * s_past_due + 0.25 * s_age + 0.20 * s_over90
                         + 0.15 * s_limit + 0.10 * s_behavior).round(1)
    out["risk_band"] = pd.cut(out["risk_score"], bins=[-0.01, 25, 50, 75, 100],
                              labels=["Low", "Moderate", "High", "Critical"])
    # Exposure is what actually needs working: risk weighted by dollars at stake.
    out["exposure"] = (out["risk_score"] / 100.0 * out["past_due_ar"]).round(2)

    # An account whose debits and credits cancel carries no exposure to work.
    out = out.loc[out["total_ar"].abs() > 0.005].copy()
    out = out.sort_values(["exposure", "past_due_ar"], ascending=False).reset_index(drop=True)
    out.insert(0, "rank", np.arange(1, len(out) + 1))
    return out.head(top_n) if top_n else out
