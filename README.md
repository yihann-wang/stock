# A 股套利全自动扫描系统

全自动监控要约收购公告 + 可转债转股套利、AI 解析要约信息、实时计算套利价差，通过 GitHub Actions 定时运行，钉钉推送套利信号。

---

## 目录

- [系统架构](#系统架构)
- [项目结构](#项目结构)
- [模块说明](#模块说明)
- [环境搭建](#环境搭建)
- [配置说明](#配置说明)
- [本地运行](#本地运行)
- [部署到 GitHub Actions](#部署到-github-actions)
- [钉钉消息格式](#钉钉消息格式)
- [套利计算逻辑](#套利计算逻辑)
- [数据流与文件说明](#数据流与文件说明)
- [常见问题与排障](#常见问题与排障)
- [二次开发指南](#二次开发指南)

---

## 系统架构

系统分为两个独立流程，由 GitHub Actions 分别调度：

```
流程1: 公告发现 (discover_main.py)
  每个工作日运行 2 次（开盘前 9:00 + 收盘后 15:30）

  巨潮资讯网搜索 → 过滤已知公告 → 下载 PDF → AI 提取要约信息
  → 校验字段 → 写入 known_offers.json → 钉钉推送

流程2: 价差监控 (monitor_main.py)
  交易时段每 30 分钟运行一次（9:30 - 15:00）

  ┌─ 要约收购监控: 加载已知要约 → 获取实时股价 → 计算价差/年化收益
  │  → 判断信号（套利/截止/负价差）→ 钉钉推送
  │
  └─ 可转债套利扫描: 获取全市场可转债行情 → 筛选负溢价(折价)转债
     → 过滤(成交额/价格/溢价率阈值) → 钉钉推送
```

## 项目结构

```
stock/
├── .github/workflows/
│   ├── discover.yml              # 工作流1: 公告发现 + AI 解析
│   └── monitor.yml               # 工作流2: 价差监控 + 信号推送
├── src/
│   ├── __init__.py
│   ├── config.py                 # 配置加载（YAML + JSON）
│   ├── announcement.py           # 巨潮资讯网公告搜索 + PDF 下载解析
│   ├── extractor.py              # AI 提取要约信息（DeepSeek / 其他 LLM）
│   ├── price.py                  # 实时股价获取（腾讯 API + akshare 备用）
│   ├── strategy.py               # 要约收购套利计算 + 信号判定
│   ├── cb_data.py                # 可转债数据获取（东方财富 + 腾讯双数据源）
│   ├── cb_strategy.py            # 可转债转股套利扫描 + 信号生成
│   ├── notifier.py               # 钉钉通知（签名鉴权 + 消息模板）
│   ├── discover_main.py          # 入口1: 公告发现流程
│   └── monitor_main.py           # 入口2: 价差监控流程（含可转债扫描）
├── config.yml                    # 阈值参数 + 通知配置
├── known_offers.json             # 已知要约记录（程序自动维护）
├── requirements.txt              # Python 依赖
└── README.md                     # 本文件
```

---

## 模块说明

### config.py — 配置加载

| 函数 | 作用 |
|------|------|
| `load_config()` | 读取 `config.yml`，返回 dict |
| `load_offers()` | 读取 `known_offers.json`，返回全部要约记录 |
| `save_offers(data)` | 保存要约记录，自动更新 `last_search_time` |
| `get_active_offers()` | 获取状态为 `active` 的要约，自动将过期要约标记为 `expired` |
| `get_known_announcement_ids()` | 获取所有已处理过的公告 ID 集合（用于去重） |
| `add_offer(offer)` | 添加一条新要约记录 |
| `get_env(key, required)` | 从环境变量获取值（用于 API Key 等敏感信息） |

### announcement.py — 公告搜索

| 函数 | 作用 |
|------|------|
| `search_announcements(keyword, days)` | 调用巨潮资讯网全文检索 API，返回公告列表 |
| `download_and_extract_text(pdf_url, max_pages)` | 下载公告 PDF 并用 pdfplumber 提取文本 |
| `get_pdf_url(adjunct_url)` | 拼接完整 PDF 下载链接（`static.cninfo.com.cn`） |
| `get_announcement_date(timestamp_ms)` | 毫秒时间戳转日期字符串 |
| `make_announcement_id(ann)` | 生成公告唯一 ID（用于去重） |

**关键细节：**
- 巨潮 API 地址：`http://www.cninfo.com.cn/new/fulltextSearch/full`（POST）
- PDF 下载地址：`http://static.cninfo.com.cn/{adjunctUrl}`（注意不是 `www.cninfo.com.cn`）
- 默认搜索关键词：`要约收购报告书`
- 请求间隔 1 秒，每天仅 2 次检索，不会触发反爬

### extractor.py — AI 提取

| 函数 | 作用 |
|------|------|
| `extract_offer_info(pdf_text)` | 调用 LLM API 从 PDF 文本中提取结构化要约信息 |
| `validate_offer(offer)` | 校验提取结果的完整性（价格、日期、代码等） |

**AI 返回的 JSON 结构：**

```json
{
  "stock_code": "6位数字",
  "stock_name": "股票名称",
  "offer_price": 10.00,
  "offer_start": "YYYY-MM-DD",
  "offer_end": "YYYY-MM-DD",
  "type": "full 或 partial",
  "partial_pct": 100,
  "condition": "none 或 min_accept",
  "min_accept_ratio": 0,
  "acquirer": "收购方名称",
  "notes": "一句话背景"
}
```

**更换 LLM 模型：** 使用 OpenAI 兼容接口，修改 `config.yml` 中的 `extractor.model` 和 `extractor.base_url` 即可切换任意模型（DeepSeek、GPT、Gemini 等）。当前配置使用 `gemini-3-flash-preview`。

**环境变量：** 优先读取 `LLM_API_KEY`，若不存在则读取 `DEEPSEEK_API_KEY`（向后兼容）。

### price.py — 股价获取

三级降级策略：

| 优先级 | 数据源 | 接口 | 特点 |
|--------|--------|------|------|
| 1 | 腾讯股票 | `http://qt.gtimg.cn/q={symbol}` | HTTP 直连，不受代理/TUN 影响，返回价格+成交额 |
| 2 | akshare 单股 | `stock_bid_ask_em()` | 东方财富 API，轻量级 |
| 3 | akshare 全量 | `stock_zh_a_spot_em()` | 东方财富全量行情，带 60 秒缓存 |

**代理处理：** 模块启动时自动清除系统代理设置（环境变量 + Windows 注册表 + requests Session 级别），确保国内 API 直连。如果使用 Clash TUN/Fake-IP 模式，腾讯 API（HTTP）仍可正常工作。

| 函数 | 作用 |
|------|------|
| `get_realtime_price(stock_code, retries)` | 获取实时股价，返回 `{"current_price": float, "daily_volume": float}` |
| `is_trading_day()` | 通过新浪交易日历判断今天是否为交易日 |

### strategy.py — 套利计算

**核心公式：**

```
价差收益率 = (要约价 - 当前市价) / 当前市价 × 100%
年化收益率 = 价差收益率 × (365 / 剩余天数)
部分要约调整 = 价差收益率 × (收购比例上限 / 100)
```

**三种信号类型：**

| 信号 | 触发条件 | 说明 |
|------|----------|------|
| `spread` | 价差 >= 阈值 且 年化 >= 阈值 且 成交额 >= 阈值 | 套利机会 |
| `deadline` | 距截止日 <= N 天 | 到期提醒 |
| `negative` | 当前价 > 要约价 | 负价差警告 |

| 函数 / 类 | 作用 |
|-----------|------|
| `ArbitrageResult` | 套利计算结果数据类 |
| `Signal` | 信号数据类（类型 + 结果 + 消息） |
| `calculate_arbitrage(offer)` | 计算单个要约的套利指标 |
| `evaluate_signals(offers)` | 对所有活跃要约评估信号 |

### notifier.py — 钉钉通知

使用签名鉴权（HmacSHA256）调用钉钉 Webhook，发送 Markdown 格式消息。

| 函数 | 对应场景 |
|------|----------|
| `send_dingtalk(title, markdown_text)` | 底层发送函数 |
| `notify_new_offer_validated(ann, offer, arb)` | 新公告 - AI 校验通过 |
| `notify_new_offer_unvalidated(ann, offer, errors)` | 新公告 - 需人工确认 |
| `notify_spread_signal(result)` | 日常套利信号 |
| `notify_deadline_warning(result)` | 截止日提醒 |
| `notify_negative_spread(result)` | 负价差警告 |
| `notify_cb_arbitrage(results)` | 可转债转股套利信号（合并推送） |

### discover_main.py — 公告发现入口

完整流程：

```
1. 加载配置
2. 调用巨潮 API 搜索公告
3. 与 known_offers.json 比对，过滤已知公告
4. 对每条新公告:
   a. 下载 PDF → 提取文本
   b. 调用 AI 提取要约信息
   c. 用公告中的股票代码/名称补充缺失字段
   d. 校验字段
   e. 校验通过 → 保存为 active + 尝试实时套利测算 + 推送通知
      校验未通过 → 保存为 pending_review + 推送人工确认通知
5. 自动更新 known_offers.json
```

### cb_data.py — 可转债数据获取

双数据源，自动降级：

| 优先级 | 数据源 | 说明 |
|--------|--------|------|
| 1 | 东方财富 push2 API | 实时行情，一次请求返回转股价值 + 溢价率 |
| 2 | 数据中心 + 腾讯 API | 数据中心获取转股价，腾讯获取实时价格，自行计算溢价率 |

| 函数 | 作用 |
|------|------|
| `get_cb_list()` | 获取全市场可转债数据，自动选择可用数据源 |

### cb_strategy.py — 可转债套利策略

**套利逻辑：** 当转股价值 > 转债价格（转股溢价率为负），买入转债并当日转股，次日卖出正股即可获利。

```
转股价值 = 正股价格 × (100 / 转股价)
转股溢价率 = (转债价格 - 转股价值) / 转股价值 × 100%
溢价率 < 0 → 套利机会
```

| 函数 / 类 | 作用 |
|-----------|------|
| `CBArbitrageResult` | 可转债套利结果数据类 |
| `scan_cb_arbitrage()` | 扫描全市场，返回负溢价套利机会列表 |

**过滤条件（均可在 config.yml 中调整）：**

| 条件 | 默认值 | 说明 |
|------|--------|------|
| `max_premium_rate` | -0.5% | 溢价率低于此值才推送 |
| `min_volume` | 1000 万 | 过滤无流动性的转债 |
| `min_bond_price` | 90 元 | 过滤信用风险高的低价债 |
| `max_bond_price` | 200 元 | 过滤投机性高价债 |
| `max_results` | 10 | 每次最多推送条数 |

### monitor_main.py — 价差监控入口

完整流程：

```
1. 检查是否为交易日（非交易日直接退出）
2. 要约收购监控:
   a. 加载活跃要约（自动过期处理）
   b. 对所有活跃要约调用 evaluate_signals()
   c. 根据信号类型发送对应的钉钉通知
3. 可转债套利扫描:
   a. 获取全市场可转债数据
   b. 筛选负溢价套利机会
   c. 通过阈值过滤后推送钉钉通知
```

---

## 环境搭建

### 前置要求

- Python 3.11+
- conda（推荐）或 pip

### 使用 conda

```bash
conda create -n stock python=3.11 -y
conda activate stock
pip install -r requirements.txt
```

### 仅使用 pip

```bash
pip install -r requirements.txt
```

### 依赖列表 (requirements.txt)

| 包 | 用途 |
|----|------|
| akshare | A 股行情数据（备用数据源） |
| requests | HTTP 请求 |
| pyyaml | 读取 config.yml |
| pdfplumber | PDF 文本提取 |
| openai | 调用 LLM API（OpenAI 兼容接口） |

---

## 配置说明

### config.yml

```yaml
# 要约收购 - 信号过滤阈值
thresholds:
  min_spread_pct: 3.0        # 最小价差收益率(%)，低于此值不推送套利信号
  min_annualized_pct: 30.0   # 最小年化收益率(%)
  warn_days_left: 5          # 截止提醒天数，剩余天数 <= 此值时推送提醒
  min_daily_volume: 500      # 最小日均成交额(万元)，过滤流动性差的标的

# 公告搜索配置
announcement:
  keyword: "要约收购报告书"   # 巨潮搜索关键词，一般不需要改
  search_days: 7             # 搜索最近 N 天的公告

# AI 提取配置
extractor:
  model: "gemini-3-flash-preview"      # LLM 模型名
  base_url: "https://jeniya.top/v1"    # OpenAI 兼容 API 地址
  max_text_length: 8000                # PDF 文本截取长度（避免超 token）

# 可转债套利配置
cb_arbitrage:
  enabled: true              # 设为 false 可禁用可转债扫描
  max_premium_rate: -0.5     # 溢价率上限(%)，低于此值才推送（负值=折价=套利）
  min_volume: 1000           # 最小成交额(万元)，过滤流动性差的转债
  min_bond_price: 90         # 最低转债价格(元)，过滤信用风险高的低价债
  max_bond_price: 200        # 最高转债价格(元)，过滤投机性高价债
  max_results: 10            # 最多推送条数

# 通知配置
notification:
  dingtalk:
    enabled: true   # 设为 false 可禁用钉钉通知（调试时有用）
```

### 环境变量

| 变量名 | 必填 | 说明 |
|--------|------|------|
| `LLM_API_KEY` | 是 | LLM API 密钥（也兼容 `DEEPSEEK_API_KEY`） |
| `LLM_BASE_URL` | 否 | LLM API 地址，覆盖 config.yml 中的 `extractor.base_url` |
| `LLM_MODEL` | 否 | LLM 模型名，覆盖 config.yml 中的 `extractor.model` |
| `DINGTALK_WEBHOOK` | 是 | 钉钉机器人 Webhook URL |
| `DINGTALK_SECRET` | 是 | 钉钉机器人签名密钥 |

---

## 本地运行

### Windows (CMD)

```bash
conda activate stock
cd E:\study\tools\stock

set LLM_API_KEY=sk-xxx
set DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
set DINGTALK_SECRET=SECxxx

:: 运行公告发现
python -m src.discover_main

:: 运行价差监控
python -m src.monitor_main
```

### Linux / macOS / Git Bash

```bash
conda activate stock
cd /path/to/stock

export LLM_API_KEY=sk-xxx
export DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx
export DINGTALK_SECRET=SECxxx

python -m src.discover_main
python -m src.monitor_main
```

### 单独测试各模块

```bash
# 测试公告搜索
python -c "from src.announcement import search_announcements; print(search_announcements(days=30))"

# 测试股价获取
python -c "from src.price import get_realtime_price; print(get_realtime_price('003041'))"

# 测试交易日判断
python -c "from src.price import is_trading_day; print(is_trading_day())"

# 测试可转债数据获取
python -c "from src.cb_data import get_cb_list; data = get_cb_list(); print(f'{len(data)} bonds')"

# 测试可转债套利扫描
python -c "from src.cb_strategy import scan_cb_arbitrage; print(scan_cb_arbitrage())"

# 测试 AI 提取（需要设置 LLM_API_KEY 环境变量）
python -c "
from src.announcement import download_and_extract_text
from src.extractor import extract_offer_info
text = download_and_extract_text('http://static.cninfo.com.cn/finalpage/2026-03-20/1225019482.PDF')
print(extract_offer_info(text))
"
```

---

## 部署到 GitHub Actions

### 1. 创建 GitHub 仓库

将本项目推送到 GitHub 仓库。

### 2. 配置 Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret 名 | 值 | 获取方式 |
|-----------|-----|---------|
| `DINGTALK_WEBHOOK` | `https://oapi.dingtalk.com/robot/send?access_token=xxx` | 钉钉群 → 群设置 → 智能群助手 → 添加自定义机器人 |
| `DINGTALK_SECRET` | `SECxxxxxxxx` | 创建机器人时选"加签"获得 |
| `LLM_API_KEY` | `sk-xxxxxxxx` | 你使用的 LLM API 提供商 |

### 3. 开启写权限

Settings → Actions → General → Workflow permissions → 选 **Read and write permissions**

这是因为 discover 工作流会自动 `git commit` 更新 `known_offers.json`。

### 4. 工作流调度

| 工作流 | 文件 | 频率 | 用量 |
|--------|------|------|------|
| 公告发现 | `discover.yml` | 工作日 2 次 (9:00, 15:30 北京时间) | ~40 分钟/月 |
| 价差监控 | `monitor.yml` | 交易时段每 30 分钟 | ~240 分钟/月 |
| **合计** | | | **~280 分钟/月**（免费额度 2000 分钟） |

两个工作流均支持 `workflow_dispatch`，可在 Actions 页面手动触发。

---

## 钉钉消息格式

### 1. 新公告（AI 校验通过）

```
【新要约收购公告发现】
━━━━━━━━━━━━━━━
股票: 003041 真爱美家
公告: 《要约收购报告书》
发布日期: 2026-03-20
公告原文: [点击查看PDF](链接)

AI 提取分析:
要约价格: 27.74 元
要约期限: 2026-03-25 ~ 2026-04-24
要约类型: 部分要约 | 无条件
收购方: 广州探迹远擎科技合伙企业

实时套利测算:
当前股价: 50.27 元
价差: -22.53 元 (-44.8%)
...

AI 置信度: 高 - 已自动加入监控
```

### 2. 新公告（需人工确认）

推送 PDF 链接 + AI 部分提取结果 + 标注缺失字段。

### 3. 套利信号

推送股票、现价、要约价、价差、年化、类型、剩余天数。

### 4. 截止日提醒

推送剩余天数、当前价差，提示尽快决策。

### 5. 负价差警告

当前价高于要约价时推送，提示无套利空间。

### 6. 可转债转股套利信号

```
【可转债转股套利信号】
━━━━━━━━━━━━━━━
发现 2 只负溢价可转债

- 奥图转债(118027) | 溢价率 -8.79% | 转债价 103.55 → 转股价值 113.53
  | 每10张赚 99.83元 | 成交额 58644万
- 万丰转债(127017) | 溢价率 -1.20% | ...

操作: 买入转债 → 当日转股 → 次日卖出正股
风险: 次日正股开盘价下跌(隔夜风险)
```

---

## 套利计算逻辑

### 要约收购原理

收购方以固定价格在规定期限内收购股份。当市价低于要约价时，买入股票并接受收购即可获得价差。

### 公式

```
价差收益率 = (要约价 - 市价) / 市价 × 100%
年化收益率 = 价差收益率 × 365 / 剩余天数
```

### 部分要约调整

部分要约只收购一定比例，超额按比例接纳：

```
调整后收益 = 价差收益率 × (收购比例上限 / 100)
```

### 要约类型

| 类型 | 说明 | 套利确定性 |
|------|------|-----------|
| 全面要约 + 无条件 | 收购方接纳所有申报 | 最高 |
| 全面要约 + 有条件 | 有最低接纳比例等前提 | 较高 |
| 部分要约 | 仅收购一定比例 | 中等 |

### 可转债转股套利原理

可转债持有人可按转股价将转债转换为正股。当转股价值 > 转债价格时，存在套利空间：

```
转股价值 = 正股价格 × (100 / 转股价)
转股溢价率 = (转债价格 - 转股价值) / 转股价值 × 100%
每10张收益 = (转股价值 - 转债价格) × 10
```

**操作流程：** 买入转债（T日）→ 当日转股（T日）→ 次日卖出正股（T+1日）

**风险：** 次日正股开盘价可能下跌（隔夜风险），实际收益 = 理论收益 - 隔夜波动

**数据源：** 东方财富 push2 API（主）+ 数据中心 + 腾讯行情 API（备），自动降级切换。

---

## 数据流与文件说明

### known_offers.json

由程序自动维护，记录所有发现过的要约。每条记录包含：

```json
{
  "announcement_id": "cninfo_2026-03-20_003041_1225019482",
  "stock_code": "003041",
  "stock_name": "真爱美家",
  "offer_price": 27.74,
  "offer_start": "2026-03-25",
  "offer_end": "2026-04-24",
  "type": "partial",
  "condition": "none",
  "min_accept_ratio": 0,
  "partial_pct": 15,
  "acquirer": "广州探迹远擎科技合伙企业",
  "notes": "...",
  "pdf_url": "http://static.cninfo.com.cn/...",
  "discovered_at": "2026-03-20T09:00:00",
  "ai_validated": true,
  "status": "active"
}
```

**status 生命周期：**
- `active` — 进行中，会被 monitor 监控
- `expired` — 已过截止日，由 `get_active_offers()` 自动标记
- `pending_review` — AI 校验未通过，等待人工确认

**手动修改：** 可以直接编辑此文件。例如人工确认后将 `pending_review` 改为 `active` 并补全缺失字段，即可纳入监控。

---

## 常见问题与排障

### Q: 本地运行股价获取失败（ProxyError）

**原因：** 代理工具（Clash/V2Ray 等）的 TUN/Fake-IP 模式劫持了 DNS 和网络流量。

**解决方案：**
- 程序已内置腾讯 API 作为首选数据源，HTTP 直连不受 TUN 影响
- 如果仍有问题，在代理规则中将 `*.eastmoney.com` 和 `qt.gtimg.cn` 设为 DIRECT
- GitHub Actions 上无此问题

### Q: AI 提取结果不准确

- 增大 `config.yml` 中的 `max_text_length`（默认 8000），让 AI 看到更多内容
- 更换更强的模型（如 `deepseek-chat`、`gpt-4o`）
- 校验未通过的公告会自动推送人工确认通知，不会产生错误信号

### Q: 巨潮搜索不到公告

- 检查 `search_days` 是否足够大
- 确认关键词是否匹配，可尝试改为 `要约收购`（更宽泛）
- 巨潮 API 偶尔不稳定，下次运行会自动重试

### Q: 想更换 LLM 提供商

修改 `config.yml`：

```yaml
extractor:
  model: "deepseek-chat"               # 或 gpt-4o、claude-3-sonnet 等
  base_url: "https://api.deepseek.com"  # 对应的 API 地址
```

设置对应的环境变量 `LLM_API_KEY`，只要是 OpenAI 兼容接口即可。

### Q: 如何手动添加要约

直接编辑 `known_offers.json`，在 `offers` 数组中添加一条记录（参考已有格式），设 `status` 为 `active`，monitor 流程就会开始监控。

### Q: GitHub Actions 没有按时运行

- GitHub Actions 的 cron 有 5-15 分钟的延迟，这是正常的
- 确认仓库近期有活动（60 天无活动的仓库会被暂停 cron）
- 可在 Actions 页面手动触发测试

---

## 二次开发指南

### 添加新的通知渠道

1. 在 `src/notifier.py` 中添加新的发送函数（如企业微信、Telegram）
2. 在 `config.yml` 的 `notification` 下添加配置开关
3. 在 `discover_main.py` 和 `monitor_main.py` 中调用

### 添加新的数据源

1. 在 `src/price.py` 的 `get_realtime_price()` 中添加新的降级方案
2. 或在 `src/announcement.py` 中添加其他公告来源

### 修改信号判定逻辑

- 要约收购：编辑 `src/strategy.py` 的 `evaluate_signals()` 函数
- 可转债：编辑 `src/cb_strategy.py` 的 `scan_cb_arbitrage()` 函数

所有阈值从 `config.yml` 读取，大多数调整只需改配置文件。

### 修改 AI 提取的字段

编辑 `src/extractor.py` 中的 `SYSTEM_PROMPT`，添加或修改需要提取的字段，同步更新 `validate_offer()` 的校验逻辑。

### 调整运行频率

编辑 `.github/workflows/` 下的 yml 文件中的 `cron` 表达式。注意 GitHub Actions 使用 UTC 时区，北京时间 = UTC + 8。

---

## 费用估算

| 项目 | 费用 |
|------|------|
| GitHub Actions | 免费（~280 分钟/月，额度 2000 分钟） |
| LLM API | ~0.01 元/次，每月 < 1 元 |
| 其他 | 全部免费 |

**每月总成本：不到 1 元。**
