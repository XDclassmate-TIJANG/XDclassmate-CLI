"""命令行参数解析模块（仅使用 Python 标准库）。

解析策略
--------
CLI 层只识别出现在**最前面**的全局开关（-h/--help、-V/--version）。
从第一个非开关 token 开始，其余内容（含 `-v`、`--opt` 之类的命令选项）
**原样透传**给命令处理函数，保持参数顺序，例如：

    py -3 -m core.main space1 space2 command1 --opt 1 -v

用法：
    py -3 -m core.main                          # 进入交互式 REPL（提示符 xd>）
    py -3 -m core.main <命令> [参数...]          # 单次执行一条命令后退出
    py -3 -m core.main [<空间>...] <命令> [选项]  # 带命令空间的调用
    py -3 -m core.main --version                # 显示 CLI 版本后退出
"""
from __future__ import annotations

import argparse
import sys
from types import SimpleNamespace

from .config import CLI_VERSION
from .exceptions import XDclassmateCLIException

# 全局开关：只在命令行最前面出现时生效
HELP_FLAGS = ("-h", "--help")
VERSION_FLAGS = ("-V", "--version")


def build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器（仅用于生成帮助文本与版本信息）。"""
    parser = argparse.ArgumentParser(
        prog="xdclassmate-cli",
        description="XDclassmate-CLI —— 由事件总线和插件组成的 Python CLI 原型",
    )
    # -V / --version：打印 CLI 版本
    parser.add_argument("-V", "--version", action="version", version=f"XDclassmate-CLI {CLI_VERSION}")
    parser.add_argument("command", nargs="*", help="要执行的命令及其参数，留空则进入交互模式")
    return parser


def parse_arguments(argv: list[str] | None = None):
    """
    解析命令行参数。

    :return: SimpleNamespace(command=剩余 token 列表)
             command 为空列表表示进入交互式 REPL
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # 只处理位于最前面的全局开关；遇到第一个非开关 token 即停止
    for token in argv:
        if token in HELP_FLAGS:
            parser.print_help()
            raise SystemExit(0)
        if token in VERSION_FLAGS:
            parser.parse_args([token])  # 由 argparse 打印版本并退出
        break  # 第一个 token 不是全局开关 → 整体视为命令调用

    # 其余 token 原样传递，保留命令选项的顺序
    return SimpleNamespace(command=argv)


def run_once(tokens: list[str]) -> int:
    """
    单命令模式：执行一条命令并返回进程退出码。

    :param tokens: 已切分的命令 token 列表，如 ["space1", "space2", "command1", "--opt", "1"]
    :return: 0 成功；1 命令未找到；2 命令执行异常
    """
    # 延迟导入：确保 main.py 先完成插件与系统命令初始化再分发
    from .command import registry

    try:
        registry.execute(tokens)
    except XDclassmateCLIException as error:
        # 框架内异常（命令未找到等）：属于用户可见的正常分支
        print(f"错误: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 —— 命令函数的未知异常统一兜底
        print(f"命令执行失败: {error}", file=sys.stderr)
        return 2
    return 0
