import importlib.util
from pathlib import Path
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()
