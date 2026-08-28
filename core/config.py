import os
import json

from typing import Any, Optional

from .exceptions import ConfigFileError, XDclassmateCLIException

DEFAULT_CONFIG_PATH = os.path.join(os.path.expanduser("~"), "configs", "config.json")

# CLI相关配置
CLI_VERSION = "1.0"

class ConfigManager:
    """
    配置管理器类，用于加载和保存配置文件。
    """
    def __init__(self, path: Optional[str] = DEFAULT_CONFIG_PATH):
        self.path = path
        self.config = self.load_config_file()
    
    def load_config_file(self, path: Optional[str] = None, default: Optional[dict] = None) -> dict:
        """
        加载配置文件。
        如果配置文件不存在，则返回一个空字典。
        """
        if path is None:
            path = self.path
        if not os.path.exists(path):
            return {} if default is None else default
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
        if not isinstance(config, dict):
            raise ConfigFileError("配置文件根节点必须是 JSON 对象", details={"path": str(path)})
        return config

    def load_config(self, key: str, path: Optional[str] = None, default: Any = None) -> Any:
        """
        加载配置文件中的指定键的值。
        如果配置文件不存在或键不存在，则返回 None。
        """
        try:
            config = self.load_config_file(path, {})
            return config.get(key, default)
        except (FileNotFoundError, json.JSONDecodeError, ValueError, XDclassmateCLIException):
            # 单键读取保持宽松：文件缺失/损坏时回退到默认值，不阻断 CLI 启动
            return default

    def save_config(self, key: str, value: Any, path: Optional[str] = None, default: Optional[dict] = None) -> None:
        """
        保存配置文件中的指定键的值。
        如果配置文件不存在，则创建一个新的配置文件。
        """
        if path is None:
            path = self.path
        config = self.load_config_file(path, {} if default is None else default)
        config[key] = value
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)