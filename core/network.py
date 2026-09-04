import requests

from typing import Optional
from .logger import get_logger
from .plugins import Plugins

# 官方接口
OFFICIAL_URL = "https://"           # TODO

logger = get_logger("network")

def get_last_cli_info() -> Optional[str]:
    try:
        resp = requests.get(OFFICIAL_URL, timeout=10)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        info = resp.json()
        return info
    except (requests.RequestException, Exception) as e:
        logger.error(f"获取最新 CLI 信息失败: {e}")
        return None

def get_last_plugin_info(name: str) -> Optional[str]:
    try:
        # 获取的插件信息中会有"url"这个值
        # 但是目前我还没做e
        plugin_info = Plugins.get(name=name)
        logger.info(f"插件 {name} 的信息为 {plugin_info}")
        if plugin_info is None:
            return None
        resp = requests.get()       # TODO
        resp.raise_for_status()
        resp.encoding = "utf-8"
        info = resp.json()
        return info
    except (requests.RequestException, Exception) as e:
        logger.error(f"获取插件 {name} 最新信息失败: {e}")
        return None