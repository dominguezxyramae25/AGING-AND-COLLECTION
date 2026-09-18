"""Collection letters and statements of account.

These documents go to real companies, so two rules shape this module:

1. It **generates** documents. It never sends them, and has no network path.
   Every document is a draft for a person to read, sign and post.
2. The wording states facts and asks for payment. It does not threaten, and it
   makes no legal claim the user has not chosen to make themselves.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from . import worklist
from .export_pdf import GRID, INK, MUTED, data_table, fmt_money, styles

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
NO_ADDRESS = "[address not on file]"


@dataclass
class Sender:
    """The letterhead. Without a company name there is no letter to send."""
    company: str = ""
    address: str = ""
    tax_id: str = ""
    contact_name: str = ""
    contact_title: str = ""
    email: str = ""
    phone: str = ""
    remittance: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.company.strip())

    @classmethod
    def from_dict(cls, data: dict | None) -> "Sender":
        data = data or {}
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: str(v or "") for k, v in data.items() if k in known})


# One place to review tone. Each entry is (subject verb, opening, ask).
STAGE_COPY: dict[str, dict[str, str]] = {
    "reminder": {
        "subject": "Payment reminder",
        "opening": ("This is a courtesy reminder that the invoices listed below have "
                    "passed their due date. If payment is already on its way, please "
                    "disregard this note."),
        "ask": "We would be grateful for settlement, or a note of the expected payment date, by {by}.",
    },
    "first_notice": {
        "subject": "Overdue account",
        "opening": ("Our records show the following invoices remain unpaid past their "
                    "due date. We have not yet received payment or an explanation of "
                    "the delay."),
        "ask": "Please arrange settlement, or tell us when payment will be made, by {by}.",
    },
    "second_notice": {
        "subject": "Second notice - overdue account",
        "opening": ("Despite our earlier notice, the invoices below remain outstanding. "
                    "We would like to resolve this without further correspondence, and "
                    "will follow up by telephone."),
        "ask": ("Please arrange settlement by {by}, or contact us to agree a payment "
                "schedule."),
    },
    "final_demand": {
        "subject": "Final demand - overdue account",
        "opening": ("The invoices listed below are now substantially overdue and remain "
                    "unpaid after previous notices. We are asking for settlement in full."),
        "ask": ("Please settle the full amount by {by}. If there is a dispute over any "
                "invoice, tell us which and why by that date so we can deal with it."),
    },
    "escalate": {
        "subject": "Overdue account - referred for review",
        "opening": ("The invoices below have been outstanding well beyond terms and our "
                    "previous notices have gone unanswered. This account is being "
                    "referred for review, and further credit is on hold."),
        "ask": ("Please contact us by {by} to settle the account or agree a schedule. "
                "We would much rather resolve this directly with you."),
    },
}


def _safe(name: str) -> str:
    return SAFE_NAME.sub("_", str(name)).strip("_")[:60] or "customer"


def _letterhead(sender: Sender, st: dict) -> list:
    lines = [f'<b><font size="13">{sender.company}</font></b>']
    for part in (sender.address, sender.tax_id):
        if part.strip():
            lines.append(part.replace("\n", "<br/>"))
    contact = " &middot; ".join(p for p in (sender.email, sender.phone) if p.strip())
    if contact:
        lines.append(contact)
    head = Paragraph("<br/>".join(lines), st["note"])
    rule = Table([[""]], colWidths=[7.0 * inch], rowHeights=[1])
    rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1, INK)]))
    return [head, Spacer(1, 7), rule, Spacer(1, 16)]


def _addressee(name: str, contact: dict, st: dict) -> Paragraph:
    lines = [f"<b>{name}</b>"]
    if contact.get("contact_name"):
        lines.append(f"Attention: {contact['contact_name']}")
    address = str(contact.get("address") or "").strip()
    lines.append(address.replace("\n", "<br/>") if address
                 else f'<font color="#9c2620">{NO_ADDRESS}</font>')
    return Paragraph("<br/>".join(lines), st["body"])


def _invoice_table(rows: pd.DataFrame, st: dict, symbol: str) -> Table:
    table_df = pd.DataFrame({
        "Invoice": rows["invoice_no"].astype(str),
        "Dated": pd.to_datetime(rows["invoice_date"]).dt.strftime("%d %b %Y"),
        "Due": pd.to_datetime(rows["due_date"]).dt.strftime("%d %b %Y"),
        "Days overdue": rows["days_past_due"].map(lambda v: f"{v:,.0f}"),
        "Amount": rows["open_balance"].map(lambda v: fmt_money(v, symbol)),
    })
    return data_table(table_df, list(table_df.columns),
                      [1.35 * inch, 1.15 * inch, 1.15 * inch, 1.1 * inch, 1.45 * inch], st)


def _totals(label: str, amount: float, st: dict, symbol: str,
            extra: list[tuple[str, float]] | None = None) -> Table:
    rows = []
    for text, value in (extra or []):
        rows.append([Paragraph(text, st["note"]),
                     Paragraph(f'<font size="9">{fmt_money(value, symbol)}</font>',
                               st["note"])])
    rows.append([Paragraph(f"<b>{label}</b>", st["body"]),
                 Paragraph(f'<b><font size="12">{fmt_money(amount, symbol)}</font></b>',
                           st["body"])])
    table = Table(rows, colWidths=[4.05 * inch, 2.15 * inch], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, len(rows) - 1), (-1, len(rows) - 1), 1, INK),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _signature(sender: Sender, st: dict) -> list:
    block = ["Yours sincerely,", "", ""]
    if sender.contact_name:
        block.append(f"<b>{sender.contact_name}</b>")
    if sender.contact_title:
        block.append(sender.contact_title)
    block.append(sender.company)
    return [Spacer(1, 22), Paragraph("<br/>".join(block), st["body"])]


def _draft_note(st: dict) -> Table:
    note = Paragraph(
        "<b>Draft for review.</b> This letter was generated from the ledger and has not "
        "been sent. Check the figures and wording, then sign and send it yourself.",
        st["note"])
    table = Table([[note]], colWidths=[7.0 * inch], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fdf6e3")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#b8860b")),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return table


def _document(title: str) -> tuple[io.BytesIO, SimpleDocTemplate]:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.7 * inch, bottomMargin=0.8 * inch, title=title, author="arcollect")
    return buffer, doc


def _contact_for(customers: pd.DataFrame | None, customer_id) -> dict:
    if customers is None or customers.empty or "customer_id" not in customers.columns:
        return {}
    match = customers.loc[customers["customer_id"] == customer_id]
    if match.empty:
        return {}
    row = match.iloc[0]
    return {f: row[f] for f in ("contact_name", "email", "phone", "address")
            if f in match.columns and pd.notna(row.get(f))}


# --------------------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------------------

def demand_letter(analysis, row: pd.Series, sender: Sender, symbol: str = "$",
                  customers: pd.DataFrame | None = None) -> bytes:
    """One collection letter, worded for the account's escalation stage."""
    st = styles()
    stage = str(row["stage"])
    copy = STAGE_COPY.get(stage, STAGE_COPY["first_notice"])
    name = str(row["customer_name"])
    due_by = pd.Timestamp(row["response_by"]).strftime("%d %B %Y")

    invoices = analysis.worklist_invoices
    mine = invoices.loc[invoices["customer_id"] == row["customer_id"]].copy()
    mine = mine.sort_values("days_past_due", ascending=False)

    buffer, doc = _document(f"{copy['subject']} - {name}")
    story: list = []
    story += _letterhead(sender, st)
    story.append(Paragraph(pd.Timestamp(analysis.as_of).strftime("%d %B %Y"), st["note"]))
    story.append(Spacer(1, 14))
    story.append(_addressee(name, _contact_for(customers, row["customer_id"]), st))
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        f'<b>{copy["subject"]}: {fmt_money(row["amount_due_now"], symbol)} outstanding</b>',
        st["h2"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Dear Sir or Madam,", st["body"]))
    story.append(Spacer(1, 9))
    story.append(Paragraph(copy["opening"], st["body"]))
    story.append(Spacer(1, 13))

    if not mine.empty:
        # The table repeats its header, so let it flow across pages. Forcing it whole
        # onto one page leaves a mostly blank first page on a long account.
        story.append(_invoice_table(mine, st, symbol))
        story.append(Spacer(1, 10))

    credits = float(row.get("past_due_credits") or 0.0)
    # An accountant writes a deduction as "less X", not as a negative amount.
    extra = [("Invoices overdue", float(row.get("past_due_gross") or 0.0)),
             ("Less credits and adjustments applied", abs(credits))] if credits else None
    # The total and the ask must never be separated from each other.
    story.append(KeepTogether([
        _totals("Total now due", float(row["amount_due_now"]), st, symbol, extra),
        Spacer(1, 14),
        Paragraph(copy["ask"].format(by=due_by), st["body"])]))

    if sender.remittance.strip():
        story.append(Spacer(1, 11))
        story.append(Paragraph(
            f'<b>Remittance</b><br/>{sender.remittance.replace(chr(10), "<br/>")}',
            st["note"]))

    story.append(Spacer(1, 11))
    story.append(Paragraph(
        "If any invoice here has already been paid, or you believe it is incorrect, "
        "please tell us which and we will look into it straight away.", st["note"]))
    story += _signature(sender, st)
    story.append(Spacer(1, 18))
    story.append(_draft_note(st))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def statement_of_account(analysis, customer_id, sender: Sender, symbol: str = "$",
                         customers: pd.DataFrame | None = None) -> bytes:
    """Every open item for one customer, aged -- not a demand, a position."""
    st = styles()
    detail = analysis.detail
    mine = detail.loc[detail["customer_id"] == customer_id].copy()
    name = (str(mine["customer_name"].dropna().iloc[0])
            if "customer_name" in mine.columns and mine["customer_name"].notna().any()
            else str(customer_id))
    mine = mine.sort_values("days_past_due", ascending=False)

    buffer, doc = _document(f"Statement of account - {name}")
    story: list = []
    story += _letterhead(sender, st)
    story.append(Paragraph("Statement of Account", st["title"]))
    story.append(Paragraph(
        f"As at {pd.Timestamp(analysis.as_of).strftime('%d %B %Y')}", st["sub"]))
    story.append(_addressee(name, _contact_for(customers, customer_id), st))
    story.append(Spacer(1, 16))

    table_df = pd.DataFrame({
        "Invoice": mine["invoice_no"].astype(str),
        "Dated": pd.to_datetime(mine["invoice_date"]).dt.strftime("%d %b %Y"),
        "Due": pd.to_datetime(mine["due_date"]).dt.strftime("%d %b %Y"),
        "Age": mine["aging_bucket"].astype(str),
        "Days": mine["days_past_due"].map(lambda v: f"{v:,.0f}"),
        "Balance": mine["open_balance"].map(lambda v: fmt_money(v, symbol)),
    })
    story.append(data_table(table_df, list(table_df.columns),
                            [1.2 * inch, 1.05 * inch, 1.05 * inch, 0.95 * inch,
                             0.7 * inch, 1.35 * inch], st))
    story.append(Spacer(1, 10))

    total = float(mine["open_balance"].sum())
    overdue = float(mine.loc[mine["is_past_due"], "open_balance"].sum())
    story.append(_totals("Balance outstanding", total, st, symbol,
                         [("Of which past due", overdue)] if overdue else None))

    if sender.remittance.strip():
        story.append(Spacer(1, 13))
        story.append(Paragraph(
            f'<b>Remittance</b><br/>{sender.remittance.replace(chr(10), "<br/>")}',
            st["note"]))
    story.append(Spacer(1, 16))
    story.append(_draft_note(st))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def letter_pack(analysis, sender: Sender, symbol: str = "$",
                customers: pd.DataFrame | None = None,
                kind: str = "letters") -> bytes:
    """A ZIP holding one PDF per account on the worklist."""
    queue = analysis.worklist
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        if queue is not None and not queue.empty:
            for _, row in queue.iterrows():
                if kind == "statements":
                    data = statement_of_account(analysis, row["customer_id"], sender,
                                                symbol, customers)
                    stem = "Statement"
                else:
                    data = demand_letter(analysis, row, sender, symbol, customers)
                    stem = f"{int(row['priority']):02d}_{row['stage']}"
                zf.writestr(f"{stem}_{_safe(row['customer_name'])}.pdf", data)
        zf.writestr("README.txt",
                    "Drafts generated from the ledger. Nothing has been sent.\n"
                    "Review the figures and wording, then send them yourself.\n")
    archive.seek(0)
    return archive.getvalue()
