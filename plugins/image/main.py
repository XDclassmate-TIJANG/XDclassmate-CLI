"""
Plugin of XDclassmate-CLI.
A tool for processing images.

本插件同时充当「官方示例」，示范插件作者应遵循的三条约定：

1. **语言包放在插件自己的 languages/ 目录**，清单里用 `"languages": "languages"`
   声明相对目录即可（框架会在入口执行前把它们并入全局 i18n）。
   不要再写 `{plugin_dir}` / `%(plugin_dir)s` 之类的占位符，也不要自建
   I18n 实例——两套实例会造成「注册期能翻译、执行期吐出键名」的假象。

2. **命令说明用 description_key=**，而不是 description=。前者让 help 视图
   按当前语言实时翻译，切换语言无需重启；后者在注册时就固定成一种语言。

3. **命令输出用全局 t()**（`from core.i18n import t`），与框架、其他插件
   共用同一个语言包命名空间，键名建议以 `plugin.<插件名>.` 开头避免撞车。
"""

# Command Register
from core.command import registry

# Tools Import
# Should many tools here, but I just made one.
# 依赖框架已把插件根目录加入模块搜索路径（Plugins._prepare_sys_path）。
from tools.size import cmd_size

# Define Command Space
# The space of this plugin is "image".
COMMAND_SPACE = "image"


# Plugin Entry
def main():
    """Plugin entrance: register image commands into the image space."""
    registry.register(
        "size",
        cmd_size,
        commandspace=COMMAND_SPACE,
        description_key="plugin.image.help",
    )
