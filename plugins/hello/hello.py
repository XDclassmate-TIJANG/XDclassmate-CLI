"""示例插件：hello。

在 plugin_init 事件触发时向命令注册表注册一条 hello 命令，
用于演示插件的标准结构与生命周期。
"""
from core.command import registry


def main():
    """插件入口函数：清单 entry 字段指向本函数，plugin_init 事件触发时执行。"""
    registry.register("hello", cmd_hello, commandspace="default")


def cmd_hello(name: str = "world"):
    """hello [名字]：向指定对象（默认 world）打印问候语。"""
    print(f"Hello, {name}! —— 来自 hello 插件")
