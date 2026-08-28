from typing import Callable, Optional

from .event_bus import bus
from .exceptions import (
    CommandNotFoundError,
    DuplicateCommandNamesError,
    CommandSpaceNotFoundError,
    DuplicateCommandSpaceNamesError
)

class CommandRegistry:
    def __init__(self):
        # {"system": ["space1", "space2", ...]}
        self.command_space_list: dict[str, list[str]] = {}
        # {"command": ["function", ["event"]], "space", ...}
        self.command_list: dict[str, tuple[Callable, list[str], str]] = {}

        # 初始化系统命令
        def _init_system_command():
            pass

        # 当CLI初始化的时候同时初始化系统命令
        # once=True确保这些系统命令只会被添加一次
        # 虽然init_cli事件理论上只会激活一次
        bus.on("init_cli", _init_system_command, once=True)

    def register(
            self,
            name: str,
            function: Callable,
            event: Optional[list[str]] = None,
            commandspace: Optional[str] = None
            ) -> None:
        """
        注册一条命令。
        name:           命令名称
        function:       对应的处理函数（可调用对象）
        event:          可选的事件列表（字符串列表），用于 EventBus 触发
        commandspace:   所属命令空间，若为None则归入默认空间
        """
        if name in self.command_list:
            raise DuplicateCommandNamesError(f"命令 {name} 已被注册")
        
        if commandspace is None:
            commandspace = "default"

        if commandspace not in self.command_space_list:
            self.command_space_list[commandspace] = []

        self.command_space_list[commandspace].append(name)
        self.command_list[name] = (function, event or [], commandspace)

    def register_command_space(self, name):
        """
        注册一个命令空间。
        name:           命令空间名称
        """
        if name in self.command_space_list:
            raise DuplicateCommandSpaceNamesError(f"命令空间 {name} 已被注册")
        self.command_space_list[name] = []

    def modify_command(self, name, old_name):
        """
        修改一个命令的名称。
        name:           新的命令名称
        old_name:       旧的命令名称
        """
        if old_name == name:
            return  # 如果名称没有变化，则不做任何操作

        if old_name not in self.command_list:
                    raise CommandNotFoundError(f"命令 {old_name} 未找到")
        
        if name in self.command_list:
            raise DuplicateCommandNamesError(f"命令 {name} 已被注册")

        # 获取旧命令的元数据
        function, event, commandspace = self.command_list[old_name]
        # 添加新命令
        self.command_list[name] = (function, event, commandspace)  
        if commandspace in self.command_space_list:
            space_commands = self.command_space_list[commandspace]
            space_commands[space_commands.index(old_name)] = name
        # 删除旧命令
        del self.command_list[old_name]

    def migration_command(self, name, commandspace):
        """
        迁移一个命令到新的命令空间。
        name:           命令名称
        commandspace:   新的命令空间名称
        """
        if name not in self.command_list:
            raise CommandNotFoundError(f"命令 {name} 未找到")

        if commandspace not in self.command_space_list:
            raise CommandSpaceNotFoundError(f"命令空间 {commandspace} 未找到")

        # 获取旧命令的元数据
        function, event, old_commandspace = self.command_list[name]

        # 从旧命令空间中移除该命令
        if old_commandspace in self.command_space_list:
            if name in self.command_space_list[old_commandspace]:
                self.command_space_list[old_commandspace].remove(name)

        # 添加到新命令空间
        self.command_space_list[commandspace].append(name)
        # 更新命令的元数据
        self.command_list[name] = (function, event, commandspace)

    def get_command_space_list(self):
        """
        获取所有命令空间的列表。
        """
        return list(self.command_space_list.keys())

    def get_command_list(self):
        """
        获取所有命令的列表。
        """
        return list(self.command_list.keys())

    def get_command_space(self, name):
        """
        根据名称获取命令空间。
        """
        return self.command_space_list.get(name)

    def get_command(self, name):
        """
        根据名称获取命令。
        """
        return self.command_list.get(name)

    def delete_command(self, name):
        """
        删除一个命令。
        """
        if name not in self.command_list:
            raise CommandNotFoundError(f"命令 {name} 未找到")
        _, _, commandspace = self.command_list.pop(name)
        if commandspace in self.command_space_list:
            self.command_space_list[commandspace].remove(name)

    def delete_command_space(self, name):
        """
        删除一个命令空间。
        """
        if name not in self.command_space_list:
            raise CommandSpaceNotFoundError(f"命令空间 {name} 未找到")
        command_names = self.command_space_list.pop(name)
        for command_name in command_names:
            self.command_list.pop(command_name, None)