"""
Plugin of XDclassmate-CLI.
A tool for processing images.
"""

from core.command import registry

# Tools Import
from tools.size import cmd_size

COMMAND_SPACE = "image"


def main():
    """插件入口：把图片处理相关命令注册到 image 命令空间。"""
    registry.register(
        "size",
        cmd_size,
        commandspace=COMMAND_SPACE,
        description="size <图片路径>：输出图片的宽高。",
    )
