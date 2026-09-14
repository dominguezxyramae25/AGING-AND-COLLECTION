"""Header detection, fuzzy mapping and type coercion.

This layer is what lets an unseen ERP export work without code changes, so it is
tested against the header spellings real systems emit.
"""

import io

import pandas as pd
import pytest

from arcollect import ingest, mapping
from arcollect.schema import required_fields


# ---- header sniffing -----------------------------------------------------------------

def test_header_row_found_below_title_rows():
    csv = ("Northwind Manufacturing\nA/R Open Item Report\n\n"
           "Customer No,Invoice No,Doc Date,Amount\n"
           "C1,INV-1,2026-01-01,100.00\n")
    df = ingest.read_table(io.StringIO(csv), filename="x.csv")
    assert list(df.columns) == ["Customer No", "Invoice No", "Doc Date", "Amount"]
    assert len(df) == 1


def test_plain_header_in_first_row():
    csv = "Customer,Invoice,Amount\nC1,INV-1,100\n"
    df = ingest.read_table(io.StringIO(csv), filename="x.csv")
    assert list(df.columns) == ["Customer", "Invoice", "Amount"]


def test_duplicate_headers_are_disambiguated():
    csv = "Amount,Amount,Date\n1,2,2026-01-01\n"
    df = ingest.read_table(io.StringIO(csv), filename="x.csv")
    assert list(df.columns) == ["Amount", "Amount (1)", "Date"]


# ---- coercion ------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1,234.50", 1234.50),
    ("$1,234.50", 1234.50),
    ("(500.00)", -500.00),        # accounting negative
    ("$(1,200.00)", -1200.00),
    ("1200-", -1200.00),          # trailing-minus convention
    ("0", 0.0),
])
def test_money_coercion(raw, expected):
    assert ingest.to_money(pd.Series([raw])).iloc[0] == pytest.approx(expected)


def test_money_coercion_handles_blanks():
    out = ingest.to_money(pd.Series(["", None, "-", "12.00"]))
    assert pd.isna(out.iloc[0]) and pd.isna(out.iloc[1]) and pd.isna(out.iloc[2])
    assert out.iloc[3] == pytest.approx(12.0)


def test_date_coercion_month_first_and_day_first():
    assert ingest.to_date(pd.Series(["03/04/2026"])).iloc[0] == pd.Timestamp("2026-03-04")
    assert (ingest.to_date(pd.Series(["03/04/2026"]), dayfirst=True).iloc[0]
            == pd.Timestamp("2026-04-03"))


def test_excel_serial_dates_are_recognised():
    """Excel exports frequently emit bare serial numbers instead of dates."""
    out = ingest.to_date(pd.Series([45000]))
    assert out.iloc[0] == pd.Timestamp("2023-03-15")


def test_string_ids_survive_excel_float_mangling():
    """Excel turns a numeric customer code into 1024.0; it must match '1024'."""
    assert ingest.to_string(pd.Series([1024.0])).iloc[0] == "1024"


# ---- fuzzy mapping -------------------------------------------------------------------

@pytest.mark.parametrize("header,field", [
    ("Customer No", "customer_id"), ("Cust #", "customer_id"),
    ("Account Number", "customer_id"), ("Debtor Code", "customer_id"),
    ("Document No", "invoice_no"), ("Invoice Number", "invoice_no"),
    ("Doc Date", "invoice_date"), ("Posting Date", "invoice_date"),
    ("Net Due Date", "due_date"), ("Maturity Date", "due_date"),
    ("Invoice Amt", "invoice_amount"), ("Gross Amount", "invoice_amount"),
    ("Balance Due", "open_balance"), ("Open Amt", "open_balance"),
    ("Amount Due", "open_balance"), ("Payment Terms", "terms_days"),
])
def test_invoice_header_aliases_map(header, field):
    result = mapping.auto_map([header], "invoices")
    assert result[field] == header, f"{header!r} should map to {field}"


def test_auto_map_never_assigns_one_header_twice():
    headers = ["Customer No", "Invoice No", "Doc Date", "Net Due Date",
               "Invoice Amt", "Balance Due"]
    result = mapping.auto_map(headers, "invoices")
    used = [h for h in result.values() if h]
    assert len(used) == len(set(used))


def test_unrelated_headers_are_left_unmapped():
    result = mapping.auto_map(["Warehouse Bin", "Colour", "Shoe Size"], "invoices")
    assert all(v is None for v in result.values())


def test_role_detection_across_realistic_exports():
    cases = {
        "invoices": ["Customer No", "Document No", "Doc Date", "Net Due Date",
                     "Invoice Amt", "Balance Due"],
        "payments": ["Cust No", "Invoice Number", "Clearing Date", "Amount Paid"],
        "sales": ["Month End", "Net Credit Sales"],
        "customers": ["Account Code", "Account Name", "Credit Limit", "Terms (Days)"],
    }
    for expected_role, headers in cases.items():
        assert mapping.guess_role(headers) == expected_role


def test_role_detection_respects_exclusions():
    """Two files can look alike; an already-claimed role must not be reused."""
    headers = ["Cust No", "Invoice Number", "Clearing Date", "Amount Paid"]
    assert mapping.guess_role(headers, exclude={"payments"}) != "payments"


def test_apply_mapping_reports_missing_required_fields():
    df = pd.DataFrame({"Customer No": ["C1"], "Doc Date": ["2026-01-01"]})
    result = mapping.apply_mapping(
        df, "invoices", {"customer_id": "Customer No", "invoice_date": "Doc Date"})
    assert not result.ok
    assert set(result.missing_required) == {"invoice_no", "invoice_amount"}


def test_apply_mapping_coerces_types():
    df = pd.DataFrame({
        "Customer No": ["C1"], "Document No": ["INV-1"], "Doc Date": ["01/15/2026"],
        "Invoice Amt": ["$1,500.00"], "Balance Due": ["(250.00)"]})
    result = mapping.apply_mapping(df, "invoices", mapping.auto_map(list(df.columns), "invoices"))
    assert result.ok
    row = result.frame.iloc[0]
    assert row["invoice_date"] == pd.Timestamp("2026-01-15")
    assert row["invoice_amount"] == pytest.approx(1500.0)
    assert row["open_balance"] == pytest.approx(-250.0)


def test_apply_mapping_warns_when_coercion_loses_data():
    df = pd.DataFrame({
        "Customer No": ["C1", "C2"], "Document No": ["A", "B"],
        "Doc Date": ["not a date", "also not a date"],
        "Invoice Amt": ["100", "200"]})
    result = mapping.apply_mapping(df, "invoices", mapping.auto_map(list(df.columns), "invoices"))
    assert any("parsed as date" in n for n in result.notes)


def test_every_role_declares_its_required_fields():
    for role in ("invoices", "payments", "sales", "customers"):
        assert required_fields(role), f"{role} must declare required fields"


def test_profile_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(mapping, "PROFILE_DIR", tmp_path)
    payload = {"invoices": {"customer_id": "Customer No", "invoice_no": "Document No"}}
    mapping.save_profile("my export", payload)
    assert "my export" in mapping.list_profiles()
    assert mapping.load_profile("my export") == payload
