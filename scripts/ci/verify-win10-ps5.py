#!/usr/bin/env python3
"""Fail packaging if the custom executable cannot link against the 2.2.0 donor."""
import argparse
import json
from pathlib import Path
import pefile


def verify(work, dist):
    cache = (work / 'build/CMakeCache.txt').read_text()
    for feature in ['REMOTEPLAY', 'MOONLIGHT', 'XBOX', 'DEINTERLACE', 'LIBDOVI']:
        if f'VEYRA_ENABLE_{feature}:BOOL=ON' not in cache:
            raise RuntimeError(f'Required feature absent: {feature}')
    ninja = (work / 'build/build.ninja').read_text()
    if 'VEYRA_HAS_FSR=1' not in ninja or 'VEYRA_HAS_AMF' not in ninja:
        raise RuntimeError('AMD backend compile definitions missing')
    donor = Path((work / 'donor-path.txt').read_text())
    local = {p.name.lower(): p for p in donor.glob('*.dll')}
    exe = pefile.PE(str(dist / 'veyra_qml_ui.exe'))
    exports = {s.name.decode(): s for s in exe.DIRECTORY_ENTRY_EXPORT.symbols if s.name}
    if not {'D3D12SDKVersion', 'D3D12SDKPath'} <= exports.keys():
        raise RuntimeError('Agility exports missing')
    if int.from_bytes(exe.get_data(exports['D3D12SDKVersion'].address, 4), 'little') != 620:
        raise RuntimeError('Agility version does not match packaged runtime')
    checked = []
    for dependency in exe.DIRECTORY_ENTRY_IMPORT:
        name = dependency.dll.decode().lower()
        if name not in local:
            # Windows/API-set dependencies are not copied into the artifact.
            system = Path('C:/Windows/System32') / name
            if not name.startswith(('api-ms-', 'ext-ms-')) and not system.exists():
                raise RuntimeError(f'DLL absent from donor and Windows: {name}')
            continue
        dll = pefile.PE(str(local[name]))
        symbols = dll.DIRECTORY_ENTRY_EXPORT.symbols
        by_name = {s.name for s in symbols if s.name}
        by_ordinal = {s.ordinal for s in symbols}
        for entry in dependency.imports:
            if (entry.name is not None and entry.name not in by_name) or (
                entry.name is None and entry.ordinal not in by_ordinal
            ):
                raise RuntimeError(f'Missing import in official donor {name}: {entry.name or entry.ordinal}')
        checked.append(name)
        dll.close()
    exe.close()
    result = {'donor_imports_verified': sorted(checked), 'agility_sdk': 620,
              'ps5_gpu_runtime_test': 'NOT RUN: requires user hardware'}
    (dist / 'dependency-check.json').write_text(json.dumps(result, indent=2))
    # Include this final evidence file in the artifact's integrity manifest.
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location('prepare', Path(__file__).with_name('prepare-win10-ps5.py'))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest_path = dist / 'custom-build-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files']['dependency-check.json'] = module.sha(dist / 'dependency-check.json')
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--dist', type=Path, required=True)
    args = parser.parse_args()
    verify(args.work.resolve(), args.dist.resolve())
