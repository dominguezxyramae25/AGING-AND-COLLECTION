"""Formatted multi-tab Excel workbook."""

from __future__ import annotations

import io

import numpy as np
import pandas as pd

from .charts import GRID, INK, INK_MUTED, SERIES

HEADER_BG = "#1f3348"
BAND = "#f4f6f9"


def _quote_symbol(symbol: str) -> str:
    """Make a currency symbol safe inside an Excel number-format code.

    Unquoted letters are date/time tokens to Excel -- d, m, y, h, s -- so a
    symbol like "PHP " turns the whole format into a time code and every amount
    renders as a date error. Quoting the literal text prevents that.
    """
    if not symbol:
        return ""
    return '"' + symbol.replace('"', "") + '"'


def _formats(book, symbol: str) -> dict:
    sym = _quote_symbol(symbol)
    money = f'{sym}#,##0.00;[Red]({sym}#,##0.00)'
    money0 = f'{sym}#,##0;[Red]({sym}#,##0)'
    return {
        "title": book.add_format({"bold": True, "font_size": 16, "font_color": INK}),
        "subtitle": book.add_format({"font_size": 10, "font_color": INK_MUTED}),
        "header": book.add_format({
            "bold": True, "font_color": "#ffffff", "bg_color": HEADER_BG,
            "align": "left", "valign": "vcenter", "text_wrap": True, "border": 0}),
        "money": book.add_format({"num_format": money}),
        "money0": book.add_format({"num_format": money0}),
        "pct": book.add_format({"num_format": '0.0"%"'}),
        "num": book.add_format({"num_format": "#,##0"}),
        "days": book.add_format({"num_format": '#,##0.0" d"'}),
        "date": book.add_format({"num_format": "yyyy-mm-dd"}),
        "text": book.add_format({}),
        "kpi_label": book.add_format({"font_color": INK_MUTED, "font_size": 10}),
        "kpi_money": book.add_format({"bold": True, "font_size": 18, "num_format": money0,
                                      "font_color": INK}),
        "kpi_num": book.add_format({"bold": True, "font_size": 18, "num_format": "#,##0.0",
                                    "font_color": INK}),
        "kpi_pct": book.add_format({"bold": True, "font_size": 18, "num_format": '0.0"%"',
                                    "font_color": INK}),
        "kpi_int": book.add_format({"bold": True, "font_size": 18, "num_format": "#,##0",
                                    "font_color": INK}),
        "section": book.add_format({"bold": True, "font_size": 12, "font_color": INK,
                                    "bottom": 1, "border_color": GRID}),
        "wrap": book.add_format({"text_wrap": True, "valign": "top"}),
    }


# Column name -> format key. Matching is by exact name, then by keyword.
_MONEY_WORDS = ("amount", "balance", "ar", "sales", "limit", "paid", "exposure",
                "collected", "collectible", "credits", "total", "past due", "current")
_PCT_WORDS = ("%", "rate", "utilization", "pct", "cei", "concentration")
_DAY_WORDS = ("days", "dso", "dpd", "add")


def _fmt_for(column: str, fmts: dict, bucket_labels: tuple[str, ...] = ()):
    c = str(column).lower()
    if c in ("period", "as_of_date", "invoice_date", "due_date", "payment_date"):
        return fmts["date"], 13
    if c in ("rows", "invoices", "open_invoices", "payments", "customers", "rank",
             "open invoices"):
        return fmts["num"], 11
    if any(w in c for w in _PCT_WORDS):
        return fmts["pct"], 13
    if any(w in c for w in _DAY_WORDS):
        return fmts["days"], 13
    if column in bucket_labels or any(w in c for w in _MONEY_WORDS):
        return fmts["money"], 15
    return fmts["text"], 20


