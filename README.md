# 每周双策略筛选

仓库只保留两个彼此独立的筛选逻辑，每周运行一次，并把候选、计算细节和数据覆盖率合并推送到钉钉。

## 策略一：低价临期转债

同时满足：

1. 转债实时价格严格低于 100 元；100 元整不入选。
2. 距到期日大于 0 天且不超过 1.5 年，当前按 `int(1.5 * 365) = 547` 天计算。

该策略不检查转股价值、溢价率、成交额、强赎状态或行业。

## 策略二：正股中线相对强势

股票池是全部在市可转债对应的正股，与转债价格和到期时间完全独立。每只正股与其申万一级行业比较，使用最近四个已经结束且有交易的周：

1. 行业四周累计涨幅不超过 5%。
2. 正股四周累计涨幅至少跑赢行业 8 个百分点。
3. 四个单周中，正股每周都不弱于行业。

周收益使用相邻周最后一个共同交易日的收盘价计算，因此四周需要五个周收盘点。Pearson 相关系数基于对齐后的日收益率计算，只作为“跟随行业还是走独立行情”的观察值展示，不参与入选判断。

## 数据与结构

- 转债列表和实时价格：东方财富数据中心。
- 正股前复权日线：腾讯行情。
- 申万一级行业映射：缓存基线加东方财富行业字段增量推断。
- 申万一级指数日线：申万宏源研究官网为主源，乐咕乐股为备用源。
- `src/bond_provider.py`、`src/market_provider.py`：外部数据边界。
- `src/low_price_strategy.py`、`src/midterm_strategy.py`：无网络依赖的纯计算。
- `src/scanner.py`：并发扫描、覆盖率和拒绝原因统计。
- `src/notifier.py`：统一报告与钉钉传输。
- `src/monitor_main.py`：命令入口。

数据抓取失败会记为“数据不可用”，不会伪装成“策略未入选”。中线可计算覆盖率低于 `config.yml` 的 `min_coverage_ratio` 时，报告仍会先发送，但任务最终失败，以便 GitHub Actions 明确告警。

## 本地运行

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m src.monitor_main --dry-run
```

正式推送需要环境变量：

```bash
export DINGTALK_WEBHOOK='https://oapi.dingtalk.com/robot/send?access_token=...'
export DINGTALK_SECRET='SEC...'
python -m src.monitor_main
```

如有新转债正股未出现在缓存映射中，运行时会自动补查。需要更新仓库里的映射文件时执行：

```bash
python -m scripts.refresh_sector_map
```

## GitHub Actions

- `CI`：每次推送和 Pull Request 运行全部单元测试。
- `Weekly Convertible Bond and Stock Scan`：每周二北京时间 09:40（UTC 01:40）执行测试、真实筛选和钉钉推送，也支持手动触发。

仓库 Secrets 必须配置 `DINGTALK_WEBHOOK` 和 `DINGTALK_SECRET`。钉钉返回非零错误码时任务直接失败。
