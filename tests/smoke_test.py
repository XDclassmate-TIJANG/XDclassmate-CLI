"""XD-CLI 核心冒烟测试（仅使用 Python 标准库，无需 pytest）。

运行方式：
    py -3 tests/smoke_test.py

覆盖范围：
    1. 空间路径归一化
    2. 默认空间裸调用与系统命令回退
    3. 多层嵌套空间解析（space1 space2 space3 command1 [参数...]）
    4. 跨空间重名允许 / 同空间重名拒绝
    5. 命令空间嵌套上限（MAX_COMMAND_SPACE_DEPTH = 20）
    6. 命令增删改与迁移
    7. 命令选项（Option）的注册与解析
    8. 视图渲染（list / tree / table 三种主题）
    9. 异常体系（错误码与继承关系）
    10. 日志系统（级别解析与命名空间）
    11. 目录插件与 .xdplug 压缩包插件的加载、hash 校验
"""
from __future__ import annotations

import logging
import sys
import tempfile
import zipfile
from pathlib import Path

# 允许从任意目录运行：把项目根目录加入模块搜索路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.builtins import register_system_commands  # noqa: E402
from core.command import (  # noqa: E402
    MAX_COMMAND_SPACE_DEPTH,
    CommandRegistry,
    CommandSpace,
    normalize_space_path,
)
from core.exceptions import (  # noqa: E402
    CommandArgumentException,
    CommandExecutionError,
    CommandNotFoundError,
    CommandSpaceDepthExceededError,
    CommandSpaceNotFoundError,
    DuplicateCommandNamesError,
    DuplicateCommandSpaceNamesError,
    DuplicateOptionNamesError,
    XDclassmateCLIException,
)
from core.logger import (  # noqa: E402
    LOG_PREFIX,
    get_logger,
    setup_logging,
)
from core.plugins import Plugins, _content_hash  # noqa: E402
from core.views import (  # noqa: E402
    THEME_LIST,
    THEME_TABLE,
    THEME_TREE,
    display_width,
    render,
)

# 统计结果
_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    """断言式检查：打印结果并统计通过/失败数量。"""
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [通过] {name}")
    else:
        _failed += 1
        print(f"  [失败] {name} {detail}")


def expect_error(name: str, exception_type: type, action, *args, **kwargs):
    """断言 action 抛出指定异常，返回捕获到的异常（未抛出返回 None）。"""
    try:
        action(*args, **kwargs)
    except exception_type as error:
        check(name, True)
        return error
    except Exception as error:  # noqa: BLE001 —— 抛出非预期类型也算失败
        check(name, False, f"(实际抛出 {type(error).__name__}: {error})")
        return None
    check(name, False, "(未抛出异常)")
    return None


def make_recorder(buffer: list, tag: str = ""):
    """生成把调用信息写入 buffer 的命令函数，便于断言。"""

    def command(*args, **kwargs):
        buffer.append((tag, list(args), dict(kwargs)))

    command.__doc__ = f"{tag} 的测试命令"
    return command


# ==================================================================
def test_normalize_path():
    print("1. 空间路径归一化")
    check("None -> 根空间", normalize_space_path(None) == [])
    check("空字符串 -> 根空间", normalize_space_path("") == [])
    check("default -> 根空间", normalize_space_path("default") == [])
    check("斜杠分隔", normalize_space_path("a/b") == ["a", "b"])
    check("空格分隔", normalize_space_path("a b") == ["a", "b"])
    check("序列形式", normalize_space_path(["a", "b"]) == ["a", "b"])
    check("default 前缀被剥离", normalize_space_path("default/a") == ["a"])


def test_default_space_and_system_fallback():
    print("2. 默认空间与系统命令回退")
    registry = CommandRegistry()
    # 微内核架构下系统命令不再随 CommandRegistry 构造自动注册，
    # 需要像 core.kernel 启动路径一样显式注册（内置命令即内置插件）
    register_system_commands(registry)
    buffer: list = []
    registry.register("hello", make_recorder(buffer, "default/hello"))
    registry.execute(["hello", "a"])
    check(
        "根空间命令可裸调用",
        buffer == [("default/hello", ["a"], {})],
        f"实际 {buffer}"
    )

    space, name, _args = registry.resolve(["help"])
    check(
        "根空间缺失时回退 system 空间",
        space.full_path() == "system" and name == "help",
        f"实际 {space.full_path()}/{name}"
    )


