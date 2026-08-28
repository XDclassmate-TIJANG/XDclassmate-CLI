"""XDclassmate-CLI —— 微内核 + 插件的命令行框架（仅依赖标准库）。

对外入口：
    python -m core.main           按配置的启动模式运行
    python -m core.main <命令>     单次执行一条命令
"""
from .command import CommandRegistry, registry
from .config import CLI_VERSION
from .kernel import Kernel

__version__ = CLI_VERSION

__all__ = ["CommandRegistry", "Kernel", "registry", "__version__"]
