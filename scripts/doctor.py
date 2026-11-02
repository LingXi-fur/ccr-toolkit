#!/usr/bin/env python3
"""CCR diagnostics: static by default; no model requests or config writes."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import urllib.request

ENV_FIELDS = ('CLAUDE_CODE_AUTO_COMPACT_WINDOW', 'CLAUDE_CODE_MAX_CONTEXT_TOKENS', 'DISABLE_AUTOUPDATER')
MAX_TEXT = 2 * 1024 * 1024
MAX_DB = 128 * 1024 * 1024


class InputError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, 'Invalid arguments; use --help.\n')


def port(value):
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 65535:
        raise argparse.ArgumentTypeError('Invalid port')
    return int(value)


def bounded_file(path, limit):
    """Reject final-component symlinks/non-regular files and concurrent changes."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise InputError()
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(limit + 1)
        after = os.fstat(fd)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(data) > limit or signature(before) != signature(after):
            raise InputError()
        return data
    finally:
        os.close(fd)


def object_file(path):
    value = json.loads(bounded_file(path, MAX_TEXT))
    if not isinstance(value, dict):
        raise InputError()
    return value


@contextmanager
def snapshot_db(path):
    """Query a bounded private copy, never open the source with SQLite.

    Active WAL/journal files are refused, not deleted. A quiet standalone DB or
    an operator-provided SQLite backup is required; this is not a backup tool.
    """
    path = Path(path)
    sidecars = [Path(str(path) + suffix) for suffix in ('-wal', '-shm', '-journal')]
    if any(os.path.lexists(p) for p in sidecars):
        raise InputError()
    data = bounded_file(path, MAX_DB)
    if any(os.path.lexists(p) for p in sidecars) or not data.startswith(b'SQLite format 3\x00'):
        raise InputError()
    with tempfile.TemporaryDirectory(prefix='ccr-doctor-') as temp:
        copy = Path(temp) / 'snapshot.db'
        copy.write_bytes(data)
        copy.chmod(0o600)
        connection = sqlite3.connect(copy.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=1)
        steps = 0
        def progress():
            nonlocal steps
            steps += 1
            return steps > 10000
        connection.set_progress_handler(progress, 1000)
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        if hasattr(connection, 'setlimit'):
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_TEXT)
        try:
            yield connection
        finally:
            connection.close()


def require_table(db, name):
    rows = db.execute('SELECT type, sql FROM sqlite_schema WHERE name = ? LIMIT 2', (name,)).fetchall()
    if len(rows) != 1 or rows[0][0] != 'table' or not isinstance(rows[0][1], str):
        raise InputError()
    if not re.match(r'^CREATE\s+TABLE\s', rows[0][1], re.IGNORECASE):
        raise InputError()
    # Disallow views, virtual tables and generated expressions on old SQLite
    # versions without a per-connection value-length limit.
    columns = db.execute('PRAGMA table_xinfo("%s")' % name).fetchall()
    if not columns or any(len(row) < 7 or row[6] != 0 for row in columns):
        raise InputError()


def read_config(path):
    with snapshot_db(path) as db:
        require_table(db, 'app_config')
        rows = db.execute('SELECT value_json FROM app_config WHERE key = ? LIMIT 2', ('default',)).fetchall()
    if len(rows) != 1 or not isinstance(rows[0][0], str) or len(rows[0][0]) > MAX_TEXT:
        raise InputError()
    value = json.loads(rows[0][0])
    if not isinstance(value, dict):
        raise InputError()
    return value


def environment(value):
    if not isinstance(value, dict):
        raise InputError()
    result = {}
    for key in ENV_FIELDS:
        if key not in value:
            continue
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise InputError()
        item = str(item)
        if not re.fullmatch(r'[0-9]{1,12}', item):
            raise InputError()
        result[key] = item
    return result


def profile_environment(config):
    profiles = config.get('profile', {}).get('profiles')
    if not isinstance(profiles, list):
        raise InputError()
    profiles = [p for p in profiles if isinstance(p, dict) and p.get('name') == 'code']
    if len(profiles) != 1:
        raise InputError()
    return environment(profiles[0].get('env', {}))


def wrapper_environment(path):
    source = bounded_file(path, MAX_TEXT).decode('utf-8')
    values = {}
    for line in source.splitlines():
        for key in ENV_FIELDS:
            if key not in line or line.lstrip().startswith('#'):
                continue
            match = re.fullmatch(r'\s*export\s+' + key + r'=(?:"([0-9]{1,12})"|\'([0-9]{1,12})\'|([0-9]{1,12}))\s*', line)
            if not match or key in values:
                raise InputError()
            values[key] = next(g for g in match.groups() if g is not None)
    return values


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def health(number, expected):
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 65535:
        return False
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open('http://127.0.0.1:%d/health' % number, timeout=3) as reply:
            data = reply.read(8193)
            if reply.status != 200 or len(data) > 8192:
                return False
            value = json.loads(data)
            return isinstance(value, dict) and value.get('status') == expected
    except (OSError, ValueError, TypeError, RecursionError):
        return False