def test_nested_spaces():
    print("3. 多层嵌套命令空间")
    registry = CommandRegistry()
    buffer: list = []
    registry.register_command_space("space1/space2/space3")
    registry.register(
        "command1", make_recorder(buffer, "s1"), commandspace="space1"
    )
    registry.register(
        "command1",
        make_recorder(buffer, "s1/s2"),
        commandspace="space1/space2",
    )
    registry.register(
        "command1",
        make_recorder(buffer, "s1/s2/s3"),
        commandspace="space1/space2/space3",
    )

    registry.execute(["space1", "command1"])
    registry.execute(["space1", "space2", "command1"])
    registry.execute(["space1", "space2", "space3", "command1", "--opt", "1"])
    check(
        "三层嵌套解析正确",
        buffer == [
            ("s1", [], {}),
            ("s1/s2", [], {}),
            ("s1/s2/s3", ["--opt", "1"], {}),
        ],
        f"实际 {buffer}"
    )
    check(
        "空间列表包含三层路径",
        "space1/space2/space3" in registry.get_command_space_list()
    )


def test_duplicate_names():
    print("4. 重名规则")
    registry = CommandRegistry()
    registry.register_command_space("space1")
    registry.register("dup", make_recorder([], "default"))
    registry.register(
        "dup", make_recorder([], "space1"), commandspace="space1"
    )
    check(
        "跨空间允许重名",
        len([c for c in registry.get_command_list() if c.endswith("dup")]) == 2
    )
    expect_error(
        "同空间重名被拒绝",
        DuplicateCommandNamesError,
        registry.register, "dup", make_recorder([], "again")
    )
    expect_error(
        "重复创建同级空间被拒绝",
        DuplicateCommandSpaceNamesError,
        registry.register_command_space, "space1"
    )


def test_depth_limit():
    print(f"5. 嵌套上限（MAX_COMMAND_SPACE_DEPTH = "
          f"{MAX_COMMAND_SPACE_DEPTH}）")
    registry = CommandRegistry()
    deepest = registry.register_command_space(
        [f"s{i}" for i in range(1, MAX_COMMAND_SPACE_DEPTH + 1)]
    )
    check(
        f"允许创建 {MAX_COMMAND_SPACE_DEPTH} 层",
        deepest.depth() == MAX_COMMAND_SPACE_DEPTH,
        f"实际深度 {deepest.depth()}"
    )
    expect_error(
        "超过上限创建空间被拒绝",
        CommandSpaceDepthExceededError,
        deepest.add_child, "s21"
    )

    # 绕过 add_child 构造超限空间链，验证解析阶段的兜底保护
    node = registry.root
    for index in range(1, MAX_COMMAND_SPACE_DEPTH + 3):
        child = CommandSpace(f"m{index}", parent=node)
        node.children[f"m{index}"] = child
        node = child
    expect_error(
        "解析时超过上限被拒绝",
        CommandSpaceDepthExceededError,
        registry._resolve_space,
        [f"m{i}" for i in range(1, MAX_COMMAND_SPACE_DEPTH + 3)]
    )


def test_command_crud():
    print("6. 命令增删改与迁移")
    registry = CommandRegistry()
    buffer: list = []
    registry.register("old", make_recorder(buffer, "renamed"))
    registry.modify_command("new", "old")
    check("重命名后旧名不可用", registry.get_command("old") is None)
    registry.execute(["new"])
    check(
        "重命名后新名可用",
        buffer == [("renamed", [], {})],
        f"实际 {buffer}"
    )

    registry.migration_command("new", "space9")
    check(
        "迁移后进入新空间",
        registry.get_command("new") is None
        and registry.get_command("space9/new") is not None
    )
    registry.delete_command("space9/new")
    check("删除命令生效", registry.get_command("space9/new") is None)
    expect_error(
        "删除不存在的空间报错",
        CommandSpaceNotFoundError,
        registry.delete_command_space, "not-exist"
    )


