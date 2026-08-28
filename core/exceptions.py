class XDclassmateCLIException(Exception):
    """XD-CLI 异常基类"""
    pass

# 插件相关
class PluginException(XDclassmateCLIException):
    """插件异常基类"""
    pass

class PluginNotFoundError(PluginException):
    """插件未找到异常"""
    pass

class DuplicatePluginNamesError(PluginException):
    """插件名称重复异常"""
    pass

# 命令相关
class CommandException(XDclassmateCLIException):
    """命令异常基类"""
    pass

class CommandSpaceNotFoundError(CommandException):
    """命令空间未找到异常"""
    pass

class DuplicateCommandSpaceNamesError(CommandException):
    """命令空间名称重复异常"""
    pass

class CommandNotFoundError(CommandException):
    """命令未找到异常"""
    pass

class DuplicateCommandNamesError(CommandException):
    """命令名称重复异常"""
    pass