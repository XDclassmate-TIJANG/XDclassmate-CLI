"""配置管理模块：读写 JSON 配置文件。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from .exceptions import ConfigFileError, XDclassmateCLIException

# 用户级默认配置路径（未显式传入 path 时使用）
DEFAULT_CONFIG_PATH = os.path.join(
    os.path.expanduser("~"), "configs", "config.json"
)
# 项目内置配置：core/configs/config.json，存放插件目录与日志设置
PROJECT_CONFIG_PATH = (
    Path(__file__).resolve().parent / "configs" / "config.json"
)

# CLI 版本
CLI_VERSION = "1.0"


class ConfigManager:
    """配置管理器：负责加载与保存 JSON 配置文件。"""

    def __init__(self, path: Optional[str] = DEFAULT_CONFIG_PATH):
        self.path = path
        self.config = self.load_config_file()

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
            return {} if default is None else default
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        if not isinstance(config, dict):
            raise ConfigFileError(
                "配置文件根节点必须是 JSON 对象", details={"path": str(path)}
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
