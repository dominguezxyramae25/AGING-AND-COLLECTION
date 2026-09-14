"""Payment-to-invoice allocation.

Shared by the DSO/CEI reconstruction and by the payment-behaviour analysis so both
work from an identical view of which cash paid which invoice.
"""

from __future__ import annotations

import pandas as pd

from . import aging


def allocate_payments(invoices: pd.DataFrame, payments: pd.DataFrame | None,
                      default_terms: int = 30) -> tuple[pd.DataFrame, list[str]]:
    """Attach each payment to an invoice.

    Prefers an explicit invoice reference. Where that is missing, payments are
    FIFO-allocated against the customer's oldest invoices -- the convention most
    cash-application teams use.

    Returns one row per (payment, invoice) allocation with an ``allocation`` amount.
    """
    notes: list[str] = []
    if payments is None or payments.empty:
        return pd.DataFrame(), ["No payment data supplied; payment behaviour is unavailable."]

    inv, _ = aging.resolve_due_dates(invoices, default_terms=default_terms)
    inv = (inv[["customer_id", "invoice_no", "invoice_date", "due_date", "invoice_amount"]]
           .dropna(subset=["invoice_no"]).drop_duplicates("invoice_no").copy())
    inv["invoice_no"] = inv["invoice_no"].astype(str)

    pay = payments.copy()
    pay["payment_date"] = pd.to_datetime(pay["payment_date"], errors="coerce")
    pay["payment_amount"] = pd.to_numeric(pay["payment_amount"], errors="coerce").fillna(0.0)
    pay = pay.dropna(subset=["payment_date"])

    has_ref = "invoice_no" in pay.columns and pay["invoice_no"].notna().any()

    direct = pd.DataFrame()
    if has_ref:
        referenced = pay.dropna(subset=["invoice_no"]).copy()
        referenced["invoice_no"] = referenced["invoice_no"].astype(str)
        direct = referenced.merge(inv, on="invoice_no", how="left", suffixes=("", "_inv"))
        orphans = direct["invoice_date"].isna()
        if orphans.any():
            notes.append(f"{int(orphans.sum()):,} payments reference an invoice number that "
                         f"is not in the invoice file; they are excluded from allocation.")
        direct = direct.loc[~orphans].copy()
        direct["allocation"] = direct["payment_amount"]
        direct["match_basis"] = "invoice reference"
        # customer_id from the invoice is authoritative for grouping.
        if "customer_id_inv" in direct.columns:
            direct["customer_id"] = direct["customer_id_inv"].fillna(direct["customer_id"])
        loose = pay.loc[pay["invoice_no"].isna()].copy()
    else:
        loose = pay
        notes.append("Payments carry no invoice reference; they were FIFO-allocated to each "
                     "customer's oldest invoices.")

    fifo_rows: list[dict] = []
    if not loose.empty:
        inv_sorted = inv.sort_values(["customer_id", "invoice_date"]).reset_index(drop=True)
        remaining = inv_sorted["invoice_amount"].fillna(0.0).astype(float).tolist()
        by_customer: dict[object, list[int]] = {}
        for pos, cust in enumerate(inv_sorted["customer_id"]):
            by_customer.setdefault(cust, []).append(pos)
        records = inv_sorted.to_dict("records")

        for p in loose.sort_values("payment_date").itertuples(index=False):
            amount = float(p.payment_amount)
            for pos in by_customer.get(p.customer_id, []):
                if amount <= 0.005:
                    break
                avail = remaining[pos]
                if avail <= 0.005:
                    continue
                applied = min(avail, amount)
                remaining[pos] = avail - applied
                amount -= applied
                rec = records[pos]
                fifo_rows.append({
                    "customer_id": p.customer_id,
                    "invoice_no": rec["invoice_no"],
                    "invoice_date": rec["invoice_date"],
                    "due_date": rec["due_date"],
                    "invoice_amount": rec["invoice_amount"],
                    "payment_date": p.payment_date,
                    "payment_amount": p.payment_amount,
                    "allocation": applied,
                    "match_basis": "FIFO",
                })

    parts = [f for f in (direct, pd.DataFrame(fifo_rows)) if not f.empty]
    if not parts:
        return pd.DataFrame(), notes + ["No payments could be matched to invoices."]

    matched = pd.concat(parts, ignore_index=True)
    matched["days_to_pay"] = (matched["payment_date"] - matched["invoice_date"]).dt.days
    matched["days_late"] = (matched["payment_date"] - matched["due_date"]).dt.days
    matched["on_time"] = matched["days_late"] <= 0
    matched["terms_days"] = (matched["due_date"] - matched["invoice_date"]).dt.days
    return matched.reset_index(drop=True), notes
