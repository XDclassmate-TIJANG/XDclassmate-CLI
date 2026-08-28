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

from .config import CLI_VERSION, ConfigManager
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

PLUGIN_MANIFEST = "xdclassmate.cli.setting.json"
CLIVersionMismatch: TypeAlias = Literal["ignore", "warn", "stop"]
_CONFIG_PATH = Path(__file__).with_name("configs") / "config.json"


def _content_hash(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != PLUGIN_MANIFEST and "__pycache__" not in path.parts)
    for path in files:
        relative_name = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative_name).to_bytes(8, "big")); digest.update(relative_name)
        digest.update(len(content).to_bytes(8, "big")); digest.update(content)
    return digest.hexdigest()

class Plugins:
    """从文件夹或 .xdplug 压缩包加载 XDclassmate-CLI 插件。"""
    def __init__(self, plugins_dir: Optional[str] = None, cli_version_mismatch: CLIVersionMismatch = "warn"):
        config = ConfigManager(str(_CONFIG_PATH))
        configured_dir = config.load_config("plugin_dir", default="./plugins")
        self.plugins_dir = Path(plugins_dir or configured_dir).expanduser()
        if not self.plugins_dir.is_absolute():
            self.plugins_dir = Path.cwd() / self.plugins_dir
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.cli_version_mismatch = cli_version_mismatch
        self.plugins_list: dict[str, dict[str, Any]] = {}
        self._temporary_roots: list[tempfile.TemporaryDirectory[str]] = []
        self.load_plugins()
        self.calculate_pre_legality_of_all_plugins()
        self.register_plugins_with_event_bus()

    def register(self, name: str, version: str = "1.0", cli_version: str = CLI_VERSION, events: Optional[list[str]] = None, pre_plugins: Optional[dict[str, str]] = None, **metadata: Any):
        """
        注册一个插件。
        name:           插件名称
        version:        插件版本
        cli_version:    CLI 版本
        events:         插件希望监听的事件列表
        pre_plugins:    前置插件字典
        """
        if not name:
            raise PluginException("插件名称不能为空")
        if name in self.plugins_list:
            raise DuplicatePluginNamesError(f"插件 {name} 已被注册")

        if cli_version != CLI_VERSION:
            message = f"插件 {name} 的 CLI 版本 {cli_version} 与当前 CLI 版本 {CLI_VERSION} 不匹配"
            if self.cli_version_mismatch == 'stop':
                raise PluginVersionMismatchError(
                    message, details={"plugin": name, "expected": CLI_VERSION, "actual": cli_version}
                )
            elif self.cli_version_mismatch == 'warn':
                print(f"警告: {message}")
        self.plugins_list[name] = {"version": version, "cli_version": cli_version, "events": list(events or []), "pre_plugin": dict(pre_plugins or {}), **metadata}

    def delete(self, name: str):
        if name in self.plugins_list:
            del self.plugins_list[name]
        else:
            raise PluginNotFoundError(f"插件 {name} 未找到")

    def get(self, name: str):
        return self.plugins_list.get(name)

    def calculate_pre_legality_of_all_plugins(self):
        """
        计算所有插件的前置合法性。
        如果某个插件的前置插件不存在或版本不匹配，则抛出异常。
        没有问题则返回True。
        """
        for name, meta in self.plugins_list.items():
            pre_plugins = meta.get('pre_plugin', {})
            for pre_name, pre_version in pre_plugins.items():
                if pre_name not in self.plugins_list:
                    raise PluginNotFoundError(f"插件 {name} 的前置插件 {pre_name} 未找到")
                if self.plugins_list[pre_name]['version'] != pre_version:
                    raise PluginVersionMismatchError(
                        f"插件 {name} 的前置插件 {pre_name} 版本不匹配",
                        details={"plugin": name, "pre_plugin": pre_name,
                                 "expected": pre_version, "actual": self.plugins_list[pre_name]['version']}
                    )
        return True

    def get_plugin_list(self):
        return self.plugins_list

    def get_pre_plugins(self, name: str):
        """
        获取指定插件的前置插件列表。
        返回格式: {pre_plugin_name: pre_plugin_version, ...}
        """
        plugin_meta = self.get(name)
        if not plugin_meta:
            raise PluginNotFoundError(f"插件 {name} 未找到")
        return plugin_meta.get('pre_plugin', {})

    def _read_manifest(self, root: Path) -> dict[str, Any]:
        path = root / PLUGIN_MANIFEST
        if not path.is_file():
            raise PluginManifestError(f"插件缺少 {PLUGIN_MANIFEST}: {root}")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PluginManifestError(f"插件清单无法读取: {path}") from error
        required = ("name", "entry", "version", "author", "cli_version", "description", "hash")
        missing = [key for key in required if not manifest.get(key)]
        if missing:
            raise PluginManifestError(
                f"插件清单缺少字段: {', '.join(missing)}", details={"manifest": str(path)}
            )
        if ":" not in manifest["entry"]:
            raise PluginEntryError(
                "插件 entry 必须使用 module.py:function 格式",
                details={"plugin": manifest["name"], "entry": manifest["entry"]}
            )
        if manifest["hash"].lower() != _content_hash(root):
            raise PluginHashMismatchError(
                f"插件 {manifest['name']} 的 hash 校验失败",
                details={"plugin": manifest["name"], "expected": manifest["hash"], "actual": _content_hash(root)}
            )
        return manifest

    def _extract_archive(self, archive: Path) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="xdclassmate-plugin-")
        self._temporary_roots.append(temporary)
        root = Path(temporary.name)
        try:
            package = zipfile.ZipFile(archive)
        except (OSError, zipfile.BadZipFile) as error:
            raise PluginArchiveError(f"插件压缩包无法读取: {archive}") from error
        with package:
            for member in package.infolist():
                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise PluginArchiveError(
                        f"插件压缩包包含非法路径: {member.filename}", details={"archive": str(archive)}
                    )
            package.extractall(root)
        manifests = list(root.rglob(PLUGIN_MANIFEST))
        if len(manifests) != 1:
            raise PluginArchiveError(
                ".xdplug 必须包含唯一插件清单", details={"archive": str(archive), "found": len(manifests)}
            )
        return manifests[0].parent

    def _load_entry(self, root: Path, manifest: dict[str, Any]) -> Any:
        module_name, function_name = manifest["entry"].rsplit(":", 1)
        module_path = (root / module_name).resolve()
        if root.resolve() not in module_path.parents:
            raise PluginEntryError("插件入口不能跳出插件目录", details={"plugin": manifest["name"]})
        if not module_path.is_file() or module_path.suffix != ".py":
            raise PluginEntryError(
                f"插件入口文件不存在: {module_name}", details={"plugin": manifest["name"], "entry": manifest["entry"]}
            )
        unique_name = f"xdclassmate_plugin_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(unique_name, module_path)
        if spec is None or spec.loader is None:
            raise PluginEntryError(f"无法创建插件模块: {module_name}", details={"plugin": manifest["name"]})
        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as error:
            sys.modules.pop(unique_name, None)
            raise PluginEntryError(
                f"插件入口加载失败: {manifest['name']}", details={"plugin": manifest["name"]}
            ) from error
        entry = getattr(module, function_name, None)
        if not callable(entry):
            raise PluginEntryError(
                f"插件入口函数不存在: {manifest['entry']}", details={"plugin": manifest["name"]}
            )
        return entry

    def load_plugins(self):
        """扫描插件目录并加载所有合法插件。

        单个插件校验失败（清单缺失/hash 不匹配/入口异常等）只会拒绝加载该插件
        并打印错误信息，不会中断其余插件加载，也不会让 CLI 整体崩溃。
        """
        for candidate in sorted(self.plugins_dir.iterdir(), key=lambda path: path.name):
            if candidate.is_dir() and not candidate.name.startswith("."):
                root = candidate
            elif candidate.is_file() and candidate.suffix == ".xdplug":
                root = self._extract_archive(candidate)
            else:
                continue
            try:
                manifest = self._read_manifest(root)
                entry = self._load_entry(root, manifest)
                self.register(manifest["name"], manifest["version"], manifest["cli_version"],
                              manifest.get("events"), manifest.get("pre_plugins"),
                              author=manifest["author"], description=manifest["description"],
                              hash=manifest["hash"], entry=entry, path=str(candidate))
            except PluginException as error:
                # 拒绝加载该插件，继续处理后续插件
                print(f"错误: 跳过插件 {candidate.name}: {error}", file=sys.stderr)

    def register_plugins_with_event_bus(self):
        """
        将所有插件注册到事件总线。
        """
        for meta in self.plugins_list.values():
            entry = meta["entry"]
            bus.on("plugin_init", entry, once=True)
            for event in meta["events"]:
                if event == "plugin_init":
                    continue
                bus.on(event, entry)

pl = Plugins()

def XDPlugin(name: Optional[str] = None, version: str = '1.0', cli_version: str = CLI_VERSION, events: Optional[list[str]] = None, pre_plugins: Optional[dict[str, str]] = None):
    """
    装饰器：标记插件入口函数，并自动注册到事件总线。
    Parameters:
        name:           插件名称，若未提供则使用函数名
        version:        插件版本
        cli_version:    CLI 版本
        events:         插件监听的事件列表
        pre_plugins:    插件的前置插件列表
    用法：
        @XDPlugin(name='my-plugin', version='2.0', cli_version='1.0', events=['before_command'], pre_plugin=None)
        def my_plugin_entry():
            ...
    """
    def decorator(func):
        plugin_name = name or func.__name__
        pl.register(plugin_name, version, cli_version, events, pre_plugins, entry=func)
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        
        bus.on('plugin_init', wrapper, once=True)
        
        # 如果插件声明了要监听其他事件，也一并注册
        for evt in events or []:
            bus.on(evt, wrapper)
        
        return wrapper
    return decorator