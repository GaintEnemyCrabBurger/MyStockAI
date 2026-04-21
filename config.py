"""
config.py — 全局配置、持久化设置、市场识别、灵敏度参数映射

职责
----
1. 常量定义：默认代码、版本号、设置文件路径等。
2. 用户设置读写：基于本地 JSON 文件持久化侧边栏状态。
3. 市场与代码规范化：根据位数自动判断 A 股 / 港股，并统一代码格式。
4. 灵敏度参数映射：将 1-10 档灵敏度线性插值为具体指标参数。
"""

from __future__ import annotations

import json
import os

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

APP_VERSION = "v4.0-modular"
DEFAULT_CODES = ["09992", "01810"]

# 灵敏度选项：1-10 整数档 + 手动微调
SENSITIVITY_OPTIONS: list[str] = [str(i) for i in range(1, 11)] + ["自定义"]

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_settings.json")


# ---------------------------------------------------------------------------
# 用户设置持久化
# ---------------------------------------------------------------------------

def load_user_settings() -> dict:
    """从本地 JSON 文件读取用户偏好；文件缺失或格式异常时返回空字典。"""
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_user_settings(data: dict) -> None:
    """将用户偏好序列化写入本地 JSON 文件；写入失败时静默忽略。"""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 市场识别与代码规范化
# ---------------------------------------------------------------------------

def detect_and_normalize(raw: str) -> tuple[str, str]:
    """
    根据输入自动识别市场并规范化股票代码。

    规则
    ----
    - 6 位纯数字 → A 股，原样返回
    - 其余 → 港股，不足 5 位时左补零

    参数
    ----
    raw : 用户输入的原始字符串（如 "1810"、"01810.HK"、"600519"）

    返回
    ----
    (market, code)：market 为 "A" 或 "HK"，code 为规范化后的纯数字字符串。
    """
    raw = str(raw).strip().upper()
    for suffix in (".HK", ".SH", ".SZ"):
        raw = raw.replace(suffix, "")
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 6:
        return "A", digits
    hk_code = digits.zfill(5) if digits else ""
    return "HK", hk_code


# ---------------------------------------------------------------------------
# 灵敏度参数映射（加权投票制）
# ---------------------------------------------------------------------------

def _lerp_int(v1: int, v10: int, level: int) -> int:
    """在 level=1 到 level=10 之间对两个整数端点做线性插值。"""
    ratio = (level - 1) / 9
    return int(round(v1 + (v10 - v1) * ratio))


def _lerp_float(v1: float, v10: float, level: int) -> float:
    """浮点版本的线性插值，用于投票阈值。"""
    ratio = (level - 1) / 9
    return float(v1 + (v10 - v1) * ratio)


# 灵敏度档位 → 投票阈值（加权制，总分 0-3.0）
# 设计目标：1-10 档之间信号频率呈等差递增，而非三段跳变
#
# 评分最大值：KDJ=1.0 + RSI=1.0 + MACD=1.0 = 3.0
# 典型场景（实测值）：
#   完美三指标反转     ≈ 2.4-3.0
#   两指标强共振       ≈ 1.5-2.0
#   单指标中强触发     ≈ 0.8-1.2
#   单指标弱触发       ≈ 0.3-0.6
#
#   L1  极保守：1.8（需明确多指标共振，年均 2-5 个信号）
#   L5  平衡值：1.0（两指标同时中度触发，月均 2-4 个）
#   L10 极进攻：0.3（任何弱触发，约 1-2 个/周）
_LEVEL_VOTE_THRESHOLD = {
    1:  1.80,
    2:  1.60,
    3:  1.40,
    4:  1.20,
    5:  1.00,
    6:  0.85,
    7:  0.70,
    8:  0.55,
    9:  0.40,
    10: 0.30,
}

# 灵敏度档位 → 超卖/超买区停留天数
# 停留越久 → KDJ 票的确认越严格
_LEVEL_STAY_DAYS = {
    1: 3, 2: 3, 3: 3,
    4: 2, 5: 2, 6: 2,
    7: 2, 8: 1, 9: 1, 10: 1,
}


def get_dynamic_params(level: int) -> dict:
    """
    将灵敏度档位（1-10）映射为各项技术指标参数与投票阈值。

    设计
    ----
    - 指标周期与 RSI 阈值：连续线性插值（_lerp_int）
    - 投票阈值：从 2.8 → 0.4 线性递减，每档相差约 0.25，10 档均匀分布
    - 停留天数：从 3 → 1 阶梯递减，用于 KDJ 票的区间确认

    返回字段
    --------
    kdj_k / kdj_d / kdj_smooth : KDJ 的 K、D 周期与二次平滑
    rsi_length                  : RSI 计算周期
    macd_fast / macd_slow / macd_signal : MACD 三线周期
    rsi_low / rsi_high          : RSI 买卖触发阈值
    kdj_low / kdj_high          : KDJ J 值触发阈值（固定）
    vote_threshold              : 加权投票触发阈值（浮点，0.3-3.0）
    stay_days                   : 超卖/超买区连续停留天数
    """
    level = max(1, min(10, int(level)))
    return {
        "kdj_k":          _lerp_int(18, 5,  level),
        "kdj_d":          _lerp_int(3,  2,  level),
        "kdj_smooth":     _lerp_int(3,  2,  level),
        "rsi_length":     _lerp_int(24, 6,  level),
        "macd_fast":      _lerp_int(26, 6,  level),
        "macd_slow":      _lerp_int(52, 13, level),
        "macd_signal":    _lerp_int(9,  5,  level),
        "rsi_low":        _lerp_int(20, 45, level),
        "rsi_high":       _lerp_int(80, 55, level),
        "kdj_low":        20,
        "kdj_high":       80,
        "vote_threshold": _LEVEL_VOTE_THRESHOLD[level],
        "stay_days":      _LEVEL_STAY_DAYS[level],
    }
