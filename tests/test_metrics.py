"""DSO, CEI and payment-behaviour math, asserted against hand-computed answers."""

import numpy as np
import pandas as pd
import pytest

from arcollect import aging, allocate, collections, dso, kpis


def _book():
    """Two months of activity with arithmetic simple enough to verify by hand.

    January: one 10,000 invoice due 31 Jan, nothing paid.
    February: one 20,000 invoice due 31 Mar; the January invoice is paid on 10 Feb.
    """
    invoices = pd.DataFrame([
        {"customer_id": "C1", "invoice_no": "JAN", "invoice_date": pd.Timestamp("2026-01-01"),
         "due_date": pd.Timestamp("2026-01-31"), "invoice_amount": 10_000.0},
        {"customer_id": "C1", "invoice_no": "FEB", "invoice_date": pd.Timestamp("2026-02-01"),
         "due_date": pd.Timestamp("2026-03-31"), "invoice_amount": 20_000.0},
    ])
    payments = pd.DataFrame([
        {"customer_id": "C1", "invoice_no": "JAN",
         "payment_date": pd.Timestamp("2026-02-10"), "payment_amount": 10_000.0},
    ])
    sales = pd.DataFrame([
        {"period": pd.Timestamp("2026-01-31"), "credit_sales": 10_000.0},
        {"period": pd.Timestamp("2026-02-28"), "credit_sales": 20_000.0},
    ])
    return invoices, payments, sales


def test_open_balances_reconstructed_per_period():
    invoices, payments, sales = _book()
    frame, _ = dso.open_balances_by_period(invoices, sales["period"], payments)
    jan, feb = frame.iloc[0], frame.iloc[1]
    # End of January: only the JAN invoice exists, unpaid, and it is due that day.
    assert jan["total_ar"] == pytest.approx(10_000.0)
    assert jan["current_ar"] == pytest.approx(10_000.0)
    # End of February: JAN is paid, FEB is outstanding and not yet due.
    assert feb["total_ar"] == pytest.approx(20_000.0)
    assert feb["current_ar"] == pytest.approx(20_000.0)
    assert feb["past_due_ar"] == pytest.approx(0.0)


def test_standard_dso_formula():
    """February: AR 20,000 / sales 20,000 x 28 days = 28.0 days."""
    invoices, payments, sales = _book()
    trend, _ = dso.dso_trend(invoices, sales, payments, days_basis="calendar")
    feb = trend.loc[trend["period"] == pd.Timestamp("2026-02-28")].iloc[0]
    assert feb["dso"] == pytest.approx(28.0)
    jan = trend.loc[trend["period"] == pd.Timestamp("2026-01-31")].iloc[0]
    assert jan["dso"] == pytest.approx(31.0)


def test_dso_days_basis_30():
    invoices, payments, sales = _book()
    trend, _ = dso.dso_trend(invoices, sales, payments, days_basis="30")
    feb = trend.loc[trend["period"] == pd.Timestamp("2026-02-28")].iloc[0]
    assert feb["dso"] == pytest.approx(30.0)


def test_delinquent_dso_is_the_gap():
    invoices, payments, sales = _book()
    trend, _ = dso.dso_trend(invoices, sales, payments)
    assert np.allclose(trend["delinquent_dso"],
                       trend["dso"] - trend["best_possible_dso"], equal_nan=True)


def test_best_possible_dso_excludes_past_due():
    """A past-due book must have BPDSO strictly below DSO."""
    invoices = pd.DataFrame([
        {"customer_id": "C1", "invoice_no": "OLD", "invoice_date": pd.Timestamp("2026-01-01"),
         "due_date": pd.Timestamp("2026-01-15"), "invoice_amount": 10_000.0},
        {"customer_id": "C1", "invoice_no": "NEW", "invoice_date": pd.Timestamp("2026-02-20"),
         "due_date": pd.Timestamp("2026-03-22"), "invoice_amount": 10_000.0},
    ])
    sales = pd.DataFrame([
        {"period": pd.Timestamp("2026-01-31"), "credit_sales": 10_000.0},
        {"period": pd.Timestamp("2026-02-28"), "credit_sales": 10_000.0}])
    trend, _ = dso.dso_trend(invoices, sales, None)
    feb = trend.loc[trend["period"] == pd.Timestamp("2026-02-28")].iloc[0]
    # 20,000 AR / 10,000 sales x 28 = 56 days; only half is current.
    assert feb["dso"] == pytest.approx(56.0)
    assert feb["best_possible_dso"] == pytest.approx(28.0)
    assert feb["delinquent_dso"] == pytest.approx(28.0)


