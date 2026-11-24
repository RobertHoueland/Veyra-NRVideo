#!/usr/bin/env python3
"""Stage pinned public sources and exact official-runtime import libraries.

No DLL is patched. FFmpeg's public headers come from its corresponding source;
configure generates avconfig.h, without rebuilding/replacing the shipped codec.
This preserves the official PS5 slices256/dav1d/zlib/deinterlace runtime.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
LOCK = json.loads((Path(__file__).with_name('win10-ps5-lock.json')).read_text())


def run(*args, cwd=None, env=None):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True, timeout=900)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def extract(archive, dest):
    dest = Path(dest).resolve()
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            # Reject traversal and symlink entries, including Windows separators.
            rel = Path(item.filename.replace('\\', '/'))
            if rel.is_absolute() or ':' in item.filename or '..' in rel.parts:
                raise ValueError(f'Unsafe ZIP path: {item.filename}')
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f'Symlink in ZIP: {item.filename}')
            if not (dest / rel).resolve().is_relative_to(dest):
                raise ValueError(item.filename)
        z.extractall(dest)


def download(url, path, expected=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        partial = path.with_suffix(path.suffix + '.partial')
        with urllib.request.urlopen(url, timeout=120) as src, partial.open('wb') as out:
            shutil.copyfileobj(src, out)
        partial.replace(path)
    if expected and sha(path) != expected:
        raise RuntimeError(f'SHA256 mismatch: {path}; remove this download before retrying')


def checkout(repo, commit, target, sparse=(), recursive=False):
    target = Path(target)
    if not (target / '.git').exists():
        run('git', 'init', target)
        run('git', '-C', target, 'remote', 'add', 'origin', 'https://github.com/' + repo + '.git')
        run('git', '-C', target, 'config', 'remote.origin.promisor', 'true')
        run('git', '-C', target, 'config', 'remote.origin.partialclonefilter', 'blob:none')
        if sparse:
            run('git', '-C', target, 'sparse-checkout', 'set', *sparse)
    run('git', '-C', target, 'fetch', '--depth', '1', '--filter=blob:none', 'origin', commit)
    run('git', '-C', target, 'checkout', '--detach', commit)
    actual = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != commit:
        raise RuntimeError(f'Source pin mismatch: {repo}')
    if recursive:
        run('git', '-C', target, 'submodule', 'update', '--init', '--recursive', '--jobs', '2')


def exported_names(text):
    names = []
    for line in text.splitlines():
        match = re.match(r'^\s+\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]{8,16}\s+(\S+)', line)
        if match:
            name = match.group(1)
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name):
                raise ValueError(f'Unexpected C export: {name}')
            names.append(name)
    if not names or len(names) != len(set(names)):
        raise ValueError('Missing or duplicate DLL exports')
    return names


def import_lib(dll, output):
    text = subprocess.check_output(['dumpbin', '/nologo', '/exports', str(dll)], text=True)
    names = exported_names(text)
    definition = Path(output).with_suffix('.def')
    definition.parent.mkdir(parents=True, exist_ok=True)
    definition.write_text('LIBRARY ' + dll.name + '\nEXPORTS\n' + '\n'.join(names) + '\n')
    run('lib', '/nologo', '/machine:x64', '/def:' + str(definition), '/out:' + str(output))


def posix(path):
    path = Path(path).resolve().as_posix()
    return '/' + path[0].lower() + path[2:] if len(path) > 1 and path[1] == ':' else path


def stage_sources(work):
    sparse = {'ffx1': ['sdk'], 'ffx2': ['Kits/FidelityFX'], 'amf': ['amf/public/include'],
              'dlss': ['include', 'lib'], 'nvenc': ['include'], 'sdl': []}
    for key, (repo, commit) in LOCK['sources'].items():
        checkout(repo, commit, work / key, sparse[key])
    for kind, config in LOCK['archives'].items():
        archive = work / 'downloads' / config['url'].rsplit('/', 1)[1]
        download(config['url'], archive, config['sha256'])
        target = work / kind
        stamp = target / '.verified-archive'
        if not stamp.exists():
            extract(archive, target)
            stamp.write_text(config['sha256'])
        elif stamp.read_text() != config['sha256']:
            raise RuntimeError(f'Stale archive stage: {target}')
    rp = json.loads((ROOT / 'scripts/remoteplay/dependency-lock.json').read_text())
    checkout(rp['chiaki_repository'].removeprefix('https://github.com/').removesuffix('.git'),
             rp['chiaki_commit'], work / 'chiaki-clean', recursive=True)
    ml = json.loads((ROOT / 'scripts/moonlight/dependency-lock.json').read_text())['moonlight_common_c']
    checkout(ml['repository'].removeprefix('https://github.com/').removesuffix('.git'),
             ml['commit'], work / 'moonlight', recursive=True)


def stage_runtime_headers(work):
    donors = list((work / 'donor').rglob('versions/2.2.0/veyra_qml_ui.exe'))
    if len(donors) != 1:
        raise RuntimeError(f'Expected one 2.2.0 donor application, found {len(donors)}')
    donor = donors[0].parent
    prefix = work / 'ffmpeg-prefix'
    source = work / 'ffmpeg/ffmpeg-patched'
    assert '#define MAX_SLICES 256' in (source / 'libavcodec/h264dec.h').read_text()
    # Generate architecture public headers with the actual Windows compiler.
    # Configuration only; codec DLLs remain the exact signed-release donor bytes.
    bash = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe'
    cl = Path(shutil.which('cl.exe')).parent
    command = 'export PATH=' + shlex.quote(posix(cl)) + ':"$PATH"; ' + shlex.quote(posix(source / 'configure'))
    command += ' --toolchain=msvc --arch=x86_64 --disable-x86asm --disable-everything --disable-autodetect --disable-programs --disable-doc'
    run(bash, '-c', command, cwd=source)
    assert (source / 'libavutil/avconfig.h').is_file()
    libs = {'avcodec': 63, 'avformat': 63, 'avutil': 61, 'avfilter': 12,
            'swscale': 10, 'swresample': 7}
    for lib, major in libs.items():
        inc = prefix / 'include' / ('lib' + lib)
        inc.mkdir(parents=True, exist_ok=True)
        for header in (source / ('lib' + lib)).glob('*.h'):
            shutil.copy2(header, inc / header.name)
        version = inc / 'version_major.h'
        if not version.exists():
            version = inc / 'version.h'
        if not re.search(r'#define\s+LIB' + lib.upper() + r'_VERSION_MAJOR\s+' + str(major) + r'\b', version.read_text()):
            raise RuntimeError(f'Unexpected FFmpeg ABI for {lib}')
        import_lib(donor / f'{lib}-{major}.dll', prefix / 'lib' / f'{lib}.lib')
    module = prefix / 'share/ffmpeg/FindFFMPEG.cmake'
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text('set(FFMPEG_FOUND TRUE)\nset(FFMPEG_INCLUDE_DIRS "${VEYRA_FFMPEG_ROOT}/include")\n'
                      'set(FFMPEG_LIBRARY_DIRS "${VEYRA_FFMPEG_ROOT}/lib")\nset(FFMPEG_LIBRARIES\n' +
                      ''.join('  "${VEYRA_FFMPEG_ROOT}/lib/' + name + '.lib"\n' for name in libs) + ')\n')
    dovi = work / 'dovi-prefix'
    header = dovi / 'include/libdovi/rpu_parser.h'
    header.parent.mkdir(parents=True, exist_ok=True)
    dovi_source = work / 'remoteplay_dovi/dovi_tool/dolby_vision'
    run('cbindgen', '--config', dovi_source / 'cbindgen.toml', '--crate', 'dolby_vision',
        '--output', header, dovi_source)
    if 'dovi_parse_rpu' not in header.read_text():
        raise RuntimeError('libdovi C API missing from generated header')
    (dovi / 'bin').mkdir(exist_ok=True)
    shutil.copy2(donor / 'dolby_vision.dll', dovi / 'bin/dolby_vision.dll')
    import_lib(donor / 'dolby_vision.dll', dovi / 'lib/dolby_vision.dll.lib')
    (work / 'donor-path.txt').write_text(str(donor))


def package(work, build, output):
    if output.exists():
        raise RuntimeError('Refusing to mix an artifact with existing output')
    output.mkdir(parents=True)
    shutil.copy2(build / 'veyra_qml_ui.exe', output)
    shutil.copytree(ROOT / 'qml/Veyra', output / 'qml/Veyra')
    shutil.copytree(ROOT / 'i18n', output / 'i18n')
    shutil.copytree(build / 'shaders', output / 'shaders')
    if not list((output / 'shaders').rglob('*.dxil')):
        raise RuntimeError('No compiled shaders')
    nuget = work / 'downloads/agility.1.620.2.zip'
    download('https://api.nuget.org/v3-flatcontainer/microsoft.direct3d.d3d12/1.620.2/microsoft.direct3d.d3d12.1.620.2.nupkg', nuget)
    with zipfile.ZipFile(nuget) as z:
        (output / 'agility').mkdir()
        (output / 'agility/D3D12Core.dll').write_bytes(z.read('build/native/bin/x64/D3D12Core.dll'))
    shutil.copy2(ROOT / 'docs/WIN10_FSR4_PS5_BUILD.md', output / 'INSTALL-AND-LIMITATIONS.md')
    shutil.copy2(ROOT / 'LICENSE', output / 'SOURCE-LICENSE.txt')
    shutil.copy2(Path(__file__).with_name('win10-ps5-lock.json'), output)
    # Exact source and patches are available at this commit, including Chiaki lock.
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    manifest = {'commit': commit, 'repository': os.environ.get('GITHUB_REPOSITORY'),
                'run': os.environ.get('GITHUB_RUN_ID'), 'base': LOCK['base_release'],
                'gpu_validation': 'NOT RUN', 'ps5_console_validation': 'NOT RUN',
                'files': {str(p.relative_to(output)).replace('\\', '/'): sha(p)
                          for p in sorted(output.rglob('*')) if p.is_file()}}
    (output / 'custom-build-manifest.json').write_text(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('phase', choices=['sources', 'headers', 'package'])
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--build', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if a.phase == 'sources':
        stage_sources(work)
    elif a.phase == 'headers':
        stage_runtime_headers(work)
    else:
        package(work, a.build.resolve(), a.output.resolve())