def _write_table(writer, sheet_name: str, df: pd.DataFrame, fmts: dict,
                 title: str = "", note: str = "", startrow: int = 0,
                 bucket_labels: tuple[str, ...] = ()) -> None:
    """Write a frame with a styled header, autofilter, frozen panes and column formats."""
    book = writer.book
    if sheet_name not in writer.sheets:
        writer.sheets[sheet_name] = book.add_worksheet(sheet_name)
    ws = writer.sheets[sheet_name]
    ws.hide_gridlines(2)

    row = startrow
    if title:
        ws.write(row, 0, title, fmts["title"])
        row += 1
    if note:
        ws.write(row, 0, note, fmts["subtitle"])
        row += 1
    if title or note:
        row += 1

    if df is None or df.empty:
        ws.write(row, 0, "No data available for this section.", fmts["subtitle"])
        ws.set_column(0, 0, 60)
        return

    clean = df.copy()
    for col in clean.columns:
        if pd.api.types.is_datetime64_any_dtype(clean[col]):
            clean[col] = pd.to_datetime(clean[col]).dt.tz_localize(None)
        elif clean[col].dtype.name == "category":
            clean[col] = clean[col].astype(str)
    clean = clean.replace([np.inf, -np.inf], np.nan)

    header_row = row
    for j, col in enumerate(clean.columns):
        ws.write(header_row, j, str(col).replace("_", " ").title(), fmts["header"])
    ws.set_row(header_row, 30)

    for j, col in enumerate(clean.columns):
        fmt, width = _fmt_for(col, fmts, bucket_labels)
        longest = max([len(str(col))] + [len(str(v)) for v in clean[col].head(200)])
        ws.set_column(j, j, max(width, min(longest + 2, 42)), fmt)
        # NaN/NaT must become None: xlsxwriter rejects NaN, and `.where(..., None)`
        # on a float column silently leaves it as NaN.
        values = [None if pd.isna(v) else v for v in clean[col].tolist()]
        ws.write_column(header_row + 1, j, values, fmt)

    last = header_row + len(clean)
    ws.autofilter(header_row, 0, last, len(clean.columns) - 1)
    ws.freeze_panes(header_row + 1, 0)

    # Colour-scale the bucket columns so deterioration is visible at a glance.
    for j, col in enumerate(clean.columns):
        if str(col) in bucket_labels or str(col).lower() in ("% past due", "risk_score"):
            ws.conditional_format(header_row + 1, j, last, j, {
                "type": "2_color_scale", "min_color": "#ffffff", "max_color": "#f6b8b8"})


