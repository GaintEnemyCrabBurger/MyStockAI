# MyStockAI — 港股 / A 股 / 美股量化可视化看板

基于 **Streamlit + akshare + pandas-ta + Plotly** 的跨市场量化分析工具，主打：

- **三大市场**：A 股（`600519` / `贵州茅台`）、港股（`700` / `腾讯控股`）、美股（`AAPL` / `Apple`）
- **模糊搜索**：中英文、公司名、行业、ticker 混合匹配
- **灵敏度 1–10 档**：加权评分制信号，从极保守到极激进均匀分布
- **金字塔分批回测**：1/3 仓位粒度做 T，WAC 成本法，真实交易成本
- **8 指标绩效面板**：追求"模糊的正确"，砍掉精度幻觉指标

---

## 目录

- [快速启动（本地）](#快速启动本地)
- [部署到 Streamlit Community Cloud](#部署到-streamlit-community-cloud)
- [项目结构](#项目结构)
- [实现原理](#实现原理)
- [使用说明](#使用说明)
- [FAQ](#faq)

---

## 快速启动（本地）

```bash
pip install -r requirements.txt
streamlit run app.py
```

默认地址：http://localhost:8501

Windows 用户可直接双击 `start_mystockai.bat`。

---

## 部署到 Streamlit Community Cloud

### 1. 推送到 GitHub

```bash
git push origin master    # 或任意你想部署的分支
```

### 2. 创建 App

1. 打开 [share.streamlit.io](https://share.streamlit.io/) 用 GitHub 账号登录
2. 点 **Create app** → 选本仓库
3. 填写：

| 字段 | 值 |
|---|---|
| **Repository** | `<你的用户名>/MyStockAI` |
| **Branch** | `master`（或你想部署的分支）|
| **Main file path** | `app.py` |
| **Python version** | `3.12`（推荐）|

4. 点 **Deploy**，2–5 分钟完成构建。每次 `git push` 会自动重新部署。

### 3. 关于 Secrets（重要）

> **本项目当前版本不需要任何 Secrets，部署时把 Secrets 栏留空即可。**

MyStockAI 使用 akshare 公共接口，**不需要任何 API Key / Token / 凭证**。你本地没有 `.env` 是正常的。

**未来如果你接入了需要凭证的数据源**（如 Tushare Pro、Finnhub、Alpha Vantage），按以下步骤把配置变成 Streamlit Secrets：

1. 本仓库里的 `.streamlit/secrets.toml.example` 就是**模板**，里面给出了常见数据源的 TOML 格式
2. 把需要的段复制出来，在 Streamlit Cloud 的：

   ```
   App 页面 → 右上角 ⋮ → Settings → Secrets
   ```

   这个文本框里直接粘贴 TOML 内容，例如：

   ```toml
   [tushare]
   token = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

   [finnhub]
   api_key = "yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"
   ```

3. 代码里读取：

   ```python
   import streamlit as st
   token = st.secrets["tushare"]["token"]
   ```

**安全说明**：真正的 `.streamlit/secrets.toml`（如果你本地创建）已经在 `.gitignore` 里被排除，**绝不会被 push 到仓库**。只有模板文件 `secrets.toml.example` 会进 git。

---

## 项目结构

```
MyStockAI/
├── app.py                    # Streamlit 入口（组装层，不含业务逻辑）
├── config.py                 # 全局配置、市场识别、灵敏度映射、用户设置持久化
├── core/
│   ├── data.py               # 行情拉取（A / HK / US 三市场，带代理保护与降级）
│   ├── search.py             # 跨市场模糊搜索与热门股票名录
│   ├── indicators.py         # 技术指标（MA / KDJ / RSI / MACD）
│   ├── signals.py            # 加权评分制信号生成与操作建议
│   └── backtest.py           # 金字塔分批回测引擎
├── ui/
│   ├── charts.py             # Plotly 图表（主图 + 绩效图）
│   └── sidebar.py            # 侧边栏控件与状态同步
├── .streamlit/
│   └── secrets.toml.example  # Secrets 模板（真正的 secrets.toml 被 gitignore）
├── requirements.txt
├── start_mystockai.bat       # Windows 一键启动
├── user_settings.json        # 用户偏好持久化（运行时自动生成）
└── README.md
```

### 分层设计

| 层级 | 目录/文件 | 依赖 | 约定 |
|---|---|---|---|
| 配置层 | `config.py` | 仅标准库 | 零依赖，可独立单元测试 |
| 业务逻辑层 | `core/` | pandas / akshare / pandas-ta | **不导入 Streamlit** |
| 视图层 | `ui/` | Streamlit / Plotly | 只做数据 → 视觉映射 |
| 入口层 | `app.py` | 全部 | 只做流程组装 |

---

## 实现原理

### 1. 跨市场数据拉取（`core/data.py`）

```
输入代码 → detect_and_normalize() → fetch_stock_data()
                                        ├─ fetch_a_data()    A 股（东财 → 新浪降级）
                                        ├─ fetch_hk_data()   港股
                                        └─ fetch_us_data()   美股（新浪源）
```

- **代理保护**：调用 akshare 前临时清除 `HTTP_PROXY` 等代理变量
- **A 股降级**：东财接口失败时自动切新浪
- **缓存**：`@st.cache_data(ttl=300)` 5 分钟
- **统一输出**：所有市场都返回标准列 `date / open / high / low / close / volume`

### 2. 模糊搜索（`core/search.py`）

A / HK / US 各维护一份**热门股票名录**（代码 + 英文名 + 中文名 + 行业）。用户可输入任意关键词：

- 中文名：`腾讯` → 00700.HK
- 英文片段：`Tesla` → TSLA
- 行业关键字：`新能源` → 宁德时代、比亚迪…
- 纯 ticker：`AAPL` 直通

任意合法代码（即使不在名录里）都能直接拉数据，只是搜索提示里没有。

### 3. 加权评分制信号（`core/signals.py`）

旧版用"3/3 票硬阈值"，灵敏度粒度跳跃严重；现在改为**连续评分**：

```
KDJ 事件 → 得分（金叉 1.0 / 超卖 0.5 / …）┐
RSI 事件 → 得分                            ├─→ 合计分数 ≥ threshold → buy/sell
MACD 事件 → 得分                           ┘

strong_threshold = threshold + (3.0 − threshold) × 0.4
合计 ≥ strong_threshold → strong_buy / strong_sell（任何灵敏度都可达）
```

灵敏度 1–10 线性映射到阈值 2.6 → 0.8，"极保守 → 极激进"均匀分布。

### 4. 金字塔分批回测（`core/backtest.py`）

真实做 T 场景建模，**不是**"一把满仓一把清仓"的激进择时。

**仓位档位：`{0, 1/3, 2/3, 3/3}`**

| 事件 | 动作 |
|---|---|
| 起始日 T+1 开盘 | 用全部现金满仓建仓（扣买入费）|
| 买入信号 & 档位 < 3 | 加仓 1/3 |
| 卖出信号 & 档位 > 0 | 减仓 1/3 |
| 同日双信号（冲突）| 整根跳过 |
| 现金不足一单位 | 跳过加仓（**不加杠杆**）|
| 末日收盘 | 强制清仓 |

**成本法：WAC（加权平均成本）**，与券商 App 显示口径一致。

**交易成本（固定、不可调、视作"物理常数"）：**

| 市场 | 买入 | 卖出 |
|---|---|---|
| A 股 | 0.05% | 0.15%（含印花税）|
| 港股 | 0.15% | 0.15% |
| 美股 | 0.05% | 0.05% |

均为佣金 + 印花税 + 滑点的保守估计。

**精简为 8 个核心指标**（刻意砍掉胜率 / 夏普 / Calmar / 盈亏比 / 在市占比——它们在做 T 场景下要么与直觉打架，要么是"精确的错误"）：

| 指标 | 回答什么问题 |
|---|---|
| 策略总收益率 | 这次做 T 的最终成绩 |
| 底仓基准 (B&H) | 不做 T 躺着是什么结果 |
| **做 T 超额收益** | 做 T 到底比躺着多赚/少赚多少（**核心**）|
| 最大回撤 | 最难受时亏了多少 |
| 年化收益率 | 把时间跨度标准化后的成绩 |
| 平均仓位 | 时间加权的"敢不敢满仓" |
| 做 T 频率 | 年均动作数，看折腾程度 |
| 单笔期望值 | 每次按 WAC 平均赚多少 |

自带 **"空仓陷阱"告警**：平均仓位 < 40% 且做 T 超额为负时，直接提示用户"空仓太多，错过底仓涨幅，做 T 拖后腿"。

### 5. 图表构建（`ui/charts.py`）

- 三行子图：K 线（62%）+ KDJ/RSI（22%）+ MACD（16%），共用 X 轴
- 信号标记：买入/卖出/强信号分用不同形状、颜色、描边
- 超买色带：KDJ / RSI 子图半透明绿/红底色
- 中文 hover：指针落在 K 线上时显示中文字段 + 带色涨跌百分比
- Apple HIG 审美配色与字体规范

### 6. 侧边栏状态同步（`ui/sidebar.py`）

灵敏度滑块与专家参数**双向同步**，通过 `updating_from_preset` 标志位防止预设批量写入时触发"自定义"误改。

---

## 使用说明

1. **输入股票**：侧边栏支持自由格式，逗号分隔或点预设标签
2. **选灵敏度**：1（防守）到 10（进攻），或手动展开专家参数精调
3. **回测区间**：默认近 365 天，可自定义
4. **点「更新数据并计算」**触发分析
5. **看绩效面板**：重点看 **做 T 超额收益** 和 **平均仓位**，再结合有效性告警

---

## FAQ

**Q：部署后数据拉不到怎么办？**
A：多半是某个 akshare 数据源被对应 IP 限流。打开 Streamlit Cloud 的 App 日志看具体报错；通常退化到新浪源比较稳。

**Q：可以接我自己的 Tushare Pro token 吗？**
A：可以。按 [Secrets 章节](#3-关于-secrets重要) 填入 token，然后在 `core/data.py` 里加一个 `fetch_a_data_tushare()` 实现即可。

**Q：热门名录不全，能搜我的股票怎么办？**
A：直接输完整代码（如 `002812` / `600519.SH` / `AAPL`），即使不在名录里也会直接拉数据并进入主图选择。

**Q：支持加密货币 / 期货 / 期权吗？**
A：不支持。本项目专注于现货股票做 T 场景，回测引擎假设"T+0 / T+1 开盘成交"，不适用于带杠杆或连续交易标的。

---

## 开发者备注

- `core/` 所有模块均可离线、不依赖 Streamlit 独立运行，方便单元测试
- 修改回测逻辑后建议跑一遍手工构造场景（高抛低吸 / 同日冲突 / 无信号）验证档位转换与 WAC
- 信号逻辑若再调整，留意 `strong_threshold` 的动态计算，确保各灵敏度档位下强信号都可达
