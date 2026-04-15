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
    add_merger,
    add_offer,
    get_known_announcement_ids,
    get_known_merger_ids,
    load_config,
    load_known_extra_announcements,
    save_known_extra_announcements,
)
from .extractor import (
    extract_merger_info,
    extract_offer_info,
    validate_merger,
    validate_offer,
)
from .merger_strategy import calculate_merger_arbitrage
from .notifier import (
    notify_new_merger_unvalidated,
    notify_new_merger_validated,
    notify_new_offer_unvalidated,
    notify_new_offer_validated,
    notify_announcement_found,
)
from .price import get_realtime_price, is_trading_day

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run():
    logger.info("=== 公告发现流程开始 ===")

    # 非交易日（节假日）直接退出
    if not is_trading_day():
        logger.info("今日非交易日，跳过公告发现")
        return

    cfg = load_config()
    ann_cfg = cfg.get("announcement", {})
    keyword = ann_cfg.get("keyword", "要约收购报告书")
    search_days = ann_cfg.get("search_days", 7)
    extra_keywords = ann_cfg.get("extra_keywords", [])

    # 1. 搜索主关键词公告
    logger.info(f"搜索关键词: {keyword}, 最近 {search_days} 天")
    announcements = search_announcements(keyword=keyword, days=search_days)

    new_announcements = []
    if announcements:
        known_ids = get_known_announcement_ids()
        for ann in announcements:
            ann_id = make_announcement_id(ann)
            if ann_id not in known_ids:
                new_announcements.append(ann)
        if not new_announcements:
            logger.info(f"共 {len(announcements)} 条要约公告，均已处理过")
    else:
        logger.info("未搜索到要约收购公告")

    # 无论主流程是否有新公告，都执行额外关键词扫描
    if not new_announcements:
        if extra_keywords:
            _scan_extra_announcements(extra_keywords, search_days)
        logger.info("=== 公告发现流程结束 ===")
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
    if extra_keywords:
        _scan_extra_announcements(extra_keywords, search_days)

    logger.info("=== 公告发现流程结束 ===")


def _scan_extra_announcements(keywords: list, search_days: int):
    """扫描额外关键词公告: 吸收合并走完整AI流程, 其他走简单推送"""
    known_extra_list = load_known_extra_announcements()
    known_extra_set = set(known_extra_list)
    known_merger_ids = get_known_merger_ids()
    new_extra_found = False

    for kw in keywords:
        is_merger = ("吸收合并" in kw) or ("换股合并" in kw)
        logger.info(f"扫描额外关键词: {kw} ({'AI套利分析' if is_merger else '简单推送'})")
        try:
            announcements = search_announcements(keyword=kw, days=search_days)
        except Exception as e:
            logger.warning(f"搜索 '{kw}' 失败: {e}")
            continue

        if not announcements:
            continue

        # 过滤已知
        dedup_set = known_merger_ids if is_merger else known_extra_set
        new_anns = []
        for ann in announcements:
            ann_id = make_announcement_id(ann)
            if ann_id not in dedup_set:
                new_anns.append((ann, ann_id))

        if not new_anns:
            logger.info(f"  '{kw}': 无新公告")
            continue

        logger.info(f"  '{kw}': 发现 {len(new_anns)} 条新公告")

        for ann, ann_id in new_anns:
            pdf_url = get_pdf_url(ann.get("adjunctUrl", ""))
            pub_date = get_announcement_date(ann.get("announcementTime", 0))

            if is_merger:
                # 走完整AI流程（下载+提取+校验+套利测算+保存）
                _process_merger_announcement(ann, ann_id, pdf_url, pub_date)
                known_merger_ids.add(ann_id)
            else:
                # 简单推送
                notify_announcement_found(
                    keyword=kw,
                    title=ann.get("announcementTitle", ""),
                    stock_name=ann.get("secName", ""),
                    stock_code=ann.get("secCode", ""),
                    pub_date=pub_date,
                    pdf_url=pdf_url,
                )
                known_extra_list.append(ann_id)
                known_extra_set.add(ann_id)
                new_extra_found = True

            time.sleep(2)

    if new_extra_found:
        save_known_extra_announcements(known_extra_list)
        logger.info(f"已保存 {min(len(known_extra_list), 500)} 条额外公告ID")


def _process_merger_announcement(ann: dict, ann_id: str, pdf_url: str, pub_date: str):
    """处理吸收合并公告: 下载PDF → AI提取 → 校验 → 套利测算 → 保存 → 推送"""
    ann_info = {
        "announcementTitle": ann.get("announcementTitle", ""),
        "pdf_url": pdf_url,
        "pub_date": pub_date,
    }

    logger.info(f"  下载PDF: {pdf_url}")
    pdf_text = download_and_extract_text(pdf_url)
    if not pdf_text:
        notify_new_merger_unvalidated(ann_info, None, ["PDF下载或解析失败"])
        return

    logger.info(f"  调用AI提取吸收合并信息...")
    merger_info = extract_merger_info(pdf_text)
    if not merger_info:
        notify_new_merger_unvalidated(ann_info, None, ["AI提取失败"])
        return

    # not_applicable = 非上市公司间合并，跳过
    if merger_info.get("not_applicable"):
        logger.info(f"  非上市公司间合并，跳过: {ann.get('announcementTitle', '')}")
        return

    valid, errors = validate_merger(merger_info)

    merger_record = {
        "announcement_id": ann_id,
        "acquirer_code": merger_info.get("acquirer_code", ""),
        "acquirer_name": merger_info.get("acquirer_name", ""),
        "target_code": merger_info.get("target_code", ""),
        "target_name": merger_info.get("target_name", ""),
        "exchange_ratio": merger_info.get("exchange_ratio"),
        "record_date": merger_info.get("record_date"),
        "expected_date": merger_info.get("expected_date"),
        "cash_option": merger_info.get("cash_option", False),
        "cash_price": merger_info.get("cash_price"),
        "notes": merger_info.get("notes", ""),
        "pdf_url": pdf_url,
        "discovered_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }

    if valid:
        logger.info(f"  AI校验通过，保存并推送")
        merger_record["ai_validated"] = True
        merger_record["status"] = "active"
        add_merger(merger_record)

        # 实时套利测算
        arb = None
        try:
            result = calculate_merger_arbitrage(merger_record)
            if result:
                arb = {
                    "target_code": result.target_code,
                    "target_price": result.target_price,
                    "acquirer_code": result.acquirer_code,
                    "acquirer_price": result.acquirer_price,
                    "exchange_ratio": result.exchange_ratio,
                    "theoretical_value": result.theoretical_value,
                    "spread": result.spread,
                    "spread_pct": result.spread_pct,
                    "annualized_pct": result.annualized_pct,
                    "days_left": result.days_left,
                }
        except Exception as e:
            logger.warning(f"  套利测算失败: {e}")

        notify_new_merger_validated(ann_info, merger_info, arb)
    else:
        logger.warning(f"  AI校验未通过: {errors}")
        merger_record["ai_validated"] = False
        merger_record["validation_errors"] = errors
        merger_record["status"] = "pending_review"
        add_merger(merger_record)
        notify_new_merger_unvalidated(ann_info, merger_info, errors)


if __name__ == "__main__":
    run()
