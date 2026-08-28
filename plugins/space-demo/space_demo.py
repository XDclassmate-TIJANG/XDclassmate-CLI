"""示例插件：space-demo。

演示内容：
    1. 多层命令空间的注册与调用
           space1 command1 [参数...]
           space1 space2 space3 command1 --opt 1
    2. 命令选项（Option）的声明与解析
           greet --name XD --loud
           spaces --theme tree
"""
from core.command import registry
from core.exceptions import CommandArgumentException
from core.views import THEME_LIST, THEME_TREE

# spaces 命令支持的视图主题
SPACES_THEMES = (THEME_LIST, THEME_TREE)


def main():
    """插件入口：plugin_init 事件触发时执行。"""
    # 一次性创建 space1/space2/space3 三层嵌套空间
    registry.register_command_space("space1/space2/space3")

    # 各层空间注册同名命令 command1，互不冲突
    for path in ("space1", "space1/space2", "space1/space2/space3"):
        registry.register("command1", make_command1(path), commandspace=path)

    # 根空间（default）也注册一个 command1，可裸调用
    registry.register("command1", make_command1("default"))

    # 注册演示选项的命令
    entry = registry.register("greet", cmd_greet)
    registry.register_option(
        entry, "-n", "--name",
        takes_value=True, default="world", help="要问候的对象"
    )
    registry.register_option(
        entry, "-l", "--loud", help="是否以大写形式输出"
    )

    # 注册带视图选项的命令
    registry.register("spaces", cmd_spaces)
    registry.register_option(
        "spaces", "-t", "--theme",
        takes_value=True, default=THEME_LIST,
        help=f"视图主题：{'/'.join(SPACES_THEMES)}"
    )


def make_command1(path: str):
    """生成打印自身所在路径的 command1 处理函数。"""

    def command1(*options):
        print(f"{path}/command1 被调用，参数: {list(options)}")

    # 动态设置文档字符串，便于在 help 中区分同名命令
    command1.__doc__ = (
        f"command1 [参数...]：位于 {path} 空间的演示命令，"
        "打印自身路径与参数"
    )
    return command1


def cmd_greet(name: str = "world", loud: bool = False):
    """greet [-n|--name 名字] [-l|--loud]：演示命令选项的用法。"""
    text = f"Hello, {name}!"
    print(text.upper() if loud else text)


def cmd_spaces(theme: str = THEME_LIST):
    """spaces [-t|--theme list|tree]：列出全部命令空间路径。"""
    if theme not in SPACES_THEMES:
        raise CommandArgumentException(
            f"未知视图 {theme}，可选主题：{', '.join(SPACES_THEMES)}",
            details={"theme": theme}
        )
    for space in registry.root.iter_spaces():
        path = space.full_path()
        # tree 主题按嵌套层级缩进，list 主题直接输出完整路径
        print("  " * space.depth() + path if theme == THEME_TREE else path)
