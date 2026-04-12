"""公告发现入口 - 搜索 → 下载 → AI 提取 → 校验 → 保存 → 通知"""

import logging
import sys
import time
from datetime import datetime

from .announcement import (
    download_and_extract_text,
    get_announcement_date,
    get_pdf_url,
    make_announcement_id,
    search_announcements,
)
from .config import (
    add_offer,
    get_known_announcement_ids,
    load_config,
    load_known_extra_announcements,
    save_known_extra_announcements,
)
from .extractor import extract_offer_info, validate_offer
from .notifier import (
    notify_new_offer_unvalidated,
    notify_new_offer_validated,
    notify_announcement_found,
)
from .price import get_realtime_price

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run():
    logger.info("=== 公告发现流程开始 ===")

    cfg = load_config()
    ann_cfg = cfg.get("announcement", {})
    keyword = ann_cfg.get("keyword", "要约收购报告书")
    search_days = ann_cfg.get("search_days", 7)

    # 1. 搜索公告
    logger.info(f"搜索关键词: {keyword}, 最近 {search_days} 天")
    announcements = search_announcements(keyword=keyword, days=search_days)

    if not announcements:
        logger.info("未搜索到公告，流程结束")
        return

    # 2. 过滤已知公告
    known_ids = get_known_announcement_ids()
    new_announcements = []
    for ann in announcements:
        ann_id = make_announcement_id(ann)
        if ann_id not in known_ids:
            new_announcements.append(ann)

    if not new_announcements:
        logger.info(f"共 {len(announcements)} 条公告，均已处理过，流程结束")
        return

    logger.info(f"发现 {len(new_announcements)} 条新公告")

    # 3. 逐条处理新公告
    for ann in new_announcements:
        ann_id = make_announcement_id(ann)
        pdf_url = get_pdf_url(ann.get("adjunctUrl", ""))
        pub_date = get_announcement_date(ann.get("announcementTime", 0))

        logger.info(
            f"处理公告: {ann.get('secName', '')} - "
            f"{ann.get('announcementTitle', '')}"
        )

        # 准备公告信息（给通知模块用）
        ann_info = {
            "announcementTitle": ann.get("announcementTitle", ""),
            "pdf_url": pdf_url,
            "pub_date": pub_date,
        }

        # 3a. 下载 PDF 并提取文本
        logger.info(f"下载 PDF: {pdf_url}")
        pdf_text = download_and_extract_text(pdf_url)
        if not pdf_text:
            logger.warning("PDF 提取文本失败，发送人工确认通知")
            notify_new_offer_unvalidated(ann_info, None, ["PDF 下载或解析失败"])
            continue

        # 3b. AI 提取要约信息
        logger.info("调用 AI 提取要约信息...")
        offer_info = extract_offer_info(pdf_text)
        if not offer_info:
            logger.warning("AI 提取失败，发送人工确认通知")
            notify_new_offer_unvalidated(ann_info, None, ["AI 提取失败"])
            continue

        # 用公告中的股票代码和名称补充
        if not offer_info.get("stock_code") and ann.get("secCode"):
            offer_info["stock_code"] = ann["secCode"]
        if not offer_info.get("stock_name") and ann.get("secName"):
            offer_info["stock_name"] = ann["secName"]

        # 3c. 校验
        valid, errors = validate_offer(offer_info)

        if valid:
            logger.info("AI 校验通过，保存要约记录")

            # 尝试获取实时套利测算
            arb_info = None
            try:
                price_data = get_realtime_price(offer_info["stock_code"])
                if price_data:
                    current_price = price_data["current_price"]
                    offer_price = offer_info["offer_price"]
                    days_left = (
                        datetime.strptime(offer_info["offer_end"], "%Y-%m-%d").date()
                        - datetime.now().date()
                    ).days
                    if days_left > 0 and current_price > 0:
                        spread = offer_price - current_price
                        spread_pct = spread / current_price * 100
                        annualized = spread_pct * (365 / days_left)
                        arb_info = {
                            "current_price": current_price,
                            "spread": spread,
                            "spread_pct": spread_pct,
                            "annualized_pct": annualized,
                            "days_left": days_left,
                            "daily_volume": price_data.get("daily_volume", 0),
                        }
            except Exception as e:
                logger.warning(f"实时套利测算失败: {e}")

            # 保存到 known_offers.json
            offer_record = {
                "announcement_id": ann_id,
                "stock_code": offer_info.get("stock_code", ""),
                "stock_name": offer_info.get("stock_name", ""),
                "offer_price": offer_info.get("offer_price"),
                "offer_start": offer_info.get("offer_start"),
                "offer_end": offer_info.get("offer_end"),
                "type": offer_info.get("type", "full"),
                "condition": offer_info.get("condition", "none"),
                "min_accept_ratio": offer_info.get("min_accept_ratio", 0),
                "partial_pct": offer_info.get("partial_pct", 100),
                "acquirer": offer_info.get("acquirer", ""),
                "notes": offer_info.get("notes", ""),
                "pdf_url": pdf_url,
                "discovered_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "ai_validated": True,
                "status": "active",
            }
            add_offer(offer_record)

            # 推送通知
            notify_new_offer_validated(ann_info, offer_info, arb_info)
        else:
            logger.warning(f"AI 校验未通过: {errors}")
            # 仍然保存记录但标记为未验证
            offer_record = {
                "announcement_id": ann_id,
                "stock_code": offer_info.get("stock_code", ann.get("secCode", "")),
                "stock_name": offer_info.get("stock_name", ann.get("secName", "")),
                "offer_price": offer_info.get("offer_price"),
                "offer_start": offer_info.get("offer_start"),
                "offer_end": offer_info.get("offer_end"),
                "type": offer_info.get("type"),
                "condition": offer_info.get("condition"),
                "min_accept_ratio": offer_info.get("min_accept_ratio"),
                "partial_pct": offer_info.get("partial_pct"),
                "acquirer": offer_info.get("acquirer"),
                "notes": offer_info.get("notes"),
                "pdf_url": pdf_url,
                "discovered_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "ai_validated": False,
                "validation_errors": errors,
                "status": "pending_review",
            }
            add_offer(offer_record)

            # 推送人工确认通知
            notify_new_offer_unvalidated(ann_info, offer_info, errors)

        # 公告之间间隔，避免请求过快
        time.sleep(2)

    # ===== 额外关键词扫描（下修、吸收合并等）=====
    extra_keywords = ann_cfg.get("extra_keywords", [])
    if extra_keywords:
        _scan_extra_announcements(extra_keywords, search_days)

    logger.info("=== 公告发现流程结束 ===")


