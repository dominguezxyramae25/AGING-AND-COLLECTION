"""The collections action queue and the documents it produces."""

import io
import zipfile

import pandas as pd
import pytest

from arcollect import aging, collections, kpis, letters, worklist

AS_OF = pd.Timestamp("2026-06-30")


def _inv(customer, no, dpd, balance):
    """An invoice whose due date sits ``dpd`` days before the as-of date."""
    due = AS_OF - pd.Timedelta(days=dpd)
    return {"customer_id": customer, "customer_name": customer, "invoice_no": no,
            "invoice_date": due - pd.Timedelta(days=30), "due_date": due,
            "invoice_amount": balance, "open_balance": balance}


def _queue(rows, scheme="standard", exclude=()):
    frame = pd.DataFrame(rows)
    detail, _ = aging.build_aging(frame, AS_OF, scheme=scheme)
    risk = collections.risk_ranking(detail, scheme=scheme)
    return worklist.build_worklist(detail, risk, AS_OF, scheme, exclude=exclude)


# ---- escalation ----------------------------------------------------------------------

@pytest.mark.parametrize("dpd,stage", [
    (1, "reminder"), (30, "reminder"),
    (31, "first_notice"), (60, "first_notice"),
    (61, "second_notice"), (90, "second_notice"),
    (91, "final_demand"), (120, "final_demand"),
    (121, "escalate"), (900, "escalate"),
])
def test_stage_at_each_bucket_edge_standard(dpd, stage):
    queue, _, _ = _queue([_inv("C1", "A", dpd, 100.0)])
    assert queue["stage"].iloc[0] == stage


@pytest.mark.parametrize("dpd,stage", [
    (120, "final_demand"), (121, "escalate"), (150, "escalate"), (151, "escalate"),
])
def test_stage_at_each_bucket_edge_quickbooks(dpd, stage):
    queue, _, _ = _queue([_inv("C1", "A", dpd, 100.0)], scheme="quickbooks")
    assert queue["stage"].iloc[0] == stage


def test_stage_follows_the_oldest_unpaid_invoice():
    """A large recent invoice must not soften the stage set by an ancient one."""
    queue, _, _ = _queue([_inv("C1", "OLD", 400, 50.0), _inv("C1", "NEW", 5, 100_000.0)])
    assert queue["stage"].iloc[0] == "escalate"
    assert queue["oldest_dpd"].iloc[0] == 400


def test_credit_age_does_not_drive_escalation():
    """An old credit note says nothing about how the customer pays."""
    queue, _, _ = _queue([_inv("C1", "CM", 900, -10.0), _inv("C1", "NEW", 10, 500.0)])
    assert queue["stage"].iloc[0] == "reminder"
    assert queue["oldest_dpd"].iloc[0] == 10


def test_response_by_is_as_of_plus_the_stage_sla():
    queue, _, _ = _queue([_inv("C1", "A", 200, 100.0)])
    expected = AS_OF + pd.Timedelta(days=worklist.STAGES["escalate"].sla_days)
    assert queue["response_by"].iloc[0] == expected


# ---- what is owed --------------------------------------------------------------------

def test_amount_due_now_nets_past_due_credits():
    """Demanding gross would bill for invoices a credit note already settled."""
    queue, _, _ = _queue([_inv("C1", "A", 200, 1_000.0), _inv("C1", "CM", 200, -400.0)])
    row = queue.iloc[0]
    assert row["amount_due_now"] == pytest.approx(600.0)
    assert row["past_due_gross"] == pytest.approx(1_000.0)
    assert row["past_due_credits"] == pytest.approx(-400.0)


def test_amount_due_now_excludes_current_invoices():
    queue, _, _ = _queue([_inv("C1", "LATE", 40, 300.0), _inv("C1", "OK", -20, 9_000.0)])
    assert queue["amount_due_now"].iloc[0] == pytest.approx(300.0)


def test_past_due_fully_offset_by_credits_is_not_chased():
    """The account still owes money overall, but nothing is collectable right now:
    a credit note cancels the whole overdue balance."""
    queue, _, notes = _queue([_inv("C1", "A", 200, 500.0),
                              _inv("C1", "CM", 200, -500.0),
                              _inv("C1", "CURRENT", -30, 900.0)])
    assert queue.empty
    assert any("offset" in n for n in notes)


def test_account_with_no_net_balance_at_all_is_not_chased():
    queue, _, notes = _queue([_inv("C1", "A", 200, 500.0), _inv("C1", "CM", 200, -500.0)])
    assert queue.empty
    assert any("nothing to collect" in n for n in notes)