def test_options():
    print("7. 命令选项（Option）")
    registry = CommandRegistry()
    buffer: list = []

    def cmd_greet(*args, name="world", loud=False):
        buffer.append((list(args), name, loud))

    cmd_greet.__doc__ = "greet 测试命令"

    entry = registry.register("greet", cmd_greet)
    registry.register_option(
        entry, "-n", "--name", takes_value=True, default="world", help="名字"
    )
    registry.register_option(entry, "-l", "--loud", help="是否大写")

    registry.execute(["greet"])
    registry.execute(["greet", "-n", "XD"])
    registry.execute(["greet", "--name=CLI"])
    registry.execute(["greet", "--loud"])
    registry.execute(["greet", "extra", "-l"])
    registry.execute(["greet", "--", "-not-option"])

    expected = [
        ([], "world", False),
        ([], "XD", False),
        ([], "CLI", False),
        ([], "world", True),
        (["extra"], "world", True),
        (["-not-option"], "world", False),
    ]
    check("选项解析结果正确", buffer == expected, f"实际 {buffer}")
    check("选项去重后数量正确", len(registry.get_command_options("greet")) == 2)

    option_names = [
        option.dest for option in registry.get_command_options("greet")
    ]
    check("dest 由长选项推导", sorted(option_names) == ["loud", "name"])

    expect_error(
        "未知选项被拒绝",
        CommandArgumentException,
        registry.execute, ["greet", "--bogus"]
    )
    expect_error(
        "选项缺少取值被拒绝",
        CommandArgumentException,
        registry.execute, ["greet", "-n"]
    )
    expect_error(
        "开关选项不接受取值",
        CommandArgumentException,
        registry.execute, ["greet", "--loud=1"]
    )
    expect_error(
        "重复选项名被拒绝",
        DuplicateOptionNamesError,
        registry.register_option, entry, "-n", "--nick"
    )

    # 未注册选项的命令保持宽松：选项原样作为位置参数
    plain: list = []
    registry.register("plain", make_recorder(plain, "plain"))
    registry.execute(["plain", "--raw", "1"])
    check(
        "未声明选项的命令原样接收参数",
        plain == [("plain", ["--raw", "1"], {})],
        f"实际 {plain}"
    )


def test_views():
    print("8. 视图渲染（theme）")
    registry = CommandRegistry()
    # 同上：系统命令需显式注册，视图才有 [system] 分组可渲染
    register_system_commands(registry)
    registry.register("hello", make_recorder([], "hello"))
    registry.register_command_space("space1/space2")
    registry.register(
        "command1", make_recorder([], "deep"), commandspace="space1/space2"
    )
    command_count = len(registry.get_command_list())

    list_lines = render(registry.root, theme=THEME_LIST, width=60)
    tree_lines = render(registry.root, theme=THEME_TREE, width=60)
    table_lines = render(registry.root, theme=THEME_TABLE, width=60)

    check("list 视图按空间分组", "[space1/space2]" in list_lines)
    check(
        "list 视图包含根空间分组",
        "[default]" in list_lines and "[system]" in list_lines
    )
    check(
        "tree 视图包含连接线",
        any(line[:1] in ("├", "└", "|", "`") for line in tree_lines)
    )
    check("tree 视图首行为根空间", tree_lines[0] == "default")
    check("table 视图包含表头", table_lines[0].startswith("空间"))
    check(
        "table 行数正确",
        len(table_lines) == command_count + 2,
        f"实际 {len(table_lines)} 行 / {command_count} 条命令"
    )
    check(
        "宽度受限时按显示宽度截断",
        all(display_width(line) <= 60 for line in table_lines)
    )
    expect_error(
        "未知主题被拒绝",
        CommandArgumentException,
        render, registry.root, "unknown"
    )


