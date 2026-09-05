"""插件管理器：扫描、校验、加载并登记插件。

加载流程（由微内核 core.kernel 驱动）：
    1. 扫描插件目录下的子目录（目录插件）与 .xdplug 文件（压缩包插件）；
    2. 读取并校验清单 xdclassmate.cli.setting.json；
    3. 通过清单 url 指向的摘要文件做完整性校验（url 为 null 时跳过）；
    4. 动态加载入口模块（插件根目录加入模块搜索路径，支持插件内子包）；
    5. 剔除依赖不满足的插件（容错，不阻断 CLI 启动）；
    6. 按前置依赖拓扑序把入口函数注册到 plugin_init 事件。

设计要点：
    * 本模块**不在导入时**创建实例（避免任意 cwd 下自动生成 plugins 目录），
      由微内核通过 get_plugins() 惰性创建；
    * 单个插件的任何问题都只影响该插件，CLI 永远可以启动。
"""
from __future__ import annotations

import importlib.util
import json
import re
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
    PluginDependencyError,
    PluginEntryError,
    PluginException,
    PluginIntegrityError,
    PluginManifestError,
    PluginNotFoundError,
    PluginVersionMismatchError,
)
from .i18n import t
from .integrity import DEFAULT_ALGORITHM, content_digest, verify_plugin
from .logger import get_logger

LOGGER = get_logger("plugins")

# 插件清单文件名
PLUGIN_MANIFEST = "xdclassmate.cli.setting.json"
# CLI 版本不匹配时的处理策略
CLIVersionMismatch: TypeAlias = Literal["ignore", "warn", "stop"]
# 插件模块命名空间前缀，避免与宿主及其他插件撞名
PLUGIN_MODULE_PREFIX = "xdclassmate.plugin"
# 清单必填字段（url 为可选：本地安装时可设为 null）
REQUIRED_FIELDS = (
    "name", "entry", "version", "author", "cli_version", "description",
)
# 插件根目录中若出现这些名字的 .py 文件，会让 ``import os`` 等解析到
# 插件实现，而不是 Python 标准库，是真实可见的安全/稳定性风险。
# 这里只做 *告警*，不强制拒绝（保留生态兼容），但要让作者能立刻看出来。
_STDLIB_SHADOW_NAMES = frozenset({
    "os", "sys", "json", "re", "io", "abc", "io", "logging", "pathlib",
    "shutil", "subprocess", "tempfile", "urllib", "zipfile", "threading",
    "socket", "ssl", "email", "html", "http", "xml", "types", "weakref",
})
# 单文件加载属于 ``from package import module`` 这类相对导入时被遮蔽的
# 子包名，命中即告警（与 STDLIB_SHADOW_NAMES 取并集）。


def _content_hash(root: Path) -> str:
    """兼容旧接口：按默认算法计算插件内容摘要。"""
    return content_digest(root)


def _safe_module_name(name: str) -> str:
    """把插件名转换成合法的模块名片段。"""
    return re.sub(r"[^0-9a-zA-Z_]", "_", name).strip("_") or "plugin"


# 清单 languages_dir 里可能出现的「插件目录」占位符写法。
# 历史上出现过三种风格，示例插件用的是 %()s 那一种，这里全部兼容，
# 保证老清单不会因为占位符写法不同而被静默跳过语言包。
# 注意替换顺序：``${plugin_dir}`` 必须先于 ``{plugin_dir}``，否则
# 后者会把 ``$`` 留下来。``%`` 风格用 ``str.replace`` 一次性整段替换，
# 不会被 ``{plugin_dir}`` 误吃。
_PLUGIN_DIR_PLACEHOLDERS = (
    "${plugin_dir}",   # 模板字符串风格（先于 {plugin_dir}）
    "{plugin_dir}",     # str.format 风格
    "%(plugin_dir)s",   # % 风格
)


