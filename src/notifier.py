"""DingTalk transport and the combined weekly report renderer."""

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

from .http_client import DirectHttpClient
from .models import WeeklyScanReport
from .settings import AppSettings, DingTalkSettings, get_required_env


logger = logging.getLogger(__name__)

REASON_LABELS = {
    "expired": "已到期",
    "price_not_below_limit": "价格不低于100元",
    "maturity_too_far": "到期超过一年半",
    "missing_bond_price": "转债价格缺失",
    "missing_expire_date": "到期日缺失",
    "sector_gain_too_high": "板块四周涨幅超过5%",
    "insufficient_excess": "正股四周超额不足8个百分点",
    "not_stronger_every_week": "未做到四周都强于板块",
    "missing_sector_mapping": "申万行业映射缺失",
    "missing_sector_history": "行业历史行情缺失",
    "missing_stock_history": "正股历史行情缺失",
    "insufficient_history": "共同历史不足五个交易周",
    "stale_history": "最新共同行情已过期",
    "internal_candidate_error": "候选组装异常",
    "strategy_disabled": "策略已禁用",
}


class NotificationError(RuntimeError):
    pass


def _format_pct(value: float) -> str:
    return f"{value:+.2f}%"


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "无"
    return "；".join(
        f"{REASON_LABELS.get(reason, reason)} {count}"
        for reason, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )
    )


def render_weekly_report(
    report: WeeklyScanReport, settings: AppSettings
) -> tuple[str, str]:
    title = f"每周双策略筛选 | {report.as_of:%Y-%m-%d}"
    low = report.low_price
    midterm = report.midterm
    lines = [
        f"### {title}",
        "",
        f"> 数据截至 **{report.as_of:%Y-%m-%d}**，耗时 {report.elapsed_seconds:.1f} 秒",
        "",
        "#### 策略一：低价临期转债",
        "",
        (
            f"> 条件：转债现价严格低于 {settings.low_price.max_bond_price:g} 元，"
            f"剩余期限大于 0 且不超过 {settings.low_price.max_years_to_expire:g} 年。"
        ),
        "",
        f"**候选 {len(low.candidates)} 只 / 扫描 {low.total_bonds} 只**",
        "",
    ]
    if low.candidates:
        for candidate in low.candidates[: settings.low_price.max_results]:
            bond = candidate.bond
            lines.append(
                f"- **{bond.bond_name}**（{bond.bond_code}） | "
                f"{bond.bond_price:.2f} 元 | 剩余 {candidate.days_to_expire} 天 | "
                f"{bond.expire_date:%Y-%m-%d} 到期 | 正股 {bond.stock_name}（{bond.stock_code}）"
            )
        if len(low.candidates) > settings.low_price.max_results:
            lines.append(
                f"- 另有 {len(low.candidates) - settings.low_price.max_results} 只，"
                "受消息长度限制未展开"
            )
    else:
        lines.append("- 本周无符合条件的转债")

    lines.extend(
        [
            "",
            f"> 未入选：{_format_counts(low.rejected_reasons)}",
            f"> 数据不可用：{_format_counts(low.unavailable_reasons)}",
            "",
            "---",
            "",
            "#### 策略二：正股中线相对强势",
            "",
            (
                f"> 条件：最近 {settings.midterm.weeks} 个完整交易周，申万一级行业累计涨幅"
                f"不超过 {settings.midterm.max_sector_gain_pct:g}%，正股累计至少跑赢行业 "
                f"{settings.midterm.min_excess_return_pct:g} 个百分点，且每周都不弱于行业。"
                "相关系数只展示，不参与筛选。"
            ),
            "",
            (
                f"**候选 {len(midterm.candidates)} 只 / 可计算 {midterm.evaluated_stocks} 只 / "
                f"正股总数 {midterm.total_stocks} 只 / 覆盖率 {midterm.coverage_ratio:.1%}**"
            ),
            "",
        ]
    )

    if midterm.candidates:
        for candidate in midterm.candidates[: settings.midterm.max_results]:
            metrics = candidate.metrics
            correlation = (
                f"{metrics.correlation:.3f}"
                if metrics.correlation is not None
                else "不可计算"
            )
            lines.extend(
                [
                    f"**{candidate.stock_name}（{candidate.stock_code}）** | "
                    f"申万一级：{candidate.sector.name}",
                    "",
                    f"- 四周：正股 {_format_pct(metrics.stock_return_pct)} | "
                    f"行业 {_format_pct(metrics.sector_return_pct)} | "
                    f"超额 {_format_pct(metrics.excess_return_pct)} | 日收益相关系数 {correlation}",
                    f"- 关联转债：{candidate.bond_name}（{candidate.bond_code}）；"
                    + "；".join(
                        f"第{index}周 正股{_format_pct(week.stock_return_pct)} / "
                        f"行业{_format_pct(week.sector_return_pct)} / "
                        f"超额{_format_pct(week.excess_return_pct)}"
                        for index, week in enumerate(metrics.weeks, start=1)
                    ),
                    "",
                ]
            )
        if len(midterm.candidates) > settings.midterm.max_results:
            lines.append(
                f"另有 {len(midterm.candidates) - settings.midterm.max_results} 只，"
                "受消息长度限制未展开"
            )
    else:
        lines.append("- 本周无符合条件的正股")

    lines.extend(
        [
            "",
            f"> 未入选：{_format_counts(midterm.rejected_reasons)}",
            f"> 数据不可用：{_format_counts(midterm.unavailable_reasons)}",
        ]
    )
    if midterm.coverage_ratio < settings.midterm.min_coverage_ratio:
        lines.extend(
            [
                "",
                (
                    f"> **数据质量告警：覆盖率低于 "
                    f"{settings.midterm.min_coverage_ratio:.0%}，本次 Action 将标记失败。**"
                ),
            ]
        )
    return title, "\n".join(lines)


def _signed_url(webhook: str, secret: str, timestamp_ms: int | None = None) -> str:
    timestamp = str(timestamp_ms or round(time.time() * 1000))
    message = f"{timestamp}\n{secret}".encode()
    signature = base64.b64encode(
        hmac.new(secret.encode(), message, digestmod=hashlib.sha256).digest()
    ).decode()
    separator = "&" if "?" in webhook else "?"
    return (
        f"{webhook}{separator}timestamp={timestamp}"
        f"&sign={urllib.parse.quote_plus(signature)}"
    )


class DingTalkNotifier:
    def __init__(
        self,
        client: DirectHttpClient,
        settings: DingTalkSettings,
    ):
        self.client = client
        self.settings = settings

    def send(self, title: str, markdown: str) -> None:
        if not self.settings.enabled:
            logger.info("DingTalk notification is disabled")
            return
        webhook = get_required_env(self.settings.webhook_env)
        secret = get_required_env(self.settings.secret_env)
        result = self.client.post_json(
            _signed_url(webhook, secret),
            {
                "msgtype": "markdown",
                "markdown": {"title": title, "text": markdown},
            },
        )
        if result.get("errcode") != 0:
            raise NotificationError(f"DingTalk rejected message: {result}")
        logger.info("DingTalk report sent: %s", title)
