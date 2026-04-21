"""
ui/charts.py — Plotly 图表构建层（Apple HIG 风格）

设计语言
--------
1. 系统色板（Apple System Colors）：低饱和、语义化
2. 克制的分隔：细线、无边框、大量留白
3. 图例分组：按语义聚类（价格 / 信号 / 指标）
4. 字体层级：靠字重而非字号区分重要性
5. 图表即内容：去除多余装饰，保留信息密度
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ---------------------------------------------------------------------------
# 设计 Token
# ---------------------------------------------------------------------------

class T:
    """Apple-inspired design tokens."""

    # 背景
    BG          = "#FFFFFF"
    BG_MUTED    = "#F5F5F7"

    # 文本
    TEXT        = "#1D1D1F"
    TEXT_MUTED  = "#6E6E73"
    TEXT_FAINT  = "#AEAEB2"

    # 分割与网格
    DIVIDER     = "#E5E5EA"
    GRID        = "#F2F2F7"

    # 语义色（Apple System Colors，已调整以适配白底）
    RED         = "#E5484D"   # 上涨 / 强力卖出
    GREEN       = "#30A46C"   # 下跌 / 强力买入
    BLUE        = "#0071E3"   # 中性蓝（MA10 / K / MACD）
    ORANGE      = "#F56E0F"   # 警示橙（MA5 / J / 信号线）
    PURPLE      = "#8E4EC6"   # 紫（MA20 / D）
    YELLOW      = "#FFB224"
    PINK        = "#E93D82"

    # 半透明区域色
    RED_BAND    = "rgba(229, 72, 77, 0.06)"
    GREEN_BAND  = "rgba(48, 164, 108, 0.06)"
    NEUTRAL_BAND = "rgba(142, 142, 147, 0.04)"

    # 字体（Apple → 思源黑体 → 系统兜底）
    FONT_FAMILY = (
        "-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', "
        "'PingFang SC', 'HarmonyOS Sans SC', 'Microsoft YaHei', 'Segoe UI', sans-serif"
    )


# ---------------------------------------------------------------------------
# 主图：K 线 + 指标 + 信号
# ---------------------------------------------------------------------------

def build_main_figure(df: pd.DataFrame, symbol: str, suggestion: str) -> go.Figure:
    """
    构建三行子图主图表（Apple 风格）。

    Row 1 (62%)：K 线 + MA5/10/20 + 买卖信号标记
    Row 2 (22%)：KDJ（K/D/J）+ RSI6
    Row 3 (16%)：MACD 柱状图 + MACD 线 + 信号线
    """
    x_vals = df["date"].dt.strftime("%Y-%m-%d")

    # ---- K 线富文本 hover（中文 + 涨跌幅配色） ----
    kline_hover_texts = _build_kline_hover_texts(df)

    # --- Y 轴范围预计算 ---
    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    price_pad = max((price_max - price_min) * 0.05, 1e-6)

    kdj_rsi_all = pd.concat([df["K"], df["D"], df["J"], df["RSI6"]], axis=0).dropna()
    kdj_rsi_min = float(kdj_rsi_all.min()) if not kdj_rsi_all.empty else -10.0
    kdj_rsi_max = float(kdj_rsi_all.max()) if not kdj_rsi_all.empty else 110.0
    kdj_rsi_pad = max((kdj_rsi_max - kdj_rsi_min) * 0.1, 8.0)
    kdj_rsi_low  = float(min(-10.0, kdj_rsi_min - kdj_rsi_pad))
    kdj_rsi_high = float(max(110.0, kdj_rsi_max + kdj_rsi_pad))

    macd_all = pd.concat([df["MACD"], df["MACD_SIGNAL"], df["MACD_HIST"]], axis=0).dropna()
    macd_min = float(macd_all.min()) if not macd_all.empty else -1.0
    macd_max = float(macd_all.max()) if not macd_all.empty else 1.0
    macd_pad = max((macd_max - macd_min) * 0.2, 0.02)

    # --- 布局：极简的子图标题 ---
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.62, 0.22, 0.16],
        subplot_titles=["", "", ""],  # 标题使用自定义 annotation
    )

    # ============================================================
    # Row 1 — 价格（K 线 + 均线）
    # ============================================================
    fig.add_trace(go.Candlestick(
        x=x_vals, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="K线",
        increasing=dict(line=dict(color=T.RED, width=1),   fillcolor=T.RED),
        decreasing=dict(line=dict(color=T.GREEN, width=1), fillcolor=T.GREEN),
        whiskerwidth=0.4,
        hoverinfo="skip",  # 默认 hover 由下方不可见 scatter 叠加层统一接管
    ), row=1, col=1)

    # 不可见 scatter 叠加层：承载 K 线的富文本 hover（中文标签 + 涨跌幅配色）
    fig.add_trace(go.Scatter(
        x=x_vals, y=df["close"],
        mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0),
        name="",
        showlegend=False,
        customdata=kline_hover_texts,
        hovertemplate="%{customdata}<extra></extra>",
        hoverlabel=dict(align="left"),
    ), row=1, col=1)

    for ma, color in [("MA5", T.ORANGE), ("MA10", T.BLUE), ("MA20", T.PURPLE)]:
        fig.add_trace(go.Scatter(
            x=x_vals, y=df[ma], name=ma,
            line=dict(color=color, width=1.6),
            opacity=0.9,
            hovertemplate=(
                f"<span style='color:{color}'>●</span> {ma} "
                f"<b>%{{y:.2f}}</b><extra></extra>"
            ),
        ), row=1, col=1)

    # ============================================================
    # Row 1 — 信号（大标记 + 白色双层描边 + 垂直参考线）
    # ============================================================
    strong_buys  = df[df["strong_buy_signal"]]  if "strong_buy_signal"  in df.columns else df.iloc[0:0]
    strong_sells = df[df["strong_sell_signal"]] if "strong_sell_signal" in df.columns else df.iloc[0:0]
    buys  = df[df["buy_signal"]  & ~df.get("strong_buy_signal",  pd.Series(False, index=df.index))]
    sells = df[df["sell_signal"] & ~df.get("strong_sell_signal", pd.Series(False, index=df.index))]
    ob    = df[df["overbought"]] if "overbought" in df.columns else df.iloc[0:0]

    # 垂直参考线：仅在强信号处画贯穿主图的彩色虚线，辅助快速定位
    for _, row_ in strong_buys.iterrows():
        _add_signal_guideline(fig, row_["date"].strftime("%Y-%m-%d"), T.GREEN, opacity=0.45)
    for _, row_ in strong_sells.iterrows():
        _add_signal_guideline(fig, row_["date"].strftime("%Y-%m-%d"), T.RED,   opacity=0.45)

    # 买入：更大的三角形 + 粗白色描边 + 同色柔光
    fig.add_trace(go.Scatter(
        x=buys["date"].dt.strftime("%Y-%m-%d"), y=buys["low"] * 0.988,
        mode="markers", name="买入",
        marker=dict(
            size=16, color=T.GREEN, symbol="triangle-up",
            line=dict(width=2.5, color="#FFFFFF"),
            opacity=0.95,
        ),
        hovertemplate=(
            f"<span style='color:{T.GREEN}'>▲</span> "
            f"<b style='color:{T.GREEN}'>买入信号</b><extra></extra>"
        ),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=sells["date"].dt.strftime("%Y-%m-%d"), y=sells["high"] * 1.012,
        mode="markers", name="卖出",
        marker=dict(
            size=16, color=T.RED, symbol="triangle-down",
            line=dict(width=2.5, color="#FFFFFF"),
            opacity=0.95,
        ),
        hovertemplate=(
            f"<span style='color:{T.RED}'>▼</span> "
            f"<b style='color:{T.RED}'>卖出信号</b><extra></extra>"
        ),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=strong_buys["date"].dt.strftime("%Y-%m-%d"), y=strong_buys["low"] * 0.982,
        mode="markers", name="强力买入",
        marker=dict(
            size=22, color=T.GREEN, symbol="star",
            line=dict(width=3, color="#FFFFFF"),
        ),
        hovertemplate=(
            f"<span style='color:{T.GREEN}'>★</span> "
            f"<b style='color:{T.GREEN}'>强力买入</b><extra></extra>"
        ),
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=strong_sells["date"].dt.strftime("%Y-%m-%d"), y=strong_sells["high"] * 1.018,
        mode="markers", name="强力卖出",
        marker=dict(
            size=20, color=T.RED, symbol="diamond",
            line=dict(width=3, color="#FFFFFF"),
        ),
        hovertemplate=(
            f"<span style='color:{T.RED}'>◆</span> "
            f"<b style='color:{T.RED}'>强力卖出</b><extra></extra>"
        ),
    ), row=1, col=1)

    # 超买预警：不进图例（次要信息），保留小点
    if not ob.empty:
        fig.add_trace(go.Scatter(
            x=ob["date"].dt.strftime("%Y-%m-%d"), y=ob["high"] * 1.015,
            mode="markers",
            marker=dict(size=5, color=T.ORANGE, symbol="circle-open",
                        line=dict(width=1.2, color=T.ORANGE)),
            hovertemplate=(
                f"<span style='color:{T.ORANGE}'>○</span> "
                f"<span style='color:{T.ORANGE}'>超买预警</span><extra></extra>"
            ),
            showlegend=False,
        ), row=1, col=1)

    # 最近一次强信号的气泡标注（仅最近一次，避免拥挤）
    if not strong_buys.empty:
        rb = strong_buys.iloc[-1]
        fig.add_annotation(
            x=rb["date"].strftime("%Y-%m-%d"), y=float(rb["low"]) * 0.99,
            text="强力买入", showarrow=True, arrowhead=0, arrowwidth=1,
            arrowcolor=T.GREEN, ax=0, ay=36,
            font=dict(size=11, color=T.GREEN, family=T.FONT_FAMILY),
            bgcolor="rgba(255,255,255,0.95)",
            bordercolor=T.GREEN, borderwidth=1, borderpad=4,
            row=1, col=1,
        )
    if not strong_sells.empty:
        rs = strong_sells.iloc[-1]
        fig.add_annotation(
            x=rs["date"].strftime("%Y-%m-%d"), y=float(rs["high"]) * 1.01,
            text="强力卖出", showarrow=True, arrowhead=0, arrowwidth=1,
            arrowcolor=T.RED, ax=0, ay=-36,
            font=dict(size=11, color=T.RED, family=T.FONT_FAMILY),
            bgcolor="rgba(255,255,255,0.95)",
            bordercolor=T.RED, borderwidth=1, borderpad=4,
            row=1, col=1,
        )

    # ============================================================
    # Row 2 — 振荡指标（图例隐藏，改为子图内嵌色标）
    # ============================================================
    fig.add_hrect(y0=0,  y1=20,  fillcolor=T.GREEN_BAND, line_width=0, row=2, col=1)
    fig.add_hrect(y0=80, y1=100, fillcolor=T.RED_BAND,   line_width=0, row=2, col=1)
    for y in [20, 50, 80]:
        fig.add_hline(y=y, line_dash="dot", line_color=T.DIVIDER, line_width=1, row=2, col=1)

    for col, color, dash, width in [
        ("K",    T.BLUE,   "solid", 1.6),
        ("D",    T.PURPLE, "solid", 1.6),
        ("J",    T.ORANGE, "solid", 1.4),
        ("RSI6", T.RED,    "dot",   1.6),
    ]:
        fig.add_trace(go.Scatter(
            x=x_vals, y=df[col], name=col, connectgaps=False,
            line=dict(color=color, width=width, dash=dash),
            hovertemplate=(
                f"<span style='color:{color}'>●</span> {col} "
                f"<b>%{{y:.2f}}</b><extra></extra>"
            ),
            showlegend=False,
        ), row=2, col=1)

    # ============================================================
    # Row 3 — 趋势指标（图例隐藏，改为子图内嵌色标）
    # ============================================================
    hist_colors = df["MACD_HIST"].apply(lambda v: T.RED if v >= 0 else T.GREEN)
    fig.add_trace(go.Bar(
        x=x_vals, y=df["MACD_HIST"], name="HIST",
        marker_color=hist_colors, opacity=0.35,
        hovertemplate=(
            f"<span style='color:{T.TEXT_FAINT}'>▮</span> 柱状 "
            f"<b>%{{y:.3f}}</b><extra></extra>"
        ),
        showlegend=False,
    ), row=3, col=1)
    fig.add_trace(go.Scatter(
        x=x_vals, y=df["MACD"], name="MACD",
        line=dict(color=T.BLUE, width=1.6),
        hovertemplate=(
            f"<span style='color:{T.BLUE}'>●</span> MACD "
            f"<b>%{{y:.3f}}</b><extra></extra>"
        ),
        showlegend=False,
    ), row=3, col=1)
    fig.add_trace(go.Scatter(
        x=x_vals, y=df["MACD_SIGNAL"], name="SIGNAL",
        line=dict(color=T.ORANGE, width=1.6),
        hovertemplate=(
            f"<span style='color:{T.ORANGE}'>●</span> 信号线 "
            f"<b>%{{y:.3f}}</b><extra></extra>"
        ),
        showlegend=False,
    ), row=3, col=1)
    fig.add_hline(y=0, line_dash="dot", line_color=T.DIVIDER, line_width=1, row=3, col=1)

    # ============================================================
    # 子图标题 + 内嵌色标徽章（取代冗长的图例）
    # ----------------------------------------------------------------
    # 根据 row_heights=[0.62, 0.22, 0.16] 与 vertical_spacing=0.07 推算：
    #   Row 1 顶部 ≈ 1.000    Row 2 顶部 ≈ 0.397    Row 3 顶部 ≈ 0.138
    # ============================================================
    _add_section_title(fig, f"{symbol} · K线", y=1.00)
    _add_section_title(fig, "振荡 · KDJ / RSI", y=0.403)
    _add_legend_badges(
        fig, y=0.403, x_start=0.170,
        items=[("K", T.BLUE), ("D", T.PURPLE), ("J", T.ORANGE), ("RSI6", T.RED)],
    )
    _add_section_title(fig, "趋势 · MACD", y=0.144)
    _add_legend_badges(
        fig, y=0.144, x_start=0.140,
        items=[("MACD", T.BLUE), ("SIGNAL", T.ORANGE), ("HIST", T.TEXT_FAINT)],
    )

    # 右上角建议 pill
    _add_suggestion_pill(fig, suggestion)

    # ============================================================
    # 轴样式
    # ============================================================
    fig.update_xaxes(
        type="category",
        showgrid=False, showline=False, zeroline=False,
        rangeslider_visible=False, fixedrange=False,
        tickfont=dict(color=T.TEXT_MUTED, size=11, family=T.FONT_FAMILY),
        tickangle=0, nticks=10, automargin=True,
        ticks="outside", ticklen=4, tickcolor=T.DIVIDER,
    )
    fig.update_xaxes(showticklabels=False, row=1, col=1)
    fig.update_xaxes(showticklabels=False, row=2, col=1)
    fig.update_xaxes(showticklabels=True,  row=3, col=1)

    y_common = dict(
        showline=False, zeroline=False,
        gridcolor=T.GRID, gridwidth=1,
        tickfont=dict(color=T.TEXT_MUTED, size=11, family=T.FONT_FAMILY),
        automargin=True,
    )
    fig.update_yaxes(
        row=1, col=1,
        fixedrange=False, autorange=True, rangemode="normal",
        range=[price_min - price_pad, price_max + price_pad],
        **y_common,
    )
    fig.update_yaxes(
        row=2, col=1,
        fixedrange=True, range=[kdj_rsi_low, kdj_rsi_high],
        tickmode="linear", dtick=20,
        **y_common,
    )
    fig.update_yaxes(
        row=3, col=1,
        fixedrange=True, range=[macd_min - macd_pad, macd_max + macd_pad],
        **y_common,
    )

    # ============================================================
    # 整体布局
    # ============================================================
    fig.update_layout(
        paper_bgcolor=T.BG,
        plot_bgcolor=T.BG,
        font=dict(family=T.FONT_FAMILY, size=12, color=T.TEXT),
        dragmode="pan",
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="rgba(255,255,255,0.98)",
            bordercolor=T.DIVIDER,
            font=dict(family=T.FONT_FAMILY, size=12, color=T.TEXT),
            align="left",
            namelength=-1,
        ),
        height=1080,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="center", x=0.5,
            font=dict(size=12, color=T.TEXT, family=T.FONT_FAMILY),
            bgcolor="rgba(0,0,0,0)",
            borderwidth=0,
            itemsizing="constant",
            itemwidth=30,
        ),
        margin=dict(l=20, r=20, t=90, b=32),
        bargap=0.15,
    )

    return fig


# ---------------------------------------------------------------------------
# 绩效图：策略净值 vs 基准净值
# ---------------------------------------------------------------------------

def build_performance_figure(perf: dict) -> go.Figure:
    """策略累计净值 vs 基准净值，带填充渐变的极简折线图。"""
    fig = go.Figure()

    # 基准线 — 次级色，细线
    fig.add_trace(go.Scatter(
        x=perf["dates"], y=perf["base_curve"],
        mode="lines", name="基准净值",
        line=dict(color=T.TEXT_FAINT, width=1.6, dash="dot"),
        hovertemplate="基准 %{y:.3f}<extra></extra>",
    ))

    # 策略线 — 主角：蓝色实线 + 半透明填充
    fig.add_trace(go.Scatter(
        x=perf["dates"], y=perf["equity_curve"],
        mode="lines", name="策略净值",
        line=dict(color=T.BLUE, width=2.4, shape="spline", smoothing=0.4),
        fill="tozeroy",
        fillcolor="rgba(0, 113, 227, 0.06)",
        hovertemplate="策略 %{y:.3f}<extra></extra>",
    ))

    # 1.0 基准线
    fig.add_hline(y=1.0, line_dash="dot", line_color=T.DIVIDER, line_width=1)

    fig.update_layout(
        paper_bgcolor=T.BG,
        plot_bgcolor=T.BG,
        font=dict(family=T.FONT_FAMILY, size=12, color=T.TEXT),
        height=340,
        margin=dict(l=16, r=16, t=24, b=16),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
            font=dict(size=11, color=T.TEXT_MUTED, family=T.FONT_FAMILY),
            bgcolor="rgba(0,0,0,0)",
        ),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="rgba(255,255,255,0.96)",
            bordercolor=T.DIVIDER,
            font=dict(family=T.FONT_FAMILY, size=12, color=T.TEXT),
        ),
    )
    fig.update_xaxes(
        showgrid=False, showline=False, zeroline=False,
        tickfont=dict(color=T.TEXT_MUTED, size=11, family=T.FONT_FAMILY),
    )
    fig.update_yaxes(
        showline=False, zeroline=False,
        gridcolor=T.GRID, gridwidth=1,
        tickfont=dict(color=T.TEXT_MUTED, size=11, family=T.FONT_FAMILY),
    )
    return fig


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _build_kline_hover_texts(df: pd.DataFrame) -> list[str]:
    """
    为每一根 K 线预生成结构化的 HTML hover 文本。

    排版（信息优先级从高到低）：
        行 1：收盘价 · 涨跌绝对值 · 涨跌百分比（带红/绿配色、上下箭头）
        行 2：开盘 · 最高 · 最低（灰色次级信息，并排紧凑）

    A 股配色约定：涨=红、跌=绿、平=中性灰。
    """
    prev_close = df["close"].shift(1)
    change_abs = df["close"] - prev_close
    pct        = change_abs / prev_close * 100

    def _fmt_change(ac: float, pc: float) -> str:
        if pd.isna(pc):
            return f"<span style='color:{T.TEXT_MUTED}'>——</span>"
        if pc > 0:
            return (
                f"<span style='color:{T.RED}'>"
                f"<b>▲ {ac:+.2f}　{pc:+.2f}%</b>"
                f"</span>"
            )
        if pc < 0:
            return (
                f"<span style='color:{T.GREEN}'>"
                f"<b>▼ {ac:+.2f}　{pc:+.2f}%</b>"
                f"</span>"
            )
        return f"<span style='color:{T.TEXT_MUTED}'>— 0.00%</span>"

    texts: list[str] = []
    opens  = df["open"].to_numpy()
    highs  = df["high"].to_numpy()
    lows   = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    pcts   = pct.to_numpy()
    abss   = change_abs.to_numpy()

    for i in range(len(df)):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        chg = _fmt_change(abss[i], pcts[i])
        texts.append(
            f"<span style='color:{T.TEXT_MUTED}'>收盘</span> "
            f"<b>{c:.2f}</b>　{chg}"
            f"<br>"
            f"<span style='color:{T.TEXT_MUTED}'>"
            f"开 {o:.2f}　高 {h:.2f}　低 {l:.2f}"
            f"</span>"
        )
    return texts


def _add_section_title(fig: go.Figure, text: str, *, y: float) -> None:
    """在各子图左上角以 Apple 风格添加标题（左对齐、小字号、主色、半粗）。"""
    fig.add_annotation(
        text=f"<b>{text}</b>",
        xref="paper", yref="paper",
        x=0.0, y=y,
        xanchor="left", yanchor="bottom",
        showarrow=False,
        font=dict(size=12, color=T.TEXT, family=T.FONT_FAMILY),
    )


def _add_signal_guideline(
    fig: go.Figure, x: str, color: str,
    *, opacity: float = 0.45, width: float = 1.2,
) -> None:
    """
    在信号日期处添加一条贯穿主图（row=1）的垂直参考虚线。
    使用 add_shape 而非 add_vline，以兼容 category 类型的 x 轴。
    """
    fig.add_shape(
        type="line",
        xref="x", yref="y domain",
        x0=x, x1=x, y0=0, y1=1,
        line=dict(color=color, width=width, dash="dot"),
        opacity=opacity,
        layer="below",
    )


def _add_legend_badges(
    fig: go.Figure, *, y: float, x_start: float,
    items: list[tuple[str, str]],
) -> None:
    """
    在子图右上角添加内嵌色标徽章序列（● Name · ● Name …）。
    避免挤在统一图例里，让 KDJ/MACD 子图自带标识。
    """
    x = x_start
    for name, color in items:
        # 彩色圆点 + 名称
        fig.add_annotation(
            text=f"<span style='color:{color}'>●</span> <span style='color:{T.TEXT_MUTED}'>{name}</span>",
            xref="paper", yref="paper",
            x=x, y=y,
            xanchor="left", yanchor="bottom",
            showarrow=False,
            font=dict(size=11, family=T.FONT_FAMILY),
        )
        # 根据名称长度计算下一项的起点（近似 char width）
        x += 0.042 + 0.012 * len(name)


def _add_suggestion_pill(fig: go.Figure, suggestion: str) -> None:
    """在图表右上角绘制建议胶囊。"""
    color_map = {
        "强力卖出": T.RED,
        "建议卖出": T.RED,
        "强力买入": T.GREEN,
        "建议买入": T.GREEN,
        "持股":     T.BLUE,
        "关注反弹": T.ORANGE,
        "观望":     T.TEXT_MUTED,
    }
    pill_color = color_map.get(suggestion, T.TEXT_MUTED)
    fig.add_annotation(
        text=f"<b>建议 · {suggestion}</b>",
        xref="paper", yref="paper",
        x=1.0, y=1.00,
        xanchor="right", yanchor="bottom",
        showarrow=False,
        font=dict(size=12, color=pill_color, family=T.FONT_FAMILY),
        bgcolor="rgba(255,255,255,0.0)",
        bordercolor=pill_color, borderwidth=1, borderpad=6,
    )
