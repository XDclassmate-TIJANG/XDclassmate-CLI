"""插件完整性校验：通过 URL 获取期望摘要并与本地内容比对。

设计背景
--------
早期版本把期望摘要直接写在清单的 `hash` 字段里，插件内容一改动就必须
重新打包发布。改为 `url` 后，摘要由**仓库中的独立文件**维护
（常见约定如 `.hash256`、`SHA256SUMS`、`*.sha512`），插件本体无需改动
即可更新摘要，也便于发布者用同一份文件同时校验目录插件与 `.xdplug`。

校验流程
--------
1. 计算本地内容摘要（算法由清单 `algorithm` 指定，默认 sha256）；
2. 清单 `url` 为 null 时**跳过校验**（本地安装/开发调试场景），记录日志；
3. 否则按 URL 取回期望摘要（支持 http(s)://、file://、本地绝对路径、
   相对项目根目录的相对路径）；
4. 解析摘要文件（支持裸摘要行与 `<摘要>  <文件名>` 两种格式）；
5. 比对，不一致抛 PluginHashMismatchError。

摘要文件示例：
    6d5edd2c1241c5a151cc5d34433e5f5b0cfd13f4e896fb578af7fed7e8421766
    或（sha256sum 风格）
    6d5edd2c...1766  hello-1.0.0.xdplug
"""
from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from .config import PROJECT_ROOT
from .exceptions import PluginIntegrityError
from .i18n import t
from .logger import get_logger

LOGGER = get_logger("integrity")

# 默认摘要算法
DEFAULT_ALGORITHM = "sha256"
# 网络请求超时（秒）
DEFAULT_TIMEOUT = 10.0
# 单次读取期望摘要文件的最大字节数，避免异常文件拖垮启动
MAX_HASH_FILE_BYTES = 64 * 1024
# URL 文件名后缀 -> 算法，便于用 .hash256/.sha512 等文件表达算法
SUFFIX_ALGORITHMS = {
    ".hash256": "sha256",
    ".sha256": "sha256",
    ".hash512": "sha512",
    ".sha512": "sha512",
    ".hash1": "sha1",
    ".sha1": "sha1",
    ".md5": "md5",
}
# 摘要文本（十六进制）的匹配模式，长度 8~128 位十六进制字符
DIGEST_PATTERN = re.compile(r"\b([0-9a-fA-F]{8,128})\b")


def normalize_algorithm(
    algorithm: Optional[str], url: Optional[str] = None
) -> str:
    """
    确定使用的摘要算法。

    优先级：清单 algorithm 字段 > URL 文件名后缀 > 默认 sha256。
    """
    if algorithm:
        name = algorithm.strip().lower().replace("-", "")
        if name in hashlib.algorithms_available:
            return name
        raise PluginIntegrityError(
            f"不支持的摘要算法: {algorithm}",
            key="error.plugin_integrity",
            params={"reason": t(
                "error.reason.algorithm_unsupported", algorithm=algorithm
            )},
        )
    if url:
        suffix = Path(re.sub(r"[?#].*$", "", url)).suffix.lower()
        if suffix in SUFFIX_ALGORITHMS:
            return SUFFIX_ALGORITHMS[suffix]
    return DEFAULT_ALGORITHM


def content_digest(root: Path, algorithm: str = DEFAULT_ALGORITHM) -> str:
    """
    计算插件目录内容摘要（与 tools/plugin_hash.py 的算法保持一致）。

    规则：按相对 POSIX 路径排序，逐个写入「路径长度 + 路径 + 文件长度 +
    文件内容」；清单文件自身与 __pycache__ 不参与计算。
    """
    digest = hashlib.new(algorithm)
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and path.name != "xdclassmate.cli.setting.json"
        and "__pycache__" not in path.parts
    )
    for path in files:
        relative_name = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative_name).to_bytes(8, "big"))
        digest.update(relative_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def resolve_url(url: str) -> str:
    """
    把清单中的 url 归一化成可访问的地址。

    * http://、https://、file:// 原样返回；
    * 绝对路径补全为 file:/// 形式；
    * 相对路径按项目根目录（core 的上级）解析。
    """
    lowered = url.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", lowered):
        return lowered
    candidate = Path(lowered).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve().as_uri()


def fetch_url_text(url: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """
    取回 URL 指向的文本内容。

    :raises PluginIntegrityError: 网络/文件读取失败或内容过大
    """
    target = resolve_url(url)
    try:
        with urllib.request.urlopen(target, timeout=timeout) as response:
            raw = response.read(MAX_HASH_FILE_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise PluginIntegrityError(
            f"无法获取摘要文件 {url}: {error}",
            key="error.plugin_integrity",
            params={"reason": t(
                "error.reason.hash_fetch_failed", url=url, error=error
            )},
        ) from error
    if len(raw) > MAX_HASH_FILE_BYTES:
        raise PluginIntegrityError(
            f"摘要文件过大: {url}",
            key="error.plugin_integrity",
            params={"reason": t("error.reason.hash_file_too_large", url=url)},
        )
    text = raw.decode("utf-8", errors="replace")
    LOGGER.debug("已获取摘要文件 %s（%s 字节）", url, len(raw))
    return text


def parse_expected_digest(text: str, filename: Optional[str] = None) -> str:
    """
    从摘要文件内容中解析期望摘要。

    支持两种格式：
        <摘要>
        <摘要>  文件名        （sha256sum 风格，可用 filename 指定目标行）
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        match = DIGEST_PATTERN.search(stripped)
        if not match:
            continue
        if filename:
            # 指定文件名时，只接受包含该文件名的行
            remainder = stripped.replace(match.group(1), "", 1)
            if filename not in remainder:
                continue
        return match.group(1).lower()
    raise PluginIntegrityError(
        "摘要文件中未找到有效的摘要",
        key="error.plugin_integrity",
        params={"reason": t("error.reason.digest_not_found")},
    )


def verify_plugin(
        root: Path,
        url: Optional[str],
        algorithm: Optional[str] = None,
        filename: Optional[str] = None
        ) -> Optional[str]:
    """
    校验插件内容。

    :param root:      插件根目录
    :param url:       期望摘要文件地址；None/空字符串表示跳过校验
    :param algorithm: 摘要算法，缺省按 URL 后缀推导，再缺省 sha256
    :param filename:  摘要文件中与本插件对应的文件名（多插件共用一份时使用）
    :return:          校验通过返回实际摘要；跳过校验返回 None
    :raises PluginIntegrityError:     获取或解析摘要文件失败
    :raises PluginHashMismatchError:  摘要不一致
    """
    if not url:
        LOGGER.info("插件 %s 未配置 url，跳过完整性校验（本地安装/调试）",
                    root.name)
        return None

    name = normalize_algorithm(algorithm, url)
    actual = content_digest(root, name)
    expected = parse_expected_digest(fetch_url_text(url), filename)

    if actual != expected:
        from .exceptions import PluginHashMismatchError
        raise PluginHashMismatchError(
            f"插件 {root.name} 的 {name} 校验失败",
            key="error.plugin_hash_mismatch",
            params={"plugin": root.name},
            details={
                "plugin": root.name,
                "algorithm": name,
                "expected": expected,
                "actual": actual,
                "url": url,
            },
        )
    LOGGER.debug("插件 %s 完整性校验通过（%s）", root.name, name)
    return actual
