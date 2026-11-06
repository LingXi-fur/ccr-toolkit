#!/usr/bin/env python3
"""Offline regression tests for the real-host readiness gate; never start CCR."""

import importlib.util
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'run_real_host.py'
spec = importlib.util.spec_from_file_location('real_host_gate', SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def run_gate(self, *args):
        return subprocess.run([sys.executable, '-I', str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=10,
                              env={'PATH': '/usr/bin:/bin'})

    def test_probe_workspace_private_and_cleaned(self):
        with gate.probe_workspace() as root:
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / '.owner').stat().st_mode & 0o777, 0o600)
        self.assertFalse(root.exists())

    def test_cleanup_refuses_changed_owner_marker(self):
        root = None
        try:
            with self.assertRaises(gate.GateError):
                with gate.probe_workspace() as root:
                    (root / '.owner').write_bytes(b'changed-synthetic-marker')
        finally:
            if root is not None and root.exists():
                import shutil
                shutil.rmtree(root)

    def test_probe_reaps_child_after_output_limit(self):
        real_popen = gate.subprocess.Popen
        children = []
        def spawn(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        with patch.object(gate.subprocess, 'Popen', side_effect=spawn):
            result = gate.bounded_probe([sys.executable, '-I', '-c',
                                         'import os,time; os.write(2,b"x"*4096); time.sleep(30)'],
                                        gate.probe_environment(self.root), self.root, timeout=2, limit=64)
        self.assertEqual(result[0], 'probe-output-limit')
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed)
        self.assertTrue(children[0].stderr.closed)

    def test_probe_reaps_child_after_timeout(self):
        real_popen = gate.subprocess.Popen
        children = []
        def spawn(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child
        with patch.object(gate.subprocess, 'Popen', side_effect=spawn):
            result = gate.bounded_probe([sys.executable, '-I', '-c', 'import time; time.sleep(30)'],
                                        gate.probe_environment(self.root), self.root, timeout=0.05)
        self.assertEqual(result[0], 'probe-timeout')
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed)
        self.assertTrue(children[0].stderr.closed)

    def test_invalid_probe_limits_do_not_start_process(self):
        with patch.object(gate.subprocess, 'Popen') as spawn:
            for options in ({'timeout': 0}, {'timeout': float('nan')}, {'limit': 0}, {'limit': gate.MAX_OUTPUT + 1}):
                with self.assertRaises(gate.GateError):
                    gate.bounded_probe(['/synthetic/program'], {}, self.root, **options)
        spawn.assert_not_called()

    def test_help_does_not_claim_integration(self):
        result = self.run_gate('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('preflight only', result.stdout)

    def test_default_refuses_without_reading_or_starting(self):
        with patch.object(gate, 'inspect_entry') as inspect, patch.object(gate, 'bounded_probe') as probe:
            self.assertEqual(gate.main([]), 2)
        inspect.assert_not_called()
        probe.assert_not_called()

    def test_missing_entry_refuses_before_probe(self):
        with patch.object(gate, 'bounded_probe') as probe:
            self.assertEqual(gate.main(['--allow-real-host']), 2)
        probe.assert_not_called()

    def test_argument_error_never_echoes_supplied_value(self):
        result = self.run_gate('--unexpected-private-value')
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('unexpected-private-value', result.stderr + result.stdout)
        self.assertNotIn('Traceback', result.stderr)

    def test_unsupported_platform_refuses_before_entry_read(self):
        with patch.object(gate.sys, 'platform', 'linux'), patch.object(gate, 'inspect_entry') as inspect:
            self.assertEqual(gate.main(['--allow-real-host', '--ccr-entry', '/synthetic/cli.js']), 2)
        inspect.assert_not_called()

    def fixture_entry(self, version='3.1.3', name='@musistudio/claude-code-router'):
        import json
        package = self.root / 'package'
        entry = package / 'dist' / 'main' / 'cli.js'
        entry.parent.mkdir(parents=True)
        entry.write_text('// synthetic fixture; never executed\n')
        (package / 'package.json').write_text(json.dumps({'name': name, 'version': version}))
        return entry

    def test_expected_package_metadata(self):
        entry = self.fixture_entry()
        self.assertEqual(gate.inspect_entry(entry), entry.resolve())

    def test_unknown_version_refused(self):
        entry = self.fixture_entry(version='9.0.0')
        with self.assertRaises(gate.GateError):
            gate.inspect_entry(entry)

    def test_wrong_package_refused(self):
        entry = self.fixture_entry(name='synthetic-other-package')
        with self.assertRaises(gate.GateError):
            gate.inspect_entry(entry)

    def test_relative_entry_refused(self):
        with self.assertRaises(gate.GateError):
            gate.inspect_entry(Path('dist/main/cli.js'))

    def test_symlink_entry_refused(self):
        entry = self.fixture_entry()
        linked = entry.with_name('linked.js')
        linked.symlink_to(entry)
        with self.assertRaises(gate.GateError):
            gate.inspect_entry(linked)

    def test_oversized_metadata_refused(self):
        entry = self.fixture_entry()
        (entry.parents[2] / 'package.json').write_bytes(b'x' * (gate.MAX_METADATA + 1))
        with self.assertRaises(gate.GateError):
            gate.inspect_entry(entry)

    def test_minimal_environment_excludes_credentials_and_overrides(self):
        with patch.dict(gate.os.environ, {'PRIVATE_API_KEY': 'synthetic-private-value',
                                         'NODE_OPTIONS': '--inspect', 'HTTP_PROXY': 'synthetic-proxy',
                                         'CCR_CONFIG_DIR': '/synthetic/other'}):
            env = gate.probe_environment(self.root)
        self.assertEqual(set(env), {'PATH', 'HOME', 'TMPDIR', 'LANG'})
        self.assertEqual(env['HOME'], str(self.root))
        self.assertEqual(env['TMPDIR'], str(self.root))

    def test_probe_success_is_not_host_success(self):
        self.assertEqual(gate.interpret_probe(0), 'capability-only')
        self.assertEqual(gate.interpret_probe(-signal.SIGABRT), 'sandbox-capability-failed')
        self.assertEqual(gate.interpret_probe(1), 'sandbox-capability-failed')

    def test_bounded_probe_caps_output_and_reaps_own_child(self):
        result = gate.bounded_probe([sys.executable, '-I', '-c',
                                     'import os,time; os.write(1,b"x"*4096); time.sleep(30)'],
                                    gate.probe_environment(self.root), self.root, timeout=2, limit=64)
        self.assertEqual(result[0], 'probe-output-limit')
        self.assertLessEqual(result[2], 65)

    def test_bounded_probe_timeout_reaps_own_child(self):
        result = gate.bounded_probe([sys.executable, '-I', '-c', 'import time; time.sleep(30)'],
                                    gate.probe_environment(self.root), self.root, timeout=0.05)
        self.assertEqual(result[0], 'probe-timeout')

    def test_probe_failure_output_not_disclosed(self):
        result = gate.bounded_probe([sys.executable, '-I', '-c',
                                     'import sys; print("synthetic-private-marker",file=sys.stderr); sys.exit(1)'],
                                    gate.probe_environment(self.root), self.root)
        self.assertEqual(result[0], 'probe-exited')
        self.assertEqual(result[1], 1)
        self.assertIsInstance(result[2], int)
        self.assertNotIn('synthetic-private-marker', repr(result))

    def test_missing_executable_is_controlled(self):
        result = gate.bounded_probe(['/synthetic/nonexistent-executable'],
                                    gate.probe_environment(self.root), self.root)
        self.assertEqual(result[0], 'probe-launch-failed')

    def test_policy_default_denies_network_and_extra_execution(self):
        policy = gate.capability_policy()
        self.assertIn('(deny default)', policy)
        self.assertNotIn('allow network', policy)
        self.assertNotIn('file-write*', policy)
        self.assertIn('(literal "/usr/bin/true")', policy)
        self.assertNotIn('(allow process-exec)', policy)
        # File writes are restricted to inherited descriptor paths (/dev/fd);
        # no disk write targets are granted.
        policy_lines = policy.splitlines()
        write_index = [i for i, line in enumerate(policy_lines) if 'file-write-data' in line]
        self.assertEqual(len(write_index), 1)
        self.assertEqual(policy_lines[write_index[0] + 1].strip(), '(subpath "/dev/fd"))')
        self.assertNotIn('(subpath "/tmp', policy)
        self.assertNotIn('home-', policy)

    def test_policy_matches_reviewed_bootstrap_rules_exactly(self):
        expected = '''
(version 3)
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
    (require-all (mac-policy-name "Sandbox") (mac-syscall-number 2)))
'''
        self.assertEqual(' '.join(gate.capability_policy().split()), ' '.join(expected.split()))

    def test_capability_failure_blocks_without_host_execution(self):
        import io
        errors = io.StringIO()
        with patch.object(gate.sys, 'platform', 'darwin'), \
             patch.object(gate, 'inspect_entry', return_value=Path('/synthetic/cli.js')), \
             patch.object(gate, 'sandbox_available', return_value=True), \
             patch.object(gate.sys, 'stderr', errors), \
             patch.object(gate, 'bounded_probe', return_value=('probe-exited', -6, 0)) as probe:
            self.assertEqual(gate.main(['--allow-real-host', '--ccr-entry', '/synthetic/cli.js']), 2)
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(probe.call_args.args[0],
                         ['/usr/bin/sandbox-exec', '-p', gate.capability_policy(), '/usr/bin/true'])
        self.assertIn('BLOCKED sandbox-capability-failed; CCR not started; no fallback', errors.getvalue())

    def test_capability_success_still_blocks_unimplemented_integration(self):
        import io
        errors = io.StringIO()
        with patch.object(gate.sys, 'platform', 'darwin'), \
             patch.object(gate, 'inspect_entry', return_value=Path('/synthetic/cli.js')), \
             patch.object(gate, 'sandbox_available', return_value=True), \
             patch.object(gate.sys, 'stderr', errors), \
             patch.object(gate, 'bounded_probe', return_value=('probe-exited', 0, 0)) as probe:
            self.assertEqual(gate.main(['--allow-real-host', '--ccr-entry', '/synthetic/cli.js']), 2)
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(probe.call_args.args[0],
                         ['/usr/bin/sandbox-exec', '-p', gate.capability_policy(), '/usr/bin/true'])
        self.assertNotIn('/synthetic/cli.js', probe.call_args.args[0])
        self.assertIn('BLOCKED integration-not-implemented; CCR not started; no fallback', errors.getvalue())

    def test_missing_sandbox_does_not_fallback(self):
        with patch.object(gate.sys, 'platform', 'darwin'), \
             patch.object(gate, 'inspect_entry', return_value=Path('/synthetic/cli.js')), \
             patch.object(gate, 'sandbox_available', return_value=False), \
             patch.object(gate, 'bounded_probe') as probe:
            self.assertEqual(gate.main(['--allow-real-host', '--ccr-entry', '/synthetic/cli.js']), 2)
        probe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
