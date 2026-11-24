#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('prepare', HERE / 'prepare-win10-ps5.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PackagingTests(unittest.TestCase):
    def test_exports(self):
        self.assertEqual(prepare.exported_names('    1    0 00001234 avcodec_version\n    2    A 00005678 avcodec_open2\n'),
                         ['avcodec_version', 'avcodec_open2'])

    def test_empty_and_duplicate_exports_fail(self):
        for text in ['', '    1 0 00001234 same\n    2 1 00005678 same\n']:
            with self.assertRaises(ValueError):
                prepare.exported_names(text)

    def test_zip_traversal_rejected(self):
        for name in ['../outside', '..\\outside', 'C:/outside', '/outside']:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                with zipfile.ZipFile(root / 'bad.zip', 'w') as z:
                    z.writestr(name, 'bad')
                with self.assertRaises(ValueError):
                    prepare.extract(root / 'bad.zip', root / 'out')

    def test_valid_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with zipfile.ZipFile(root / 'good.zip', 'w') as z:
                z.writestr('a/b.txt', 'hello')
            prepare.extract(root / 'good.zip', root / 'out')
            self.assertEqual((root / 'out/a/b.txt').read_text(), 'hello')

    def test_dependency_pins(self):
        lock = json.loads((HERE / 'win10-ps5-lock.json').read_text())
        for item in lock['archives'].values():
            self.assertRegex(item['sha256'], r'^[0-9a-f]{64}$')
        for _, commit in lock['sources'].values():
            self.assertRegex(commit, r'^[0-9a-f]{40}$')

    def test_final_shim_not_deprecated_initial_patch(self):
        source = (prepare.ROOT / 'src/gfx/FsrFgPresenter.cpp').read_text()
        self.assertIn('GetEnvironmentVariableW(L"VEYRA_NO_WIN11_SHIM"', source)
        self.assertNotIn('_wgetenv', source)


if __name__ == '__main__':
    unittest.main()
