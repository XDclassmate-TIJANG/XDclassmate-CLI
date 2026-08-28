"""国际化子包：对外暴露 I18n 与便捷翻译函数。"""
from .i18n import (
    DEFAULT_LANGUAGE,
    ENV_LANGUAGE,
    FALLBACK_LANGUAGE,
    LANGUAGE_DIR,
    I18n,
    detect_language,
    detect_system_language,
    get_i18n,
    set_global_i18n,
    t,
)

__all__ = [
    "DEFAULT_LANGUAGE",
    "ENV_LANGUAGE",
    "FALLBACK_LANGUAGE",
    "LANGUAGE_DIR",
    "I18n",
    "detect_language",
    "detect_system_language",
    "get_i18n",
    "set_global_i18n",
    "t",
]
