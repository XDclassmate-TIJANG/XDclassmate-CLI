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
    get_language,
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
    "get_language",
    # "load_pack_from_directory", 改到I18n内部了
    # "register_pack",
    "set_global_i18n",
    "t",
]
