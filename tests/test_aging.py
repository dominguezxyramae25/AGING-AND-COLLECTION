"""Aging boundaries and weighted-average math, asserted against hand-computed answers."""

import pandas as pd
import pytest

from arcollect import aging

AS_OF = pd.Timestamp("2026-01-31")


def _invoice(due_offset_days: int, balance: float = 100.0, invoice_no: str = "X"):
    """An invoice whose due date sits ``due_offset_days`` before the as-of date."""
    due = AS_OF - pd.Timedelta(days=due_offset_days)
    return {"customer_id": "C1", "invoice_no": invoice_no,
            "invoice_date": due - pd.Timedelta(days=30), "due_date": due,
            "invoice_amount": balance, "open_balance": balance}


@pytest.mark.parametrize("dpd,expected", [
    (-5, "Current"), (0, "Current"),
    (1, "1-30"), (30, "1-30"),
    (31, "31-60"), (60, "31-60"),
    (61, "61-90"), (90, "61-90"),
    (91, "91-120"), (120, "91-120"),
    (121, "120+"), (900, "120+"),
])
def test_bucket_boundaries(dpd, expected):
    """Every bucket edge is inclusive on both ends; off-by-one here misstates the report."""
    assert aging.bucket_of(dpd) == expected


@pytest.mark.parametrize("dpd,expected", [
    (0, "Current"), (1, "1-30"), (30, "1-30"), (31, "31-60"),
    (60, "31-60"), (61, "61-90"), (90, "61-90"), (91, "91-120"),
    (120, "91-120"), (121, "120+"),
])
def test_vectorised_matches_scalar(dpd, expected):
    series = aging.assign_buckets(pd.Series([dpd]))
    assert series.iloc[0] == expected


def test_build_aging_computes_days_past_due():
    df = pd.DataFrame([_invoice(45, 500.0, "A"), _invoice(-10, 250.0, "B")])
    detail, _ = aging.build_aging(df, AS_OF)
    by_invoice = detail.set_index("invoice_no")
    assert by_invoice.loc["A", "days_past_due"] == 45
    assert by_invoice.loc["A", "aging_bucket"] == "31-60"
    assert bool(by_invoice.loc["A", "is_past_due"]) is True
    assert by_invoice.loc["B", "days_past_due"] == -10
    assert by_invoice.loc["B", "aging_bucket"] == "Current"
    assert bool(by_invoice.loc["B", "is_past_due"]) is False


def test_settled_invoices_are_excluded():
    df = pd.DataFrame([_invoice(45, 500.0, "A"), _invoice(45, 0.0, "PAID")])
    detail, notes = aging.build_aging(df, AS_OF)
    assert set(detail["invoice_no"]) == {"A"}
    assert any("settled" in n for n in notes)


def test_due_date_derived_from_terms():
    df = pd.DataFrame([{
        "customer_id": "C1", "invoice_no": "A",
        "invoice_date": pd.Timestamp("2025-12-01"), "due_date": pd.NaT,
        "invoice_amount": 100.0, "open_balance": 100.0, "terms_days": 45}])
    detail, notes = aging.build_aging(df, AS_OF)
    assert detail["due_date"].iloc[0] == pd.Timestamp("2026-01-15")
    assert detail["days_past_due"].iloc[0] == 16
    assert any("derived" in n for n in notes)


def test_due_date_falls_back_to_default_terms():
    df = pd.DataFrame([{
        "customer_id": "C1", "invoice_no": "A",
        "invoice_date": pd.Timestamp("2025-12-01"), "due_date": pd.NaT,
        "invoice_amount": 100.0, "open_balance": 100.0}])
    detail, notes = aging.build_aging(df, AS_OF, default_terms=30)
    assert detail["due_date"].iloc[0] == pd.Timestamp("2025-12-31")
    assert any("Net 30" in n for n in notes)


def test_open_balance_derived_from_payments():
    invoices = pd.DataFrame([{
        "customer_id": "C1", "invoice_no": "A",
        "invoice_date": pd.Timestamp("2025-12-01"),
        "due_date": pd.Timestamp("2025-12-31"), "invoice_amount": 1000.0}])
    payments = pd.DataFrame([{
        "customer_id": "C1", "invoice_no": "A",
        "payment_date": pd.Timestamp("2026-01-10"), "payment_amount": 400.0}])
    detail, notes = aging.build_aging(invoices, AS_OF, payments)
    assert detail["open_balance"].iloc[0] == pytest.approx(600.0)
    assert any("derived" in n for n in notes)


def test_weighted_average_days_delinquent():
    """ADD is balance-weighted: (900x100 + 100x10) / 1000 = 91.0, not the 55 a
    simple mean would give."""
    df = pd.DataFrame([_invoice(100, 900.0, "BIG"), _invoice(10, 100.0, "SMALL")])
    detail, _ = aging.build_aging(df, AS_OF)
    assert aging.weighted_average_days_delinquent(detail) == pytest.approx(91.0)


def test_add_ignores_current_invoices():
    df = pd.DataFrame([_invoice(50, 100.0, "LATE"), _invoice(-20, 900.0, "CURRENT")])
    detail, _ = aging.build_aging(df, AS_OF)
    assert aging.weighted_average_days_delinquent(detail) == pytest.approx(50.0)


def test_credits_are_not_netted_into_buckets():
    df = pd.DataFrame([_invoice(45, 500.0, "A"), _invoice(45, -200.0, "CM")])
    detail, _ = aging.build_aging(df, AS_OF)
    totals = aging.bucket_totals(detail)
    assert totals.loc[totals["Bucket"] == "31-60", "Amount"].iloc[0] == pytest.approx(500.0)
    summary = aging.aging_summary(detail)
    assert summary["Credits"].iloc[0] == pytest.approx(-200.0)
    assert summary["Total"].iloc[0] == pytest.approx(300.0)


def test_bucket_totals_percentages_sum_to_100():
    df = pd.DataFrame([_invoice(5, 100.0, "A"), _invoice(45, 300.0, "B"),
                       _invoice(200, 600.0, "C")])
    detail, _ = aging.build_aging(df, AS_OF)
    totals = aging.bucket_totals(detail)
    assert totals["% of AR"].sum() == pytest.approx(100.0)
    assert totals["Amount"].sum() == pytest.approx(1000.0)
