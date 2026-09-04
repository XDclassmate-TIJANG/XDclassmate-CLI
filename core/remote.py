"""远程插件仓库：从 INSTALL_URL 拉取插件包并做完整性校验。

仓库布局约定（INSTALL_URL 指向一个目录或支持 http(s)/file 的等效地址）：

    index.json                         插件目录（插件名 -> 条目）
    <name>/<name>-<version>.xdplug     插件压缩包
    hashes/<name>.hash256              摘要文件（sha256sum 风格）

index.json 的条目格式：

    {
      "image": {
        "version": "1.0.0",
        "file": "image-1.0.0.xdplug",             # 相对 INSTALL_URL 的包路径
        "hash": "hashes/image.hash256",           # 相对 INSTALL_URL 的摘要路径
        "algorithm": "sha256"                       # 可选，缺省 sha256
      }
    }

`hash` 也可以是**内联**的 64 位十六进制摘要，省去一次网络请求。

设计要点：
    * 本模块只负责「网络/文件搬运 + 校验 + 落盘」，不持有任何全局状态；
    * 校验复用 core.integrity 的算法（content_digest / parse_expected_digest），
      与本地插件加载走同一套摘要逻辑，保证「拉下来即可用」；
    * 所有用户可见错误都通过 XDclassmateCLIException 带 key 抛出，
      交给内核按当前语言翻译，避免在此直接 print；
    * INSTALL_URL 未配置（空串）时，install/upgrade 直接拒绝并给出明确提示。
"""
from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from .config import DEFAULT_INSTALL_URL
from .exceptions import (
    PluginArchiveError,
    PluginHashMismatchError,
    PluginIntegrityError,
    PluginNotInRepositoryError,
    PluginNotInstalledError,
    RemoteDownloadError,
    RemoteNotConfiguredError,
)
from .integrity import (
    DIGEST_PATTERN,
    content_digest,
    fetch_url_text,
    parse_expected_digest,
    resolve_url,
)
from .logger import get_logger

# HASH 一类的校验目前是在本地做，使用的也是本地文件
# 后面会通过URL请求和证书校验确保完整性
# 本地校验会保留证书校验，sha256等本地文件校验则移除

# TODO: 迁移到install目录

LOGGER = get_logger("remote")

# 仓库索引文件名
DEFAULT_INDEX = "index.json"
# 网络请求超时（秒）
DEFAULT_TIMEOUT = 10.0

# 内联摘要判定：整串为 8~128 位十六进制
_INLINE_DIGEST = re.compile(r"^[0-9a-fA-F]{8,128}$")


def compare_versions(current: str, target: str) -> int:
    """
    比较两个版本号大小。

    :return: 负数表示 current 较旧，0 表示相等，正数表示 current 较新
    """

    def parse(version: str) -> tuple[int, ...]:
        parts = re.findall(r"\d+", version)
        return tuple(int(part) for part in parts) if parts else (0,)

    left, right = parse(current), parse(target)
    length = max(len(left), len(right))
    left = left + (0,) * (length - len(left))
    right = right + (0,) * (length - len(right))
    return (left > right) - (left < right)


def _join_url(install_url: str, relative: str) -> str:
    """把相对路径拼到 INSTALL_URL 之后，得到可解析的完整地址。"""
    return f"{install_url.rstrip('/')}/{relative.lstrip('/')}"


def _fetch_bytes(url: str) -> bytes:
    """
    取回 URL 指向的二进制内容（用于插件包下载）。

    :raises RemoteDownloadError: 网络/文件读取失败
    """
    target = resolve_url(url)
    try:
        with urllib.request.urlopen(
                target, timeout=DEFAULT_TIMEOUT) as response:
            return response.read()
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise RemoteDownloadError(
            f"无法下载文件 {url}: {error}",
            key="cmd.install.download_error",
            params={"reason": t_install(
                "error.reason.download_failed", url=url, error=error
            )},
        ) from error