def _scan_extra_announcements(keywords: list, search_days: int):
    """扫描额外关键词的公告（下修、吸收合并等），发现即推送，不做AI解析"""
    known_extra = load_known_extra_announcements()
    new_found = False

    for kw in keywords:
        logger.info(f"扫描额外关键词: {kw}")
        try:
            announcements = search_announcements(keyword=kw, days=search_days)
        except Exception as e:
            logger.warning(f"搜索 '{kw}' 失败: {e}")
            continue

        if not announcements:
            continue

        new_anns = []
        for ann in announcements:
            ann_id = make_announcement_id(ann)
            if ann_id not in known_extra:
                new_anns.append((ann, ann_id))
                known_extra.add(ann_id)
                new_found = True

        if not new_anns:
            logger.info(f"  '{kw}': 无新公告")
            continue

        logger.info(f"  '{kw}': 发现 {len(new_anns)} 条新公告")
        for ann, _ in new_anns:
            pdf_url = get_pdf_url(ann.get("adjunctUrl", ""))
            pub_date = get_announcement_date(ann.get("announcementTime", 0))
            notify_announcement_found(
                keyword=kw,
                title=ann.get("announcementTitle", ""),
                stock_name=ann.get("secName", ""),
                stock_code=ann.get("secCode", ""),
                pub_date=pub_date,
                pdf_url=pdf_url,
            )
            time.sleep(1)

    # 持久化已推送的ID，避免跨次运行重复推送
    if new_found:
        save_known_extra_announcements(known_extra)
        logger.info(f"已保存 {len(known_extra)} 条额外公告ID")


if __name__ == "__main__":
    run()
