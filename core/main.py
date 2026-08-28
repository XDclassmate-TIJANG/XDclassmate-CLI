"""XDclassmate-CLI 主入口。

初始化流程（顺序固定，勿调整）：
    1. 导入 core.command / core.plugins —— 构造 registry 与 pl 单例，
       插件加载器在此阶段扫描插件目录并校验清单与内容 hash；
    2. 触发 init_cli 事件 —— 系统命令已就绪；
    3. 触发 plugin_init 事件 —— 各插件入口函数执行（一次性），
       插件通常在此时向 registry 注册自己的命令；
    4. 根据命令行参数决定进入单命令模式或交互式 REPL。
"""
from .args import parse_arguments, run_once
from .command import registry
from .event_bus import bus
from .exceptions import XDclassmateCLIException
from .plugins import pl

# REPL 提示符（XDclassmate-CLI 缩写）
PROMPT = "xd> "
# REPL 中用于退出的命令（不经过 registry 分发，由 REPL 直接处理）
EXIT_COMMANDS = ("exit", "quit")


def init_cli():
    """初始化命令注册表与插件，并广播 CLI 启动事件。"""
    bus.emit("init_cli")      # 系统命令在此后可用
    bus.emit("plugin_init")   # 插件入口在此事件中执行（once=True，仅一次）


def repl() -> int:
    """
    交互式模式：循环读取用户输入并分发给命令注册表。

    :return: 进程退出码（0 正常退出）
    """
    print(f"XDclassmate-CLI  已加载 {len(pl.get_plugin_list())} 个插件，输入 help 查看命令，exit 退出")
    while True:
        try:
            line = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            # Ctrl+Z/Ctrl+C 等价于 exit，换行后正常退出
            print()
            return 0

        # 空行直接跳过，不报错
        if not line:
            continue

        # 切分为 token（与 argparse 单命令模式保持一致的语义）
        tokens = line.split()

        # 退出命令由 REPL 直接处理
        if tokens[0] in EXIT_COMMANDS:
            return 0

        try:
            registry.execute(tokens)
        except XDclassmateCLIException as error:
            # 框架内异常（命令未找到等）：提示后继续，不退出 REPL
            print(f"错误: {error}")
        except Exception as error:  # noqa: BLE001 —— 命令函数异常兜底，保证 REPL 不中断
            print(f"命令执行失败: {error}")


def main() -> int:
    """CLI 总入口：返回进程退出码。"""
    init_cli()
    arguments = parse_arguments()
    if arguments.command:
        # 单命令模式：执行完一条命令后退出
        return run_once(arguments.command)
    # 无命令参数：进入交互式 REPL
    return repl()


if __name__ == "__main__":
    raise SystemExit(main())
