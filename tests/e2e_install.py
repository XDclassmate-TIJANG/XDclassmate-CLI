"""install/upgrade/uninstall 端到端验证（隔离环境，不污染项目）。

用法：py -3 tests/e2e_install.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许从任意目录运行：把项目根目录加入模块搜索路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import tempfile
import zipfile

from core.integrity import content_digest
from core.remote import (
    compare_versions,
    install_package,
    uninstall_package,
    upgrade_package,
)


def build_plugin_dir(root: Path, name: str, version: str) -> Path:
    """合成一个最小插件目录（含清单 + 一个模块）。"""
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "main.py").write_text(
        "def main():\n    print('hi')\n", encoding="utf-8"
    )
    manifest = {
        "name": name,
        "entry": "main.py:main",
        "version": version,
        "author": "tester",
        "cli_version": "1.0",
        "description": "demo",
        "events": ["plugin_init"],
        "pre_plugins": {},
        "url": None,
        "algorithm": "sha256",
    }
    (pdir / "xdclassmate.cli.setting.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return pdir


def build_package(pdir: Path, file_name: str) -> Path:
    """把插件目录打包成 .xdplug（与加载器规则一致）。"""
    out = pdir.parent / file_name
    files = sorted(
        p for p in pdir.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as pkg:
        for path in files:
            pkg.write(path, path.relative_to(pdir).as_posix())
    return out


def main() -> int:
    failures = 0

    def check(label: str, ok: bool, extra: str = "") -> None:
        nonlocal failures
        if ok:
            print(f"  [通过] {label}")
        else:
            failures += 1
            print(f"  [失败] {label} {extra}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        repo = tmp_path / "repo"
        repo.mkdir()
        plugin_root = tmp_path / "plugins_src"
        plugin_dir = tmp_path / "installed"

        # ---- 仓库：demo v1.0.0 ----
        src1 = build_plugin_dir(plugin_root, "demo", "1.0.0")
        pkg1 = build_package(src1, "demo-1.0.0.xdplug")
        digest1 = content_digest(src1)
        # 摘要文件（sha256sum 风格，引用压缩包文件名）
        (repo / "hashes").mkdir(exist_ok=True)
        (repo / "hashes" / "demo.hash256").write_text(
            f"{digest1}  demo-1.0.0.xdplug\n", encoding="utf-8"
        )
        # 同时准备内联 hash 的第二个插件
        src_inline = build_plugin_dir(plugin_root, "inline", "1.0.0")
        pkg_inline = build_package(src_inline, "inline-1.0.0.xdplug")
        digest_inline = content_digest(src_inline)
        pkg1.replace(repo / "demo-1.0.0.xdplug")
        pkg_inline.replace(repo / "inline-1.0.0.xdplug")
        index = {
            "demo": {
                "version": "1.0.0",
                "file": "demo-1.0.0.xdplug",
                "hash": "hashes/demo.hash256",
                "algorithm": "sha256",
            },
            "inline": {
                "version": "1.0.0",
                "file": "inline-1.0.0.xdplug",
                "hash": digest_inline,
            },
        }
        (repo / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        install_url = str(repo)

        # 1) 安装 demo（hash 文件分支）
        result = install_package(install_url, "demo", plugin_dir)
        check("install demo 返回版本",
              result["version"] == "1.0.0", str(result))
        check("demo 落盘到 installed/demo",
              (plugin_dir / "demo" / "xdclassmate.cli.setting.json").is_file())

        # 2) 安装 inline（内联 hash 分支）
        res_inline = install_package(install_url, "inline", plugin_dir)
        check("install inline 成功",
              res_inline["name"] == "inline", str(res_inline))
        check("inline 落盘",
              (plugin_dir / "inline" /
               "xdclassmate.cli.setting.json").is_file())

        # 3) 校验失败：用不同内容重新打包（digest 改变），但索引仍持旧摘要。
        #    注意：install 在落盘前就完成校验，已安装的 demo 目录不受影响。
        tampered_src = build_plugin_dir(plugin_root, "demo", "1.0.0")
        (tampered_src / "main.py").write_text(
            "def main():\n    print('tampered')\n", encoding="utf-8"
        )
        build_package(tampered_src, "demo-1.0.0.xdplug").replace(
            repo / "demo-1.0.0.xdplug"
        )
        from core.exceptions import PluginHashMismatchError
        raised = False
        try:
            install_package(install_url, "demo", plugin_dir)
        except PluginHashMismatchError:
            raised = True
        check("内容被篡改时 install 拒绝（内容摘要校验）", raised)

        # 4) uninstall
        removed = uninstall_package("demo", plugin_dir)
        check("uninstall demo 返回路径", "demo" in removed, removed)
        check("demo 目录已移除",
              not (plugin_dir / "demo").exists())

        # 5) upgrade：仓库升级到 v2.0.0
        src2 = build_plugin_dir(plugin_root, "demo", "2.0.0")
        pkg2 = build_package(src2, "demo-2.0.0.xdplug")
        digest2 = content_digest(src2)
        pkg2.replace(repo / "demo-2.0.0.xdplug")
        (repo / "hashes" / "demo.hash256").write_text(
            f"{digest2}  demo-2.0.0.xdplug\n", encoding="utf-8"
        )
        index["demo"]["version"] = "2.0.0"
        index["demo"]["file"] = "demo-2.0.0.xdplug"
        (repo / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 重新安装 v1.0.0 基线
        install_package(install_url, "demo", plugin_dir)
        outcome = upgrade_package(
            install_url, "demo", plugin_dir, current_version="1.0.0"
        )
        check("upgrade 返回新版本结果",
              outcome is not None and outcome["version"] == "2.0.0",
              str(outcome))
        check("升级后 installed/demo 版本为 2.0.0",
              json.loads((plugin_dir / "demo" /
                          "xdclassmate.cli.setting.json").read_text()
                         )["version"] == "2.0.0")

        # 6) 已是最新时不重装
        up_to_date = upgrade_package(
            install_url, "demo", plugin_dir, current_version="2.0.0"
        )
        check("已是最新时 upgrade 返回 None", up_to_date is None)

        # 7) 版本比较
        check("compare_versions 1.0.0 < 2.0.0",
              compare_versions("1.0.0", "2.0.0") == -1)
        check("compare_versions 2.0.0 >= 2.0.0",
              compare_versions("2.0.0", "2.0.0") >= 0)

    print(f"\n{'全部通过' if failures == 0 else '存在失败'}："
          f"{'0' if failures == 0 else failures} 项失败")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
