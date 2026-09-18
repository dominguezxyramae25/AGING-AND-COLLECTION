"""The collections action queue.

Everything here is derived from invoice dates and balances alone -- deliberately,
so the worklist stays usable when the payment export is incomplete and DSO, CEI,
days-to-pay and on-time rate cannot be trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import aging


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    action: str
    sla_days: int
    severity: str          # ok | warn | crit -- drives the chip colour in the UI

    @property
    def is_action(self) -> bool:
        return self.key != "none"


STAGES: dict[str, Stage] = {
    "none": Stage("none", "No action", "Within terms", 0, "ok"),
    "reminder": Stage("reminder", "Reminder", "Courtesy reminder", 7, "ok"),
    "first_notice": Stage("first_notice", "First notice", "First written notice", 7, "warn"),
    "second_notice": Stage("second_notice", "Second notice",
                           "Second notice and phone call", 5, "warn"),
    "final_demand": Stage("final_demand", "Final demand",
                          "Final demand with response deadline", 5, "crit"),
    "escalate": Stage("escalate", "Escalate",
                      "Hold credit; refer for collection or legal review", 3, "crit"),
}

STAGE_ORDER = ["none", "reminder", "first_notice", "second_notice", "final_demand",
               "escalate"]

# Bucket label -> stage. Covers both bucket schemes; anything past 91-120 escalates.
DEFAULT_POLICY: dict[str, str] = {
    "Current": "none",
    "1-30": "reminder",
    "31-60": "first_notice",
    "61-90": "second_notice",
    "91-120": "final_demand",
    "121-150": "escalate",
    "151+": "escalate",
    "120+": "escalate",
}

# Ledger accounts that exist to balance the books, not to be chased for cash.
CLEARING_PATTERN = re.compile(
    r"adjust|suspense|clearing|write.?off|inter.?company|opening balance", re.I)


def policy_for(scheme: str = aging.DEFAULT_SCHEME,
               overrides: dict[str, str] | None = None) -> dict[str, str]:
    """The bucket-to-stage map for a scheme, with any user overrides applied."""
    policy = {label: DEFAULT_POLICY.get(label, "escalate")
              for label in aging.bucket_labels(scheme)}
    if overrides:
        policy.update({k: v for k, v in overrides.items()
                       if k in policy and v in STAGES})
    return policy


def is_clearing_account(name: object, extra: tuple[str, ...] = ()) -> bool:
    text = "" if name is None else str(name)
    if CLEARING_PATTERN.search(text):
        return True
    return any(e and e.strip().lower() in text.lower() for e in extra)


def build_worklist(detail: pd.DataFrame, risk: pd.DataFrame, as_of: pd.Timestamp,
                   scheme: str = aging.DEFAULT_SCHEME,
                   overrides: dict[str, str] | None = None,
                   exclude: tuple[str, ...] = ()
                   ) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Build the customer action queue and the invoice-level chase list.

    Returns ``(customer_queue, invoice_queue, notes)``. Notes record every account
    that was left out and why, so an empty-looking queue is never a mystery.
    """
    notes: list[str] = []
    empty_cols = ["priority", "customer_id", "customer_name", "stage", "stage_label",
                  "action", "severity", "amount_due_now", "past_due_gross",
                  "past_due_credits", "total_ar", "oldest_dpd", "invoices_to_chase",
                  "risk_band", "risk_score", "response_by"]
    if detail.empty:
        return pd.DataFrame(columns=empty_cols), pd.DataFrame(), notes

    as_of = pd.Timestamp(as_of).normalize()
    policy = policy_for(scheme, overrides)

    # Credits dated past their due date age alongside invoices, so the amount to
    # demand is the NET past-due position. Chasing gross would bill a customer for
    # invoices a credit note has already settled.
    past_due = detail.loc[detail["is_past_due"]].copy()
    chaseable = past_due.loc[past_due["open_balance"] > 0]
    if chaseable.empty:
        notes.append("Nothing is past due; the worklist is empty.")
        return pd.DataFrame(columns=empty_cols), pd.DataFrame(), notes

    past_due["stage"] = (past_due["aging_bucket"].astype(str)
                         .map(policy).fillna("escalate"))
    chaseable = past_due.loc[past_due["open_balance"] > 0]

    # Net position per customer decides whether there is anything to chase at all.
    net = detail.groupby("customer_id")["open_balance"].sum()

    rows = []
    for customer_id, group in past_due.groupby("customer_id"):
        owed = group.loc[group["open_balance"] > 0]
        if owed.empty:
            continue
        name = (group["customer_name"].dropna().iloc[0]
                if "customer_name" in group.columns and group["customer_name"].notna().any()
                else str(customer_id))

        if is_clearing_account(name, exclude) or is_clearing_account(customer_id, exclude):
            notes.append(f"Excluded '{name}' -- looks like a clearing or adjustment "
                         f"account rather than a customer.")
            continue

        net_balance = float(net.get(customer_id, 0.0))
        if net_balance <= 0.005:
            notes.append(f"Excluded '{name}' -- credits and adjustments leave a net "
                         f"balance of {net_balance:,.2f}; there is nothing to collect.")
            continue

        gross = float(owed["open_balance"].sum())
        credits = float(group.loc[group["open_balance"] < 0, "open_balance"].sum())
        due_now = gross + credits
        if due_now <= 0.005:
            notes.append(f"Excluded '{name}' -- {abs(credits):,.2f} of past-due credits "
                         f"offset {gross:,.2f} of invoices, leaving nothing to demand.")
            continue

        # Escalation keys off the oldest UNPAID item -- that is what has gone
        # unanswered. A credit note's age says nothing about customer behaviour.
        oldest = int(owed["days_past_due"].max())
        stage_key = str(owed.loc[owed["days_past_due"].idxmax(), "stage"])
        stage = STAGES[stage_key]

        rows.append({
            "customer_id": customer_id,
            "customer_name": name,
            "stage": stage_key,
            "stage_label": stage.label,
            "action": stage.action,
            "severity": stage.severity,
            "amount_due_now": due_now,
            "past_due_gross": gross,
            "past_due_credits": credits,
            "total_ar": net_balance,
            "oldest_dpd": oldest,
            "invoices_to_chase": int(len(owed)),
            "response_by": as_of + pd.Timedelta(days=stage.sla_days),
        })

    if not rows:
        return pd.DataFrame(columns=empty_cols), pd.DataFrame(), notes

    queue = pd.DataFrame(rows)

    # Reuse the risk ranking's exposure so "worst account" means one thing app-wide.
    if risk is not None and not risk.empty and "exposure" in risk.columns:
        ranked = risk.set_index("customer_id")
        queue["risk_band"] = queue["customer_id"].map(ranked["risk_band"]).astype(str)
        queue["risk_score"] = queue["customer_id"].map(ranked["risk_score"])
        queue["_exposure"] = queue["customer_id"].map(ranked["exposure"]).fillna(0.0)
    else:
        queue["risk_band"] = "Moderate"
        queue["risk_score"] = np.nan
        queue["_exposure"] = queue["amount_due_now"]

    queue = (queue.sort_values(["_exposure", "amount_due_now"], ascending=False)
                  .drop(columns="_exposure").reset_index(drop=True))
    queue.insert(0, "priority", np.arange(1, len(queue) + 1))

    chased = set(queue["customer_id"])
    # The chase list holds only what is actually owed; you do not dun a credit note.
    invoices = chaseable.loc[chaseable["customer_id"].isin(chased)].copy()
    invoices["stage_label"] = invoices["stage"].map(lambda s: STAGES[s].label)
    keep = ["customer_id", "customer_name", "invoice_no", "invoice_date", "due_date",
            "days_past_due", "aging_bucket", "stage_label", "invoice_amount",
            "open_balance"]
    invoices = (invoices[[c for c in keep if c in invoices.columns]]
                .sort_values(["customer_id", "days_past_due"], ascending=[True, False])
                .reset_index(drop=True))

    return queue, invoices, notes


def stage_summary(queue: pd.DataFrame) -> pd.DataFrame:
    """Accounts and money sitting at each escalation stage."""
    if queue is None or queue.empty:
        return pd.DataFrame(columns=["Stage", "Accounts", "Amount"])
    grouped = (queue.groupby("stage")
                    .agg(Accounts=("customer_id", "nunique"),
                         Amount=("amount_due_now", "sum")))
    order = [s for s in STAGE_ORDER if s in grouped.index]
    grouped = grouped.reindex(order)
    grouped.insert(0, "Stage", [STAGES[s].label for s in grouped.index])
    return grouped.reset_index(drop=True)
