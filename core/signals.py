"""
core/signals.py — 买卖信号生成与操作建议（加权投票制）

设计理念
--------
放弃传统的"整数票数"阈值（3 票/2 票/1 票只产生 3 种档位），改用连续的
"加权评分制"，让灵敏度 1-10 档之间呈现真正均匀的信号频率变化。

评分规则（事件驱动，避免状态过拟合）
------------------------------------
每个维度（KDJ / RSI / MACD）独立给出 0.0-1.0 的"强度分"，三项相加得到
总分（0-3.0）。总分 ≥ vote_threshold 时触发信号。

**KDJ 买分**（最高 1.0）：
    + 0.5  当日触发 金叉 AND J 向上拐（事件）
    + 0.3  同时满足：J 与 RSI 在超卖区连续停留 ≥ stay_days 天
    + 0.2  同时满足：J < (kdj_low − 10)，极端超卖

**RSI 买分**（最高 1.0）：
    + 0.4  RSI ≤ rsi_low − 5      （明确处于超卖区）
    + 0.3  RSI ≤ rsi_low − 10     （深度超卖）
    + 0.3  RSI 刚从非超卖进入超卖（事件，当日穿越 rsi_low）

**MACD 买分**（最高 1.0）：
    + 0.5  当日触发 MACD 线上穿信号线（事件：金叉）
    + 0.3  负柱正在收缩（动能减弱，状态）
    + 0.2  MACD_HIST 由负转正（零轴上穿事件）

卖分结构对称。

阈值含义（由灵敏度档位决定）
----------------------------
    2.8 → 近乎完美的三指标强共振（L1，极保守）
    2.0 → 两个指标强 + 一个指标弱（L4）
    1.6 → 两个指标中度 / 一强一中（L5，平衡）
    1.0 → 一个指标强 或 两个指标弱（L7）
    0.4 → 任何微弱触发（L10，极进攻）

强信号
------
阈值与满分（3.0）之间插 40% 作为强信号门槛：
    strong_threshold = vote_threshold + (3.0 - vote_threshold) * 0.4

保证 1-10 档全部可达且幅度有区分（旧版固定 +0.8 在保守档位永远达不到）。

边沿检测
--------
raw_buy/raw_sell 仅在"上升沿"标记，避免连续满足条件时重复报信号。
"""

from __future__ import annotations

import pandas as pd


# ---------------------------------------------------------------------------
# 加权分数常量（按维度拆分，便于调试）
# ---------------------------------------------------------------------------

# KDJ
_KDJ_BASE       = 0.5   # 金叉 + J 拐头（事件）
_KDJ_ZONE_BONUS = 0.3   # 区间停留确认
_KDJ_EXTREME    = 0.2   # J 极端超买/超卖

# RSI（事件 + 深度）
_RSI_MID        = 0.4   # rsi_low − 5 深度
_RSI_DEEP       = 0.3   # rsi_low − 10 极端深度
_RSI_CROSS      = 0.3   # 刚穿越 rsi_low 事件

# MACD（以事件为主）
_MACD_CROSS     = 0.5   # 金/死叉事件（MACD vs SIGNAL 当日交叉）
_MACD_SHRINK    = 0.3   # 柱状图动能减弱状态
_MACD_ZERO_CROSS = 0.2  # 柱状图穿 0 轴事件


