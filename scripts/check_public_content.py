#!/usr/bin/env python3
"""Conservative local release-content checks; never print matched values."""

from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

TOP_FILES = {'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'SECURITY.md', 'package.json', '.gitignore'}
SOURCE_DIRS = {'bin', 'scripts', 'plugins', 'tests', 'examples', 'docs', '.github'}
SKIP_DIRS = {'.git', '.local-results', '__pycache__', 'node_modules'}
EXTENSIONS = {'.md', '.py', '.mjs', '.sh', '.json', '.yml', '.yaml'}
FORBIDDEN = {'.sqlite', '.db', '.log', '.jsonl', '.pem', '.key', '.bak', '.backup'}
MAX_FILE = 1024 * 1024
MAX_FILES = 500
RULES = [
    ('credential-shape', re.compile(r'\b(?:sk|ghp|github_pat)[-_][A-Za-z0-9_-]{20,}')),
    ('personal-path', re.compile(r'/(?:Users|home)/[^\s/"\']+/')),
    ('credential-assignment', re.compile(r'(?i)["\']?\b(?:api[_-]?key|password|client_secret)\b["\']?\s*[:=]\s*["\']?[A-Za-z0-9_+/=-]{16,}')),
    ('authorization-value', re.compile(r'(?i)\bBearer\s+[A-Za-z0-9_.+/=-]{16,}')),
    ('session-identifier', re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b')),
]
URL = re.compile(r'https?://[^\s<>"\'`)]+')


def scan(root):
    root = Path(root)
    findings = []
    count = 0
    def walk(folder):
        nonlocal count
        for path in sorted(folder.iterdir()):
            rel = path.relative_to(root)
            name = str(rel)
            # Never traverse links, even if their target is inside the project.
            if path.is_symlink():
                findings.append((name, 0, 'symlink'))
                continue
            if path.is_dir():
                if path.name in SKIP_DIRS:
                    continue
                if rel.parts[0] not in SOURCE_DIRS:
                    findings.append((name, 0, 'unapproved-path'))
                    continue
                yield from walk(path)
            else:
                count += 1
                if count > MAX_FILES:
                    raise ValueError('file-count limit')
                yield path, rel
    try:
        for path, rel in walk(root):
            name = str(rel)
            if path.suffix in FORBIDDEN or path.name.startswith('.env') or any(path.name.endswith(s) for s in ('-wal', '-shm', '-journal')):
                findings.append((name, 0, 'forbidden-file'))
                continue
            if not ((len(rel.parts) == 1 and path.name in TOP_FILES) or (len(rel.parts) > 1 and rel.parts[0] in SOURCE_DIRS and path.suffix in EXTENSIONS)):
                findings.append((name, 0, 'unapproved-path'))
                continue
            if not path.is_file() or path.stat().st_size > MAX_FILE:
                findings.append((name, 0, 'file-type-or-size'))
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except (UnicodeError, OSError):
                findings.append((name, 0, 'unreadable-text'))
                continue
            for number, line in enumerate(text.splitlines(), 1):
                for rule, expression in RULES:
                    if expression.search(line):
                        findings.append((name, number, rule))
                for match in URL.finditer(line):
                    value = urlsplit(match.group())
                    host = value.hostname or ''
                    placeholder = host == 'example.com' or host.endswith('.example') or host.endswith('.invalid')
                    loopback = host in {'127.0.0.1', 'localhost', '::1'}
                    public_reference = host in {'github.com', 'docs.github.com', 'opensource.org', 'spdx.org'} and value.scheme == 'https'
                    if value.username or value.password or value.query or not (placeholder or loopback or public_reference):
                        findings.append((name, number, 'nonpublic-endpoint'))
    except (OSError, ValueError):
        findings.append(('.', 0, 'scan-incomplete'))
    return count, findings


def main():
    root = Path(__file__).resolve().parents[1]
    count, findings = scan(root)
    for path, line, rule in findings[:100]:
        # Escape attacker-controlled filenames, not the file's matched contents.
        print('%s:%d %s' % (ascii(path), line, rule))
    print('files=%d findings=%d' % (count, len(findings)))
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
