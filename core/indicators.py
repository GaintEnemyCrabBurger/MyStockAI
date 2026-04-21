"""
core/indicators.py — 技术指标计算层

职责
----
计算主流技术分析指标，在原始行情 DataFrame 的基础上追加指标列并返回副本。
本模块完全用 pandas 原生接口实现，不依赖 pandas-ta / TA-Lib 等二进制库，
方便在任意 Python 版本的部署环境中无痛安装。

计算的指标
----------
MA（移动平均线）
    MA5 / MA10 / MA20：5、10、20 日简单移动平均，用于判断中短期趋势。

KDJ（随机指标）
    K / D / J：衡量价格在一段时间内的相对强弱位置。
    - RSV = (close - lowest_low_k) / (highest_high_k - lowest_low_k) * 100
    - K   = smooth_k 期简单移动平均后的 RSV
    - D   = d 期简单移动平均后的 K
    - J   = 3K − 2D（放大超买/超卖信号）

RSI（相对强弱指数）
    RSI6：Wilder 平滑法，范围 0-100，高于 70 超买，低于 30 超卖。
    - 上涨均值 / 下跌均值 均用 alpha = 1/length 的 EMA 递推

MACD（指数平滑异同移动平均线）
    MACD / MACD_SIGNAL / MACD_HIST：
    - EMA_fast / EMA_slow 均用 span = fast / slow 的 pandas EMA
    - MACD     = EMA_fast − EMA_slow
    - SIGNAL   = span = signal 的 EMA(MACD)
    - HIST     = MACD − SIGNAL（柱状图）

参数说明
--------
所有周期参数均由灵敏度档位在 config.get_dynamic_params() 中动态生成，
支持用户在侧边栏"专家参数"中手动覆盖。

容错处理
--------
当数据点数不足以支撑某个窗口时，pandas 原生行为会在前若干行返回 NaN，
后续流水线（信号/绘图）对 NaN 均已做裁剪处理。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ============================================================
# 基础指标实现
# ============================================================

def _sma(series: pd.Series, length: int) -> pd.Series:
    """简单移动平均。"""
    return series.rolling(window=length, min_periods=length).mean()


def _rsi(close: pd.Series, length: int) -> pd.Series:
    """
    Wilder RSI 实现，和 pandas_ta.rsi 默认行为一致。

    步骤
    ----
    1. 计算相邻收盘价差 delta
    2. 正向分量 gain = max(delta, 0)
       反向分量 loss = max(-delta, 0)
    3. 均值用 Wilder EMA（alpha = 1 / length）递推平滑
    4. RS = avg_gain / avg_loss
    5. RSI = 100 - 100 / (1 + RS)
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # 当 avg_loss 连续为 0 时 rs 为 inf，RSI 理论上趋于 100
    rsi = rsi.replace([np.inf, -np.inf], 100.0)
    return rsi


def _macd(
    close: pd.Series, fast: int, slow: int, signal: int,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD / SIGNAL / HIST 三条曲线。

    使用 pandas EMA（span 约定，与绝大多数交易软件一致）。
    """
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _stoch_kd(
    high: pd.Series, low: pd.Series, close: pd.Series,
    k: int, d: int, smooth_k: int,
) -> tuple[pd.Series, pd.Series]:
    """
    随机指标 K / D（KDJ 的 K、D 分量）。

    参数语义与 pandas_ta.stoch 保持一致：
    - k        : 计算 RSV 的回望窗口
    - smooth_k : 对 RSV 平滑得到慢 K 的窗口
    - d        : 对慢 K 再平滑得到 D 的窗口
    平滑均采用简单移动平均。
    """
    lowest_low = low.rolling(window=k, min_periods=k).min()
    highest_high = high.rolling(window=k, min_periods=k).max()

    denom = (highest_high - lowest_low).replace(0.0, np.nan)
    rsv = 100.0 * (close - lowest_low) / denom

    slow_k = rsv.rolling(window=smooth_k, min_periods=smooth_k).mean()
    slow_d = slow_k.rolling(window=d, min_periods=d).mean()
    return slow_k, slow_d


# ============================================================
# 对外接口
# ============================================================

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
    kdj_d      : D 线平滑周期
    kdj_smooth : K 线平滑周期
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
    out["MA5"] = _sma(out["close"], 5)
    out["MA10"] = _sma(out["close"], 10)
    out["MA20"] = _sma(out["close"], 20)

    # --- KDJ 随机指标 ---
    k_line, d_line = _stoch_kd(
        high=out["high"], low=out["low"], close=out["close"],
        k=kdj_k, d=kdj_d, smooth_k=kdj_smooth,
    )
    out["K"] = k_line
    out["D"] = d_line
    out["J"] = 3 * out["K"] - 2 * out["D"]

    # --- RSI ---
    out["RSI6"] = _rsi(out["close"], rsi_length)

    # --- MACD ---
    macd_line, signal_line, hist = _macd(
        out["close"], fast=macd_fast, slow=macd_slow, signal=macd_signal,
    )
    out["MACD"] = macd_line
    out["MACD_SIGNAL"] = signal_line
    out["MACD_HIST"] = hist

    return out
