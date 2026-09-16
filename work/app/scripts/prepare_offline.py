"""Build a Windows/Python 3.12 wheelhouse from the hash-locked requirements.

Run in a dedicated Windows build environment with approved setuptools/wheel.
Does not change the current application environment, data, or services.
"""
import argparse
from email.parser import BytesParser
import hashlib
from importlib.metadata import version
import json
import platform
from pathlib import Path
import subprocess
import sys
import zipfile


def wheel_requirement(path):
    with zipfile.ZipFile(path) as archive:
        entries = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(entries) != 1:
            raise RuntimeError(f'Invalid wheel metadata: {path.name}')
        metadata = BytesParser().parsebytes(archive.read(entries[0]))
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return f'{metadata["Name"]}=={metadata["Version"]} --hash=sha256:{digest}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='New, empty directory (must not exist)')
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12) or platform.architecture()[0] != '64bit':
        raise SystemExit('Build on Windows x64 / Python 3.12, matching production.')
    toolchain = {name: version(name) for name in ('pip', 'setuptools', 'wheel')}
    root = args.destination.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = root / 'sources'
    wheels = root / 'wheels'
    source.mkdir()
    wheels.mkdir()
    lock = Path(__file__).resolve().parent.parent / 'requirements.lock'
    base = [sys.executable, '-m', 'pip']
    subprocess.run(base + ['download', '--require-hashes', '--no-deps', '--no-build-isolation',
                          '-r', str(lock), '--dest', str(source)], check=True)
    # Sources are verified against requirements.lock before building. Native
    # source builds may require approved Windows build tools; fail, never skip.
    subprocess.run(base + ['wheel', '--no-index', '--find-links', str(source),
                          '--no-deps', '--no-build-isolation', '--require-hashes',
                          '-r', str(lock), '--wheel-dir', str(wheels)], check=True)
    requirements = sorted(wheel_requirement(path) for path in wheels.glob('*.whl'))
    if not requirements:
        raise RuntimeError('No wheels were generated.')
    (root / 'requirements.offline.lock').write_text('\n'.join(requirements) + '\n', encoding='utf-8')
    (root / 'build.json').write_text(json.dumps({
        'python': sys.version, 'platform': platform.platform(), 'build_tools': toolchain,
        'source_lock_sha256': hashlib.sha256(lock.read_bytes()).hexdigest(),
        'wheels': len(requirements),
    }, indent=2), encoding='utf-8')
    print(f'Wheelhouse prepared at {root}. A clean offline installation is still required.')


if __name__ == '__main__':
    main()
