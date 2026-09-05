"""
Plugin of XDclassmate-CLI.
A tool for processing images.
"""

# Command Register
from core.command import registry

# Config Manage
from core.config import ConfigManager

# Logger
from core.logger import get_logger
logger = get_logger("plugin.image")

# I18n
from core.i18n.i18n import I18n

# Tools Import
# Should many tools here, but I just made one.
from tools.size import cmd_size

# Define Command Space
# The scape of this plugin is "image".
COMMAND_SPACE = "image"

# Dir About Plugin
import os
SRC = os.path.dirname(__file__)
DEFAULR_LANGUAGE_DIR = os.path.join(SRC, "languages")


# Plugin Entry
def main():
    """Plugin entrance: register image commands into the image space."""

    # Load Config
    cfg = ConfigManager(f"{SRC}/xdclassmate.cli.setting.json")
    languages_dir = cfg.load_config("languages_dir", default=DEFAULR_LANGUAGE_DIR)
    languages_dir = languages_dir % {"plugin_dir": SRC}

    # Set I18n
    i18n = I18n(directory=languages_dir)

    # Register Command
    registry.register(
        "size",
        cmd_size,
        commandspace=COMMAND_SPACE,
        description=i18n.t("plugin.image.help")
    )