"""Generate a realistic four-file AR dataset for demo and testing.

Deliberately messy: headers use ERP-style spellings rather than canonical names, money
is formatted as text with parentheses for credits, there are title rows above the
grid, and a handful of data-quality defects are seeded so the Data Quality tab has
something real to find.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

SEGMENTS = ["Enterprise", "Mid-Market", "SMB", "Public Sector"]
REGIONS = ["Northeast", "Southeast", "Midwest", "West"]
REPS = ["A. Reyes", "B. Okafor", "C. Lindqvist", "D. Navarro", "E. Tanaka"]
METHODS = ["ACH", "Wire", "Check", "Card"]
TERMS_CHOICES = [15, 30, 30, 30, 45, 60]

RNG = np.random.default_rng(20240914)


def _money(value: float) -> str:
    """Format like an ERP text export: thousands separators, parens for negatives."""
    if value < 0:
        return f"({abs(value):,.2f})"
    return f"{value:,.2f}"


def build(n_customers: int = 60, months: int = 18, as_of: str = "2026-08-31"):
    as_of_ts = pd.Timestamp(as_of)
    start = (as_of_ts - pd.DateOffset(months=months - 1)).replace(day=1)

    # ---- customers ----------------------------------------------------------
    customers = pd.DataFrame({
        "customer_id": [f"C{1000 + i}" for i in range(n_customers)],
        "customer_name": [f"{n} {s}" for n, s in zip(
            RNG.choice(["Apex", "Borealis", "Cascade", "Delta", "Everline", "Fairmont",
                        "Granite", "Harbor", "Ironwood", "Juniper", "Keystone", "Lakeshore",
                        "Meridian", "Northgate", "Orchard", "Pinnacle", "Quarry", "Redwood",
                        "Summit", "Trailhead", "Umbra", "Vantage", "Westford", "Yarrow"],
                       n_customers),
            RNG.choice(["Industries", "Logistics", "Health", "Foods", "Systems", "Partners",
                        "Manufacturing", "Retail Group", "Technologies", "Holdings"],
                       n_customers))],
        "segment": RNG.choice(SEGMENTS, n_customers, p=[0.18, 0.32, 0.40, 0.10]),
        "region": RNG.choice(REGIONS, n_customers),
        "sales_rep": RNG.choice(REPS, n_customers),
        "terms_days": RNG.choice(TERMS_CHOICES, n_customers),
        "risk_rating": RNG.choice(["A", "B", "C", "D"], n_customers, p=[0.3, 0.4, 0.2, 0.1]),
    })
    # Each customer has a payment personality that persists across their invoices.
    # Most accounts pay near terms; a long tail pays late. Negative = pays early.
    lateness = RNG.gamma(shape=1.3, scale=5.5, size=n_customers) - 4.0
    scale = RNG.lognormal(mean=9.2, sigma=0.85, size=n_customers)
    customers["credit_limit"] = np.round(scale * RNG.uniform(9.0, 22.0, n_customers), -3)

    # ---- invoices -----------------------------------------------------------
    periods = pd.date_range(start, as_of_ts, freq="ME")
    rows, inv_seq = [], 50000
    for p_idx, period in enumerate(periods):
        # Gentle growth plus seasonality so the DSO trend has something to show.
        volume = 1.0 + 0.015 * p_idx + 0.12 * np.sin(p_idx / 2.0)
        for c_idx, cust in customers.iterrows():
            if RNG.random() > 0.72:
                continue
            for _ in range(RNG.integers(1, 4)):
                inv_seq += 1
                day = int(RNG.integers(1, period.days_in_month + 1))
                inv_date = period.replace(day=day)
                if inv_date > as_of_ts:
                    continue
                amount = round(float(scale[c_idx] * RNG.uniform(0.35, 2.4) * volume), 2)
                terms = int(cust["terms_days"])
                rows.append({
                    "customer_id": cust["customer_id"],
                    "customer_name": cust["customer_name"],
                    "invoice_no": f"INV-{inv_seq}",
                    "invoice_date": inv_date,
                    "due_date": inv_date + pd.Timedelta(days=terms),
                    "invoice_amount": amount,
                    "terms_days": terms,
                    "sales_rep": cust["sales_rep"],
                    "segment": cust["segment"],
                    "lateness_bias": lateness[c_idx],
                })
    invoices = pd.DataFrame(rows)

    # ---- payments -----------------------------------------------------------
    pay_rows = []
    for inv in invoices.itertuples(index=False):
        age = (as_of_ts - inv.invoice_date).days
        lag = int(max(1, RNG.normal(inv.terms_days + inv.lateness_bias, 7)))
        # Older invoices are mostly settled; recent ones mostly are not.
        settle_p = 0.995 if age > 150 else (0.97 if age > 90 else (0.88 if age > 45 else 0.55))
        if RNG.random() > settle_p or lag > age:
            continue
        pay_date = inv.invoice_date + pd.Timedelta(days=lag)
        if pay_date > as_of_ts:
            continue
        if RNG.random() < 0.12:  # partial payment, then the remainder later
            first = round(inv.invoice_amount * RNG.uniform(0.3, 0.7), 2)
            pay_rows.append((inv.customer_id, inv.invoice_no, pay_date, first))
            second_date = pay_date + pd.Timedelta(days=int(RNG.integers(5, 40)))
            if second_date <= as_of_ts:
                pay_rows.append((inv.customer_id, inv.invoice_no, second_date,
                                 round(inv.invoice_amount - first, 2)))
        else:
            pay_rows.append((inv.customer_id, inv.invoice_no, pay_date, inv.invoice_amount))

    payments = pd.DataFrame(pay_rows, columns=["customer_id", "invoice_no",
                                               "payment_date", "payment_amount"])
    payments["method"] = RNG.choice(METHODS, len(payments), p=[0.45, 0.2, 0.25, 0.1])

    paid = payments.groupby("invoice_no")["payment_amount"].sum()
    invoices["open_balance"] = (invoices["invoice_amount"]
                                - invoices["invoice_no"].map(paid).fillna(0.0)).round(2)
    invoices["open_balance"] = invoices["open_balance"].clip(lower=0.0)

    # ---- monthly sales ------------------------------------------------------
    sales = (invoices.assign(period=invoices["invoice_date"].dt.to_period("M")
                             .dt.to_timestamp("M"))
             .groupby("period", as_index=False)["invoice_amount"].sum()
             .rename(columns={"invoice_amount": "credit_sales"}))

    # ---- seeded defects -----------------------------------------------------
    open_inv = invoices.loc[invoices["open_balance"] > 0]
    # A duplicated invoice row (double-counted balance).
    dupe = open_inv.sample(2, random_state=7)
    # Missing due dates, to exercise the terms-derivation fallback.
    missing_due_idx = open_inv.sample(12, random_state=11).index
    invoices.loc[missing_due_idx, "due_date"] = pd.NaT
    # A pair of unapplied credit memos.
    credits = open_inv.sample(3, random_state=13).copy()
    credits["invoice_no"] = credits["invoice_no"] + "-CM"
    credits["invoice_amount"] = -credits["invoice_amount"].abs().round(2) * 0.25
    credits["open_balance"] = credits["invoice_amount"]
    # A payment referencing an invoice that is not in the export.
    orphan = pd.DataFrame([{
        "customer_id": customers["customer_id"].iloc[0], "invoice_no": "INV-999999",
        "payment_date": as_of_ts - pd.Timedelta(days=9), "payment_amount": 4250.00,
        "method": "ACH"}])

    invoices = pd.concat([invoices, dupe, credits], ignore_index=True)
    payments = pd.concat([payments, orphan], ignore_index=True)

    invoices = invoices.drop(columns=["lateness_bias"])
    return invoices, payments, sales, customers


def write(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    invoices, payments, sales, customers = build()
    written: list[Path] = []

    # Invoices: Excel, with two junk title rows above the header and text money.
    inv_out = pd.DataFrame({
        "Customer No": invoices["customer_id"],
        "Customer Name": invoices["customer_name"],
        "Document No": invoices["invoice_no"],
        "Doc Date": invoices["invoice_date"].dt.strftime("%m/%d/%Y"),
        "Net Due Date": invoices["due_date"].dt.strftime("%m/%d/%Y"),
        "Invoice Amt": invoices["invoice_amount"].map(_money),
        "Balance Due": invoices["open_balance"].map(_money),
        "Payment Terms": invoices["terms_days"],
        "Salesperson": invoices["sales_rep"],
        "Customer Group": invoices["segment"],
    })
    path = out_dir / "01_open_invoices.xlsx"
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        title = pd.DataFrame([["Northwind Manufacturing Group"], ["A/R Open Item Report"]])
        title.to_excel(writer, sheet_name="AR Detail", index=False, header=False)
        inv_out.to_excel(writer, sheet_name="AR Detail", index=False, startrow=2)
    written.append(path)

    path = out_dir / "02_payments.csv"
    pd.DataFrame({
        "Cust No": payments["customer_id"],
        "Invoice Number": payments["invoice_no"],
        "Clearing Date": payments["payment_date"].dt.strftime("%Y-%m-%d"),
        "Amount Paid": payments["payment_amount"].round(2),
        "Payment Method": payments["method"],
    }).to_csv(path, index=False)
    written.append(path)

    path = out_dir / "03_monthly_sales.csv"
    pd.DataFrame({
        "Month End": sales["period"].dt.strftime("%Y-%m-%d"),
        "Net Credit Sales": sales["credit_sales"].round(2),
    }).to_csv(path, index=False)
    written.append(path)

    path = out_dir / "04_customer_master.xlsx"
    pd.DataFrame({
        "Account Code": customers["customer_id"],
        "Account Name": customers["customer_name"],
        "Credit Limit": customers["credit_limit"],
        "Terms (Days)": customers["terms_days"],
        "Customer Group": customers["segment"],
        "Territory": customers["region"],
        "Account Manager": customers["sales_rep"],
        "Credit Rating": customers["risk_rating"],
    }).to_excel(path, index=False, sheet_name="Customers")
    written.append(path)
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate sample AR data files.")
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    args = ap.parse_args()
    for p in write(Path(args.out)):
        print(f"wrote {p}")
