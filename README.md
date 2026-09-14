# AR Aging & Collections Analytics

A local app for accounts-receivable collections analysis. Upload your AR export and get
**AR aging**, **collection analysis**, **DSO**, and **collection effectiveness** — as an
interactive dashboard, a formatted Excel workbook, and a print-ready PDF.

Everything runs on your machine. Files are read in memory and nothing is sent anywhere.

---

## Quick start

```bash
make install     # pip install -r requirements.txt
make sample      # generate a demo dataset (optional)
make run         # http://localhost:8501
```

Then click **Load sample** in the sidebar to see it working, or upload your own files.

## What to upload

One file per dataset, Excel (`.xlsx` / `.xls`) or CSV. Only the first is required.

| Dataset | Unlocks | Minimum columns |
|---|---|---|
| **Open invoices** *(required)* | AR aging, risk ranking, everything downstream | customer, invoice no., invoice date, invoice amount, open balance |
| **Payments / cash receipts** | CEI, days-to-pay, on-time rate | customer, payment date, payment amount |
| **Monthly sales** | DSO trend | month, credit sales |
| **Customer master** | Credit limits, segments, risk bands | customer, credit limit, terms |

### Your column names do not need to match anything

The app reads whatever headers your export has, guesses the mapping, and shows you every
guess to correct. It already knows the spellings that SAP, NetSuite, QuickBooks, Sage and
Dynamics emit — `Cust No`, `Doc Date`, `Net Due Date`, `Balance Due`, `Amt Due` and so on.

It also handles the things that break naive readers:

- **Title rows above the grid** — the header row is detected, not assumed to be row 1.
- **Money as text** — `$1,234.50`, `(500.00)` for credits, and trailing-minus `1200-`.
- **Excel serial dates** — a bare `45000` is read as a date, not as 1970.
- **Day-first dates** — tick the sidebar box for `DD/MM/YYYY` files.
- **Ragged CSVs** where the title row has fewer fields than the data.

Save the mapping as a **profile** and next month's export is one click.

## What it calculates

**AR Aging** — Current / 1–30 / 31–60 / 61–90 / 91–120 / 120+, on days past **due date**.
Where an invoice has no due date it is derived from payment terms; every such fallback is
recorded on the Assumptions tab and the Excel Assumptions sheet. Credit balances are
reported separately rather than silently netted into buckets.

**DSO** — Standard DSO `(AR ÷ credit sales) × days in period`, trended monthly, plus:
- **Best Possible DSO**, using only current AR — the floor you would hit if nothing went late.
- **Delinquent DSO**, the gap between them — the part collections activity can recover.
- **DSO by customer and segment**, so you can see who drags the average.

**Collection analysis**
- **CEI** — `(Begin AR + Credit Sales − End AR) ÷ (Begin AR + Credit Sales − End Current AR)`.
  Unlike DSO it is not distorted by swings in sales volume. 100% is perfect; 80% is the
  usual benchmark.
- **Days-to-pay and payment behaviour** — average days to pay weighted by amount, slippage
  against each invoice's own terms, and on-time payment rate, trended.
- **Top delinquent accounts** ranked by exposure (risk score × past-due balance), with a
  composite risk score: 30% share past due, 25% age of delinquency, 20% over-90
  concentration, 15% credit-limit utilisation, 10% payment slippage. Every component is
  shown alongside the score.

Monthly history for DSO and CEI is reconstructed by replaying invoice and payment activity,
so the AR balance at each month end is split into current and past due as it stood *then*.

**Data quality** runs before any metric is shown: duplicate invoices, balances exceeding
invoice amounts, missing or reversed dates, payments matching no invoice, mixed currencies,
customers missing from the master. Errors are flagged on the dashboard, because a
collections report built on a bad file is confidently wrong rather than obviously broken.

## Outputs

- **Dashboard** — six tabs: AR Aging, Collection Analysis, DSO, Customer Detail, Data
  Quality, Assumptions.
- **Excel workbook** — 12 formatted sheets with frozen panes, autofilters, conditional
  formatting and embedded charts, plus an Assumptions sheet recording every methodology
  choice and fallback applied.
- **PDF report** — executive summary with KPI tiles, charts, the top-20 delinquent table,
  and the same methodology notes.

All three are generated from one analysis object, so they cannot disagree with each other.

## Project layout

```
app.py                  Streamlit UI
arcollect/
  schema.py             Canonical field definitions + header aliases
  ingest.py             File reading, header sniffing, type coercion
  mapping.py            Fuzzy column mapping, validation, profiles
  allocate.py           Payment-to-invoice matching (reference, else FIFO)
  aging.py              Days past due, buckets, aging tables, ADD
  dso.py                AR reconstruction, DSO / BPDSO / delinquent DSO
  collections.py        CEI, payment behaviour, risk ranking
  quality.py            Data-quality checks
  kpis.py               Orchestrator -> one Analysis object
  charts.py             Plotly (screen) + matplotlib (PDF) figures
  export_excel.py       Formatted workbook
  export_pdf.py         Executive report
sample_data/generate.py Synthetic dataset generator
tests/                  Known-answer tests for the financial math
```

## Tests

```bash
make test
```

The financial math is asserted against hand-computed answers rather than eyeballed —
bucket boundaries at every edge (0, 1, 30, 31, 120, 121), the DSO and CEI formulas,
balance-weighted averages, FIFO allocation, and a reconciliation check that the aging
total matches the reconstructed AR at the as-of date.

## Notes and limits

- **Multi-currency** is flagged but not converted. Filter to one currency, or convert
  before upload, if your export mixes them.
- **Open-items-only exports** (no settled invoices) still give correct current aging, but
  the DSO and CEI trends for earlier months will understate AR. The app detects this and
  says so.
- The earliest period in any trend is understated, since there is no prior history to
  carry an opening balance.
- The app pins a light theme so the dashboard, Excel and PDF all render against the one
  surface the chart palette was validated against.