def build_workbook(analysis, symbol: str = "$") -> bytes:
    """Render the whole analysis as a formatted .xlsx and return the bytes."""
    buffer = io.BytesIO()
    labels = tuple(analysis.bucket_labels or [])
    with pd.ExcelWriter(buffer, engine="xlsxwriter",
                        datetime_format="yyyy-mm-dd", date_format="yyyy-mm-dd") as writer:
        book = writer.book
        fmts = _formats(book, symbol)
        k = analysis.kpis
        as_of = analysis.as_of.date().isoformat()

        # ---- 00 KPI Dashboard -------------------------------------------------
        ws = book.add_worksheet("00 KPI Dashboard")
        writer.sheets["00 KPI Dashboard"] = ws
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 2)
        ws.set_column(1, 4, 22)
        ws.write(1, 1, "AR Aging & Collections Analysis", fmts["title"])
        ws.write(2, 1, f"As of {as_of}   ·   generated {pd.Timestamp.now():%Y-%m-%d %H:%M}",
                 fmts["subtitle"])

        tiles = [
            ("Total AR", k["total_ar"], "kpi_money"),
            ("Past due", k["past_due_ar"], "kpi_money"),
            ("% past due", k["pct_past_due"], "kpi_pct"),
            ("Over 90 days", k["over_90_ar"], "kpi_money"),
            ("DSO", k["dso"], "kpi_num"),
            ("Best possible DSO", k["best_possible_dso"], "kpi_num"),
            ("Delinquent DSO", k["delinquent_dso"], "kpi_num"),
            ("CEI", k["cei"], "kpi_pct"),
            ("Avg days to pay", k["avg_days_to_pay"], "kpi_num"),
            ("On-time payment rate", k["on_time_rate"], "kpi_pct"),
            ("Avg days delinquent", k["add_days"], "kpi_num"),
            ("Open invoices", k["open_invoices"], "kpi_int"),
            ("Customers with AR", k["customers"], "kpi_int"),
            ("Over credit limit", k["over_limit_customers"], "kpi_int"),
            ("Top-10 concentration", k["top10_concentration"], "kpi_pct"),
            ("Critical-risk accounts", k["critical_accounts"], "kpi_int"),
        ]
        row = 4
        for i, (label, value, fmt_key) in enumerate(tiles):
            col = 1 + (i % 4)
            if i and i % 4 == 0:
                row += 3
            ws.write(row, col, label, fmts["kpi_label"])
            if value is None or (isinstance(value, float) and not np.isfinite(value)):
                ws.write(row + 1, col, "n/a", fmts["kpi_label"])
            else:
                ws.write_number(row + 1, col, float(value), fmts[fmt_key])

        row += 4
        ws.write(row, 1, "Aging profile", fmts["section"])
        row += 1
        bt = analysis.bucket_totals
        for j, col_name in enumerate(("Bucket", "Amount", "% of AR", "Invoices")):
            ws.write(row, 1 + j, col_name, fmts["header"])
        for i, r in bt.iterrows():
            ws.write(row + 1 + i, 1, r["Bucket"], fmts["text"])
            ws.write_number(row + 1 + i, 2, float(r["Amount"]), fmts["money"])
            ws.write_number(row + 1 + i, 3, float(r["% of AR"]), fmts["pct"])
            ws.write_number(row + 1 + i, 4, int(r["Invoices"]), fmts["num"])

        chart = book.add_chart({"type": "column"})
        first = row + 1
        chart.add_series({
            "name": "Open balance",
            "categories": ["00 KPI Dashboard", first, 1, first + len(bt) - 1, 1],
            "values": ["00 KPI Dashboard", first, 2, first + len(bt) - 1, 2],
            "fill": {"color": SERIES[0]}, "border": {"none": True},
            "gap": 60,
        })
        chart.set_title({"name": "AR by aging bucket"})
        chart.set_legend({"none": True})
        chart.set_size({"width": 560, "height": 300})
        chart.set_chartarea({"border": {"none": True}})
        ws.insert_chart(row, 6, chart)

        # ---- data sheets ------------------------------------------------------
        _write_table(writer, "01 Aging Summary", analysis.customer_aging, fmts,
                     "Aging summary by customer", f"Open balances as of {as_of}.", bucket_labels=labels)
        _write_table(writer, "02 Aging Detail", analysis.detail.drop(
            columns=[c for c in ("is_credit", "as_of_date", "due_date_source")
                     if c in analysis.detail.columns]), fmts,
                     "Aging detail by invoice",
                     "One row per open invoice, with days past due and bucket.", bucket_labels=labels)
        if not analysis.segment_aging.empty:
            _write_table(writer, "03 Aging by Segment", analysis.segment_aging, fmts,
                         "Aging by segment", bucket_labels=labels)
        _write_table(writer, "04 DSO Trend", analysis.dso_trend, fmts,
                     "DSO trend",
                     "Standard DSO = (AR / credit sales) x days in period. "
                     "BPDSO uses current AR only; the gap is delinquent DSO.", bucket_labels=labels)
        _write_table(writer, "05 DSO by Customer", analysis.dso_customer, fmts,
                     "DSO by customer", bucket_labels=labels)
        if not analysis.dso_segment.empty:
            _write_table(writer, "06 DSO by Segment", analysis.dso_segment, fmts,
                         "DSO by segment", bucket_labels=labels)
        _write_table(writer, "07 CEI", analysis.cei, fmts,
                     "Collection Effectiveness Index",
                     "CEI = (Begin AR + Credit Sales - End AR) / "
                     "(Begin AR + Credit Sales - End Current AR). 100% is perfect.", bucket_labels=labels)
        _write_table(writer, "08 Days to Pay", analysis.behavior, fmts,
                     "Payment behaviour by customer",
                     "Weighted by amount paid. Slippage is days to pay less terms.", bucket_labels=labels)
        _write_table(writer, "09 Top Delinquent", analysis.risk, fmts,
                     "Delinquency risk ranking",
                     "Risk score weights: 30% past due, 25% age, 20% over-90, "
                     "15% credit-limit use, 10% payment slippage.", bucket_labels=labels)
        _write_table(writer, "10 Data Quality", analysis.issues, fmts,
                     "Data quality findings",
                     "Resolve Errors before relying on the totals above.", bucket_labels=labels)

        # ---- assumptions ------------------------------------------------------
        ws = book.add_worksheet("11 Assumptions")
        writer.sheets["11 Assumptions"] = ws
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 34)
        ws.set_column(1, 1, 90)
        ws.write(0, 0, "Methodology & assumptions", fmts["title"])
        r = 2
        for key, value in analysis.settings.items():
            ws.write(r, 0, key, fmts["kpi_label"])
            ws.write(r, 1, str(value), fmts["wrap"])
            r += 1
        if analysis.notes:
            r += 1
            ws.write(r, 0, "Applied to this dataset", fmts["section"])
            r += 1
            for note in analysis.notes:
                ws.write(r, 1, note, fmts["wrap"])
                r += 1

    buffer.seek(0)
    return buffer.getvalue()
