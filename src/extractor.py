"""AI 提取模块 - DeepSeek API 调用 + JSON 解析 + 字段校验"""

import json
import logging
import re
from datetime import datetime

from openai import OpenAI

from .config import get_env, load_config

logger = logging.getLogger(__name__)

MERGER_SYSTEM_PROMPT = """你是金融文档解析助手。请从以下吸收合并相关公告中提取关键信息，
严格按照 JSON 格式输出，不要输出任何其他内容：

{
  "acquirer_code": "存续公司/合并方A股代码(6位数字)",
  "acquirer_name": "存续公司名称",
  "target_code": "被合并方/注销方A股代码(6位数字)",
  "target_name": "被合并方名称",
  "exchange_ratio": 换股比例(数字,1股被合并方换多少股存续公司,如1.05),
  "record_date": "股权登记日 YYYY-MM-DD",
  "expected_date": "预计实施/完成日期 YYYY-MM-DD",
  "cash_option": 是否有现金选择权(true或false),
  "cash_price": 现金选择权价格(数字,元,无填null),
  "notes": "一句话总结合并背景"
}

重要说明:
- 只处理A股上市公司之间的换股合并
- 若公告是对子公司/非上市公司的吸收合并(不涉及上市股票换股套利),请返回 {"not_applicable": true}
- 若某字段无法确定,填 null
- 只输出JSON,不要输出解释文字"""


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


def extract_merger_info(pdf_text: str) -> dict | None:
    """调用 LLM 从吸收合并公告中提取结构化信息"""
    cfg = load_config().get("extractor", {})
    api_key = get_env("LLM_API_KEY", required=False) or get_env("DEEPSEEK_API_KEY")
    model = get_env("LLM_MODEL", required=False) or cfg.get("model", "deepseek-chat")
    base_url = get_env("LLM_BASE_URL", required=False) or cfg.get("base_url", "https://api.deepseek.com")
    max_len = cfg.get("max_text_length", 20000)

    client = OpenAI(api_key=api_key, base_url=base_url)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": MERGER_SYSTEM_PROMPT},
                {"role": "user", "content": pdf_text[:max_len]},
            ],
            temperature=0.1,
        )
        content = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Merger AI 调用失败: {e}")
        return None

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.error(f"无法解析Merger AI返回: {content[:200]}")
    return None


def validate_merger(merger: dict) -> tuple[bool, list[str]]:
    """校验吸收合并提取结果"""
    errors = []

    if not merger:
        return False, ["AI 提取结果为空"]

    # 不适用（子公司合并等）
    if merger.get("not_applicable"):
        return False, ["非上市公司间吸收合并，无套利空间"]

    acquirer_code = merger.get("acquirer_code")
    if not acquirer_code or not re.match(r"^\d{6}$", str(acquirer_code)):
        errors.append("合并方代码无效")

    target_code = merger.get("target_code")
    if not target_code or not re.match(r"^\d{6}$", str(target_code)):
        errors.append("被合并方代码无效")

    ratio = merger.get("exchange_ratio")
    if ratio is None or not isinstance(ratio, (int, float)) or ratio <= 0:
        errors.append("换股比例缺失或无效")

    exp_date = merger.get("expected_date")
    if exp_date:
        try:
            d = datetime.strptime(exp_date, "%Y-%m-%d").date()
            if d < datetime.now().date():
                errors.append("预计实施日期已过")
        except ValueError:
            errors.append("预计实施日期格式无效")

    return (len(errors) == 0, errors)


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
