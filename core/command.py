"""命令注册表：以树形结构组织命令空间（commandspace）与命令。

设计要点
--------
1. **默认空间**：`default` 是根空间，也是 `commandspace` 的默认值。
   注册到根空间的命令，调用时**不需要**写空间前缀，例如 `hello`；
   注册到子空间的命令，需要按路径调用，例如 `space1 command1`。
2. **嵌套空间**：空间可以逐层嵌套，调用形式为
   `space1 space2 space3 command1 [参数...]`，
   嵌套层级上限见 `MAX_COMMAND_SPACE_DEPTH`（默认 20 层，根空间不计入）。
3. **同名命令**：不同空间内的命令允许重名，只有**同一空间内**重名才报错。
4. **路径表示**：API 中的空间/命令路径可写成
   * 字符串：`"space1/space2"` 或 `"space1 space2"`
   * 序列：`["space1", "space2"]`
   以 `default` 开头的路径会被自动归一（根空间无需写出）。
5. **解析优先级**：每一层优先匹配**子空间**，再匹配**当前空间的命令**；
   根空间中找不到命令时，会回退到 `system` 空间（系统命令可裸调用，如 `help`）。
"""
from __future__ import annotations

import re
from typing import Callable, Iterator, Optional, Sequence, Union

from .config import CLI_VERSION
from .event_bus import bus
from .exceptions import (
    CommandExecutionError,
    CommandNotFoundError,
    CommandSpaceDepthExceededError,
    CommandSpaceNotFoundError,
    DuplicateCommandNamesError,
    DuplicateCommandSpaceNamesError,
    InvalidCommandSpaceNameError,
    XDclassmateCLIException,
)

# 根空间名称：commandspace 省略时使用，命令可裸调用
DEFAULT_SPACE = "default"
# 系统命令所在空间：根空间查找失败时回退到这里
SYSTEM_SPACE = "system"
# 命令空间最大嵌套层数（根空间不计入，即最多 20 层显式空间）
MAX_COMMAND_SPACE_DEPTH = 20
# 路径分隔符：支持 "/" 与空白两种写法
_PATH_SPLIT_PATTERN = re.compile(r"[/\\\s]+")

# 空间/命令路径的入参类型：字符串或字符串序列
SpacePath = Union[str, Sequence[str], None]
# 命令条目：处理函数 + 声明的事件列表
CommandEntry = tuple[Callable, list[str]]


