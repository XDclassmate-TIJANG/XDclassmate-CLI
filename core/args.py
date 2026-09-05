"""命令行参数解析模块（仅使用 Python 标准库）。

解析策略
--------
CLI 层只识别出现在**最前面**的全局开关：

    -h / --help          打印帮助后退出
    -V / --version       打印 CLI 版本后退出
    --log-level <级别>    设置日志级别（DEBUG/INFO/WARNING/ERROR）
    --lang <语言代码>     设置界面语言（如 zh_CN / en_US）

从第一个非开关 token 开始，其余内容（含 `-v`、`--opt` 之类的命令选项）
**原样透传**给命令处理函数，保持参数顺序，例如：

    py -3 -m core.main space1 space2 command1 --opt 1 -v

用法：
    py -3 -m core.main                          # 按配置启动（repl 或 help）
    py -3 -m core.main <命令> [参数...]          # 单次执行一条命令后退出
    py -3 -m core.main [<空间>...] <命令> [选项]  # 带命令空间的调用
    py -3 -m core.main --lang en_US help        # 以英文界面输出帮助

注意：-h/--help 在内核装配前就会输出帮助，此时只能按
「环境变量 XDCLI_LANG > 系统区域」探测语言（配置文件尚未加载）。
"""
from __future__ import annotations

import argparse
import sys
from types import SimpleNamespace
from typing import Optional

from .command import EXIT_COMMAND_ERROR, coerce_exit_code
from .config import CLI_VERSION
from .exceptions import CommandExecutionError, XDclassmateCLIException
from .i18n import detect_language, get_i18n, t
from .logger import get_logger

LOGGER = get_logger("args")

# 全局开关：只在命令行最前面出现时生效
HELP_FLAGS = ("-h", "--help")
VERSION_FLAGS = ("-V", "--version")
LOG_LEVEL_FLAGS = ("--log-level", "--loglevel")
LANGUAGE_FLAGS = ("--lang", "--language")


def _prepare_early_language() -> None:
    """
    在内核装配前把全局 i18n 切到探测语言。

    此时尚未读取配置文件，探测顺序为「环境变量 XDCLI_LANG > 系统区域」；
    内核启动后会用自己的 I18n 实例重新覆盖全局实例。
    """
    get_i18n().set_language(detect_language())


def build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器（帮助文本按当前语言翻译）。"""
    parser = argparse.ArgumentParser(
        prog="xdclassmate-cli",
        description=t("cli.args.prog_desc"),
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"XDclassmate-CLI {CLI_VERSION}",
        help=t("cli.args.version_help"),
    )
    parser.add_argument(
        "--log-level",
        metavar="LEVEL",
        help=t("cli.args.loglevel_help"),
    )
    parser.add_argument(
        "--lang",
        metavar="CODE",
        help=t("cli.args.lang_help"),
    )
    parser.add_argument(
        "command",
        nargs="*",
        help=t("cli.args.command_help"),
    )
    return parser


def parse_arguments(argv: Optional[list[str]] = None) -> SimpleNamespace:
    """
    解析命令行参数。

    :param argv: 参数列表，缺省使用 sys.argv[1:]
    :return:     SimpleNamespace(command=, log_level=, language=)
                 command 为空列表表示按配置的启动模式处理
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    _prepare_early_language()
    parser = build_parser()
    LOGGER.debug("解析命令行参数: %s", argv)

    log_level = None
    language = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in HELP_FLAGS:
            parser.print_help()
            raise SystemExit(0)
        if token in VERSION_FLAGS:
            # 由 argparse 打印版本并退出
            parser.parse_args([token])
        if token in LOG_LEVEL_FLAGS:
            index += 1
            if index >= len(argv):
                _print_error(t("error.flag_missing_value", token=token))
                raise SystemExit(2)
            log_level = argv[index]
            index += 1
            continue
        if token in LANGUAGE_FLAGS:
            index += 1
            if index >= len(argv):
                _print_error(t("error.flag_missing_value", token=token))
                raise SystemExit(2)
            language = argv[index]
            # 立即应用 --lang，后续提前退出的报错也能用所选语言
            get_i18n().set_language(language)
            index += 1
            continue
        # 第一个非全局开关 token → 其后整体交给命令分发
        break

    result = SimpleNamespace(
        command=argv[index:],
        log_level=log_level,
        language=language,
    )
    LOGGER.debug(
        "解析结果 command=%s log_level=%s language=%s",
        result.command, result.log_level, result.language
    )
    return result


def _print_error(text: str) -> None:
    """带「错误」前缀输出用户可见的报错文本。"""
    print(f"{t('cli.error.prefix')}: {text}", file=sys.stderr)


def run_once(tokens: list[str]) -> int:
        """
        单命令模式：执行一条命令并返回进程退出码。

        与 ``kernel.run_once`` 保持一致的退出码约定：
            0 成功；1 框架异常（命令未找到、参数错误等）；2 命令执行异常。
        命令函数可返回整数 / False / True 表达自身状态。

        :param tokens: 已切分的命令 token 列表
        """
        # 延迟导入：确保 main.py 完成插件与系统命令初始化后再分发
        from .command import registry

        try:
            result = registry.execute(tokens)
        except CommandExecutionError as error:
            _print_error(error.translated(t))
            return EXIT_COMMAND_ERROR
        except XDclassmateCLIException as error:
            # 框架内异常（命令未找到、参数错误等）：用户可见的正常分支
            _print_error(error.translated(t))
            return 1
        except Exception as error:  # noqa: BLE001 —— 未知异常统一兜底
            LOGGER.exception("命令执行出现未预期异常: %s", error)
            _print_error(str(error))
            return EXIT_COMMAND_ERROR
        return coerce_exit_code(result)
