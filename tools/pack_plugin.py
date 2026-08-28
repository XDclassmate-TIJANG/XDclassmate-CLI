"""插件打包工具：把插件目录打包成 `.xdplug` 压缩包（标准 ZIP 格式）。

用法：
    py -3 tools/pack_plugin.py <插件目录> [-o 输出路径.xdplug] [--update-hash]

说明：
    * 默认输出路径为 build/<name>-<version>.xdplug（取自清单的 name/version 字段）
    * --update-hash：打包前先按内容重算 hash 并回填清单，避免因忘记同步 hash
      导致加载时被拒绝（推荐日常使用）
    * 自动排除 __pycache__ 目录与 .pyc 文件
    * 压缩包内使用相对插件根目录的 POSIX 路径，与加载器的解析规则一致
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

# plugin_hash.py 与本脚本同目录：直接运行时可直接导入，被导入时回退到路径注入
try:
    from plugin_hash import MANIFEST_NAME, content_hash, write_manifest_hash
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from plugin_hash import MANIFEST_NAME, content_hash, write_manifest_hash


def pack_plugin(plugin_dir: str, output_path: str | None = None, update_hash: bool = False) -> Path:
    """
    打包插件目录为 .xdplug 压缩包。

    :param plugin_dir:  插件目录（需包含 xdclassmate.cli.setting.json）
    :param output_path: 输出文件路径，缺省为 build/<name>-<version>.xdplug
    :param update_hash: 打包前是否回填清单 hash 字段
    :return:            生成的 .xdplug 文件路径
    """
    plugin_dir = Path(plugin_dir).expanduser().resolve()
    manifest_path = plugin_dir / MANIFEST_NAME
    if not plugin_dir.is_dir():
        raise NotADirectoryError(f"插件目录不存在: {plugin_dir}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"插件缺少清单文件: {manifest_path}")

    # 先回填 hash，保证打包进去的清单与内容一致
    if update_hash:
        write_manifest_hash(plugin_dir, content_hash(plugin_dir))
        print(f"已更新清单 hash: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if output_path is None:
        output_path = Path("build") / f"{manifest['name']}-{manifest['version']}.xdplug"
    output_path = Path(output_path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 收集待打包文件：排除缓存目录与字节码文件
    files = sorted(
        path for path in plugin_dir.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )

    # ZIP_DEFLATED 压缩，arcname 统一为相对插件根目录的 POSIX 路径
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path in files:
            package.write(path, path.relative_to(plugin_dir).as_posix())

    print(f"已打包 {len(files)} 个文件 -> {output_path}")
    print(f"内容 hash: {content_hash(plugin_dir)}")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pack_plugin",
        description="把 XDclassmate-CLI 插件目录打包为 .xdplug 压缩包",
    )
    parser.add_argument("plugin_dir", help="插件目录路径")
    parser.add_argument("-o", "--output", default=None, help="输出 .xdplug 路径")
    parser.add_argument("--update-hash", action="store_true", help="打包前回填清单 hash 字段")
    arguments = parser.parse_args(argv)

    try:
        pack_plugin(arguments.plugin_dir, arguments.output, arguments.update_hash)
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError) as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
