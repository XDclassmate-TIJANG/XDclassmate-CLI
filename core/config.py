"""配置管理模块：读写 JSON 配置文件。

配置查找顺序（避免"改了配置却没生效"的幽灵配置问题）：
    1. 环境变量 XDCLI_CONFIG 指向的文件   —— 便携模式 / 临时覆盖 / 测试
    2. 项目根目录 configs/config.json     —— 首选，也是写入目标
    3. 用户级配置目录                     —— 全局安装（pip install）时使用

都没找到时，使用首选路径并在首次写入时创建。实际使用的路径会写入日志
（INFO 级别），便于排查。

为什么要有用户级配置：CLI 只认项目根目录下的 configs/config.json 时，
一旦离开仓库目录运行，配置就"消失"了，插件目录与语言设置全部回退默认值。
全局安装的命令行工具必须有一处与当前工作目录无关的配置位置。

兼容性：项目级配置存在时行为与旧版本完全一致（项目级优先），
已存在的部署不会因为升级而改变。
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
# 环境变量名：显式指定配置文件位置，优先级最高
ENV_CONFIG_PATH = "XDCLI_CONFIG"
# 项目级配置路径（首选，也是 save_config 的写入目标）
PROJECT_CONFIG_PATH = PROJECT_ROOT / "configs" / "config.json"
# 用户级配置目录名
USER_CONFIG_DIR_NAME = "xdclassmate"

# CLI 版本
CLI_VERSION = "1.0.0"

# 常用配置键名
CONFIG_KEY_PLUGIN_DIR = "plugin_dir"
CONFIG_KEY_LOG_LEVEL = "log_level"
CONFIG_KEY_LOG_FILE = "log_file"
CONFIG_KEY_CONSOLE_OUTPUT = "log_console_output"
CONFIG_KEY_STARTUP_MODE = "startup_mode"
CONFIG_KEY_LANGUAGE = "language"
CONFIG_KEY_INSTALL_URL = "install_url"
CONFIG_KEY_HELP_THEME = "help_theme"
CONFIG_KEY_OFFICIAL_URL = "official_url"
CONFIG_KEY_UPDATE_MESSAGE = "update_message"
CONFIG_KEY_AUTO_UPDATE = "auto_update"
# help 默认视图主题
DEFAULT_HELP_THEME = "list"
# 插件仓库（install_url）缺省为空：需用户在 config.json 中显式填写
DEFAULT_INSTALL_URL = ""
# 官方信息接口（official_url）缺省为空：未配置时不发起任何网络请求
DEFAULT_OFFICIAL_URL = ""
# 更新提示开关缺省值：开启，但 official_url 为空时不会联网
DEFAULT_UPDATE_MESSAGE = True
# 自动更新（auto_update）目前仅供外部脚本读取，CLI 自身不自动升级
DEFAULT_AUTO_UPDATE = False
# 终端启动模式取值
STARTUP_MODE_REPL = "repl"
STARTUP_MODE_HELP = "help"
STARTUP_MODES = (STARTUP_MODE_REPL, STARTUP_MODE_HELP)


def user_config_path() -> Path:
    """
    用户级配置文件路径（与当前工作目录无关）。

    Windows 用 %APPDATA%，其他平台遵循 XDG（$XDG_CONFIG_HOME 或 ~/.config）。
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = (
            os.environ.get("XDG_CONFIG_HOME")
            or os.path.expanduser("~/.config")
        )
    return Path(base).expanduser() / USER_CONFIG_DIR_NAME / "config.json"


# 兼容旧名称：早期版本定义过 DEFAULT_CONFIG_PATH（指向 ~/configs/），
# 但从未真正启用。保留导出，指向新的用户级路径，避免外部引用失效。
DEFAULT_CONFIG_PATH = str(user_config_path())


def resolve_config_path() -> Path:
    """
    返回实际使用的配置文件路径。

    查找顺序见模块 docstring：**环境变量 > 项目级 > 用户级**，
    都找不到时回退到用户级（首次写入时会自动创建在用户目录）。
    从仓库直接运行 `python -m core.main` 时，项目级配置优先，
    全局 ``pip install`` 之后命令也会找到用户级配置。
    """
    env_path = os.environ.get(ENV_CONFIG_PATH, "").strip()
    if env_path:
        return Path(env_path).expanduser()

    for candidate in (PROJECT_CONFIG_PATH, user_config_path()):
        if candidate.is_file():
            LOGGER.debug("使用配置文件: %s", candidate)
            return candidate

    # 都找不到：新写入的归宿是用户级配置（全局安装后用户的首选位置）。
    fallback = user_config_path()
    LOGGER.info("未找到配置文件，将使用用户级默认配置: %s", fallback)
    return fallback


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
                key="error.config_root",
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
