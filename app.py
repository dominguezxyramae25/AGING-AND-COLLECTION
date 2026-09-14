"""AR Aging & Collections Analytics -- local Streamlit app.

Run with:  streamlit run app.py
Everything happens in-process on this machine; no data leaves it.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from arcollect import charts, export_excel, export_pdf, ingest, kpis, mapping
from arcollect.schema import ROLE_LABELS, ROLES, fields_for, required_fields

APP_DIR = Path(__file__).resolve().parent
SAMPLE_DIR = APP_DIR / "sample_data" / "out"

st.set_page_config(page_title="AR Aging & Collections", page_icon="📊",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
  .block-container {padding-top: 2.2rem; max-width: 1500px;}
  [data-testid="stMetricValue"] {font-size: 1.55rem;}
  [data-testid="stMetricLabel"] {color: #52514e;}
  div[data-testid="stMetric"] {background:#f4f6f9; padding:14px 16px; border-radius:8px;}
  h1 {font-size: 1.9rem !important;}
  .stTabs [data-baseweb="tab"] {font-size: 0.95rem;}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _read(data: bytes, filename: str, sheet: str | None) -> pd.DataFrame:
    return ingest.read_table(io.BytesIO(data), filename=filename, sheet=sheet)


@st.cache_data(show_spinner=False)
def _sheets(data: bytes, filename: str) -> list[str]:
    return ingest.list_sheets(io.BytesIO(data), filename=filename)


def _sample_files() -> list[tuple[str, bytes]]:
    if not SAMPLE_DIR.exists():
        return []
    return [(p.name, p.read_bytes()) for p in sorted(SAMPLE_DIR.iterdir()) if p.is_file()]


def _fmt_money(value, symbol="$") -> str:
    if value is None or not pd.notna(value):
        return "n/a"
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{symbol}{value / 1_000_000:,.2f}M"
    if magnitude >= 1_000:
        return f"{symbol}{value / 1_000:,.1f}K"
    return f"{symbol}{value:,.0f}"


def _money_format(symbol: str) -> str:
    """Streamlit's built-in numeric formats give thousands separators and
    accounting-style negatives; a printf string would give neither."""
    return {"$": "dollar", "\u20ac": "euro", "\u00a5": "yen"}.get(symbol, "accounting")


def _fmt(value, suffix="", digits=1) -> str:
    if value is None or not pd.notna(value):
        return "n/a"
    return f"{value:,.{digits}f}{suffix}"


# --------------------------------------------------------------------------------------
# Sidebar: files, roles, column mapping, settings
# --------------------------------------------------------------------------------------

st.sidebar.title("AR Collections")
st.sidebar.caption("Upload your AR export, map the columns once, get the analysis.")

if "use_sample" not in st.session_state:
    st.session_state.use_sample = False

uploads = st.sidebar.file_uploader(
    "Upload your files", type=["xlsx", "xls", "csv", "tsv", "txt"],
    accept_multiple_files=True,
    help="Open invoices (required), plus payments, monthly sales and the customer "
         "master if you have them. One file per dataset.")

col_a, col_b = st.sidebar.columns(2)
if col_a.button("Load sample", use_container_width=True,
                help="Try the app on a generated dataset."):
    st.session_state.use_sample = True
if col_b.button("Clear", use_container_width=True):
    st.session_state.use_sample = False
    for key in [k for k in st.session_state if k.startswith(("map_", "role_", "sheet_"))]:
        del st.session_state[key]
    st.rerun()

sources: list[tuple[str, bytes]] = []
if uploads:
    st.session_state.use_sample = False
    sources = [(u.name, u.getvalue()) for u in uploads]
elif st.session_state.use_sample:
    sources = _sample_files()
    if not sources:
        st.sidebar.error("No sample data found. Run `make sample` first.")

if not sources:
    st.title("AR Aging & Collections Analytics")
    st.markdown("""
Upload your accounts-receivable export in the sidebar and this app produces the full
collections pack: **AR aging**, **collection analysis**, **DSO**, and **collection
effectiveness** — on screen, as a formatted Excel workbook, and as a print-ready PDF.

**What to upload** — one file per dataset, in Excel or CSV:

| Dataset | Needed for | Minimum columns |
|---|---|---|
| **Open invoices** *(required)* | AR aging, everything downstream | customer, invoice no., invoice date, amount, balance |
| **Payments / cash receipts** | CEI, days-to-pay, on-time rate | customer, payment date, amount |
| **Monthly sales** | DSO trend | month, credit sales |
| **Customer master** | Credit limits, segments, risk | customer, credit limit, terms |

