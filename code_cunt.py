#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
代码统计工具 (Code Statistics Tool)
====================================
功能：
  1. 统计项目代码量（总行数 / 代码行 / 注释行 / 空行 / 文件数）
  2. 计算各编程语言的代码量与占比
  3. 获取项目及各语言的最后更新时间

用法：
  python code_stats.py                     # 统计当前目录
  python code_stats.py /path/to/project    # 统计指定目录
  python code_stats.py -j report.json      # 额外导出 JSON 报告
  python code_stats.py --top 10            # 只显示前 10 种语言
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# ==================== 配置区（可按需增删） ====================

# 文件扩展名 → 语言名称
LANGUAGE_MAP = {
    '.py': 'Python',        '.pyw': 'Python',
    '.js': 'JavaScript',    '.jsx': 'JavaScript',   '.mjs': 'JavaScript',
    '.ts': 'TypeScript',    '.tsx': 'TypeScript',
    '.java': 'Java',
    '.c': 'C',              '.h': 'C/C++ Header',
    '.cpp': 'C++',          '.cc': 'C++',           '.cxx': 'C++',
    '.hpp': 'C++ Header',   '.hh': 'C++ Header',
    '.cs': 'C#',
    '.go': 'Go',
    '.rs': 'Rust',
    '.rb': 'Ruby',
    '.php': 'PHP',
    '.swift': 'Swift',
    '.kt': 'Kotlin',        '.kts': 'Kotlin',
    '.scala': 'Scala',
    '.html': 'HTML',        '.htm': 'HTML',
    '.css': 'CSS',
    '.scss': 'SCSS',        '.sass': 'Sass',        '.less': 'Less',
    '.vue': 'Vue',
    '.sh': 'Shell',         '.bash': 'Shell',       '.zsh': 'Shell',
    '.sql': 'SQL',
    '.r': 'R',
    '.m': 'Objective-C',    '.mm': 'Objective-C++',
    '.lua': 'Lua',
    '.pl': 'Perl',          '.pm': 'Perl',
    '.yml': 'YAML',         '.yaml': 'YAML',
    '.json': 'JSON',
    '.xml': 'XML',
    '.md': 'Markdown',      '.markdown': 'Markdown',
    '.toml': 'TOML',        '.ini': 'INI',          '.cfg': 'INI',
    '.dart': 'Dart',
    '.ex': 'Elixir',        '.exs': 'Elixir',
    '.erl': 'Erlang',
    '.hs': 'Haskell',
    '.jl': 'Julia',
    '.asm': 'Assembly',
    '.f90': 'Fortran',
    '.pas': 'Pascal',
    '.vb': 'Visual Basic',
    '.txt': 'Text',
}

# 默认忽略的目录（版本控制、依赖、缓存等）
DEFAULT_IGNORE_DIRS = {
    '.git', '.svn', '.hg', '__pycache__', 'node_modules', 'venv', '.venv',
    'env', '.idea', '.vscode', 'dist', 'build', '.next', 'target',
    'vendor', 'bin', 'obj', '.gradle', '.mypy_cache', '.pytest_cache',
    'coverage', '.tox', 'site-packages', '.cache', 'logs',
}

