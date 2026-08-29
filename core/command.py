"""命令注册表：以树形结构组织命令空间（commandspace）、命令与选项。

设计要点
--------
1. **默认空间**：`default` 是根空间，也是 `commandspace` 的默认值。
   注册到根空间的命令，调用时**不需要**写空间前缀，例如 `hello`；
   注册到子空间的命令，需要按路径调用，例如 `space1 command1`。
2. **嵌套空间**：空间可以逐层嵌套，调用形式为
   `space1 space2 space3 command1 [参数...] [选项...]`，
   嵌套层级上限见 `MAX_COMMAND_SPACE_DEPTH`（默认 20 层，根空间不计入）。
3. **同名命令**：不同空间内的命令允许重名，只有**同一空间内**重名才报错。
4. **命令选项**：通过 `register_option` 为命令声明选项（如 `-v/--verbose`），
   执行时选项会被解析为关键字参数传给命令函数。
5. **路径表示**：API 中的空间/命令路径可写成
   * 字符串：`"space1/space2"` 或 `"space1 space2"`
   * 序列：`["space1", "space2"]`
   以 `default` 开头的路径会被自动归一（根空间无需写出）。
6. **解析优先级**：每一层优先匹配**子空间**，再匹配**当前空间的命令**；
   根空间中找不到命令时，会回退到 `system` 空间（系统命令可裸调用）。
"""
from __future__ import annotations

import re
from typing import Callable, Iterator, Optional, Sequence, Union

from .event_bus import bus
from .exceptions import (
    CommandArgumentException,
    CommandExecutionError,
    CommandNotFoundError,
    CommandSpaceDepthExceededError,
    CommandSpaceNotFoundError,
    DuplicateCommandNamesError,
    DuplicateCommandSpaceNamesError,
    DuplicateOptionNamesError,
    InvalidCommandSpaceNameError,
    XDclassmateCLIException,
)
from .logger import get_logger

# 模块日志记录器
LOGGER = get_logger("command")

# 根空间名称：commandspace 省略时使用，命令可裸调用
DEFAULT_SPACE = "default"
# 系统命令空间的默认名称（由 core.builtins 创建并登记）
SYSTEM_SPACE = "system"
# 命令空间最大嵌套层数（根空间不计入，即最多 20 层显式空间）
MAX_COMMAND_SPACE_DEPTH = 20
# 路径分隔符：支持 "/"、反斜杠与空白三种写法
_PATH_SPLIT_PATTERN = re.compile(r"[/\\\s]+")
# 选项标记：以 - 开头且不是单独的 "-"
_OPTION_PATTERN = re.compile(r"^--?[^-].*$")

# 空间/命令路径的入参类型：字符串或字符串序列
SpacePath = Union[str, Sequence[str], None]


def _simplify_type_error(message: str) -> str:
    """
    去掉 TypeError 内部的函数名，只保留人类可读的原因。

    例如 "<locals>.cmd_about() takes 0 positional arguments but 2 were given"
    会被简化为 "takes 0 positional arguments but 2 were given"。
    """
    match = re.search(r"\)\s*(.+)$", message)
    if match:
        return match.group(1).strip()
    return message


