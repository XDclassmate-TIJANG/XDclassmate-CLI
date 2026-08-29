"""XD-CLI 统一异常定义。

设计约定：
    * 所有框架异常都继承 `XDclassmateCLIException`，调用方只需捕获基类即可兜底；
    * 每个异常类自带 `code`（错误码）与 `default_message`（默认提示），
      便于日志统计、文档索引和用户反馈时精确定位问题；
    * `details` 用于携带结构化上下文（插件名、命令路径、期望值/实际值等），
      会附加在 `__str__` 末尾，不影响 isinstance 判定。

错误码分段（XD-CLI-XXXX）：
    1xxx  配置（Config）
    2xxx  插件（Plugin）
    3xxx  命令（Command）
    4xxx  远程 / 安装（Remote）
"""
from __future__ import annotations

from typing import Any, Optional

__all__ = [
    "XDclassmateCLIException",
    # 配置
    "ConfigException", "ConfigFileError",
    # 插件
    "PluginException", "PluginNotFoundError", "DuplicatePluginNamesError",
    "PluginManifestError", "PluginHashMismatchError", "PluginEntryError",
    "PluginArchiveError", "PluginVersionMismatchError", "PluginIntegrityError",
    "PluginDependencyError",
    # 命令
    "CommandException", "CommandNotFoundError", "DuplicateCommandNamesError",
    "CommandSpaceNotFoundError", "DuplicateCommandSpaceNamesError",
    "CommandSpaceDepthExceededError", "InvalidCommandSpaceNameError",
    "CommandExecutionError", "CommandArgumentException",
    "DuplicateOptionNamesError",
    # 远程 / 安装
    "RemoteException", "RemoteNotConfiguredError",
    "PluginNotInRepositoryError", "RemoteDownloadError",
    "PluginNotInstalledError",
]


class XDclassmateCLIException(Exception):
    """
    XD-CLI 异常基类。

    :param message: 具体错误信息；为 None 时使用类默认提示
    :param code:    覆盖默认错误码（一般无需指定）
    :param details: 结构化上下文，形如 {"plugin": "hello", "expected": "1.0"}
    """

    code: str = "XD-CLI-0000"
    default_message: str = "XD-CLI 发生未知错误"

    def __init__(
            self,
            message: Optional[str] = None,
            *,
            key: Optional[str] = None,
            params: Optional[dict[str, Any]] = None,
            code: Optional[str] = None,
            details: Optional[dict[str, Any]] = None
            ):
        """
        :param message: 错误信息（用于日志，缺省使用类默认提示）
        :param key:     国际化键；提供后展示层可按当前语言翻译
        :param params:  国际化占位符参数
        :param code:    覆盖默认错误码（一般无需指定）
        :param details: 结构化上下文，会附加在字符串表示末尾
        """
        self.message = message or self.default_message
        self.code = code or self.code
        self.key = key
        self.params: dict[str, Any] = dict(params or {})
        self.details: dict[str, Any] = dict(details or {})
        # 传给基类完整文本，保证未使用本项目 __str__ 的地方也能看到信息
        super().__init__(self.message)

    def as_params(self) -> dict[str, Any]:
        """拼接国际化参数：占位符参数 + 结构化上下文 + 错误码与原始信息。"""
        merged = dict(self.params)
        merged.update(self.details)
        merged.setdefault("code", self.code)
        merged.setdefault("message", self.message)
        return merged

    def translated(self, translate) -> str:
        """
        按当前语言生成本地化文本。

        :param translate: 翻译函数，签名与 I18n.t 一致
        :return:          有国际化键时返回翻译文本，否则返回原始字符串
        """
        if not self.key:
            return str(self)
        text = translate(self.key, **self.as_params())
        # 翻译键缺失时 t() 会返回键名，此时退回原始信息
        if text == self.key:
            return str(self)
        return f"[{self.code}] {text}"

    def __str__(self) -> str:
        text = f"[{self.code}] {self.message}"
        if self.details:
            pairs = [f"{key}={value}" for key, value in self.details.items()]
            text = f"{text} ({', '.join(pairs)})"
        return text

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} code={self.code} "
            f"message={self.message!r}>"
        )


# ==================================================================
# 1xxx 配置相关
# ==================================================================
class ConfigException(XDclassmateCLIException):
    """配置异常基类"""
    code = "XD-CLI-1000"
    default_message = "配置错误"


class ConfigFileError(ConfigException):
    """配置文件损坏或格式非法（例如根节点不是 JSON 对象）"""
    code = "XD-CLI-1001"
    default_message = "配置文件格式非法"


# ==================================================================
# 2xxx 插件相关
# ==================================================================
class PluginException(XDclassmateCLIException):
    """插件异常基类"""
    code = "XD-CLI-2000"
    default_message = "插件错误"


