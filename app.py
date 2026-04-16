import warnings
from datetime import datetime, timedelta, date
import os

import akshare as ak
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

warnings.filterwarnings("ignore")


DEFAULT_CODES = ["09992", "01114"]
APP_VERSION = "v3.3"
SENSITIVITY_OPTIONS = [str(i) for i in range(1, 11)] + ["手动微调"]


def normalize_code(code: str) -> str:
    code = str(code).strip().upper().replace(".HK", "").replace("HK", "")
    digits = "".join(ch for ch in code if ch.isdigit())
    return digits.zfill(5) if digits else ""


def get_preset(level: str) -> dict:
    lv = int(level)
    if 1 <= lv <= 3:
        return {
            "kdj_k": 18,
            "kdj_d": 3,
            "kdj_smooth": 3,
            "rsi_length": 24,
            "macd_fast": 26,
            "macd_slow": 52,
            "macd_signal": 18,
            "kdj_low": 20,
            "kdj_high": 80,
            "rsi_low": 25,
            "rsi_high": 75,
        }
    if 4 <= lv <= 7:
        return {
            "kdj_k": 9,
            "kdj_d": 3,
            "kdj_smooth": 3,
            "rsi_length": 14,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "kdj_low": 20,
            "kdj_high": 80,
            "rsi_low": 30,
            "rsi_high": 70,
        }
    return {
        "kdj_k": 5,
        "kdj_d": 3,
        "kdj_smooth": 3,
        "rsi_length": 6,
        "macd_fast": 6,
        "macd_slow": 13,
        "macd_signal": 5,
        "kdj_low": 35,
        "kdj_high": 65,
        "rsi_low": 35,
        "rsi_high": 65,
    }


def apply_preset_to_state(level: str) -> None:
    params = get_preset(level)
    st.session_state["updating_from_preset"] = True
    for k, v in params.items():
        st.session_state[k] = v
    st.session_state["sensitivity_level"] = level
    st.session_state["updating_from_preset"] = False


def on_sensitivity_change() -> None:
    level = st.session_state.get("sensitivity_level", "5")
    if level != "手动微调":
        apply_preset_to_state(level)


def on_expert_change() -> None:
    if st.session_state.get("updating_from_preset", False):
        return
    st.session_state["sensitivity_level"] = "手动微调"


@st.cache_data(ttl=300)
def fetch_hk_data(
    symbol: str,
    adjust: str = "qfq",
    period: str = "daily",
    backtest_start_date: date | None = None,
    backtest_end_date: date | None = None,
) -> pd.DataFrame:
    start_dt = backtest_start_date or (datetime.now() - timedelta(days=365)).date()
    end_dt = backtest_end_date or datetime.now().date()
    start_date = pd.to_datetime(start_dt).strftime("%Y%m%d")
    end_date = pd.to_datetime(end_dt).strftime("%Y%m%d")
    proxy_keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ]
    backup_proxy = {k: os.environ.get(k) for k in proxy_keys}
    try:
        # 部分环境下系统代理会导致 akshare 请求失败，临时禁用可提高稳定性
        for k in proxy_keys:
            os.environ.pop(k, None)
        df = ak.stock_hk_hist(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        for k, v in backup_proxy.items():
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    # 主源失败时自动降级到 stock_hk_daily，避免接口波动导致全页面无数据
    if df is None or df.empty:
        try:
            df = ak.stock_hk_daily(symbol=symbol, adjust=adjust)
        except Exception:
            return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    rename_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
    }
    df = df.rename(columns=rename_map)
    if "date" not in df.columns and df.index.name is not None and "date" in str(df.index.name).lower():
        df = df.reset_index()
    if "date" not in df.columns and isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index().rename(columns={"index": "date"})

    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)
    df = df[(df["date"] >= pd.to_datetime(start_date)) & (df["date"] <= pd.to_datetime(end_date))]
    return df


