"""Canonical data model.

Every module downstream of ``mapping`` consumes ONLY the canonical field names
defined here -- never a raw header from a customer's export.  That indirection is
what lets the app absorb an unseen ERP export without code changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DType = Literal["string", "date", "money", "number"]

# Dataset roles.  ``invoices`` is the only one strictly required to produce a report.
ROLES = ("invoices", "payments", "sales", "customers")

ROLE_LABELS = {
    "invoices": "Open Invoices (AR detail)",
    "payments": "Payments / Cash Receipts",
    "sales": "Monthly Sales / Credit Revenue",
    "customers": "Customer Master",
}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    dtype: DType
    required: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)
    help: str = ""

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()


# --------------------------------------------------------------------------------------
# Field definitions.  Alias lists intentionally cover the spellings that real ERP
# exports (SAP, NetSuite, QuickBooks, Sage, Dynamics, Oracle) actually emit.
# --------------------------------------------------------------------------------------

INVOICE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "customer_id", "string", True,
        ("customer id", "customer no", "customer number", "customer code", "cust no",
         "cust id", "cust code", "custno", "account", "account no", "account number",
         "account code", "acct no", "bp code", "business partner", "debtor", "debtor no",
         "debtor code", "kunnr", "payer", "sold to", "customer"),
        "Key that links invoices to payments and the customer master.",
    ),
    FieldSpec(
        "invoice_no", "string", True,
        ("invoice no", "invoice number", "invoice id", "invoice", "inv no", "inv num",
         "invno", "document no", "document number", "doc no", "doc number", "doc id",
         "reference", "reference no", "ref no", "belnr", "transaction no", "trans no",
         "transaction number", "txn no", "txn number", "number", "num",
         "voucher no", "bill no", "bill number"),
        "Unique invoice identifier; also used to match payments.",
    ),
    FieldSpec(
        "invoice_date", "date", True,
        ("invoice date", "inv date", "document date", "doc date", "posting date",
         "post date", "date", "issue date", "issued", "created date", "billing date",
         "bill date", "bldat", "transaction date", "trans date"),
        "Date the invoice was issued.",
    ),
    FieldSpec(
        "due_date", "date", False,
        ("due date", "net due date", "date due", "payment due", "payment due date",
         "maturity date", "maturity", "net due", "duedate", "expected payment date",
         "terms due date"),
        "Date payment was contractually due. If absent it is derived from terms.",
    ),
    FieldSpec(
        "invoice_amount", "money", True,
        ("invoice amount", "invoice amt", "inv amount", "inv amt", "amount", "amt",
         "gross amount", "total", "total amount", "invoice total", "original amount",
         "orig amount", "document amount", "doc amount", "net amount", "value",
         "invoice value", "dmbtr", "billed amount"),
        "Original face value of the invoice.",
    ),
    FieldSpec(
        "open_balance", "money", False,
        ("open balance", "balance", "balance due", "open amount", "open amt", "amt due",
         "amount due", "outstanding", "outstanding amount", "outstanding balance",
         "remaining", "remaining balance", "unpaid", "unpaid amount", "open item",
         "net due amount", "current balance", "openbal", "bal due"),
        "Still-unpaid portion. If absent it is derived from invoice amount less payments.",
    ),
    FieldSpec(
        "terms_days", "number", False,
        ("terms days", "terms", "payment terms", "payment term", "term days", "net days",
         "credit terms", "credit days", "days", "term", "payterm", "zterm"),
        "Credit terms in days (e.g. 30 for Net 30). Used to derive a missing due date.",
    ),
    FieldSpec(
        "paid_status", "string", False,
        ("a/r paid", "ar paid", "paid status", "payment status", "paid", "settled",
         "open status", "invoice status", "status", "cleared"),
        "Says whether the invoice is settled (e.g. Paid / Unpaid). Used to resolve a "
        "missing open balance without guessing.",
    ),
    FieldSpec(
        "customer_name", "string", False,
        ("customer name", "customer full name", "customer", "name", "cust name",
         "account name", "client", "client name", "debtor name", "bp name",
         "business partner name", "company", "company name", "sold to name"),
    ),
    FieldSpec(
        "currency", "string", False,
        ("currency", "curr", "ccy", "currency code", "waers", "cur"),
    ),
    FieldSpec(
        "sales_rep", "string", False,
        ("sales rep", "rep", "salesperson", "sales person", "sales rep name", "owner",
         "collector", "credit analyst", "account manager", "agent"),
    ),
    FieldSpec(
        "segment", "string", False,
        ("segment", "region", "territory", "business unit", "division", "market",
         "customer group", "group", "class", "category", "channel", "country"),
    ),
    FieldSpec(
        "entity", "string", False,
        ("entity", "company code", "legal entity", "org", "organization", "subsidiary",
         "bukrs", "book"),
    ),
)

PAYMENT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "customer_id", "string", True,
        INVOICE_FIELDS[0].aliases,
    ),
    FieldSpec(
        "payment_date", "date", True,
        ("payment date", "paid date", "date paid", "receipt date", "cash receipt date",
         "clearing date", "cleared date", "settlement date", "applied date", "date",
         "posting date", "post date", "transaction date", "deposit date", "augdt"),
    ),
    FieldSpec(
        "payment_amount", "money", True,
        ("payment amount", "payment amt", "paid amount", "amount paid", "amount",
         "amt", "receipt amount", "cash received", "cash applied", "applied amount",
         "settlement amount", "value", "payment", "remittance amount"),
    ),
    FieldSpec(
        "invoice_no", "string", False,
        INVOICE_FIELDS[1].aliases,
        "Invoice the payment was applied to. If absent, payments are FIFO-allocated.",
    ),
    FieldSpec(
        "method", "string", False,
        ("method", "payment method", "pay method", "type", "payment type", "tender",
         "mode", "payment mode", "instrument"),
    ),
)

SALES_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "period", "date", True,
        ("period", "month", "date", "posting period", "fiscal period", "period end",
         "month end", "period start", "year month", "yearmonth", "accounting period",
         "calendar month"),
        "Any date inside the month; it is normalised to month-end.",
    ),
    FieldSpec(
        "credit_sales", "money", True,
        ("credit sales", "sales", "revenue", "net sales", "gross sales", "amount",
         "sales amount", "total sales", "billings", "billed", "invoiced", "invoiced amount",
         "turnover", "net revenue", "total revenue", "value"),
        "Credit sales for the period -- the denominator of DSO.",
    ),
    FieldSpec("customer_id", "string", False, INVOICE_FIELDS[0].aliases),
    FieldSpec("segment", "string", False, INVOICE_FIELDS[10].aliases),
)

CUSTOMER_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("customer_id", "string", True, INVOICE_FIELDS[0].aliases),
    FieldSpec("customer_name", "string", False, INVOICE_FIELDS[7].aliases),
    FieldSpec(
        "credit_limit", "money", False,
        ("credit limit", "limit", "credit line", "line of credit", "approved limit",
         "max credit", "credit ceiling", "exposure limit"),
    ),
    FieldSpec("terms_days", "number", False, INVOICE_FIELDS[6].aliases),
    FieldSpec("segment", "string", False, INVOICE_FIELDS[10].aliases),
    FieldSpec(
        "region", "string", False,
        ("region", "territory", "country", "state", "area", "zone", "district"),
    ),
    FieldSpec("sales_rep", "string", False, INVOICE_FIELDS[9].aliases),
    FieldSpec(
        "risk_rating", "string", False,
        ("risk rating", "risk", "rating", "credit rating", "risk class", "risk score",
         "credit score", "grade", "credit grade"),
    ),
    # Contact details are only used to address collection letters and statements.
    FieldSpec(
        "contact_name", "string", False,
        ("contact name", "contact", "attention", "attn", "contact person",
         "ap contact", "accounts payable contact", "billing contact", "primary contact"),
        "Who collection letters should be addressed to.",
    ),
    FieldSpec(
        "email", "string", False,
        ("email", "e-mail", "email address", "contact email", "billing email",
         "ap email", "mail"),
    ),
    FieldSpec(
        "phone", "string", False,
        ("phone", "telephone", "tel", "phone number", "contact number", "mobile",
         "contact no", "landline"),
    ),
    FieldSpec(
        "address", "string", False,
        ("address", "billing address", "mailing address", "street address",
         "postal address", "address line 1", "location"),
        "Postal address for letters. Left blank, letters show a visible placeholder.",
    ),
)

ROLE_FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "invoices": INVOICE_FIELDS,
    "payments": PAYMENT_FIELDS,
    "sales": SALES_FIELDS,
    "customers": CUSTOMER_FIELDS,
}


def fields_for(role: str) -> tuple[FieldSpec, ...]:
    return ROLE_FIELDS[role]


def required_fields(role: str) -> tuple[str, ...]:
    return tuple(f.name for f in ROLE_FIELDS[role] if f.required)


def spec_for(role: str, name: str) -> FieldSpec | None:
    return next((f for f in ROLE_FIELDS[role] if f.name == name), None)
