"""
core/backtest.py — 分批回测引擎（金字塔式做 T）

设计价值观：追求"模糊的正确"而非"精确的错误"
------------------------------------------------
本引擎模拟**散户真实做 T 场景**：有底仓、高位减仓、低位补仓，
不是"一把满仓 / 一把清仓"的激进择时。

核心规则
--------
**1. 仓位档位：{0, 1/3, 2/3, 3/3}**
每次信号只调整 **1/3 仓位单位**（`unit_shares`），单位大小 = 初始满仓总股数 / 3。
单位一经设定不再变化，让"一笔操作 = 动 1/3 仓位"的语义固定。

**2. 初始建仓 @ T+1 开盘**
回测起始日次日开盘，以全部初始资金（净值 1.0）买入满仓 3/3。
含买入成本（随市场档位扣除）。

**3. 信号触发规则（不引入新参数）**
    - 买入信号 & 档位 < 3 → 加仓 1 单位，档位 +1
    - 买入信号 & 档位 = 3 → 忽略（满仓不再追）
    - 卖出信号 & 档位 > 0 → 减仓 1 单位，档位 −1
    - 卖出信号 & 档位 = 0 → 忽略（空仓不能再卖）
    - 同日双信号 → 整根跳过（震荡噪音）
    - 买入时现金不足一单位 → 跳过（保留 all-cash 状态，不加杠杆）

**4. 终止平仓 @ 末日收盘**
回测结束日仍有持仓，按末日收盘价扣费清仓。

**5. 成本法：加权平均成本（WAC）**
    - WAC = total_cost / total_shares（总现金支出 / 总股数）
    - 加仓后 WAC 按买入量加权更新
    - 减仓时按当前 WAC 结算盈亏，WAC 保持不变（只做成本迁移）
    - 完全对齐 A 股 / 港股券商 App 显示的"持仓成本"概念

**6. 交易成本**
保持和上一版一致：A 股 0.05/0.15%，港股 0.15/0.15%，美股 0.05/0.05%。
每个加仓和减仓动作都单独扣费。

绩效指标（刻意精简为 8 个）
---------------------------
同时维护两条曲线：
    - 策略净值（底仓 + 做 T）
    - 基准净值（纯底仓 Buy & Hold，不扣费）

刻意不用"胜率、夏普、Calmar、盈亏比"这类指标——它们在金字塔做 T 场景下
要么与直觉打架（例如空仓错过大涨时胜率仍可能 100%），要么需要假设无风险利率/
波动率分布，精度幻觉大于信息量。

只保留 8 个能直接回答"做 T 值不值"的指标：
    1. 策略总收益率
    2. 底仓基准 (B&H)
    3. 做 T 超额收益 = 策略 − 底仓  ← 核心
    4. 最大回撤
    5. 年化收益率
    6. 平均仓位（时间加权档位，解释超额正/负）
    7. 做 T 频率（年均信号驱动动作数）
    8. 单笔期望值（每次卖出平均 PnL%）

交易清单格式
------------
逐动作记录（非逐对记录）：
    日期 / 动作 / 价格 / 份额 / 金额 / 持仓档位 / 持仓成本(WAC) / 本笔盈亏(%)
买入动作的"本笔盈亏"为 None；卖出动作按 WAC 结算。
"""

from __future__ import annotations

from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# 市场交易成本（单向，含佣金 + 印花税 + 滑点，保守估计）
# ---------------------------------------------------------------------------

_MARKET_COSTS: dict[str, tuple[float, float]] = {
    #       (buy_cost, sell_cost)
    "A":       (0.0005, 0.0015),
    "HK":      (0.0015, 0.0015),
    "US":      (0.0005, 0.0005),
    "UNKNOWN": (0.0010, 0.0015),
}

# 分批档位：1/3 粒度（4 档：0, 1/3, 2/3, 3/3）
_LEVELS = 3


def _resolve_costs(market: str) -> tuple[float, float]:
    """市场代号 → (买入成本, 卖出成本)，未知市场走保守兜底。"""
    return _MARKET_COSTS.get((market or "UNKNOWN").upper(), _MARKET_COSTS["UNKNOWN"])