def strip_plugin_dir_placeholder(raw: str, default: str = "languages") -> str:
    """
    剥掉 languages_dir 中的插件目录占位符，只保留相对部分。

    兼容写法（前三种为历史遗留，最后一种为推荐写法）：

        {plugin_dir}/languages     —— str.format 风格
        %(plugin_dir)s/languages   —— % 风格（示例插件在用）
        ${plugin_dir}/languages    —— 模板字符串风格
        languages                  —— 推荐，直接写相对路径

    占位符由框架在加载时补上插件根目录，清单里写出来反而容易写错风格，
    因此这里统一剥掉；剥完为空时回退到 default。
    ``./languages`` / ``/languages`` 形式也归一为 ``languages``。

    :param raw:     清单中的 languages / languages_dir 原值
    :param default: 剥完为空时的回退值
    :return:        相对插件根目录的语言包目录名
    """
    text = str(raw).strip()
    for token in _PLUGIN_DIR_PLACEHOLDERS:
        text = text.replace(token, "")
    # 占位符被剥掉后可能留下前导分隔符（/languages、\\languages）
    text = text.lstrip("/\\").strip()
    # ``./languages`` 也归一为 languages
    if text.startswith("./"):
        text = text[2:].lstrip("/\\").strip()
    return text or default


class Plugins:
    """插件管理器：扫描、校验并加载插件。"""

    def __init__(
            self,
            plugins_dir: Optional[str] = None,
            cli_version_mismatch: CLIVersionMismatch = "warn"
            ):
        """
        :param plugins_dir:          插件目录，缺省读取配置的 plugin_dir
        :param cli_version_mismatch: CLI 版本不匹配时的处理策略
        """
        config = ConfigManager()
        configured_dir = config.load_config("plugin_dir", default="./plugins")
        self.plugins_dir = Path(plugins_dir or configured_dir).expanduser()
        if not self.plugins_dir.is_absolute():
            self.plugins_dir = Path.cwd() / self.plugins_dir
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.cli_version_mismatch = cli_version_mismatch
        self.plugins_list: dict[str, dict[str, Any]] = {}
        self._temporary_roots: list[tempfile.TemporaryDirectory[str]] = []
        # 已加入 sys.path 的插件根目录，便于排查模块搜索顺序
        self._sys_path_entries: list[str] = []
        # 宿主基线模块名（仅顶层），用于发现插件对宿主模块的遮蔽
        self._host_modules: set[str] = {
            name.split(".")[0] for name in sys.modules
        }

        LOGGER.info("开始扫描插件目录 %s", self.plugins_dir)
        self.load_plugins()
        self._drop_unmet_dependency_plugins()
        self.register_plugins_with_event_bus()

    # ------------------------------------------------------------------
    # 注册与查询
    # ------------------------------------------------------------------
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
        :param metadata:     其余元数据（作者、描述、url、入口、路径等）
        """
        if not name:
            raise PluginException(
                "插件名称不能为空",
                key="error.plugin_name_empty",
            )
        if name in self.plugins_list:
            raise DuplicatePluginNamesError(
                f"插件 {name} 已被注册",
                key="error.plugin_duplicate",
                params={"name": name},
                details={"plugin": name},
            )

        if cli_version != CLI_VERSION:
            message = t(
                "error.reason.cli_version_mismatch",
                name=name, actual=cli_version, expected=CLI_VERSION,
            )
            if self.cli_version_mismatch == "stop":
                raise PluginVersionMismatchError(
                    message,
                    key="error.plugin_version",
                    params={"reason": message},
                    details={
                        "plugin": name,
                        "expected": CLI_VERSION,
                        "actual": cli_version,
                    },
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
                f"插件 {name} 未找到",
                key="error.plugin_not_found",
                params={"name": name},
                details={"plugin": name},
            )

    def get(self, name: str) -> Optional[dict[str, Any]]:
        """按名称获取插件信息，不存在返回 None。"""
        return self.plugins_list.get(name)

    def get_plugin_list(self) -> dict[str, dict[str, Any]]:
        """获取全部已注册插件。"""
        return self.plugins_list

    def find_by_path_alias(self, alias: str) -> Optional[dict[str, Any]]:
        """
        按任意「叫法」查找插件：清单名、目录名、压缩包名均可。

        同一插件可能有三种不同的标识：
            * 清单的 ``name``     （如 ``"Image Processing"``）
            * 插件目录名          （如 ``"image"``）
            * 压缩包文件名（去后缀）（如 ``"image-1.0.0"`` → 仍指向 ``image``）

        :param alias: 任何一种叫法
        :return:       第一个匹配的插件元数据；未找到返回 None
        """
        if not alias:
            return None
        # 1. 直接按清单名查找（最常见的写法）
        direct = self.plugins_list.get(alias)
        if direct:
            return direct
        # 2. 按目录名 / 压缩包名反查
        target_stem = Path(alias).stem  # 去后缀
        for meta in self.plugins_list.values():
            path = meta.get("path") or ""
            if not path:
                continue
            candidate = Path(path)
            if candidate.name == alias or candidate.stem == target_stem:
                return meta
            # 压缩包：name-version.xdplug -> name 与目录同
            if candidate.suffix == ".xdplug" and candidate.stem.startswith(
                    target_stem + "-"):
                return meta
        return None

    def plugin_names(self) -> list[str]:
        """返回全部已加载插件的清单名列表（便于对外暴露）。"""
        return list(self.plugins_list)

    def get_pre_plugins(self, name: str) -> dict[str, str]:
        """
        获取指定插件的前置插件表。

        :return: {前置插件名: 版本}
        """
        plugin_meta = self.get(name)
        if not plugin_meta:
            raise PluginNotFoundError(
                f"插件 {name} 未找到",
                key="error.plugin_not_found",
                params={"name": name},
                details={"plugin": name},
            )
        return plugin_meta.get("pre_plugin", {})

    def validate_dependencies(self) -> list[str]:
        """
        校验全部插件的前置依赖。

        :return: 问题描述列表，空列表表示全部满足
        """
        problems: list[str] = []
        for name, meta in self.plugins_list.items():
            for pre_name, pre_version in meta.get("pre_plugin", {}).items():
                target = self.plugins_list.get(pre_name)
                if target is None:
                    problems.append(
                        f"{name}: 前置插件 {pre_name} 未加载"
                    )
                elif target["version"] != pre_version:
                    problems.append(
                        f"{name}: 前置插件 {pre_name} 版本不匹配"
                        f"（需要 {pre_version}，实际 {target['version']}）"
                    )
        return problems

    # ------------------------------------------------------------------
    # 清单与完整性
    # ------------------------------------------------------------------
    def _read_manifest(self, root: Path) -> dict[str, Any]:
        """读取并校验插件清单。"""
        path = root / PLUGIN_MANIFEST
        if not path.is_file():
            raise PluginManifestError(
                f"插件缺少 {PLUGIN_MANIFEST}: {root}",
                key="error.plugin_manifest",
                params={"reason": t(
                    "error.reason.manifest_missing", file=PLUGIN_MANIFEST
                )},
                details={"root": str(root)},
            )
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PluginManifestError(
                f"插件清单无法读取: {path}",
                key="error.plugin_manifest",
                params={"reason": t(
                    "error.reason.manifest_unparsable", error=error
                )},
                details={"manifest": str(path)},
            ) from error

        missing = [key for key in REQUIRED_FIELDS if not manifest.get(key)]
        if missing:
            raise PluginManifestError(
                f"插件清单缺少字段: {', '.join(missing)}",
                key="error.plugin_manifest",
                params={"reason": t(
                    "error.reason.manifest_missing_fields",
                    fields=", ".join(missing),
                )},
                details={"manifest": str(path)},
            )
        if ":" not in manifest["entry"]:
            raise PluginEntryError(
                "插件 entry 必须使用 module.py:function 格式",
                key="error.plugin_entry",
                params={"reason": t("error.reason.entry_format")},
                details={
                    "plugin": manifest["name"],
                    "entry": manifest["entry"],
                },
            )

        # 完整性校验：url 为空表示本地安装/调试，跳过校验
        url = manifest.get("url")
        algorithm = manifest.get("algorithm", DEFAULT_ALGORITHM)
        inline_hash = manifest.get("hash")
        if url is None and inline_hash:
            # 兼容旧清单：hash 字段视为内联期望摘要
            LOGGER.warning(
                "插件 %s 使用已废弃的 hash 字段，建议改用 url",
                manifest["name"]
            )
            actual = content_digest(root, algorithm)
            if str(inline_hash).lower() != actual:
                raise PluginIntegrityError(
                    f"插件 {manifest['name']} 的 {algorithm} 校验失败",
                    key="error.plugin_hash_mismatch",
                    params={"plugin": manifest["name"]},
                    details={
                        "plugin": manifest["name"],
                        "algorithm": algorithm,
                        "expected": inline_hash,
                        "actual": actual,
                    },
                )
        else:
            verify_plugin(root, url, algorithm)

        manifest["_verified_url"] = url
        return manifest

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
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
                key="error.plugin_archive",
                params={"reason": t(
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
                        params={"reason": t(
                            "error.reason.archive_illegal_path",
                            path=member.filename,
                        )},
                        details={"archive": str(archive)},
                    )
            package.extractall(root)
        manifests = list(root.rglob(PLUGIN_MANIFEST))
        if len(manifests) != 1:
            raise PluginArchiveError(
                ".xdplug 必须包含唯一插件清单",
                key="error.plugin_archive",
                params={"reason": t(
                    "error.reason.archive_manifest_count",
                    count=len(manifests),
                )},
                details={"archive": str(archive), "found": len(manifests)},
            )
        return manifests[0].parent

    def _prepare_sys_path(self, root: Path) -> None:
        """
        把插件根目录加入模块搜索路径。

        这样插件内部的子包（如 tools/）才能被 import 解析；
        插件目录优先于宿主目录，避免与项目根目录下的同名包混淆。
        """
        entry = str(root)
        if entry in self._sys_path_entries:
            return
        # 先做 stdlib 遮蔽检测；命中即 *仍然* 加载（生态兼容），但要把
        # 风险曝给作者。避免插件里手贱写个 ``os.py`` 把全局 ``import os``
        # 拐到插件实现上。
        shadowed: list[str] = []
        for child in root.iterdir():
            if not child.is_file() or child.suffix != ".py":
                continue
            stem = child.stem
            if stem in _STDLIB_SHADOW_NAMES or stem == "tools":
                shadowed.append(stem)
        if shadowed:
            LOGGER.warning(
                "插件根 %s 包含可能遮蔽标准库的模块名: %s"
                "（import 同名模块时会优先命中插件版本）",
                root, ", ".join(sorted(set(shadowed)))
            )
        sys.path.insert(0, entry)
        self._sys_path_entries.append(entry)
        LOGGER.debug("插件根目录已加入模块搜索路径: %s", root)

    def _snapshot_modules(self) -> set[str]:
        """记录当前已加载模块，用于后续发现插件引入的新模块。"""
        return set(sys.modules)

    def _warn_module_shadowing(
            self,
            plugin_name: str,
            before: set[str]
            ) -> None:
        """
        发现插件引入的顶层模块与宿主同名时给出警告。

        例如插件自带 tools/ 子包，而宿主项目根目录也有 tools/ 包，
        此时导入顺序决定了谁生效，属于极易踩坑的隐性问题。
        """
        added = {
            name.split(".")[0] for name in set(sys.modules) - before
        }
        shadowed = sorted(added & self._host_modules)
        if shadowed:
            LOGGER.warning(
                "插件 %s 引入的模块与宿主同名，可能遮蔽宿主实现: %s",
                plugin_name, ", ".join(shadowed)
            )

    def _load_entry(self, root: Path, manifest: dict[str, Any]) -> Any:
        """动态加载插件入口模块，返回入口可调用对象。"""
        module_name, function_name = manifest["entry"].rsplit(":", 1)
        module_path = (root / module_name).resolve()
        if root.resolve() not in module_path.parents:
            raise PluginEntryError(
                "插件入口不能跳出插件目录",
                key="error.plugin_entry",
                params={"reason": t("error.reason.entry_escape")},
                details={"plugin": manifest["name"]},
            )
        if not module_path.is_file() or module_path.suffix != ".py":
            raise PluginEntryError(
                f"插件入口文件不存在: {module_name}",
                key="error.plugin_entry",
                params={"reason": t(
                    "error.reason.entry_file_missing", file=module_name
                )},
                details={
                    "plugin": manifest["name"],
                    "entry": manifest["entry"],
                },
            )

        # 插件根目录入 sys.path，使插件内的子包可被导入
        self._prepare_sys_path(root)
        unique_name = (
            f"{PLUGIN_MODULE_PREFIX}."
            f"{_safe_module_name(manifest['name'])}."
            f"{module_path.stem}.{uuid.uuid4().hex}"
        )
        spec = importlib.util.spec_from_file_location(unique_name, module_path)
        if spec is None or spec.loader is None:
            raise PluginEntryError(
                f"无法创建插件模块: {module_name}",
                key="error.plugin_entry",
                params={"reason": t(
                    "error.reason.entry_module", module=module_name
                )},
                details={"plugin": manifest["name"]},
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = module
        before = self._snapshot_modules()
        try:
            spec.loader.exec_module(module)
        except Exception as error:
            sys.modules.pop(unique_name, None)
            raise PluginEntryError(
                f"插件入口加载失败: {manifest['name']}（{error}）",
                key="error.plugin_entry",
                params={"reason": str(error)},
                details={"plugin": manifest["name"]},
            ) from error
        self._warn_module_shadowing(manifest["name"], before)

        entry = getattr(module, function_name, None)
        if not callable(entry):
            raise PluginEntryError(
                f"插件入口函数不存在: {manifest['entry']}",
                key="error.plugin_entry",
                params={"reason": t(
                    "error.reason.entry_function_missing",
                    entry=manifest["entry"],
                )},
                details={"plugin": manifest["name"]},
            )
        return entry

    def _load_plugin_languages(
            self,
            root: Path,
            manifest: dict[str, Any]
            ) -> None:
        """
        把插件自带的语言包（由清单指定目录）合并进全局 i18n。

        插件语言包不再集成在 CLI 的 core/i18n/languages，而是放在插件自身
        目录内（也会被打进 .xdplug），清单中声明相对目录，推荐写法：
            "languages": "languages"
        也兼容旧写法（含插件目录占位符，会被自动剥掉）：
            "languages_dir": "languages"
            "languages_dir": "{plugin_dir}/languages"
            "languages_dir": "%(plugin_dir)s/languages"
        缺省目录为 languages。

        与旧实现的关键差别：目录不存在时会给出 WARNING（而不是 DEBUG），
        避免占位符写错这类问题被静默吞掉——语言包没加载的表现是界面直接
        吐出翻译键名，排查成本很高。
        """
        rel = strip_plugin_dir_placeholder(
            manifest.get("languages")
            or manifest.get("languages_dir")
            or "languages"
        )
        lang_dir = root / rel
        if not lang_dir.is_dir():
            LOGGER.warning(
                "插件 %s 的语言目录不存在，已跳过（清单值 %r，解析为 %s）；"
                "推荐直接写相对路径 \"languages\"",
                manifest["name"],
                manifest.get("languages") or manifest.get("languages_dir"),
                rel,
            )
            return
        from .i18n import get_i18n
        i18n = get_i18n()
        loaded = i18n.load_pack_from_directory(lang_dir)
        if loaded:
            LOGGER.info(
                "插件 %s 已加载 %s 个语言包（%s）",
                manifest["name"], loaded, lang_dir
            )

    def load_plugins(self) -> None:
        """
        扫描插件目录并加载所有合法插件。

        单个插件校验失败只跳过该插件并记录错误日志，不会中断其余插件加载，
        也不会让 CLI 崩溃。
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
                # 入口加载成功后再并入插件自带语言包，保证命令执行时键已就绪
                self._load_plugin_languages(root, manifest)
                self.register(
                    manifest["name"],
                    manifest["version"],
                    manifest["cli_version"],
                    manifest.get("events"),
                    manifest.get("pre_plugins"),
                    author=manifest["author"],
                    description=manifest["description"],
                    url=manifest.get("url"),
                    algorithm=manifest.get("algorithm", DEFAULT_ALGORITHM),
                    entry=entry,
                    path=str(candidate),
                )
            except PluginException as error:
                # 拒绝加载该插件，继续处理后续插件
                LOGGER.error("跳过插件 %s: %s", candidate.name, error)

    # ------------------------------------------------------------------
    # 依赖处理与事件注册
    # ------------------------------------------------------------------
    def _drop_unmet_dependency_plugins(self) -> None:
        """
        剔除依赖不满足的插件（容错）。

        早期实现在这里直接抛异常，会因为一个插件的依赖写错而让整个 CLI
        无法启动。改为逐个剔除 + 循环重检，直到剩余插件的依赖全部满足；
        被剔除的插件会记录 ERROR 日志。
        """
        while True:
            victim = None
            for name, meta in self.plugins_list.items():
                for pre_name, pre_version in (
                    meta.get("pre_plugin", {}).items()
                ):
                    target = self.plugins_list.get(pre_name)
                    if target is None:
                        victim = (name, f"前置插件 {pre_name} 未加载")
                    elif target["version"] != pre_version:
                        victim = (
                            name,
                            f"前置插件 {pre_name} 版本不匹配"
                            f"（需要 {pre_version}，实际 {target['version']}）"
                        )
                    if victim:
                        break
                if victim:
                    break
            if not victim:
                return
            name, reason = victim
            self.plugins_list.pop(name, None)
            LOGGER.error("移除插件 %s：%s", name, reason)

    def _resolve_init_order(self) -> list[str]:
        """
        按前置依赖做拓扑排序，返回插件初始化顺序。

        :raises PluginDependencyError: 存在循环依赖
        """
        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise PluginDependencyError(
                    f"插件存在循环依赖: {name}",
                    key="error.plugin_dependency",
                    params={"reason": t(
                        "error.reason.cycle_dependency", plugin=name
                    )},
                    details={"plugin": name},
                )
            visiting.add(name)
            for pre_name in self.plugins_list[name].get("pre_plugin", {}):
                if pre_name in self.plugins_list:
                    visit(pre_name)
            visiting.discard(name)
            visited.add(name)
            order.append(name)

        for name in list(self.plugins_list):
            visit(name)
        LOGGER.debug("插件初始化顺序: %s", " -> ".join(order) or "(空)")
        return order

    def register_plugins_with_event_bus(self) -> None:
        """按依赖拓扑序把插件入口注册到事件总线。"""
        try:
            order = self._resolve_init_order()
        except PluginDependencyError as error:
            LOGGER.error("%s，退化为按名称顺序初始化", error)
            order = list(self.plugins_list)
        for name in order:
            meta = self.plugins_list[name]
            entry = meta["entry"]
            bus.on("plugin_init", entry, once=True)
            for event in meta["events"]:
                if event == "plugin_init":
                    continue
                bus.on(event, entry)
        LOGGER.info(
            "插件加载完成，共 %s 个：%s",
            len(self.plugins_list),
            ", ".join(self.plugins_list) or "(无)"
        )


