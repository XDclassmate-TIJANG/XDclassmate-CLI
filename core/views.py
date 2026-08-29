"""命令视图渲染：把命令空间树渲染成不同样式的文本。

支持的主题（theme）：
    list   扁平分组：按命令空间分组列出命令，组标题使用完整空间路径
    tree   树形视图：用连接线绘制空间与命令的层级关系
    table  表格视图：按 空间 / 命令 / 说明 三列对齐，自动截断过长文本

所有渲染函数只负责生成文本行，不做任何打印，便于测试与复用。
"""
from __future__ import annotations

import shutil
import sys
import unicodedata
from typing import TYPE_CHECKING, Iterator, Optional

from .exceptions import CommandArgumentException
from .i18n import t
from .logger import get_logger

if TYPE_CHECKING:  # 仅用于类型提示，避免与 command 模块循环导入
    from .command import CommandSpace

LOGGER = get_logger("views")

# 可用视图主题
THEME_LIST = "list"
THEME_TREE = "tree"
THEME_TABLE = "table"
THEMES = (THEME_LIST, THEME_TREE, THEME_TABLE)

# 树形视图字符集：非 UTF-8 终端自动回退为 ASCII，避免 Windows 控制台乱码
UNICODE_GLYPHS = {
    "branch": "├── ",
    "last": "└── ",
    "vertical": "│   ",
    "space": "    ",
}
ASCII_GLYPHS = {
    "branch": "|-- ",
    "last": "`-- ",
    "vertical": "|   ",
    "space": "    ",
}

# 无法获取终端宽度时的兜底值
DEFAULT_WIDTH = 80


def supports_unicode() -> bool:
    """判断标准输出是否支持 UTF-8（否则回退 ASCII 字符）。"""
    encoding = getattr(sys.stdout, "encoding", "") or ""
    normalized = encoding.lower().replace("-", "").replace("_", "")
    return normalized.startswith("utf")


def terminal_width(default: int = DEFAULT_WIDTH) -> int:
    """获取终端宽度，异常时使用默认值，并保证最小值 40。"""
    try:
        width = shutil.get_terminal_size().columns
    except (AttributeError, ValueError, OSError):
        width = default
    return max(width, 40)


def display_width(text: str) -> int:
    """计算字符串显示宽度：东亚宽字符按 2 个字符宽计。"""
    return sum(
        2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        for char in text
    )


def pad(text: str, width: int) -> str:
    """按显示宽度右填充空格，保证中英文混排时列仍对齐。"""
    return text + " " * max(width - display_width(text), 0)


def truncate(text: str, width: int) -> str:
    """按显示宽度截断文本，超出部分用 ... 表示。"""
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    if width <= 3:
        return "." * width
    collected: list[str] = []
    total = 0
    for char in text:
        char_width = display_width(char)
        if total + char_width > width - 3:
            break
        collected.append(char)
        total += char_width
    return "".join(collected) + "..."


def command_summary(entry) -> str:
    """
    取命令说明文本。

    优先级：注册时的国际化键 > 注册时的说明文本 > 函数文档字符串首行。
    """
    key = getattr(entry, "description_key", None)
    if key:
        text = t(key)
        if text != key:
            return text
    description = getattr(entry, "description", None)
    if description:
        return description
    doc = getattr(entry.function, "__doc__", None)
    if not doc:
        return t("cmd.help.no_doc")
    return doc.strip().splitlines()[0]


def render(
        root: "CommandSpace",
        theme: str = THEME_LIST,
        width: Optional[int] = None
        ) -> list[str]:
    """
    按指定主题渲染命令空间树。

    :param root:  根命令空间
    :param theme: 视图主题（list/tree/table）
    :param width: 渲染宽度，缺省自动获取终端宽度
    :return:      文本行列表
    :raises CommandArgumentException: 主题不存在
    """
    if theme not in THEMES:
        raise CommandArgumentException(
            f"未知视图 {theme}，可选主题：{', '.join(THEMES)}",
            key="error.theme_invalid",
            params={"theme": theme, "themes": ", ".join(THEMES)},
            details={"theme": theme, "supported": ", ".join(THEMES)},
        )
    if theme == THEME_TREE:
        lines = render_tree(root, width)
    elif theme == THEME_TABLE:
        lines = render_table(root, width)
    else:
        lines = render_list(root, width)
    LOGGER.debug("渲染 %s 视图，共 %s 行", theme, len(lines))
    return lines