class PluginNotFoundError(PluginException):
    """插件未找到（包括前置插件缺失）"""
    code = "XD-CLI-2001"
    default_message = "插件未找到"


class DuplicatePluginNamesError(PluginException):
    """插件名称重复"""
    code = "XD-CLI-2002"
    default_message = "插件名称重复"


class PluginManifestError(PluginException):
    """插件清单缺失、无法解析或必填字段不完整"""
    code = "XD-CLI-2003"
    default_message = "插件清单错误"


class PluginHashMismatchError(PluginException):
    """插件内容 hash 与清单声明不一致（内容被篡改或未同步 hash）"""
    code = "XD-CLI-2004"
    default_message = "插件内容 hash 校验失败"


class PluginEntryError(PluginException):
    """插件入口格式错误、文件缺失、加载失败或不可调用"""
    code = "XD-CLI-2005"
    default_message = "插件入口错误"


class PluginArchiveError(PluginException):
    """.xdplug 压缩包无法读取、包含非法路径或清单数量不为 1"""
    code = "XD-CLI-2006"
    default_message = "插件压缩包错误"


class PluginVersionMismatchError(PluginException):
    """CLI 版本不匹配，或前置插件版本不满足要求"""
    code = "XD-CLI-2007"
    default_message = "插件版本不匹配"


class PluginIntegrityError(PluginException):
    """完整性校验过程出错：hash 文件 URL 无法获取、算法不支持等"""
    code = "XD-CLI-2008"
    default_message = "插件完整性校验错误"


class PluginDependencyError(PluginException):
    """插件依赖关系非法：循环依赖等"""
    code = "XD-CLI-2009"
    default_message = "插件依赖错误"


# ==================================================================
# 3xxx 命令相关
# ==================================================================
class CommandException(XDclassmateCLIException):
    """命令异常基类"""
    code = "XD-CLI-3000"
    default_message = "命令错误"


class CommandNotFoundError(CommandException):
    """命令未找到（含只输入了命令空间而缺少命令名的情况）"""
    code = "XD-CLI-3001"
    default_message = "命令未找到"


class DuplicateCommandNamesError(CommandException):
    """同一命令空间内命令名称重复"""
    code = "XD-CLI-3002"
    default_message = "命令名称重复"


class CommandSpaceNotFoundError(CommandException):
    """命令空间未找到"""
    code = "XD-CLI-3003"
    default_message = "命令空间未找到"


class DuplicateCommandSpaceNamesError(CommandException):
    """同一父空间下命令空间名称重复"""
    code = "XD-CLI-3004"
    default_message = "命令空间名称重复"


class CommandSpaceDepthExceededError(CommandException):
    """命令空间嵌套层级超过允许的最大值（MAX_COMMAND_SPACE_DEPTH）"""
    code = "XD-CLI-3005"
    default_message = "命令空间嵌套层级超限"


class InvalidCommandSpaceNameError(CommandException):
    """命令空间名称非法（为空或包含路径分隔符）"""
    code = "XD-CLI-3006"
    default_message = "命令空间名称非法"


class CommandExecutionError(CommandException):
    """命令函数执行过程中抛出异常（原始异常通过 __cause__ 保留）"""
    code = "XD-CLI-3007"
    default_message = "命令执行失败"


class CommandArgumentException(CommandException):
    """命令参数/选项不合法：未知选项、缺少取值、选项与函数签名不匹配等"""
    code = "XD-CLI-3008"
    default_message = "命令参数错误"


class DuplicateOptionNamesError(CommandException):
    """同一命令下选项名称（含别名）重复注册"""
    code = "XD-CLI-3009"
    default_message = "命令选项名称重复"


# ==================================================================
# 4xxx 远程 / 安装相关
# ==================================================================
class RemoteException(XDclassmateCLIException):
    """远程仓库与安装操作异常基类"""
    code = "XD-CLI-4000"
    default_message = "远程操作错误"


class RemoteNotConfiguredError(RemoteException):
    """INSTALL_URL 未配置（无法拉取远程仓库）"""
    code = "XD-CLI-4001"
    default_message = "插件仓库地址未配置"


class PluginNotInRepositoryError(RemoteException):
    """插件不在远程仓库索引中（无法定位可下载条目）"""
    code = "XD-CLI-4002"
    default_message = "插件不在仓库中"


class RemoteDownloadError(RemoteException):
    """远程文件下载失败或仓库索引无法解析"""
    code = "XD-CLI-4003"
    default_message = "远程文件获取失败"


class PluginNotInstalledError(RemoteException):
    """卸载时插件未安装或无法通过任何路径定位"""
    code = "XD-CLI-4005"
    default_message = "插件未安装"
