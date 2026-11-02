#!/usr/bin/env python3
"""Conservative local release-content checks; never print matched values."""

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

TOP_FILES = {'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'SECURITY.md', 'package.json', '.gitignore'}
SOURCE_DIRS = {'bin', 'scripts', 'plugins', 'tests', 'examples', 'docs', '.github'}
SKIP_DIRS = {'.git', '.claude', '.local-results', '__pycache__', 'node_modules'}
EXTENSIONS = {'.md', '.py', '.mjs', '.sh', '.json', '.yml', '.yaml'}
FORBIDDEN = {'.sqlite', '.db', '.log', '.jsonl', '.pem', '.key', '.bak', '.backup'}
MAX_FILE = 1024 * 1024
MAX_FILES = 500
MAX_MANIFEST = 256 * 1024
RULES = [
    ('credential-shape', re.compile(r'\b(?:sk|ghp|github_pat)[-_][A-Za-z0-9_-]{20,}')),
    ('personal-path', re.compile(r'/(?:Users|home)/[^\s/"\']+/')),
    ('credential-assignment', re.compile(r'(?i)["\']?\b(?:api[_-]?key|password|client_secret)\b["\']?\s*[:=]\s*["\']?[A-Za-z0-9_+/=-]{16,}')),
    ('authorization-value', re.compile(r'(?i)\bBearer\s+[A-Za-z0-9_.+/=-]{16,}')),
    ('session-identifier', re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b')),
]
URL = re.compile(r'https?://[^\s<>"\'`)]+')


def path_rule(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return 'unapproved-path'
    if any(part in SKIP_DIRS for part in path.parts):
        return 'forbidden-directory'
    if path.suffix in FORBIDDEN or path.name.startswith('.env') or path.name.endswith(('-wal', '-shm', '-journal')):
        return 'forbidden-file'
    if not ((len(path.parts) == 1 and path.name in TOP_FILES) or (len(path.parts) > 1 and path.parts[0] in SOURCE_DIRS and path.suffix in EXTENSIONS)):
        return 'unapproved-path'
    return None


def scan_content(name, data):
    if len(data) > MAX_FILE:
        return [(name, 0, 'file-type-or-size')]
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        return [(name, 0, 'unreadable-text')]
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        for rule, expression in RULES:
            if expression.search(line):
                findings.append((name, number, rule))
        for match in URL.finditer(line):
            try:
                value = urlsplit(match.group())
                host = value.hostname or ''
                placeholder = host == 'example.com' or host.endswith('.example') or host.endswith('.invalid')
                loopback = host in {'127.0.0.1', 'localhost', '::1'}
                public_reference = host in {'github.com', 'docs.github.com', 'opensource.org', 'spdx.org'} and value.scheme == 'https'
                allowed = not (value.username or value.password or value.query) and (placeholder or loopback or public_reference)
            except ValueError:
                allowed = False
            if not allowed:
                findings.append((name, number, 'nonpublic-endpoint'))
    return findings


def scan(root):
    root = Path(root)
    findings = []
    count = 0
    def walk(folder):
        nonlocal count
        for path in sorted(folder.iterdir()):
            rel = path.relative_to(root)
            name = rel.as_posix()
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
                yield path, name
    try:
        for path, name in walk(root):
            rule = path_rule(name)
            if rule:
                findings.append((name, 0, rule))
                continue
            if not path.is_file() or path.stat().st_size > MAX_FILE:
                findings.append((name, 0, 'file-type-or-size'))
                continue
            try:
                with path.open('rb') as source:
                    data = source.read(MAX_FILE + 1)
                findings.extend(scan_content(name, data))
            except OSError:
                findings.append((name, 0, 'unreadable-text'))
    except (OSError, ValueError):
        findings.append(('.', 0, 'scan-incomplete'))
    return count, findings


def git_output(root, args, limit):
    # Do not inherit GIT_DIR, alternate indexes, replacement objects, config
    # injection or object-directory overrides from the caller's environment.
    git = shutil.which('git')
    if not git:
        raise ValueError('Git unavailable')
    env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'WINDIR') if k in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1',
               GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='', LC_ALL='C')
    command = [git, '--no-optional-locks', '-c', 'core.fsmonitor=false',
               '-c', 'protocol.allow=never', '-c', 'protocol.ext.allow=never',
               '-C', str(root), *args]
    # Read only limit+1 bytes. Never spool an unbounded manifest to disk.
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    output = bytearray()
    deadline = time.monotonic() + 10
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError('Git command timed out')
                chunk = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    raise ValueError('Git output over limit')
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining):
            raise ValueError('Git command failed')
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def scan_git(root, snapshot):
    root = Path(root).resolve()
    findings = []
    count = 0
    try:
        if snapshot not in {'index', 'head'}:
            raise ValueError('unknown snapshot')
        top = git_output(root, ['rev-parse', '--show-toplevel'], 8192).decode().rstrip('\n')
        if Path(top).resolve() != root:
            raise ValueError('not repository root')
        if snapshot == 'index':
            raw = git_output(root, ['ls-files', '--stage', '-z'], MAX_MANIFEST)
        else:
            commit = git_output(root, ['rev-parse', '--verify', 'HEAD^{commit}'], 128).decode().strip()
            if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
                raise ValueError('invalid commit')
            raw = git_output(root, ['ls-tree', '-r', '-z', commit], MAX_MANIFEST)
        entries = raw.split(b'\0')
        if entries[-1] != b'':
            raise ValueError('incomplete manifest')
        entries.pop()
        if not entries:
            return 0, [('.', 0, 'empty-manifest')]
        if len(entries) > MAX_FILES:
            raise ValueError('file-count limit')
        for entry in entries:
            metadata, name_bytes = entry.split(b'\t', 1)
            name = name_bytes.decode('utf-8')
            fields = metadata.decode('ascii').split()
            if snapshot == 'index':
                mode, oid, stage = fields
                if stage != '0':
                    findings.append((name, 0, 'unmerged-entry'))
                    continue
            else:
                mode, kind, oid = fields
                if kind not in {'blob', 'commit'}:
                    raise ValueError('unsupported tree entry')
            count += 1
            rule = path_rule(name)
            if mode == '120000':
                rule = 'symlink'
            elif mode == '160000':
                rule = 'submodule'
            elif mode not in {'100644', '100755'}:
                rule = 'file-type-or-size'
            if rule:
                findings.append((name, 0, rule))
                continue
            if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid):
                raise ValueError('invalid object')
            size = int(git_output(root, ['cat-file', '-s', oid], 32))
            if size > MAX_FILE:
                findings.append((name, 0, 'file-type-or-size'))
                continue
            data = git_output(root, ['cat-file', 'blob', oid], MAX_FILE)
            if len(data) != size:
                raise ValueError('object size mismatch')
            findings.extend(scan_content(name, data))
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        findings.append(('.', 0, 'git-scan-incomplete'))
    return count, findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--index', action='store_true', help='inspect all staged paths and blobs, including ignored paths')
    group.add_argument('--head', action='store_true', help='inspect all HEAD paths and blobs, not worktree contents')
    args = parser.parse_args()
    if args.index or args.head:
        count, findings = scan_git(args.root, 'index' if args.index else 'head')
    else:
        count, findings = scan(args.root)
    for path, line, rule in findings[:100]:
        print('%s:%d %s' % (ascii(path), line, rule))
    print('files=%d findings=%d' % (count, len(findings)))
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
