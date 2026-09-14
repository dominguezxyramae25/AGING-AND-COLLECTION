"""Days Sales Outstanding.

Standard DSO is the headline; Best Possible DSO is computed alongside it because the
gap between the two -- Delinquent DSO -- is the number a collections team can act on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import aging, allocate
from .util import month_end


def monthly_sales(sales: pd.DataFrame | None, invoices: pd.DataFrame | None = None
                  ) -> tuple[pd.DataFrame, list[str]]:
    """Credit sales by month -- the DSO denominator.

    Falls back to invoiced amounts when no sales file is supplied, which is a close
    proxy for credit sales in most AR-driven businesses.
    """
    notes: list[str] = []
    if sales is not None and not sales.empty and "credit_sales" in sales.columns:
        df = sales.copy()
        df["period"] = month_end(df["period"])
        out = (df.dropna(subset=["period"])
                 .groupby("period", as_index=False)["credit_sales"].sum()
                 .sort_values("period").reset_index(drop=True))
        return out, notes

    if invoices is None or invoices.empty:
        return pd.DataFrame(columns=["period", "credit_sales"]), [
            "No sales data and no invoices available; DSO cannot be calculated."]

    df = invoices.copy()
    df["period"] = month_end(df["invoice_date"])
    out = (df.dropna(subset=["period"])
             .groupby("period", as_index=False)["invoice_amount"].sum()
             .rename(columns={"invoice_amount": "credit_sales"})
             .sort_values("period").reset_index(drop=True))
    notes.append("No monthly sales file supplied; credit sales were derived from "
                 "invoiced amounts by month.")
    return out, notes


def open_balances_by_period(invoices: pd.DataFrame, periods: pd.Series,
                            payments: pd.DataFrame | None = None,
                            default_terms: int = 30) -> tuple[pd.DataFrame, list[str]]:
    """Per-invoice open balance at each month end.

    This is the backbone of both the DSO trend and CEI: it replays invoice and payment
    activity so that, at any month end, we know not just the AR total but how much of
    it was current versus past due at that moment.
    """
    inv, notes = aging.resolve_due_dates(invoices.copy(), default_terms=default_terms)
    inv["invoice_date"] = pd.to_datetime(inv["invoice_date"], errors="coerce")
    inv = inv.dropna(subset=["invoice_date"]).copy()
    inv["invoice_no"] = inv["invoice_no"].astype(str)
    inv["invoice_amount"] = pd.to_numeric(inv["invoice_amount"], errors="coerce").fillna(0.0)

    periods = pd.to_datetime(pd.Series(list(periods))).sort_values().reset_index(drop=True)

    # Cumulative cash applied to each invoice as at each period end.
    matched, alloc_notes = allocate.allocate_payments(inv, payments, default_terms)
    if matched is not None and not matched.empty:
        notes += [n for n in alloc_notes if "FIFO" in n]
        m = matched.copy()
        m["period"] = month_end(m["payment_date"])
        grid = (m.pivot_table(index="invoice_no", columns="period", values="allocation",
                              aggfunc="sum", fill_value=0.0))
        # Include every reporting period, then accumulate left to right.
        grid = grid.reindex(columns=sorted(set(grid.columns) | set(periods)), fill_value=0.0)
        cumulative = grid.cumsum(axis=1)[list(periods)]
    else:
        cumulative = pd.DataFrame(0.0, index=inv["invoice_no"], columns=list(periods))

    paid = cumulative.reindex(inv["invoice_no"]).fillna(0.0)

    rows = []
    amounts = inv["invoice_amount"].to_numpy()
    for period in periods:
        period = pd.Timestamp(period)
        issued = (inv["invoice_date"] <= period).to_numpy()
        # An invoice cannot be over-collected; cash beyond its face value belongs to
        # another document and must not create a negative balance here.
        open_bal = np.clip(amounts - paid[period].to_numpy(), 0.0, None)
        open_bal = np.where(issued, open_bal, 0.0)

        dpd_then = (period - inv["due_date"]).dt.days.to_numpy()
        is_current = (dpd_then <= 0) | np.isnan(dpd_then)

        rows.append({
            "period": period,
            "total_ar": float(open_bal.sum()),
            "current_ar": float(open_bal[is_current].sum()),
            "past_due_ar": float(open_bal[~is_current].sum()),
            "billed_to_date": float(amounts[issued].sum()),
        })
    return pd.DataFrame(rows), notes


def ar_by_month(invoices: pd.DataFrame, periods: pd.Series,
                payments: pd.DataFrame | None = None,
                default_terms: int = 30) -> pd.DataFrame:
    """Backwards-compatible wrapper returning only the reconstructed balances."""
    frame, _ = open_balances_by_period(invoices, periods, payments, default_terms)
    return frame


def dso_trend(invoices: pd.DataFrame, sales: pd.DataFrame | None,
              payments: pd.DataFrame | None = None,
              days_basis: str = "calendar",
              default_terms: int = 30) -> tuple[pd.DataFrame, list[str]]:
    """Monthly standard DSO, Best Possible DSO and Delinquent DSO.

    ``days_basis`` is ``"calendar"`` (actual days in the month) or ``"30"``.
    """
    sales_m, notes = monthly_sales(sales, invoices)
    if sales_m.empty:
        return pd.DataFrame(), notes

    ar, ar_notes = open_balances_by_period(invoices, sales_m["period"], payments,
                                          default_terms)
    notes += ar_notes
    df = sales_m.merge(ar, on="period", how="left")

    df["days_in_period"] = (df["period"].dt.days_in_month.astype(float)
                            if days_basis == "calendar" else 30.0)

    denom = df["credit_sales"].replace(0, np.nan)
    df["dso"] = df["total_ar"] / denom * df["days_in_period"]
    df["best_possible_dso"] = df["current_ar"] / denom * df["days_in_period"]
    df["delinquent_dso"] = df["dso"] - df["best_possible_dso"]

    for col in ("dso", "best_possible_dso", "delinquent_dso"):
        df[col] = df[col].replace([np.inf, -np.inf], np.nan)

    if len(df) > 1:
        notes.append("The earliest period in the trend is understated: there is no "
                     "invoice history before it to carry an opening AR balance.")

    if df["credit_sales"].le(0).any():
        notes.append("Months with zero credit sales show no DSO -- the ratio is undefined.")

    return df.reset_index(drop=True), notes


def dso_by_customer(detail: pd.DataFrame, sales: pd.DataFrame | None,
                    invoices: pd.DataFrame, as_of: pd.Timestamp,
                    lookback_months: int = 12) -> tuple[pd.DataFrame, list[str]]:
    """Per-customer DSO using each customer's own credit sales over the lookback.

    Uses the sales file when it identifies customers; otherwise annualises each
    customer's invoiced volume, which is always available.
    """
    notes: list[str] = []
    as_of = pd.Timestamp(as_of)
    window_start = as_of - pd.DateOffset(months=lookback_months)

    if (sales is not None and not sales.empty
            and "customer_id" in sales.columns and sales["customer_id"].notna().any()):
        s = sales.copy()
        s["period"] = month_end(s["period"])
        window = s.loc[s["period"].between(window_start, as_of)]
        cust_sales = window.groupby("customer_id")["credit_sales"].sum()
        months = max(window["period"].nunique(), 1)
    else:
        inv = invoices.copy()
        inv["invoice_date"] = pd.to_datetime(inv["invoice_date"], errors="coerce")
        window = inv.loc[inv["invoice_date"].between(window_start, as_of)]
        cust_sales = window.groupby("customer_id")["invoice_amount"].sum()
        months = max(month_end(window["invoice_date"]).nunique(), 1)
        notes.append("Customer-level DSO uses invoiced amounts as the sales proxy "
                     "(the sales file does not identify customers).")

    ar = detail.groupby("customer_id")["open_balance"].sum()
    current = (detail.loc[~detail["is_past_due"]].groupby("customer_id")["open_balance"].sum())

    out = pd.DataFrame({"customer_id": ar.index, "ar": ar.to_numpy()})
    out["current_ar"] = out["customer_id"].map(current).fillna(0.0)
    out["credit_sales"] = out["customer_id"].map(cust_sales).fillna(0.0)
    out["days_in_window"] = months * 30.0

    denom = out["credit_sales"].replace(0, np.nan)
    out["dso"] = out["ar"] / denom * out["days_in_window"]
    out["best_possible_dso"] = out["current_ar"] / denom * out["days_in_window"]
    out["delinquent_dso"] = out["dso"] - out["best_possible_dso"]
    out[["dso", "best_possible_dso", "delinquent_dso"]] = (
        out[["dso", "best_possible_dso", "delinquent_dso"]]
        .replace([np.inf, -np.inf], np.nan))

    if "customer_name" in detail.columns:
        names = detail.groupby("customer_id")["customer_name"].first()
        out.insert(1, "customer_name", out["customer_id"].map(names))

    return out.sort_values("ar", ascending=False).reset_index(drop=True), notes


def dso_by_segment(detail: pd.DataFrame, customer_dso: pd.DataFrame,
                   customers: pd.DataFrame | None) -> pd.DataFrame:
    """Roll customer DSO up to segment. DSO is a ratio, so aggregate the components."""
    df = customer_dso.copy()

    segment = None
    if customers is not None and "segment" in customers.columns:
        segment = customers.set_index("customer_id")["segment"]
    elif "segment" in detail.columns:
        segment = detail.groupby("customer_id")["segment"].first()

    if segment is None:
        return pd.DataFrame()

    df["segment"] = df["customer_id"].map(segment).fillna("Unassigned")
    grouped = df.groupby("segment", as_index=False).agg(
        ar=("ar", "sum"), current_ar=("current_ar", "sum"),
        credit_sales=("credit_sales", "sum"), customers=("customer_id", "nunique"),
        days_in_window=("days_in_window", "max"))

    denom = grouped["credit_sales"].replace(0, np.nan)
    grouped["dso"] = grouped["ar"] / denom * grouped["days_in_window"]
    grouped["best_possible_dso"] = grouped["current_ar"] / denom * grouped["days_in_window"]
    grouped["delinquent_dso"] = grouped["dso"] - grouped["best_possible_dso"]
    return grouped.sort_values("ar", ascending=False).reset_index(drop=True)