# 各语言的注释规则：line=行注释符，block=块注释(起,止)
COMMENT_RULES = {
    'Python':        {'line': ['#'],  'block': [('"""', '"""'), ("'''", "'''")]},
    'JavaScript':    {'line': ['//'], 'block': [('/*', '*/')]},
    'TypeScript':    {'line': ['//'], 'block': [('/*', '*/')]},
    'Java':          {'line': ['//'], 'block': [('/*', '*/')]},
    'C':             {'line': ['//'], 'block': [('/*', '*/')]},
    'C/C++ Header':  {'line': ['//'], 'block': [('/*', '*/')]},
    'C++':           {'line': ['//'], 'block': [('/*', '*/')]},
    'C++ Header':    {'line': ['//'], 'block': [('/*', '*/')]},
    'C#':            {'line': ['//'], 'block': [('/*', '*/')]},
    'Go':            {'line': ['//'], 'block': [('/*', '*/')]},
    'Rust':          {'line': ['//'], 'block': [('/*', '*/')]},
    'PHP':           {'line': ['//', '#'], 'block': [('/*', '*/')]},
    'Swift':         {'line': ['//'], 'block': [('/*', '*/')]},
    'Kotlin':        {'line': ['//'], 'block': [('/*', '*/')]},
    'Scala':         {'line': ['//'], 'block': [('/*', '*/')]},
    'CSS':           {'line': [],     'block': [('/*', '*/')]},
    'SCSS':          {'line': ['//'], 'block': [('/*', '*/')]},
    'Less':          {'line': ['//'], 'block': [('/*', '*/')]},
    'HTML':          {'line': [],     'block': [('<!--', '-->')]},
    'Vue':           {'line': ['//'], 'block': [('/*', '*/'), ('<!--', '-->')]},
    'Shell':         {'line': ['#'],  'block': []},
    'SQL':           {'line': ['--'], 'block': [('/*', '*/')]},
    'Lua':           {'line': ['--'], 'block': [('--[[', ']]')]},
    'Ruby':          {'line': ['#'],  'block': [('=begin', '=end')]},
    'YAML':          {'line': ['#'],  'block': []},
    'TOML':          {'line': ['#'],  'block': []},
    'INI':           {'line': ['#', ';'], 'block': []},
    'Dart':          {'line': ['//'], 'block': [('/*', '*/')]},
    'Objective-C':   {'line': ['//'], 'block': [('/*', '*/')]},
    'Elixir':        {'line': ['#'],  'block': []},
}

# ==================== 核心逻辑 ====================

def detect_language(file_path: Path):
    """根据扩展名检测语言，未知返回 None"""
    return LANGUAGE_MAP.get(file_path.suffix.lower())


def should_ignore(path: Path, ignore_dirs: set) -> bool:
    """判断路径是否应被忽略"""
    # 检查路径任一部分是否在忽略目录集合中
    return any(part in ignore_dirs for part in path.parts)


def analyze_file(file_path: Path, language: str):
    """
    分析单个文件，返回 (总行, 代码行, 注释行, 空行)
    采用简单的状态机检测块注释
    """
    rule = COMMENT_RULES.get(language, {})
    line_symbols = rule.get('line', [])
    block_pairs = rule.get('block', [])

    total = code = comment = blank = 0
    in_block = False           # 是否处于块注释中
    block_end = None           # 当前块注释的结束符

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for raw_line in f:
                total += 1
                stripped = raw_line.strip()

                # 空行
                if not stripped:
                    blank += 1
                    continue

                # 处于块注释中
                if in_block:
                    comment += 1
                    if block_end and block_end in stripped:
                        in_block = False
                        block_end = None
                    continue

                # 行注释
                if any(stripped.startswith(sym) for sym in line_symbols):
                    comment += 1
                    continue

                # 块注释开始（处理同行闭合，如 /* comment */）
                matched = False
                for start, end in block_pairs:
                    if start in stripped:
                        comment += 1
                        if end not in stripped[start in stripped and stripped.index(start) + len(start):]:
                            # 结束符不在开始符之后 → 进入块注释
                            in_block = True
                            block_end = end
                        matched = True
                        break

                if matched:
                    continue

                # 其余视为代码行
                code += 1

    except (OSError, UnicodeDecodeError):
        return None  # 跳过无法读取的文件

    return total, code, comment, blank


def scan_project(root: Path, ignore_dirs: set):
    """
    扫描项目目录，返回统计结果
    结构：{language: {'files':数量,'total':总行,'code':代码行,'comment':注释行,'blank':空行,'last_mtime':时间戳}}
    """
    stats = defaultdict(lambda: {
        'files': 0, 'total': 0, 'code': 0, 'comment': 0, 'blank': 0, 'last_mtime': 0
    })
    project_last = 0        # 项目最近修改时间
    project_last_file = ''  # 对应文件

    for dirpath, dirnames, filenames in os.walk(root):
        # 就地过滤忽略目录（提升效率）
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs]

        for fname in filenames:
            fpath = Path(dirpath) / fname
            language = detect_language(fpath)
            if language is None:
                continue  # 跳过未知类型

            result = analyze_file(fpath, language)
            if result is None:
                continue

            total, code, comment, blank = result
            try:
                mtime = fpath.stat().st_mtime
            except OSError:
                mtime = 0

            s = stats[language]
            s['files'] += 1
            s['total'] += total
            s['code'] += code
            s['comment'] += comment
            s['blank'] += blank
            s['last_mtime'] = max(s['last_mtime'], mtime)

            if mtime > project_last:
                project_last = mtime
                project_last_file = str(fpath.relative_to(root))

    return dict(stats), project_last, project_last_file


