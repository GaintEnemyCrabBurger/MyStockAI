"""
core/backtest.py — 回测引擎

设计价值观：追求"模糊的正确"而非"精确的错误"
------------------------------------------------
本引擎刻意保持"做 T"层面的简单性，只修复会导致**系统性偏差**的根本问题，
不引入任何需要调参的风控逻辑（止损/止盈/时间止损都会带来过拟合自由度）。

规则（修订版）
--------------
1. **信号生成与成交分离（T+1 开盘价）**
   信号在 T 日收盘生成（指标需要当日收盘），于 **T+1 开盘价**成交。
   这避免了"拿收盘价又同一秒用收盘价成交"的隐性未来函数。

2. **交易成本：按市场固定档位，无参数可调**
   基于真实零售环境的保守估计（含佣金、印花税、滑点）：
     - A 股：买入 0.05% / 卖出 0.15%（卖方含 0.1% 印花税）
     - 港股：买卖双向 0.15%
     - 美股：买卖双向 0.05%（零佣金 + 滑点）
     - 未知：买 0.10% / 卖 0.15%
   这不是"策略参数"，是"物理常数"，不影响价值观。

3. **同日双信号整根跳过**
   若某根 K 线同时触发买入与卖出信号（震荡区常见），整根跳过，
   避免"当天买入又卖出、产生 0% 虚假交易"。

4. **严格交替持仓**
   空仓期只接受买入，持仓期只接受卖出，冗余同类信号过滤。

5. **强平**
   回测最后一日仍持仓时，按末日收盘价扣除卖出成本后平仓。

6. **净值基准**
   策略净值与基准净值均从 1.0 出发。基准为"个股 buy & hold"，
   回答"择时是否优于躺平"——注：基准不扣成本（理想参照）。

返回结构（向后兼容，仅增字段）
-------------------------------
原有字段：
    策略总收益率(%) / 基准收益率(%) / 胜率 / 最大回撤(%) / 收益有效性检查
    equity_curve / base_curve / dates / 交易清单全量 / 交易清单过去一年

新增字段（均为从已有数据直接导出的描述性指标，零新增参数）：
    年化收益率(%)   — 让不同回测周期可比
    夏普比率        — 风险调整后收益（日收益 mean/std × √252）
    Calmar比率      — 年化收益 / 最大回撤
    盈亏比          — 平均单笔盈利 / |平均单笔亏损|
    期望值(%)       — 每笔交易的平均净收益（与胜率互补）
    交易频率(次/年) — 年化交易次数，直观感受成本影响
    在市占比(%)     — 持仓天数 / 回测总天数
    单向成本_买(%)  / 单向成本_卖(%)  — 本次回测采用的成本档位
"""

from __future__ import annotations

from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# 市场交易成本（单向，含佣金 + 印花税 + 滑点，保守估计）
# 这是物理常数而非策略参数，不对外暴露给用户调整
# ---------------------------------------------------------------------------

_MARKET_COSTS: dict[str, tuple[float, float]] = {
    #       (buy_cost, sell_cost)
    "A":       (0.0005, 0.0015),   # A 股：卖方含 0.1% 印花税
    "HK":      (0.0015, 0.0015),   # 港股：双向 0.15%（含印花税 + 过户 + 监管）
    "US":      (0.0005, 0.0005),   # 美股：双向 0.05%（零佣金为主，主要为滑点）
    "UNKNOWN": (0.0010, 0.0015),   # 兜底：偏保守
}


def _resolve_costs(market: str) -> tuple[float, float]:
    """将市场代号映射到单向交易成本；未知市场使用保守默认。"""
    return _MARKET_COSTS.get((market or "UNKNOWN").upper(), _MARKET_COSTS["UNKNOWN"])


# ---------------------------------------------------------------------------
# 回测主入口
# ---------------------------------------------------------------------------