def compute_indicators(
    df: pd.DataFrame,
    kdj_k: int,
    kdj_d: int,
    kdj_smooth: int,
    rsi_length: int,
    macd_fast: int,
    macd_slow: int,
    macd_signal: int,
) -> pd.DataFrame:
    out = df.copy()
    out["MA5"] = out["close"].rolling(window=5, min_periods=1).mean()
    out["MA10"] = out["close"].rolling(window=10, min_periods=1).mean()
    out["MA20"] = out["close"].rolling(window=20, min_periods=1).mean()

    ll = out["low"].rolling(window=kdj_k, min_periods=1).min()
    hh = out["high"].rolling(window=kdj_k, min_periods=1).max()
    rsv = ((out["close"] - ll) / (hh - ll).replace(0, pd.NA) * 100).fillna(50)
    out["K"] = rsv.ewm(alpha=1 / max(kdj_smooth, 1), adjust=False).mean()
    out["D"] = out["K"].ewm(alpha=1 / max(kdj_d, 1), adjust=False).mean()
    out["J"] = 3 * out["K"] - 2 * out["D"]

    delta = out["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / max(rsi_length, 1), adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / max(rsi_length, 1), adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out["RSI6"] = (100 - (100 / (1 + rs))).fillna(50)

    ema_fast = out["close"].ewm(span=macd_fast, adjust=False).mean()
    ema_slow = out["close"].ewm(span=macd_slow, adjust=False).mean()
    out["MACD"] = ema_fast - ema_slow
    out["MACD_SIGNAL"] = out["MACD"].ewm(span=macd_signal, adjust=False).mean()
    out["MACD_HIST"] = out["MACD"] - out["MACD_SIGNAL"]

    return out


def calculate_signals(
    df: pd.DataFrame,
    kdj_low: int = 20,
    kdj_high: int = 80,
    rsi_low: int = 30,
    rsi_high: int = 70,
    stay_days: int = 2,
) -> pd.DataFrame:
    out = df.copy()
    out["golden_cross"] = (out["K"] > out["D"]) & (out["K"].shift(1) <= out["D"].shift(1))
    out["dead_cross"] = (out["K"] < out["D"]) & (out["K"].shift(1) >= out["D"].shift(1))
    out["j_turn_up"] = out["J"] > out["J"].shift(1)
    out["j_turn_down"] = out["J"] < out["J"].shift(1)

    prev_hist = out["MACD_HIST"].shift(1)
    out["macd_green_shrinking"] = (out["MACD_HIST"] < 0) & (prev_hist < 0) & (out["MACD_HIST"] > prev_hist)

    low_zone = (out["J"] < kdj_low) & (out["RSI6"] < rsi_low)
    high_zone = (out["J"] > kdj_high) & (out["RSI6"] > rsi_high)
    low_stay = low_zone.rolling(stay_days, min_periods=stay_days).sum().shift(1) >= stay_days
    high_stay = high_zone.rolling(stay_days, min_periods=stay_days).sum().shift(1) >= stay_days

    raw_buy = low_stay & out["j_turn_up"] & out["golden_cross"] & out["macd_green_shrinking"]
    raw_sell = high_stay & out["j_turn_down"] & out["dead_cross"]
    out["buy_signal"] = raw_buy & (~raw_buy.shift(1).fillna(False))
    out["sell_signal"] = raw_sell & (~raw_sell.shift(1).fillna(False))
    out["strong_buy_signal"] = out["buy_signal"] & (out["RSI6"] < max(rsi_low - 5, 10)) & (out["J"] < max(kdj_low - 5, 5))
    out["strong_sell_signal"] = out["sell_signal"] & (out["RSI6"] > min(rsi_high + 5, 90)) & (out["J"] > min(kdj_high + 5, 95))
    out["overbought"] = out["RSI6"] > 80

    return out


def get_action_suggestion(last: pd.Series) -> str:
    if bool(last.get("sell_signal", False)) or bool(last.get("strong_sell_signal", False)):
        return "减仓"
    if bool(last.get("buy_signal", False)) or bool(last.get("strong_buy_signal", False)):
        return "抄底"
    rsi = float(last.get("RSI6", 50))
    macd = float(last.get("MACD", 0))
    macd_signal = float(last.get("MACD_SIGNAL", 0))
    if rsi > 72:
        return "持股"
    if rsi < 30 and macd > macd_signal:
        return "关注反弹"
    if abs(float(last.get("K", 50)) - 50) <= 8 and abs(rsi - 50) <= 8:
        return "观望"
    return "观望"


def build_figure(df: pd.DataFrame, symbol: str, suggestion: str) -> go.Figure:
    x_vals = df["date"]
    j_plot = df["J"]
    price_min = float(df["low"].min())
    price_max = float(df["high"].max())
    price_pad = max((price_max - price_min) * 0.05, 1e-6)
    kdj_rsi_series = pd.concat([df["K"], df["D"], df["J"], df["RSI6"]], axis=0).dropna()
    kdj_rsi_min = float(kdj_rsi_series.min()) if not kdj_rsi_series.empty else -10.0
    kdj_rsi_max = float(kdj_rsi_series.max()) if not kdj_rsi_series.empty else 110.0
    kdj_rsi_pad = max((kdj_rsi_max - kdj_rsi_min) * 0.1, 8.0)
    kdj_rsi_low = float(min(-10.0, kdj_rsi_min - kdj_rsi_pad))
    kdj_rsi_high = float(max(110.0, kdj_rsi_max + kdj_rsi_pad))

    macd_all = pd.concat([df["MACD"], df["MACD_SIGNAL"], df["MACD_HIST"]], axis=0).dropna()
    macd_min = float(macd_all.min()) if not macd_all.empty else -1.0
    macd_max = float(macd_all.max()) if not macd_all.empty else 1.0
    macd_pad = max((macd_max - macd_min) * 0.2, 0.02)

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.045,
        row_heights=[0.62, 0.22, 0.16],
        subplot_titles=[f"{symbol} K线与买卖信号（建议：{suggestion}）", "KDJ + RSI", "MACD"],
    )

    fig.add_trace(
        go.Candlestick(
            x=x_vals,
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="K线",
            increasing_line_color="#26A69A",
            decreasing_line_color="#EF5350",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(go.Scatter(x=x_vals, y=df["MA5"], name="MA5", line=dict(color="#FFD54F", width=1.4)), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df["MA10"], name="MA10", line=dict(color="#4FC3F7", width=1.4)), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_vals, y=df["MA20"], name="MA20", line=dict(color="#CE93D8", width=1.4)), row=1, col=1)

    strong_buys = df[df["strong_buy_signal"]] if "strong_buy_signal" in df.columns else df.iloc[0:0]
    strong_sells = df[df["strong_sell_signal"]] if "strong_sell_signal" in df.columns else df.iloc[0:0]
    buys = df[df["buy_signal"] & ~df["strong_buy_signal"]] if "strong_buy_signal" in df.columns else df[df["buy_signal"]]
    sells = df[df["sell_signal"] & ~df["strong_sell_signal"]] if "strong_sell_signal" in df.columns else df[df["sell_signal"]]
    ob = df[df["overbought"]]
    fig.add_trace(
        go.Scatter(
            x=buys["date"],
            y=buys["low"] * 0.996,
            mode="markers",
            marker=dict(size=10, color="#2E7D32", symbol="triangle-up", line=dict(width=1, color="#1B5E20")),
            name="买入",
            hovertemplate="买入<br>%{x}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=sells["date"],
            y=sells["high"] * 1.004,
            mode="markers",
            marker=dict(size=10, color="#C62828", symbol="triangle-down", line=dict(width=1, color="#7F0000")),
            name="卖出",
            hovertemplate="卖出<br>%{x}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=strong_buys["date"],
            y=strong_buys["low"] * 0.992,
            mode="markers+text",
            text=["强买"] * len(strong_buys),
            textposition="bottom center",
            textfont=dict(size=10, color="#0B3D0B"),
            marker=dict(size=14, color="#00C853", symbol="star", line=dict(width=1.4, color="#0B3D0B")),
            name="强力买入",
            hovertemplate="强力买入<br>%{x}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=strong_sells["date"],
            y=strong_sells["high"] * 1.008,
            mode="markers+text",
            text=["强卖"] * len(strong_sells),
            textposition="top center",
            textfont=dict(size=10, color="#5A0000"),
            marker=dict(size=14, color="#FF1744", symbol="diamond", line=dict(width=1.4, color="#5A0000")),
            name="强力卖出",
            hovertemplate="强力卖出<br>%{x}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    if not strong_buys.empty:
        rb = strong_buys.iloc[-1]
        fig.add_annotation(
            x=rb["date"],
            y=float(rb["low"]) * 0.992,
            text="强力买入",
            showarrow=True,
            arrowhead=2,
            arrowcolor="#2E7D32",
            ax=0,
            ay=40,
            font=dict(size=11, color="#0B3D0B"),
            bgcolor="rgba(255,255,255,0.85)",
            row=1,
            col=1,
        )
    if not strong_sells.empty:
        rs = strong_sells.iloc[-1]
        fig.add_annotation(
            x=rs["date"],
            y=float(rs["high"]) * 1.008,
            text="强力卖出",
            showarrow=True,
            arrowhead=2,
            arrowcolor="#C62828",
            ax=0,
            ay=-40,
            font=dict(size=11, color="#5A0000"),
            bgcolor="rgba(255,255,255,0.85)",
            row=1,
            col=1,
        )

    if not ob.empty:
        fig.add_trace(
            go.Scatter(
                x=ob["date"],
                y=ob["high"] * 1.01,
                mode="markers",
                marker=dict(size=6, color="#FF9800", symbol="circle"),
                hovertemplate="超买预警<br>%{x}<extra></extra>",
                name="超买预警",
            ),
            row=1,
            col=1,
        )

    fig.add_trace(
        go.Scatter(x=x_vals, y=df["K"], name="K", connectgaps=False, line=dict(color="#29B6F6", width=1.6)),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=x_vals, y=df["D"], name="D", connectgaps=False, line=dict(color="#AB47BC", width=1.6)),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x_vals,
            y=j_plot,
            name="J",
            connectgaps=False,
            line=dict(color="#FFA726", width=1.4),
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x_vals,
            y=df["RSI6"],
            name="RSI6",
            connectgaps=False,
            line=dict(color="#EF5350", width=1.6, dash="dot"),
        ),
        row=2,
        col=1,
    )
    fig.add_hrect(y0=0, y1=20, fillcolor="rgba(76, 175, 80, 0.08)", line_width=0, row=2, col=1)
    fig.add_hrect(y0=20, y1=80, fillcolor="rgba(158, 158, 158, 0.05)", line_width=0, row=2, col=1)
    fig.add_hrect(y0=80, y1=100, fillcolor="rgba(244, 67, 54, 0.08)", line_width=0, row=2, col=1)
    fig.add_hline(y=20, line_dash="solid", line_color="#90A4AE", line_width=1.8, row=2, col=1)
    fig.add_hline(y=50, line_dash="solid", line_color="#B0BEC5", line_width=1.8, row=2, col=1)
    fig.add_hline(y=80, line_dash="solid", line_color="#90A4AE", line_width=1.8, row=2, col=1)

    fig.add_trace(
        go.Bar(
            x=x_vals,
            y=df["MACD_HIST"],
            name="MACD_HIST",
            marker_color=df["MACD_HIST"].apply(lambda v: "#26A69A" if v >= 0 else "#EF5350"),
            opacity=0.5,
        ),
        row=3,
        col=1,
    )
    fig.add_trace(go.Scatter(x=x_vals, y=df["MACD"], name="MACD", line=dict(color="#42A5F5", width=1.4)), row=3, col=1)
    fig.add_trace(
        go.Scatter(x=x_vals, y=df["MACD_SIGNAL"], name="MACD_SIGNAL", line=dict(color="#FFCA28", width=1.4)),
        row=3,
        col=1,
    )
    fig.add_hline(y=0, line_dash="dot", line_color="#78909C", row=3, col=1)

    # 只保留统一的共享X轴，禁用rangeslider，避免在多子图中叠层导致可读性崩坏
    fig.update_xaxes(
        type="date",
        showgrid=False,
        rangeslider_visible=False,
        fixedrange=False,
        tickangle=0,
        nticks=12,
        automargin=True,
    )
    fig.update_xaxes(showticklabels=False, row=1, col=1)
    fig.update_xaxes(showticklabels=False, row=2, col=1)
    fig.update_xaxes(showticklabels=True, row=3, col=1)
    fig.update_yaxes(
        row=1,
        col=1,
        fixedrange=False,
        autorange=True,
        rangemode="normal",
        range=[price_min - price_pad, price_max + price_pad],
    )
    fig.update_yaxes(
        row=2,
        col=1,
        fixedrange=True,
        range=[kdj_rsi_low, kdj_rsi_high],
        tickmode="linear",
        dtick=20,
        automargin=True,
    )
    fig.update_yaxes(
        row=3,
        col=1,
        fixedrange=True,
        range=[macd_min - macd_pad, macd_max + macd_pad],
        automargin=True,
    )

    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font=dict(family="Microsoft YaHei, Arial", size=13, color="#263238"),
        dragmode="pan",
        hovermode="x unified",
        height=1180,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=11)),
        margin=dict(l=20, r=20, t=80, b=20),
    )
    fig.add_annotation(
        x=0.99,
        y=0.99,
        xref="paper",
        yref="paper",
        text=f"信号统计(1年): 买入 {int(df['buy_signal'].sum())} 次 | 卖出 {int(df['sell_signal'].sum())} 次",
        showarrow=False,
        xanchor="right",
        yanchor="top",
        font=dict(size=12, color="#263238"),
        bgcolor="rgba(255,255,255,0.92)",
        bordercolor="#B0BEC5",
        borderwidth=1,
    )
    return fig