def normalize_space_path(value: SpacePath) -> list[str]:
    """
    把各种写法的空间路径归一化为名称列表。

    :param value: None / "" / "space1/space2" / "space1 space2" / 序列
    :return: 归一化后的名称列表，根空间返回空列表
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = _PATH_SPLIT_PATTERN.split(value.strip())
    else:
        parts = []
        for item in value:
            parts.extend(_PATH_SPLIT_PATTERN.split(str(item).strip()))
    parts = [part for part in parts if part]
    # 根空间名称只是书写上的可选项，归一化时去掉
    while parts and parts[0] == DEFAULT_SPACE:
        parts = parts[1:]
    return parts


class Option:
    """命令选项定义，例如 `-v/--verbose` 或 `--theme list`。"""

    def __init__(
            self,
            *names: str,
            takes_value: bool = False,
            default: object = None,
            help: str = "",
            dest: Optional[str] = None
            ):
        """
        :param names:       选项名称，可多个别名，均需以 - 或 -- 开头
        :param takes_value: 是否需要取值（True 为值选项，False 为开关选项）
        :param default:     未提供该选项时的默认值（开关选项缺省为 False，
                            即「未出现即为假」）
        :param help:        选项说明
        :param dest:        传给命令函数的关键字参数名，缺省由名称推导
        """
        if not names:
            raise CommandArgumentException(
                "选项至少需要提供一个名称",
                key="error.option_requires_name",
            )
        for name in names:
            if not name.startswith("-"):
                raise CommandArgumentException(
                    f"选项名称必须以 - 开头: {name}",
                    key="error.option_name_invalid",
                    params={"option": name},
                    details={"option": name},
                )
        self.names = tuple(names)
        self.takes_value = takes_value
        # 开关选项未出现时应为 False，而不是 None
        if default is None and not takes_value:
            default = False
        self.default = default
        self.help = help
        self.dest = dest or self._infer_dest()

    def _infer_dest(self) -> str:
        """由选项名称推导关键字参数名，优先使用长选项。"""
        long_name = next(
            (name for name in self.names if name.startswith("--")), None
        )
        base = long_name or self.names[0]
        return base.lstrip("-").replace("-", "_")

    @property
    def display(self) -> str:
        """形如 `-t/--theme <theme>` 的展示文本。"""
        text = "/".join(self.names)
        if self.takes_value:
            return f"{text} <{self.dest}>"
        return text

    def matches(self, token: str) -> bool:
        """判断 token 是否命中该选项的某个名称。"""
        return token in self.names

    def __repr__(self) -> str:
        return f"<Option {self.display} default={self.default!r}>"


class CommandEntry:
    """命令条目：处理函数 + 事件列表 + 选项表 + 展示元数据。"""

    def __init__(
            self,
            function: Callable,
            events: Optional[list[str]] = None,
            description_key: Optional[str] = None,
            description: Optional[str] = None
            ):
        """
        :param function:        命令处理函数
        :param events:          执行前广播的事件列表
        :param description_key: 命令说明的国际化键（优先于 description）
        :param description:     命令说明文本（未国际化时使用）
        """
        self.function = function
        self.events = list(events or [])
        # 选项按名称索引，别名指向同一个 Option 对象
        self.options: dict[str, Option] = {}
        self.description_key = description_key
        self.description = description

    def add_option(self, option: Option) -> None:
        """登记选项；任一名称重复都会报错。"""
        for name in option.names:
            if name in self.options:
                raise DuplicateOptionNamesError(
                    f"选项 {name} 已被注册",
                    key="error.option_duplicate",
                    params={"option": name},
                    details={"option": name},
                )
        for name in option.names:
            self.options[name] = option

    def unique_options(self) -> list[Option]:
        """返回去重后的选项列表（别名只保留一个）。"""
        seen: dict[int, Option] = {}
        for option in self.options.values():
            seen[id(option)] = option
        return list(seen.values())

    def __repr__(self) -> str:
        return (
            f"<CommandEntry {self.function.__name__} "
            f"events={len(self.events)} options={len(self.unique_options())}>"
        )


class CommandSpace:
    """命令空间节点：可包含子空间与命令，构成一棵树。"""

    def __init__(self, name: str, parent: Optional["CommandSpace"] = None):
        self.name = name
        self.parent = parent
        self.children: dict[str, "CommandSpace"] = {}
        self.commands: dict[str, CommandEntry] = {}

    # ---------------- 结构信息 ----------------
    @property
    def is_root(self) -> bool:
        """是否为根空间（default）。"""
        return self.parent is None

    def depth(self) -> int:
        """当前空间所处的嵌套层数，根空间为 0。"""
        level, node = 0, self
        while node.parent is not None:
            level += 1
            node = node.parent
        return level

    def full_path(self, separator: str = "/") -> str:
        """从根空间到当前空间的完整路径，根空间显示为 default。"""
        names: list[str] = []
        node: Optional[CommandSpace] = self
        while node is not None and not node.is_root:
            names.append(node.name)
            node = node.parent
        return separator.join(reversed(names)) or DEFAULT_SPACE

    def __repr__(self) -> str:
        return (
            f"<CommandSpace {self.full_path()} "
            f"children={len(self.children)} commands={len(self.commands)}>"
        )

    # ---------------- 子空间操作 ----------------
    def child(self, name: str) -> Optional["CommandSpace"]:
        """获取直接子空间，不存在返回 None。"""
        return self.children.get(name)

    def add_child(
            self,
            name: str,
            max_depth: int = MAX_COMMAND_SPACE_DEPTH
            ) -> "CommandSpace":
        """
        在当前空间下新增一个子空间。

        :raises InvalidCommandSpaceNameError: 名称为空或包含路径分隔符
        :raises DuplicateCommandSpaceNamesError: 同级同名空间已存在
        :raises CommandSpaceDepthExceededError: 嵌套层数超过 max_depth
        """
        if not name or _PATH_SPLIT_PATTERN.search(name):
            raise InvalidCommandSpaceNameError(
                f"命令空间名称非法: {name!r}（不能为空，也不能包含 / 或空白）",
                key="error.space_name_invalid",
                params={"name": name},
                details={"name": name},
            )
        if name in self.children:
            raise DuplicateCommandSpaceNamesError(
                f"命令空间 {self.full_path()}/{name} 已被注册",
                key="error.space_duplicate",
                params={"space": f"{self.full_path()}/{name}"},
                details={"space": f"{self.full_path()}/{name}"},
            )
        if self.depth() + 1 > max_depth:
            raise CommandSpaceDepthExceededError(
                "命令空间嵌套层数超过上限",
                key="error.space_depth",
                params={
                    "depth": self.depth() + 1,
                    "max_depth": max_depth,
                },
                details={
                    "space": f"{self.full_path()}/{name}",
                    "depth": self.depth() + 1,
                    "max_depth": max_depth,
                }
            )
        space = CommandSpace(name, parent=self)
        self.children[name] = space
        return space

    def remove_child(self, name: str) -> "CommandSpace":
        """移除直接子空间（连同其内部所有命令与子空间）。"""
        if name not in self.children:
            raise CommandSpaceNotFoundError(
                f"命令空间 {self.full_path()}/{name} 未找到",
                key="error.space_not_found",
                params={"space": f"{self.full_path()}/{name}"},
                details={"space": f"{self.full_path()}/{name}"},
            )
        return self.children.pop(name)

    def iter_spaces(self) -> Iterator["CommandSpace"]:
        """深度优先遍历自身及其全部后代空间。"""
        yield self
        for child in self.children.values():
            yield from child.iter_spaces()

    # ---------------- 命令操作 ----------------
    def add_command(
            self,
            name: str,
            function: Callable,
            events: Optional[list[str]] = None,
            description_key: Optional[str] = None,
            description: Optional[str] = None
            ) -> CommandEntry:
        """在当前空间新增命令，返回命令条目。"""
        if name in self.commands:
            raise DuplicateCommandNamesError(
                f"命令 {self.full_path()}/{name} 已被注册",
                key="error.command_duplicate",
                params={"command": f"{self.full_path()}/{name}"},
                details={"command": f"{self.full_path()}/{name}"},
            )
        entry = CommandEntry(function, events, description_key, description)
        self.commands[name] = entry
        return entry

    def remove_command(self, name: str) -> CommandEntry:
        """移除当前空间内的命令。"""
        if name not in self.commands:
            raise CommandNotFoundError(
                f"命令 {self.full_path()}/{name} 未找到",
                key="error.command_missing",
                params={"command": f"{self.full_path()}/{name}"},
                details={"command": f"{self.full_path()}/{name}"},
            )
        return self.commands.pop(name)

    def command_names(self) -> list[str]:
        """当前空间（不含子空间）内的命令名列表。"""
        return list(self.commands.keys())


class CommandRegistry:
    """命令注册表：管理命令空间树、命令选项与命令分发。"""

    def __init__(self):
        # 根空间即 default，注册在其中的命令可裸调用
        self.root = CommandSpace(DEFAULT_SPACE)
        # 系统命令空间：由 core.builtins 创建并登记（微内核不含内置命令）
        self._system_space: Optional[CommandSpace] = None

    # ------------------------------------------------------------------
    # 系统命令空间（由 builtins 注入，未注入时不做回退）
    # ------------------------------------------------------------------
    def set_system_space(self, name: SpacePath) -> CommandSpace:
        """
        登记系统命令所在空间（不存在时自动创建）。

        登记后，根空间中找不到的命令会回退到该系统空间，
        因此 `help` 与 `system help` 等价。
        """
        space = self._ensure_space(name)
        self._system_space = space
        LOGGER.debug("系统命令空间已登记: %s", space.full_path())
        return space

    @property
    def system_space(self) -> Optional[CommandSpace]:
        """当前登记的系统命令空间，未登记时为 None。"""
        return self._system_space

    # ------------------------------------------------------------------
    # 命令空间管理
    # ------------------------------------------------------------------
    def register_command_space(
            self,
            name: SpacePath,
            parent: SpacePath = None
            ) -> CommandSpace:
        """
        注册（创建）一个命令空间，支持一次创建多级。

        :param name:    空间路径，如 "space1"、"space1/space2"、序列
        :param parent:  可选父空间路径，缺省从根空间开始
        :return:        新建的最深层空间对象
        """
        node = self.require_command_space(parent) if parent else self.root
        parts = normalize_space_path(name)
        if not parts:
            raise InvalidCommandSpaceNameError(
                "命令空间名称不能为空",
                key="error.space_name_empty",
            )
        for part in parts:
            node = node.add_child(part)
        LOGGER.debug("注册命令空间 %s", node.full_path())
        return node

    def get_command_space(self, name: SpacePath = None) -> Optional[
            CommandSpace]:
        """按路径查找命令空间，根空间传 None/"default"/""，不存在返回 None。"""
        node: Optional[CommandSpace] = self.root
        for part in normalize_space_path(name):
            node = node.child(part) if node else None
            if node is None:
                return None
        return node

    def require_command_space(self, name: SpacePath = None) -> CommandSpace:
        """按路径查找命令空间，不存在则抛出 CommandSpaceNotFoundError。"""
        space = self.get_command_space(name)
        if space is None:
            raise CommandSpaceNotFoundError(
                f"命令空间 {name or DEFAULT_SPACE} 未找到",
                key="error.space_not_found",
                params={"space": name or DEFAULT_SPACE},
                details={"space": name or DEFAULT_SPACE},
            )
        return space

    def get_command_space_list(self) -> list[str]:
        """返回全部命令空间的完整路径列表（根空间显示为 default）。"""
        return [space.full_path() for space in self.root.iter_spaces()]

    def delete_command_space(self, name: SpacePath) -> None:
        """删除命令空间及其内部所有命令与子空间。"""
        parts = normalize_space_path(name)
        if not parts:
            raise InvalidCommandSpaceNameError(
                "不能删除根命令空间 default",
                key="error.space_root_delete",
            )
        parent = self.require_command_space(parts[:-1])
        parent.remove_child(parts[-1])
        LOGGER.debug("删除命令空间 %s", "/".join(parts))

    # ------------------------------------------------------------------
    # 命令注册与查询
    # ------------------------------------------------------------------
    def register(
            self,
            name: str,
            function: Callable,
            event: Optional[list[str]] = None,
            commandspace: SpacePath = None,
            description_key: Optional[str] = None,
            description: Optional[str] = None
            ) -> CommandEntry:
        """
        注册一条命令。

        :param name:            命令名称（同一空间内不可重复，跨空间允许重名）
        :param function:        对应的处理函数（可调用对象）
        :param event:           可选的事件列表，命令执行前会依次广播
        :param commandspace:    所属命令空间，缺省为 default（根空间，可裸调用）
        :param description_key: 命令说明的国际化键（多语言优先）
        :param description:     命令说明文本（未国际化时使用）
        :return:                命令条目，可用于继续注册选项
        """
        space = self._ensure_space(commandspace)
        entry = space.add_command(
            name, function, event, description_key, description
        )
        LOGGER.debug("注册命令 %s/%s", space.full_path(), name)
        return entry

    def register_option(
            self,
            command: SpacePath,
            *names: str,
            takes_value: bool = False,
            default: object = None,
            help: str = "",
            dest: Optional[str] = None
            ) -> Option:
        """
        为已注册的命令声明一个选项。

        :param command:     命令完整路径（如 "space1/command1"），
                            也可直接传入 register() 返回的 CommandEntry
        :param names:       选项名称，如 "-t"、"--theme"
        :param takes_value: 是否需要取值
        :param default:     未提供时的默认值
        :param help:        选项说明
        :param dest:        关键字参数名，缺省由名称推导
        :return:            新建的 Option 对象
        """
        entry = self.require_command_entry(command)
        option = Option(
            *names,
            takes_value=takes_value,
            default=default,
            help=help,
            dest=dest
        )
        entry.add_option(option)
        # 传入 CommandEntry 时用函数名记录日志，避免打印整个对象
        label = (
            command.function.__name__
            if isinstance(command, CommandEntry) else command
        )
        LOGGER.debug("为命令 %s 注册选项 %s", label, option.display)
        return option

    def get_command_entry(
            self,
            name: "SpacePath | CommandEntry"
            ) -> Optional[CommandEntry]:
        """
        查询命令条目，未找到返回 None。

        :param name: 命令完整路径，或 register() 返回的 CommandEntry 对象
        """
        # 直接传入命令条目时无需再按路径查找
        if isinstance(name, CommandEntry):
            return name
        parts = normalize_space_path(name)
        if not parts:
            return None
        space = self.get_command_space(parts[:-1])
        if space is None:
            return None
        entry = space.commands.get(parts[-1])
        if entry is None and space is self.root and self._system_space:
            # 与 execute 保持一致：根空间缺失时回退系统空间
            return self._system_space.commands.get(parts[-1])
        return entry

    def require_command_entry(
            self,
            name: "SpacePath | CommandEntry"
            ) -> CommandEntry:
        """查询命令条目，不存在则抛出 CommandNotFoundError。"""
        entry = self.get_command_entry(name)
        if entry is None:
            raise CommandNotFoundError(
                f"命令 {name} 未找到",
                key="error.command_missing",
                params={"command": name},
                details={"command": name},
            )
        return entry

    def get_command_options(
            self,
            name: "SpacePath | CommandEntry"
            ) -> list[Option]:
        """返回命令已注册的选项列表（别名已去重）。"""
        return self.require_command_entry(name).unique_options()

    def get_command(
            self,
            name: SpacePath
            ) -> Optional[tuple[Callable, list[str], str]]:
        """
        按完整路径查询命令。

        :return: (处理函数, 事件列表, 所属空间完整路径)；未找到返回 None
        """
        parts = normalize_space_path(name)
        if not parts:
            return None
        space = self.get_command_space(parts[:-1])
        if space is None:
            return None
        entry = space.commands.get(parts[-1])
        if entry is None and space is self.root and self._system_space:
            entry = self._system_space.commands.get(parts[-1])
            space = self._system_space
        if entry is None:
            return None
        return entry.function, entry.events, space.full_path()

    def get_command_list(self) -> list[str]:
        """返回全部命令的完整路径列表（根空间的命令只显示命令名）。"""
        commands: list[str] = []
        for space in self.root.iter_spaces():
            prefix = "" if space.is_root else f"{space.full_path()}/"
            commands.extend(f"{prefix}{name}" for name in space.commands)
        return commands

    def _ensure_space(self, commandspace: SpacePath) -> CommandSpace:
        """取得（必要时自动创建）指定路径的命令空间。"""
        node: CommandSpace = self.root
        for part in normalize_space_path(commandspace):
            node = node.child(part) or node.add_child(part)
        return node

    def modify_command(self, name: str, old_name: str) -> None:
        """
        重命名一条命令（在同一空间内）。

        :param name:     新的命令名称（可带空间路径，缺省同旧命令所在空间）
        :param old_name: 旧的命令完整路径
        """
        new_parts = normalize_space_path(name)
        old_parts = normalize_space_path(old_name)
        if not new_parts or not old_parts:
            raise CommandNotFoundError(
                "命令名称不能为空",
                key="error.command_name_empty",
            )

        old_space = self.require_command_space(old_parts[:-1])
        old_key = old_parts[-1]
        if old_key not in old_space.commands:
            raise CommandNotFoundError(
                f"命令 {old_space.full_path()}/{old_key} 未找到",
                key="error.command_missing",
                params={"command": f"{old_space.full_path()}/{old_key}"},
                details={"command": f"{old_space.full_path()}/{old_key}"},
            )

        # 只给了新名字（无路径）时沿用原空间
        new_space = (
            self.require_command_space(new_parts[:-1])
            if len(new_parts) > 1 else old_space
        )
        new_key = new_parts[-1]
        if new_key in new_space.commands:
            raise DuplicateCommandNamesError(
                f"命令 {new_space.full_path()}/{new_key} 已被注册",
                key="error.command_duplicate",
                params={"command": f"{new_space.full_path()}/{new_key}"},
                details={"command": f"{new_space.full_path()}/{new_key}"},
            )

        new_space.commands[new_key] = old_space.commands.pop(old_key)
        LOGGER.debug("重命名命令 %s -> %s", old_key, new_key)

    def migration_command(self, name: str, commandspace: SpacePath) -> None:
        """
        迁移一条命令到新的命令空间（空间不存在则自动创建）。

        :param name:         命令在原空间中的完整路径
        :param commandspace: 目标命令空间路径
        """
        parts = normalize_space_path(name)
        if not parts:
            raise CommandNotFoundError(
                "命令名称不能为空",
                key="error.command_name_empty",
            )
        old_space = self.require_command_space(parts[:-1])
        key = parts[-1]
        if key not in old_space.commands:
            raise CommandNotFoundError(
                f"命令 {old_space.full_path()}/{key} 未找到",
                key="error.command_missing",
                params={"command": f"{old_space.full_path()}/{key}"},
                details={"command": f"{old_space.full_path()}/{key}"},
            )
        new_space = self._ensure_space(commandspace)
        if key in new_space.commands:
            raise DuplicateCommandNamesError(
                f"命令 {new_space.full_path()}/{key} 已被注册",
                key="error.command_duplicate",
                params={"command": f"{new_space.full_path()}/{key}"},
                details={"command": f"{new_space.full_path()}/{key}"},
            )
        new_space.commands[key] = old_space.commands.pop(key)
        LOGGER.debug("迁移命令 %s -> %s", key, new_space.full_path())

    def delete_command(self, name: SpacePath) -> None:
        """按完整路径删除一条命令。"""
        parts = normalize_space_path(name)
        if not parts:
            raise CommandNotFoundError(
                "命令名称不能为空",
                key="error.command_name_empty",
            )
        space = self.require_command_space(parts[:-1])
        space.remove_command(parts[-1])
        LOGGER.debug("删除命令 %s", "/".join(parts))

    # ------------------------------------------------------------------
    # 命令分发
    # ------------------------------------------------------------------
    def resolve(
            self,
            tokens: Sequence[str]
            ) -> tuple[CommandSpace, str, list[str]]:
        """
        解析用户输入的 token 序列，定位到目标命令。

        规则：逐层优先匹配子空间；空间匹配结束后，剩余第一个 token 视为
        命令名，其后全部作为参数（含选项）；根空间内找不到命令时回退到
        system 空间。

        :return: (所属空间, 命令名, 参数列表)
        :raises CommandSpaceDepthExceededError: 空间嵌套超过上限
        :raises CommandNotFoundError: 缺少命令名或命令不存在
        """
        if not tokens:
            raise CommandNotFoundError(
                "输入为空，请指定要执行的命令",
                key="error.command_input_empty",
            )

        space, index = self._resolve_space(tokens)
        if index >= len(tokens):
            raise CommandNotFoundError(
                f"缺少命令名：{'/'.join(tokens)} 只是命令空间",
                key="error.command_missing_name",
                params={"space": space.full_path()},
                details={"space": space.full_path()},
            )

        name, args = tokens[index], list(tokens[index + 1:])
        target = space
        system = self._system_space
        if (system is not None and name not in target.commands
                and target is self.root and name in system.commands):
            # 系统命令回落：允许裸调用 system 空间的命令
            target = system
        if name not in target.commands:
            path = f"{target.full_path()}/{name}"
            raise CommandNotFoundError(
                f"命令 {path} 未找到，输入 help 查看可用命令",
                key="error.command_not_found",
                params={"path": path},
                details={"command": path},
            )
        return target, name, args

    def _resolve_space(
            self,
            tokens: Sequence[str]
            ) -> tuple[CommandSpace, int]:
        """沿 token 序列尽可能深地匹配子空间，返回 (空间, 下一个下标)。"""
        node = self.root
        index = 0
        while index < len(tokens):
            child = node.child(tokens[index])
            if child is None:
                break
            node = child
            index += 1
            if node.depth() > MAX_COMMAND_SPACE_DEPTH:
                raise CommandSpaceDepthExceededError(
                    "命令空间嵌套层数超过上限",
                    key="error.space_depth",
                    params={
                        "depth": node.depth(),
                        "max_depth": MAX_COMMAND_SPACE_DEPTH,
                    },
                    details={
                        "space": node.full_path(),
                        "depth": node.depth(),
                        "max_depth": MAX_COMMAND_SPACE_DEPTH,
                    }
                )
        return node, index

    def execute(self, tokens: Sequence[str]):
        """
        根据用户输入的 token 列表查找并执行命令。

        支持形式：
            ["命令", "参数", ...]                     —— 根空间命令，裸调用
            ["空间1", "命令", "参数", ...]              —— 单层空间
            ["空间1", "空间2", "命令", "--选项", "值"]  —— 多层嵌套 + 选项
        """
        space, name, args = self.resolve(tokens)
        entry = space.commands[name]
        path = name if space.is_root else f"{space.full_path()}/{name}"
        positional, values = self._parse_arguments(entry, args, path)
        LOGGER.debug(
            "执行命令 %s 位置参数=%s 选项=%s", path, positional, values
        )
        self._invoke(path, entry, positional, values)

    def _parse_arguments(
            self,
            entry: CommandEntry,
            tokens: list[str],
            path: str
            ) -> tuple[list[str], dict[str, object]]:
        """
        把参数 token 拆分为位置参数与选项值。

        命令未注册任何选项时，所有 token 原样作为位置参数（保持宽松兼容）；
        一旦注册了选项，未知选项、缺少取值等情况都会抛出
        CommandArgumentException。

        :return: (位置参数列表, 选项值字典)
        """
        if not entry.options:
            return list(tokens), {}

        positional: list[str] = []
        values: dict[str, object] = {}
        index = 0
        only_positional = False

        while index < len(tokens):
            token = tokens[index]
            if token == "--":
                # "--" 之后的内容一律视为位置参数
                only_positional = True
                index += 1
                continue
            if only_positional or not _OPTION_PATTERN.match(token):
                positional.append(token)
                index += 1
                continue

            name, inline_value = self._split_option(token)
            option = entry.options.get(name)
            if option is None:
                raise CommandArgumentException(
                    f"命令 {path} 不接受选项 {name}",
                    key="error.option_unknown",
                    params={"command": path, "option": name},
                    details={"command": path, "option": name},
                )
            if option.takes_value:
                value = inline_value
                if value is None:
                    index += 1
                    if index >= len(tokens) or _OPTION_PATTERN.match(
                            tokens[index]):
                        raise CommandArgumentException(
                            f"选项 {name} 缺少取值",
                            key="error.option_missing_value",
                            params={"option": name},
                            details={"command": path, "option": name},
                        )
                    value = tokens[index]
            else:
                if inline_value is not None:
                    raise CommandArgumentException(
                        f"选项 {name} 是开关选项，不接受取值",
                        key="error.option_no_value",
                        params={"option": name},
                        details={"command": path, "option": name},
                    )
                value = True
            values[option.dest] = value
            index += 1

        for option in entry.unique_options():
            values.setdefault(option.dest, option.default)
        return positional, values

    @staticmethod
    def _split_option(token: str) -> tuple[str, Optional[str]]:
        """拆分 `--theme=tree` 形式的选项为 (名称, 内联值)。"""
        if "=" in token:
            name, value = token.split("=", 1)
            return name, value
        return token, None

    def _invoke(
            self,
            path: str,
            entry: CommandEntry,
            args: list[str],
            values: dict[str, object]
            ) -> None:
        """
        执行单个命令：先广播声明的事件，再调用函数本体。

        框架异常原样上抛；参数不匹配包装为 CommandArgumentException；
        其他异常统一包装为 CommandExecutionError 并保留 __cause__。
        """
        for event in entry.events:
            bus.emit(event, path, args)
        try:
            entry.function(*args, **values)
        except XDclassmateCLIException:
            raise
        except TypeError as error:
            reason = _simplify_type_error(str(error))
            raise CommandArgumentException(
                f"命令 {path} 的参数不匹配: {reason}",
                key="error.command_signature_mismatch",
                params={"command": path, "reason": reason},
                details={"command": path},
            ) from error
        except Exception as error:
            raise CommandExecutionError(
                f"命令 {path} 执行失败: {error}",
                key="error.command_execution",
                params={"reason": str(error)},
                details={"command": path}
            ) from error


# 全局默认命令注册表：微内核默认使用它，插件也可通过它注册命令。
# 需要隔离实例时，直接构造 CommandRegistry() 即可（测试推荐做法）。
registry = CommandRegistry()
