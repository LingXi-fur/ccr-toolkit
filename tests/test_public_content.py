import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_public_content.py'


class PublicContentTests(unittest.TestCase):
    def scanner(self):
        spec = importlib.util.spec_from_file_location('public_content', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_examples_allowed(self):
        module = self.scanner()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            urls = [
                'https://example.com',
                'http://127.0.0.1:4000/health',
            ]
            (root / 'README.md').write_text('\n'.join(urls))
            self.assertEqual(module.scan(root), (1, []))

    def test_secret_path_and_endpoint_report_only_locations(self):
        module = self.scanner()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'README.md').write_text('sk-' + 'X' * 40 + '\n' + '/Users/' + 'private-owner/secret\n' + 'http://' + '192.0.2.10/api\n')
            count, findings = module.scan(root)
            self.assertEqual(count, 1)
            self.assertEqual({f[2] for f in findings}, {'credential-shape', 'personal-path', 'nonpublic-endpoint'})
            self.assertNotIn('private-owner', repr(findings))

    def test_unknown_files_and_symlinks_blocked(self):
        module = self.scanner()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.env').write_text('do not read')
            (root / 'unknown.txt').write_text('not approved')
            (root / 'README.md').symlink_to(root / '.env')
            _, findings = module.scan(root)
            self.assertTrue(any(f[2] == 'unapproved-path' for f in findings))
            self.assertTrue(any(f[2] == 'symlink' for f in findings))

    def test_quoted_json_yaml_and_source_credentials_detected(self):
        module = self.scanner()
        for value in ['{"api_key":"' + 'a' * 24 + '"}',
                      "'password': '" + 'b' * 24 + "'",
                      'client_secret = "' + 'c' * 24 + '"']:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / 'README.md').write_text(value)
                _, findings = module.scan(root)
                self.assertTrue(any(f[2] == 'credential-assignment' for f in findings))

    def test_local_output_ignored_but_database_inside_source_blocked(self):
        module = self.scanner()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '.local-results').mkdir()
            (root / '.local-results/private.log').write_text('ignored local output')
            (root / 'tests').mkdir()
            (root / 'tests/private.sqlite').write_bytes(b'db')
            _, findings = module.scan(root)
            self.assertEqual([f[2] for f in findings], ['forbidden-file'])


class GitContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repository'
        self.root.mkdir()
        self.env = {'PATH': os.defpath, 'HOME': self.temp.name, 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic Tester')
        self.git('config', 'user.email', 'tester@example.invalid')
        spec = importlib.util.spec_from_file_location('public_content', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def git(self, *args, data=None):
        return subprocess.check_output(['git', '-C', str(self.root), *args], env=self.env, input=data, stderr=subprocess.PIPE)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'synthetic fixture')

    def rules(self, snapshot='index'):
        return {finding[2] for finding in self.module.scan_git(self.root, snapshot)[1]}

    def test_index_rejects_tracked_nested_ignored_directory(self):
        self.write('docs/.local-results/leak.md', 'synthetic text')
        self.git('add', '-f', 'docs/.local-results/leak.md')
        self.assertIn('forbidden-directory', self.rules())

    def test_head_rejects_tracked_nested_ignored_directory(self):
        self.write('docs/node_modules/leak.md', 'synthetic text')
        self.commit()
        self.assertIn('forbidden-directory', self.rules('head'))

    def test_index_scans_staged_blob_not_clean_working_file(self):
        path = self.write('README.md', 'sk-' + 'X' * 40)
        self.git('add', 'README.md')
        path.write_text('clean working copy')
        self.assertIn('credential-shape', self.rules())
        self.assertNotIn('credential-shape', {f[2] for f in self.module.scan(self.root)[1]})

    def test_index_ignores_unstaged_secret_but_directory_scan_finds_it(self):
        path = self.write('README.md', 'clean staged text')
        self.git('add', 'README.md')
        path.write_text('sk-' + 'X' * 40)
        self.assertEqual(self.rules(), set())
        self.assertIn('credential-shape', {f[2] for f in self.module.scan(self.root)[1]})

    def test_head_scans_committed_blob_not_index_or_worktree(self):
        path = self.write('README.md', 'sk-' + 'X' * 40)
        self.commit()
        path.write_text('clean working copy')
        self.git('add', 'README.md')
        self.assertIn('credential-shape', self.rules('head'))
        self.assertEqual(self.rules(), set())

    def test_head_missing_fails_closed(self):
        self.assertIn('git-scan-incomplete', self.rules('head'))

    def test_empty_index_fails_closed(self):
        self.assertIn('empty-manifest', self.rules())

    def test_unmerged_index_refused(self):
        oid = self.git('hash-object', '-w', '--stdin', data=b'synthetic').strip().decode()
        rows = ''.join('100644 %s %d\tREADME.md\n' % (oid, stage) for stage in (1, 2, 3))
        self.git('update-index', '--index-info', data=rows.encode())
        self.assertIn('unmerged-entry', self.rules())

    def test_symlink_refused_without_following(self):
        (self.root / 'README.md').symlink_to('outside-file')
        self.git('add', 'README.md')
        self.assertIn('symlink', self.rules())

    def test_submodule_refused(self):
        self.write('README.md', 'clean')
        self.commit()
        oid = self.git('rev-parse', 'HEAD').strip().decode()
        self.git('update-index', '--add', '--cacheinfo', '160000,%s,docs/vendor' % oid)
        self.assertIn('submodule', self.rules())

    def test_large_blob_refused_before_read(self):
        self.write('README.md', 'x' * (self.module.MAX_FILE + 1))
        self.git('add', 'README.md')
        self.assertIn('file-type-or-size', self.rules())

    def test_clean_head_and_index_pass_without_mutation(self):
        self.write('README.md', 'clean')
        self.commit()
        index = self.root / '.git/index'
        before = index.read_bytes()
        self.assertEqual(self.rules('head'), set())
        self.assertEqual(self.rules(), set())
        self.assertEqual(index.read_bytes(), before)

    def test_non_root_path_refused(self):
        path = self.root / 'docs'
        path.mkdir()
        self.assertIn('git-scan-incomplete', {f[2] for f in self.module.scan_git(path, 'index')[1]})

    def test_missing_promisor_object_never_invokes_transport(self):
        self.write('README.md', 'clean')
        self.commit()
        blob = self.git('rev-parse', 'HEAD:README.md').decode().strip()
        sentinel = Path(self.temp.name) / 'transport-invoked'
        helper = Path(self.temp.name) / 'transport-helper'
        helper.write_text('#!/bin/sh\ntouch "' + str(sentinel) + '"\nexit 1\n')
        helper.chmod(0o700)
        # Local transport sentinel only: no external network or server.
        self.git('config', 'extensions.partialClone', 'origin')
        self.git('config', 'remote.origin.promisor', 'true')
        self.git('config', 'remote.origin.url', 'ext::' + str(helper))
        self.git('config', 'protocol.ext.allow', 'always')
        (self.root / '.git/objects' / blob[:2] / blob[2:]).unlink()
        self.assertIn('git-scan-incomplete', self.rules('head'))
        self.assertFalse(sentinel.exists())

    def test_git_output_over_limit_reaps_process(self):
        fake = Path(self.temp.name) / 'fake-git'
        fake.write_text('#!' + sys.executable + '\nimport os, time\nos.write(1, b"x" * 4096)\ntime.sleep(30)\n')
        fake.chmod(0o700)
        original = subprocess.Popen
        children = []
        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        with patch.object(self.module.shutil, 'which', return_value=str(fake)), patch.object(self.module.subprocess, 'Popen', side_effect=launch):
            with self.assertRaises(ValueError):
                self.module.git_output(self.root, ['ls-files'], 64)
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_oversized_manifest_fails_closed(self):
        self.write('README.md', 'clean')
        self.git('add', 'README.md')
        with patch.object(self.module, 'MAX_MANIFEST', 1):
            self.assertIn('git-scan-incomplete', self.rules())

    def test_cli_reports_locations_not_secret_values(self):
        self.write('README.md', 'sk-' + 'X' * 40)
        self.git('add', 'README.md')
        run = subprocess.run([sys.executable, '-I', str(SCRIPT), '--root', str(self.root), '--index'],
                             env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 1)
        self.assertIn('credential-shape', run.stdout)
        self.assertNotIn('X' * 40, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