def iter_command_rows(root: "CommandSpace") -> Iterator[tuple[str, str, str]]:
    """遍历全部命令，产出 (空间路径, 命令名, 说明) 三元组。"""
    for space in root.iter_spaces():
        for name in space.command_names():
            summary = command_summary(space.commands[name])
            yield space.full_path(), name, summary


def render_list(
        root: "CommandSpace",
        width: Optional[int] = None
        ) -> list[str]:
    """
    list 主题：按命令空间扁平分组，每组标题为完整空间路径。

    修复点：旧实现把子空间缩进在父空间内部且重复打印完整路径，
    层级与路径信息混在一起难以阅读；新实现一组一标题、路径完整、
    命令统一两格缩进，结构一目了然。
    """
    width = width or terminal_width()
    lines: list[str] = []
    for space in root.iter_spaces():
        names = space.command_names()
        if not names:
            continue
        lines.append(f"[{space.full_path()}]")
        name_width = max(len(name) for name in names)
        indent = 2
        for name in names:
            entry = space.commands[name]
            rest = width - indent - name_width - 2
            summary = truncate(command_summary(entry), max(rest, 12))
            lines.append(f"{' ' * indent}{pad(name, name_width)}  {summary}")
    return lines


def render_tree(
        root: "CommandSpace",
        width: Optional[int] = None
        ) -> list[str]:
    """tree 主题：用连接线绘制空间与命令的层级树。"""
    width = width or terminal_width()
    glyphs = UNICODE_GLYPHS if supports_unicode() else ASCII_GLYPHS
    lines = [root.full_path()]
    _render_tree_node(root, "", glyphs, lines, width)
    return lines


def _render_tree_node(
        space: "CommandSpace",
        prefix: str,
        glyphs: dict[str, str],
        lines: list[str],
        width: int
        ) -> None:
    """递归渲染单个空间节点：命令在前，子空间在后。"""
    names = space.command_names()
    # 同一节点内的命令名按显示宽度对齐，输出更整齐
    name_width = max((display_width(name) for name in names), default=0)
    items: list[tuple[str, object, bool]] = [
        (name, space.commands[name], False) for name in names
    ]
    items.extend(
        (child.name, child, True) for child in space.children.values()
    )

    for index, (name, payload, is_space) in enumerate(items):
        is_last = index == len(items) - 1
        connector = glyphs["last"] if is_last else glyphs["branch"]
        head = f"{prefix}{connector}"
        if is_space:
            lines.append(f"{head}{name}/")
            child_prefix = prefix + (
                glyphs["space"] if is_last else glyphs["vertical"]
            )
            _render_tree_node(payload, child_prefix, glyphs, lines, width)
            continue
        rest = width - display_width(head) - name_width - 2
        summary = truncate(command_summary(payload), max(rest, 12))
        lines.append(f"{head}{pad(name, name_width)}  {summary}")


def render_table(
        root: "CommandSpace",
        width: Optional[int] = None
        ) -> list[str]:
    """table 主题：空间 / 命令 / 说明 三列对齐，自动截断说明列。"""
    width = width or terminal_width()
    rows = list(iter_command_rows(root))
    # 表头与 help 命令详情视图共用同一组语言键，保证术语一致
    headers = (
        t("cmd.help.detail.space"),
        t("cmd.help.detail.command"),
        t("cmd.help.detail.description"),
    )

    space_width = max(
        [display_width(headers[0])] + [display_width(row[0]) for row in rows]
    )
    name_width = max(
        [display_width(headers[1])] + [display_width(row[1]) for row in rows]
    )
    desc_width = max(width - space_width - name_width - 4, 12)

    lines = [
        f"{pad(headers[0], space_width)}  "
        f"{pad(headers[1], name_width)}  {headers[2]}",
        "-" * min(width, space_width + name_width + desc_width + 4),
    ]
    for space_name, name, summary in rows:
        lines.append(
            f"{pad(space_name, space_width)}  {pad(name, name_width)}  "
            f"{truncate(summary, desc_width)}"
        )
    return lines
