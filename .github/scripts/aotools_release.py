"""Read AOtools release identity and check public Anaconda publication state."""
import argparse
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import urlopen


def release_identity(root):
    config = json.loads((root / 'aotools/release.json').read_text())
    build = config['build_number']
    if type(build) is not int or build < 0:
        raise ValueError('build_number must be a non-negative integer')
    workflow = (root / '.github/workflows/build_base.yml').read_text()
    version = re.search(r'^  AOTOOLS_VERSION: ([0-9]+\.[0-9]+\.[0-9]+)\s*$', workflow, re.M)
    if not version:
        raise ValueError('Expected one stable AOTOOLS_VERSION in build_base.yml')
    variants = (root / 'aotools/conda_build_config.yaml').read_text()
    python_block = re.search(r'^python:\s*\n((?:[ \t]+.*\n|\n)*)', variants, re.M)
    if not python_block:
        raise ValueError('Missing Python build variants')
    pythons = re.findall(r'^\s+-\s+[\'"]?(3\.\d+)[\'"]?\s*$', python_block[1], re.M)
    if not pythons:
        raise ValueError('No Python build variants found')
    return version[1], build, {'py' + p.replace('.', '') for p in pythons}


def publication_state(files, version, build, variants):
    found = set()
    for item in files:
        attrs = item.get('attrs', {})
        if item.get('version') != version or attrs.get('build_number') != build:
            continue
        if not item.get('basename', '').startswith('linux-64/'):
            continue
        if 'main' not in item.get('labels', []):
            continue
        match = re.match(r'(py\d+)(?:_|h)', attrs.get('build', ''))
        if match:
            found.add(match[1])
    if not found:
        return 'missing'
    if variants <= found:
        return 'complete'
    raise ValueError('Partially published AOtools build; inspect Anaconda before retrying: ' + ', '.join(sorted(found)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-channel', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    version, build, variants = release_identity(root)
    outputs = {'version': version, 'build_number': str(build)}
    if args.check_channel:
        channel = os.environ.get('ANACONDA_CHANNEL', 'i4ds')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', channel):
            raise ValueError('Invalid Anaconda channel')
        try:
            with urlopen(f'https://api.anaconda.org/package/{channel}/aotools', timeout=30) as response:
                files = json.load(response)['files']
        except HTTPError as error:
            if error.code != 404:
                raise
            files = []
        state = publication_state(files, version, build, variants)
        outputs['publish'] = str(state == 'missing').lower()
        print(f'AOtools {version} build {build}: {state}')
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a') as stream:
                stream.write(f'AOtools {version}, build {build}: **{state}**.\n')
    target = os.environ.get('GITHUB_OUTPUT')
    if target:
        with open(target, 'a') as stream:
            for key, value in outputs.items():
                stream.write(f'{key}={value}\n')
    else:
        print(json.dumps(outputs))


if __name__ == '__main__':
    main()
