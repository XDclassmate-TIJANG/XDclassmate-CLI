"""XD-CLI 微内核：装配与驱动整个 CLI。

微内核只做四件事，其余能力全部通过"内置命令 + 插件"扩展：

    1. **装配**：配置 -> 日志 -> 国际化 -> 命令注册表 -> 插件管理器；
    2. **启动**：装载内置命令、加载插件、按序广播 init_cli / plugin_init；
    3. **分发**：把用户输入交给命令注册表执行，统一处理异常与退出码；
    4. **交互**：按配置的启动模式进入 REPL 或直接输出帮助。

启动阶段（boot）顺序固定：
    load_builtins -> load_plugins -> emit(init_cli) -> emit(plugin_init)

为什么强调顺序：内置命令先于插件注册，插件才能在其入口里调用 help；
插件扫描在 plugin_init 之前完成，插件才有机会订阅事件。

退出码约定：
    0 成功 / 1 框架异常（命令未找到、参数错误等）/ 2 命令执行异常
"""
from __future__ import annotations

from typing import Optional

from .args import parse_arguments
from .builtins import register_system_commands
from .command import CommandRegistry, registry as default_registry
from .config import (
    CONFIG_KEY_LANGUAGE,
    CONFIG_KEY_STARTUP_MODE,
    STARTUP_MODE_HELP,
    STARTUP_MODE_REPL,
    STARTUP_MODES,
    ConfigManager,
)
from .event_bus import bus
from .exceptions import XDclassmateCLIException
from .i18n import I18n, detect_language, set_global_i18n
from .logger import get_logger, setup_logging
from .plugins import get_plugins

LOGGER = get_logger("kernel")

# REPL 提示符
PROMPT = "xd> "
# REPL 退出命令
EXIT_COMMANDS = ("exit", "quit")


class Kernel:
    """微内核：持有各子系统并定义启动与分发流程。"""

    def __init__(
            self,
            config_path: Optional[str] = None,
            registry: Optional[CommandRegistry] = None,
            language: Optional[str] = None,
            log_level: Optional[str] = None
            ):
        """
        :param config_path: 配置文件路径，缺省自动查找
        :param registry:    命令注册表，缺省使用全局默认实例
        :param language:    指定语言，缺省按优先级探测
        :param log_level:   日志级别，缺省取自配置
        """
        LOGGER.debug("正在装配微内核")
        self.config = ConfigManager(config_path)
        setup_logging(level=log_level)
        resolved = language or detect_language(
            self.config.load_config(CONFIG_KEY_LANGUAGE, default="")
        )
        self.i18n = I18n(resolved)
        set_global_i18n(self.i18n)
        self.bus = bus
        self.registry = registry or default_registry
        self.plugins = None
        LOGGER.info("微内核装配完成（语言=%s）", self.i18n.language)

    # ------------------------------------------------------------------
    # 启动
    # ------------------------------------------------------------------
    def boot(self) -> "Kernel":
        """按固定顺序完成启动：内置命令 -> 插件 -> 事件广播。"""
        LOGGER.debug("阶段 1/3：装载内置命令")
        register_system_commands(
            self.registry, plugins_provider=lambda: self.plugins
        )
        LOGGER.debug("阶段 2/3：加载插件")
        self.plugins = get_plugins()
        LOGGER.debug("阶段 3/3：广播启动事件")
        self.bus.emit("init_cli")
        self.bus.emit("plugin_init")
        count = len(self.plugins.get_plugin_list()) if self.plugins else 0
        LOGGER.info("CLI 启动完成，已加载 %s 个插件", count)
        return self

    @property
    def startup_mode(self) -> str:
        """按配置返回终端启动模式，取值非法时回退 repl 并告警。"""
        mode = self.config.load_config(
            CONFIG_KEY_STARTUP_MODE, default=STARTUP_MODE_REPL
        )
        mode = str(mode).strip().lower()
        if mode not in STARTUP_MODES:
            LOGGER.warning(
                "配置的启动模式 %r 非法（可选 %s），回退为 %s",
                mode, "/".join(STARTUP_MODES), STARTUP_MODE_REPL
            )
            return STARTUP_MODE_REPL
        return mode

    # ------------------------------------------------------------------
    # 分发
    # ------------------------------------------------------------------
    def run_once(self, tokens: list[str]) -> int:
        """
        执行单条命令。

        :return: 0 成功；1 框架异常；2 命令执行异常
        """
        try:
            self.registry.execute(tokens)
        except XDclassmateCLIException as error:
            self._print_error(error)
            return 1
        except Exception as error:  # noqa: BLE001 —— 未知异常兜底
            self._print_error(error)
            return 2
        return 0

    def _print_error(self, error: Exception) -> None:
        """按当前语言输出错误信息（框架异常走翻译，其余原样输出）。"""
        prefix = self.i18n.t("cli.error.prefix")
        if isinstance(error, XDclassmateCLIException):
            text = error.translated(self.i18n.t)
            LOGGER.debug("命令被拒绝: %s", error)
        else:
            text = str(error)
            LOGGER.exception("命令执行出现未预期异常: %s", error)
        print(f"{prefix}: {text}", file=__import__("sys").stderr)

    def print_help(self, theme: Optional[str] = None) -> int:
        """输出命令帮助（终端启动模式为 help 时使用）。"""
        tokens = ["help"]
        if theme:
            tokens += ["--theme", theme]
        return self.run_once(tokens)

    def repl(self) -> int:
        """交互式命令循环。"""
        count = len(self.plugins.get_plugin_list()) if self.plugins else 0
        print(f"{self.i18n.t('cli.title')}  "
              f"{self.i18n.t('cli.startup.repl_hint', plugins=count)}")
        while True:
            try:
                line = input(PROMPT).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                LOGGER.info("REPL 收到中断信号，正常退出")
                return 0
            if not line:
                continue
            tokens = line.split()
            if tokens[0] in EXIT_COMMANDS:
                LOGGER.debug("REPL 收到退出命令")
                return 0
            self.run_once(tokens)

    def dispatch(self, arguments) -> int:
        """
        根据解析结果选择执行方式。

        :param arguments: core.args.parse_arguments 的返回值
        """
        if arguments.command:
            return self.run_once(arguments.command)
        if self.startup_mode == STARTUP_MODE_HELP:
            LOGGER.debug("启动模式为 help，输出帮助后退出")
            return self.print_help()
        return self.repl()


def create_kernel(
        log_level: Optional[str] = None,
        language: Optional[str] = None
        ) -> Kernel:
    """
    创建并启动内核（命令行入口的推荐用法）。

    :param log_level: 日志级别（来自 --log-level）
    :param language: 语言代码（来自 --lang）
    """
    setup_logging()
    arguments = parse_arguments()
    kernel = Kernel(
        language=language or arguments.language,
        log_level=log_level or arguments.log_level
    )
    return kernel.boot()
