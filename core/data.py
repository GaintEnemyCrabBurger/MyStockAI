"""
core/data.py — 行情数据拉取层

职责
----
1. 封装 akshare 的 A 股与港股历史行情接口。
2. 统一列名映射（中文 → 英文）、数据类型转换、日期过滤与排序。
3. 处理网络代理冲突：在调用 akshare 前临时清除代理环境变量，结束后恢复。
4. 提供 A 股降级回退：东方财富源不稳定时自动切换新浪财经源。
5. 对外暴露统一的 fetch_stock_data() 入口，由市场类型路由到具体实现。

数据流
------
用户输入代码 + 市场类型
  → fetch_stock_data()
  → fetch_a_data() 或 fetch_hk_data()
  → akshare API（带代理保护上下文）
  → 列重命名 + 类型转换 + 排序
  → 标准 DataFrame（列：date / open / high / low / close / volume）

缓存策略
--------
使用 @st.cache_data(ttl=300) 对相同参数的请求缓存 5 分钟，
避免用户在调整指标参数时重复发起网络请求。
"""

from __future__ import annotations

import contextlib
import os
from datetime import date, datetime, timedelta

import akshare as ak
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

_PROXY_KEYS = [
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
]

_RENAME_MAP = {
    "日期": "date", "开盘": "open", "收盘": "close",
    "最高": "high", "最低": "low", "成交量": "volume",
}


@contextlib.contextmanager
def _no_proxy():
    """
    临时清除所有代理环境变量的上下文管理器。

    akshare 在部分网络环境下会被系统代理拦截导致拉取失败，
    此上下文管理器保证调用期间代理变量不干扰请求，退出后自动恢复原值。
    """
    backup = {k: os.environ.pop(k, None) for k in _PROXY_KEYS}
    try:
        yield
    finally:
        for k, v in backup.items():
            if v is not None:
                os.environ[k] = v


def _resolve_dates(
    backtest_start_date: date | None,
    backtest_end_date: date | None,
) -> tuple[str, str]:
    """将可选的回测日期转换为 akshare 需要的 'YYYYMMDD' 字符串格式。"""
    start_dt = backtest_start_date or (datetime.now() - timedelta(days=365)).date()
    end_dt = backtest_end_date or datetime.now().date()
    return (
        pd.to_datetime(start_dt).strftime("%Y%m%d"),
        pd.to_datetime(end_dt).strftime("%Y%m%d"),
    )


def _clean_df(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """
    对原始 akshare DataFrame 做标准化处理：
    1. 列重命名（中文 → 英文）
    2. 将 index 型日期重置为普通列
    3. 转换数值列类型，删除关键列为空的行
    4. 按日期升序排列，裁剪到请求的日期区间
    """
    df = df.rename(columns=_RENAME_MAP)

    if "date" not in df.columns:
        if df.index.name and "date" in str(df.index.name).lower():
            df = df.reset_index()
        elif isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index().rename(columns={"index": "date"})

    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = (
        df.dropna(subset=["date", "open", "high", "low", "close"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    df = df[
        (df["date"] >= pd.to_datetime(start_date))
        & (df["date"] <= pd.to_datetime(end_date))
    ]
    return df


# ---------------------------------------------------------------------------
# 港股数据拉取
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def fetch_hk_data(
    symbol: str,
    adjust: str = "qfq",
    period: str = "daily",
    backtest_start_date: date | None = None,
    backtest_end_date: date | None = None,
) -> pd.DataFrame:
    """
    拉取港股历史行情（akshare stock_hk_hist，备用 stock_hk_daily）。

    参数
    ----
    symbol              : 港股代码（5 位，如 "01810"）
    adjust              : 复权方式，默认前复权 "qfq"
    period              : 周期，默认日线 "daily"
    backtest_start_date : 回测起始日期（None 时取近 365 天）
    backtest_end_date   : 回测结束日期（None 时取今天）

    返回
    ----
    标准化 DataFrame，列：date / open / high / low / close / volume；
    拉取失败时返回空 DataFrame。
    """
    start_date, end_date = _resolve_dates(backtest_start_date, backtest_end_date)

    df = pd.DataFrame()
    with _no_proxy():
        try:
            df = ak.stock_hk_hist(
                symbol=symbol, period=period,
                start_date=start_date, end_date=end_date,
                adjust=adjust,
            )
        except Exception:
            pass

    if df is None or df.empty:
        try:
            with _no_proxy():
                df = ak.stock_hk_daily(symbol=symbol, adjust=adjust)
        except Exception:
            return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    return _clean_df(df, start_date, end_date)


# ---------------------------------------------------------------------------
# A 股数据拉取
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def fetch_a_data(
    symbol: str,
    adjust: str = "qfq",
    period: str = "daily",
    backtest_start_date: date | None = None,
    backtest_end_date: date | None = None,
) -> pd.DataFrame:
    """
    拉取 A 股历史行情（主源 stock_zh_a_hist，降级到新浪 stock_zh_a_daily）。

    降级策略：东方财富接口在部分网络下不稳定，若返回空则自动切换新浪源，
    新浪代码格式为 sh/sz + 6 位代码（如 sh600519、sz000001）。

    参数
    ----
    symbol              : A 股代码（6 位，如 "600519"）
    adjust              : 复权方式，默认前复权 "qfq"
    period              : 周期，默认日线 "daily"
    backtest_start_date : 回测起始日期
    backtest_end_date   : 回测结束日期

    返回
    ----
    标准化 DataFrame，列：date / open / high / low / close / volume；
    拉取失败时返回空 DataFrame。
    """
    start_date, end_date = _resolve_dates(backtest_start_date, backtest_end_date)

    df = pd.DataFrame()
    with _no_proxy():
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol, period=period,
                start_date=start_date, end_date=end_date,
                adjust=adjust,
            )
        except Exception:
            pass

    if df is None or df.empty:
        # 降级到新浪财经源
        sina_symbol = (
            f"sh{symbol}" if str(symbol).startswith(("5", "6", "9")) else f"sz{symbol}"
        )
        try:
            with _no_proxy():
                df = ak.stock_zh_a_daily(symbol=sina_symbol, adjust=adjust)
            if df is not None and not df.empty:
                if "date" not in df.columns and isinstance(df.index, pd.DatetimeIndex):
                    df = df.reset_index().rename(columns={"index": "date"})
                df["date"] = pd.to_datetime(df["date"])
                df = df[
                    (df["date"] >= pd.to_datetime(start_date))
                    & (df["date"] <= pd.to_datetime(end_date))
                ]
        except Exception:
            df = pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    return _clean_df(df, start_date, end_date)


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def fetch_stock_data(
    code: str,
    market: str,
    backtest_start_date: date | None = None,
    backtest_end_date: date | None = None,
) -> pd.DataFrame:
    """
    根据市场类型路由到对应的数据拉取函数。

    参数
    ----
    code   : 规范化后的股票代码
    market : "A" 表示 A 股，"HK" 表示港股
    """
    if market == "A":
        return fetch_a_data(
            code,
            backtest_start_date=backtest_start_date,
            backtest_end_date=backtest_end_date,
        )
    return fetch_hk_data(
        code,
        backtest_start_date=backtest_start_date,
        backtest_end_date=backtest_end_date,
    )