def test_dso_undefined_when_no_sales():
    invoices, payments, _ = _book()
    sales = pd.DataFrame([
        {"period": pd.Timestamp("2026-01-31"), "credit_sales": 10_000.0},
        {"period": pd.Timestamp("2026-02-28"), "credit_sales": 0.0}])
    trend, notes = dso.dso_trend(invoices, sales, payments)
    feb = trend.loc[trend["period"] == pd.Timestamp("2026-02-28")].iloc[0]
    assert pd.isna(feb["dso"])
    assert any("zero credit sales" in n for n in notes)


def test_monthly_sales_falls_back_to_invoiced_amounts():
    invoices, _, _ = _book()
    frame, notes = dso.monthly_sales(None, invoices)
    assert frame["credit_sales"].tolist() == [10_000.0, 20_000.0]
    assert any("derived from invoiced amounts" in n for n in notes)


def test_cei_formula():
    """February CEI: begin 10,000 + sales 20,000 - end 20,000 = 10,000 collected.
    Collectible = 10,000 + 20,000 - 20,000 current = 10,000. So CEI = 100%."""
    invoices, payments, sales = _book()
    cei, _ = collections.cei_trend(invoices, sales, payments)
    feb = cei.loc[cei["period"] == pd.Timestamp("2026-02-28")].iloc[0]
    assert feb["collected"] == pytest.approx(10_000.0)
    assert feb["collectible"] == pytest.approx(10_000.0)
    assert feb["cei"] == pytest.approx(100.0)


def test_cei_penalises_uncollected_past_due():
    """Nothing is paid, and the opening balance goes past due: CEI must fall to 0."""
    invoices = pd.DataFrame([
        {"customer_id": "C1", "invoice_no": "JAN", "invoice_date": pd.Timestamp("2026-01-01"),
         "due_date": pd.Timestamp("2026-01-31"), "invoice_amount": 10_000.0}])
    sales = pd.DataFrame([
        {"period": pd.Timestamp("2026-01-31"), "credit_sales": 10_000.0},
        {"period": pd.Timestamp("2026-02-28"), "credit_sales": 0.0}])
    cei, _ = collections.cei_trend(invoices, sales, None)
    feb = cei.loc[cei["period"] == pd.Timestamp("2026-02-28")].iloc[0]
    assert feb["cei"] == pytest.approx(0.0)


def test_cei_requires_two_periods():
    invoices = pd.DataFrame([
        {"customer_id": "C1", "invoice_no": "JAN", "invoice_date": pd.Timestamp("2026-01-01"),
         "due_date": pd.Timestamp("2026-01-31"), "invoice_amount": 10_000.0}])
    sales = pd.DataFrame([{"period": pd.Timestamp("2026-01-31"), "credit_sales": 10_000.0}])
    cei, notes = collections.cei_trend(invoices, sales, None)
    assert cei.empty
    assert any("two months" in n for n in notes)


def test_payment_matching_by_invoice_reference():
    invoices, payments, _ = _book()
    matched, _ = allocate.allocate_payments(invoices, payments)
    row = matched.iloc[0]
    assert row["invoice_no"] == "JAN"
    assert row["match_basis"] == "invoice reference"
    assert row["days_to_pay"] == 40      # 1 Jan -> 10 Feb
    assert row["days_late"] == 10        # due 31 Jan
    assert bool(row["on_time"]) is False


