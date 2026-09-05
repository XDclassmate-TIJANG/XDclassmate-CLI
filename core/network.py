"""官方信息接口：查询 CLI 与插件的最新版本。

设计约定
--------
1. **只用标准库**（urllib）。CLI 核心保持零第三方依赖是本项目的硬性
   约束，早期版本引入 `requests` 会直接破坏"零依赖安装即可用"；
2. **离线优先**。`official_url` 未配置时所有查询直接返回 None，不发起
   任何网络请求——既不拖慢启动，也不会产生用户意料之外的外联；
3. **无模块级副作用**。本模块只定义函数，导入它不会联网、不会读配置；
4. **失败即 None**。网络/解析异常只记日志，绝不向上抛，保证更新检查
   永远不会让 CLI 起不来。

接口约定（official_url 指向一个 JSON 文件）::

    {
      "version": "1.1.0",
      "download_url": "https://example.com/xdclassmate-cli-1.1.0.zip",
      "changelog_url": "https://example.com/CHANGELOG.md"
    }

插件信息按 `plugins/<插件名>.json` 拼在 official_url 同级目录下，
字段同上（version / download_url / changelog_url）。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import quote

from .config import CONFIG_KEY_OFFICIAL_URL, ConfigManager
from .logger import get_logger

LOGGER = get_logger("network")

# 网络请求超时（秒）：更新检查是"顺手为之"，不能拖慢启动
DEFAULT_TIMEOUT = 5.0
# 单次响应最大字节数，避免异常响应拖垮启动
MAX_RESPONSE_BYTES = 256 * 1024


def get_official_url(config_value: Optional[str] = None) -> str:
    """
    取得官方信息接口地址。

    :param config_value: 已读到的配置值，缺省按配置 official_url 读取
    :return:             地址字符串；未配置时为空串（表示离线）
    """
    if config_value is None:
        config_value = ConfigManager().load_config(
            CONFIG_KEY_OFFICIAL_URL, default=""
        )
    return str(config_value or "").strip().rstrip("/")


def fetch_json(url: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict]:
    """
    取回 URL 指向的 JSON 对象。

    :return: 解析成功返回 dict；URL 为空或任何异常返回 None
    """
    if not url:
        LOGGER.debug("未提供接口地址，跳过网络请求")
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES)
    except (urllib.error.URLError, OSError, ValueError) as error:
        LOGGER.debug("获取 %s 失败: %s", url, error)
        return None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError) as error:
        LOGGER.warning("接口 %s 返回的不是合法 JSON: %s", url, error)
        return None
    if not isinstance(data, dict):
        LOGGER.warning("接口 %s 返回的根节点不是 JSON 对象", url)
        return None
    return data


def get_last_cli_info(config_value: Optional[str] = None) -> Optional[dict]:
    """
    查询 CLI 最新版本信息。

    :return: {"version", "download_url", "changelog_url"}；
             未配置地址或请求失败返回 None
    """
    base = get_official_url(config_value)
    if not base:
        LOGGER.debug("未配置 official_url，跳过 CLI 更新检查")
        return None
    info = fetch_json(base)
    if info and info.get("version"):
        LOGGER.debug("远端 CLI 版本: %s", info["version"])
        return info
    if info is not None:
        LOGGER.warning("接口 %s 未返回 version 字段", base)
    return None


def get_last_plugin_info(
        name: str,
        config_value: Optional[str] = None
        ) -> Optional[dict]:
    """
    查询指定插件的最新版本信息。

    插件名会先用调用方给出的字面名去查，查不到时再试已加载插件登记的
    清单名——兼容"目录名 != 清单名"的常见情况（例如目录 image、
    清单名 "Image Processing"）。

    :return: {"version", "download_url", "changelog_url"}；失败返回 None
    """
    base = get_official_url(config_value)
    if not base:
        LOGGER.debug("未配置 official_url，跳过插件更新检查")
        return None
    if not name:
        LOGGER.debug("插件名为空，跳过插件更新检查")
        return None

    candidates = [name]
    # 延迟导入：plugins 模块较重，且只有确实要联网时才需要
    from .plugins import get_plugins

    try:
        meta = get_plugins().get(name)
        registered = str((meta or {}).get("name") or "")
        if registered and registered not in candidates:
            candidates.append(registered)
    except Exception as error:  # noqa: BLE001 —— 查不到不影响更新检查
        LOGGER.debug("读取已加载插件 %s 失败: %s", name, error)

    for candidate in candidates:
        url = f"{base}/plugins/{quote(str(candidate), safe='')}.json"
        info = fetch_json(url)
        if info and info.get("version"):
            return info
    LOGGER.debug("插件 %s 在官方接口中无更新信息", name)
    return None
