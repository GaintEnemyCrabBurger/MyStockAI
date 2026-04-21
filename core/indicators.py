"""
core/indicators.py — 技术指标计算层

职责
----
计算主流技术分析指标，在原始行情 DataFrame 的基础上追加指标列并返回副本。

计算的指标
----------
MA（移动平均线）
    MA5 / MA10 / MA20：5、10、20 日简单移动平均，用于判断中短期趋势。

KDJ（随机指标）
    K / D / J：衡量价格在一段时间内的相对强弱位置。
    - K = RSV 的 D 期 EMA 平滑
    - D = K 的 D 期 EMA 平滑
    - J = 3K − 2D（放大超买/超卖信号）
    计算底层使用 pandas_ta.stoch()，列名格式为 STOCHk_{k}_{d}_{smooth} 等。

RSI（相对强弱指数）
    RSI6：衡量一段时间内涨幅占总波幅的比例，范围 0-100，高于 70 超买，低于 30 超卖。

MACD（指数平滑异同移动平均线）
    MACD / MACD_SIGNAL / MACD_HIST：
    - MACD = EMA(fast) − EMA(slow)
    - SIGNAL = EMA(MACD, signal)
    - HIST = MACD − SIGNAL（柱状图，俗称"红绿柱"）

参数说明
--------
所有周期参数均由灵敏度档位在 config.get_dynamic_params() 中动态生成，
支持用户在侧边栏"专家参数"中手动覆盖。

容错处理
--------
当 pandas_ta 因数据不足返回 None 时，对应指标列填充 NA，保证后续流水线不崩溃。
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta


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
    """
    在行情 DataFrame 上追加技术指标列，返回新副本（不修改原始数据）。

    参数
    ----
    df         : 标准化行情 DataFrame，至少含 open/high/low/close 列
    kdj_k      : KDJ 随机值 RSV 的计算周期
    kdj_d      : K 线平滑周期
    kdj_smooth : RSV 的二次平滑周期
    rsi_length : RSI 计算周期
    macd_fast  : MACD 快线 EMA 周期
    macd_slow  : MACD 慢线 EMA 周期
    macd_signal: MACD 信号线 EMA 周期

    返回
    ----
    含以下新增列的 DataFrame：
    MA5, MA10, MA20, K, D, J, RSI6, MACD, MACD_SIGNAL, MACD_HIST
    """
    out = df.copy()

    # --- 移动平均线 ---
    out["MA5"]  = ta.sma(out["close"], length=5)
    out["MA10"] = ta.sma(out["close"], length=10)
    out["MA20"] = ta.sma(out["close"], length=20)

    # --- KDJ 随机指标 ---
    kdj = ta.stoch(
        high=out["high"], low=out["low"], close=out["close"],
        k=kdj_k, d=kdj_d, smooth_k=kdj_smooth,
    )
    k_col = f"STOCHk_{kdj_k}_{kdj_d}_{kdj_smooth}"
    d_col = f"STOCHd_{kdj_k}_{kdj_d}_{kdj_smooth}"

    _na_series = lambda: pd.Series(float("nan"), index=out.index, dtype="float64")
    if kdj is None or not hasattr(kdj, "get"):
        out["K"] = _na_series()
        out["D"] = _na_series()
    else:
        out["K"] = kdj.get(k_col, _na_series())
        out["D"] = kdj.get(d_col, _na_series())
    out["J"] = 3 * out["K"] - 2 * out["D"]

    # --- RSI ---
    out["RSI6"] = ta.rsi(out["close"], length=rsi_length)

    # --- MACD ---
    macd = ta.macd(out["close"], fast=macd_fast, slow=macd_slow, signal=macd_signal)
    if macd is None or not hasattr(macd, "get"):
        out["MACD"]        = _na_series()
        out["MACD_SIGNAL"] = _na_series()
        out["MACD_HIST"]   = _na_series()
    else:
        out["MACD"]        = macd.get(f"MACD_{macd_fast}_{macd_slow}_{macd_signal}",  _na_series())
        out["MACD_SIGNAL"] = macd.get(f"MACDs_{macd_fast}_{macd_slow}_{macd_signal}", _na_series())
        out["MACD_HIST"]   = macd.get(f"MACDh_{macd_fast}_{macd_slow}_{macd_signal}", _na_series())

    return out