def latest_summary_row(symbol: str, df: pd.DataFrame) -> dict:
    last = df.iloc[-1]
    return {
        "代码": symbol,
        "日期": last["date"].strftime("%Y-%m-%d"),
        "收盘": round(float(last["close"]), 3),
        "K": round(float(last["K"]), 3) if pd.notna(last["K"]) else None,
        "D": round(float(last["D"]), 3) if pd.notna(last["D"]) else None,
        "J": round(float(last["J"]), 3) if pd.notna(last["J"]) else None,
        "RSI6": round(float(last["RSI6"]), 3) if pd.notna(last["RSI6"]) else None,
        "MACD": round(float(last["MACD"]), 3) if pd.notna(last["MACD"]) else None,
        "MACD_SIGNAL": round(float(last["MACD_SIGNAL"]), 3) if pd.notna(last["MACD_SIGNAL"]) else None,
        "MACD_HIST": round(float(last["MACD_HIST"]), 3) if pd.notna(last["MACD_HIST"]) else None,
        "建议": get_action_suggestion(last),
        "买入信号": "是" if bool(last["buy_signal"]) else "否",
        "卖出信号": "是" if bool(last["sell_signal"]) else "否",
        "风险预警": "超买" if bool(last["overbought"]) else "-",
    }


def calculate_trade_stats(df: pd.DataFrame) -> dict:
    work = df.copy()
    if "date" not in work.columns or "close" not in work.columns:
        return {}
    work["date"] = pd.to_datetime(work["date"])
    work = work.sort_values("date").reset_index(drop=True)
    work = work.dropna(subset=["close", "buy_signal", "sell_signal", "date"]).reset_index(drop=True)
    if work.empty:
        return {}

    close = work["close"].astype(float)
    if close.iloc[0] <= 1e-6:
        return {}
    dates = work["date"]
    buy_mask = work["buy_signal"].fillna(False).astype(bool)
    sell_mask = work["sell_signal"].fillna(False).astype(bool)
    if buy_mask.any():
        first_buy_idx = int(buy_mask[buy_mask].index[0])
        sell_mask.iloc[:first_buy_idx] = False

    equity = 1.0
    shares = 0.0
    in_pos = False
    entry_idx = None
    entry_close = None

    equity_curve = []
    trades = []

    for i in range(len(work)):
        # 进场：只要当天出现 buy_signal，就认为按当日收盘价全仓买入
        if (not in_pos) and bool(buy_mask.iloc[i]):
            entry_close = float(close.iloc[i])
            if entry_close > 1e-6:
                shares = equity / entry_close
                in_pos = True
                entry_idx = i

        # 标记到收盘
        if in_pos:
            equity_today = shares * float(close.iloc[i])

            # 出场：只要当天出现 sell_signal，就按当日收盘价全仓卖出
            if bool(sell_mask.iloc[i]):
                exit_close = float(close.iloc[i])
                if exit_close > 1e-6 and entry_close is not None and entry_idx is not None:
                    ret = exit_close / entry_close - 1.0
                    hold_days = int(i - entry_idx)  # 交易日持仓天数（按行号差）
                    trades.append(
                        {
                            "买入日期": dates.iloc[entry_idx].strftime("%Y-%m-%d"),
                            "买入价格": round(entry_close, 4),
                            "卖出日期": dates.iloc[i].strftime("%Y-%m-%d"),
                            "卖出价格": round(exit_close, 4),
                            "单笔涨跌幅(%)": round(ret * 100.0, 4),
                            "持仓天数": hold_days,
                        }
                    )
                equity = equity_today
                shares = 0.0
                in_pos = False
                entry_idx = None
                entry_close = None

            equity_curve.append(float(equity_today))
        else:
            equity_curve.append(float(equity))

    # 如果回测结束仍只有买入没卖出：用最后一个交易日收盘价强制平仓
    if in_pos and entry_idx is not None and entry_close is not None:
        last_i = len(work) - 1
        exit_close = float(close.iloc[last_i])
        if exit_close > 0:
            ret = exit_close / entry_close - 1.0
            hold_days = int(last_i - entry_idx)
            trades.append(
                {
                    "买入日期": dates.iloc[entry_idx].strftime("%Y-%m-%d"),
                    "买入价格": round(entry_close, 4),
                    "卖出日期": dates.iloc[last_i].strftime("%Y-%m-%d"),
                    "卖出价格": round(exit_close, 4),
                    "单笔涨跌幅(%)": round(ret * 100.0, 4),
                    "持仓天数": hold_days,
                }
            )
            equity = shares * exit_close
            equity_curve[-1] = float(equity)

    equity_s = pd.Series(equity_curve, index=work.index, dtype=float)
    strategy_total_return_pct = (equity_s.iloc[-1] - 1.0) * 100.0

    base_eq = close / float(close.iloc[0])
    base_total_return_pct = (base_eq.iloc[-1] - 1.0) * 100.0

    suspicious_return = bool(
        strategy_total_return_pct > 100 and base_total_return_pct < 100 and close.max() / max(close.min(), 1e-6) < 2
    )

    wins = sum(1 for t in trades if t["单笔涨跌幅(%)"] > 0)
    total_trades = len(trades)
    win_rate = (wins / total_trades) if total_trades else 0.0

    running_max = equity_s.cummax()
    dd = equity_s / running_max - 1.0
    max_drawdown_pct = abs(float(dd.min()) * 100.0)

    trades_df = pd.DataFrame(trades)
    cutoff = work["date"].max() - pd.Timedelta(days=365)
    trades_df_year = trades_df[pd.to_datetime(trades_df["买入日期"]) >= cutoff] if not trades_df.empty else trades_df

    perf_fig = go.Figure()
    perf_fig.add_trace(
        go.Scatter(
            x=work["date"],
            y=equity_s,
            mode="lines",
            name="策略累计净值",
            line=dict(color="#00E676", width=2),
        )
    )
    perf_fig.add_trace(
        go.Scatter(
            x=work["date"],
            y=base_eq,
            mode="lines",
            name="个股基准净值",
            line=dict(color="#42A5F5", width=2),
        )
    )
    perf_fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font=dict(family="Microsoft YaHei, Arial", size=12, color="#263238"),
        height=360,
        margin=dict(l=20, r=20, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hovermode="x unified",
    )
    perf_fig.update_yaxes(title_text="累计净值")

    return {
        "策略总收益率(%)": float(strategy_total_return_pct),
        "基准收益率(%)": float(base_total_return_pct),
        "胜率": float(win_rate),
        "最大回撤(%)": float(max_drawdown_pct),
        "收益有效性检查": "警告：收益率异常，请检查价格分母" if suspicious_return else "通过",
        "策略净值曲线图": perf_fig,
        "交易清单全量": trades_df,
        "交易清单过去一年": trades_df_year,
    }


