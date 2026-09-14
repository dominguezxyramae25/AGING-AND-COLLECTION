"""Print-ready executive PDF report."""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from . import charts

INK = colors.HexColor(charts.INK)
MUTED = colors.HexColor(charts.INK_MUTED)
GRID = colors.HexColor(charts.GRID)
ACCENT = colors.HexColor(charts.SERIES[0])
SURFACE = colors.HexColor(charts.SURFACE)
BAND = colors.HexColor("#f4f6f9")
HEADER_BG = colors.HexColor("#1f3348")

RISK_FILL = {k: colors.HexColor(v) for k, v in charts.RISK_COLORS.items()}


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=22, textColor=INK, alignment=TA_LEFT,
                                spaceAfter=7, leading=26),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontSize=9.5,
                              textColor=MUTED, spaceAfter=14),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold",
                             fontSize=13, textColor=INK, spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("b", parent=base["Normal"], fontSize=9,
                               textColor=INK, leading=13),
        "note": ParagraphStyle("n", parent=base["Normal"], fontSize=8,
                               textColor=MUTED, leading=11.5),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontSize=7.8,
                               textColor=INK, leading=10),
    }


def _fmt_money(v, symbol="$") -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    return f"{symbol}{v:,.0f}"


def _fmt_num(v, suffix="") -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "n/a"
    return f"{v:,.1f}{suffix}"