# ==================== 输出展示 ====================

def fmt_time(ts: float) -> str:
    """时间戳格式化"""
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else 'N/A'


def print_report(stats: dict, project_last: float, project_last_file: str,
                 root: Path, top: int = None):
    """打印可视化统计报告"""
    if not stats:
        print(f"未在 {root} 中找到可统计的代码文件。")
        return

    # 汇总
    grand = {'files': 0, 'total': 0, 'code': 0, 'comment': 0, 'blank': 0}
    for s in stats.values():
        for k in grand:
            grand[k] += s[k]

    print("\n" + "=" * 78)
    print(f"  代码统计报告 — 项目路径: {root}")
    print("=" * 78)
    print(f"  总文件数: {grand['files']}    总行数: {grand['total']}")
    print(f"  代码行: {grand['code']}    注释行: {grand['comment']}    空行: {grand['blank']}")
    print(f"  项目最近更新: {fmt_time(project_last)}  ({project_last_file})")
    print("=" * 78)

    # 按代码行降序
    ordered = sorted(stats.items(), key=lambda x: x[1]['code'], reverse=True)
    if top:
        ordered = ordered[:top]

    # 表头
    header = f"{'语言':<14}{'文件数':>7}{'总行数':>9}{'代码行':>9}{'注释行':>8}{'空行':>8}{'占比':>8}  最后更新"
    print("\n" + header)
    print("-" * 100)

    for lang, s in ordered:
        pct = (s['code'] / grand['code'] * 100) if grand['code'] else 0
        print(f"{lang:<14}{s['files']:>7}{s['total']:>9}{s['code']:>9}"
              f"{s['comment']:>8}{s['blank']:>8}{pct:>7.1f}%  {fmt_time(s['last_mtime'])}")

    print("-" * 100 + "\n")


def export_json(stats: dict, project_last: float, project_last_file: str,
                root: Path, out_path: Path):
    """导出 JSON 报告"""
    data = {
        'root': str(root),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'project_last_modified': fmt_time(project_last),
        'project_last_file': project_last_file,
        'languages': {}
    }
    grand_code = sum(s['code'] for s in stats.values())
    for lang, s in sorted(stats.items(), key=lambda x: x[1]['code'], reverse=True):
        data['languages'][lang] = dict(
            s,
            ratio=round(s['code'] / grand_code * 100, 2) if grand_code else 0,
            last_modified=fmt_time(s['last_mtime'])
        )
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"JSON 报告已导出 → {out_path}")


# ==================== 入口 ====================

def main():
    parser = argparse.ArgumentParser(description='统计代码量、语言占比与更新时间')
    parser.add_argument('path', nargs='?', default='.', help='要统计的项目目录（默认当前目录）')
    parser.add_argument('-j', '--json', metavar='FILE', help='导出 JSON 报告到指定文件')
    parser.add_argument('--top', type=int, help='只显示代码量前 N 的语言')
    parser.add_argument('--ignore', nargs='*', default=[], help='额外忽略的目录名')
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"错误：{root} 不是有效目录")
        sys.exit(1)

    ignore_dirs = DEFAULT_IGNORE_DIRS | set(args.ignore)

    print(f"正在扫描 {root} ...")
    stats, project_last, project_last_file = scan_project(root, ignore_dirs)

    print_report(stats, project_last, project_last_file, root, args.top)

    if args.json:
        export_json(stats, project_last, project_last_file, root, Path(args.json))


if __name__ == '__main__':
    main()