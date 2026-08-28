"""内置命令：help / plugins / clear / about。

微内核（core.kernel）本身**不包含任何命令**，内置能力以"内置插件"的形式
注册进来。这样做的收益：

* 内核保持最小，启动路径清晰可审计；
* 内置命令与第三方插件走完全相同的注册通道，能力对等；
* 需要裁剪或替换（例如把 help 换成更丰富的实现）时无需改动内核。

所有面向用户的文本都通过 core.i18n 翻译，命令说明通过
`description_key` 交给视图层按当前语言渲染。
"""
from __future__ import annotations

from typing import Callable, Optional

from ..command import (
    MAX_COMMAND_SPACE_DEPTH,
    SYSTEM_SPACE,
    CommandRegistry,
)
from ..config import CLI_VERSION
from ..i18n import t
from ..logger import get_logger
from ..views import THEMES, THEME_LIST, render

LOGGER = get_logger("builtins")

# 分隔线宽度
SEPARATOR_WIDTH = 60


def register_system_commands(
        registry: CommandRegistry,
        plugins_provider: Optional[Callable] = None,
        system_space: str = SYSTEM_SPACE
        ) -> None:
    """
    注册全部内置命令。

    :param registry:         命令注册表
    :param plugins_provider: 返回插件管理器的可调用对象（由内核注入，
                             避免内置命令直接依赖全局单例）
    :param system_space:     系统命令所在空间名称
    """
    space = registry.set_system_space(system_space)

    def cmd_help(*command_path: str, theme: str = THEME_LIST):
        """查看命令（说明由 description_key 提供多语言文本）。"""
        if command_path:
            _print_command_detail(registry, list(command_path))
            return
        if theme not in THEMES:
            from ..exceptions import CommandArgumentException
            raise CommandArgumentException(
                f"未知视图 {theme}",
                key="error.command_argument",
                params={"reason": f"未知视图 {theme}，可选 {', '.join(THEMES)}"},
                details={"theme": theme, "supported": ", ".join(THEMES)},
            )
        print(t(
            "cmd.help.title",
            title=t("cli.title"), version=CLI_VERSION, theme=theme
        ))
        print(t("cmd.help.hint", max_depth=MAX_COMMAND_SPACE_DEPTH))
        print("-" * SEPARATOR_WIDTH)
        for line in render(registry.root, theme=theme):
            print(line)
        print("-" * SEPARATOR_WIDTH)
        print(t("cmd.help.usage"))
        LOGGER.debug("已输出 %s 视图的命令列表", theme)

    def cmd_plugins():
        """列出当前已加载的全部插件。"""
        provider = plugins_provider or (lambda: None)
        manager = provider()
        plugins = manager.get_plugin_list() if manager else {}
        if not plugins:
            print(t("cmd.plugins.empty"))
            return
        print(t("cmd.plugins.title", count=len(plugins)))
        print("-" * SEPARATOR_WIDTH)
        for name, meta in plugins.items():
            print(
                f"  {name} v{meta.get('version')} "
                f"(cli {meta.get('cli_version')}) "
                f"by {meta.get('author')}"
            )
            print(f"    {meta.get('description')}")
        print("-" * SEPARATOR_WIDTH)

    def cmd_clear():
        """清空终端屏幕。"""
        print("\033c", end="")

    def cmd_about():
        """输出关于信息。"""
        print(t("cmd.about.title", title=t("cli.title"),
                version=CLI_VERSION))
        print(t("cmd.about.author"))

    # 后续注册统一使用空间路径字符串，避免依赖对象引用
    space_path = space.full_path()
    registry.register(
        "help", cmd_help, commandspace=space_path,
        description_key="cmd.help.description"
    )
    registry.register(
        "plugins", cmd_plugins, commandspace=space_path,
        description_key="cmd.plugins.description"
    )
    registry.register(
        "clear", cmd_clear, commandspace=space_path,
        description_key="cmd.clear.description"
    )
    registry.register(
        "about", cmd_about, commandspace=space_path,
        description_key="cmd.about.description"
    )
    registry.register_option(
        "system/help", "-t", "--theme",
        takes_value=True, default=THEME_LIST,
        help=t("cmd.help.theme_desc", themes="/".join(THEMES))
    )
    LOGGER.info("内置命令已注册到 %s 空间", space.full_path())


def _print_command_detail(registry: CommandRegistry, command_path: list[str]):
    """打印单个命令的详细信息（名称、空间、事件、选项、说明）。"""
    info = registry.get_command(command_path)
    if not info:
        print(t("cmd.help.not_found", command=" ".join(command_path)))
        return
    _function, events, space = info
    label = t("cmd.help.detail.command")
    print(f"{label}:   {' '.join(command_path)}")
    print(f"{t('cmd.help.detail.space')}:   {space}")
    events_text = ", ".join(events) if events else t("cmd.help.no_value")
    print(f"{t('cmd.help.detail.events')}:   {events_text}")
    entry = registry.get_command_entry(command_path)
    if entry and entry.description_key:
        summary = t(entry.description_key)
    elif entry and entry.description:
        summary = entry.description
    else:
        doc = getattr(entry.function, "__doc__", None) if entry else None
        summary = (doc or t("cmd.help.no_doc")).strip().splitlines()[0]
    print(f"{t('cmd.help.detail.description')}:   {summary}")

    options = entry.unique_options() if entry else []
    if options:
        print(f"{t('cmd.help.detail.options')}:")
        for option in options:
            suffix = (
                "" if option.default is None
                else t("cmd.help.default", default=option.default)
            )
            line = f"  {option.display:<24} {option.help} {suffix}"
            print(line.rstrip())
