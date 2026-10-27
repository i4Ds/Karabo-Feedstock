"""Read tools21cm release identity and check both published architectures."""
import argparse
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = {'linux-64', 'linux-aarch64'}


def release_identity(root=ROOT):
    release = json.loads((root / 'tools21cm/release.json').read_text())
    build = release['build_number']
    if type(build) is not int or build < 0:
        raise ValueError('build_number must be a non-negative integer')
    workflow = (root / '.github/workflows/build_base.yml').read_text()
    versions = re.findall(r'^  TOOLS_21CM_VERSION:\s*([0-9]+(?:\.[0-9]+){2})\s*(?:#.*)?$', workflow, re.M)
    if len(versions) != 1:
        raise ValueError('Expected exactly one TOOLS_21CM_VERSION')
    if release.get('version') != versions[0]:
        raise ValueError('release.json version must match TOOLS_21CM_VERSION')
    config = (root / 'tools21cm/conda_build_config.yaml').read_text()
    m = re.search(r'^python:\s*\n((?:[ \t]+[^\n]*\n|\n)*)', config, re.M)
    variants = re.findall(r'^\s+-\s+[\'"]?(3\.\d+)[\'"]?\s*$', m[1], re.M) if m else []
    if not variants or len(set(variants)) != len(variants):
        raise ValueError('Missing or duplicate Python variants')
    return versions[0], build, {'py' + v.replace('.', '') for v in variants}


def publication_state(files, version, build, variants):
    expected = {(platform, variant) for platform in PLATFORMS for variant in variants}
    found = set()
    for item in files:
        attrs = item.get('attrs') or {}
        if item.get('version') != version or attrs.get('build_number') != build:
            continue
        if 'main' not in item.get('labels', []):
            continue
        platform = item.get('basename', '').split('/')[0]
        variant = re.match(r'^(py\d+)(?:_|h)', attrs.get('build', ''))
        if platform in PLATFORMS and variant:
            found.add((platform, variant[1]))
    matched = expected & found
    if not matched:
        return 'missing'
    if matched == expected:
        return 'complete'
    raise ValueError(f'Partial tools21cm publication: found {sorted(matched)}; expected {sorted(expected)}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-channel', action='store_true')
    args = parser.parse_args()
    version, build, variants = release_identity()
    outputs = {'version': version, 'build_number': str(build)}
    if args.check_channel:
        channel = os.environ.get('ANACONDA_CHANNEL', 'i4ds')
        if channel.lower() != 'i4ds':
            raise ValueError('Automatic publishing is restricted to the i4ds channel')
        try:
            with urlopen('https://api.anaconda.org/package/i4ds/tools21cm', timeout=30) as response:
                files = json.load(response)['files']
        except HTTPError as error:
            if error.code != 404:
                raise
            files = []
        state = publication_state(files, version, build, variants)
        outputs['publish'] = str(state == 'missing').lower()
        print(f'tools21cm {version} build {build}: {state}')
    target = os.environ.get('GITHUB_OUTPUT')
    if target:
        with open(target, 'a') as stream:
            for key, value in outputs.items():
                stream.write(f'{key}={value}\n')
    else:
        print(json.dumps(outputs))


if __name__ == '__main__':
    main()