def test_fifo_allocation_without_invoice_reference():
    """Unreferenced cash clears the oldest invoice first, splitting across two when
    the payment is larger than the first."""
    invoices = pd.DataFrame([
        {"customer_id": "C1", "invoice_no": "OLD", "invoice_date": pd.Timestamp("2026-01-01"),
         "due_date": pd.Timestamp("2026-01-31"), "invoice_amount": 100.0},
        {"customer_id": "C1", "invoice_no": "NEW", "invoice_date": pd.Timestamp("2026-02-01"),
         "due_date": pd.Timestamp("2026-03-03"), "invoice_amount": 100.0}])
    payments = pd.DataFrame([
        {"customer_id": "C1", "payment_date": pd.Timestamp("2026-02-15"),
         "payment_amount": 150.0}])
    matched, notes = allocate.allocate_payments(invoices, payments)
    by_invoice = matched.set_index("invoice_no")["allocation"]
    assert by_invoice["OLD"] == pytest.approx(100.0)
    assert by_invoice["NEW"] == pytest.approx(50.0)
    assert set(matched["match_basis"]) == {"FIFO"}
    assert any("FIFO" in n for n in notes)


def test_fifo_never_over_allocates():
    invoices = pd.DataFrame([
        {"customer_id": "C1", "invoice_no": "ONLY", "invoice_date": pd.Timestamp("2026-01-01"),
         "due_date": pd.Timestamp("2026-01-31"), "invoice_amount": 100.0}])
    payments = pd.DataFrame([
        {"customer_id": "C1", "payment_date": pd.Timestamp("2026-02-15"),
         "payment_amount": 500.0}])
    matched, _ = allocate.allocate_payments(invoices, payments)
    assert matched["allocation"].sum() == pytest.approx(100.0)


def test_payment_behavior_is_amount_weighted():
    """A large slow payment must outweigh a small fast one:
    (900x40 + 100x10) / 1000 = 37 days, not the 25 a simple mean gives."""
    invoices = pd.DataFrame([
        {"customer_id": "C1", "invoice_no": "BIG", "invoice_date": pd.Timestamp("2026-01-01"),
         "due_date": pd.Timestamp("2026-01-31"), "invoice_amount": 900.0},
        {"customer_id": "C1", "invoice_no": "SMALL", "invoice_date": pd.Timestamp("2026-01-01"),
         "due_date": pd.Timestamp("2026-01-31"), "invoice_amount": 100.0}])
    payments = pd.DataFrame([
        {"customer_id": "C1", "invoice_no": "BIG",
         "payment_date": pd.Timestamp("2026-02-10"), "payment_amount": 900.0},
        {"customer_id": "C1", "invoice_no": "SMALL",
         "payment_date": pd.Timestamp("2026-01-11"), "payment_amount": 100.0}])
    matched, _ = allocate.allocate_payments(invoices, payments)
    behavior = collections.payment_behavior(matched)
    row = behavior.iloc[0]
    assert row["avg_days_to_pay"] == pytest.approx(37.0)
    assert row["on_time_rate"] == pytest.approx(50.0)


def test_risk_score_ranks_worse_accounts_higher():
    as_of = pd.Timestamp("2026-06-30")
    invoices = pd.DataFrame([
        {"customer_id": "BAD", "invoice_no": "B1", "invoice_date": pd.Timestamp("2025-12-01"),
         "due_date": pd.Timestamp("2025-12-31"), "invoice_amount": 10_000.0,
         "open_balance": 10_000.0},
        {"customer_id": "GOOD", "invoice_no": "G1", "invoice_date": pd.Timestamp("2026-06-01"),
         "due_date": pd.Timestamp("2026-07-31"), "invoice_amount": 10_000.0,
         "open_balance": 10_000.0}])
    detail, _ = aging.build_aging(invoices, as_of)
    risk = collections.risk_ranking(detail)
    scores = risk.set_index("customer_id")["risk_score"]
    assert scores["BAD"] > scores["GOOD"]
    assert scores["GOOD"] == pytest.approx(0.0)
    assert risk.iloc[0]["customer_id"] == "BAD"


def test_analysis_totals_reconcile():
    """The aging total and the reconstructed AR at the as-of date must agree --
    if they drift apart, the trend and the current report are telling
    different stories."""
    invoices, payments, sales = _book()
    analysis = kpis.build_analysis(invoices, payments, sales,
                                   as_of=pd.Timestamp("2026-02-28"))
    final_ar = analysis.dso_trend["total_ar"].iloc[-1]
    assert analysis.kpis["gross_ar"] == pytest.approx(final_ar, rel=1e-6)
    assert analysis.kpis["total_ar"] == pytest.approx(20_000.0)