def main() -> None:
    st.set_page_config(page_title="港股量化看板", layout="wide")
    st.title(f"港股量化可视化看板 {APP_VERSION}")
    if "sensitivity_level" not in st.session_state:
        apply_preset_to_state("5")
    if "updating_from_preset" not in st.session_state:
        st.session_state["updating_from_preset"] = False

    st.sidebar.markdown("## 策略预设")
    st.sidebar.select_slider(
        "策略灵敏度 (1-10)",
        options=SENSITIVITY_OPTIONS,
        key="sensitivity_level",
        on_change=on_sensitivity_change,
    )
    tip_map = {
        "1": "老僧入定模式：信号极少，追求绝对安全",
        "10": "短线刺客模式：信号极多，小心虚假波动",
        "手动微调": "手动微调模式：你正在覆盖预设参数",
    }
    st.sidebar.caption(tip_map.get(st.session_state["sensitivity_level"], "均衡交易模式：稳健与效率兼顾"))

    with st.sidebar.expander("专家参数（默认隐藏）", expanded=False):
        st.slider("KDJ 周期 K", 5, 30, key="kdj_k", on_change=on_expert_change)
        st.slider("KDJ 周期 D", 2, 10, key="kdj_d", on_change=on_expert_change)
        st.slider("KDJ 平滑", 2, 10, key="kdj_smooth", on_change=on_expert_change)
        st.slider("RSI 周期", 4, 30, key="rsi_length", on_change=on_expert_change)
        st.slider("MACD 快线", 3, 30, key="macd_fast", on_change=on_expert_change)
        st.slider("MACD 慢线", 6, 80, key="macd_slow", on_change=on_expert_change)
        st.slider("MACD 信号线", 3, 30, key="macd_signal", on_change=on_expert_change)
        st.slider("KDJ 低阈值", 10, 45, key="kdj_low", on_change=on_expert_change)
        st.slider("KDJ 高阈值", 55, 90, key="kdj_high", on_change=on_expert_change)
        st.slider("RSI 低阈值", 10, 45, key="rsi_low", on_change=on_expert_change)
        st.slider("RSI 高阈值", 55, 90, key="rsi_high", on_change=on_expert_change)

    if st.session_state["macd_slow"] <= st.session_state["macd_fast"]:
        st.session_state["macd_slow"] = st.session_state["macd_fast"] + 1
    if st.session_state["kdj_low"] >= st.session_state["kdj_high"]:
        st.session_state["kdj_high"] = min(90, st.session_state["kdj_low"] + 1)
    if st.session_state["rsi_low"] >= st.session_state["rsi_high"]:
        st.session_state["rsi_high"] = min(90, st.session_state["rsi_low"] + 1)

    if "backtest_start_date" not in st.session_state:
        st.session_state["backtest_start_date"] = (datetime.now() - timedelta(days=365)).date()
    if "backtest_end_date" not in st.session_state:
        st.session_state["backtest_end_date"] = datetime.now().date()
    st.sidebar.date_input("回测起始日期", key="backtest_start_date")
    st.sidebar.date_input("回测结束日期", key="backtest_end_date")
    if st.session_state["backtest_start_date"] > st.session_state["backtest_end_date"]:
        st.sidebar.warning("起始日期不能晚于结束日期，已自动对齐。")
        st.session_state["backtest_end_date"] = st.session_state["backtest_start_date"]

    with st.sidebar.expander("策略逻辑说明（点击展开）", expanded=False):
        st.markdown(
            f"- 买入准则：超卖区连续停留超过2天后出现拐点，且KDJ金叉 + MACD负柱缩短\n"
            f"- 卖出准则：超买区连续停留超过2天后出现拐点，且KDJ死叉\n"
            f"- 中性区：指标在50附近波动时，一律显示`观望`\n"
            f"- 当前参数：KDJ({st.session_state['kdj_k']},{st.session_state['kdj_d']},{st.session_state['kdj_smooth']}) / "
            f"RSI({st.session_state['rsi_length']}) / "
            f"MACD({st.session_state['macd_fast']},{st.session_state['macd_slow']},{st.session_state['macd_signal']}) / "
            f"阈值 KDJ({st.session_state['kdj_low']}/{st.session_state['kdj_high']}) RSI({st.session_state['rsi_low']}/{st.session_state['rsi_high']})"
        )

    with st.sidebar.expander("回测逻辑说明（点击展开）", expanded=False):
        st.markdown(
            "- 回测输入：使用当前参数下生成的 `buy_signal` 与 `sell_signal`\n"
            "- 成交规则：信号当日收盘价成交（买入/卖出）\n"
            "- 首单规则：第一笔必须是买入，第一买点前的卖点全部忽略\n"
            "- 闭环规则：买入后等待卖出；若到结束日仍持仓，则以结束日收盘价强平\n"
            "- 净值规则：策略净值与基准净值均从回测起始日的 `1.0` 起步，对齐比较\n"
            "- 风险检查：若出现异常高收益但标的未明显翻倍，会触发收益有效性警告"
        )

    default_text = st.session_state.get("input_codes_text", ",".join(DEFAULT_CODES))
    with st.sidebar.form("stock_control_form"):
        input_text = st.text_input("输入港股代码（逗号分隔）", value=default_text)
        submitted = st.form_submit_button("更新数据并计算", use_container_width=True, type="primary")

    if submitted:
        st.session_state["run_analysis"] = True
        st.session_state["input_codes_text"] = input_text

    if not st.session_state.get("run_analysis", False):
        st.info("请在左侧侧边栏输入代码并点击“更新数据并计算”。")
        return

    text = st.session_state.get("input_codes_text", ",".join(DEFAULT_CODES))
    codes = [normalize_code(x) for x in text.split(",")]
    codes = [c for c in codes if c] or DEFAULT_CODES
    selected = st.selectbox("主图展示代码", options=codes, index=0)

    data_map = {}
    table_rows = []

    for code in codes:
        raw = fetch_hk_data(
            code,
            backtest_start_date=st.session_state["backtest_start_date"],
            backtest_end_date=st.session_state["backtest_end_date"],
        )
        if raw.empty:
            st.warning(f"{code} 无可用数据")
            continue
        calc = compute_indicators(
            raw,
            kdj_k=st.session_state["kdj_k"],
            kdj_d=st.session_state["kdj_d"],
            kdj_smooth=st.session_state["kdj_smooth"],
            rsi_length=st.session_state["rsi_length"],
            macd_fast=st.session_state["macd_fast"],
            macd_slow=st.session_state["macd_slow"],
            macd_signal=st.session_state["macd_signal"],
        )
        calc = calculate_signals(
            calc,
            kdj_low=st.session_state["kdj_low"],
            kdj_high=st.session_state["kdj_high"],
            rsi_low=st.session_state["rsi_low"],
            rsi_high=st.session_state["rsi_high"],
            stay_days=2,
        )
        if calc.empty:
            st.warning(f"{code} 指标计算后无有效数据")
            continue
        data_map[code] = calc
        table_rows.append(latest_summary_row(code, calc))

    if selected in data_map:
        selected_df = data_map[selected]
        recent = selected_df.tail(5)
        if recent.get("strong_sell_signal", pd.Series(False)).any():
            suggestion = "强力减仓"
        elif recent.get("strong_buy_signal", pd.Series(False)).any():
            suggestion = "强力关注买点"
        elif recent.get("sell_signal", pd.Series(False)).any():
            suggestion = "减仓"
        elif recent.get("buy_signal", pd.Series(False)).any():
            suggestion = "关注买点"
        else:
            suggestion = get_action_suggestion(selected_df.iloc[-1])
        st.sidebar.markdown(
            f"<h2 style='color:#00E5FF;margin-top:8px;'>当前操作建议：{suggestion}</h2>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<h2 style='color:#00E5FF;margin-bottom:0.5rem;'>{selected} 当前操作建议：{suggestion}</h2>",
            unsafe_allow_html=True,
        )
        fig = build_figure(selected_df, selected, suggestion)
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={
                "scrollZoom": True,
                "displaylogo": False,
                "modeBarButtonsToRemove": [
                    "zoom2d",
                    "pan2d",
                    "select2d",
                    "lasso2d",
                    "zoomIn2d",
                    "zoomOut2d",
                    "autoScale2d",
                    "resetScale2d",
                ],
            },
        )
    else:
        st.error("主图代码暂无数据，无法展示。")

    st.subheader("自选股指标汇总")
    if table_rows:
        table_df = pd.DataFrame(table_rows)
        st.dataframe(table_df, use_container_width=True, hide_index=True)
    else:
        st.info("暂无可展示的指标数据。")

    st.subheader("策略绩效看板（信号纯度回测）")
    if selected in data_map:
        perf = calculate_trade_stats(data_map[selected])
        if perf:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("策略总收益率", f"{perf['策略总收益率(%)']:.2f}%")
            c2.metric("基准收益率", f"{perf['基准收益率(%)']:.2f}%")
            c3.metric("胜率", f"{perf['胜率']*100:.2f}%")
            c4.metric("最大回撤", f"{perf['最大回撤(%)']:.2f}%")
            if perf.get("收益有效性检查", "通过") != "通过":
                st.warning(perf["收益有效性检查"])

            st.plotly_chart(perf["策略净值曲线图"], use_container_width=True)

            with st.expander("历史交易清单（过去一年）", expanded=False):
                trades_year = perf["交易清单过去一年"]
                if trades_year is None or trades_year.empty:
                    st.info("过去一年无闭环交易。")
                else:
                    st.dataframe(
                        trades_year[
                            ["买入日期", "买入价格", "卖出日期", "卖出价格", "单笔涨跌幅(%)", "持仓天数"]
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
        else:
            st.info("暂无可统计数据。")
    else:
        st.info("暂无可统计数据。")


if __name__ == "__main__":
    main()
