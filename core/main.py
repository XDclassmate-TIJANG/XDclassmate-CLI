"""XDclassmate-CLI 主入口。

初始化流程（顺序固定，勿调整）：
    1. 初始化日志系统（按配置或 --log-level 参数）；
    2. 导入 core.command / core.plugins —— 构造 registry 与 pl 单例，
       插件加载器在此阶段扫描插件目录并校验清单与内容 hash；
    3. 触发 init_cli 事件 —— 系统命令已就绪；
    4. 触发 plugin_init 事件 —— 各插件入口函数执行（一次性），
       插件通常在此时向 registry 注册命令与选项；
    5. 根据命令行参数决定进入单命令模式或交互式 REPL。
"""
from __future__ import annotations

from .args import parse_arguments, run_once
from .command import registry
from .event_bus import bus
from .exceptions import XDclassmateCLIException
from .logger import get_logger, setup_logging
from .plugins import pl

# 模块日志记录器
LOGGER = get_logger("main")

# REPL 提示符（XDclassmate-CLI 缩写）
PROMPT = "xd> "
# REPL 中用于退出的命令（不经过 registry 分发，由 REPL 直接处理）
EXIT_COMMANDS = ("exit", "quit")


def init_cli() -> None:
    """初始化命令注册表与插件，并广播 CLI 启动事件。"""
    # 系统命令在此后可用
    bus.emit("init_cli")
    # 插件入口在此事件中执行（once=True，仅一次）
    bus.emit("plugin_init")
    LOGGER.debug("CLI 初始化完成，已加载 %s 个插件", len(pl.get_plugin_list()))


def repl() -> int:
    """
    交互式模式：循环读取用户输入并分发给命令注册表。

    :return: 进程退出码（0 表示正常退出）
    """
    print(
        f"XDclassmate-CLI  已加载 {len(pl.get_plugin_list())} 个插件，"
        "输入 help 查看命令，exit 退出"
    )
    while True:
        try:
            line = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            # Ctrl+Z / Ctrl+C 等价于 exit，换行后正常退出
            print()
            LOGGER.debug("REPL 收到中断信号，正常退出")
            return 0

        # 空行直接跳过，不报错
        if not line:
            continue

        # 切分为 token（与单命令模式保持一致的语义）
        tokens = line.split()

        # 退出命令由 REPL 直接处理
        if tokens[0] in EXIT_COMMANDS:
            return 0

        try:
            registry.execute(tokens)
        except XDclassmateCLIException as error:
            # 框架内异常：提示后继续，不退出 REPL
            print(f"错误: {error}")
            LOGGER.debug("命令执行被拒绝: %s", error)
        except Exception as error:  # noqa: BLE001 —— 兜底，保证 REPL 不中断
            print(f"命令执行失败: {error}")
            LOGGER.exception("命令执行出现未预期异常: %s", error)


def main() -> int:
    """CLI 总入口：返回进程退出码。"""
    # 先按配置初始化日志，保证插件加载阶段的日志也能输出
    setup_logging()
    arguments = parse_arguments()
    if arguments.log_level:
        # 命令行级别优先，重新配置日志
        setup_logging(level=arguments.log_level)
        LOGGER.debug("日志级别已设置为 %s", arguments.log_level)

    init_cli()
    if arguments.command:
        # 单命令模式：执行完一条命令后退出
        return run_once(arguments.command)
    # 无命令参数：进入交互式 REPL
    return repl()


if __name__ == "__main__":
    raise SystemExit(main())
