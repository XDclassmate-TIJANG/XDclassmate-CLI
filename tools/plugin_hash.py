"""插件内容 hash 计算工具（仅使用 Python 标准库）。

按 README 规定的算法计算插件目录或 .xdplug 压缩包的内容 SHA-256：
    按 POSIX 相对路径排序，逐个写入：路径长度(8字节大端) + 路径 + 文件长度(8字节大端) + 文件内容；
    清单文件 xdclassmate.cli.setting.json 自身与 __pycache__ 目录不参与计算。

用法：
    py -3 tools/plugin_hash.py <插件目录或.xdplug路径>
    py -3 tools/plugin_hash.py --write <插件目录>      # 计算后直接回填清单 hash 字段

输出：
    插件的 SHA-256 十六进制字符串（可直接填入清单的 hash 字段）
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path

MANIFEST_NAME = "xdclassmate.cli.setting.json"


def content_hash(root: Path) -> str:
    """计算插件根目录的内容 hash，算法与 core/plugins._content_hash 完全一致。"""
    digest = hashlib.sha256()
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and path.name != MANIFEST_NAME
        and "__pycache__" not in path.parts
    )
    for path in files:
        relative_name = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative_name).to_bytes(8, "big")); digest.update(relative_name)
        digest.update(len(content).to_bytes(8, "big")); digest.update(content)
    return digest.hexdigest()


def write_manifest_hash(plugin_dir: Path, value: str) -> None:
    """把计算得到的 hash 回填到插件清单的 hash 字段。"""
    manifest_path = plugin_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"插件缺少清单文件: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["hash"] = value
    # ensure_ascii=False 保证中文描述可读；末尾换行符合常规文本文件格式
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    # 支持可选的 --write / -w 开关
    write_back = "--write" in argv or "-w" in argv
    argv = [item for item in argv if item not in ("--write", "-w")]

    if len(argv) != 1:
        print(__doc__)
        return 2

    target = Path(argv[0]).expanduser()
    if write_back and not target.is_dir():
        print("错误: --write 只支持插件目录", file=sys.stderr)
        return 2

    if target.is_dir():
        # 目录插件：直接对根目录计算
        value = content_hash(target)
        if write_back:
            write_manifest_hash(target, value)
            print(f"已更新 {target / MANIFEST_NAME} 的 hash 字段")
        print(value)
        return 0

    if target.is_file() and target.suffix == ".xdplug":
        # 压缩包插件：解压到临时目录后计算
        with tempfile.TemporaryDirectory(prefix="xdclassmate-hash-") as temp:
            root = Path(temp)
            with zipfile.ZipFile(target) as package:
                package.extractall(root)
            manifests = list(root.rglob(MANIFEST_NAME))
            if len(manifests) != 1:
                print(f"错误: {target} 必须包含唯一插件清单", file=sys.stderr)
                return 1
            print(content_hash(manifests[0].parent))
            return 0

    print(f"错误: {target} 不是插件目录或 .xdplug 压缩包", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
