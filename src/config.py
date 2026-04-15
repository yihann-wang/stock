"""配置加载模块 - 读取 YAML 配置 + JSON 要约记录"""

import json
import os
from datetime import datetime
from pathlib import Path

import yaml

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.yml"
OFFERS_PATH = ROOT_DIR / "known_offers.json"
MERGERS_PATH = ROOT_DIR / "known_mergers.json"
EXTRA_ANNS_PATH = ROOT_DIR / "known_extra_announcements.json"

# 自动加载 .env 文件（本地开发用，GitHub Actions 用 Secrets）
_env_file = ROOT_DIR / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def load_config() -> dict:
    """加载 config.yml 配置"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_offers() -> dict:
    """加载已知要约记录"""
    if not OFFERS_PATH.exists():
        return {"offers": [], "last_search_time": None}
    with open(OFFERS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_offers(data: dict):
    """保存要约记录到 JSON"""
    data["last_search_time"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(OFFERS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_active_offers() -> list[dict]:
    """获取所有状态为 active 的要约"""
    data = load_offers()
    today = datetime.now().strftime("%Y-%m-%d")
    active = []
    for offer in data["offers"]:
        if offer.get("status") != "active":
            continue
        # 自动过期
        if offer.get("offer_end") and offer["offer_end"] < today:
            offer["status"] = "expired"
            continue
        active.append(offer)
    # 保存可能的状态变更
    save_offers(data)
    return active


def get_known_announcement_ids() -> set[str]:
    """获取所有已知公告 ID 集合"""
    data = load_offers()
    return {o["announcement_id"] for o in data["offers"] if "announcement_id" in o}


def load_mergers() -> dict:
    if not MERGERS_PATH.exists():
        return {"mergers": []}
    with open(MERGERS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_mergers(data: dict):
    data["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(MERGERS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_active_mergers() -> list[dict]:
    """活跃的吸收合并（未过实施日期）"""
    data = load_mergers()
    today = datetime.now().strftime("%Y-%m-%d")
    active = []
    for m in data.get("mergers", []):
        if m.get("status") != "active":
            continue
        exp = m.get("expected_date")
        if exp and exp < today:
            m["status"] = "expired"
            continue
        active.append(m)
    save_mergers(data)
    return active


def get_known_merger_ids() -> set[str]:
    data = load_mergers()
    return {m["announcement_id"] for m in data.get("mergers", []) if "announcement_id" in m}


def add_merger(merger: dict):
    data = load_mergers()
    if "mergers" not in data:
        data["mergers"] = []
    data["mergers"].append(merger)
    save_mergers(data)


def load_known_extra_announcements() -> list[str]:
    """加载已推送过的额外公告ID列表（按插入顺序，下修/吸收合并等）"""
    if not EXTRA_ANNS_PATH.exists():
        return []
    try:
        with open(EXTRA_ANNS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("ids", []))
    except Exception:
        return []


def save_known_extra_announcements(ids: list[str]):
    """保存已推送过的额外公告ID列表，保留最近500条（末尾500个）"""
    trimmed = ids[-500:] if len(ids) > 500 else ids
    with open(EXTRA_ANNS_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"ids": trimmed, "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")},
            f, ensure_ascii=False, indent=2,
        )


def add_offer(offer: dict):
    """添加新的要约记录"""
    data = load_offers()
    data["offers"].append(offer)
    save_offers(data)


# 环境变量读取
def get_env(key: str, required: bool = True) -> str:
    """从环境变量获取配置"""
    val = os.environ.get(key, "")
    if required and not val:
        raise EnvironmentError(f"环境变量 {key} 未设置")
    return val
