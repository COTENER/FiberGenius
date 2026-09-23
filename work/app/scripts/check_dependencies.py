"""Read-only comparison of installed versions against Fiber Genius' pinned lock.

Does not import Django, load .env, access the database, install or download.
Run with the Python interpreter of the installation being checked.
"""
import argparse
from importlib import metadata
import json
from pathlib import Path
import re
import struct
import sys


def read_pins(path):
    pins = {}
    for number, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(
            r'([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)'
            r'(?:\s+--hash=sha256:[0-9a-fA-F]{64})+', line,
        )
        if not match:
            raise ValueError(f'Unsupported or unhashed requirement at line {number}.')
        name, version = match.groups()
        name = re.sub(r'[-_.]+', '-', name).lower()
        if name in pins:
            raise ValueError(f'Duplicate requirement at line {number}.')
        pins[name] = version
    if not pins:
        raise ValueError('The lock contains no requirements.')
    return pins


def compare_versions(pins, version_reader=None):
    version_reader = version_reader or metadata.version
    differences = []
    for name, expected in sorted(pins.items()):
        try:
            installed = version_reader(name)
        except metadata.PackageNotFoundError:
            installed = None
        if installed != expected:
            differences.append({'package': name, 'expected': expected, 'installed': installed})
    return differences


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path,
                        default=Path(__file__).resolve().parent.parent / 'requirements.lock')
    args = parser.parse_args(argv)
    try:
        pins = read_pins(args.lock)
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'error', 'detail': str(exc)}))
        return 2
    differences = compare_versions(pins)
    compatible_runtime = (sys.platform == 'win32' and sys.version_info[:2] == (3, 12)
                          and struct.calcsize('P') == 8)
    ok = compatible_runtime and not differences
    print(json.dumps({
        'status': 'ok' if ok else 'mismatch',
        'python': sys.version.split()[0], 'platform': sys.platform,
        'windows_x64_python312': compatible_runtime,
        'locked_packages': len(pins), 'differences': differences,
        'scope': 'Versions only; run pip check and functional/offline acceptance separately.',
    }, indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
