"""
简单的仓库密钥扫描脚本（非完全替代审计）
用法：
  python3 tools/secret_scan.py

会扫描仓库内常见的敏感关键词并输出匹配行。
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(__file__))
PATTERNS = [
    r"DEEPSEEK_API_KEY",
    r"DINGTALK_CLIENT_SECRET",
    r"DINGTALK_CLIENT_ID",
    r"sk-[A-Za-z0-9-_]+",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN PRIVATE KEY-----",
    r"password\s*=",
]


def scan():
    for dirpath, dirs, files in os.walk(ROOT):
        # skip .git and virtual envs
        if '.git' in dirpath or 'venv' in dirpath or 'node_modules' in dirpath:
            continue
        for fn in files:
            if fn.endswith(('.pyc', '.png', '.jpg', '.jpeg', '.db')):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, 'r', errors='ignore') as f:
                    for i, line in enumerate(f, 1):
                        for p in PATTERNS:
                            if re.search(p, line):
                                print(f"MATCH: {path}:{i}: {p} -> {line.strip()}")
            except Exception:
                continue


if __name__ == '__main__':
    scan()