def usage_counts(path, model, provider):
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=24)).isoformat(timespec='milliseconds')
    end = now.isoformat(timespec='milliseconds')
    with snapshot_db(path) as db:
        require_table(db, 'usage_events')
        # Bound the query to avoid silently scanning an unbounded personal DB.
        size = db.execute('SELECT COUNT(*) FROM (SELECT 1 FROM usage_events LIMIT 100001)').fetchone()[0]
        if size > 100000:
            raise InputError()
        row = db.execute('''SELECT COUNT(*),
            COALESCE(SUM(CASE WHEN provider IS NULL OR provider != ? THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END), 0)
            FROM usage_events WHERE model = ?
            AND julianday(created_at) >= julianday(?)
            AND julianday(created_at) <= julianday(?)''', (provider, model, start, end)).fetchone()
    return row


class Report:
    def __init__(self):
        self.failed = 0
        self.warned = 0

    def emit(self, status, check, message):
        # Only static strings and numeric aggregates reach this output boundary.
        print('%s %s: %s' % (status, check, message))
        self.failed += status == 'FAIL'
        self.warned += status == 'WARN'


ERRORS = (OSError, ValueError, TypeError, AttributeError, sqlite3.Error, InputError, RecursionError)


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('--ccr-dir', type=Path, default=Path.home() / '.claude-code-router')
    parser.add_argument('--claude-dir', type=Path, default=Path.home() / '.claude')
    parser.add_argument('--wrapper', type=Path, help='override wrapper file (parsed, never executed)')
    parser.add_argument('--expected-version', help='compare version directory in Claude symlink; never execute it')
    parser.add_argument('--claude-bin', type=Path, default=Path.home() / '.local/bin/claude')
    parser.add_argument('--health', action='store_true', help='explicit opt-in to loopback HTTP /health only')
    parser.add_argument('--front-port', type=port, default=3460)
    parser.add_argument('--core-port', type=port, default=3461)
    parser.add_argument('--usage', action='store_true', help='explicit opt-in to aggregate usage SQLite snapshot')
    parser.add_argument('--expected-model')
    parser.add_argument('--expected-provider')
    parser.add_argument('--strict', action='store_true', help='warnings also return exit 1')
    args = parser.parse_args(argv)
    if args.usage and not (args.expected_model and args.expected_provider):
        parser.error('usage requires explicit rules')
    if args.front_port == args.core_port and args.health:
        parser.error('distinct ports required')
    report = Report()
    config_path = args.ccr_dir / 'config.sqlite'
    settings_path = args.claude_dir / 'settings.json'
    wrapper_path = args.wrapper or args.ccr_dir / 'bin/ccr-claude-code-wrapper-default-claude-code'
    config = settings = None
    try:
        config = read_config(config_path)
        report.emit('PASS', 'config', 'supported configuration snapshot read')
    except ERRORS:
        report.emit('FAIL', 'config', 'unreadable/unsupported input, active sidecars, or size limit; details suppressed')
    try:
        settings = object_file(settings_path)
        report.emit('PASS', 'settings', 'object parsed')
    except ERRORS:
        report.emit('FAIL', 'settings', 'unreadable/invalid object; details suppressed')
    if config is not None and settings is not None:
        try:
            values = [profile_environment(config), environment(settings.get('env', {})), wrapper_environment(wrapper_path)]
            if values[0] == values[1] == values[2]:
                report.emit('PASS', 'env-drift', 'allowlisted static environment fields agree')
            else:
                report.emit('FAIL', 'env-drift', 'allowlisted static environment fields differ; values suppressed')
        except ERRORS:
            report.emit('FAIL', 'env-drift', 'unsupported profile or wrapper; details suppressed')
    else:
        report.emit('SKIP', 'env-drift', 'configuration prerequisites failed')
    for path in (config_path, settings_path, wrapper_path):
        try:
            info = path.lstat()
            if stat.S_ISREG(info.st_mode) and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
                report.emit('WARN', 'permissions', 'a sensitive input is group/world accessible or has a different owner')
        except OSError:
            pass
    if args.expected_version:
        try:
            resolved = args.claude_bin.resolve(strict=True)
            match = resolved.name == args.expected_version or resolved.parent.name == args.expected_version
            report.emit('PASS' if match else 'WARN', 'version', 'symlink path label matches' if match else 'symlink path label differs; not a binary version attestation')
        except ERRORS:
            report.emit('FAIL', 'version', 'binary path unavailable; details suppressed')
    else:
        report.emit('SKIP', 'version', 'no expected version supplied')
    if args.usage:
        try:
            count, mismatches, errors = usage_counts(args.ccr_dir / 'app-data/usage.sqlite', args.expected_model, args.expected_provider)
            if not count:
                report.emit('WARN', 'routing', 'no matching events in past 24h; no identity attestation')
            else:
                report.emit('WARN' if mismatches or errors else 'PASS', 'routing', 'events=%d mismatches=%d server_errors=%d; recorded routes only' % (count, mismatches, errors))
        except ERRORS:
            report.emit('FAIL', 'routing', 'usage snapshot unreadable, unsupported, active, or over budget')
    else:
        report.emit('SKIP', 'routing', 'usage aggregation not requested')
    if args.health:
        for name, number, expected in (('front', args.front_port, 'running'), ('core', args.core_port, 'ok')):
            good = health(number, expected)
            report.emit('PASS' if good else 'FAIL', 'health-' + name, 'loopback status matches' if good else 'loopback status check failed; details suppressed')
    else:
        report.emit('SKIP', 'health', 'network disabled; use --health for loopback-only checks')
    failed = report.failed or (args.strict and report.warned)
    print('RESULT %s failures=%d warnings=%d' % ('FAIL' if failed else 'WARN' if report.warned else 'PASS', report.failed, report.warned))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
