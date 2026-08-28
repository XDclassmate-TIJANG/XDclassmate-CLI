"""插件加载器：从目录或 .xdplug 压缩包加载插件。

加载流程：
    1. 扫描插件目录下的子目录（目录插件）与 .xdplug 文件（压缩包插件）；
    2. 读取并校验清单 xdclassmate.cli.setting.json；
    3. 校验内容 hash、CLI 版本、前置插件；
    4. 动态加载入口模块，取得入口可调用对象；
    5. 把入口函数注册到事件总线的 plugin_init 事件。

任何单个插件校验失败都只跳过该插件并记录日志，不影响 CLI 其余部分。
"""
from __future__ import annotations

import functools
import hashlib
import importlib.util
import json
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Literal, Optional, TypeAlias

from .config import CLI_VERSION, PROJECT_CONFIG_PATH, ConfigManager
from .event_bus import bus
from .exceptions import (
    DuplicatePluginNamesError,
    PluginArchiveError,
    PluginEntryError,
    PluginException,
    PluginHashMismatchError,
    PluginManifestError,
    PluginNotFoundError,
    PluginVersionMismatchError,
)
from .logger import get_logger

LOGGER = get_logger("plugins")

# 插件清单文件名
PLUGIN_MANIFEST = "xdclassmate.cli.setting.json"
# CLI 版本不匹配时的处理策略
CLIVersionMismatch: TypeAlias = Literal["ignore", "warn", "stop"]


def _content_hash(root: Path) -> str:
    """
    计算插件内容 hash。

    算法：按相对 POSIX 路径排序，逐个写入「路径长度 + 路径 + 文件长度 +
    文件内容」；清单文件自身与 __pycache__ 不参与计算。
    """
    digest = hashlib.sha256()
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and path.name != PLUGIN_MANIFEST
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


