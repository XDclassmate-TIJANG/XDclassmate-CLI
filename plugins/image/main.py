"""
Plugin of XDclassmate-CLI.
A tool for processing images.
"""

import os

from core.command import registry
from core.config import ConfigManager

# Logger
from core.logger import get_logger
logger = get_logger("plugin.image")

# I18n
from core.i18n.i18n import I18n

# Tools Import
from tools.size import cmd_size

COMMAND_SPACE = "image"

SRC = os.path.dirname(__file__)
LANGUAGE_DIR = os.path.join(SRC, "languages")

# Plugin Entry
def main():
    """Plugin entrance: Register image processing related commands in the image command space."""
    # """插件入口：把图片处理相关命令注册到 image 命令空间。"""

    # Load Config
    cfg = ConfigManager(f"{SRC}/xdclassmate.cli.setting.json")
    language = cfg.load_config("default_language")

    # Set I18n
    # You can also use "get_i18n()" and "t()".
    i18n = I18n(directory=LANGUAGE_DIR)
    i18n.set_language(str(language))

    # Register Command
    registry.register(
        "size",
        cmd_size,
        commandspace=COMMAND_SPACE,
        description=i18n.t("plugin.image.help"),
    )