def test_errors():
    print("9. 异常体系")
    registry = CommandRegistry()
    registry.register_command_space("space1")
    expect_error(
        "只给空间名缺命令名", CommandNotFoundError,
        registry.execute, ["space1"]
    )
    expect_error("未知命令", CommandNotFoundError, registry.execute, ["nope"])
    expect_error("空输入", CommandNotFoundError, registry.execute, [])

    def boom():
        raise ValueError("内部错误")

    registry.register("boom", boom)
    error = expect_error(
        "命令函数异常被包装", CommandExecutionError,
        registry.execute, ["boom"]
    )
    check(
        "原始异常保留在 __cause__",
        error is not None and isinstance(error.__cause__, ValueError)
    )
    check(
        "错误码格式正确",
        all(
            exception.code.startswith("XD-CLI-")
            for exception in (
                CommandNotFoundError(), CommandExecutionError(),
                CommandArgumentException(),
            )
        )
    )
    check(
        "异常均继承基类",
        all(
            issubclass(cls, XDclassmateCLIException) for cls in (
                CommandNotFoundError, CommandSpaceNotFoundError,
                DuplicateCommandNamesError, CommandExecutionError,
                CommandArgumentException, DuplicateOptionNamesError,
            )
        )
    )
    check(
        "异常文本包含错误码",
        str(CommandNotFoundError("找不到命令")).startswith("[XD-CLI-3001]")
    )


def test_logger():
    print("10. 日志系统")
    logger = setup_logging(level="DEBUG")
    check("级别名称解析为 DEBUG", logger.level == logging.DEBUG)
    logger = setup_logging(level="NOT_A_LEVEL")
    check("非法级别回退 INFO", logger.level == logging.INFO)

    demo = get_logger("demo")
    check("命名空间前缀正确", demo.name == f"{LOG_PREFIX}.demo")
    check(
        "日志器归属项目命名空间",
        demo.parent is logging.getLogger(LOG_PREFIX)
    )
    setup_logging(level="INFO")


def test_plugin_loading():
    print("11. 目录插件与 .xdplug 压缩包插件加载")
    manifest_name = "xdclassmate.cli.setting.json"
    module_source = (
        "from core.command import registry\n"
        "def main():\n"
        "    registry.register('demo-cmd', lambda *a: print('demo', a))\n"
    )

    with tempfile.TemporaryDirectory(prefix="xd-cli-test-") as temp:
        temp_root = Path(temp)
        source = temp_root / "demo"
        source.mkdir()
        (source / "demo.py").write_text(module_source, encoding="utf-8")
        digest = _content_hash(source)
        (source / manifest_name).write_text(
            '{"name": "demo", "entry": "demo.py:main", '
            '"version": "1.0.0", "author": "test", "cli_version": "1.0", '
            '"description": "打包测试插件", "events": ["plugin_init"], '
            f'"pre_plugins": {{}}, "hash": "{digest}"}}',
            encoding="utf-8"
        )

        # 11.1 目录插件
        plugins = Plugins(plugins_dir=str(temp_root))
        check("目录插件被加载", plugins.get("demo") is not None)
        check("清单字段被解析", plugins.get("demo")["version"] == "1.0.0")

        # 11.2 打包为 .xdplug 后在独立目录加载
        archive_root = temp_root / "archive-only"
        archive_root.mkdir()
        archive = archive_root / "demo.xdplug"
        with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    package.write(path, path.relative_to(source).as_posix())
        plugins = Plugins(plugins_dir=str(archive_root))
        check("压缩包插件被加载", plugins.get("demo") is not None)

        # 11.3 hash 不匹配时拒绝加载（不抛异常，插件被跳过）
        tampered_root = temp_root / "tampered"
        tampered = tampered_root / "demo"
        tampered.mkdir(parents=True)
        (tampered / "demo.py").write_text("# 与原 hash 不一致\n", encoding="utf-8")
        (tampered / manifest_name).write_text(
            (source / manifest_name).read_text(encoding="utf-8"),
            encoding="utf-8"
        )
        plugins = Plugins(plugins_dir=str(tampered_root))
        check("hash 不匹配时拒绝加载", plugins.get("demo") is None)


def main() -> int:
    print(f"XD-CLI 冒烟测试（Python {sys.version.split()[0]}）")
    print("=" * 60)
    for test in (
        test_normalize_path,
        test_default_space_and_system_fallback,
        test_nested_spaces,
        test_duplicate_names,
        test_depth_limit,
        test_command_crud,
        test_options,
        test_views,
        test_errors,
        test_logger,
        test_plugin_loading,
    ):
        test()
    print("=" * 60)
    print(f"通过 {_passed} 项，失败 {_failed} 项")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
