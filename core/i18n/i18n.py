"""国际化模块：语言包加载、占位符替换与语言探测。

目录结构：
    core/i18n/languages/<语言代码>.json    语言包（UTF-8，扁平键值）

语言探测顺序（由 core.kernel 调用 detect_language 决定）：
    显式参数 > 环境变量 XDCLI_LANG > 配置项 language > 系统区域 > zh_CN

约定：
    * 缺少语言键时返回键名本身，并记录 DEBUG 日志，不抛异常；
    * 占位符使用 str.format 语法，缺失的参数渲染为空串而非报错；
    * 日志面向维护者，保持单一语言；i18n 只翻译面向用户的输出。
"""
from __future__ import annotations

import json
import locale
import os
from pathlib import Path
from typing import Any, Optional

from ..logger import get_logger

LOGGER = get_logger("i18n")

# 语言包目录与默认语言
LANGUAGE_DIR = Path(__file__).resolve().parent / "languages"
DEFAULT_LANGUAGE = "zh_CN"
FALLBACK_LANGUAGE = "zh_CN"
# 语言环境变量名
ENV_LANGUAGE = "XDCLI_LANG"


class _SafeDict(dict):
    """格式化时的安全字典：缺少的键渲染为空串，避免抛 KeyError。"""

    def __missing__(self, key: str) -> str:
        LOGGER.debug("语言文本缺少占位符参数: %s", key)
        return ""


class I18n:
    """语言包容器：负责加载语言、切换语言与翻译文本。"""

    def __init__(
            self,
            language: Optional[str] = None,
            directory: Path | str = LANGUAGE_DIR,
            fallback: str = FALLBACK_LANGUAGE
            ):
        """
        :param language:  初始语言代码，缺省使用 DEFAULT_LANGUAGE
        :param directory: 语言包目录
        :param fallback:  回退语言代码（键缺失时再查一次）
        """
        self.directory = Path(directory)
        self.fallback = fallback
        self._packs: dict[str, dict[str, str]] = {}
        self.language = DEFAULT_LANGUAGE
        self.set_language(language or DEFAULT_LANGUAGE)

    @property
    def available_languages(self) -> list[str]:
        """返回目录中可用的语言代码列表。"""
        if not self.directory.is_dir():
            LOGGER.warning("语言包目录不存在: %s", self.directory)
            return []
        return sorted(path.stem for path in self.directory.glob("*.json"))

    def load_language(self, code: str) -> dict[str, str]:
        """加载并缓存语言包；文件缺失或损坏时返回空字典。"""
        if code in self._packs:
            return self._packs[code]
        path = self.directory / f"{code}.json"
        if not path.is_file():
            LOGGER.warning("语言包不存在: %s", path)
            self._packs[code] = {}
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            LOGGER.error("语言包解析失败 %s: %s", path, error)
            self._packs[code] = {}
            return {}
        if not isinstance(data, dict):
            LOGGER.error("语言包根节点必须是 JSON 对象: %s", path)
            self._packs[code] = {}
            return {}
        pack = {str(key): str(value) for key, value in data.items()}
        LOGGER.debug("已加载语言包 %s（%s 条文本）", code, len(pack))
        self._packs[code] = pack
        return pack

    def set_language(self, code: str) -> bool:
        """
        切换当前语言。

        :param code: 语言代码，如 zh_CN / en_US
        :return:     语言包存在（可加载）返回 True，否则 False 并保持原语言
        """
        pack = self.load_language(code)
        if not pack and code != self.fallback:
            LOGGER.warning("语言 %s 不可用，回退到 %s", code, self.fallback)
            return False
        self.language = code
        LOGGER.info("当前语言: %s", code)
        return True

    def has(self, key: str) -> bool:
        """判断当前语言或回退语言中是否存在指定键。"""
        return (
            key in self._packs.get(self.language, {})
            or key in self._packs.get(self.fallback, {})
        )

    def t(self, key: str, **params: Any) -> str:
        """
        翻译文本并按参数替换占位符。

        :param key:     语言键，如 "cmd.help.title"
        :param params:  占位符参数
        :return:        翻译后的文本；键缺失时返回键名本身
        """
        text = self._packs.get(self.language, {}).get(key)
        if text is None and self.language != self.fallback:
            text = self._packs.get(self.fallback, {}).get(key)
        if text is None:
            LOGGER.debug("缺少语言键: %s（语言 %s）", key, self.language)
            return key
        if not params:
            return text
        try:
            return text.format_map(_SafeDict(params))
        except (IndexError, ValueError) as error:
            LOGGER.warning("语言文本格式化失败 key=%s: %s", key, error)
            return text


def detect_system_language() -> str:
    """根据操作系统区域设置猜测语言代码，失败时返回默认语言。"""
    try:
        current = locale.getlocale()[0] or ""
    except (ValueError, IndexError, AttributeError):
        current = ""
    normalized = current.lower()
    if normalized.startswith("zh"):
        return "zh_CN"
    if normalized.startswith("en"):
        return "en_US"
    return DEFAULT_LANGUAGE


def detect_language(config_value: Optional[str] = None) -> str:
    """
    按优先级探测语言：环境变量 > 配置项 > 系统区域 > 默认语言。

    :param config_value: 配置文件中的 language 值
    :return:             语言代码
    """
    env_value = os.environ.get(ENV_LANGUAGE, "").strip()
    if env_value:
        LOGGER.debug("语言来自环境变量 %s=%s", ENV_LANGUAGE, env_value)
        return env_value
    if config_value:
        LOGGER.debug("语言来自配置 language=%s", config_value)
        return str(config_value)
    system_language = detect_system_language()
    LOGGER.debug("语言来自系统区域: %s", system_language)
    return system_language


# ----------------------------------------------------------------------
# 全局实例：供不方便传递 I18n 对象的模块（如视图层）使用
# ----------------------------------------------------------------------
_I18N: Optional[I18n] = None


def get_i18n() -> I18n:
    """获取全局 I18n 实例（首次调用时惰性创建）。"""
    global _I18N
    if _I18N is None:
        _I18N = I18n()
    return _I18N


def set_global_i18n(i18n: I18n) -> None:
    """设置全局 I18n 实例（由微内核在启动阶段调用）。"""
    global _I18N
    _I18N = i18n
    LOGGER.debug("已设置全局 I18n 实例，语言=%s", i18n.language)


def t(key: str, **params: Any) -> str:
    """使用全局 I18n 实例翻译文本。"""
    return get_i18n().t(key, **params)
