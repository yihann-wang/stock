"""AI 提取模块 - DeepSeek API 调用 + JSON 解析 + 字段校验"""

import json
import logging
import re
from datetime import datetime

from openai import OpenAI

from .config import get_env, load_config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个金融文档解析助手。请从以下要约收购报告书中提取关键信息，
严格按照 JSON 格式输出，不要输出任何其他内容：

{
  "stock_code": "股票代码(6位数字)",
  "stock_name": "股票名称",
  "offer_price": 要约价格(数字,单位元),
  "offer_start": "要约起始日 YYYY-MM-DD",
  "offer_end": "要约截止日 YYYY-MM-DD",
  "type": "full 或 partial",
  "partial_pct": 收购比例上限(数字,全面要约填100),
  "condition": "none 或 min_accept",
  "min_accept_ratio": 最低接纳比例(数字,无条件填0),
  "acquirer": "收购方名称",
  "notes": "一句话总结要约背景"
}

如果某个字段无法确定，填 null。只输出 JSON，不要输出任何解释文字。"""


def extract_offer_info(pdf_text: str) -> dict | None:
    """调用 DeepSeek API 从 PDF 文本中提取要约信息"""
    cfg = load_config().get("extractor", {})
    api_key = get_env("LLM_API_KEY", required=False) or get_env("DEEPSEEK_API_KEY")
    model = get_env("LLM_MODEL", required=False) or cfg.get("model", "deepseek-chat")
    base_url = get_env("LLM_BASE_URL", required=False) or cfg.get("base_url", "https://api.deepseek.com")
    max_len = cfg.get("max_text_length", 8000)

    truncated_text = pdf_text[:max_len]

    client = OpenAI(api_key=api_key, base_url=base_url)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": truncated_text},
            ],
            temperature=0.1,
        )
        content = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"DeepSeek API 调用失败: {e}")
        return None

    # 尝试从返回内容中提取 JSON
    try:
        # 先尝试直接解析
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块中提取
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个 JSON 对象
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.error(f"无法解析 AI 返回的 JSON: {content[:200]}")
    return None


def validate_offer(offer: dict) -> tuple[bool, list[str]]:
    """
    校验提取结果的完整性和有效性。
    返回 (是否通过, 错误列表)。
    """
    errors = []

    if not offer:
        return False, ["AI 提取结果为空"]

    # 校验要约价格
    price = offer.get("offer_price")
    if price is None or (isinstance(price, (int, float)) and price <= 0):
        errors.append("要约价格缺失或无效")

    # 校验截止日期
    offer_end = offer.get("offer_end")
    if not offer_end:
        errors.append("截止日期缺失")
    else:
        try:
            end_date = datetime.strptime(offer_end, "%Y-%m-%d").date()
            if end_date < datetime.now().date():
                errors.append("截止日期已过期")
        except ValueError:
            errors.append("截止日期格式无效")

    # 校验股票代码
    code = offer.get("stock_code")
    if not code or not re.match(r"^\d{6}$", str(code)):
        errors.append("股票代码无效")

    # 校验要约类型
    offer_type = offer.get("type")
    if offer_type not in ("full", "partial", None):
        errors.append(f"要约类型无效: {offer_type}")

    return (len(errors) == 0, errors)
