import requests

from .logger import get_logger

# 官方接口
OFFICIAL_URL = "https://"

logger = get_logger("network")

def get_last_cli_info() -> str | None:
    try:
        resp = requests.get(OFFICIAL_URL, timeout=10)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        info = resp.json()
        return info
    except (requests.RequestException, Exception) as e:
        logger.error(f"获取最新 CLI 信息失败: {e}")
        return None