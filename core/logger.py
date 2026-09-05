"""日志模块：统一配置 XD-CLI 的日志输出。

设计要点：
    * 所有模块通过 `get_logger(name)` 获取 `xdclassmate.cli.*` 命名空间的
      日志记录器，便于整体控制级别与输出目标；
    * 日志级别默认取自 configs/config.json 的 `log_level`，
      可由命令行 `--log-level DEBUG` 覆盖；
    * 配置 `log_file` 后可写入日志文件（UTF-8 编码）；
    * 配置 `log_console_output` 控制是否把日志输出到控制台（stderr），
      默认关闭，避免干扰命令本身打印到 stdout 的用户内容。

用法：
    from .logger import get_logger

    LOGGER = get_logger("command")
    LOGGER.debug("执行命令 %s", path)
"""
from __future__ import annotations

import sys
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

# 说明：logger 不在模块顶层导入 config，避免循环依赖
# （config 需要 logger 记录配置读取过程，logger 需要 config 读取级别）

# 日志器命名空间前缀
LOG_PREFIX = "xdclassmate.cli"
# 输出格式：时间 级别 模块: 消息
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"
# 默认日志级别
DEFAULT_LEVEL = "INFO"
# 配置文件中日志级别与日志文件的键名
CONFIG_KEY_LEVEL = "log_level"
CONFIG_KEY_FILE = "log_file"
# datetime格式：YYMMDD-HHMMSS
DEFAULT_LOG_FILE = "./logs/%(datetime)s.log"
DATETIME_PATTERN = "%(datetime)s"
DATETIME_FORMAT = "%y%m%d-%H%M%S"
# 按时间戳切分时保留的日志数量，超出后删除最旧的；0 表示不清理
MAX_LOG_FILES = 20


def get_logger(name: str) -> logging.Logger:
    """
    获取项目统一命名空间下的日志记录器。

    :param name: 模块短名，如 "command"、"plugins"
    :return:     形如 xdclassmate.cli.command 的 Logger 对象
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


def render_path(template: str, when: Optional[datetime] = None) -> str:
    """
    把路径模板中的 %(datetime)s 替换为启动时刻字符串。

    :param template: 路径模板，如 "./logs/%(datetime)s.log"
    :param when:     替换所用时刻，缺省取当前时间
    :return:         真实路径；模板无占位符时原样返回
    """
    if DATETIME_PATTERN not in template:
        return template
    stamp = (when or datetime.now()).strftime(DATETIME_FORMAT)
    return template.replace(DATETIME_PATTERN, stamp)


def prune_log_files(template: str, keep: int) -> None:
    """
    清理按时间戳切分产生的历史日志，只删除符合该模板的文件，最旧的优先。

    :param template: 日志路径模板；不含 %(datetime)s 时不做任何清理
    :param keep:     保留数量，小于等于 0 表示不清理
    """
    if keep <= 0 or DATETIME_PATTERN not in template:
        return
    pattern = Path(template.replace(DATETIME_PATTERN, "*"))
    files = sorted(
        pattern.parent.glob(pattern.name),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def setup_logging(
        level: Optional[str] = None,
        log_file: Optional[str] = None,
        path: Optional[str] = None,
        console_output: Optional[bool] = None
        ) -> logging.Logger:
    """
    初始化日志系统（可重复调用，以最后一次配置为准）。

    :param level:          命令行传入的级别，优先级高于配置文件
    :param log_file:       命令行传入的日志文件路径，优先级高于配置文件
    :param path:           配置文件路径，缺省使用项目内置配置
    :param console_output: 是否把日志输出到控制台（stderr）；
                           为 None 时读取配置 log_console_output（默认 False）
    :return:               项目根日志器（xdclassmate.cli）
    """
    # 延迟导入 config，规避 config <-> logger 的循环依赖
    from .config import CONFIG_KEY_CONSOLE_OUTPUT, ConfigManager

    config = ConfigManager(path)
    level_name = level or config.load_config(
        CONFIG_KEY_LEVEL, default=DEFAULT_LEVEL
    )
    template = log_file or config.load_config(
        CONFIG_KEY_FILE, default=DEFAULT_LOG_FILE
    )
    file_path = render_path(template)
    if console_output is None:
        console_output = bool(
            config.load_config(CONFIG_KEY_CONSOLE_OUTPUT, default=False)
        )

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    root = logging.getLogger(LOG_PREFIX)
    root.setLevel(resolve_level(level_name))
    # 清除旧处理器，保证重复调用不会重复输出
    # 先关闭再移除，避免旧的 FileHandler 残留文件句柄
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    # 不向 root 传播，避免与第三方库的日志配置互相干扰
    root.propagate = False

    # 控制台输出默认关闭，保持用户可见输出（stdout）干净
    if console_output:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    if file_path:
        target = Path(file_path).expanduser()
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_handler = logging.FileHandler(target, encoding="utf-8")
        except OSError as exc:
            # 路径不可写时降级，不让日志问题拖垮命令本身
            root.addHandler(logging.NullHandler())
            root.warning("无法打开日志文件 %s：%s", target, exc)
            return root
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        # 仅当路径按时间戳切分时清理；固定路径是用户有意累积的日志，不动
        prune_log_files(template, MAX_LOG_FILES)

    # 当控制台与文件都未启用时，挂一个 NullHandler，避免 logging 的
    # lastResort 把日志兜底输出到 stderr，保证控制台真正干净
    if not root.handlers:
        root.addHandler(logging.NullHandler())

    return root
