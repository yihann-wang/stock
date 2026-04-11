"""公告搜索模块 - 巨潮资讯网 API 全文检索 + PDF 下载 + PDF 转文本"""

import logging
import time
from datetime import datetime, timedelta
from io import BytesIO

import pdfplumber
import requests

logger = logging.getLogger(__name__)

CNINFO_SEARCH_URL = "http://www.cninfo.com.cn/new/fulltextSearch/full"
CNINFO_PDF_BASE = "http://static.cninfo.com.cn"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "http://www.cninfo.com.cn/new/fulltextSearch",
}


def search_announcements(keyword: str = "要约收购报告书", days: int = 7) -> list[dict]:
    """
    搜索巨潮资讯网公告。

    返回列表，每个元素包含:
      - announcementId: 公告唯一 ID
      - secCode: 股票代码
      - secName: 股票名称
      - announcementTitle: 公告标题
      - announcementTime: 发布时间戳(ms)
      - adjunctUrl: PDF 相对路径
    """
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    results = []
    page = 1

    while True:
        params = {
            "searchkey": keyword,
            "sdate": start_date,
            "edate": end_date,
            "isfulltext": "false",
            "sortName": "pubdate",
            "sortType": "desc",
            "pageNum": page,
            "pageSize": 10,
        }
        try:
            resp = requests.post(
                CNINFO_SEARCH_URL, data=params, headers=HEADERS, timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"巨潮 API 请求失败: {e}")
            break

        announcements = data.get("announcements", [])
        if not announcements:
            break

        for ann in announcements:
            results.append({
                "announcementId": ann.get("announcementId", ""),
                "secCode": ann.get("secCode", ""),
                "secName": ann.get("secName", ""),
                "announcementTitle": ann.get("announcementTitle", ""),
                "announcementTime": ann.get("announcementTime", 0),
                "adjunctUrl": ann.get("adjunctUrl", ""),
            })

        # 如果返回数量小于 pageSize，说明已经是最后一页
        if len(announcements) < 10:
            break

        page += 1
        time.sleep(1)  # 避免请求过快

    logger.info(f"搜索到 {len(results)} 条公告")
    return results


def get_pdf_url(adjunct_url: str) -> str:
    """拼接完整的 PDF 下载链接"""
    if adjunct_url.startswith("http"):
        return adjunct_url
    return f"{CNINFO_PDF_BASE}/{adjunct_url}"


def get_announcement_date(timestamp_ms: int) -> str:
    """将毫秒时间戳转换为日期字符串"""
    if not timestamp_ms:
        return ""
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d")


def make_announcement_id(ann: dict) -> str:
    """生成公告唯一 ID"""
    sec_code = ann.get("secCode", "unknown")
    date = get_announcement_date(ann.get("announcementTime", 0))
    ann_id = ann.get("announcementId", "")
    return f"cninfo_{date}_{sec_code}_{ann_id}"


def download_and_extract_text(pdf_url: str, max_pages: int = 30) -> str:
    """
    下载公告 PDF 并转为文本。
    max_pages: 最多提取前 N 页，避免超大 PDF 耗时过长。
    """
    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"PDF 下载失败 {pdf_url}: {e}")
        return ""

    try:
        with pdfplumber.open(BytesIO(resp.content)) as pdf:
            pages = pdf.pages[:max_pages]
            texts = []
            for page in pages:
                text = page.extract_text()
                if text:
                    texts.append(text)
            return "\n".join(texts)
    except Exception as e:
        logger.error(f"PDF 解析失败: {e}")
        return ""
