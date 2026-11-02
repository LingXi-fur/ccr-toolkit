import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'doctor.py'


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.ccr = self.home / '.claude-code-router'
        self.claude = self.home / '.claude'
        self.ccr.mkdir()
        self.claude.mkdir()
        self.config = {'profile': {'profiles': [{'name': 'code', 'env': {'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '100000'}}]}}
        self.db = self.ccr / 'config.sqlite'
        self.save_config()
        self.settings = self.claude / 'settings.json'
        self.settings.write_text(json.dumps({'env': {'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '100000'}}))
        self.settings.chmod(0o600)
        self.wrapper = self.ccr / 'bin' / 'ccr-claude-code-wrapper-default-claude-code'
        self.wrapper.parent.mkdir()
        self.wrapper.write_text("export CLAUDE_CODE_AUTO_COMPACT_WINDOW='100000'\n")
        self.wrapper.chmod(0o600)

    def save_config(self):
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value_json TEXT)')
            db.execute('INSERT OR REPLACE INTO app_config VALUES (?, ?)', ('default', json.dumps(self.config)))
        self.db.chmod(0o600)

    def run_doctor(self, *args):
        env = {'HOME': str(self.home), 'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'}
        return subprocess.run([sys.executable, '-I', str(SCRIPT), '--ccr-dir', str(self.ccr), '--claude-dir', str(self.claude), *args],
                              env=env, capture_output=True, text=True, timeout=15)

    def snapshot(self):
        return {str(p.relative_to(self.home)): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.home.rglob('*') if p.is_file()}

    def test_pass_and_no_file_mutation(self):
        before = self.snapshot()
        run = self.run_doctor()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('PASS config', run.stdout)
        self.assertIn('PASS env-drift', run.stdout)
        self.assertIn('SKIP health', run.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_missing_db_does_not_create_file(self):
        self.db.unlink()
        run = self.run_doctor()
        self.assertEqual(run.returncode, 1)
        self.assertIn('FAIL config', run.stdout)
        self.assertFalse(self.db.exists())

    def test_invalid_settings_never_prints_content(self):
        self.settings.write_text('PRIVATE-CONTENT\x1b[31m')
        run = self.run_doctor()
        self.assertEqual(run.returncode, 1)
        self.assertNotIn('PRIVATE', run.stdout + run.stderr)
        self.assertNotIn('\x1b', run.stdout + run.stderr)

    def test_environment_drift(self):
        self.settings.write_text(json.dumps({'env': {'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '999'}}))
        run = self.run_doctor()
        self.assertEqual(run.returncode, 1)
        self.assertIn('FAIL env-drift', run.stdout)

    def test_wrapper_is_parsed_not_executed(self):
        sentinel = self.home / 'executed'
        self.wrapper.write_text(f"touch '{sentinel}'\nexport CLAUDE_CODE_AUTO_COMPACT_WINDOW='100000'\n")
        self.assertEqual(self.run_doctor().returncode, 0)
        self.assertFalse(sentinel.exists())

    def test_duplicate_profile_fails(self):
        self.config['profile']['profiles'].append({'name': 'code', 'env': {}})
        self.save_config()
        self.assertEqual(self.run_doctor().returncode, 1)

    def test_wrong_schema_fails(self):
        self.db.unlink()
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE other (value TEXT)')
        self.assertIn('FAIL config', self.run_doctor().stdout)

    def test_corrupt_database_fails_without_exception_dump(self):
        self.db.write_bytes(b'PRIVATE-BROKEN-DATABASE')
        run = self.run_doctor()
        self.assertEqual(run.returncode, 1)
        self.assertNotIn('PRIVATE', run.stdout + run.stderr)
        self.assertNotIn('Traceback', run.stderr)

    def test_wal_refused_without_side_effects(self):
        with sqlite3.connect(self.db) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('INSERT INTO app_config VALUES (?, ?)', ('another', '{}'))
            db.commit()
            before = self.snapshot()
            run = self.run_doctor()
            self.assertEqual(run.returncode, 1)
            self.assertIn('FAIL config', run.stdout)
            self.assertEqual(self.snapshot(), before)

    def test_symlink_sensitive_file_refused(self):
        other = self.home / 'other'
        self.settings.rename(other)
        self.settings.symlink_to(other)
        self.assertEqual(self.run_doctor().returncode, 1)

    def test_large_config_fails(self):
        self.settings.write_bytes(b' ' * (2 * 1024 * 1024 + 1))
        self.assertEqual(self.run_doctor().returncode, 1)

    def test_permissions_warn_and_strict_mode(self):
        self.settings.chmod(0o644)
        run = self.run_doctor()
        self.assertEqual(run.returncode, 0)
        self.assertIn('WARN permissions', run.stdout)
        self.assertEqual(self.run_doctor('--strict').returncode, 1)

    def test_raw_provider_and_model_values_not_printed(self):
        self.config['Router'] = {'fallback': {'models': ['PRIVATE\x1b[31m/PRIVATE']}}
        self.save_config()
        run = self.run_doctor()
        self.assertNotIn('PRIVATE', run.stdout + run.stderr)
        self.assertNotIn('\x1b', run.stdout + run.stderr)

    def test_usage_opt_in_and_aggregate_only(self):
        usage_dir = self.ccr / 'app-data'
        usage_dir.mkdir()
        with sqlite3.connect(usage_dir / 'usage.sqlite') as db:
            db.execute('CREATE TABLE usage_events (created_at TEXT, model TEXT, provider TEXT, status_code INTEGER)')
            db.execute("INSERT INTO usage_events VALUES (strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, 200)", ('example-model', 'other-provider'))
        run = self.run_doctor('--usage', '--expected-model', 'example-model', '--expected-provider', 'example-provider')
        self.assertEqual(run.returncode, 0)
        self.assertIn('WARN routing', run.stdout)
        self.assertIn('mismatches=1', run.stdout)
        self.assertNotIn('other-provider', run.stdout)

    def test_usage_requires_explicit_rules(self):
        run = self.run_doctor('--usage')
        self.assertEqual(run.returncode, 2)
        self.assertNotIn('Traceback', run.stderr)

    def test_invalid_ports_rejected_without_echo(self):
        run = self.run_doctor('--health', '--front-port', 'PRIVATE')
        self.assertEqual(run.returncode, 2)
        self.assertNotIn('PRIVATE', run.stdout + run.stderr)

    def test_health_fixture_without_network(self):
        spec = importlib.util.spec_from_file_location('doctor', SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        calls = []
        class Reply:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'{"status":"running"}'
        class Opener:
            def open(self, url, timeout):
                calls.append((url, timeout))
                return Reply()
        with patch.object(mod.urllib.request, 'build_opener', return_value=Opener()):
            self.assertTrue(mod.health(4000, 'running'))
        self.assertEqual(calls, [('http://127.0.0.1:4000/health', 3)])

    def test_health_errors_and_large_reply_are_bounded(self):
        spec = importlib.util.spec_from_file_location('doctor', SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch.object(mod.urllib.request, 'build_opener', side_effect=TimeoutError()):
            self.assertFalse(mod.health(4000, 'running'))

    def test_shell_entrypoint_uses_temporary_inputs(self):
        run = subprocess.run(['bash', str(ROOT / 'bin/ccr-doctor.sh'), '--ccr-dir', str(self.ccr), '--claude-dir', str(self.claude)],
                             env={'HOME': str(self.home), 'PATH': '/usr/bin:/bin'},
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('RESULT PASS', run.stdout)

    def test_rollback_journal_refused(self):
        with sqlite3.connect(self.db) as db:
            db.execute('BEGIN EXCLUSIVE')
            db.execute('INSERT INTO app_config VALUES (?, ?)', ('pending', '{}'))
            before = self.snapshot()
            run = self.run_doctor()
            self.assertEqual(run.returncode, 1)
            self.assertIn('FAIL config', run.stdout)
            self.assertEqual(self.snapshot(), before)
            db.rollback()

    def test_usage_unsupported_schema_fails(self):
        usage_dir = self.ccr / 'app-data'
        usage_dir.mkdir()
        with sqlite3.connect(usage_dir / 'usage.sqlite') as db:
            db.execute('CREATE TABLE usage_events (other TEXT)')
        run = self.run_doctor('--usage', '--expected-model', 'example-model', '--expected-provider', 'example-provider')
        self.assertEqual(run.returncode, 1)
        self.assertIn('FAIL routing', run.stdout)

    def test_default_run_has_no_network_or_process_execution(self):
        spec = importlib.util.spec_from_file_location('doctor', SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch.object(mod.urllib.request, 'build_opener', side_effect=AssertionError('network forbidden')), \
                patch.object(subprocess, 'Popen', side_effect=AssertionError('process forbidden')), \
                patch('builtins.print'):
            code = mod.main(['--ccr-dir', str(self.ccr), '--claude-dir', str(self.claude)])
        self.assertEqual(code, 0)

    def test_generated_value_view_refused(self):
        self.db.unlink()
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE VIEW app_config AS SELECT 'default' AS key, hex(zeroblob(3000000)) AS value_json")
        run = self.run_doctor()
        self.assertEqual(run.returncode, 1)
        self.assertIn('FAIL config', run.stdout)

    def test_wrapper_later_assignment_and_dynamic_syntax_refused(self):
        for line in ['CLAUDE_CODE_AUTO_COMPACT_WINDOW=999',
                     'if true; then export CLAUDE_CODE_AUTO_COMPACT_WINDOW=999; fi',
                     'unset CLAUDE_CODE_AUTO_COMPACT_WINDOW']:
            self.wrapper.write_text("export CLAUDE_CODE_AUTO_COMPACT_WINDOW='100000'\n" + line + '\n')
            run = self.run_doctor()
            self.assertEqual(run.returncode, 1, line)
            self.assertIn('FAIL env-drift', run.stdout)

    def test_deep_or_oversized_health_json_fails_closed(self):
        spec = importlib.util.spec_from_file_location('doctor', SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        class Reply:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return self.body[:limit]
        class Opener:
            def open(self, url, timeout): return reply
        for body in [b'[' * 2000 + b'0' + b']' * 2000, b' ' * 8193]:
            reply = Reply()
            reply.body = body
            with patch.object(mod.urllib.request, 'build_opener', return_value=Opener()):
                self.assertFalse(mod.health(4000, 'running'))

    def test_help_does_not_require_real_home(self):
        run = self.run_doctor('--help')
        self.assertEqual(run.returncode, 0)
        self.assertIn('--health', run.stdout)


if __name__ == '__main__':
    unittest.main()