def test_customer_with_nothing_past_due_is_absent():
    queue, _, _ = _queue([_inv("C1", "A", -10, 500.0)])
    assert queue.empty


def test_clearing_accounts_are_excluded_by_name():
    rows = [_inv("Audit Adjustment_ Accounts Receivable", "J1", 300, 5_000.0),
            _inv("Real Customer Ltd", "A", 300, 1_000.0)]
    queue, _, notes = _queue(rows)
    assert list(queue["customer_name"]) == ["Real Customer Ltd"]
    assert any("clearing or adjustment" in n for n in notes)


def test_extra_exclusions_are_honoured():
    rows = [_inv("Internal Holding Co", "A", 300, 5_000.0),
            _inv("Real Customer Ltd", "B", 300, 1_000.0)]
    queue, _, _ = _queue(rows, exclude=("Internal Holding Co",))
    assert list(queue["customer_name"]) == ["Real Customer Ltd"]


# ---- ordering and the chase list -----------------------------------------------------

def test_queue_is_ordered_by_risk_exposure():
    rows = [_inv("Small", "S", 400, 100.0), _inv("Large", "L", 400, 90_000.0)]
    queue, _, _ = _queue(rows)
    assert list(queue["customer_name"]) == ["Large", "Small"]
    assert list(queue["priority"]) == [1, 2]


def test_chase_list_holds_only_what_is_owed():
    rows = [_inv("C1", "A", 200, 1_000.0), _inv("C1", "CM", 200, -400.0),
            _inv("C1", "CURRENT", -5, 700.0)]
    _, invoices, _ = _queue(rows)
    assert set(invoices["invoice_no"]) == {"A"}


def test_stage_summary_totals_match_the_queue():
    rows = [_inv("C1", "A", 400, 1_000.0), _inv("C2", "B", 10, 250.0)]
    queue, _, _ = _queue(rows)
    summary = worklist.stage_summary(queue)
    assert summary["Amount"].sum() == pytest.approx(queue["amount_due_now"].sum())
    assert summary["Accounts"].sum() == len(queue)


def test_empty_book_yields_an_empty_queue():
    queue, invoices, _ = worklist.build_worklist(
        pd.DataFrame(), pd.DataFrame(), AS_OF)
    assert queue.empty and invoices.empty


# ---- documents -----------------------------------------------------------------------

@pytest.fixture
def analysis():
    frame = pd.DataFrame([_inv("Acme Trading Inc", "INV-100", 200, 1_000.0),
                          _inv("Acme Trading Inc", "INV-101", 40, 500.0)])
    return kpis.build_analysis(frame, as_of=AS_OF)


SENDER = letters.Sender(company="Test Co", address="1 Test Street",
                        contact_name="A. Person", email="ar@test.co")


def test_demand_letter_names_the_customer_and_every_invoice(analysis):
    import fitz
    row = analysis.worklist.iloc[0]
    data = letters.demand_letter(analysis, row, SENDER, "$")
    assert data[:4] == b"%PDF"
    text = "".join(page.get_text() for page in fitz.open(stream=data, filetype="pdf"))
    assert "Acme Trading Inc" in text
    assert "INV-100" in text and "INV-101" in text
    assert "Draft for review" in text


def test_letter_shows_the_placeholder_when_no_address_is_known(analysis):
    import fitz
    row = analysis.worklist.iloc[0]
    data = letters.demand_letter(analysis, row, SENDER, "$")
    text = "".join(page.get_text() for page in fitz.open(stream=data, filetype="pdf"))
    assert letters.NO_ADDRESS in text


def test_statement_lists_every_open_item(analysis):
    import fitz
    data = letters.statement_of_account(analysis, "Acme Trading Inc", SENDER, "$")
    text = "".join(page.get_text() for page in fitz.open(stream=data, filetype="pdf"))
    assert "Statement of Account" in text
    assert "INV-100" in text and "INV-101" in text


def test_letter_pack_has_one_pdf_per_chased_account(analysis):
    data = letters.letter_pack(analysis, SENDER, "$")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    pdfs = [n for n in names if n.endswith(".pdf")]
    assert len(pdfs) == len(analysis.worklist)
    assert "README.txt" in names


def test_sender_needs_a_company_name():
    assert not letters.Sender().ready
    assert letters.Sender(company="Anything").ready


def test_every_stage_has_copy():
    for key in worklist.STAGE_ORDER[1:]:
        assert key in letters.STAGE_COPY
        assert "{by}" in letters.STAGE_COPY[key]["ask"]
