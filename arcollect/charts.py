"""Figure builders.

Plotly powers the interactive dashboard; matplotlib renders the same figures as
images for the PDF. Both draw from one palette so screen, Excel and PDF read as a
single system.

Palette: validated categorical slots 1-3 and the reserved status ramp. The app pins
a light theme (see ``.streamlit/config.toml``) so one surface serves every output.
"""

from __future__ import annotations

import io

import pandas as pd
import plotly.graph_objects as go

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e6e5e1"

# Categorical slots, in fixed order. Never cycled.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")

# Reserved status palette -- always paired with a text label, never colour alone.
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a",
          "critical": "#d03b3b"}
RISK_COLORS = {"Low": STATUS["good"], "Moderate": STATUS["warning"],
               "High": STATUS["serious"], "Critical": STATUS["critical"]}

FONT = dict(family="Inter, -apple-system, Segoe UI, Helvetica, Arial, sans-serif",
            size=13, color=INK)


def _layout(fig: go.Figure, title: str = "", height: int = 320,
            legend: bool = False, ylab: str = "", xlab: str = "") -> go.Figure:
    fig.update_layout(
        # An omitted title serialises to None and Streamlit renders it as
        # the literal text "undefined", so always pass a string.
        title=dict(text=title or "", font=dict(size=15, color=INK)),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=FONT,
        height=height, margin=dict(l=56, r=24, t=48 if title else 16, b=44),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=12, color=INK_MUTED)),
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=GRID,
                     tickfont=dict(color=INK_MUTED, size=12), title=xlab,
                     title_font=dict(size=12, color=INK_MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor="rgba(0,0,0,0)",
                     tickfont=dict(color=INK_MUTED, size=12), title=ylab,
                     title_font=dict(size=12, color=INK_MUTED))
    return fig


def _money_ticks(fig: go.Figure, symbol: str = "$") -> go.Figure:
    fig.update_yaxes(tickprefix=symbol, tickformat=",.0f")
    return fig


def aging_bars(buckets: pd.DataFrame, symbol: str = "$") -> go.Figure:
    """Single-series bars. Bucket identity comes from the axis, so colour is
    uniform -- colouring by bucket would only re-encode the x-axis."""
    total = buckets["Amount"].sum()
    labels = [f"{symbol}{v:,.0f}<br><span style='color:{INK_MUTED}'>"
              f"{(v / total * 100 if total else 0):.1f}%</span>"
              for v in buckets["Amount"]]
    fig = go.Figure(go.Bar(
        x=buckets["Bucket"], y=buckets["Amount"], marker_color=SERIES[0],
        marker_line_width=0, text=labels, textposition="outside",
        textfont=dict(size=11, color=INK),
        hovertemplate=("<b>%{x}</b><br>" + symbol + "%{y:,.2f}"
                       "<br>%{customdata[0]:,} invoices<extra></extra>"),
        customdata=buckets[["Invoices"]].to_numpy(),
    ))
    fig.update_traces(marker_cornerradius=4)
    _layout(fig, height=340, ylab="Open balance")
    _money_ticks(fig, symbol)
    fig.update_yaxes(range=[0, buckets["Amount"].max() * 1.22 if total else 1])
    return fig