# ----------------------------------------------------------------------
# 惰性单例：导入本模块不会产生任何副作用
# ----------------------------------------------------------------------
_PLUGINS: Optional[Plugins] = None


def get_plugin_path(name: str) -> Optional[str]:
    """获取已注册插件的路径，未注册返回 None。"""
    global _PLUGINS
    if _PLUGINS is None:
        return None
    meta = _PLUGINS.get(name)
    if not meta:
        return None
    return meta.get("path")


def get_plugins(plugins_dir: Optional[str] = None) -> Plugins:
    """
    获取插件管理器实例（惰性创建）。

    :param plugins_dir: 传入时创建独立实例；否则返回全局单例
    """
    global _PLUGINS
    if plugins_dir is not None:
        LOGGER.debug("创建独立插件管理器: %s", plugins_dir)
        return Plugins(plugins_dir=plugins_dir)
    if _PLUGINS is None:
        _PLUGINS = Plugins()
    return _PLUGINS


def set_plugins(instance: Optional[Plugins]) -> None:
    """设置全局插件管理器实例（供微内核与测试使用）。"""
    global _PLUGINS
    _PLUGINS = instance
    LOGGER.debug("已设置插件管理器实例: %s", instance)


# 这个我做来原本是相当装饰器用的(@XDPlugin)
# 但是有了main.py:main之后我发现这个好像多余了
# 算了先不删(
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
    import functools

    def decorator(func):
        plugin_name = name or func.__name__
        get_plugins().register(
            plugin_name, version, cli_version, events, pre_plugins,
            entry=func
        )

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        bus.on("plugin_init", wrapper, once=True)
        for event in events or []:
            bus.on(event, wrapper)
        return wrapper

    return decorator