def normalize_space_path(value: SpacePath) -> list[str]:
    """
    把各种写法的空间路径归一化为名称列表。

    :param value: None / "" / "space1/space2" / "space1 space2" / ["space1", "space2"]
    :return: 归一化后的名称列表，根空间返回空列表
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = _PATH_SPLIT_PATTERN.split(value.strip())
    else:
        parts: list[str] = []
        for item in value:
            parts.extend(_PATH_SPLIT_PATTERN.split(str(item).strip()))
    parts = [part for part in parts if part]
    # 根空间名称只是书写上的可选项，归一化时去掉
    while parts and parts[0] == DEFAULT_SPACE:
        parts = parts[1:]
    return parts


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
        """是否为根空间（default）"""
        return self.parent is None

    def depth(self) -> int:
        """当前空间所处的嵌套层数，根空间为 0"""
        level, node = 0, self
        while node.parent is not None:
            level += 1
            node = node.parent
        return level

    def full_path(self, separator: str = "/") -> str:
        """从根空间到当前空间的完整路径，根空间显示为 default"""
        names: list[str] = []
        node: Optional[CommandSpace] = self
        while node is not None and not node.is_root:
            names.append(node.name)
            node = node.parent
        return separator.join(reversed(names)) or DEFAULT_SPACE

    def __repr__(self) -> str:
        return f"<CommandSpace {self.full_path()} children={len(self.children)} commands={len(self.commands)}>"

    # ---------------- 子空间操作 ----------------
    def child(self, name: str) -> Optional["CommandSpace"]:
        """获取直接子空间，不存在返回 None"""
        return self.children.get(name)

    def has_child(self, name: str) -> bool:
        return name in self.children

    def add_child(self, name: str, max_depth: int = MAX_COMMAND_SPACE_DEPTH) -> "CommandSpace":
        """
        在当前空间下新增一个子空间。

        :raises InvalidCommandSpaceNameError: 名称为空或包含路径分隔符
        :raises DuplicateCommandSpaceNamesError: 同级同名空间已存在
        :raises CommandSpaceDepthExceededError: 嵌套层数超过 max_depth
        """
        if not name or _PATH_SPLIT_PATTERN.search(name):
            raise InvalidCommandSpaceNameError(
                f"命令空间名称非法: {name!r}（不能为空，也不能包含 / 或空白）", details={"name": name}
            )
        if name in self.children:
            raise DuplicateCommandSpaceNamesError(
                f"命令空间 {self.full_path()}/{name} 已被注册", details={"space": f"{self.full_path()}/{name}"}
            )
        if self.depth() + 1 > max_depth:
            raise CommandSpaceDepthExceededError(
                f"命令空间嵌套层数超过上限 {max_depth}",
                details={"space": f"{self.full_path()}/{name}", "depth": self.depth() + 1, "max_depth": max_depth}
            )
        space = CommandSpace(name, parent=self)
        self.children[name] = space
        return space

    def remove_child(self, name: str) -> "CommandSpace":
        """移除直接子空间（连同其内部所有命令与子空间）"""
        if name not in self.children:
            raise CommandSpaceNotFoundError(
                f"命令空间 {self.full_path()}/{name} 未找到", details={"space": f"{self.full_path()}/{name}"}
            )
        return self.children.pop(name)

    def iter_spaces(self) -> Iterator["CommandSpace"]:
        """深度优先遍历自身及其全部后代空间"""
        yield self
        for child in self.children.values():
            yield from child.iter_spaces()

    # ---------------- 命令操作 ----------------
    def add_command(self, name: str, function: Callable, events: Optional[list[str]] = None) -> None:
        if name in self.commands:
            raise DuplicateCommandNamesError(
                f"命令 {self.full_path()}/{name} 已被注册", details={"command": f"{self.full_path()}/{name}"}
            )
        self.commands[name] = (function, list(events or []))

    def remove_command(self, name: str) -> CommandEntry:
        if name not in self.commands:
            raise CommandNotFoundError(
                f"命令 {self.full_path()}/{name} 未找到", details={"command": f"{self.full_path()}/{name}"}
            )
        return self.commands.pop(name)

    def command_names(self) -> list[str]:
        """当前空间（不含子空间）内的命令名列表"""
        return list(self.commands.keys())


class CommandRegistry:
    """命令注册表：管理命令空间树与命令分发。"""

    def __init__(self):
        # 根空间即 default，注册在其中的命令可裸调用
        self.root = CommandSpace(DEFAULT_SPACE)
        # 系统命令空间（help / plugins / echo / clear）
        self._system_space = self.root.add_child(SYSTEM_SPACE)
        self._register_system_commands()

    # ------------------------------------------------------------------
    # 命令空间管理
    # ------------------------------------------------------------------
    def register_command_space(self, name: SpacePath, parent: SpacePath = None) -> CommandSpace:
        """
        注册（创建）一个命令空间，支持一次创建多级。

        :param name:    空间路径，如 "space1"、"space1/space2"、["space1", "space2"]
        :param parent:  可选父空间路径，缺省从根空间开始
        :return:        新建的最深层空间对象
        """
        node = self.get_command_space(parent) if parent else self.root
        if node is None:
            raise CommandSpaceNotFoundError(f"父命令空间 {parent} 未找到", details={"space": parent})
        parts = normalize_space_path(name)
        if not parts:
            raise InvalidCommandSpaceNameError("命令空间名称不能为空")
        for part in parts:
            node = node.add_child(part)
        return node

    def get_command_space(self, name: SpacePath) -> Optional[CommandSpace]:
        """按路径查找命令空间，根空间传 None/"default"/""，不存在返回 None"""
        node: Optional[CommandSpace] = self.root
        for part in normalize_space_path(name):
            node = node.child(part) if node else None
            if node is None:
                return None
        return node

    def require_command_space(self, name: SpacePath) -> CommandSpace:
        """按路径查找命令空间，不存在则抛出 CommandSpaceNotFoundError"""
        space = self.get_command_space(name)
        if space is None:
            raise CommandSpaceNotFoundError(
                f"命令空间 {name or DEFAULT_SPACE} 未找到", details={"space": name or DEFAULT_SPACE}
            )
        return space

    def get_command_space_list(self) -> list[str]:
        """返回全部命令空间的完整路径列表（根空间显示为 default）"""
        return [space.full_path() for space in self.root.iter_spaces()]

    def delete_command_space(self, name: SpacePath) -> None:
        """删除命令空间及其内部所有命令与子空间"""
        parts = normalize_space_path(name)
        if not parts:
            raise InvalidCommandSpaceNameError("不能删除根命令空间 default")
        parent = self.require_command_space(parts[:-1])
        parent.remove_child(parts[-1])

    # ------------------------------------------------------------------
    # 命令注册与查询
    # ------------------------------------------------------------------
    def register(
            self,
            name: str,
            function: Callable,
            event: Optional[list[str]] = None,
            commandspace: SpacePath = None
            ) -> None:
        """
        注册一条命令。

        :param name:          命令名称（同一空间内不可重复，跨空间允许重名）
        :param function:      对应的处理函数（可调用对象）
        :param event:         可选的事件列表，命令执行前会依次广播
        :param commandspace:  所属命令空间，缺省为 default（根空间，可裸调用）
        """
        space = self._ensure_space(commandspace)
        space.add_command(name, function, event)

    def _ensure_space(self, commandspace: SpacePath) -> CommandSpace:
        """取得（必要时自动创建）指定路径的命令空间"""
        node: CommandSpace = self.root
        for part in normalize_space_path(commandspace):
            node = node.child(part) or node.add_child(part)
        return node

    def get_command(self, name: SpacePath) -> Optional[tuple[Callable, list[str], str]]:
        """
        按完整路径查询命令。

        :return: (处理函数, 事件列表, 所属空间完整路径)；未找到返回 None
        """
        parts = normalize_space_path(name)
        if not parts:
            return None
        space = self.get_command_space(parts[:-1])
        if space is None or parts[-1] not in space.commands:
            # 根空间命令缺失时回退到系统空间，与 execute 的解析规则保持一致
            if space is self.root and parts[-1] in self._system_space.commands:
                function, events = self._system_space.commands[parts[-1]]
                return function, events, self._system_space.full_path()
            return None
        function, events = space.commands[parts[-1]]
        return function, events, space.full_path()

    def get_command_list(self) -> list[str]:
        """返回全部命令的完整路径列表（根空间的命令只显示命令名）"""
        commands: list[str] = []
        for space in self.root.iter_spaces():
            prefix = "" if space.is_root else f"{space.full_path()}/"
            commands.extend(f"{prefix}{name}" for name in space.commands)
        return commands

    def modify_command(self, name: str, old_name: str) -> None:
        """
        重命名一条命令（在同一空间内）。

        :param name:     新的命令名称（可带空间路径，缺省同旧命令所在空间）
        :param old_name: 旧的命令完整路径
        """
        new_parts = normalize_space_path(name)
        old_parts = normalize_space_path(old_name)
        if not new_parts or not old_parts:
            raise CommandNotFoundError("命令名称不能为空")

        old_space = self.require_command_space(old_parts[:-1])
        old_key = old_parts[-1]
        if old_key not in old_space.commands:
            raise CommandNotFoundError(
                f"命令 {old_space.full_path()}/{old_key} 未找到",
                details={"command": f"{old_space.full_path()}/{old_key}"}
            )

        # 只给了新名字（无路径）时沿用原空间
        new_space = self.require_command_space(new_parts[:-1]) if len(new_parts) > 1 else old_space
        new_key = new_parts[-1]
        if new_key in new_space.commands and not (new_space is old_space and new_key == old_key):
            raise DuplicateCommandNamesError(
                f"命令 {new_space.full_path()}/{new_key} 已被注册",
                details={"command": f"{new_space.full_path()}/{new_key}"}
            )

        function, events = old_space.commands.pop(old_key)
        new_space.commands[new_key] = (function, events)

    def migration_command(self, name: str, commandspace: SpacePath) -> None:
        """
        迁移一条命令到新的命令空间（空间不存在则自动创建）。

        :param name:         命令在原空间中的完整路径
        :param commandspace: 目标命令空间路径
        """
        parts = normalize_space_path(name)
        if not parts:
            raise CommandNotFoundError("命令名称不能为空")
        old_space = self.require_command_space(parts[:-1])
        key = parts[-1]
        if key not in old_space.commands:
            raise CommandNotFoundError(
                f"命令 {old_space.full_path()}/{key} 未找到",
                details={"command": f"{old_space.full_path()}/{key}"}
            )
        new_space = self._ensure_space(commandspace)
        if key in new_space.commands:
            raise DuplicateCommandNamesError(
                f"命令 {new_space.full_path()}/{key} 已被注册",
                details={"command": f"{new_space.full_path()}/{key}"}
            )
        new_space.commands[key] = old_space.commands.pop(key)

    def delete_command(self, name: SpacePath) -> None:
        """按完整路径删除一条命令"""
        parts = normalize_space_path(name)
        if not parts:
            raise CommandNotFoundError("命令名称不能为空")
        space = self.require_command_space(parts[:-1])
        space.remove_command(parts[-1])

    # ------------------------------------------------------------------
    # 命令分发
    # ------------------------------------------------------------------
    def resolve(self, tokens: Sequence[str]) -> tuple[CommandSpace, str, list[str]]:
        """
        解析用户输入的 token 序列，定位到目标命令。

        规则：逐层优先匹配子空间；空间匹配结束后，剩余第一个 token 视为命令名，
        其后全部作为参数；根空间内找不到命令时回退到 system 空间。

        :return: (所属空间, 命令名, 参数列表)
        :raises CommandSpaceDepthExceededError: 空间嵌套超过上限
        :raises CommandNotFoundError: 缺少命令名或命令不存在
        """
        if not tokens:
            raise CommandNotFoundError("输入为空，请指定要执行的命令")

        space, index = self._resolve_space(tokens)
        if index >= len(tokens):
            raise CommandNotFoundError(
                f"缺少命令名：{'/'.join(tokens)} 只是命令空间",
                details={"space": space.full_path()}
            )

        name, args = tokens[index], list(tokens[index + 1:])
        target = space
        if name not in target.commands and target is self.root and name in self._system_space.commands:
            # 系统命令回落：default 空间中查找失败时，允许裸调用 system 空间的命令
            target = self._system_space
        if name not in target.commands:
            raise CommandNotFoundError(
                f"命令 {target.full_path()}/{name} 未找到，输入 help 查看可用命令",
                details={"command": f"{target.full_path()}/{name}"}
            )
        return target, name, args

    def _resolve_space(self, tokens: Sequence[str]) -> tuple[CommandSpace, int]:
        """沿 token 序列尽可能深地向下匹配子空间，返回 (最终空间, 下一个待处理下标)"""
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
                    f"命令空间嵌套层数超过上限 {MAX_COMMAND_SPACE_DEPTH}",
                    details={"space": node.full_path(), "depth": node.depth(), "max_depth": MAX_COMMAND_SPACE_DEPTH}
                )
        return node, index

    def execute(self, tokens: Sequence[str]):
        """
        根据用户输入的 token 列表查找并执行命令。

        支持形式：
            ["命令", "参数", ...]                     —— 根空间（default）命令，裸调用
            ["空间1", "命令", "参数", ...]              —— 单层空间
            ["空间1", "空间2", "空间3", "命令", "参数"] —— 多层嵌套空间
        """
        space, name, args = self.resolve(tokens)
        function, events = space.commands[name]
        self._invoke(f"{space.full_path()}/{name}" if not space.is_root else name, function, events, args)

    def _invoke(self, path: str, function: Callable, events: list[str], args: list[str]):
        """
        执行单个命令：先广播该命令声明的前置事件，再调用函数本体。

        命令函数抛出的框架异常原样上抛；其他异常统一包装为 CommandExecutionError，
        并保留原始异常在 __cause__ 中，便于排查。
        """
        for event in events:
            bus.emit(event, path, args)
        try:
            function(*args)
        except XDclassmateCLIException:
            raise
        except Exception as error:
            raise CommandExecutionError(
                f"命令 {path} 执行失败: {error}", details={"command": path}
            ) from error

    # ------------------------------------------------------------------
    # 系统命令：全部注册在 system 空间，可在根空间裸调用
    # ------------------------------------------------------------------
    def _register_system_commands(self):
        """注册 CLI 内置系统命令。"""

        def cmd_help(*command_path):
            """help [命令路径]：不带参数时列出命令空间树；带参数时显示单个命令详情。"""
            if command_path:
                info = self.get_command(list(command_path))
                if not info:
                    print(f"命令 {' '.join(command_path)} 不存在，输入 help 查看全部命令")
                    return
                function, events, space = info
                print(f"命令:   {' '.join(command_path)}")
                print(f"空间:   {space}")
                print(f"事件:   {', '.join(events) if events else '(无)'}")
                print(f"说明:   {getattr(function, '__doc__', None) or '(无文档)'}")
                return

            print(f"XDclassmate-CLI v{CLI_VERSION} — 命令空间树")
            print(f"（嵌套上限 {MAX_COMMAND_SPACE_DEPTH} 层；default 为根空间，其命令可裸调用）")
            print("-" * 60)
            self._print_space(self.root)
            print("-" * 60)
            print("用法: [<空间>...] <命令> [参数...]；REPL 中输入 exit/quit 退出")

        def cmd_plugins():
            """plugins：列出当前已加载的全部插件信息。"""
            # 延迟导入避免命令模块先于插件模块初始化
            from .plugins import pl
            plugins = pl.get_plugin_list()
            if not plugins:
                print("当前没有已加载的插件")
                return
            print(f"已加载 {len(plugins)} 个插件:")
            print("-" * 60)
            for name, meta in plugins.items():
                print(f"  {name} v{meta.get('version')} (cli {meta.get('cli_version')}) by {meta.get('author')}")
                print(f"    {meta.get('description')}")
            print("-" * 60)

        def cmd_echo(*texts):
            """echo <文本...>：原样输出给定文本。"""
            print(" ".join(texts))

        def cmd_clear():
            """clear：清空终端屏幕（ANSI 转义序列）。"""
            # \033[2J 清屏，\033[H 光标回到左上角
            print("\033[2J\033[H", end="")

        self.register("help", cmd_help, commandspace=SYSTEM_SPACE)
        self.register("plugins", cmd_plugins, commandspace=SYSTEM_SPACE)
        self.register("echo", cmd_echo, commandspace=SYSTEM_SPACE)
        self.register("clear", cmd_clear, commandspace=SYSTEM_SPACE)

    def _print_space(self, space: CommandSpace, indent: int = 0):
        """递归打印命令空间树（供 help 使用）"""
        pad = "  " * indent
        print(f"{pad}[{space.full_path()}]")
        for name, (function, _events) in space.commands.items():
            doc = getattr(function, "__doc__", None)
            summary = (doc or "(无说明)").splitlines()[0]
            print(f"{pad}  {name:<12} {summary}")
        for child in space.children.values():
            self._print_space(child, indent + 1)


# 全局命令注册表单例：导入 core.command 即完成实例化并注册系统命令
registry = CommandRegistry()
