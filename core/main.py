"""XDclassmate-CLI 主入口（薄入口）。

真正的装配与调度逻辑在 core.kernel.Kernel 中，本模块只负责：

    1. 尽早初始化日志（保证插件加载阶段的日志也能输出）；
    2. 解析命令行全局开关（--log-level / --lang / -h / -V）；
    3. 创建并启动微内核，把剩余参数交给内核分发。

退出码：0 成功 / 1 框架异常 / 2 命令执行异常
"""
from __future__ import annotations

from .args import parse_arguments
from .kernel import Kernel
from .logger import get_logger, setup_logging

LOGGER = get_logger("main")


def main() -> int:
    """CLI 总入口：返回进程退出码。"""
    # 先按配置初始化日志，保证插件加载阶段的日志也能输出
    setup_logging()
    # 解析可能提前退出的全局开关（-h / -V）
    arguments = parse_arguments()

    kernel = Kernel(
        language=arguments.language,
        log_level=arguments.log_level
    )
    kernel.boot()
    LOGGER.debug("进入分发阶段，启动模式=%s", kernel.startup_mode)
    return kernel.dispatch(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