class Plugins:
    """插件管理器：扫描、校验并加载插件。"""

    def __init__(
            self,
            plugins_dir: Optional[str] = None,
            cli_version_mismatch: CLIVersionMismatch = "warn"
            ):
        """
        :param plugins_dir:         插件目录，缺省读取配置的 plugin_dir
        :param cli_version_mismatch: CLI 版本不匹配时的处理策略
        """
        config = ConfigManager(str(PROJECT_CONFIG_PATH))
        configured_dir = config.load_config("plugin_dir", default="./plugins")
        self.plugins_dir = Path(plugins_dir or configured_dir).expanduser()
        if not self.plugins_dir.is_absolute():
            self.plugins_dir = Path.cwd() / self.plugins_dir
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.cli_version_mismatch = cli_version_mismatch
        self.plugins_list: dict[str, dict[str, Any]] = {}
        self._temporary_roots: list[tempfile.TemporaryDirectory[str]] = []

        LOGGER.debug("开始扫描插件目录 %s", self.plugins_dir)
        self.load_plugins()
        self.calculate_pre_legality_of_all_plugins()
        self.register_plugins_with_event_bus()

    def register(
            self,
            name: str,
            version: str = "1.0",
            cli_version: str = CLI_VERSION,
            events: Optional[list[str]] = None,
            pre_plugins: Optional[dict[str, str]] = None,
            **metadata: Any
            ) -> None:
        """
        注册一个插件。

        :param name:         插件名称
        :param version:      插件版本
        :param cli_version:  插件要求的 CLI 版本
        :param events:       插件希望监听的事件列表
        :param pre_plugins:  前置插件字典 {插件名: 版本}
        :param metadata:     其余元数据（作者、描述、hash、入口、路径等）
        """
        if not name:
            raise PluginException("插件名称不能为空")
        if name in self.plugins_list:
            raise DuplicatePluginNamesError(
                f"插件 {name} 已被注册", details={"plugin": name}
            )

        if cli_version != CLI_VERSION:
            message = (
                f"插件 {name} 的 CLI 版本 {cli_version} "
                f"与当前 CLI 版本 {CLI_VERSION} 不匹配"
            )
            if self.cli_version_mismatch == "stop":
                raise PluginVersionMismatchError(
                    message,
                    details={
                        "plugin": name,
                        "expected": CLI_VERSION,
                        "actual": cli_version,
                    }
                )
            if self.cli_version_mismatch == "warn":
                LOGGER.warning(message)

        self.plugins_list[name] = {
            "version": version,
            "cli_version": cli_version,
            "events": list(events or []),
            "pre_plugin": dict(pre_plugins or {}),
            **metadata,
        }
        LOGGER.debug("注册插件 %s v%s", name, version)

    def delete(self, name: str) -> None:
        """删除已注册的插件。"""
        if name in self.plugins_list:
            del self.plugins_list[name]
            LOGGER.debug("删除插件 %s", name)
        else:
            raise PluginNotFoundError(
                f"插件 {name} 未找到", details={"plugin": name}
            )

    def get(self, name: str) -> Optional[dict[str, Any]]:
        """按名称获取插件信息，不存在返回 None。"""
        return self.plugins_list.get(name)

    def calculate_pre_legality_of_all_plugins(self) -> bool:
        """
        校验所有插件的前置依赖：前置插件必须存在且版本一致。

        :return: 全部合法返回 True，否则抛出相应异常
        """
        for name, meta in self.plugins_list.items():
            pre_plugins = meta.get("pre_plugin", {})
            for pre_name, pre_version in pre_plugins.items():
                if pre_name not in self.plugins_list:
                    raise PluginNotFoundError(
                        f"插件 {name} 的前置插件 {pre_name} 未找到",
                        details={"plugin": name, "pre_plugin": pre_name}
                    )
                actual = self.plugins_list[pre_name]["version"]
                if actual != pre_version:
                    raise PluginVersionMismatchError(
                        f"插件 {name} 的前置插件 {pre_name} 版本不匹配",
                        details={
                            "plugin": name,
                            "pre_plugin": pre_name,
                            "expected": pre_version,
                            "actual": actual,
                        }
                    )
        return True

    def get_plugin_list(self) -> dict[str, dict[str, Any]]:
        """获取全部已注册插件。"""
        return self.plugins_list

    def get_pre_plugins(self, name: str) -> dict[str, str]:
        """
        获取指定插件的前置插件表。

        :return: {前置插件名: 版本}
        """
        plugin_meta = self.get(name)
        if not plugin_meta:
            raise PluginNotFoundError(
                f"插件 {name} 未找到", details={"plugin": name}
            )
        return plugin_meta.get("pre_plugin", {})

    def _read_manifest(self, root: Path) -> dict[str, Any]:
        """读取并校验插件清单。"""
        path = root / PLUGIN_MANIFEST
        if not path.is_file():
            raise PluginManifestError(
                f"插件缺少 {PLUGIN_MANIFEST}: {root}", details={"root": str(root)}
            )
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PluginManifestError(
                f"插件清单无法读取: {path}", details={"root": str(root)}
            ) from error

        required = (
            "name", "entry", "version", "author", "cli_version",
            "description", "hash",
        )
        missing = [key for key in required if not manifest.get(key)]
        if missing:
            raise PluginManifestError(
                f"插件清单缺少字段: {', '.join(missing)}",
                details={"manifest": str(path)}
            )
        if ":" not in manifest["entry"]:
            raise PluginEntryError(
                "插件 entry 必须使用 module.py:function 格式",
                details={
                    "plugin": manifest["name"],
                    "entry": manifest["entry"],
                }
            )
        if manifest["hash"].lower() != _content_hash(root):
            raise PluginHashMismatchError(
                f"插件 {manifest['name']} 的 hash 校验失败",
                details={
                    "plugin": manifest["name"],
                    "expected": manifest["hash"],
                    "actual": _content_hash(root),
                }
            )
        return manifest

    def _extract_archive(self, archive: Path) -> Path:
        """解压 .xdplug 压缩包到临时目录，返回插件根目录。"""
        temporary = tempfile.TemporaryDirectory(prefix="xdclassmate-plugin-")
        self._temporary_roots.append(temporary)
        root = Path(temporary.name)
        try:
            package = zipfile.ZipFile(archive)
        except (OSError, zipfile.BadZipFile) as error:
            raise PluginArchiveError(
                f"插件压缩包无法读取: {archive}",
                details={"archive": str(archive)}
            ) from error
        with package:
            for member in package.infolist():
                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise PluginArchiveError(
                        f"插件压缩包包含非法路径: {member.filename}",
                        details={"archive": str(archive)}
                    )
            package.extractall(root)
        manifests = list(root.rglob(PLUGIN_MANIFEST))
        if len(manifests) != 1:
            raise PluginArchiveError(
                ".xdplug 必须包含唯一插件清单",
                details={"archive": str(archive), "found": len(manifests)}
            )
        return manifests[0].parent

    def _load_entry(self, root: Path, manifest: dict[str, Any]) -> Any:
        """动态加载插件入口模块，返回入口可调用对象。"""
        module_name, function_name = manifest["entry"].rsplit(":", 1)
        module_path = (root / module_name).resolve()
        if root.resolve() not in module_path.parents:
            raise PluginEntryError(
                "插件入口不能跳出插件目录",
                details={"plugin": manifest["name"]}
            )
        if not module_path.is_file() or module_path.suffix != ".py":
            raise PluginEntryError(
                f"插件入口文件不存在: {module_name}",
                details={
                    "plugin": manifest["name"],
                    "entry": manifest["entry"],
                }
            )
        unique_name = f"xdclassmate_plugin_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(unique_name, module_path)
        if spec is None or spec.loader is None:
            raise PluginEntryError(
                f"无法创建插件模块: {module_name}",
                details={"plugin": manifest["name"]}
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as error:
            sys.modules.pop(unique_name, None)
            raise PluginEntryError(
                f"插件入口加载失败: {manifest['name']}",
                details={"plugin": manifest["name"]}
            ) from error
        entry = getattr(module, function_name, None)
        if not callable(entry):
            raise PluginEntryError(
                f"插件入口函数不存在: {manifest['entry']}",
                details={"plugin": manifest["name"]}
            )
        return entry

    def load_plugins(self) -> None:
        """
        扫描插件目录并加载所有合法插件。

        单个插件校验失败（清单缺失、hash 不匹配、入口异常等）只会拒绝加载
        该插件并记录错误日志，不会中断其余插件加载或让 CLI 崩溃。
        """
        for candidate in sorted(
                self.plugins_dir.iterdir(), key=lambda path: path.name):
            if candidate.is_dir() and not candidate.name.startswith("."):
                root = candidate
            elif candidate.is_file() and candidate.suffix == ".xdplug":
                root = self._extract_archive(candidate)
            else:
                continue
            try:
                manifest = self._read_manifest(root)
                entry = self._load_entry(root, manifest)
                self.register(
                    manifest["name"],
                    manifest["version"],
                    manifest["cli_version"],
                    manifest.get("events"),
                    manifest.get("pre_plugins"),
                    author=manifest["author"],
                    description=manifest["description"],
                    hash=manifest["hash"],
                    entry=entry,
                    path=str(candidate),
                )
            except PluginException as error:
                # 拒绝加载该插件，继续处理后续插件
                LOGGER.error("跳过插件 %s: %s", candidate.name, error)

    def register_plugins_with_event_bus(self) -> None:
        """把所有插件入口注册到事件总线。"""
        for meta in self.plugins_list.values():
            entry = meta["entry"]
            bus.on("plugin_init", entry, once=True)
            for event in meta["events"]:
                if event == "plugin_init":
                    continue
                bus.on(event, entry)


# 全局插件管理器单例：导入本模块即完成插件扫描与校验
pl = Plugins()


def XDPlugin(
        name: Optional[str] = None,
        version: str = "1.0",
        cli_version: str = CLI_VERSION,
        events: Optional[list[str]] = None,
        pre_plugins: Optional[dict[str, str]] = None
        ):
    """
    装饰器：标记插件入口函数并自动注册到事件总线。

    :param name:         插件名称，缺省使用函数名
    :param version:      插件版本
    :param cli_version:  CLI 版本
    :param events:       插件监听的事件列表
    :param pre_plugins:  前置插件字典

    用法：
        @XDPlugin(name="my-plugin", version="2.0", events=["before_cmd"])
        def my_plugin_entry():
            ...
    """

    def decorator(func):
        plugin_name = name or func.__name__
        pl.register(plugin_name, version, cli_version, events, pre_plugins,
                    entry=func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        bus.on("plugin_init", wrapper, once=True)
        for event in events or []:
            bus.on(event, wrapper)
        return wrapper

    return decorator
