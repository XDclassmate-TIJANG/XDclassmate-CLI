"""示例插件：space-demo。

演示多层命令空间（commandspace）的注册与调用：

    space1 command1 [参数...]
    space1 space2 command1 [参数...]
    space1 space2 space3 command1 [参数...]

四个空间（default / space1 / space1/space2 / space1/space2/space3）中
都有名为 command1 的命令，名称相同但互不影响——只有同一空间内重名才会报错。
"""
from core.command import registry


def main():
    """插件入口：plugin_init 事件触发时执行，注册三层嵌套命令空间与同名命令。"""
    # 一次性创建 space1/space2/space3 三层嵌套空间
    registry.register_command_space("space1/space2/space3")

    # 各层空间注册同名命令 command1，实现互不影响
    registry.register("command1", make_command1("space1"), commandspace="space1")
    registry.register("command1", make_command1("space1/space2"), commandspace="space1/space2")
    registry.register("command1", make_command1("space1/space2/space3"), commandspace="space1/space2/space3")
    # 根空间（default）也注册一个 command1，可裸调用
    registry.register("command1", make_command1("default"))

    # 辅助命令：查看当前全部命令空间与命令路径
    registry.register("spaces", cmd_spaces)


def make_command1(path: str):
    """工厂函数：生成一个打印自身路径的 command1 命令处理函数。"""

    def command1(*options):
        print(f"{path}/command1 被调用，参数: {list(options)}")

    # 动态设置文档字符串，使 help 中能区分不同空间的同名命令
    command1.__doc__ = f"command1 [参数...]：位于 {path} 空间的演示命令，打印自身路径与参数"
    return command1


def cmd_spaces():
    """spaces：列出当前全部命令空间路径与命令路径。"""
    print("命令空间:")
    for path in registry.get_command_space_list():
        print(f"  {path}")
    print("命令:")
    for path in registry.get_command_list():
        print(f"  {path}")
