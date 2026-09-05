"""XD-CLI 集成测试：覆盖 P0/P1/P2 修复点的端到端行为。

与 smoke_test 的关系：
    * smoke_test 覆盖核心数据结构与单元行为（无需文件系统外的副作用）
    * integration_test 覆盖修复点（退出码、插件 i18n、打包链、安全加固），
      任何一个失败都意味着「修复后跑回归」。

运行方式：
    py -3 tests/integration_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.command import (  # noqa: E402
    EXIT_COMMAND_ERROR,
    EXIT_FRAMEWORK_ERROR,
    EXIT_OK,
    CommandRegistry,
    coerce_exit_code,
)
from core.kernel import Kernel  # noqa: E402
from core.plugins import strip_plugin_dir_placeholder  # noqa: E402
from core.remote import (  # noqa: E402
    ARCHIVE_MAX_COMPRESSED_BYTES,
    ARCHIVE_MAX_ENTRIES,
    ARCHIVE_MAX_UNCOMPRESSED_BYTES,
    SUPPORTED_ALGORITHMS,
    _validate_relative_path,
)

# ----------------------------------------------------------------------
# 统计
# ----------------------------------------------------------------------
_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [通过] {name}")
    else:
        _failed += 1
        print(f"  [失败] {name} {detail}")


def run_cli(*args: str, cwd: Path | None = None) -> tuple[int, str, str]:
    """以子进程方式跑 ``python -m core.main``，返回 (退出码, stdout, stderr)。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.run(
        [sys.executable, "-m", "core.main", *args],
        cwd=str(cwd or PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return process.returncode, process.stdout, process.stderr


# ----------------------------------------------------------------------
# 1. 退出码约定
# ----------------------------------------------------------------------
def test_exit_codes():
    print("1. 退出码约定")
    code, _, _ = run_cli("help")
    check("help 退出码 0", code == EXIT_OK, f"实际 {code}")

    code, _, _ = run_cli("nosuch_command")
    check("未知命令退出码 1", code == EXIT_FRAMEWORK_ERROR, f"实际 {code}")

    # 命令函数返回 False → 1；返回 None/True → 0；返回 7 → 7
    cases = [
        (None, EXIT_OK),
        (True, EXIT_OK),
        (False, EXIT_FRAMEWORK_ERROR),
        (0, EXIT_OK),
        (7, 7),
        (300, 255),  # POSIX 裁剪
        ("string", EXIT_OK),  # 其它类型宽容
    ]
    for value, expected in cases:
        actual = coerce_exit_code(value)
        check(
            f"coerce_exit_code({value!r}) -> {expected}",
            actual == expected,
            f"实际 {actual}",
        )


# ----------------------------------------------------------------------
# 2. 退出码：命令函数抛异常 → 2
# ----------------------------------------------------------------------
def test_exit_codes_via_kernel():
    print("2. 命令函数抛异常退出码")
    k = Kernel(log_level="CRITICAL")
    k.boot()

    from core.command import registry

    def boom():
        raise RuntimeError("kaboom")

    registry.register("_test_boom", boom)
    code = k.run_once(["_test_boom"])
    check("命令抛异常 → 退出码 2", code == EXIT_COMMAND_ERROR, f"实际 {code}")


# ----------------------------------------------------------------------
# 3. 命令返回值通道（业务可表达失败）
# ----------------------------------------------------------------------
def test_command_return_code():
    print("3. 命令函数返回值通道")
    # 用 *隔离* 注册表避免污染全局 registry（test 2 已用了一次 Kernel）
    from core.command import registry as global_registry
    from core.builtins import register_system_commands

    isolated = CommandRegistry()
    register_system_commands(isolated)

    def succeed():
        return 0

    def fail():
        return 9

    isolated.register("_test_ok", succeed)
    isolated.register("_test_fail", fail)

    check(
        "return 0 → 0",
        coerce_exit_code(isolated.execute(["_test_ok"])) == 0,
    )
    check(
        "return 9 → 9",
        coerce_exit_code(isolated.execute(["_test_fail"])) == 9,
    )


# ----------------------------------------------------------------------
# 4. 插件 i18n 占位符兼容
# ----------------------------------------------------------------------
def test_languages_dir_placeholders():
    print("4. 插件 languages_dir 占位符兼容")
    samples = {
        "languages": "languages",
        "./languages": "languages",
        "{plugin_dir}/languages": "languages",
        "%(plugin_dir)s/languages": "languages",
        "${plugin_dir}/languages": "languages",
        "/languages": "languages",
        "langs/sub": "langs/sub",
        "  ": "languages",  # 空白回退到默认
    }
    for raw, expected in samples.items():
        got = strip_plugin_dir_placeholder(raw)
        check(
            f"strip({raw!r}) -> {expected!r}",
            got == expected,
            f"实际 {got!r}",
        )


# ----------------------------------------------------------------------
# 5. 真实加载 plugins/image（i18n 不应输出裸键）
# ----------------------------------------------------------------------
def test_real_image_plugin_loaded():
    print("5. 真实插件 plugins/image 加载与 i18n")
    # 用子进程跑，避免被同进程前面已构造的 Kernel 污染全局 registry。
    code, stdout, stderr = run_cli("plugins")
    text = (stdout + stderr)
    check(
        "plugins 命令显示 Image Processing",
        "Image Processing" in text,
        f"实际: {text[:300]}",
    )

    # size 命令的中文错误应该是翻译键的解析结果，而不是原样键
    code, stdout, stderr = run_cli("image", "size")
    text = (stdout + stderr).strip()
    check(
        "image size 输出不包含裸翻译键",
        "plugin.image.size.fail" not in text
        and "plugin.image.depend_missing" not in text,
        f"实际: {text}",
    )
    # 业务失败应退 1
    check("image size 缺参数 → 1", code == 1, f"实际 {code}")


# ----------------------------------------------------------------------
# 6. 路径校验（防止 index.json 被劫持）
# ----------------------------------------------------------------------
def test_remote_path_validation():
    print("6. 远端路径字段校验")
    ok = ["image-1.0.0.xdplug", "hashes/image.hash256", "sub/dir/file.txt"]
    bad = [
        ("", "空路径"),
        ("../../etc/passwd", ".. 逃逸"),
        ("/abs/path", "POSIX 绝对路径"),
        ("C:/Windows", "Win32 盘符"),
        ("\\\\server\\share", "UNC 路径"),
        ("./../../escape", "混合逃逸"),
    ]
    for value in ok:
        try:
            got = _validate_relative_path(value, "file")
            check(f"通过: {value!r}", got == value)
        except Exception as error:
            check(f"通过: {value!r}", False, f"被拒: {error}")

    for value, why in bad:
        try:
            _validate_relative_path(value, "file")
            check(f"拒绝: {value!r} ({why})", False, "居然通过了")
        except Exception:
            check(f"拒绝: {value!r} ({why})", True)


# ----------------------------------------------------------------------
# 7. 摘要算法白名单
# ----------------------------------------------------------------------
def test_supported_algorithms():
    print("7. 摘要算法白名单")
    check("白名单非空", len(SUPPORTED_ALGORITHMS) > 0)
    check(
        "sha256 在白名单中",
        "sha256" in SUPPORTED_ALGORITHMS,
    )
    check(
        "非标准算法（blake999）不在白名单中",
        "blake999" not in SUPPORTED_ALGORITHMS,
    )


# ----------------------------------------------------------------------
# 8. 压缩包配额常量
# ----------------------------------------------------------------------
def test_archive_quotas():
    print("8. 压缩包配额常量存在且大小合理")
    check("条目上限 >= 1000", ARCHIVE_MAX_ENTRIES >= 1000)
    check(
        "解压体积上限 >= 100MB",
        ARCHIVE_MAX_UNCOMPRESSED_BYTES >= 100 * 1024 * 1024,
    )
    check(
        "压缩后体积上限 >= 10MB",
        ARCHIVE_MAX_COMPRESSED_BYTES >= 10 * 1024 * 1024,
    )


# ----------------------------------------------------------------------
# 9. plugin_hash → pack → install 全链路
# ----------------------------------------------------------------------
def test_pack_install_roundtrip():
    print("9. 打包→安装全链路回归")
    with tempfile.TemporaryDirectory(prefix="xd-pack-test-") as tmp:
        tmp_root = Path(tmp)
        # 1. 准备一个最小可装插件
        plugin_src = tmp_root / "demo"
        plugin_src.mkdir()
        (plugin_src / "main.py").write_text(
            "from core.command import registry\n"
            "def main():\n"
            "    registry.register(\n"
            "        'demo',\n"
            "        lambda: print('demo loaded'),\n"
            "        commandspace='default',\n"
            "        description='demo',\n"
            "    )\n",
            encoding="utf-8",
        )
        (plugin_src / "xdclassmate.cli.setting.json").write_text(
            json.dumps(
                {
                    "name": "Demo",
                    "entry": "main.py:main",
                    "version": "1.0.0",
                    "author": "test",
                    "cli_version": "1.0.0",
                    "description": "打包测试",
                    "events": ["plugin_init"],
                    "pre_plugins": {},
                    "languages": "languages",
                }
            ),
            encoding="utf-8",
        )

        # 2. 跑 plugin_hash.py 写摘要（用临时 url 写到 tmp_root）
        hash_path = tmp_root / "demo.hash256"
        hash_path.write_text("", encoding="utf-8")
        # 改 manifest 写入 url
        manifest = json.loads(
            (plugin_src / "xdclassmate.cli.setting.json").read_text(
                encoding="utf-8"
            )
        )
        manifest["url"] = str(hash_path)
        (plugin_src / "xdclassmate.cli.setting.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        subprocess.run(
            [
                sys.executable,
                "tools/plugin_hash.py",
                "--emit",
                "-o",
                str(hash_path),
                "--label",
                "demo-1.0.0.xdplug",
                str(plugin_src),
            ],
            check=True,
            cwd=PROJECT_ROOT,
        )
        check("摘要文件已生成", hash_path.is_file() and hash_path.stat().st_size > 0)

        # 3. 打包
        archive = tmp_root / "demo.xdplug"
        subprocess.run(
            [
                sys.executable,
                "tools/pack_plugin.py",
                str(plugin_src),
                "-o",
                str(archive),
            ],
            check=True,
            cwd=PROJECT_ROOT,
        )
        check(".xdplug 已生成", archive.is_file() and archive.stat().st_size > 0)

        # 4. 把 .xdplug 放进 plugins/，重启后应能加载
        plugin_root = tmp_root / "plugins_dir"
        plugin_root.mkdir()
        shutil.copy(archive, plugin_root / "demo.xdplug")

        manager = type("Plugins", (), {})  # 仅占位，下面用真类
        # 真实加载
        from core.plugins import Plugins as RealPlugins  # noqa: E402
        manager = RealPlugins(plugins_dir=str(plugin_root))
        check(
            "压缩包插件被加载（按目录名反查）",
            manager.find_by_path_alias("Demo") is not None
            or manager.find_by_path_alias("demo") is not None,
        )


# ----------------------------------------------------------------------
# 10. 内置插件在自定义系统空间下也能用
# ----------------------------------------------------------------------
def test_system_space_rename():
    print("10. 系统空间改名后内置命令仍可裸调用")
    r = CommandRegistry()
    from core.builtins import register_system_commands
    register_system_commands(r, system_space="core")
    # system_space 改成 core 后 register_option 不应再写死 system/help
    entry = r.get_command_entry(["core", "help"])
    check("core/help 命令存在", entry is not None)
    option = entry.options.get("--theme")
    check("core/help 仍带 --theme 选项", option is not None)


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def main() -> int:
    print(f"XD-CLI 集成测试（Python {sys.version.split()[0]}）")
    print("=" * 60)
    for test in (
        test_exit_codes,
        test_exit_codes_via_kernel,
        test_command_return_code,
        test_languages_dir_placeholders,
        test_real_image_plugin_loaded,
        test_remote_path_validation,
        test_supported_algorithms,
        test_archive_quotas,
        test_pack_install_roundtrip,
        test_system_space_rename,
    ):
        try:
            test()
        except Exception as error:  # noqa: BLE001
            global _failed
            _failed += 1
            print(f"  [失败] {test.__name__} 抛异常: {error}")
    print("=" * 60)
    print(f"通过 {_passed} 项，失败 {_failed} 项")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