def run_backtest(df: pd.DataFrame, market: str = "UNKNOWN") -> dict[str, Any]:
    """
    对含信号列的行情 DataFrame 运行模拟回测，返回绩效数据字典。

    参数
    ----
    df     : 需包含 date / open / close / buy_signal / sell_signal 列
    market : "A" / "HK" / "US"，用于套用对应市场的固定交易成本档位

    返回
    ----
    绩效数据字典（见模块文档），数据不足时返回 {}。
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
    if len(work) < 2:
        return {}

    opens  = work["open"].astype(float)
    close  = work["close"].astype(float)
    if close.iloc[0] <= 1e-6 or opens.iloc[0] <= 1e-6:
        return {}

    dates     = work["date"]
    buy_mask  = work["buy_signal"].fillna(False).astype(bool)
    sell_mask = work["sell_signal"].fillna(False).astype(bool)

    # ---- 同日双信号整根跳过（修复原版会产生 0% 伪交易的 bug）----
    both = buy_mask & sell_mask
    buy_mask  = buy_mask  & ~both
    sell_mask = sell_mask & ~both

    # ---- 严格交替过滤：空仓只接受买入，持仓只接受卖出 ----
    valid_buy  = pd.Series(False, index=work.index)
    valid_sell = pd.Series(False, index=work.index)
    has_position = False
    for i in work.index:
        if not has_position:
            if bool(buy_mask.loc[i]):
                valid_buy.loc[i]  = True
                has_position = True
        else:
            if bool(sell_mask.loc[i]):
                valid_sell.loc[i] = True
                has_position = False

    # ---- T+1 执行：信号当日生效，次日开盘成交 ----
    # shift(1) 把 T 日的 valid_buy 挪到 T+1 日的 exec_buy；末日之后无 T+1，自动丢弃
    exec_buy  = valid_buy.shift(1).fillna(False).astype(bool)
    exec_sell = valid_sell.shift(1).fillna(False).astype(bool)

    # ---- 应用该市场的交易成本 ----
    buy_cost, sell_cost = _resolve_costs(market)

    # ---- 逐日净值演算（按 T+1 开盘价成交，扣成本；收盘 mark-to-market）----
    cash              = 1.0
    shares            = 0.0
    in_pos            = False
    entry_idx         = None      # 买入成交所在 bar
    entry_open_px     = None      # 买入成交价（未扣成本，用于展示）
    equity_at_entry   = None      # 买入前现金，用于计算单笔净收益

    equity_curve: list[float] = []
    trades: list[dict]        = []

    last_i = len(work) - 1

    for i in range(len(work)):
        o = float(opens.iloc[i])
        c = float(close.iloc[i])

        # ==== 开盘成交：买入 / 卖出互斥，本轮迭代内只做一侧 ====
        if (not in_pos) and bool(exec_buy.iloc[i]) and o > 1e-6:
            equity_at_entry = cash
            shares          = (cash * (1.0 - buy_cost)) / o
            cash            = 0.0
            in_pos          = True
            entry_idx       = i
            entry_open_px   = o

        elif in_pos and bool(exec_sell.iloc[i]) and o > 1e-6:
            proceeds  = shares * o * (1.0 - sell_cost)
            net_ret   = proceeds / equity_at_entry - 1.0
            hold_days = int(i - entry_idx) if entry_idx is not None else 0
            trades.append({
                "买入日期":        dates.iloc[entry_idx].strftime("%Y-%m-%d"),
                "买入价格":        round(float(entry_open_px or 0.0), 4),
                "卖出日期":        dates.iloc[i].strftime("%Y-%m-%d"),
                "卖出价格":        round(o, 4),
                "单笔净收益(%)":   round(net_ret * 100.0, 4),
                "持仓天数":        hold_days,
            })
            cash             = proceeds
            shares           = 0.0
            in_pos           = False
            entry_idx        = None
            entry_open_px    = None
            equity_at_entry  = None

        # ==== 日终净值：持仓按收盘 mark-to-market，空仓即现金 ====
        equity_curve.append(float(shares * c) if in_pos else float(cash))

    # ---- 强平：回测最后一日仍持仓 ----
    if in_pos and entry_idx is not None and entry_open_px is not None:
        exit_close = float(close.iloc[last_i])
        if exit_close > 0:
            proceeds  = shares * exit_close * (1.0 - sell_cost)
            net_ret   = proceeds / equity_at_entry - 1.0
            hold_days = int(last_i - entry_idx)
            trades.append({
                "买入日期":        dates.iloc[entry_idx].strftime("%Y-%m-%d"),
                "买入价格":        round(float(entry_open_px), 4),
                "卖出日期":        dates.iloc[last_i].strftime("%Y-%m-%d"),
                "卖出价格":        round(exit_close, 4),
                "单笔净收益(%)":   round(net_ret * 100.0, 4),
                "持仓天数":        hold_days,
            })
            equity_curve[-1] = float(proceeds)

    # ==== 核心绩效 ====
    equity_s = pd.Series(equity_curve, index=work.index, dtype=float)
    strategy_total_return_pct = (equity_s.iloc[-1] - 1.0) * 100.0

    base_eq = close / float(close.iloc[0])
    base_total_return_pct = (base_eq.iloc[-1] - 1.0) * 100.0

    total_trades = len(trades)
    wins = [t["单笔净收益(%)"] for t in trades if t["单笔净收益(%)"] > 0]
    losses = [t["单笔净收益(%)"] for t in trades if t["单笔净收益(%)"] < 0]
    win_rate = (len(wins) / total_trades) if total_trades else 0.0

    running_max = equity_s.cummax()
    max_drawdown_pct = abs(float((equity_s / running_max - 1.0).min()) * 100.0)

    # ==== 描述性指标（零新增参数，全部从已有数据导出）====

    # 年化收益（以自然日折算，比交易日更保守）
    span_days  = max((dates.iloc[-1] - dates.iloc[0]).days, 1)
    years      = span_days / 365.25
    total_mult = equity_s.iloc[-1]
    if total_mult > 0 and years >= 1 / 12:  # 至少 1 个月才年化
        annual_return_pct = ((total_mult ** (1.0 / years)) - 1.0) * 100.0
    else:
        annual_return_pct = float("nan")

    # 夏普比率（无风险利率视为 0，按日频年化 √252）
    daily_ret = equity_s.pct_change().dropna()
    if len(daily_ret) > 5 and float(daily_ret.std()) > 1e-12:
        sharpe = float(daily_ret.mean() / daily_ret.std()) * (252 ** 0.5)
    else:
        sharpe = float("nan")

    # Calmar = 年化收益 / 最大回撤
    if max_drawdown_pct > 1e-6 and not _isnan(annual_return_pct):
        calmar = annual_return_pct / max_drawdown_pct
    else:
        calmar = float("nan")

    # 盈亏比 & 期望值
    avg_win  = (sum(wins) / len(wins))    if wins   else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0  # 负数
    if avg_loss < 0:
        profit_loss_ratio = abs(avg_win / avg_loss)
    else:
        profit_loss_ratio = float("inf") if wins else float("nan")

    expectancy_pct = (sum(wins) + sum(losses)) / total_trades if total_trades else 0.0

    # 交易频率（次 / 年）
    trades_per_year = (total_trades / years) if years > 0 else float("nan")

    # 在市占比
    hold_sum = sum(int(t["持仓天数"]) for t in trades)
    time_in_market_pct = (hold_sum / max(len(work) - 1, 1)) * 100.0

    # ==== 收益有效性检查：多维启发（不阻止显示，仅提示）====
    warnings: list[str] = []
    if total_trades < 3:
        warnings.append(f"样本过小（仅 {total_trades} 笔交易），统计意义有限")
    if total_trades >= 1 and strategy_total_return_pct > 0:
        abs_contrib = [abs(t["单笔净收益(%)"]) for t in trades]
        if sum(abs_contrib) > 0:
            top_share = max(abs_contrib) / sum(abs_contrib)
            if top_share > 0.6 and total_trades >= 2:
                warnings.append(f"收益过度依赖单笔（最大 1 笔贡献 {top_share * 100:.0f}%）")
    if (
        strategy_total_return_pct > base_total_return_pct + 50
        and total_trades < 5
    ):
        warnings.append("大幅跑赢基准但交易样本少，可能为偶发性收益")

    validity = "通过" if not warnings else "； ".join(warnings)

    # ==== 过去一年交易清单 ====
    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        # 保持向后兼容：历史调用方可能引用"单笔涨跌幅(%)"，加一列别名
        trades_df["单笔涨跌幅(%)"] = trades_df["单笔净收益(%)"]
        cutoff = work["date"].max() - pd.Timedelta(days=365)
        trades_df_year = trades_df[pd.to_datetime(trades_df["买入日期"]) >= cutoff]
    else:
        trades_df_year = trades_df

    return {
        # ---- 原有字段（向后兼容）----
        "策略总收益率(%)":  float(strategy_total_return_pct),
        "基准收益率(%)":    float(base_total_return_pct),
        "胜率":             float(win_rate),
        "最大回撤(%)":      float(max_drawdown_pct),
        "收益有效性检查":   validity,
        "equity_curve":     equity_s,
        "base_curve":       base_eq,
        "dates":            dates,
        "交易清单全量":     trades_df,
        "交易清单过去一年": trades_df_year,

        # ---- 新增字段：描述性指标 ----
        "年化收益率(%)":    float(annual_return_pct),
        "夏普比率":         float(sharpe),
        "Calmar比率":       float(calmar),
        "盈亏比":           float(profit_loss_ratio),
        "期望值(%)":        float(expectancy_pct),
        "交易频率(次/年)":  float(trades_per_year),
        "在市占比(%)":      float(time_in_market_pct),

        # ---- 本次回测采用的成本档位（展示给用户看）----
        "单向成本_买(%)":   round(buy_cost * 100.0, 4),
        "单向成本_卖(%)":   round(sell_cost * 100.0, 4),
    }


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _isnan(x: float) -> bool:
    """numpy-free 的 NaN 判断，避免引入额外依赖。"""
    try:
        return x != x
    except Exception:
        return False
