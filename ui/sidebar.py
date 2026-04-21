"""
ui/sidebar.py — Streamlit 侧边栏渲染与状态管理

职责
----
1. 初始化 session_state：首次加载时从持久化文件还原用户偏好，设置默认参数。
2. 渲染侧边栏控件：灵敏度滑块、专家参数折叠面板、日期选择、股票代码输入表单。
3. 管理灵敏度与专家参数的双向同步：
   - 拖动灵敏度滑块 → 自动用线性插值更新所有指标参数
   - 手动调整任一专家参数 → 灵敏度标记切换为"自定义"
4. 参数合法性保护：确保 MACD 慢线 > 快线，KDJ/RSI 低阈值 < 高阈值。
5. 渲染策略说明与回测说明的可折叠面板（只读文档）。

状态键约定（session_state）
---------------------------
sensitivity_level   : 当前灵敏度（"1"-"10" 或 "自定义"）
updating_from_preset: 标志位，防止灵敏度→参数更新时触发 on_expert_change 回调
kdj_k / kdj_d / kdj_smooth / rsi_length
macd_fast / macd_slow / macd_signal
kdj_low / kdj_high / rsi_low / rsi_high
backtest_start_date / backtest_end_date
input_codes_text    : 上次输入的股票代码字符串
settings_loaded     : 是否已从文件还原设置（防止重复加载）
run_analysis        : 表单提交标志，触发主页面计算

返回值
------
render_sidebar() 返回 (submitted: bool, input_text: str)
供 app.py 判断是否需要重新触发分析。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import streamlit as st

from config import (
    DEFAULT_CODES,
    SENSITIVITY_OPTIONS,
    get_dynamic_params,
    load_user_settings,
    save_user_settings,
)
from core.search import build_catalog, lookup_label


# ---------------------------------------------------------------------------
# 灵敏度 ↔ 专家参数 双向同步回调
# ---------------------------------------------------------------------------

def _apply_preset(level: str) -> None:
    """将指定灵敏度档位的参数写入 session_state（标志位保护，防止递归触发）。"""
    params = get_dynamic_params(int(level))
    st.session_state["updating_from_preset"] = True
    for k, v in params.items():
        st.session_state[k] = v
    st.session_state["sensitivity_level"] = level
    st.session_state["updating_from_preset"] = False


def _on_sensitivity_change() -> None:
    """灵敏度滑块 on_change 回调：非自定义时同步刷新专家参数。"""
    level = st.session_state.get("sensitivity_level", "5")
    if level != "自定义":
        _apply_preset(level)


def _on_expert_change() -> None:
    """任一专家参数 on_change 回调：将灵敏度标记为"自定义"。"""
    if st.session_state.get("updating_from_preset", False):
        return
    st.session_state["sensitivity_level"] = "自定义"


# ---------------------------------------------------------------------------
# session_state 初始化
# ---------------------------------------------------------------------------

def _init_state() -> None:
    """在第一次渲染时初始化所有 session_state 键，避免后续 KeyError。"""
    if "settings_loaded" not in st.session_state:
        settings = load_user_settings()
        st.session_state["input_codes_text"] = settings.get(
            "input_codes_text", ",".join(DEFAULT_CODES)
        )
        st.session_state["settings_loaded"] = True

    if "sensitivity_level" not in st.session_state:
        _apply_preset("5")

    if "updating_from_preset" not in st.session_state:
        st.session_state["updating_from_preset"] = False

    # 兼容旧版本存储的"手动微调"值
    if st.session_state.get("sensitivity_level") == "手动微调":
        st.session_state["sensitivity_level"] = "自定义"

    if "backtest_start_date" not in st.session_state:
        st.session_state["backtest_start_date"] = (
            datetime.now() - timedelta(days=365)
        ).date()
    if "backtest_end_date" not in st.session_state:
        st.session_state["backtest_end_date"] = datetime.now().date()


# ---------------------------------------------------------------------------
# 策略说明面板（只读）
# ---------------------------------------------------------------------------

def _render_strategy_docs() -> None:
    """渲染"策略逻辑说明"与"回测逻辑说明"两个折叠面板。"""
    lv_now    = st.session_state.get("sensitivity_level", "5")
    threshold = float(st.session_state.get("vote_threshold", 1.6))
    stay_days = int(st.session_state.get("stay_days", 2))

    with st.sidebar.expander("策略逻辑说明（点击展开）", expanded=False):
        st.markdown(
            f"**加权投票制**（灵敏度={lv_now}，阈值 = **{threshold:.2f} / 3.0**）\n\n"
            f"三个维度（KDJ / RSI / MACD）各自独立给出 **0.0 – 1.0** 的强度分，"
            f"总分 ≥ 阈值时触发信号。\n\n"
            f"**买分构成（满分 3.0）：**\n"
            f"- **KDJ**  金叉+J 拐 (+0.5)；连续 {stay_days} 天双重超卖 (+0.3)；J 极端超卖 (+0.2)\n"
            f"- **RSI**  ≤{st.session_state['rsi_low']} (+0.5)；≤{max(st.session_state['rsi_low']-5,10)} (+0.3)；"
            f"≤{max(st.session_state['rsi_low']-10,5)} (+0.2)\n"
            f"- **MACD** 金叉 (+0.4)；负柱收缩 (+0.3)；柱上穿零轴 (+0.3)\n\n"
            f"**卖分结构对称**（替换为死叉、超买、正柱收缩等）。\n\n"
            f"**强信号**：总分 ≥ 阈值 + 0.8 时升级为强力买入/卖出。\n\n"
            f"当前参数：KDJ({st.session_state['kdj_k']},{st.session_state['kdj_d']},{st.session_state['kdj_smooth']}) / "
            f"RSI({st.session_state['rsi_length']}) / "
            f"MACD({st.session_state['macd_fast']},{st.session_state['macd_slow']},{st.session_state['macd_signal']}) / "
            f"KDJ({st.session_state['kdj_low']}/{st.session_state['kdj_high']}) "
            f"RSI({st.session_state['rsi_low']}/{st.session_state['rsi_high']})"
        )

    with st.sidebar.expander("回测逻辑说明（点击展开）", expanded=False):
        st.markdown(
            "- 信号来源：使用当前参数生成的 `buy_signal` / `sell_signal`\n"
            "- 成交规则：信号当日**收盘价**买入或卖出（不含滑点/手续费）\n"
            "- 首单规则：必须先买后卖，第一个买点前的卖点全部忽略\n"
            "- 持仓规则：严格交替——空仓只接受买入，持仓只接受卖出\n"
            "- 强平规则：回测结束日仍持仓时，以结束日收盘价强制平仓\n"
            "- 净值起点：策略净值与基准净值均从回测起始日 `1.0` 出发对齐比较\n"
            "- 风险检查：策略收益异常高而标的未明显翻倍时触发警告"
        )


# ---------------------------------------------------------------------------
# 主渲染入口
# ---------------------------------------------------------------------------

def render_sidebar() -> tuple[bool, str]:
    """
    渲染完整侧边栏，返回 (submitted, input_text)。

    返回
    ----
    submitted  : 用户是否点击了"更新数据并计算"按钮
    input_text : 当前表单中的股票代码字符串
    """
    _init_state()

    # ---- 策略灵敏度 ----
    st.sidebar.markdown("## 策略预设")
    st.sidebar.select_slider(
        "策略灵敏度 (1-10)",
        options=SENSITIVITY_OPTIONS,
        key="sensitivity_level",
        on_change=_on_sensitivity_change,
    )
    lv = st.session_state["sensitivity_level"]
    # 每一档都给一句独立的、语感递进的风格说明
    _LEVEL_CAPTIONS = {
        "1":  "极端保守 · 只为历史级机会出手（阈值 1.80）",
        "2":  "非常保守 · 宁可错过不可做错（阈值 1.60）",
        "3":  "保守 · 在明确的共识点入场（阈值 1.40）",
        "4":  "稳健偏保守 · 需要多维度互相确认（阈值 1.20）",
        "5":  "平衡 · 默认策略，兼顾信号质量与数量（阈值 1.00）",
        "6":  "稳健偏进取 · 接受更多次级机会（阈值 0.85）",
        "7":  "进取 · 单一强指标即可驱动决策（阈值 0.70）",
        "8":  "激进 · 对初步征兆快速响应（阈值 0.55）",
        "9":  "非常激进 · 频繁进出，依赖止损管理（阈值 0.40）",
        "10": "极端激进 · 任何微弱触发都出手（阈值 0.30）",
    }
    if lv == "自定义":
        st.sidebar.caption(
            f"自定义：当前投票阈值 = {float(st.session_state.get('vote_threshold', 1.6)):.2f}，"
            f"停留天数 = {int(st.session_state.get('stay_days', 2))}"
        )
    else:
        st.sidebar.caption(_LEVEL_CAPTIONS.get(str(lv), ""))

    # ---- 专家参数（折叠） ----
    with st.sidebar.expander("专家参数（默认隐藏）", expanded=False):
        st.slider(
            "投票阈值（越低越敏感）", 0.3, 3.0,
            step=0.1, key="vote_threshold", on_change=_on_expert_change,
            help="加权投票总分 ≥ 该阈值时触发买/卖信号。总分满分 3.0（KDJ+RSI+MACD 各 1.0）。",
        )
        st.slider(
            "停留天数", 1, 5,
            step=1, key="stay_days", on_change=_on_expert_change,
            help="KDJ 票要求 J 与 RSI 在超卖/超买区连续停留的天数。越长越严格。",
        )
        st.markdown("---")
        st.slider("KDJ 周期 K",  5,  30, key="kdj_k",      on_change=_on_expert_change)
        st.slider("KDJ 周期 D",  2,  10, key="kdj_d",      on_change=_on_expert_change)
        st.slider("KDJ 平滑",    2,  10, key="kdj_smooth",  on_change=_on_expert_change)
        st.slider("RSI 周期",    4,  30, key="rsi_length",  on_change=_on_expert_change)
        st.slider("MACD 快线",   3,  30, key="macd_fast",   on_change=_on_expert_change)
        st.slider("MACD 慢线",   6,  80, key="macd_slow",   on_change=_on_expert_change)
        st.slider("MACD 信号线", 3,  30, key="macd_signal", on_change=_on_expert_change)
        st.slider("KDJ 低阈值", 10,  45, key="kdj_low",     on_change=_on_expert_change)
        st.slider("KDJ 高阈值", 55,  90, key="kdj_high",    on_change=_on_expert_change)
        st.slider("RSI 低阈值", 10,  45, key="rsi_low",     on_change=_on_expert_change)
        st.slider("RSI 高阈值", 55,  90, key="rsi_high",    on_change=_on_expert_change)

    # ---- 参数合法性保护 ----
    if st.session_state["macd_slow"] <= st.session_state["macd_fast"]:
        st.session_state["macd_slow"] = st.session_state["macd_fast"] + 1
    if st.session_state["kdj_low"] >= st.session_state["kdj_high"]:
        st.session_state["kdj_high"] = min(90, st.session_state["kdj_low"] + 1)
    if st.session_state["rsi_low"] >= st.session_state["rsi_high"]:
        st.session_state["rsi_high"] = min(90, st.session_state["rsi_low"] + 1)

    # ---- 回测日期 ----
    st.sidebar.date_input("回测起始日期", key="backtest_start_date")
    st.sidebar.date_input("回测结束日期", key="backtest_end_date")
    if st.session_state["backtest_start_date"] > st.session_state["backtest_end_date"]:
        st.sidebar.warning("起始日期不能晚于结束日期，已自动对齐。")
        st.session_state["backtest_end_date"] = st.session_state["backtest_start_date"]

    # ---- 策略与回测说明 ----
    _render_strategy_docs()

    # ---- 股票代码输入（单框：可搜索 + 可自由输入） ----
    selected_codes = _render_code_multiselect()

    with st.sidebar.form("stock_control_form"):
        submitted = st.form_submit_button(
            "更新数据并计算", use_container_width=True, type="primary"
        )

    input_text = ",".join(selected_codes)
    if submitted:
        st.session_state["run_analysis"] = True
        st.session_state["input_codes_text"] = input_text
        save_user_settings({"input_codes_text": input_text})

    return submitted, input_text


# ---------------------------------------------------------------------------
# 单一代码输入框（替代原"搜索框 + 输入框"双控件）
# ---------------------------------------------------------------------------

# 目录在进程生命周期内只构建一次
_CATALOG = build_catalog()
_CATALOG_CODES = [item["code"] for item in _CATALOG]


def _label_of(code: str) -> str:
    """
    multiselect 显示用：先查精选目录拿到带名称与市场的长标签；
    未命中的表外代码则原样显示，保证用户自定义条目也可识别。
    """
    hit = lookup_label(code)
    return hit if hit else code


def _render_code_multiselect() -> list[str]:
    """
    渲染单一的代码选择器。

    关键特性
    --------
    - **一个控件完成三件事**：浏览精选池 / 中英文模糊搜索 / 手动键入新代码
    - **中英文 + 代码全命中**：option label 同时包含四段信息
      （code · 中文名 · 英文名 · 市场），Streamlit 原生 filter 即可做
      子串级模糊搜索，无需自写 JS
    - **自由输入新代码**：`accept_new_options=True` 让用户对表外 ticker
      （如 SOFI、002747）直接按 Enter 追加为新选项
    - **长标签在徽章内可读**：通过 format_func 把存储的 code 重新格式化

    返回
    ----
    当前选中的 code 列表（去重，按用户选择顺序）。
    """
    saved = st.session_state.get("input_codes_text", ",".join(DEFAULT_CODES))
    current = [c.strip() for c in saved.split(",") if c.strip()]

    # options = 精选目录 ∪ 当前已选（保持顺序、去重）
    options = list(_CATALOG_CODES)
    seen = set(options)
    for c in current:
        if c not in seen:
            options.append(c)
            seen.add(c)

    selected = st.sidebar.multiselect(
        "🔍 搜索股票（中文名 / 英文名 / 代码均可）",
        options=options,
        default=current,
        key="stock_code_selector",
        format_func=_label_of,
        accept_new_options=True,
        placeholder="点此输入：苹果、茅台、nvidia、AAPL、600519 …",
        help=(
            "**一个框 · 三种用法：**\n"
            "- 中文名：苹果 / 茅台 / 腾讯\n"
            "- 英文名：nvidia / tencent / moutai\n"
            "- 代码：600519 / 01810 / AAPL（前缀即可）\n\n"
            "精选池（254 只）未收录的代码可直接键入后按 Enter 加入。"
        ),
    )
    cleaned = [c.strip() for c in selected if str(c).strip()]
    # 给出即时反馈：已选数量 + 展开"点击此处可继续搜索"的视觉提示
    if cleaned:
        st.sidebar.caption(
            f"已选 **{len(cleaned)}** 只 · 点击代码旁 ✕ 可移除 · "
            f"点击框内空白处可继续搜索"
        )
    else:
        st.sidebar.caption("⬆️ 点击上方输入框开始搜索（例如输入「苹果」）")
    return cleaned