def _kpi_grid(kpis: dict, styles: dict, symbol: str) -> Table:
    """Four-across KPI tiles: label above a large value."""
    tiles = [
        ("Total AR", _fmt_money(kpis["total_ar"], symbol)),
        ("Past due", _fmt_money(kpis["past_due_ar"], symbol)),
        ("% past due", _fmt_num(kpis["pct_past_due"], "%")),
        ("Over 90 days", _fmt_money(kpis["over_90_ar"], symbol)),
        ("DSO", _fmt_num(kpis["dso"], " d")),
        ("Best possible DSO", _fmt_num(kpis["best_possible_dso"], " d")),
        ("Delinquent DSO", _fmt_num(kpis["delinquent_dso"], " d")),
        ("CEI", _fmt_num(kpis["cei"], "%")),
        ("Avg days to pay", _fmt_num(kpis["avg_days_to_pay"], " d")),
        ("On-time rate", _fmt_num(kpis["on_time_rate"], "%")),
        ("Avg days delinquent", _fmt_num(kpis["add_days"], " d")),
        ("Open invoices", f"{kpis['open_invoices']:,}"),
    ]
    rows = []
    for i in range(0, len(tiles), 4):
        chunk = tiles[i:i + 4]
        rows.append([Paragraph(f'<font size="7.5" color="#52514e">{lbl.upper()}</font>',
                               styles["note"]) for lbl, _ in chunk])
        rows.append([Paragraph(f'<font size="14" color="#0b0b0b"><b>{val}</b></font>',
                               styles["body"]) for _, val in chunk])

    table = Table(rows, colWidths=[1.72 * inch] * 4, hAlign="LEFT")
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for block in range(len(rows) // 2):
        top, bottom = block * 2, block * 2 + 1
        style += [("BACKGROUND", (0, top), (-1, bottom), BAND),
                  ("TOPPADDING", (0, top), (-1, top), 7),
                  ("BOTTOMPADDING", (0, bottom), (-1, bottom), 7),
                  ("LINEBELOW", (0, bottom), (-1, bottom), 6, colors.white)]
    table.setStyle(TableStyle(style))
    return table


def _data_table(df: pd.DataFrame, headers: list[str], widths: list[float],
                styles: dict, band_col: int | None = None) -> Table:
    rows = [[Paragraph(f'<font color="#ffffff"><b>{h}</b></font>', styles["cell"])
             for h in headers]]
    for _, r in df.iterrows():
        rows.append([Paragraph(str(v), styles["cell"]) for v in r.tolist()])

    table = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), BAND))
    # Risk band gets a colour chip, but the band name is printed in the cell too,
    # so the colour never carries the meaning on its own.
    if band_col is not None:
        for i, (_, r) in enumerate(df.iterrows(), start=1):
            band = str(r.iloc[band_col])
            fill = RISK_FILL.get(band)
            if fill is not None:
                style.append(("BACKGROUND", (band_col, i), (band_col, i), fill))
                # White is unreadable on the lighter warning/serious steps.
                ink = colors.white if band == "Critical" else INK
                style.append(("TEXTCOLOR", (band_col, i), (band_col, i), ink))
    table.setStyle(TableStyle(style))
    return table


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(0.75 * inch, 0.5 * inch,
                      "AR Aging & Collections Analysis")
    canvas.drawRightString(LETTER[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.setStrokeColor(GRID)
    canvas.setLineWidth(0.5)
    canvas.line(0.75 * inch, 0.68 * inch, LETTER[0] - 0.75 * inch, 0.68 * inch)
    canvas.restoreState()


def build_pdf(analysis, symbol: str = "$", entity: str = "") -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.7 * inch, bottomMargin=0.8 * inch,
        title="AR Aging & Collections Analysis", author="arcollect")
    st = _styles()
    k = analysis.kpis
    story = []

    story.append(Paragraph("AR Aging &amp; Collections Analysis", st["title"]))
    subtitle = f"As of {analysis.as_of.date().isoformat()}"
    if entity:
        subtitle = f"{entity}  ·  {subtitle}"
    subtitle += f"  ·  Generated {pd.Timestamp.now():%Y-%m-%d %H:%M}"
    story.append(Paragraph(subtitle, st["sub"]))

    story.append(_kpi_grid(k, st, symbol))

    story.append(Paragraph("AR aging profile", st["h2"]))
    story.append(Image(charts.png_aging(analysis.bucket_totals, symbol),
                       width=6.6 * inch, height=2.7 * inch))
    story.append(Paragraph(
        f"{_fmt_money(k['past_due_ar'], symbol)} of {_fmt_money(k['gross_ar'], symbol)} gross AR "
        f"({k['pct_past_due']:.1f}%) is past due, of which "
        f"{_fmt_money(k['over_90_ar'], symbol)} ({k['pct_over_90']:.1f}% of AR) is over 90 days. "
        f"Balance-weighted average days delinquent is {k['add_days']:.0f} days. "
        f"(Gross AR excludes {_fmt_money(abs(k['credit_balances']), symbol)} of unapplied "
        f"credit balances, which the Total AR figure nets off.)", st["body"]))

    if not analysis.dso_trend.empty:
        story.append(Paragraph("Days Sales Outstanding", st["h2"]))
        story.append(Image(charts.png_dso(analysis.dso_trend),
                           width=6.6 * inch, height=2.5 * inch))
        story.append(Paragraph(
            f"DSO is {_fmt_num(k['dso'])} days against a best-possible floor of "
            f"{_fmt_num(k['best_possible_dso'])} days. The {_fmt_num(k['delinquent_dso'])}-day "
            f"gap is delinquent DSO &mdash; the portion attributable to late payment rather "
            f"than to credit terms, and the part collections activity can recover.", st["body"]))

    if not analysis.cei.empty:
        story.append(Paragraph("Collection effectiveness", st["h2"]))
        story.append(Image(charts.png_cei(analysis.cei), width=6.6 * inch, height=2.3 * inch))
        cei_value = k["cei"]
        verdict = ("above the 80% benchmark" if np.isfinite(cei_value) and cei_value >= 80
                   else "below the 80% benchmark")
        story.append(Paragraph(
            f"CEI for the latest period is {_fmt_num(cei_value, '%')}, {verdict}. CEI measures "
            f"cash collected against cash that was collectible, so unlike DSO it is not "
            f"distorted by changes in sales volume. Average days to pay is "
            f"{_fmt_num(k['avg_days_to_pay'])} days with an on-time rate of "
            f"{_fmt_num(k['on_time_rate'], '%')}.", st["body"]))

    if not analysis.risk.empty:
        story.append(Paragraph("Top delinquent accounts by exposure", st["h2"]))
        top = analysis.risk.head(20).copy()
        label = "customer_name" if "customer_name" in top.columns else "customer_id"
        table_df = pd.DataFrame({
            "#": top["rank"].astype(int).astype(str),
            "Customer": top[label].astype(str).str.slice(0, 30),
            "Total AR": top["total_ar"].map(lambda v: _fmt_money(v, symbol)),
            "Past due": top["past_due_ar"].map(lambda v: _fmt_money(v, symbol)),
            "% PD": top["pct_past_due"].map(lambda v: f"{v:.0f}%"),
            "Over 90": top["over_90"].map(lambda v: _fmt_money(v, symbol)),
            "ADD": top["add_days"].map(lambda v: f"{v:.0f} d"),
            "Score": top["risk_score"].map(lambda v: f"{v:.0f}"),
            "Band": top["risk_band"].astype(str),
        })
        widths = [0.3, 1.6, 0.85, 0.85, 0.45, 0.85, 0.5, 0.58, 0.77]
        story.append(_data_table(table_df, list(table_df.columns),
                                 [w * inch for w in widths], st, band_col=8))
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            "Ranked by exposure (risk score x past-due balance). Score weights: 30% share "
            "past due, 25% age of delinquency, 20% over-90 concentration, 15% credit-limit "
            "utilisation, 10% payment slippage against terms.", st["note"]))

    story.append(PageBreak())
    story.append(Paragraph("Methodology &amp; assumptions", st["h2"]))
    settings_df = pd.DataFrame({"Item": list(analysis.settings.keys()),
                                "Basis": [str(v) for v in analysis.settings.values()]})
    story.append(_data_table(settings_df, ["Item", "Basis"],
                             [2.0 * inch, 4.75 * inch], st))

    if analysis.notes:
        story.append(Paragraph("Applied to this dataset", st["h2"]))
        for note in analysis.notes:
            story.append(Paragraph(f"&bull; {note}", st["note"]))
            story.append(Spacer(1, 3))

    if not analysis.issues.empty:
        story.append(Paragraph("Data quality findings", st["h2"]))
        issues_df = pd.DataFrame({
            "Severity": analysis.issues["severity"],
            "Dataset": analysis.issues["dataset"],
            "Finding": analysis.issues["check"],
            "Rows": analysis.issues["rows"].map(lambda v: f"{v:,}"),
            "Detail": analysis.issues["detail"],
        })
        story.append(_data_table(issues_df, list(issues_df.columns),
                                 [0.7 * inch, 0.75 * inch, 1.6 * inch, 0.5 * inch,
                                  3.2 * inch], st))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer.getvalue()
