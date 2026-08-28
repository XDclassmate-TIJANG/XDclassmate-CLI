"""轻量 PEP 8 风格检查器（仅使用 Python 标准库）。

用途：在没有安装 flake8 / autopep8 的环境下，快速自查项目源码的
常见 PEP 8 问题。

用法：
    py -3 tools/check_style.py               # 检查 core/、tools/、tests/、plugins/
    py -3 tools/check_style.py <文件或目录>   # 指定路径

检查项：
    E501  单行长度超过 79 个字符
    W191  使用制表符缩进
    W291  行尾存在多余空格
    W293  空白行中存在空格
    W391  文件未以单个换行符结尾
    E302  顶层函数/类定义前缺少两个空行
    E303  连续空行超过两个
    E231  逗号后缺少空格（简单检测）
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

MAX_LINE_LENGTH = 79
DEFAULT_TARGETS = ("core", "tools", "tests", "plugins")


def iter_python_files(targets: list[str]) -> Iterator[Path]:
    """遍历给定路径下的全部 .py 文件（跳过 __pycache__ 与隐藏目录）。"""
    for target in targets:
        path = Path(target)
        if path.is_file() and path.suffix == ".py":
            yield path
            continue
        if not path.is_dir():
            continue
        for item in sorted(path.rglob("*.py")):
            if "__pycache__" not in item.parts:
                yield item


def check_file(path: Path) -> list[str]:
    """检查单个文件，返回问题描述列表（形如 文件:行号: 代码 说明）。"""
    problems: list[str] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    if text and not text.endswith("\n"):
        problems.append(f"{path}:{len(lines)}: W391 文件末尾缺少换行符")
    if text.endswith("\n\n"):
        problems.append(f"{path}:{len(lines)}: W391 文件末尾存在多余空行")

    previous_blank = 0
    consecutive_blank = 0
    for number, line in enumerate(lines, start=1):
        if len(line) > MAX_LINE_LENGTH:
            problems.append(
                f"{path}:{number}: E501 行长度 {len(line)} 超过 {MAX_LINE_LENGTH}"
            )
        if "\t" in line:
            problems.append(f"{path}:{number}: W191 存在制表符缩进")
        if line != line.rstrip():
            problems.append(f"{path}:{number}: W291 行尾存在多余空格")
        if not line.strip() and line:
            problems.append(f"{path}:{number}: W293 空白行中存在空格")

        # 顶层定义前应有且仅有两个空行
        is_top_def = line.startswith(("def ", "class ", "@"))
        if is_top_def and previous_blank < 2 and number > 1:
            problems.append(f"{path}:{number}: E302 顶层定义前应有两个空行")

        # 注释行视为中性：不增加空行计数，也不打断计数，
        # 这样「注释横幅 + 类定义」的常见写法不会被误判为 E302
        stripped_line = line.strip()
        if not stripped_line:
            previous_blank += 1
            # E303 只统计真正连续的空行：注释等任何非空行都会打断
            consecutive_blank += 1
            if consecutive_blank > 2:
                problems.append(
                    f"{path}:{number}: E303 连续空行超过两个"
                )
        else:
            consecutive_blank = 0
            if not stripped_line.startswith("#"):
                previous_blank = 0

        # 逗号后缺少空格：如 foo(a,b)
        # 先扫描引号状态，跳过字符串字面量内部的逗号，
        # 避免正则表达式（如 r"{8,128}"）被误判为 E231
        stripped = line.strip()
        if stripped.startswith(("#", '"""', "'''")):
            continue
        in_string: str | None = None
        for index, char in enumerate(line):
            if in_string:
                if char == in_string:
                    in_string = None
                continue
            if char in ("'", '"'):
                in_string = char
                continue
            if char == "," and index + 1 < len(line):
                following = line[index + 1]
                if following not in (" ", ")", "]", "}", "\n", "#"):
                    problems.append(
                        f"{path}:{number}: E231 逗号后缺少空格"
                    )
                    break

    return problems


def main(argv: list[str]) -> int:
    targets = argv or list(DEFAULT_TARGETS)
    files = list(iter_python_files(targets))
    if not files:
        print(f"未找到 Python 文件: {', '.join(targets)}")
        return 2

    problems: list[str] = []
    for file in files:
        problems.extend(check_file(file))

    print(f"检查 {len(files)} 个文件，发现 {len(problems)} 处问题")
    for problem in problems:
        print(f"  {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
