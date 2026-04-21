"""
app.py — Streamlit 应用入口

职责
----
作为各功能模块的"组装层"，本文件只负责：
1. 页面基础配置（标题、主题样式）
2. 调用侧边栏渲染，获取用户参数
3. 调用 core 层完成数据拉取 → 指标计算 → 信号生成 → 回测
4. 调用 ui 层渲染主图与绩效面板
5. 展示操作建议与交易清单

不包含任何业务逻辑或图表构建代码。
"""

import warnings

import streamlit as st

from config import APP_VERSION, DEFAULT_CODES, MARKET_LABELS, detect_and_normalize
from core.backtest import run_backtest
from core.data import fetch_stock_data
from core.indicators import compute_indicators
from core.signals import calculate_signals, get_action_suggestion
from ui.charts import build_main_figure, build_performance_figure
from ui.sidebar import render_sidebar

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# 页面配置
# ---------------------------------------------------------------------------

def _setup_page() -> None:
    st.set_page_config(page_title="全球股票量化看板", layout="wide")
    # Apple HIG 风格：系统字体 / 柔和背景 / 卡片式布局 / 克制的色彩
    st.markdown(
        """
        <style>
        :root {
            --bg:           #F5F5F7;
            --card:         #FFFFFF;
            --text:         #1D1D1F;
            --text-muted:   #6E6E73;
            --divider:      #E5E5EA;
            --accent:       #0071E3;
            --accent-hover: #0077ED;
            --red:          #E5484D;
            --green:        #30A46C;
        }

        /* 全局字体：SF → PingFang → 系统中文兜底 */
        html, body, [class*="css"], .stApp, .stMarkdown,
        button, input, textarea, select {
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
                "SF Pro Text", "PingFang SC", "HarmonyOS Sans SC",
                "Microsoft YaHei", "Segoe UI", sans-serif !important;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }

        /* 主体：柔和背景 */
        .stApp {
            background-color: var(--bg);
            color: var(--text);
        }
        [data-testid="stHeader"],
        [data-testid="stToolbar"] { background-color: transparent; }

        /* 主内容区：限宽 + 居中 + 上下呼吸 */
        .main .block-container {
            max-width: 1280px;
            padding-top: 2rem;
            padding-bottom: 4rem;
        }

        /* 标题层级：靠字重与颜色，不靠字号 */
        h1, h2, h3, h4 {
            color: var(--text);
            letter-spacing: -0.01em;
            font-weight: 600;
        }
        h1 { font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em; }
        h2 { font-size: 1.25rem; }
        h3 { font-size: 1.05rem; color: var(--text-muted); font-weight: 500; }

        /* 卡片：承载图表与指标 */
        [data-testid="stPlotlyChart"],
        [data-testid="stDataFrame"] {
            background: var(--card);
            border-radius: 16px;
            padding: 8px 4px;
            box-shadow:
                0 1px 2px rgba(0,0,0,0.04),
                0 4px 16px rgba(0,0,0,0.04);
            border: 1px solid var(--divider);
        }

        /* 指标卡片 */
        [data-testid="stMetric"] {
            background: var(--card);
            border-radius: 14px;
            padding: 16px 20px;
            border: 1px solid var(--divider);
            box-shadow: 0 1px 2px rgba(0,0,0,0.03);
        }
        [data-testid="stMetricLabel"] {
            color: var(--text-muted);
            font-size: 0.82rem;
            font-weight: 500;
            letter-spacing: 0.01em;
        }
        [data-testid="stMetricValue"] {
            color: var(--text);
            font-weight: 600;
            letter-spacing: -0.01em;
        }

        /* 侧边栏：毛玻璃底色 */
        [data-testid="stSidebar"] {
            background-color: #FBFBFD;
            border-right: 1px solid var(--divider);
        }
        [data-testid="stSidebar"] .stMarkdown h2 {
            font-size: 1.05rem;
            font-weight: 600;
        }

        /* 按钮：Apple 胶囊 */
        .stButton > button,
        [data-testid="baseButton-primary"],
        [data-testid="baseButton-secondaryFormSubmit"] {
            background: var(--accent) !important;
            color: #FFFFFF !important;
            border: none !important;
            border-radius: 980px !important;
            padding: 0.5rem 1.25rem !important;
            font-weight: 500 !important;
            transition: background 0.15s ease;
            box-shadow: none !important;
        }
        .stButton > button:hover,
        [data-testid="baseButton-primary"]:hover {
            background: var(--accent-hover) !important;
        }

        /* 输入框 / 选择框：圆角 + 细边 */
        .stTextInput input,
        .stSelectbox > div > div,
        .stDateInput input {
            border-radius: 10px !important;
            border: 1px solid var(--divider) !important;
            background: var(--card) !important;
        }
        .stTextInput input:focus,
        .stDateInput input:focus {
            border-color: var(--accent) !important;
            box-shadow: 0 0 0 3px rgba(0,113,227,0.15) !important;
        }

        /* 滑块：精致的蓝色 */
        [data-testid="stSlider"] [role="slider"] {
            background: var(--accent) !important;
            box-shadow: 0 1px 3px rgba(0,0,0,0.15) !important;
        }

        /* Info / Warning 卡片：更柔和 */
        [data-testid="stAlert"] {
            border-radius: 12px;
            border: 1px solid var(--divider);
            background: var(--card);
        }

        /* 折叠面板：平滑 */
        [data-testid="stExpander"] {
            border-radius: 12px !important;
            border: 1px solid var(--divider) !important;
            background: var(--card) !important;
        }
        [data-testid="stExpander"] summary {
            font-weight: 500;
            color: var(--text);
        }

        /* 顶部 App 标题容器 */
        .app-hero {
            display: flex;
            align-items: baseline;
            gap: 10px;
            margin-bottom: 0.25rem;
        }
        .app-hero .title {
            font-size: 1.75rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            color: var(--text);
        }
        .app-hero .version {
            font-size: 0.85rem;
            font-weight: 500;
            color: var(--text-muted);
            padding: 2px 8px;
            background: var(--card);
            border: 1px solid var(--divider);
            border-radius: 999px;
        }
        .app-subtitle {
            color: var(--text-muted);
            font-size: 0.92rem;
            margin-bottom: 1.5rem;
        }

        /* 建议胶囊（主内容区顶部） */
        .suggestion-banner {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 18px;
            border-radius: 999px;
            background: var(--card);
            border: 1px solid var(--divider);
            box-shadow: 0 1px 2px rgba(0,0,0,0.03);
            margin: 0.25rem 0 1rem;
            font-size: 0.95rem;
            color: var(--text);
        }
        .suggestion-banner .dot {
            width: 8px; height: 8px; border-radius: 50%;
        }
        .suggestion-banner .label {
            color: var(--text-muted);
            font-size: 0.85rem;
        }
        .suggestion-banner .value {
            font-weight: 600;
        }

        /* DataFrame 细节 */
        [data-testid="stDataFrame"] {
            padding: 0 !important;
            overflow: hidden;
        }

        /* Multiselect：让输入区域即使有多个选中标签时也足够显眼 */
        [data-testid="stSidebar"] [data-baseweb="select"] {
            min-height: 48px;
            padding: 6px 8px !important;
            border-radius: 12px !important;
            border: 1px solid var(--divider) !important;
            background: var(--card) !important;
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
        }
        [data-testid="stSidebar"] [data-baseweb="select"]:focus-within {
            border-color: var(--accent) !important;
            box-shadow: 0 0 0 3px rgba(0,113,227,0.15) !important;
        }
        /* 已选标签：弱化红色,改成柔和中性色（避免看着像错误状态）*/
        [data-testid="stSidebar"] [data-baseweb="tag"] {
            background: #EEF3FF !important;
            color: #1D1D1F !important;
            border: 1px solid #D8E4FB !important;
            border-radius: 8px !important;
            margin: 3px 4px 3px 0 !important;
        }
        [data-testid="stSidebar"] [data-baseweb="tag"] span {
            color: #1D1D1F !important;
        }
        /* 输入光标区域：保证可见高度 */
        [data-testid="stSidebar"] [data-baseweb="select"] input {
            min-height: 28px !important;
            font-size: 0.95rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="app-hero">
            <span class="title">全球股票量化看板</span>
            <span class="version">{APP_VERSION}</span>
        </div>
        <div class="app-subtitle">三指标加权投票 · 信号纯度回测 · 支持 A 股 / 港股 / 美股</div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _build_suggestion(df) -> str:
    """
    综合最近 5 根 K 线的信号优先级，生成操作建议文字。

    优先级：强力卖出 > 强力买入 > 建议卖出 > 建议买入 > 状态型建议（持股/关注反弹/观望）
    """
    import pandas as pd
    recent = df.tail(5)
    empty = pd.Series(False, index=recent.index)
    if recent.get("strong_sell_signal", empty).any():
        return "强力卖出"
    if recent.get("strong_buy_signal", empty).any():
        return "强力买入"
    if recent.get("sell_signal", empty).any():
        return "建议卖出"
    if recent.get("buy_signal", empty).any():
        return "建议买入"
    return get_action_suggestion(df.iloc[-1])


def main() -> None:
    _setup_page()

    # ---- 侧边栏：渲染控件，获取提交状态 ----
    _submitted, _input_text = render_sidebar()

    if not st.session_state.get("run_analysis", False):
        st.info(
            "请在左侧「股票代码」框中选择或输入标的，然后点击「更新数据并计算」。\n\n"
            "**一个框 · 三种用法**：直接输入中文名（苹果 / 茅台 / 腾讯）、英文名"
            "（nvidia / tencent）或代码（600519 / 01810 / AAPL）即可搜索定位；"
            "精选池未收录的代码也可直接键入后按 Enter 加入。"
        )
        return

    # ---- 解析代码列表 ----
    raw_codes = [
        x.strip()
        for x in st.session_state.get("input_codes_text", ",".join(DEFAULT_CODES)).split(",")
        if x.strip()
    ]
    parsed = [
        (market, code)
        for raw in raw_codes
        for market, code in [detect_and_normalize(raw)]
        if code
    ]
    if not parsed:
        st.error("未识别到有效代码，请重新输入。")
        return

    # ---- 数据拉取 + 指标计算 + 信号生成（在选择主图之前完成,失败代码不阻断成功标的）----
    data_map: dict[tuple[str, str], object] = {}
    failed: list[tuple[str, str, str]] = []  # (market, code, reason)
    ss = st.session_state

    for market, code in parsed:
        raw_df = fetch_stock_data(
            code, market,
            backtest_start_date=ss["backtest_start_date"],
            backtest_end_date=ss["backtest_end_date"],
        )
        if raw_df.empty:
            failed.append((market, code, "数据源未返回行情（代码不存在或该区间无成交）"))
            continue

        calc = compute_indicators(
            raw_df,
            kdj_k=ss["kdj_k"],       kdj_d=ss["kdj_d"],
            kdj_smooth=ss["kdj_smooth"], rsi_length=ss["rsi_length"],
            macd_fast=ss["macd_fast"], macd_slow=ss["macd_slow"],
            macd_signal=ss["macd_signal"],
        )
        calc = calculate_signals(
            calc,
            vote_threshold=float(ss.get("vote_threshold", 1.6)),
            kdj_low=ss["kdj_low"],  kdj_high=ss["kdj_high"],
            rsi_low=ss["rsi_low"],  rsi_high=ss["rsi_high"],
            stay_days=int(ss.get("stay_days", 2)),
        )
        if calc.empty:
            failed.append((market, code, "数据不足,指标计算为空"))
            continue

        data_map[(market, code)] = calc

    # ---- 失败代码：折叠提示,不挡住成功标的的图表 ----
    if failed:
        fail_summary = "、".join(
            f"`{c}`（{MARKET_LABELS.get(m, m)}）" for m, c, _ in failed
        )
        st.warning(
            f"以下 {len(failed)} 个代码无可用数据：{fail_summary}。"
            "建议在左侧**股票代码**输入框中键入公司中/英文名（如 “卡骆驰”→ `CROX`）"
            "从下拉里选中正确代码。"
        )
        with st.expander(f"展开查看 {len(failed)} 个失败代码的详细原因", expanded=False):
            for m, c, why in failed:
                st.markdown(f"- **{c}**（{MARKET_LABELS.get(m, m)}）：{why}")

    # ---- 如果全部失败,直接退出 ----
    if not data_map:
        st.error(
            "所有代码都无可用数据。请检查输入——直接在左侧输入框里按公司名搜索"
            "（苹果、茅台、腾讯等）是最可靠的做法。"
        )
        return

    # ---- 只在可用标的里让用户选主图 ----
    display_labels = {
        (m, c): f"{c}（{MARKET_LABELS.get(m, m)}）"
        for m, c in data_map.keys()
    }
    viable_options = list(display_labels.values())
    selected_label = st.selectbox("主图展示", options=viable_options, index=0)
    selected_pair = next(
        (k for k, v in display_labels.items() if v == selected_label),
        next(iter(data_map.keys())),
    )

    selected_df = data_map[selected_pair]
    selected_label_str = display_labels[selected_pair]
    suggestion = _build_suggestion(selected_df)

    # 建议色彩映射（与图表右上角胶囊保持一致）
    color_map = {
        "强力卖出": "#E5484D", "建议卖出": "#E5484D",
        "强力买入": "#30A46C", "建议买入": "#30A46C",
        "持股":     "#0071E3", "关注反弹": "#F56E0F",
    }
    dot_color = color_map.get(suggestion, "#6E6E73")

    st.sidebar.markdown(
        f"""
        <div class="suggestion-banner" style="width:100%;box-sizing:border-box;">
            <span class="dot" style="background:{dot_color}"></span>
            <span class="label">当前建议</span>
            <span class="value" style="color:{dot_color}">{suggestion}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="suggestion-banner">
            <span class="dot" style="background:{dot_color}"></span>
            <span class="label">{selected_label_str} · 当前建议</span>
            <span class="value" style="color:{dot_color}">{suggestion}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    fig = build_main_figure(selected_df, selected_label_str, suggestion)
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "scrollZoom": True,
            "displaylogo": False,
            "modeBarButtonsToRemove": [
                "zoom2d", "pan2d", "select2d", "lasso2d",
                "zoomIn2d", "zoomOut2d", "autoScale2d", "resetScale2d",
            ],
        },
    )

    # ---- 策略绩效面板 ----
    st.markdown(
        """
        <div style="margin: 2.5rem 0 0.5rem; padding: 0;">
            <div style="font-size:1.25rem;font-weight:600;letter-spacing:-0.01em;color:#1D1D1F;">
                策略绩效
            </div>
            <div style="font-size:0.88rem;color:#6E6E73;margin-top:2px;">
                基于当前参数的信号纯度回测 · 起始净值 1.0
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    perf = run_backtest(selected_df, market=selected_pair[0])

    if not perf:
        st.info("暂无可统计数据。")
        return

    # 工具：把可能的 NaN / None 转成可读字符串
    def _fmt(v, tpl: str = "{:.2f}") -> str:
        if v is None:
            return "—"
        try:
            if v != v:
                return "—"
        except TypeError:
            return "—"
        return tpl.format(v)

    excess = perf["做T超额收益(%)"]

    # ---- 第一排：成绩单 + 核心问题答案 ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "策略总收益率",
        _fmt(perf["策略总收益率(%)"], "{:.2f}%"),
        delta=f"{excess:+.2f}% vs 底仓",
        delta_color="normal",
    )
    c2.metric("底仓基准 (B&H)", _fmt(perf["基准收益率(%)"], "{:.2f}%"))
    c3.metric("做 T 超额收益",  _fmt(excess,               "{:+.2f}%"))
    c4.metric("最大回撤",       _fmt(perf["最大回撤(%)"],  "{:.2f}%"))

    # ---- 第二排：时间归一化 + 行为画像 ----
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("年化收益率", _fmt(perf.get("年化收益率(%)"),    "{:.2f}%"))
    c6.metric("平均仓位",   _fmt(perf.get("平均仓位(%)"),       "{:.1f}%"))
    c7.metric("做 T 频率",  _fmt(perf.get("交易频率(次/年)"),  "{:.1f} 次/年"))
    c8.metric("单笔期望值", _fmt(perf.get("期望值(%)"),         "{:+.2f}%"))

    # ---- 一行说明，把容易混淆的概念统一讲清楚 ----
    st.caption(
        f"**分批做 T 模式**｜起始日 T+1 开盘满仓建仓，每次买/卖信号调整 1/3 仓位（档位 0–3/3），末日收盘强制清仓。  \n"
        f"• **做 T 超额** = 策略 − 底仓，直接回答\"做这些 T 到底比躺着多赚多少\"。  \n"
        f"• **平均仓位**：时间加权档位占比（满仓 = 100% / 空仓 = 0%），过低说明空仓太多，可能白白错过底仓涨幅。  \n"
        f"• **单笔期望值**：每次减仓/平仓按 WAC 结算的平均 PnL%（单片视角，不等同于总收益）。  \n"
        f"• **成本档位**（按市场自动套用）：买 {perf.get('单向成本_买(%)', 0.0):.2f}% / 卖 {perf.get('单向成本_卖(%)', 0.0):.2f}%，含佣金 + 印花税 + 滑点。"
    )

    if perf.get("收益有效性检查", "通过") != "通过":
        st.warning(perf["收益有效性检查"])

    perf_fig = build_performance_figure(perf)
    st.plotly_chart(perf_fig, use_container_width=True)

    with st.expander("做 T 动作流水（过去一年）", expanded=False):
        trades_year = perf["交易清单过去一年"]
        if trades_year is None or trades_year.empty:
            st.info("过去一年无交易动作。")
        else:
            st.dataframe(
                trades_year[[
                    "日期", "动作", "价格", "份额", "金额",
                    "持仓档位", "持仓成本", "本笔盈亏(%)",
                ]],
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                "本笔盈亏按卖出时点的加权平均成本（WAC）结算，已扣除买/卖双向交易成本。"
                "买入动作的盈亏列为空（尚未兑现）。"
            )


if __name__ == "__main__":
    main()
