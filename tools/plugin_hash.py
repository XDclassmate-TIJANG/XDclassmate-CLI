"""插件摘要计算工具（仅使用 Python 标准库）。

按 README 规定的算法计算插件目录或 .xdplug 压缩包的内容摘要：
按 POSIX 相对路径排序，逐个写入「路径长度(8字节大端) + 路径 + 文件长度
(8字节大端) + 文件内容」；清单文件自身与 __pycache__ 不参与计算。

用法：
    py -3 tools/plugin_hash.py <插件目录或.xdplug路径>     # 打印摘要
    py -3 tools/plugin_hash.py <插件目录> -a sha512        # 指定算法
    py -3 tools/plugin_hash.py --emit <插件目录> [-o 文件]  # 写入摘要文件
    py -3 tools/plugin_hash.py --write <插件目录>          # 按清单 url 回填

输出：
    摘要的十六进制字符串
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

MANIFEST_NAME = "xdclassmate.cli.setting.json"
DEFAULT_ALGORITHM = "sha256"
# 算法 -> 摘要文件默认扩展名
ALGORITHM_SUFFIX = {
    "sha256": ".hash256",
    "sha512": ".hash512",
    "sha1": ".hash1",
    "md5": ".md5",
}
# 默认摘要文件目录（相对项目根目录）
HASH_DIR = "hashes"


def content_hash(root: Path, algorithm: str = DEFAULT_ALGORITHM) -> str:
    """计算插件根目录的内容摘要，算法与 core.integrity 完全一致。"""
    digest = hashlib.new(algorithm)
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and path.name != MANIFEST_NAME
        and "__pycache__" not in path.parts
    )
    for path in files:
        relative_name = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        # 路径长度 + 路径 + 内容长度 + 内容
        digest.update(len(relative_name).to_bytes(8, "big"))
        digest.update(relative_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def read_manifest(plugin_dir: Path) -> dict:
    """读取插件清单。"""
    path = plugin_dir / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(f"插件缺少清单文件: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def default_hash_file(plugin_dir: Path, algorithm: str) -> Path:
    """按插件名与算法推导默认摘要文件路径（项目根 hashes/ 目录）。"""
    manifest = read_manifest(plugin_dir)
    suffix = ALGORITHM_SUFFIX.get(algorithm, f".{algorithm}")
    name = manifest.get("name") or plugin_dir.name
    return Path(HASH_DIR) / f"{name}{suffix}"


def emit_hash_file(
        plugin_dir: Path,
        output: Path,
        algorithm: str,
        label: Optional[str] = None
        ) -> str:
    """
    计算摘要并写入摘要文件，返回摘要字符串。

    :param label: 第二列的文件名标注（sha256sum 风格）。
                  缺省用插件目录名；打包发布时建议传目标 .xdplug 文件名，
                  与 install 端按压缩包名定位条目的逻辑保持一致。
                  无论写什么都**不影响**校验结果——摘要文件只要含一个有效
                  摘要即可通过，第二列仅用于人工核对与多条目区分。
    """
    value = content_hash(plugin_dir, algorithm)
    output.parent.mkdir(parents=True, exist_ok=True)
    # sha256sum 风格：摘要 + 两个空格 + 标注，便于人工核对
    output.write_text(
        f"{value}  {label or plugin_dir.name}\n", encoding="utf-8"
    )
    print(f"已写入摘要文件 {output}（{algorithm}）")
    return value


def write_back(
        plugin_dir: Path,
        algorithm: str,
        label: Optional[str] = None
        ) -> str:
    """
    按清单的 url 字段回填摘要文件。

    * url 为本地路径（含 file://）时直接写入；
    * url 为 http(s) 时无法自动写入，提示改用 --emit；
    * url 为 null 且清单存在旧 hash 字段时，更新该字段（兼容旧清单）。
    """
    manifest = read_manifest(plugin_dir)
    url = manifest.get("url")
    value = content_hash(plugin_dir, algorithm)

    if url:
        if str(url).lower().startswith(("http://", "https://")):
            raise ValueError(
                f"url 指向远程地址，无法自动写入: {url}（请改用 --emit）"
            )
        target = Path(str(url).replace("file://", "")).expanduser()
        if not target.is_absolute():
            target = Path.cwd() / target
        emit_hash_file(plugin_dir, target, algorithm, label)
        return value

    if "hash" in manifest:
        manifest["hash"] = value
        path = plugin_dir / MANIFEST_NAME
        text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        path.write_text(text, encoding="utf-8")
        print(f"已更新清单的旧 hash 字段: {path}")
        print("提示: 建议改用 url 字段指向仓库中的摘要文件")
        return value

    raise ValueError(
        "清单 url 为 null，无法回填；请先用 --emit 生成摘要文件并在清单中填写 url"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plugin_hash",
        description="计算 XDclassmate-CLI 插件的内容摘要",
    )
    parser.add_argument("plugin_path", help="插件目录或 .xdplug 压缩包路径")
    parser.add_argument(
        "-a", "--algorithm", default=DEFAULT_ALGORITHM,
        help=f"摘要算法，默认 {DEFAULT_ALGORITHM}"
    )
    parser.add_argument(
        "--emit", action="store_true",
        help="把摘要写入文件（默认 hashes/<插件名>.hash256）"
    )
    parser.add_argument(
        "-o", "--output", default=None, help="--emit 的输出文件路径"
    )
    parser.add_argument(
        "--label", default=None,
        help="--emit 时摘要文件第二列的标注；"
             "缺省为插件目录名，打包发布时可写成 .xdplug 文件名"
    )
    parser.add_argument(
        "--write", action="store_true", help="按清单 url 回填摘要文件"
    )
    arguments = parser.parse_args(argv)

    target = Path(arguments.plugin_path).expanduser()
    try:
        if target.is_dir():
            if arguments.write:
                value = write_back(target, arguments.algorithm,
                                   arguments.label)
            elif arguments.emit:
                output = (
                    Path(arguments.output)
                    if arguments.output
                    else default_hash_file(target, arguments.algorithm)
                )
                value = emit_hash_file(
                    target, output, arguments.algorithm, arguments.label
                )
            else:
                value = content_hash(target, arguments.algorithm)
            print(value)
            return 0

        if target.is_file() and target.suffix == ".xdplug":
            with tempfile.TemporaryDirectory(
                prefix="xdclassmate-hash-"
            ) as temp:
                root = Path(temp)
                with zipfile.ZipFile(target) as package:
                    package.extractall(root)
                manifests = list(root.rglob(MANIFEST_NAME))
                if len(manifests) != 1:
                    print(
                        f"错误: {target} 必须包含唯一插件清单",
                        file=sys.stderr
                    )
                    return 1
                value = content_hash(manifests[0].parent, arguments.algorithm)
                print(value)
                return 0
    except (FileNotFoundError, NotADirectoryError, ValueError,
            json.JSONDecodeError) as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1

    print(f"错误: {target} 不是插件目录或 .xdplug 压缩包", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
