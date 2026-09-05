"""版本号解析与比较（仅使用 Python 标准库）。

为什么单独成模块
----------------
最初的版本比较写在 `core/remote.py` 里，但 CLI 自身的更新检查
（`core/update.py`）也需要比较版本号。为了避免把"插件仓库"模块的
语义借给"CLI 升级"用，这里把它提升为公共能力，两处共同复用。

支持的版本号格式
----------------
PEP 440 的常用子集，足以覆盖插件生态：

    1.2.3          正式版
    1.2            短写法（等价于 1.2.0）
    1.2.3-rc1      预发布（rc / a / b / alpha / beta / pre / dev）
    2.0.0b1        预发布的紧凑写法

规则：先按数字段逐个比较（短写法补 0 对齐），数字段完全相同时
**预发布版本低于正式版**，预发布内部按 dev/a < b < rc 排序。
"""
from __future__ import annotations

import re
from typing import Optional

# 预发布标记：匹配 a / b / rc / alpha / beta / pre / dev 及其后的序号
_PRE_RELEASE = re.compile(
    r"(?:^|[-_.])(a|b|c|rc|alpha|beta|pre|preview|dev)[-_.]?(\d*)",
    re.IGNORECASE,
)
# 别名归一化：不同生态对同一阶段的叫法
_PRE_ALIASES = {
    "alpha": "a",
    "beta": "b",
    "c": "rc",
    "pre": "rc",
    "preview": "rc",
    "dev": "a",
}
# 预发布阶段权重，越大越接近正式版
_PRE_WEIGHT = {"a": 0, "b": 1, "rc": 2}


def version_key(version: Optional[str]) -> tuple:
    """
    把版本号转换成可比较的元组。

    :return: ((数字段...), 是否正式版, 预发布阶段权重, 预发布序号)
             正式版第二项为 1，预发布为 0 —— 保证 rc < 正式版
    """
    raw = str(version or "").strip()
    digits = re.findall(r"\d+", raw)
    release = tuple(int(part) for part in digits) or (0,)
    match = _PRE_RELEASE.search(raw.lower())
    if match:
        label = _PRE_ALIASES.get(match.group(1), match.group(1))
        number = int(match.group(2) or 0)
        return (release, 0, _PRE_WEIGHT.get(label, 0), number)
    return (release, 1, 0, 0)


def compare_versions(current: str, target: str) -> int:
    """
    比较两个版本号大小。

    :return: 负数表示 current 较旧，0 表示相等，正数表示 current 较新
    """
    left, right = version_key(current), version_key(target)
    # 数字段补 0 对齐，保证 1.2 与 1.2.0 判为相等
    length = max(len(left[0]), len(right[0]))
    left = (left[0] + (0,) * (length - len(left[0])),) + left[1:]
    right = (right[0] + (0,) * (length - len(right[0])),) + right[1:]
    return (left > right) - (left < right)


def is_newer(candidate: str, current: str) -> bool:
    """判断 candidate 是否严格新于 current（相等不算新）。"""
    return compare_versions(candidate, current) > 0
