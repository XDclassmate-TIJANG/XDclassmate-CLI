"""配置管理模块：读写 JSON 配置文件。

配置查找顺序（避免"改了配置却没生效"的幽灵配置问题）：
    1. 项目根目录 configs/config.json       —— 首选
    2. core/configs/config.json              —— 兼容早期布局

两级都没找到时，使用首选路径并在首次写入时创建。实际使用的路径会写入
日志（INFO 级别），便于排查。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from .exceptions import ConfigFileError, XDclassmateCLIException
from .logger import get_logger

LOGGER = get_logger("config")

# 项目根目录（core 的上级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 用户级默认配置路径（未显式传入 path 时使用）
DEFAULT_CONFIG_PATH = os.path.join(
    os.path.expanduser("~"), "configs", "config.json"
)
# 项目级配置候选路径，按优先级排列
CONFIG_CANDIDATES = (
    PROJECT_ROOT / "configs" / "config.json",
    # PROJECT_ROOT / "core" / "configs" / "config.json",
)
# 首选（写入时使用）的配置路径
PROJECT_CONFIG_PATH = CONFIG_CANDIDATES[0]

# CLI 版本
CLI_VERSION = "1.0"

# 常用配置键名
CONFIG_KEY_PLUGIN_DIR = "plugin_dir"
CONFIG_KEY_LOG_LEVEL = "log_level"
CONFIG_KEY_LOG_FILE = "log_file"
CONFIG_KEY_CONSOLE_OUTPUT = "log_console_output"
CONFIG_KEY_STARTUP_MODE = "startup_mode"
CONFIG_KEY_LANGUAGE = "language"
CONFIG_KEY_INSTALL_URL = "install_url"
CONFIG_KEY_HELP_THEME = "help_theme"
# help 默认视图主题
DEFAULT_HELP_THEME = "list"
# 插件仓库（INSTALL_URL）缺省为空：需用户在 config.json 中显式填写
DEFAULT_INSTALL_URL = ""
# 终端启动模式取值
STARTUP_MODE_REPL = "repl"
STARTUP_MODE_HELP = "help"
STARTUP_MODES = (STARTUP_MODE_REPL, STARTUP_MODE_HELP)


def resolve_config_path() -> Path:
    """返回实际使用的项目配置文件路径（第一个存在的候选，否则首选）。"""
    for candidate in CONFIG_CANDIDATES:
        if candidate.is_file():
            if candidate != CONFIG_CANDIDATES[0]:
                LOGGER.warning(
                    "使用兼容路径的配置: %s（建议迁移到 %s）",
                    candidate, CONFIG_CANDIDATES[0]
                )
            return candidate
    LOGGER.info("未找到项目配置文件，将使用默认配置: %s", CONFIG_CANDIDATES[0])
    return CONFIG_CANDIDATES[0]


class ConfigManager:
    """配置管理器：负责加载与保存 JSON 配置文件。"""

    def __init__(self, path: Optional[str] = None):
        """
        :param path: 配置文件路径；缺省使用 resolve_config_path() 的结果
        """
        self.path = path or str(resolve_config_path())
        self.config = self.load_config_file()
        LOGGER.debug("已加载配置 %s（%s 个键）", self.path, len(self.config))

    def load_config_file(
            self,
            path: Optional[str] = None,
            default: Optional[dict] = None
            ) -> dict:
        """
        加载整个配置文件。

        :param path:    配置文件路径，缺省使用实例化时的路径
        :param default: 文件不存在时返回的默认值
        :return:        配置字典
        """
        if path is None:
            path = self.path
        if not os.path.exists(path):
            LOGGER.debug("配置文件不存在，使用默认值: %s", path)
            return {} if default is None else default
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        if not isinstance(config, dict):
            raise ConfigFileError(
                "配置文件根节点必须是 JSON 对象",
                key="error.unknown",
                params={"message": "配置文件根节点必须是 JSON 对象"},
                details={"path": str(path)}
            )
        return config

    def load_config(
            self,
            key: str,
            path: Optional[str] = None,
            default: Any = None
            ) -> Any:
        """
        读取配置项；文件缺失或损坏时返回默认值。

        :param key:     配置键名
        :param path:    配置文件路径
        :param default: 键不存在时的默认值
        """
        try:
            config = self.load_config_file(path, {})
            return config.get(key, default)
        except (FileNotFoundError, json.JSONDecodeError, ValueError,
                XDclassmateCLIException):
            # 单键读取保持宽松，避免配置问题阻断 CLI 启动
            LOGGER.debug("配置项 %s 读取失败，使用默认值 %r", key, default)
            return default

    def save_config(
            self,
            key: str,
            value: Any,
            path: Optional[str] = None,
            default: Optional[dict] = None
            ) -> None:
        """
        写入配置项（文件不存在时自动创建）。

        :param key:     配置键名
        :param value:   配置值
        :param path:    配置文件路径
        :param default: 新建文件时的初始内容
        """
        if path is None:
            path = self.path
        config = self.load_config_file(
            path, {} if default is None else default
        )
        config[key] = value
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=4)
        LOGGER.info("已写入配置 %s = %r（文件: %s）", key, value, path)