# ---------------------------------------------------------------------------
# 回测主入口
# ---------------------------------------------------------------------------

def run_backtest(df: pd.DataFrame, market: str = "UNKNOWN") -> dict[str, Any]:
    """
    对含信号列的行情 DataFrame 运行"金字塔式分批做 T"回测。

    参数
    ----
    df     : 需包含 date / open / close / buy_signal / sell_signal 列
    market : "A" / "HK" / "US"，用于套用对应市场的固定交易成本

    返回
    ----
    绩效数据字典（见模块文档）；数据不足 2 根 K 线时返回 {}。
    """
    required_cols = {"date", "open", "close", "buy_signal", "sell_signal"}
    if not required_cols.issubset(df.columns):
        return {}

    work = (
        df.copy()
        .assign(date=lambda x: pd.to_datetime(x["date"]))
        .sort_values("date")
        .reset_index(drop=True)
        .dropna(subset=["open", "close", "buy_signal", "sell_signal", "date"])
        .reset_index(drop=True)
    )
    # 至少需要 2 根 K 线：row[0] 为起始日（持币待命），row[1] 及以后为交易区间
    if len(work) < 2:
        return {}

    opens = work["open"].astype(float)
    close = work["close"].astype(float)
    if opens.iloc[0] <= 1e-6 or close.iloc[0] <= 1e-6:
        return {}

    dates     = work["date"]
    buy_mask  = work["buy_signal"].fillna(False).astype(bool)
    sell_mask = work["sell_signal"].fillna(False).astype(bool)

    # 同日双信号整根跳过（震荡噪音）
    both = buy_mask & sell_mask
    buy_mask  = buy_mask  & ~both
    sell_mask = sell_mask & ~both

    # T+1 执行：T 日收盘生成信号，T+1 日开盘成交
    exec_buy  = buy_mask.shift(1).fillna(False).astype(bool)
    exec_sell = sell_mask.shift(1).fillna(False).astype(bool)

    buy_cost, sell_cost = _resolve_costs(market)

    # ---- 状态初始化 ----
    initial_equity = 1.0
    cash           = initial_equity
    shares         = 0.0
    unit_shares    = 0.0      # 1/3 仓位对应的股数（初始满仓后锁定）
    total_cost     = 0.0      # 持仓成本总额（WAC 分子）
    level          = 0        # 当前档位：0 / 1 / 2 / 3

    equity_curve: list[float] = []
    level_history: list[int]  = []
    trades: list[dict]        = []

    initial_bought = False

    for i in range(len(work)):
        o = float(opens.iloc[i])
        c = float(close.iloc[i])

        if not initial_bought:
            # row[0]：起始日当天，持币不动（等 T+1 开盘）
            # row[1]（或更后）：首次有效开盘价时完成初始满仓建仓
            if i >= 1 and o > 1e-6:
                shares         = initial_equity * (1.0 - buy_cost) / o
                unit_shares    = shares / _LEVELS
                total_cost     = initial_equity   # 含买入费
                cash           = 0.0
                level          = _LEVELS
                initial_bought = True
                trades.append({
                    "日期":       dates.iloc[i].strftime("%Y-%m-%d"),
                    "动作":       f"初始建仓（满仓 {level}/{_LEVELS}）",
                    "价格":       round(o, 4),
                    "份额":       round(shares, 6),
                    "金额":       round(initial_equity, 4),
                    "持仓档位":   f"{level}/{_LEVELS}",
                    "持仓成本":   round(total_cost / shares, 4),
                    "本笔盈亏(%)": None,
                })
                equity_curve.append(float(shares * c))
                level_history.append(level)
            else:
                equity_curve.append(float(cash))
                level_history.append(0)
            continue

        # ==== T+1 开盘成交 ====
        # 卖出优先级高于买入（在同 bar 上两者不可能同时为真，前面已去重，
        # 但保险起见仍用 elif）
        if bool(exec_sell.iloc[i]) and level > 0 and o > 1e-6:
            sold           = min(unit_shares, shares)
            current_wac    = total_cost / shares if shares > 1e-12 else 0.0
            cost_removed   = sold * current_wac
            proceeds       = sold * o * (1.0 - sell_cost)
            pnl_pct        = (proceeds / cost_removed - 1.0) * 100.0 if cost_removed > 1e-12 else 0.0

            shares     -= sold
            cash       += proceeds
            total_cost -= cost_removed
            level      -= 1
            # 防漂移：档位归零时强制清空残留计数误差
            if level == 0:
                shares     = 0.0
                total_cost = 0.0

            trades.append({
                "日期":       dates.iloc[i].strftime("%Y-%m-%d"),
                "动作":       f"减仓 1/3（{level + 1}/{_LEVELS} → {level}/{_LEVELS}）",
                "价格":       round(o, 4),
                "份额":       round(sold, 6),
                "金额":       round(proceeds, 4),
                "持仓档位":   f"{level}/{_LEVELS}",
                "持仓成本":   round(current_wac, 4),
                "本笔盈亏(%)": round(pnl_pct, 4),
            })

        elif bool(exec_buy.iloc[i]) and level < _LEVELS and o > 1e-6:
            # 买 unit_shares：需要 cash = unit * o / (1 - buy_cost)
            cash_needed = unit_shares * o / (1.0 - buy_cost)
            if cash + 1e-9 >= cash_needed:
                cash       -= cash_needed
                shares     += unit_shares
                total_cost += cash_needed
                level      += 1
                new_wac     = total_cost / shares

                trades.append({
                    "日期":       dates.iloc[i].strftime("%Y-%m-%d"),
                    "动作":       f"加仓 1/3（{level - 1}/{_LEVELS} → {level}/{_LEVELS}）",
                    "价格":       round(o, 4),
                    "份额":       round(unit_shares, 6),
                    "金额":       round(cash_needed, 4),
                    "持仓档位":   f"{level}/{_LEVELS}",
                    "持仓成本":   round(new_wac, 4),
                    "本笔盈亏(%)": None,
                })
            # else: 现金不足以买 1 单位，跳过（不加杠杆、不按分数买）

        # ==== 日终 mark-to-market ====
        equity_curve.append(float(shares * c + cash))
        level_history.append(level)

    # ---- 终止平仓：最后一日仍持仓，按末日收盘价扣费清仓 ----
    last_i = len(work) - 1
    if shares > 1e-12:
        exit_px      = float(close.iloc[last_i])
        if exit_px > 0:
            current_wac  = total_cost / shares if shares > 1e-12 else 0.0
            proceeds     = shares * exit_px * (1.0 - sell_cost)
            cost_removed = shares * current_wac
            pnl_pct      = (proceeds / cost_removed - 1.0) * 100.0 if cost_removed > 1e-12 else 0.0

            trades.append({
                "日期":       dates.iloc[last_i].strftime("%Y-%m-%d"),
                "动作":       f"终止平仓（{level}/{_LEVELS} → 0/{_LEVELS}）",
                "价格":       round(exit_px, 4),
                "份额":       round(shares, 6),
                "金额":       round(proceeds, 4),
                "持仓档位":   f"0/{_LEVELS}",
                "持仓成本":   round(current_wac, 4),
                "本笔盈亏(%)": round(pnl_pct, 4),
            })

            cash       += proceeds
            shares      = 0.0
            total_cost  = 0.0
            level       = 0
            equity_curve[-1] = float(cash)
            level_history[-1] = 0

    # ==== 绩效指标计算 ====
    equity_s = pd.Series(equity_curve, index=work.index, dtype=float)
    strategy_total_return_pct = (equity_s.iloc[-1] - 1.0) * 100.0

    base_eq = close / float(close.iloc[0])
    base_total_return_pct = (base_eq.iloc[-1] - 1.0) * 100.0

    # 核心新指标：做 T 超额收益
    do_t_excess_pct = strategy_total_return_pct - base_total_return_pct

    # 按"动作"分类的交易序列
    sell_trades = [t for t in trades if t["本笔盈亏(%)"] is not None]
    discretionary_count = sum(
        1 for t in trades if ("加仓" in t["动作"]) or ("减仓" in t["动作"])
    )

    expectancy_pct = (
        sum(t["本笔盈亏(%)"] for t in sell_trades) / len(sell_trades)
        if sell_trades else 0.0
    )

    # 最大回撤
    running_max      = equity_s.cummax()
    max_drawdown_pct = abs(float((equity_s / running_max - 1.0).min()) * 100.0)

    # 年化收益
    span_days = max((dates.iloc[-1] - dates.iloc[0]).days, 1)
    years     = span_days / 365.25
    if equity_s.iloc[-1] > 0 and years >= 1 / 12:
        annual_return_pct = ((equity_s.iloc[-1] ** (1.0 / years)) - 1.0) * 100.0
    else:
        annual_return_pct = float("nan")

    # 交易频率（年均信号驱动动作数，不含初始建仓与终止平仓）
    trades_per_year = (discretionary_count / years) if years > 0 else float("nan")

    # 平均仓位（时间加权的档位占比）
    avg_position_pct = (
        (sum(level_history) / len(level_history) / _LEVELS) * 100.0
        if level_history else 0.0
    )

    # ==== 收益有效性启发式检查 ====
    warnings: list[str] = []
    if discretionary_count < 3:
        warnings.append(f"做 T 操作过少（仅 {discretionary_count} 次），无统计意义")
    if sell_trades:
        pnl_abs = [abs(t["本笔盈亏(%)"]) for t in sell_trades]
        total_abs = sum(pnl_abs)
        if total_abs > 0 and len(pnl_abs) >= 2:
            top_share = max(pnl_abs) / total_abs
            if top_share > 0.6:
                warnings.append(
                    f"收益过度依赖单笔 T 操作（最大 1 笔贡献 {top_share * 100:.0f}%）"
                )
    if do_t_excess_pct > 30 and discretionary_count < 5:
        warnings.append("做 T 超额收益显著但操作样本过少，可能为偶发性")
    # 空仓陷阱：平均仓位过低会让"胜率再高"也跑输底仓
    if avg_position_pct < 40 and do_t_excess_pct < 0:
        warnings.append(
            f"平均仓位仅 {avg_position_pct:.0f}%，多数时间空仓错过底仓涨幅，"
            f"做 T 拖了后腿（超额 {do_t_excess_pct:+.1f}%）"
        )

    validity = "通过" if not warnings else "； ".join(warnings)

    # ==== 交易清单 DataFrame ====
    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        cutoff = work["date"].max() - pd.Timedelta(days=365)
        trades_df_year = trades_df[pd.to_datetime(trades_df["日期"]) >= cutoff]
    else:
        trades_df_year = trades_df

    return {
        # ---- 8 个核心指标（精简为"能直接回答做 T 值不值"的一组）----
        "策略总收益率(%)":  float(strategy_total_return_pct),
        "基准收益率(%)":    float(base_total_return_pct),
        "做T超额收益(%)":   float(do_t_excess_pct),
        "最大回撤(%)":      float(max_drawdown_pct),
        "年化收益率(%)":    float(annual_return_pct),
        "平均仓位(%)":      float(avg_position_pct),
        "交易频率(次/年)":  float(trades_per_year),
        "期望值(%)":        float(expectancy_pct),

        # ---- 成本档位（告知用户，caption 里展示）----
        "单向成本_买(%)":   round(buy_cost * 100.0, 4),
        "单向成本_卖(%)":   round(sell_cost * 100.0, 4),

        # ---- 诊断 ----
        "收益有效性检查":   validity,

        # ---- 序列 / 清单（图表与详情用）----
        "equity_curve":     equity_s,
        "base_curve":       base_eq,
        "dates":            dates,
        "level_history":    pd.Series(level_history, index=work.index, dtype=int),
        "交易清单全量":     trades_df,
        "交易清单过去一年": trades_df_year,
    }


