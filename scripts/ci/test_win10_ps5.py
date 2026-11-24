#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import zipfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('prepare', HERE / 'prepare-win10-ps5.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PackagingTests(unittest.TestCase):
    def test_pe_export_table_above_default_limit(self):
        spec = importlib.util.spec_from_file_location('verify', HERE / 'verify-win10-ps5.py')
        verify = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verify)
        image = MagicMock()
        image.DIRECTORY_ENTRY_EXPORT.struct.NumberOfNames = 9525
        image.DIRECTORY_ENTRY_EXPORT.symbols = [MagicMock(name='export') for _ in range(9525)]
        with patch.object(verify.pefile, 'PE', return_value=image) as constructor:
            self.assertIs(verify.read_pe('Qt6Quick.dll'), image)
            self.assertGreaterEqual(constructor.call_args.kwargs['max_symbol_exports'], 9525)
            image.DIRECTORY_ENTRY_EXPORT.struct.NumberOfNames = 9526
            with self.assertRaises(RuntimeError):
                verify.read_pe('Qt6Quick.dll')

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

    def test_ffmpeg_avutil_compatibility_header(self):
        with tempfile.TemporaryDirectory() as temp:
            inc = Path(temp)
            (inc / 'version_major.h').write_text('#include "version.h"\n')
            (inc / 'version.h').write_text('#define LIBAVUTIL_VERSION_MAJOR 61\n')
            prepare.verify_ffmpeg_major(inc, 'avutil', 61)
            with self.assertRaises(RuntimeError):
                prepare.verify_ffmpeg_major(inc, 'avutil', 60)
            (inc / 'version_major.h').write_text('#define LIBAVUTIL_VERSION_MAJOR 60\n')
            with self.assertRaises(RuntimeError):
                prepare.verify_ffmpeg_major(inc, 'avutil', 61)

    def test_ffmpeg_split_version_header(self):
        with tempfile.TemporaryDirectory() as temp:
            inc = Path(temp)
            (inc / 'version.h').write_text('#include "version_major.h"\n')
            (inc / 'version_major.h').write_text('#define LIBAVCODEC_VERSION_MAJOR 63\n')
            prepare.verify_ffmpeg_major(inc, 'avcodec', 63)

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
