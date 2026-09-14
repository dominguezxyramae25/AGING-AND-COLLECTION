"""Data-quality checks.

These run before any metric is displayed. A collections report built on a file with
duplicate invoices or unmatched payments is confidently wrong, which is worse than
being obviously broken.
"""

from __future__ import annotations

import pandas as pd

SEVERITIES = ("Error", "Warning", "Info")


def _issue(severity: str, dataset: str, check: str, count: int, amount: float | None,
           detail: str) -> dict:
    return {"severity": severity, "dataset": dataset, "check": check,
            "rows": int(count), "amount": amount, "detail": detail}


def run_checks(invoices: pd.DataFrame, payments: pd.DataFrame | None,
               sales: pd.DataFrame | None, customers: pd.DataFrame | None,
               as_of: pd.Timestamp) -> pd.DataFrame:
    issues: list[dict] = []
    as_of = pd.Timestamp(as_of)

    if invoices is not None and not invoices.empty:
        inv = invoices

        dupes = inv["invoice_no"].dropna()
        dupe_mask = dupes.duplicated(keep=False)
        if dupe_mask.any():
            dupe_ids = dupes[dupe_mask]
            issues.append(_issue(
                "Error", "invoices", "Duplicate invoice numbers", dupe_mask.sum(), None,
                f"{dupe_ids.nunique():,} invoice numbers appear more than once "
                f"(e.g. {', '.join(map(str, dupe_ids.unique()[:3]))}). Balances may be double-counted."))

        missing_date = inv["invoice_date"].isna()
        if missing_date.any():
            issues.append(_issue(
                "Error", "invoices", "Missing invoice date", missing_date.sum(), None,
                "Invoices without a date cannot be aged and are treated as current."))

        if "due_date" in inv.columns:
            no_due = inv["due_date"].isna()
            if no_due.any():
                issues.append(_issue(
                    "Warning", "invoices", "Missing due date", no_due.sum(), None,
                    "Due date derived from payment terms. See the Assumptions sheet."))

            backwards = inv["due_date"].notna() & inv["invoice_date"].notna() & \
                (inv["due_date"] < inv["invoice_date"])
            if backwards.any():
                issues.append(_issue(
                    "Error", "invoices", "Due date before invoice date", backwards.sum(),
                    None, "These rows will age incorrectly. Check the date column mapping."))

        future = inv["invoice_date"] > as_of
        if future.any():
            issues.append(_issue(
                "Warning", "invoices", "Invoice dated after the as-of date", future.sum(),
                float(inv.loc[future, "invoice_amount"].sum()),
                "Post-dated invoices are included in AR but show negative days outstanding."))

        if "open_balance" in inv.columns:
            over = inv["open_balance"].abs() > inv["invoice_amount"].abs() + 0.01
            if over.any():
                issues.append(_issue(
                    "Error", "invoices", "Open balance exceeds invoice amount", over.sum(),
                    float(inv.loc[over, "open_balance"].sum()),
                    "Usually a mis-mapped amount column or an unapplied credit."))

            credits = inv["open_balance"] < 0
            if credits.any():
                issues.append(_issue(
                    "Info", "invoices", "Credit balances", credits.sum(),
                    float(inv.loc[credits, "open_balance"].sum()),
                    "Reported separately and not netted into aging buckets."))

        if "currency" in inv.columns and inv["currency"].nunique(dropna=True) > 1:
            found = ", ".join(map(str, inv["currency"].dropna().unique()[:6]))
            issues.append(_issue(
                "Error", "invoices", "Mixed currencies", inv["currency"].nunique(dropna=True),
                None, f"Amounts in {found} are being summed without conversion. "
                      "Filter to one currency or convert before relying on totals."))

        no_customer = inv["customer_id"].isna()
        if no_customer.any():
            issues.append(_issue(
                "Error", "invoices", "Missing customer ID", no_customer.sum(), None,
                "These invoices cannot be grouped by customer."))

    if payments is not None and not payments.empty and invoices is not None \
            and "open_balance" in invoices.columns:
        settled = (invoices["open_balance"].abs() < 0.005).mean()
        if settled < 0.02:
            issues.append(_issue(
                "Warning", "invoices", "Export appears to contain open items only",
                len(invoices), None,
                "Almost no settled invoices are present, so paid history is missing. "
                "Current aging is unaffected, but the DSO and CEI trends for earlier "
                "months understate AR and should be read as approximate."))

    if payments is not None and not payments.empty and invoices is not None:
        if "invoice_no" in payments.columns and payments["invoice_no"].notna().any():
            known = set(invoices["invoice_no"].dropna().astype(str))
            refs = payments["invoice_no"].dropna().astype(str)
            orphan = ~refs.isin(known)
            if orphan.any():
                issues.append(_issue(
                    "Warning", "payments", "Payment references unknown invoice",
                    orphan.sum(), float(payments.loc[orphan.reindex(payments.index, fill_value=False),
                                                     "payment_amount"].sum()),
                    "Likely a partially exported invoice file, or a different invoice-number format."))

        unknown_cust = ~payments["customer_id"].isin(set(invoices["customer_id"].dropna()))
        if unknown_cust.any():
            issues.append(_issue(
                "Warning", "payments", "Payment from customer not in invoice file",
                unknown_cust.sum(), float(payments.loc[unknown_cust, "payment_amount"].sum()),
                "These payments are excluded from days-to-pay analysis."))

        negative = payments["payment_amount"] < 0
        if negative.any():
            issues.append(_issue(
                "Info", "payments", "Negative payments", negative.sum(),
                float(payments.loc[negative, "payment_amount"].sum()),
                "Reversals or NSF returns. Included at face value."))

    if sales is not None and not sales.empty:
        negative = sales["credit_sales"] < 0
        if negative.any():
            issues.append(_issue(
                "Warning", "sales", "Negative credit sales", negative.sum(),
                float(sales.loc[negative, "credit_sales"].sum()),
                "Net credit months distort DSO; verify returns are handled as intended."))

        zero = sales["credit_sales"] == 0
        if zero.any():
            issues.append(_issue(
                "Info", "sales", "Zero-sales months", zero.sum(), None,
                "DSO is undefined for these months and is left blank."))

    if customers is not None and not customers.empty and invoices is not None:
        missing = ~invoices["customer_id"].isin(set(customers["customer_id"].dropna()))
        if missing.any():
            issues.append(_issue(
                "Warning", "customers", "Invoice customer not in customer master",
                missing.sum(), float(invoices.loc[missing, "invoice_amount"].sum()),
                "Credit limits and segments are unavailable for these accounts."))

        dupes = customers["customer_id"].dropna().duplicated()
        if dupes.any():
            issues.append(_issue(
                "Error", "customers", "Duplicate customer IDs", dupes.sum(), None,
                "Credit limits may be applied inconsistently."))

    if not issues:
        return pd.DataFrame(columns=["severity", "dataset", "check", "rows", "amount", "detail"])

    df = pd.DataFrame(issues)
    order = {s: i for i, s in enumerate(SEVERITIES)}
    return (df.assign(_o=df["severity"].map(order))
              .sort_values(["_o", "rows"], ascending=[True, False])
              .drop(columns="_o").reset_index(drop=True))


def summarize(issues: pd.DataFrame) -> dict[str, int]:
    if issues is None or issues.empty:
        return {s: 0 for s in SEVERITIES}
    counts = issues["severity"].value_counts()
    return {s: int(counts.get(s, 0)) for s in SEVERITIES}