def _extract_archive_to(archive: Path, dest: Path) -> None:
    """
    把 .xdplug 压缩包安全解压到 dest（dest 会被清空后重建）。

    拒绝任何绝对路径或包含 `..` 的成员，避免压缩包逃逸。
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        package = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as error:
        raise PluginArchiveError(
            f"插件压缩包无法读取: {archive}",
            key="error.plugin_archive",
            params={"reason": t_install(
                "error.reason.archive_unreadable", error=error
            )},
            details={"archive": str(archive)},
        ) from error
    with package:
        for member in package.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise PluginArchiveError(
                    f"插件压缩包包含非法路径: {member.filename}",
                    key="error.plugin_archive",
                    params={"reason": t_install(
                        "error.reason.archive_illegal_path",
                        path=member.filename,
                    )},
                    details={"archive": str(archive)},
                )
        package.extractall(dest)


def fetch_index(install_url: str) -> dict:
    """
    获取并解析仓库索引 index.json。

    :raises RemoteNotConfiguredError: INSTALL_URL 未配置
    :raises RemoteDownloadError:      索引无法获取或解析
    """
    if not install_url:
        raise RemoteNotConfiguredError(
            "INSTALL_URL 未配置",
            key="cmd.install.url_missing",
        )
    text = fetch_url_text(_join_url(install_url, DEFAULT_INDEX))
    try:
        index = json.loads(text)
    except (ValueError, json.JSONDecodeError) as error:
        raise RemoteDownloadError(
            f"仓库索引格式非法: {error}",
            key="cmd.install.catalog_error",
            params={"reason": t_install(
                "error.reason.index_invalid", error=error
            )},
        ) from error
    if not isinstance(index, dict):
        raise RemoteDownloadError(
            "仓库索引根节点必须是 JSON 对象",
            key="cmd.install.catalog_error",
            params={"reason": t_install("error.reason.index_root")},
        )
    return index


def resolve_plugin_dir(config_value: Optional[str] = None) -> Path:
    """
    按与 Plugins 管理器一致的规则解析插件目录（相对路径以 cwd 为基准）。

    仅计算路径，不创建目录；需要时由调用方负责 mkdir。
    """
    if config_value is None:
        config_value = "./plugins"
    path = Path(config_value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def install_package(
        install_url: str,
        name: str,
        plugin_dir: Path,
        ) -> dict:
    """
    从仓库下载并校验单个插件，落盘到 plugin_dir/<name>/。

    :param install_url: 仓库根地址
    :param name:        插件名（与仓库索引键一致）
    :param plugin_dir:  插件目录（不存在则创建）
    :return:            {"name", "version", "path"} 安装结果
    :raises PluginNotInRepositoryError: 插件不在仓库中 / INSTALL_URL 缺失
    :raises PluginHashMismatchError:     校验不通过
    """
    index = fetch_index(install_url)
    entry = index.get(name)
    if not isinstance(entry, dict):
        raise PluginNotInRepositoryError(
            f"插件 {name} 不在仓库中",
            key="cmd.install.not_in_repo",
            params={"name": name},
        )
    version = str(entry.get("version", ""))
    package_file = entry.get("file", f"{name}.xdplug")
    algorithm = str(entry.get("algorithm", "sha256")).strip().lower()
    hash_source = entry.get("hash")

    print(t_install("cmd.install.downloading", file=package_file))
    package_bytes = _fetch_bytes(_join_url(install_url, package_file))

    # 写入临时压缩包并解压到临时目录，计算内容摘要
    import tempfile
    temporary = tempfile.TemporaryDirectory(prefix="xdclassmate-dl-")
    archive_path = Path(temporary.name) / package_file
    archive_path.write_bytes(package_bytes)
    extract_root = Path(temporary.name) / "_extracted"
    _extract_archive_to(archive_path, extract_root)

    print(t_install("cmd.install.verifying", algorithm=algorithm))
    actual = content_digest(extract_root, algorithm)
    expected = _resolve_expected_digest(install_url, hash_source, package_file)
    if actual != expected:
        raise PluginHashMismatchError(
            f"插件 {name} 的 {algorithm} 校验失败",
            key="error.plugin_hash_mismatch",
            params={"plugin": name},
            details={
                "plugin": name,
                "algorithm": algorithm,
                "expected": expected,
                "actual": actual,
            },
        )

    plugin_dir.mkdir(parents=True, exist_ok=True)
    destination = plugin_dir / name
    _extract_archive_to(archive_path, destination)
    LOGGER.info("插件 %s v%s 已安装到 %s", name, version, destination)
    return {"name": name, "version": version, "path": str(destination)}


def _resolve_expected_digest(
        install_url: str,
        hash_source: Optional[str],
        package_file: str
        ) -> str:
    """
    解析期望摘要。

    hash_source 为内联十六进制时直接返回；否则当作相对 INSTALL_URL 的
    摘要文件路径，按 sha256sum 风格解析（用 package_file 定位对应行）。
    """
    if hash_source and _INLINE_DIGEST.fullmatch(str(hash_source).strip()):
        return str(hash_source).strip().lower()
    if not hash_source:
        raise PluginIntegrityError(
            "仓库条目缺少 hash（摘要或内联摘要）",
            key="error.plugin_integrity",
            params={"reason": t_install("error.reason.missing_hash")},
        )
    text = fetch_url_text(_join_url(install_url, str(hash_source)))
    return parse_expected_digest(text, filename=package_file)


def upgrade_package(
        install_url: str,
        name: str,
        plugin_dir: Path,
        current_version: str,
        ) -> Optional[dict]:
    """
    升级单个插件：仅当仓库版本更新时才下载安装。

    :return: 安装成功返回结果 dict；已是最新返回 None
    """
    index = fetch_index(install_url)
    entry = index.get(name)
    if not isinstance(entry, dict):
        raise PluginNotInRepositoryError(
            f"插件 {name} 不在仓库中",
            key="cmd.install.not_in_repo",
            params={"name": name},
        )
    target_version = str(entry.get("version", ""))
    if compare_versions(current_version, target_version) >= 0:
        return None
    print(t_install(
        "cmd.upgrade.installing", new=target_version, current=current_version
    ))
    return install_package(install_url, name, plugin_dir)


def uninstall_package(
        name: str,
        plugin_dir: Path,
        installed_path: Optional[str] = None,
        ) -> str:
    """
    卸载插件：移除 plugin_dir/<name>/ 目录或 plugin_dir/<name>.xdplug 文件。

    :param installed_path: 已加载插件记录的原始路径（优先用于定位）
    :return:               被移除的路径
    :raises PluginNotInstalledError: 插件不存在
    """
    target: Optional[Path] = None
    if installed_path:
        candidate = Path(installed_path)
        if candidate.exists():
            target = candidate
    if target is None:
        as_dir = plugin_dir / name
        as_file = plugin_dir / f"{name}.xdplug"
        if as_dir.is_dir():
            target = as_dir
        elif as_file.is_file():
            target = as_file
    if target is None or not target.exists():
        raise PluginNotInstalledError(
            f"插件 {name} 未找到",
            key="cmd.uninstall.not_found",
            params={"name": name},
        )
    print(t_install("cmd.uninstall.removing", name=name, path=target))
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    LOGGER.info("插件 %s 已卸载（%s）", name, target)
    return str(target)


def t_install(key: str, **params) -> str:
    """远程模块内部使用的翻译入口（避免多处重复 import t）。"""
    from .i18n import t
    return t(key, **params)
