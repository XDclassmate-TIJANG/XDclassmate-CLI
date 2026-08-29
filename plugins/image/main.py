"""
Plugin of XDclassmate-CLI.
A tool for processing images.
"""

import os

from core.command import registry
# from core.config import ConfigManager

# Logger
from core.logger import get_logger
logger = get_logger("plugin.image")

# I18n
from core.i18n.i18n import get_i18n

# Tools Import
from tools.size import cmd_size

COMMAND_SPACE = "image"

SRC = os.path.dirname(__file__)
LANGUAGE_DIR = os.path.join(SRC, "languages")


# Plugin Entry
def main():
    """Plugin entrance: register image commands into the image space."""
    # """插件入口：把图片处理相关命令注册到 image 命令空间。"""

    # Load Config
    # cfg = ConfigManager(f"{SRC}/xdclassmate.cli.setting.json")

    # Set I18n
    i18n = get_i18n()

    # Register Command
    registry.register(
        "size",
        cmd_size,
        commandspace=COMMAND_SPACE,
        description=i18n.t("plugin.image.help"),
    )
