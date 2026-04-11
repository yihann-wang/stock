"""钉钉通知模块 - 签名鉴权 + 四种 Markdown 消息模板"""

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

import requests

from .config import get_env, load_config

logger = logging.getLogger(__name__)


def _sign_url(webhook: str, secret: str) -> str:
    """生成带签名的钉钉 Webhook URL"""
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return f"{webhook}&timestamp={timestamp}&sign={sign}"


def send_dingtalk(title: str, markdown_text: str) -> bool:
    """发送钉钉 Markdown 消息"""
    cfg = load_config().get("notification", {}).get("dingtalk", {})
    if not cfg.get("enabled", True):
        logger.info("钉钉通知已禁用")
        return False

    webhook = get_env("DINGTALK_WEBHOOK")
    secret = get_env("DINGTALK_SECRET")
    url = _sign_url(webhook, secret)

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": markdown_text,
        },
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        result = resp.json()
        if result.get("errcode") == 0:
            logger.info(f"钉钉消息发送成功: {title}")
            return True
        else:
            logger.error(f"钉钉消息发送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"钉钉消息发送异常: {e}")
        return False


# ==================== 消息模板 ====================


def notify_new_offer_validated(ann: dict, offer: dict, arb: dict | None):
    """新公告发现 - AI 校验通过"""
    offer_type_map = {"full": "全面要约", "partial": "部分要约"}
    condition_map = {"none": "无条件", "min_accept": "有条件"}

    offer_type_str = offer_type_map.get(offer.get("type", ""), offer.get("type", ""))
    condition_str = condition_map.get(offer.get("condition", ""), offer.get("condition", ""))

    # 套利测算部分
    arb_section = ""
    if arb:
        arb_section = f"""
**实时套利测算**

- 当前股价: {arb['current_price']:.2f} 元
- 价差: +{arb['spread']:.2f} 元 ({arb['spread_pct']:.1f}%)
- 年化收益: {arb['annualized_pct']:.1f}% (剩余{arb['days_left']}天)
- 日均成交额: {arb['daily_volume']:,.0f} 万元
"""

    text = f"""### 【新要约收购公告发现】

---

**公告信息**

- 股票: {offer.get('stock_code', '')} {offer.get('stock_name', '')}
- 公告: {ann.get('announcementTitle', '')}
- 发布日期: {ann.get('pub_date', '')}
- 公告原文: [点击查看PDF]({ann.get('pdf_url', '')})

**AI 提取分析**

- 要约价格: {offer.get('offer_price', 'N/A')} 元
- 要约期限: {offer.get('offer_start', 'N/A')} ~ {offer.get('offer_end', 'N/A')}
- 要约类型: {offer_type_str} | {condition_str}
- 收购方: {offer.get('acquirer', 'N/A')}
- 背景: {offer.get('notes', 'N/A')}
{arb_section}
> AI 置信度: **高** - 所有字段校验通过，已自动加入监控

---"""

    send_dingtalk("新要约收购公告发现", text)


def notify_new_offer_unvalidated(ann: dict, offer: dict | None, errors: list[str]):
    """新公告发现 - AI 校验未通过"""
    offer_info = ""
    if offer:
        offer_info = f"""
**AI 提取结果**

- 要约价格: {offer.get('offer_price', '[未能提取]')} 元
- 截止日期: {offer.get('offer_end', '[未能提取]')}
- 要约类型: {offer.get('type', '[未能提取]')}
"""

    error_str = "、".join(errors) if errors else "AI 解析失败"

    text = f"""### 【新要约收购公告 - 需人工确认】

---

- 公告: {ann.get('announcementTitle', '')}
- 公告原文: [点击查看PDF]({ann.get('pdf_url', '')})
{offer_info}
> 缺失/异常字段: {error_str}
> 请查看 PDF 原文确认

---"""

    send_dingtalk("新要约收购公告 - 需人工确认", text)


def notify_spread_signal(result):
    """日常套利信号"""
    offer_type_map = {"full": "全面要约", "partial": "部分要约"}
    condition_map = {"none": "无条件", "min_accept": "有条件"}

    offer_type_str = offer_type_map.get(result.offer_type, result.offer_type)
    condition_str = condition_map.get(result.condition, result.condition)

    partial_info = ""
    if result.offer_type == "partial" and result.adjusted_spread_pct is not None:
        partial_info = (
            f"\n- 调整后价差(按{result.partial_pct:.0f}%接纳): "
            f"{result.adjusted_spread_pct:.1f}%"
            f"\n- 调整后年化: {result.adjusted_annualized_pct:.1f}%"
        )

    text = f"""### 【要约收购套利信号】

---

- 股票: {result.stock_code} {result.stock_name}
- 现价: {result.current_price:.2f} | 要约价: {result.offer_price:.2f}
- 价差: +{result.spread:.2f} 元 ({result.spread_pct:.1f}%)
- 年化: {result.annualized_pct:.1f}%
- 类型: {offer_type_str} | {condition_str}
- 截止: {result.offer_end} (剩余{result.days_left}天){partial_info}

---"""

    send_dingtalk("要约收购套利信号", text)


def notify_deadline_warning(result):
    """截止日提醒"""
    text = f"""### 【要约即将截止提醒】

---

- 股票: {result.stock_code} {result.stock_name}
- 要约价: {result.offer_price:.2f} | 现价: {result.current_price:.2f}
- 剩余: **{result.days_left} 天** ({result.offer_end} 截止)
- 当前价差: {result.spread_pct:.1f}% | 年化: {result.annualized_pct:.1f}%
- **请尽快决策是否参与!**

---"""

    send_dingtalk("要约即将截止提醒", text)


def notify_negative_spread(result):
    """负价差警告"""
    text = f"""### 【负价差警告】

---

- 股票: {result.stock_code} {result.stock_name}
- 现价: {result.current_price:.2f} > 要约价: {result.offer_price:.2f}
- 价差: {result.spread:.2f} 元 ({result.spread_pct:.1f}%)
- 截止: {result.offer_end} (剩余{result.days_left}天)
- **当前买入无套利空间**

---"""

    send_dingtalk("负价差警告", text)


def notify_cb_arbitrage(results: list):
    """可转债转股套利信号 - 合并推送所有负溢价机会"""
    if not results:
        return

    rows = []
    for r in results:
        rows.append(
            f"- **{r.bond_name}**({r.bond_code}) | "
            f"溢价率 **{r.premium_rate:.2f}%** | "
            f"转债价 {r.bond_price:.2f} → 转股价值 {r.convert_value:.2f} | "
            f"每10张赚 {r.profit_per_ten:.2f}元 | "
            f"成交额 {r.volume:.0f}万"
        )
    rows_text = "\n".join(rows)

    text = f"""### 【可转债转股套利信号】

---

> 发现 **{len(results)}** 只负溢价可转债

{rows_text}

> 操作: 买入转债 → 当日转股 → 次日卖出正股
> 风险: 次日正股开盘价下跌(隔夜风险)

---"""

    send_dingtalk("可转债转股套利信号", text)


def notify_cb_no_opportunity(total: int, neg_list: list):
    """可转债扫描完成但无达标机会"""
    neg_info = ""
    if neg_list:
        rows = []
        for d in sorted(neg_list, key=lambda x: x.get("premium_rate", 0)):
            reasons = []
            if d.get("volume", 0) < 1000:
                reasons.append(f"成交额{d.get('volume', 0):.0f}万不足")
            bp = d.get("bond_price", 0)
            if bp < 90:
                reasons.append(f"价格{bp:.1f}<90")
            if bp > 200:
                reasons.append(f"价格{bp:.1f}>200")
            reason_str = "、".join(reasons) if reasons else "溢价率未达阈值"
            rows.append(
                f"- {d.get('bond_name', '')}({d.get('bond_code', '')}) "
                f"溢价率 {d.get('premium_rate', 0):.2f}% → 未通过: {reason_str}"
            )
        neg_info = "\n\n**负溢价转债(未达标):**\n\n" + "\n".join(rows)

    text = f"""### 【可转债扫描报告】

---

> 扫描 **{total}** 只可转债，当前无达标套利机会
{neg_info}

> 达标条件: 溢价率<-0.5% 且 成交额>1000万 且 价格90~200元

---"""

    send_dingtalk("可转债扫描报告", text)


def notify_cb_redemption_alert(results: list):
    """可转债强赎预警 - 正股接近或超过转股价130%"""
    if not results:
        return

    rows = []
    for r in results:
        status = "**已触发**" if r.ratio >= 130 else "接近触发"
        rows.append(
            f"- **{r.bond_name}**({r.bond_code}) | "
            f"正股/转股价 **{r.ratio:.1f}%** {status} | "
            f"转债价 {r.bond_price:.2f} | 转股价值 {r.convert_value:.2f}"
        )
    rows_text = "\n".join(rows)

    text = f"""### 【可转债强赎预警】

---

> **{len(results)}** 只可转债正股接近/超过强赎触发线(130%)

{rows_text}

> 强赎触发: 正股连续15-20天 > 转股价×130%
> 触发后转债将被强制赎回(约100元)，高价转债面临大幅回调风险

---"""

    send_dingtalk("可转债强赎预警", text)


def notify_announcement_found(keyword: str, title: str, stock_name: str,
                               stock_code: str, pub_date: str, pdf_url: str):
    """通用公告发现通知（下修、吸收合并等）"""
    tag_map = {
        "转股价格向下修正": "转股价下修",
        "下修": "转股价下修",
        "吸收合并": "吸收合并",
        "换股合并": "换股合并",
    }
    tag = "公告"
    for k, v in tag_map.items():
        if k in keyword:
            tag = v
            break

    text = f"""### 【{tag}公告发现】

---

- 股票: {stock_code} {stock_name}
- 公告: {title}
- 发布日期: {pub_date}
- 公告原文: [点击查看PDF]({pdf_url})

> 关键词: {keyword}

---"""

    send_dingtalk(f"{tag}公告发现", text)


def notify_ah_premium(results: list):
    """AH股溢价率极端偏离通知"""
    if not results:
        return

    discount = [r for r in results if r.premium_rate < 0]
    premium = [r for r in results if r.premium_rate >= 100]

    sections = []
    if discount:
        rows = "\n".join(
            f"- **{r.stock_name}**({r.stock_code}) | "
            f"溢价率 **{r.premium_rate:.1f}%** | A股 {r.a_price:.2f} | H股 {r.h_price:.2f}港元"
            for r in discount
        )
        sections.append(f"**A股折价(罕见):**\n\n{rows}")

    if premium:
        rows = "\n".join(
            f"- **{r.stock_name}**({r.stock_code}) | "
            f"溢价率 **{r.premium_rate:.1f}%** | A股 {r.a_price:.2f} | H股 {r.h_price:.2f}港元"
            for r in premium
        )
        sections.append(f"**A股高溢价(>200%):**\n\n{rows}")

    text = f"""### 【AH股溢价异常】

---

> 发现 **{len(results)}** 只AH股溢价率极端偏离

{chr(10).join(sections)}

> A股折价 → 买A卖H机会 | A股高溢价 → 回归风险

---"""

    send_dingtalk("AH股溢价异常", text)
