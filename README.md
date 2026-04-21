# MyStockAI — 港 A 股量化可视化看板

基于 Streamlit + akshare + pandas_ta + Plotly 构建的本地量化分析工具，
支持港股与 A 股历史行情的技术指标计算、买卖信号生成与信号纯度回测。

---

## 快速启动

```bash
pip install -r requirements.txt
streamlit run app.py
```

本地访问地址：[http://localhost:8501](http://localhost:8501)

---

## 项目结构

```
MyStockAI/
├── app.py               # Streamlit 入口（组装层，不含业务逻辑）
├── config.py            # 全局配置、持久化设置、市场识别、灵敏度参数
├── core/
│   ├── data.py          # 行情数据拉取（A股/港股，含代理保护与降级策略）
│   ├── indicators.py    # 技术指标计算（MA / KDJ / RSI / MACD）
│   ├── signals.py       # 三指标投票制信号生成与操作建议
│   └── backtest.py      # 模拟回测引擎（返回纯数据，不含图表）
├── ui/
│   ├── charts.py        # Plotly 图表构建（主图 + 绩效图）
│   └── sidebar.py       # Streamlit 侧边栏控件与状态管理
├── user_settings.json   # 用户偏好持久化（自动生成）
└── requirements.txt
```

### 分层设计原则


| 层级    | 目录/文件       | 依赖                       | 说明                |
| ----- | ----------- | ------------------------ | ----------------- |
| 配置层   | `config.py` | 仅标准库                     | 零依赖，可独立单元测试       |
| 业务逻辑层 | `core/`     | pandas、akshare、pandas_ta | 纯函数，不依赖 Streamlit |
| 视图层   | `ui/`       | Streamlit、Plotly         | 只做数据→视觉映射         |
| 入口层   | `app.py`    | 全部模块                     | 只做流程组装，不含逻辑       |


> **关键约定**：`core/` 中的代码不导入 Streamlit，可在任意 Python 环境中运行；
> `ui/` 中的代码不包含业务规则，只负责渲染。

---

## 实现原理

### 1. 数据拉取（`core/data.py`）

```
用户输入 → detect_and_normalize() → fetch_stock_data()
                                        ├── fetch_a_data()   A股
                                        └── fetch_hk_data()  港股
```

- **代理保护**：通过 `_no_proxy()` 上下文管理器在调用 akshare 前临时清除所有代理环境变量，结束后自动恢复，防止系统代理拦截导致数据拉取失败。
- **A 股降级策略**：东方财富（Eastmoney）接口不稳定时，自动切换至新浪财经源（`stock_zh_a_daily`），代码格式转换为 `sh/sz + 6位`。
- **缓存**：`@st.cache_data(ttl=300)` 对相同参数的请求缓存 5 分钟，避免用户调参时重复发起网络请求。
- **标准化输出**：所有列名统一映射为英文（`date / open / high / low / close / volume`），数据类型强制为数值型。

### 2. 技术指标（`core/indicators.py`）

在行情 DataFrame 上追加以下列（原始数据不被修改）：


| 列名                             | 指标       | 用途            |
| ------------------------------ | -------- | ------------- |
| MA5 / MA10 / MA20              | 简单移动平均   | 判断短中期趋势       |
| K / D / J                      | KDJ 随机指标 | 捕捉超买超卖，识别金叉死叉 |
| RSI6                           | 相对强弱指数   | 量化涨跌强弱，辅助确认反转 |
| MACD / MACD_SIGNAL / MACD_HIST | 指数平滑异同均线 | 趋势确认与动量检测     |


所有周期参数均由灵敏度档位通过 `get_dynamic_params()` 线性插值生成，支持专家模式手动覆盖。

### 3. 信号生成（`core/signals.py`）

#### 三指标投票制

```
KDJ 票 ┐
RSI 票  ├─→ 买票合计 ≥ 阈值 → buy_signal
MACD 票 ┘

KDJ 票 ┐
RSI 票  ├─→ 卖票合计 ≥ 阈值 → sell_signal
MACD 票 ┘
```

**投票阈值由灵敏度决定：**


| 灵敏度  | 所需票数  | 策略风格              |
| ---- | ----- | ----------------- |
| 1-3  | 3/3 票 | 防守型，极少信号，只捕捉历史级机会 |
| 4-7  | 2/3 票 | 平衡型，常规策略          |
| 8-10 | 1/3 票 | 进攻型，高频信号，噪音较多     |


**边沿检测**：对 `raw_buy/raw_sell` 做 `& (~shift(1))` 处理，确保连续满足条件时只在第一根 K 线触发信号，避免重复标记。

**强信号**：在普通信号基础上，同时要求 RSI 和 J 值超出更极端的阈值（low-5 / high+5）时升级为 `strong_buy/sell`。

### 4. 回测引擎（`core/backtest.py`）

#### 交易规则

```
时间轴: t0  t1  t2  t3  t4  t5
信号:   买       卖      买  卖
         ↓       ↓      ↓  ↓
净值:  1.0  →  1.12  →  持→ 最终值
```

1. **首单限制**：必须先出现买入信号才能建仓。
2. **严格交替**：空仓期忽略所有卖出信号；持仓期忽略所有买入信号。
3. **收盘价成交**：买入/卖出均以信号当日收盘价执行（不含滑点、手续费）。
4. **强制平仓**：回测结束日仍持仓时，以最后一个交易日收盘价平仓。
5. **基准对比**：策略净值与个股买入持有净值均从 1.0 出发，便于直观比较超额收益。

#### 绩效指标


| 指标     | 计算方式                      |
| ------ | ------------------------- |
| 策略总收益率 | 最终净值 − 1                  |
| 基准收益率  | 末日收盘价 / 首日收盘价 − 1         |
| 胜率     | 盈利交易笔数 / 总交易笔数            |
| 最大回撤   | max(净值历史高点 − 当前净值) / 历史高点 |


### 5. 图表构建（`ui/charts.py`）

- **三行子图布局**：K 线（62%）+ KDJ/RSI（22%）+ MACD（16%），共用 X 轴，禁用 rangeslider（多子图下可读性差）。
- **信号标记**：买入/卖出/强信号分别用不同形状和颜色的标记，最近一次强信号附加文字气泡标注。
- **超买色带**：KDJ/RSI 子图通过 `add_hrect` 绘制绿色（超卖）/ 红色（超买）半透明底色带。

### 6. 侧边栏状态管理（`ui/sidebar.py`）

灵敏度与专家参数之间存在**双向同步**关系：

```
拖动灵敏度滑块
  → _on_sensitivity_change()
  → _apply_preset(level)        ← updating_from_preset=True（防递归）
  → 批量写入 session_state 各参数
  → updating_from_preset=False

手动调整任一专家参数
  → _on_expert_change()
  → 若 updating_from_preset=False，则 sensitivity_level = "自定义"
```

`updating_from_preset` 标志位防止批量写入参数时触发 `on_expert_change` 回调，从而避免灵敏度被误改为"自定义"。

---

## 使用说明

1. 在侧边栏"输入股票代码"框中填入代码（逗号分隔），支持混合输入：
  - 港股：4 位或 5 位数字（如 `1810` 或 `01810`），也可带 `.HK` 后缀
  - A 股：6 位数字（如 `600519`），也可带 `.SH` 或 `.SZ` 后缀
2. 调整"策略灵敏度"（1=最保守，10=最激进）
3. 可选择自定义回测日期区间（默认近 365 天）
4. 点击「更新数据并计算」触发分析

---

## 分享给他人

```bash
# 临时公网链接（需先安装 Node.js）
npx localtunnel --port 8501
```

---

## 部署到 Streamlit Community Cloud

1. 将本项目推送到 GitHub 公开仓库
2. 访问 [https://share.streamlit.io/](https://share.streamlit.io/) 并部署：
  - Main file: `app.py`
  - Requirements: `requirements.txt`