Column names do not need to match anything — the app reads your headers, guesses the
mapping, and lets you correct it. Save the mapping as a profile and next month's export
is one click.

Nothing is uploaded anywhere: the files are read in memory on this machine.
""")
    st.info("No files yet. Use **Load sample** in the sidebar to see the app working on "
            "a generated dataset.", icon="📂")
    st.stop()

# ---- role assignment and mapping -----------------------------------------------------
st.sidebar.divider()
st.sidebar.subheader("Column mapping")

profiles = mapping.list_profiles()
if profiles:
    chosen = st.sidebar.selectbox("Load a saved profile", ["-- none --"] + profiles)
    if chosen != "-- none --" and st.sidebar.button("Apply profile", use_container_width=True):
        saved = mapping.load_profile(chosen)
        for role, cols in saved.items():
            for field_name, source in cols.items():
                st.session_state[f"map_{role}_{field_name}"] = source or "-- not mapped --"
        st.sidebar.success(f"Applied '{chosen}'.")

dayfirst = st.sidebar.checkbox(
    "Dates are day-first (31/12/2025)", value=False,
    help="Tick this for DD/MM/YYYY files so dates are not misread as month-first.")

frames: dict[str, pd.DataFrame] = {}
mappings: dict[str, dict[str, str | None]] = {}
all_notes: list[str] = []
blocking: list[str] = []
assigned: set[str] = set()

for index, (filename, data) in enumerate(sources):
    sheet_names = _sheets(data, filename)
    sheet = None
    if len(sheet_names) > 1:
        sheet = st.sidebar.selectbox(f"Sheet in {filename}", sheet_names,
                                     key=f"sheet_{index}")
    try:
        raw = _read(data, filename, sheet)
    except Exception as exc:                                  # noqa: BLE001
        st.sidebar.error(f"Could not read {filename}: {exc}")
        continue

    if raw.empty:
        st.sidebar.warning(f"{filename} has no readable rows.")
        continue

    headers = [str(c) for c in raw.columns]
    guess = mapping.guess_role(headers, exclude=assigned)
    role_key = f"role_{index}"
    role = st.sidebar.selectbox(
        f"**{filename}** is…", ROLES, index=ROLES.index(guess),
        format_func=lambda r: ROLE_LABELS[r], key=role_key)
    assigned.add(role)

    auto = mapping.auto_map(headers, role)
    options = ["-- not mapped --"] + headers
    with st.sidebar.expander(f"Columns · {ROLE_LABELS[role]}", expanded=False):
        selected: dict[str, str | None] = {}
        for spec in fields_for(role):
            state_key = f"map_{role}_{spec.name}"
            default = auto.get(spec.name) or "-- not mapped --"
            if state_key in st.session_state and st.session_state[state_key] in options:
                default = st.session_state[state_key]
            label = f"{spec.label}{' *' if spec.required else ''}"
            picked = st.selectbox(label, options, index=options.index(default),
                                  key=state_key, help=spec.help or None)
            selected[spec.name] = None if picked == "-- not mapped --" else picked

    result = mapping.apply_mapping(raw, role, selected, dayfirst=dayfirst)
    mappings[role] = selected
    all_notes += result.notes

    if result.missing_required:
        missing = ", ".join(f.replace("_", " ") for f in result.missing_required)
        blocking.append(f"**{filename}** ({ROLE_LABELS[role]}) is missing required "
                        f"column(s): {missing}. Set them under *Columns* in the sidebar.")
    else:
        frames[role] = result.frame

if profiles is not None:
    with st.sidebar.expander("Save this mapping"):
        profile_name = st.text_input("Profile name", value="my-export")
        if st.button("Save profile", use_container_width=True):
            path = mapping.save_profile(profile_name, mappings)
            st.success(f"Saved to {path.name}")

# ---- analysis settings ---------------------------------------------------------------
st.sidebar.divider()
st.sidebar.subheader("Settings")
symbol = st.sidebar.text_input("Currency symbol", value="$", max_chars=3)
entity = st.sidebar.text_input("Entity name (for the report header)", value="")
default_terms = st.sidebar.number_input(
    "Default terms when none supplied (days)", 0, 365, 30,
    help="Used only where an invoice has neither a due date nor payment terms.")
days_basis = st.sidebar.radio("Days in period for DSO", ["calendar", "30"],
                              horizontal=True,
                              help="'calendar' uses actual days in each month.")
lookback = st.sidebar.slider("Customer DSO lookback (months)", 3, 24, 12)

if blocking:
    st.title("AR Aging & Collections Analytics")
    for message in blocking:
        st.error(message, icon="⚠️")
    st.stop()

if "invoices" not in frames:
    st.title("AR Aging & Collections Analytics")
    st.error("An **Open Invoices** file is required. Set one of your files to that role "
             "in the sidebar.", icon="⚠️")
    st.stop()

invoices = frames["invoices"]
max_date = pd.to_datetime(invoices["invoice_date"], errors="coerce").max()
as_of = st.sidebar.date_input(
    "As-of date", value=(max_date.date() if pd.notna(max_date) else pd.Timestamp.today().date()),
    help="Aging is measured to this date.")

with st.spinner("Analysing…"):
    analysis = kpis.build_analysis(
        invoices, frames.get("payments"), frames.get("sales"), frames.get("customers"),
        as_of=pd.Timestamp(as_of), default_terms=int(default_terms),
        days_basis=days_basis, lookback_months=int(lookback))
analysis.notes = all_notes + analysis.notes

k = analysis.kpis

# --------------------------------------------------------------------------------------
# Header, exports, KPI strip
# --------------------------------------------------------------------------------------

head_l, head_r = st.columns([3, 1])
with head_l:
    st.title("AR Aging & Collections Analytics")
    st.caption(f"{entity + '  ·  ' if entity else ''}As of **{analysis.as_of:%Y-%m-%d}**  ·  "
               f"{k['open_invoices']:,} open invoices  ·  {k['customers']:,} customers")
with head_r:
    st.write("")
    stamp = analysis.as_of.strftime("%Y%m%d")
    st.download_button(
        "⬇  Excel workbook",
        data=export_excel.build_workbook(analysis, symbol),
        file_name=f"AR_Collections_Analysis_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True)
    st.download_button(
        "⬇  PDF report",
        data=export_pdf.build_pdf(analysis, symbol, entity),
        file_name=f"AR_Collections_Report_{stamp}.pdf",
        mime="application/pdf", use_container_width=True)

errors = int((analysis.issues["severity"] == "Error").sum()) if not analysis.issues.empty else 0
if errors:
    st.warning(f"{errors} data-quality **error(s)** found. Totals may be wrong until they "
               f"are resolved — see the *Data Quality* tab.", icon="⚠️")


def _metric(column, label: str, value: str, note: str | None = None) -> None:
    """A metric plus an optional plain caption.

    Streamlit's ``delta`` argument draws a coloured up/down arrow, which would
    misread a secondary figure ("40.4% of AR") as a movement, so notes are
    rendered as captions instead.
    """
    with column:
        st.metric(label, value)
        if note:
            st.caption(note)


row1 = st.columns(5)
_metric(row1[0], "Total AR", _fmt_money(k["total_ar"], symbol),
        f"{_fmt_money(k['gross_ar'], symbol)} gross of credits"
        if k["credit_balances"] else None)
_metric(row1[1], "Past due", _fmt_money(k["past_due_ar"], symbol),
        f"{k['pct_past_due']:.1f}% of AR")
_metric(row1[2], "Over 90 days", _fmt_money(k["over_90_ar"], symbol),
        f"{k['pct_over_90']:.1f}% of AR")
_metric(row1[3], "DSO", _fmt(k["dso"], " d"),
        f"{_fmt(k['delinquent_dso'], ' d')} delinquent")
_metric(row1[4], "CEI", _fmt(k["cei"], "%"), "80% is the usual benchmark")

row2 = st.columns(5)
_metric(row2[0], "Best possible DSO", _fmt(k["best_possible_dso"], " d"),
        "floor if nothing went late")
_metric(row2[1], "Avg days to pay", _fmt(k["avg_days_to_pay"], " d"),
        "weighted by amount")
_metric(row2[2], "On-time payment rate", _fmt(k["on_time_rate"], "%"),
        "paid by due date")
_metric(row2[3], "Avg days delinquent", _fmt(k["add_days"], " d"), "balance-weighted")
_metric(row2[4], "Over credit limit", f"{k['over_limit_customers']:,}",
        f"{_fmt_money(k['over_limit_exposure'], symbol)} exposed")

# --------------------------------------------------------------------------------------
# Tabs
# --------------------------------------------------------------------------------------

tabs = st.tabs(["AR Aging", "Collection Analysis", "DSO", "Customer Detail",
                "Data Quality", "Assumptions"])

# ---- AR Aging ----
with tabs[0]:
    left, right = st.columns([1.15, 1])
    with left:
        st.subheader("AR by aging bucket")
        st.plotly_chart(charts.aging_bars(analysis.bucket_totals, symbol),
                        use_container_width=True)
    with right:
        st.subheader("Bucket detail")
        st.dataframe(
            analysis.bucket_totals, use_container_width=True, hide_index=True,
            column_config={
                "Amount": st.column_config.NumberColumn(format=_money_format(symbol)),
                "% of AR": st.column_config.NumberColumn(format="%.1f%%"),
                "Invoices": st.column_config.NumberColumn(format="%d")})
        st.caption("Aged on days past **due date**. Credit balances are listed "
                   "separately and are not netted into buckets.")

    st.subheader("Largest balances: current vs past due")
    label_col = ("customer_name" if "customer_name" in analysis.customer_aging.columns
                 else "customer_id")
    st.plotly_chart(charts.bucket_mix_bars(analysis.customer_aging, label_col, 12, symbol),
                    use_container_width=True)

    st.subheader("Aging summary by customer")
    st.dataframe(analysis.customer_aging, use_container_width=True, hide_index=True,
                 height=420)

    if not analysis.segment_aging.empty:
        st.subheader("Aging by segment")
        st.dataframe(analysis.segment_aging, use_container_width=True, hide_index=True)

    with st.expander("Invoice-level aging detail"):
        st.dataframe(analysis.detail, use_container_width=True, hide_index=True, height=420)

# ---- Collection Analysis ----
with tabs[1]:
    if analysis.cei.empty:
        st.info("CEI needs at least two months of invoice history plus payments.", icon="ℹ️")
    else:
        st.subheader("Collection Effectiveness Index")
        st.plotly_chart(charts.cei_line(analysis.cei), use_container_width=True)
        st.caption("CEI = (Beginning AR + Credit Sales − Ending AR) ÷ "
                   "(Beginning AR + Credit Sales − Ending Current AR). It compares cash "
                   "collected with cash that was collectible, so it is not distorted by "
                   "swings in sales volume the way DSO is. 100% is perfect.")

    st.subheader("Top delinquent accounts by exposure")
    if analysis.risk.empty:
        st.info("No past-due balances to rank.", icon="ℹ️")
    else:
        st.plotly_chart(charts.top_delinquent_bars(analysis.risk, 15, symbol),
                        use_container_width=True)
        show = ["rank", "customer_name", "customer_id", "total_ar", "past_due_ar",
                "pct_past_due", "over_90", "add_days", "oldest_dpd", "limit_utilization",
                "risk_score", "risk_band", "exposure"]
        st.dataframe(analysis.risk[[c for c in show if c in analysis.risk.columns]],
                     use_container_width=True, hide_index=True, height=420,
                     column_config={
                         "pct_past_due": st.column_config.NumberColumn("% past due", format="%.1f%%"),
                         "limit_utilization": st.column_config.NumberColumn("Limit used", format="%.0f%%"),
                         "risk_score": st.column_config.ProgressColumn(
                             "Risk", min_value=0, max_value=100, format="%.0f")})
        st.caption("Ranked by exposure (risk score × past-due balance). Score weights: "
                   "30% share past due, 25% age of delinquency, 20% over-90 concentration, "
                   "15% credit-limit utilisation, 10% payment slippage against terms.")

    if not analysis.behavior.empty:
        st.subheader("Payment behaviour")
        if not analysis.payment_trend.empty:
            st.plotly_chart(charts.payment_behavior_line(analysis.payment_trend),
                            use_container_width=True)
        st.dataframe(analysis.behavior, use_container_width=True, hide_index=True,
                     height=380,
                     column_config={
                         "on_time_rate": st.column_config.NumberColumn("On-time %", format="%.1f%%"),
                         "avg_days_to_pay": st.column_config.NumberColumn("Avg days to pay", format="%.1f"),
                         "slippage_vs_terms": st.column_config.NumberColumn("Slippage (days)", format="%.1f")})
        st.caption("Days to pay is weighted by amount. Slippage is days to pay less the "
                   "invoice's own terms — positive means the customer runs past terms.")

# ---- DSO ----
with tabs[2]:
    if analysis.dso_trend.empty:
        st.info("DSO needs monthly sales, or invoices spanning more than one month.",
                icon="ℹ️")
    else:
        st.subheader("DSO trend")
        st.plotly_chart(charts.dso_lines(analysis.dso_trend), use_container_width=True)
        st.caption("Standard DSO = (AR ÷ credit sales) × days in period. Best Possible DSO "
                   "uses only current AR — the floor you would hit if nothing went late. "
                   "The gap between the lines is **delinquent DSO**, the part collections "
                   "activity can actually recover.")
        st.dataframe(analysis.dso_trend, use_container_width=True, hide_index=True)

    st.subheader("DSO by customer")
    if analysis.dso_customer.empty:
        st.info("Not enough sales history per customer.", icon="ℹ️")
    else:
        st.dataframe(analysis.dso_customer, use_container_width=True, hide_index=True,
                     height=420)

    if not analysis.dso_segment.empty:
        st.subheader("DSO by segment")
        st.dataframe(analysis.dso_segment, use_container_width=True, hide_index=True)

# ---- Customer Detail ----
with tabs[3]:
    detail = analysis.detail
    names = (detail[["customer_id", "customer_name"]].drop_duplicates()
             if "customer_name" in detail.columns
             else detail[["customer_id"]].drop_duplicates().assign(customer_name=lambda d: d["customer_id"]))
    names = names.sort_values("customer_name")
    options = names["customer_id"].tolist()
    labels = dict(zip(names["customer_id"], names["customer_name"].fillna(names["customer_id"])))

    if not options:
        st.info("No open invoices to show.", icon="ℹ️")
    else:
        picked = st.selectbox("Customer", options, format_func=lambda c: labels.get(c, c))
        rows = detail.loc[detail["customer_id"] == picked]
        risk_row = analysis.risk.loc[analysis.risk["customer_id"] == picked]

        cols = st.columns(5)
        cols[0].metric("Open AR", _fmt_money(rows["open_balance"].sum(), symbol))
        cols[1].metric("Past due", _fmt_money(
            rows.loc[rows["is_past_due"], "open_balance"].sum(), symbol))
        cols[2].metric("Open invoices", f"{len(rows):,}")
        if not risk_row.empty:
            cols[3].metric("Risk score", f"{risk_row['risk_score'].iloc[0]:.0f}",
                           str(risk_row["risk_band"].iloc[0]), delta_color="off")
            util = risk_row["limit_utilization"].iloc[0]
            cols[4].metric("Credit limit used", _fmt(util, "%", 0) if pd.notna(util) else "n/a")

        behavior_row = (analysis.behavior.loc[analysis.behavior["customer_id"] == picked]
                        if not analysis.behavior.empty else pd.DataFrame())
        if not behavior_row.empty:
            b = behavior_row.iloc[0]
            st.caption(f"Pays in **{b['avg_days_to_pay']:.0f} days** on average against "
                       f"**{b['avg_terms_days']:.0f}-day** terms — "
                       f"**{b['on_time_rate']:.0f}%** of payments on time.")

        st.dataframe(rows.sort_values("days_past_due", ascending=False),
                     use_container_width=True, hide_index=True, height=420)

# ---- Data Quality ----
with tabs[4]:
    if analysis.issues.empty:
        st.success("No data-quality issues found.", icon="✅")
    else:
        counts = analysis.issues["severity"].value_counts()
        cols = st.columns(3)
        for i, severity in enumerate(("Error", "Warning", "Info")):
            cols[i].metric(severity, f"{int(counts.get(severity, 0)):,}")
        st.dataframe(analysis.issues, use_container_width=True, hide_index=True,
                     column_config={
                         "amount": st.column_config.NumberColumn("Amount", format=_money_format(symbol)),
                         "rows": st.column_config.NumberColumn("Rows", format="%d"),
                         "detail": st.column_config.TextColumn("Detail", width="large")})
        st.caption("Resolve **Errors** before relying on the totals — they can change the "
                   "numbers materially. Warnings and info are usually expected.")

# ---- Assumptions ----
with tabs[5]:
    st.subheader("Methodology")
    st.dataframe(pd.DataFrame({"Item": list(analysis.settings.keys()),
                               "Basis": [str(v) for v in analysis.settings.values()]}),
                 use_container_width=True, hide_index=True)
    if analysis.notes:
        st.subheader("Applied to this dataset")
        for note in analysis.notes:
            st.markdown(f"- {note}")
    st.subheader("Column mapping used")
    for role, cols in mappings.items():
        used = {field: source for field, source in cols.items() if source}
        if used:
            st.markdown(f"**{ROLE_LABELS[role]}**")
            st.dataframe(pd.DataFrame({"Canonical field": list(used.keys()),
                                       "Your column": list(used.values())}),
                         use_container_width=True, hide_index=True)
