"""The analysis orchestrator.

``build_analysis`` runs every module once and returns a single ``Analysis`` object.
The dashboard, the Excel export and the PDF export all read from that one object, so
the three outputs cannot disagree with each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import aging, collections, dso, quality


@dataclass
class Analysis:
    as_of: pd.Timestamp
    detail: pd.DataFrame
    bucket_totals: pd.DataFrame
    customer_aging: pd.DataFrame
    segment_aging: pd.DataFrame
    dso_trend: pd.DataFrame
    dso_customer: pd.DataFrame
    dso_segment: pd.DataFrame
    cei: pd.DataFrame
    matched_payments: pd.DataFrame
    behavior: pd.DataFrame
    payment_trend: pd.DataFrame
    risk: pd.DataFrame
    issues: pd.DataFrame
    kpis: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)


def _safe(value) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return f if np.isfinite(f) else float("nan")


def _latest(df: pd.DataFrame, column: str) -> float:
    if df is None or df.empty or column not in df.columns:
        return float("nan")
    series = df[column].dropna()
    return _safe(series.iloc[-1]) if len(series) else float("nan")


def build_analysis(invoices: pd.DataFrame,
                   payments: pd.DataFrame | None = None,
                   sales: pd.DataFrame | None = None,
                   customers: pd.DataFrame | None = None,
                   as_of: pd.Timestamp | None = None,
                   default_terms: int = 30,
                   days_basis: str = "calendar",
                   lookback_months: int = 12) -> Analysis:
    notes: list[str] = []

    invoices = invoices.copy()
    invoices["invoice_date"] = pd.to_datetime(invoices["invoice_date"], errors="coerce")

    if as_of is None:
        candidates = [invoices["invoice_date"].max()]
        if payments is not None and not payments.empty:
            candidates.append(pd.to_datetime(payments["payment_date"], errors="coerce").max())
        valid = [c for c in candidates if pd.notna(c)]
        as_of = max(valid) if valid else pd.Timestamp.today()
    as_of = pd.Timestamp(as_of).normalize()

    # Enrich invoices with customer-master attributes so every downstream grouping
    # (segment, rep, terms) works even when the invoice export is sparse.
    if customers is not None and not customers.empty:
        enrich = [c for c in ("customer_name", "segment", "region", "sales_rep", "terms_days")
                  if c in customers.columns]
        if enrich:
            lookup = customers.drop_duplicates("customer_id").set_index("customer_id")
            for col in enrich:
                if col not in invoices.columns or invoices[col].isna().all():
                    invoices[col] = invoices["customer_id"].map(lookup[col])
                else:
                    invoices[col] = invoices[col].fillna(invoices["customer_id"].map(lookup[col]))

    detail, aging_notes = aging.build_aging(invoices, as_of, payments,
                                            default_terms=default_terms)
    notes += aging_notes

    buckets = aging.bucket_totals(detail)
    customer_aging = aging.aging_summary(detail, by="customer_id")
    segment_aging = (aging.aging_summary(detail, by="segment")
                     if "segment" in detail.columns and detail["segment"].notna().any()
                     else pd.DataFrame())

    trend, dso_notes = dso.dso_trend(invoices, sales, payments, days_basis, default_terms)
    notes += dso_notes

    dso_cust, cust_notes = dso.dso_by_customer(detail, sales, invoices, as_of, lookback_months)
    notes += cust_notes
    dso_seg = dso.dso_by_segment(detail, dso_cust, customers)

    cei, cei_notes = collections.cei_trend(invoices, sales, payments, default_terms)
    notes += cei_notes

    matched, match_notes = collections.match_payments(invoices, payments, default_terms)
    notes += match_notes
    behavior = collections.payment_behavior(matched)
    pay_trend = collections.payment_trend(matched)

    risk = collections.risk_ranking(detail, behavior, customers)
    issues = quality.run_checks(invoices, payments, sales, customers, as_of)

    # --- headline KPIs ---------------------------------------------------------
    open_ar = detail.loc[~detail["is_credit"], "open_balance"].sum() if not detail.empty else 0.0
    credits = detail.loc[detail["is_credit"], "open_balance"].sum() if not detail.empty else 0.0
    past_due = (detail.loc[detail["is_past_due"] & ~detail["is_credit"], "open_balance"].sum()
                if not detail.empty else 0.0)
    over_90 = (detail.loc[detail["aging_bucket"].isin(["91-120", "120+"]) & ~detail["is_credit"],
                          "open_balance"].sum() if not detail.empty else 0.0)

    kpis = {
        "as_of": as_of,
        "total_ar": float(open_ar + credits),
        "gross_ar": float(open_ar),
        "credit_balances": float(credits),
        "past_due_ar": float(past_due),
        "pct_past_due": float(past_due / open_ar * 100.0) if open_ar else 0.0,
        "over_90_ar": float(over_90),
        "pct_over_90": float(over_90 / open_ar * 100.0) if open_ar else 0.0,
        "open_invoices": int(len(detail)),
        "customers": int(detail["customer_id"].nunique()) if not detail.empty else 0,
        "dso": _latest(trend, "dso"),
        "best_possible_dso": _latest(trend, "best_possible_dso"),
        "delinquent_dso": _latest(trend, "delinquent_dso"),
        "cei": _latest(cei, "cei"),
        "add_days": aging.weighted_average_days_delinquent(detail),
        "ado_days": aging.weighted_average_days_outstanding(detail),
        "avg_days_to_pay": (_safe((behavior["avg_days_to_pay"] * behavior["amount_paid"]).sum()
                                  / behavior["amount_paid"].sum())
                            if not behavior.empty and behavior["amount_paid"].sum() else float("nan")),
        "on_time_rate": (_safe(matched["on_time"].mean() * 100.0)
                         if not matched.empty else float("nan")),
        "over_limit_customers": int(risk["over_limit"].sum()) if not risk.empty else 0,
        "over_limit_exposure": (float(risk.loc[risk["over_limit"], "total_ar"].sum())
                                if not risk.empty else 0.0),
        "top10_concentration": (float(risk.nlargest(10, "total_ar")["total_ar"].sum()
                                      / open_ar * 100.0) if open_ar and not risk.empty else 0.0),
        "critical_accounts": (int((risk["risk_band"] == "Critical").sum())
                              if not risk.empty else 0),
    }

    for label in aging.BUCKET_LABELS:
        row = buckets.loc[buckets["Bucket"] == label]
        kpis[f"bucket_{label}"] = float(row["Amount"].iloc[0]) if not row.empty else 0.0

    settings = {
        "As-of date": as_of.date().isoformat(),
        "Aging basis": "Days past due date",
        "Buckets": " / ".join(aging.BUCKET_LABELS),
        "Default terms when none supplied": f"Net {default_terms}",
        "DSO method": f"Standard -- (AR / credit sales) x days in period ({days_basis})",
        "Customer DSO lookback": f"{lookback_months} months",
        "CEI formula": "(Begin AR + Credit Sales - End AR) / (Begin AR + Credit Sales - End Current AR)",
        "Payment matching": ("Invoice reference where present, otherwise FIFO by customer"
                             if not matched.empty else "Not available"),
    }

    return Analysis(
        as_of=as_of, detail=detail, bucket_totals=buckets, customer_aging=customer_aging,
        segment_aging=segment_aging, dso_trend=trend, dso_customer=dso_cust,
        dso_segment=dso_seg, cei=cei, matched_payments=matched, behavior=behavior,
        payment_trend=pay_trend, risk=risk, issues=issues, kpis=kpis,
        notes=notes, settings=settings,
    )
