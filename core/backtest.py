"""
core/backtest.py — 回测引擎

职责
----
基于信号列（buy_signal / sell_signal）对历史行情进行模拟交易，
计算策略绩效指标，返回纯数据字典（图表由 ui/charts.py 负责构建）。

回测规则（与原版一致）
----------------------
1. 成交价格：信号当日收盘价，不考虑滑点与手续费。
2. 首单限制：必须先出现买入信号才能建仓，第一个买点前的所有卖点忽略。
3. 持仓规则：严格交替——空仓期只接受买入信号，持仓期只接受卖出信号，
             冗余同类信号自动过滤。
4. 强平规则：回测结束日如仍持仓，以结束日收盘价强制平仓。
5. 净值基准：策略净值与基准净值均从回测起始日 1.0 出发，便于对比超额收益。
6. 异常检测：策略收益远超 100% 而标的实际未翻倍时触发"收益有效性"警告。

返回结构
--------
{
    "策略总收益率(%)": float,
    "基准收益率(%)":   float,
    "胜率":            float,       # 0.0-1.0
    "最大回撤(%)":     float,
    "收益有效性检查":  str,         # "通过" 或警告文字
    "equity_curve":    pd.Series,   # 策略净值序列（index 对齐 work.index）
    "base_curve":      pd.Series,   # 个股基准净值序列
    "dates":           pd.Series,   # 对应日期序列
    "交易清单全量":    pd.DataFrame,
    "交易清单过去一年": pd.DataFrame,
}
若数据不足则返回空字典 {}。
"""

from __future__ import annotations

import pandas as pd


def run_backtest(df: pd.DataFrame) -> dict:
    """
    对含信号列的行情 DataFrame 运行模拟回测，返回绩效数据字典。

    参数
    ----
    df : 含 date / close / buy_signal / sell_signal 列的 DataFrame

    返回
    ----
    绩效数据字典（见模块文档），数据不足时返回 {}。
    """
    required_cols = {"date", "close", "buy_signal", "sell_signal"}
    if not required_cols.issubset(df.columns):
        return {}

    work = (
        df.copy()
        .assign(date=lambda x: pd.to_datetime(x["date"]))
        .sort_values("date")
        .reset_index(drop=True)
        .dropna(subset=["close", "buy_signal", "sell_signal", "date"])
        .reset_index(drop=True)
    )
    if work.empty:
        return {}

    close = work["close"].astype(float)
    if close.iloc[0] <= 1e-6:
        return {}

    dates    = work["date"]
    buy_mask = work["buy_signal"].fillna(False).astype(bool)
    sell_mask = work["sell_signal"].fillna(False).astype(bool)

    # ---- 严格交替过滤：空仓只接受买入，持仓只接受卖出 ----
    valid_buy  = pd.Series(False, index=work.index)
    valid_sell = pd.Series(False, index=work.index)
    has_position = False
    for i in work.index:
        if not has_position:
            if bool(buy_mask.loc[i]):
                valid_buy.loc[i] = True
                has_position = True
        else:
            if bool(sell_mask.loc[i]):
                valid_sell.loc[i] = True
                has_position = False

    # ---- 模拟逐日净值演算 ----
    equity      = 1.0
    shares      = 0.0
    in_pos      = False
    entry_idx   = None
    entry_close = None

    equity_curve: list[float] = []
    trades:       list[dict]  = []

    for i in range(len(work)):
        if (not in_pos) and bool(valid_buy.iloc[i]):
            entry_close = float(close.iloc[i])
            if entry_close > 1e-6:
                shares    = equity / entry_close
                in_pos    = True
                entry_idx = i

        if in_pos:
            equity_today = shares * float(close.iloc[i])

            if bool(valid_sell.iloc[i]):
                exit_close = float(close.iloc[i])
                if exit_close > 1e-6 and entry_close is not None and entry_idx is not None:
                    ret       = exit_close / entry_close - 1.0
                    hold_days = int(i - entry_idx)
                    trades.append({
                        "买入日期":      dates.iloc[entry_idx].strftime("%Y-%m-%d"),
                        "买入价格":      round(entry_close, 4),
                        "卖出日期":      dates.iloc[i].strftime("%Y-%m-%d"),
                        "卖出价格":      round(exit_close, 4),
                        "单笔涨跌幅(%)": round(ret * 100.0, 4),
                        "持仓天数":      hold_days,
                    })
                equity      = equity_today
                shares      = 0.0
                in_pos      = False
                entry_idx   = None
                entry_close = None

            equity_curve.append(float(equity_today))
        else:
            equity_curve.append(float(equity))

    # ---- 强平：回测结束仍持仓 ----
    if in_pos and entry_idx is not None and entry_close is not None:
        last_i     = len(work) - 1
        exit_close = float(close.iloc[last_i])
        if exit_close > 0:
            ret       = exit_close / entry_close - 1.0
            hold_days = int(last_i - entry_idx)
            trades.append({
                "买入日期":      dates.iloc[entry_idx].strftime("%Y-%m-%d"),
                "买入价格":      round(entry_close, 4),
                "卖出日期":      dates.iloc[last_i].strftime("%Y-%m-%d"),
                "卖出价格":      round(exit_close, 4),
                "单笔涨跌幅(%)": round(ret * 100.0, 4),
                "持仓天数":      hold_days,
            })
            equity = shares * exit_close
            equity_curve[-1] = float(equity)

    # ---- 绩效统计 ----
    equity_s = pd.Series(equity_curve, index=work.index, dtype=float)
    strategy_total_return_pct = (equity_s.iloc[-1] - 1.0) * 100.0

    base_eq = close / float(close.iloc[0])
    base_total_return_pct = (base_eq.iloc[-1] - 1.0) * 100.0

    suspicious_return = bool(
        strategy_total_return_pct > 100
        and base_total_return_pct < 100
        and close.max() / max(close.min(), 1e-6) < 2
    )

    total_trades = len(trades)
    wins         = sum(1 for t in trades if t["单笔涨跌幅(%)"] > 0)
    win_rate     = (wins / total_trades) if total_trades else 0.0

    running_max     = equity_s.cummax()
    max_drawdown_pct = abs(float((equity_s / running_max - 1.0).min()) * 100.0)

    trades_df = pd.DataFrame(trades)
    cutoff = work["date"].max() - pd.Timedelta(days=365)
    trades_df_year = (
        trades_df[pd.to_datetime(trades_df["买入日期"]) >= cutoff]
        if not trades_df.empty
        else trades_df
    )

    return {
        "策略总收益率(%)": float(strategy_total_return_pct),
        "基准收益率(%)":   float(base_total_return_pct),
        "胜率":            float(win_rate),
        "最大回撤(%)":     float(max_drawdown_pct),
        "收益有效性检查":  "警告：收益率异常，请检查价格分母" if suspicious_return else "通过",
        "equity_curve":    equity_s,
        "base_curve":      base_eq,
        "dates":           dates,
        "交易清单全量":    trades_df,
        "交易清单过去一年": trades_df_year,
    }
