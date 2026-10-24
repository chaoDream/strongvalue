#!/usr/bin/env python3
"""Check the maintained user guide without importing or starting the application."""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

GUIDE = 'docs/USER-GUIDE.md'
SOURCE_DIRS = ('backend/', 'frontend/src/', 'scripts/')
SOURCE_SUFFIXES = {'.py', '.tsx', '.ts', '.css', '.swift'}
RUNTIME_FILES = {'.env.example', 'Dockerfile', 'requirements.txt',
                 'requirements.lock.txt', 'frontend/package.json',
                 'frontend/pnpm-lock.yaml', 'frontend/vite.config.ts'}


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        raise ValueError('Git 基线不可读取，请提供已有提交或完整获取比较历史。')
    return result.stdout.decode('utf-8')


def check_guide(root, base=None):
    issues = []
    path = root / GUIDE
    if not path.is_file():
        return ['缺少详细使用手册：' + GUIDE]
    guide = path.read_text(encoding='utf-8')
    version = re.search(r'^适用版本：([0-9]+\.[0-9]+\.[0-9]+)\s*$', guide, re.M)
    frontend = json.loads((root / 'frontend/package.json').read_text())['version']
    backend = re.search(r"\bversion\s*=\s*['\"]([^'\"]+)['\"]",
                        (root / 'backend/main.py').read_text())
    if not version or not backend or version[1] != frontend or version[1] != backend[1]:
        issues.append('手册适用版本必须与 frontend/package.json 及 backend/main.py 一致。')
    if not re.search(r'^最后更新：\d{4}-\d{2}-\d{2}\s*$', guide, re.M):
        issues.append('手册缺少“最后更新：YYYY-MM-DD”。')
    # Ignore code examples: their paths and links are explanatory, not navigation.
    prose = re.sub(r'^```[^\n]*\n.*?^```\s*$', '', guide, flags=re.M | re.S)
    anchors = re.findall(r'<a id="([^"]+)"\s*></a>', prose)
    if len(set(anchors)) != len(anchors):
        issues.append('手册存在重复的章节锚点。')
    for target in re.findall(r'!?\[[^\]\n]*\]\(([^\s)]+)\)', prose):
        parsed = urlsplit(target.strip('<>'))
        if parsed.scheme or parsed.netloc:
            continue
        if not parsed.path:
            if parsed.fragment and unquote(parsed.fragment) not in anchors:
                issues.append('手册目录指向不存在的章节：' + target)
        elif not (path.parent / unquote(parsed.path)).is_file():
            issues.append('手册引用的本地文件不存在：' + target)
    if base and not re.fullmatch(r'0+', base):
        try:
            commit = git(root, 'rev-parse', '--verify', '--end-of-options', base + '^{commit}').strip()
            # Compare the base with the working tree, so local staged/unstaged edits
            # and committed CI changes both work. Include untracked new features.
            changed = set(git(root, 'diff', '--name-only', '-z', commit, '--').split('\0'))
            changed.update(git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0'))
            sources = sorted(p for p in changed if p in RUNTIME_FILES or
                             p.startswith(SOURCE_DIRS) and Path(p).suffix in SOURCE_SUFFIXES)
            if sources and GUIDE not in changed:
                issues.append('功能／运行文件发生变化，但使用手册未同步修改：' + '、'.join(sources[:8]))
        except ValueError as exc:
            issues.append(str(exc))
    return issues


def main():
    parser = argparse.ArgumentParser(description='检查使用手册版本、目录、引用及功能变动时的同步维护。')
    parser.add_argument('--base', default=os.getenv('USER_GUIDE_BASE') or None,
                        help='与指定 Git 提交比较；省略时只做文档静态检查')
    args = parser.parse_args()
    try:
        issues = check_guide(Path(__file__).resolve().parents[1], args.base)
    except (OSError, ValueError, KeyError) as exc:
        issues = ['使用手册检查无法完成：' + str(exc)]
    for issue in issues:
        print('错误：' + issue, file=sys.stderr)
    if issues:
        return 1
    print('使用手册检查通过（版本、目录、本地引用' + ('、功能变动同步' if args.base and not re.fullmatch(r'0+', args.base) else '') + '）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
