#!/usr/bin/env python3
"""Real-host readiness gate (preflight only); this revision never starts CCR."""

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import resource
import secrets
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time

MAX_METADATA = 65536
MAX_ENTRY = 16 * 1024 * 1024
MAX_OUTPUT = 65536


class GateError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise GateError('invalid-arguments')


def bounded_regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise GateError('unsupported-input')
        data = source.read(limit + 1)
        after = os.fstat(source.fileno())
        if len(data) > limit or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise GateError('unsupported-input')
        return data


def inspect_entry(value):
    # Metadata is only a compatibility gate, not package provenance attestation.
    path = Path(value)
    if not path.is_absolute() or len(os.fsencode(path)) > 4096:
        raise GateError('unsupported-entry')
    if path.parts[-3:] != ('dist', 'main', 'cli.js') or path.resolve() != path:
        raise GateError('unsupported-entry')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ENTRY:
        raise GateError('unsupported-entry')
    metadata = json.loads(bounded_regular(path.parents[2] / 'package.json', MAX_METADATA))
    # Pin tracks the locally installed version (3.1.3 since 2026-11-06).
    # Source-derived plugin facts in docs were audited on 3.0.22 bytes and
    # are not re-audited for this version.
    if not isinstance(metadata, dict) or metadata.get('name') != '@musistudio/claude-code-router' or metadata.get('version') != '3.1.3':
        raise GateError('unsupported-package')
    return path


def probe_environment(root):
    return {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'TMPDIR': str(root), 'LANG': 'C'}


def capability_policy():
    # Explicit candidate bootstrap rules, based on Apple's dyld-support.sb.
    # Do not import mutable system profiles or treat a zero exit as isolation proof.
    # The three extra syscall numbers are the exact measured deny set from the
    # second probe (execve 59, stat64 338, write_nocancel 397); under deny
    # default the syscall-unix layer is checked independently of process-exec.
    # The remaining syscall numbers are first-round candidate rules, not
    # measured denies. /dev/fd write access mirrors system.sb:119 so inherited
    # pipe descriptors stay usable; no disk write target is granted.
    return '''(version 3)
(deny default)
(allow process-exec (literal "/usr/bin/true"))
(allow file-read* file-test-existence (literal "/usr/bin/true") (literal "/"))
(allow file-read* file-test-existence file-map-executable
    (subpath "/usr/lib")
    (subpath "/System/Library/Frameworks")
    (subpath "/System/Library/PrivateFrameworks")
    (subpath "/System/Cryptexes/OS")
    (subpath "/System/Volumes/Preboot/Cryptexes/OS"))
(allow file-read* file-test-existence
    (path-ancestors "/System/Cryptexes/OS")
    (path-ancestors "/System/Volumes/Preboot/Cryptexes/OS"))
(allow file-read-data file-test-existence file-write-data
    (subpath "/dev/fd"))
(allow syscall-unix
    (syscall-number SYS___mac_syscall SYS_getfsstat SYS_getfsstat64
                    SYS_map_with_linking_np SYS_open SYS_openat
                    SYS_fstatat SYS_fstatat64 SYS_dup
                    SYS_execve SYS_stat64 SYS_write_nocancel))
(allow system-fcntl
    (fcntl-command F_ADDFILESIGS_RETURN F_CHECK_LV F_GETPATH))
(allow system-mac-syscall
    (require-all (mac-policy-name "Sandbox") (mac-syscall-number 2)))'''


def sandbox_available():
    path = Path('/usr/bin/sandbox-exec')
    return path.is_file() and os.access(path, os.X_OK)


def child_limits():
    # A failing sandbox can abort; do not leave a core dump of its process.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def bounded_probe(command, env, cwd, timeout=5, limit=MAX_OUTPUT):
    if not math.isfinite(timeout) or timeout <= 0 or not isinstance(limit, int) or limit < 1 or limit > MAX_OUTPUT:
        raise GateError('invalid-probe-limit')
    try:
        process = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   close_fds=True, preexec_fn=child_limits)
    except OSError:
        return 'probe-launch-failed', None, 0
    counts = {}
    total = 0
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdout, process.stderr):
                selector.register(stream, selectors.EVENT_READ)
                counts[stream.fileno()] = 0
            while selector.get_map():
                remaining = deadline - time.monotonic()
                events = selector.select(max(0, remaining))
                if remaining <= 0 or not events:
                    return 'probe-timeout', None, total
                for key, _ in events:
                    fd = key.fd
                    chunk = os.read(fd, min(8192, limit + 1 - counts[fd]))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    counts[fd] += len(chunk)
                    total += len(chunk)
                    if counts[fd] > limit:
                        return 'probe-output-limit', None, total
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 'probe-timeout', None, total
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            return 'probe-timeout', None, total
        return 'probe-exited', code, total
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def interpret_probe(code):
    return 'capability-only' if code == 0 else 'sandbox-capability-failed'


@contextmanager
def probe_workspace():
    parent = Path(tempfile.gettempdir()).resolve()
    root = Path(tempfile.mkdtemp(prefix='ccr-toolkit-preflight-', dir=parent))
    identity = root.stat()
    token = secrets.token_hex(16).encode()
    marker = root / '.owner'
    with marker.open('xb') as out:
        out.write(token)
    marker.chmod(0o600)
    try:
        yield root
    finally:
        current = root.lstat()
        if (not stat.S_ISDIR(current.st_mode) or root.resolve().parent != parent
                or (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino)
                or bounded_regular(marker, 64) != token):
            raise GateError('cleanup-ownership-refused')
        shutil.rmtree(root)


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('--allow-real-host', action='store_true', help='explicitly permit a sandbox capability probe; does not start CCR')
    parser.add_argument('--ccr-entry', type=Path, help='absolute installed dist/main/cli.js path; metadata only, never executed')
    try:
        args = parser.parse_args(argv)
        if not args.allow_real_host or args.ccr_entry is None:
            raise GateError('explicit-opt-in-and-entry-required')
        if sys.platform != 'darwin':
            raise GateError('unsupported-platform')
        inspect_entry(args.ccr_entry)
        if not sandbox_available():
            raise GateError('sandbox-unavailable')
        with probe_workspace() as root:
            status, code, count = bounded_probe(
                ['/usr/bin/sandbox-exec', '-p', capability_policy(), '/usr/bin/true'],
                probe_environment(root), root)
        print('probe=%s exit=%s output_bytes=%d' % (status, code, count))
        if status != 'probe-exited' or interpret_probe(code) != 'capability-only':
            raise GateError('sandbox-capability-failed')
        # Capability success proves neither resource isolation nor host loading.
        raise GateError('integration-not-implemented')
    except GateError as error:
        print('BLOCKED %s; CCR not started; no fallback' % error, file=sys.stderr)
    except (OSError, ValueError, TypeError, RecursionError, subprocess.SubprocessError):
        print('BLOCKED preflight-input-or-process-error; CCR not started; no fallback', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
