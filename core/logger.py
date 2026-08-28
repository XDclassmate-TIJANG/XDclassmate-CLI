"""日志模块：统一配置 XD-CLI 的日志输出。

设计要点：
    * 所有模块通过 `get_logger(name)` 获取 `xdclassmate.*` 命名空间的
      日志记录器，便于整体控制级别与输出目标；
    * 日志级别默认取自 core/configs/config.json 的 `log_level`，
      可由命令行 `--log-level DEBUG` 覆盖；
    * 配置 `log_file` 后可同时写入日志文件（UTF-8 编码）；
    * 日志统一输出到 stderr，不干扰命令本身打印到 stdout 的用户内容。

用法：
    from .logger import get_logger

    LOGGER = get_logger("command")
    LOGGER.debug("执行命令 %s", path)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

# 说明：logger 不在模块顶层导入 config，避免循环依赖
# （config 需要 logger 记录配置读取过程，logger 需要 config 读取级别）

# 日志器命名空间前缀
LOG_PREFIX = "xdclassmate"
# 输出格式：时间 级别 模块: 消息
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"
# 默认日志级别
DEFAULT_LEVEL = "INFO"
# 配置文件中日志级别与日志文件的键名
CONFIG_KEY_LEVEL = "log_level"
CONFIG_KEY_FILE = "log_file"


def get_logger(name: str) -> logging.Logger:
    """
    获取项目统一命名空间下的日志记录器。

    :param name: 模块短名，如 "command"、"plugins"
    :return:     形如 xdclassmate.command 的 Logger 对象
    """
    return logging.getLogger(f"{LOG_PREFIX}.{name}")


def resolve_level(level: Optional[str]) -> int:
    """
    把级别名称转换为 logging 的数值级别。

    :param level: 级别名称（DEBUG/INFO/WARNING/ERROR/CRITICAL，忽略大小写）
    :return:      数值级别；无法识别时回退到 INFO
    """
    if not level:
        return logging.INFO
    numeric = logging.getLevelName(str(level).strip().upper())
    if isinstance(numeric, int):
        return numeric
    return logging.INFO


def setup_logging(
        level: Optional[str] = None,
        log_file: Optional[str] = None,
        path: Optional[str] = None
        ) -> logging.Logger:
    """
    初始化日志系统（可重复调用，以最后一次配置为准）。

    :param level:    命令行传入的级别，优先级高于配置文件
    :param log_file: 命令行传入的日志文件路径，优先级高于配置文件
    :param path:     配置文件路径，缺省使用项目内置配置
    :return:         项目根日志器（xdclassmate）
    """
    # 延迟导入 config，规避 config <-> logger 的循环依赖
    from .config import ConfigManager

    config = ConfigManager(path)
    level_name = level or config.load_config(
        CONFIG_KEY_LEVEL, default=DEFAULT_LEVEL
    )
    file_path = log_file or config.load_config(CONFIG_KEY_FILE, default="")

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    root = logging.getLogger(LOG_PREFIX)
    root.setLevel(resolve_level(level_name))
    # 清除旧处理器，保证重复调用不会重复输出
    root.handlers.clear()
    # 不向 root 传播，避免与第三方库的日志配置互相干扰
    root.propagate = False

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if file_path:
        target = Path(file_path).expanduser()
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(target, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return root