def dso_lines(trend: pd.DataFrame) -> go.Figure:
    """DSO against Best Possible DSO. The gap between them is the collectable
    opportunity, so both belong on one axis in the same units (days)."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=trend["period"], y=trend["dso"], name="DSO", mode="lines+markers",
        line=dict(color=SERIES[0], width=2), marker=dict(size=8, color=SERIES[0],
                                                         line=dict(color=SURFACE, width=2)),
        hovertemplate="DSO %{y:.1f} days<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=trend["period"], y=trend["best_possible_dso"], name="Best possible DSO",
        mode="lines+markers", line=dict(color=SERIES[1], width=2, dash="dot"),
        marker=dict(size=8, color=SERIES[1], line=dict(color=SURFACE, width=2)),
        hovertemplate="BPDSO %{y:.1f} days<extra></extra>"))
    _layout(fig, height=340, legend=True, ylab="Days")
    return fig


def cei_line(cei: pd.DataFrame) -> go.Figure:
    """One series, so no legend box -- the title names it."""
    fig = go.Figure(go.Scatter(
        x=cei["period"], y=cei["cei"], mode="lines+markers",
        line=dict(color=SERIES[0], width=2),
        marker=dict(size=8, color=SERIES[0], line=dict(color=SURFACE, width=2)),
        hovertemplate="CEI %{y:.1f}%<extra></extra>", name="CEI"))
    # 80% is the conventional "healthy collections" threshold.
    # Right-aligned: CEI series typically sit high on the left of the range, so a
    # left-anchored label lands on the line.
    fig.add_hline(y=80, line=dict(color=INK_MUTED, width=1, dash="dash"),
                  annotation_text="80% benchmark", annotation_position="bottom right",
                  annotation_font=dict(size=11, color=INK_MUTED))
    _layout(fig, height=320, ylab="CEI %")
    fig.update_yaxes(range=[0, 105], ticksuffix="%")
    return fig


def payment_behavior_line(trend: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=trend["period"], y=trend["avg_days_to_pay"], mode="lines+markers",
        line=dict(color=SERIES[0], width=2),
        marker=dict(size=8, color=SERIES[0], line=dict(color=SURFACE, width=2)),
        hovertemplate="%{y:.1f} days<extra></extra>", name="Avg days to pay"))
    _layout(fig, height=300, ylab="Days to pay")
    return fig


def top_delinquent_bars(risk: pd.DataFrame, n: int = 15, symbol: str = "$") -> go.Figure:
    """Horizontal bars ranked by past-due exposure, coloured by risk band.

    Risk band is a reserved status colour and is also printed in the hover text and
    the adjacent table, so the colour never carries the meaning alone.
    """
    top = risk.head(n).iloc[::-1]
    label_col = "customer_name" if "customer_name" in top.columns else "customer_id"
    colors = [RISK_COLORS.get(str(b), SERIES[0]) for b in top["risk_band"]]
    fig = go.Figure(go.Bar(
        x=top["past_due_ar"], y=top[label_col].astype(str), orientation="h",
        marker_color=colors, marker_line_width=0,
        customdata=top[["risk_score", "risk_band", "pct_past_due", "total_ar"]].to_numpy(),
        hovertemplate=("<b>%{y}</b><br>Past due " + symbol + "%{x:,.0f}"
                       "<br>Total AR " + symbol + "%{customdata[3]:,.0f}"
                       "<br>%{customdata[2]:.0f}% past due"
                       "<br>Risk %{customdata[0]:.0f} (%{customdata[1]})<extra></extra>")))
    fig.update_traces(marker_cornerradius=4)
    _layout(fig, height=max(320, 26 * len(top)), xlab="Past-due balance")
    fig.update_xaxes(tickprefix=symbol, tickformat=",.0f", showgrid=True, gridcolor=GRID)
    fig.update_yaxes(showgrid=False)
    return fig


def bucket_mix_bars(summary: pd.DataFrame, label_col: str, n: int = 12,
                    symbol: str = "$") -> go.Figure:
    """Current vs past due for the largest accounts -- two series, so a legend."""
    top = summary.head(n).iloc[::-1]
    labels = top[label_col].astype(str)
    past_due = top["Past Due"]
    current = top["Total"] - past_due
    fig = go.Figure()
    fig.add_trace(go.Bar(y=labels, x=current, orientation="h", name="Current",
                         marker_color=SERIES[0], marker_line=dict(color=SURFACE, width=2),
                         hovertemplate="Current " + symbol + "%{x:,.0f}<extra></extra>"))
    fig.add_trace(go.Bar(y=labels, x=past_due, orientation="h", name="Past due",
                         marker_color=SERIES[1], marker_line=dict(color=SURFACE, width=2),
                         hovertemplate="Past due " + symbol + "%{x:,.0f}<extra></extra>"))
    fig.update_layout(barmode="stack")
    _layout(fig, height=max(320, 28 * len(top)), legend=True, xlab="Open balance")
    fig.update_xaxes(tickprefix=symbol, tickformat=",.0f", showgrid=True, gridcolor=GRID)
    fig.update_yaxes(showgrid=False)
    return fig


# --------------------------------------------------------------------------------------
# Matplotlib renderings for the PDF -- same data, same palette.
# --------------------------------------------------------------------------------------

def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _style_axes(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=8, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def png_aging(buckets: pd.DataFrame, symbol: str = "$") -> io.BytesIO:
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(6.6, 2.7), dpi=200)
    ax.bar(buckets["Bucket"], buckets["Amount"], color=SERIES[0], width=0.62)
    total = buckets["Amount"].sum()
    for x, v in zip(buckets["Bucket"], buckets["Amount"]):
        ax.annotate(f"{symbol}{v:,.0f}\n{(v / total * 100 if total else 0):.1f}%",
                    (x, v), ha="center", va="bottom", fontsize=7, color=INK,
                    xytext=(0, 3), textcoords="offset points")
    _style_axes(ax)
    ax.set_ylim(0, buckets["Amount"].max() * 1.28 if total else 1)
    ax.yaxis.set_major_formatter(lambda v, _: f"{symbol}{v:,.0f}")
    fig.tight_layout()
    return _save(fig)


def png_dso(trend: pd.DataFrame) -> io.BytesIO:
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(6.6, 2.5), dpi=200)
    ax.plot(trend["period"], trend["dso"], color=SERIES[0], lw=2, marker="o",
            ms=4, label="DSO")
    ax.plot(trend["period"], trend["best_possible_dso"], color=SERIES[1], lw=2,
            ls=":", marker="o", ms=4, label="Best possible DSO")
    _style_axes(ax)
    ax.set_ylabel("Days", color=INK_MUTED, fontsize=8)
    leg = ax.legend(frameon=False, fontsize=8, loc="upper left", ncol=2)
    for text in leg.get_texts():
        text.set_color(INK_MUTED)
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    return _save(fig)


def png_cei(cei: pd.DataFrame) -> io.BytesIO:
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(6.6, 2.3), dpi=200)
    ax.plot(cei["period"], cei["cei"], color=SERIES[0], lw=2, marker="o", ms=4)
    ax.axhline(80, color=INK_MUTED, lw=1, ls="--")
    # Anchored to the axes, not the data, so it never lands on the series.
    ax.annotate("80% benchmark", xy=(0.99, 0.80), xycoords="axes fraction",
                fontsize=7, color=INK_MUTED, ha="right", va="bottom",
                bbox=dict(fc=SURFACE, ec="none", pad=1.5))
    _style_axes(ax)
    ax.set_ylim(0, 105)
    ax.set_ylabel("CEI %", color=INK_MUTED, fontsize=8)
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    return _save(fig)


def _save(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=SURFACE)
    fig.clf()
    buf.seek(0)
    return buf