def calculate_signals(
    df: pd.DataFrame,
    vote_threshold: float = 1.6,
    kdj_low: int = 20,
    kdj_high: int = 80,
    rsi_low: int = 30,
    rsi_high: int = 70,
    stay_days: int = 2,
) -> pd.DataFrame:
    """
    在含指标列的 DataFrame 上追加加权买卖信号列，返回副本。

    参数
    ----
    df              : 含 K / D / J / RSI6 / MACD 族 列的 DataFrame
    vote_threshold  : 浮点阈值，0.3-3.0（越低越敏感）
    kdj_low/high    : KDJ J 值触发阈
    rsi_low/high    : RSI 触发阈
    stay_days       : 超卖/超买区连续停留天数要求

    新增列
    ------
    buy_score / sell_score       : 加权总分（0-3.0）
    buy_signal / sell_signal     : 边沿触发的普通信号
    strong_buy_signal / strong_sell_signal : 强信号（分数更高）
    overbought                   : RSI > 80 超买预警
    """
    out = df.copy()

    # ==== 辅助列：交叉、拐头、柱状图变化 ====
    out["golden_cross"] = (out["K"] > out["D"]) & (out["K"].shift(1) <= out["D"].shift(1))
    out["dead_cross"]   = (out["K"] < out["D"]) & (out["K"].shift(1) >= out["D"].shift(1))
    out["j_turn_up"]    = out["J"] > out["J"].shift(1)
    out["j_turn_down"]  = out["J"] < out["J"].shift(1)

    prev_hist = out["MACD_HIST"].shift(1)
    macd_green_shrinking = (out["MACD_HIST"] < 0) & (prev_hist < 0) & (out["MACD_HIST"] > prev_hist)
    macd_red_shrinking   = (out["MACD_HIST"] > 0) & (prev_hist > 0) & (out["MACD_HIST"] < prev_hist)
    macd_hist_up_cross   = (out["MACD_HIST"] > 0) & (prev_hist <= 0)
    macd_hist_down_cross = (out["MACD_HIST"] < 0) & (prev_hist >= 0)

    # MACD 金/死叉事件（上穿 / 下穿）
    macd_prev          = out["MACD"].shift(1)
    macd_sig_prev      = out["MACD_SIGNAL"].shift(1)
    macd_golden_cross  = (out["MACD"] > out["MACD_SIGNAL"]) & (macd_prev <= macd_sig_prev)
    macd_dead_cross    = (out["MACD"] < out["MACD_SIGNAL"]) & (macd_prev >= macd_sig_prev)

    # RSI 穿越事件
    rsi_prev         = out["RSI6"].shift(1)
    rsi_enter_low    = (out["RSI6"] <= rsi_low)  & (rsi_prev > rsi_low)
    rsi_enter_high   = (out["RSI6"] >= rsi_high) & (rsi_prev < rsi_high)

    low_zone  = (out["J"] < kdj_low)  & (out["RSI6"] < rsi_low)
    high_zone = (out["J"] > kdj_high) & (out["RSI6"] > rsi_high)
    stay_days = max(1, int(stay_days))
    low_stay  = low_zone.rolling(stay_days,  min_periods=stay_days).sum().shift(1) >= stay_days
    high_stay = high_zone.rolling(stay_days, min_periods=stay_days).sum().shift(1) >= stay_days

    # ==== 买分（加权） ====
    kdj_buy_base = out["golden_cross"] & out["j_turn_up"]
    kdj_buy_score = (
        kdj_buy_base.astype(float) * _KDJ_BASE
        + (kdj_buy_base & low_stay).astype(float) * _KDJ_ZONE_BONUS
        + (kdj_buy_base & (out["J"] < max(kdj_low - 10, 0))).astype(float) * _KDJ_EXTREME
    )

    rsi_buy_score = (
        (out["RSI6"] <= max(rsi_low - 5, 10)).astype(float) * _RSI_MID
        + (out["RSI6"] <= max(rsi_low - 10, 5)).astype(float) * _RSI_DEEP
        + rsi_enter_low.astype(float) * _RSI_CROSS
    )

    macd_buy_score = (
        macd_golden_cross.astype(float) * _MACD_CROSS
        + macd_green_shrinking.astype(float) * _MACD_SHRINK
        + macd_hist_up_cross.astype(float) * _MACD_ZERO_CROSS
    )

    out["buy_score"] = kdj_buy_score + rsi_buy_score + macd_buy_score

    # ==== 卖分（加权，对称结构） ====
    kdj_sell_base = out["dead_cross"] & out["j_turn_down"]
    kdj_sell_score = (
        kdj_sell_base.astype(float) * _KDJ_BASE
        + (kdj_sell_base & high_stay).astype(float) * _KDJ_ZONE_BONUS
        + (kdj_sell_base & (out["J"] > min(kdj_high + 10, 100))).astype(float) * _KDJ_EXTREME
    )

    rsi_sell_score = (
        (out["RSI6"] >= min(rsi_high + 5, 90)).astype(float) * _RSI_MID
        + (out["RSI6"] >= min(rsi_high + 10, 95)).astype(float) * _RSI_DEEP
        + rsi_enter_high.astype(float) * _RSI_CROSS
    )

    macd_sell_score = (
        macd_dead_cross.astype(float) * _MACD_CROSS
        + macd_red_shrinking.astype(float) * _MACD_SHRINK
        + macd_hist_down_cross.astype(float) * _MACD_ZERO_CROSS
    )

    out["sell_score"] = kdj_sell_score + rsi_sell_score + macd_sell_score

    # ==== 阈值判定 + 边沿检测 ====
    threshold = float(vote_threshold)
    raw_buy  = out["buy_score"]  >= threshold
    raw_sell = out["sell_score"] >= threshold

    out["buy_signal"]  = raw_buy  & (~raw_buy.shift(1).fillna(False))
    out["sell_signal"] = raw_sell & (~raw_sell.shift(1).fillna(False))

    # ==== 强信号：分数明显高于阈值 ====
    # 强档 = 阈值 → 满分（3.0）之间的 40% 位置
    # 保证任何灵敏度档位下都可达，且幅度随档位自然缩放
    strong_threshold = threshold + (3.0 - threshold) * 0.4
    out["strong_buy_signal"]  = out["buy_signal"]  & (out["buy_score"]  >= strong_threshold)
    out["strong_sell_signal"] = out["sell_signal"] & (out["sell_score"] >= strong_threshold)

    out["overbought"] = out["RSI6"] > 80

    return out


def get_action_suggestion(last: pd.Series) -> str:
    """
    当近期没有触发买/卖信号时，根据最新一根 K 线的指标状态给出方向性建议。

    调用约定
    --------
    主 UI 层（app._build_suggestion）会先检查近 5 根 K 线的信号：
      strong_sell → 强力卖出
      strong_buy  → 强力买入
      sell_signal → 建议卖出
      buy_signal  → 建议买入
    只有都没触发时才 fallback 到本函数。

    返回
    ----
    str：持股 / 关注反弹 / 观望
    """
    rsi      = float(last.get("RSI6", 50))
    macd     = float(last.get("MACD", 0))
    macd_sig = float(last.get("MACD_SIGNAL", 0))
    k        = float(last.get("K", 50))

    if rsi > 72:
        return "持股"
    if rsi < 30 and macd > macd_sig:
        return "关注反弹"
    if abs(k - 50) <= 8 and abs(rsi - 50) <= 8:
        return "观望"
    return "观望"
