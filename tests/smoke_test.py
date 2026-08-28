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
    7. 异常体系（错误码与继承关系）
    8. 目录插件与 .xdplug 压缩包插件的加载、hash 校验
"""
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

# 允许从任意目录运行：把项目根目录加入模块搜索路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.command import MAX_COMMAND_SPACE_DEPTH, CommandRegistry, CommandSpace, normalize_space_path
from core.exceptions import (
    CommandExecutionError,
    CommandNotFoundError,
    CommandSpaceDepthExceededError,
    CommandSpaceNotFoundError,
    DuplicateCommandNamesError,
    DuplicateCommandSpaceNamesError,
    XDclassmateCLIException,
)
from core.plugins import Plugins, _content_hash

# 记录执行结果
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
    """断言 action(*args, **kwargs) 抛出指定类型的异常，返回捕获到的异常（未抛出则返回 None）。"""
    try:
        action(*args, **kwargs)
    except exception_type as error:
        check(name, True)
        return error
    except Exception as error:  # noqa: BLE001 —— 抛出了非预期类型也算失败
        check(name, False, f"(实际抛出 {type(error).__name__}: {error})")
        return None
    check(name, False, "(未抛出异常)")
    return None


def make_printer(buffer: list[str], tag: str = ""):
    """生成一个把调用信息写入 buffer 的命令函数，便于断言调用结果。"""

    def command(*args):
        buffer.append((tag, list(args)))

    command.__doc__ = f"{tag} 的测试命令"
    return command


# ==================================================================
def test_normalize_path():
    print("1. 空间路径归一化")
    check("None -> 根空间", normalize_space_path(None) == [])
    check("空字符串 -> 根空间", normalize_space_path("") == [])
    check("default -> 根空间", normalize_space_path("default") == [])
    check("斜杠分隔", normalize_space_path("space1/space2") == ["space1", "space2"])
    check("空格分隔", normalize_space_path("space1 space2") == ["space1", "space2"])
    check("序列形式", normalize_space_path(["space1", "space2"]) == ["space1", "space2"])
    check("default 前缀被剥离", normalize_space_path("default/space1") == ["space1"])


def test_default_space_and_system_fallback():
    print("2. 默认空间与系统命令回退")
    registry = CommandRegistry()
    buffer: list[str] = []
    registry.register("hello", make_printer(buffer, "default/hello"))
    registry.execute(["hello", "a"])
    check("根空间命令可裸调用", buffer == [("default/hello", ["a"])], f"实际 {buffer}")

    space, name, args = registry.resolve(["help"])
    check("根空间缺失时回退 system 空间", space.full_path() == "system" and name == "help",
          f"实际 {space.full_path()}/{name}")


def test_nested_spaces():
    print("3. 多层嵌套命令空间")
    registry = CommandRegistry()
    buffer: list[str] = []
    registry.register_command_space("space1/space2/space3")
    registry.register("command1", make_printer(buffer, "space1"), commandspace="space1")
    registry.register("command1", make_printer(buffer, "space1/space2"), commandspace="space1/space2")
    registry.register("command1", make_printer(buffer, "space1/space2/space3"), commandspace="space1/space2/space3")

    registry.execute(["space1", "command1"])
    registry.execute(["space1", "space2", "command1"])
    registry.execute(["space1", "space2", "space3", "command1", "--opt", "1"])
    check("三层嵌套解析正确", buffer == [
        ("space1", []),
        ("space1/space2", []),
        ("space1/space2/space3", ["--opt", "1"]),
    ], f"实际 {buffer}")

    check("空间列表包含三层路径", "space1/space2/space3" in registry.get_command_space_list(),
          f"实际 {registry.get_command_space_list()}")


def test_duplicate_names():
    print("4. 重名规则")
    registry = CommandRegistry()
    registry.register_command_space("space1")
    registry.register("dup", make_printer([], "default"))
    registry.register("dup", make_printer([], "space1"), commandspace="space1")
    check("跨空间允许重名", len([c for c in registry.get_command_list() if c.endswith("dup")]) == 2)
    expect_error("同空间重名被拒绝", DuplicateCommandNamesError,
                 registry.register, "dup", make_printer([], "again"))
    expect_error("重复创建同级空间被拒绝", DuplicateCommandSpaceNamesError,
                 registry.register_command_space, "space1")


def test_depth_limit():
    print(f"5. 嵌套上限（MAX_COMMAND_SPACE_DEPTH = {MAX_COMMAND_SPACE_DEPTH}）")
    registry = CommandRegistry()
    # 恰好创建到上限层数应当成功
    deepest = registry.register_command_space([f"s{i}" for i in range(1, MAX_COMMAND_SPACE_DEPTH + 1)])
    check(f"允许创建 {MAX_COMMAND_SPACE_DEPTH} 层", deepest.depth() == MAX_COMMAND_SPACE_DEPTH,
          f"实际深度 {deepest.depth()}")
    # 第 21 层应当被拒绝
    expect_error("超过上限创建空间被拒绝", CommandSpaceDepthExceededError,
                 deepest.add_child, "s21")
    # 正常解析 20 层空间的命令不受影响
    registry.register("command1", make_printer([], "deep"), commandspace=[f"s{i}" for i in range(1, 21)])
    registry.execute([f"s{i}" for i in range(1, 21)] + ["command1"])

    # 绕过 add_child 直接构造一条超限空间链，验证解析阶段的兜底保护仍然生效
    node = registry.root
    for index in range(1, MAX_COMMAND_SPACE_DEPTH + 3):
        child = CommandSpace(f"m{index}", parent=node)
        node.children[f"m{index}"] = child
        node = child
    expect_error("解析时超过上限被拒绝", CommandSpaceDepthExceededError,
                 registry._resolve_space, [f"m{i}" for i in range(1, MAX_COMMAND_SPACE_DEPTH + 3)])


def test_command_crud():
    print("6. 命令增删改与迁移")
    registry = CommandRegistry()
    buffer: list[str] = []
    registry.register("old", make_printer(buffer, "renamed"))
    registry.modify_command("new", "old")
    check("重命名后旧名不可用", registry.get_command("old") is None)
    registry.execute(["new"])
    check("重命名后新名可用", buffer == [("renamed", [])], f"实际 {buffer}")

    registry.migration_command("new", "space9")
    check("迁移后进入新空间", registry.get_command("new") is None
          and registry.get_command("space9/new") is not None)

    registry.delete_command("space9/new")
    check("删除命令生效", registry.get_command("space9/new") is None)
    expect_error("删除不存在的空间报错", CommandSpaceNotFoundError,
                 registry.delete_command_space, "not-exist")


def test_errors():
    print("7. 异常体系")
    registry = CommandRegistry()
    registry.register_command_space("space1")
    expect_error("只给空间名缺命令名", CommandNotFoundError, registry.execute, ["space1"])
    expect_error("未知命令", CommandNotFoundError, registry.execute, ["nope"])
    expect_error("空输入", CommandNotFoundError, registry.execute, [])

    def boom():
        raise ValueError("内部错误")

    registry.register("boom", boom)
    error = expect_error("命令函数异常被包装", CommandExecutionError, registry.execute, ["boom"])
    check("原始异常保留在 __cause__", error is not None and isinstance(error.__cause__, ValueError))

    check("错误码格式正确", all(
        exception.code.startswith("XD-CLI-") for exception in (
            CommandNotFoundError(), CommandSpaceDepthExceededError(), CommandExecutionError(),
        )))
    check("异常均继承基类", all(
        issubclass(cls, XDclassmateCLIException) for cls in (
            CommandNotFoundError, CommandSpaceNotFoundError, DuplicateCommandNamesError,
            CommandSpaceDepthExceededError, CommandExecutionError,
        )))
    check("异常文本包含错误码", str(CommandNotFoundError("找不到命令")).startswith("[XD-CLI-3001]"))


def test_plugin_loading():
    print("8. 目录插件与 .xdplug 压缩包插件加载")
    manifest_name = "xdclassmate.cli.setting.json"

    with tempfile.TemporaryDirectory(prefix="xd-cli-test-") as temp:
        temp_root = Path(temp)
        source = temp_root / "demo"
        source.mkdir()

        # 构造一个最小插件：入口函数在 plugin_init 时注册一条命令
        (source / "demo.py").write_text(
            "from core.command import registry\n"
            "def main():\n"
            "    registry.register('demo-cmd', lambda *a: print('demo', a))\n",
            encoding="utf-8"
        )
        digest = _content_hash(source)
        (source / manifest_name).write_text(
            "{" + f'"name": "demo", "entry": "demo.py:main", "version": "1.0.0", '
            f'"author": "test", "cli_version": "1.0", "description": "打包测试插件", '
            f'"events": ["plugin_init"], "pre_plugins": {{}}, "hash": "{digest}"' + "}",
            encoding="utf-8"
        )

        # 8.1 目录插件
        plugins = Plugins(plugins_dir=str(temp_root))
        check("目录插件被加载", plugins.get("demo") is not None)
        check("清单字段被解析", plugins.get("demo")["version"] == "1.0.0")

        # 8.2 打包为 .xdplug 后再用独立目录加载
        archive = temp_root / "build" / "demo.xdplug"
        archive.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    package.write(path, path.relative_to(source).as_posix())

        archive_root = temp_root / "archive-only"
        archive_root.mkdir()
        archive.rename(archive_root / "demo.xdplug")
        plugins = Plugins(plugins_dir=str(archive_root))
        check("压缩包插件被加载", plugins.get("demo") is not None)

        # 8.3 hash 不匹配时拒绝加载（不抛异常，插件被跳过）
        (source / "demo.py").write_text(
            "from core.command import registry\n"
            "def main():\n    pass\n# 篡改\n",
            encoding="utf-8"
        )
        tampered_root = temp_root / "tampered"
        tampered_root.mkdir()
        tampered = tampered_root / "demo"
        tampered.mkdir()
        (tampered / "demo.py").write_text("# 与原 hash 不一致\n", encoding="utf-8")
        (tampered / manifest_name).write_text(
            (source / manifest_name).read_text(encoding="utf-8"), encoding="utf-8"
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
        test_errors,
        test_plugin_loading,
    ):
        test()
    print("=" * 60)
    print(f"通过 {_passed} 项，失败 {_failed} 项")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
