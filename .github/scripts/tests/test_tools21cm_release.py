import importlib.util
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'tools21cm_release.py'
spec = importlib.util.spec_from_file_location('tools21cm_release', PATH)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def artifact(platform, variant='py312', version='2.4.3', build=0, labels=None):
    return {'basename': f'{platform}/tools21cm-file.conda', 'version': version,
            'attrs': {'build_number': build, 'build': f'{variant}h123_{build}'},
            'labels': ['main'] if labels is None else labels}


class ReleaseTests(unittest.TestCase):
    def test_missing_and_complete(self):
        self.assertEqual(release.publication_state([], '2.4.3', 0, {'py312'}), 'missing')
        files = [artifact(p) for p in release.PLATFORMS]
        self.assertEqual(release.publication_state(files, '2.4.3', 0, {'py312'}), 'complete')

    def test_partial_fails(self):
        with self.assertRaisesRegex(ValueError, 'Partial'):
            release.publication_state([artifact('linux-64')], '2.4.3', 0, {'py312'})

    def test_unrelated_files_do_not_count(self):
        files = [artifact('linux-64', version='2.4.2'), artifact('linux-aarch64', build=1),
                 artifact('linux-64', labels=['dev']), artifact('osx-64'), artifact('linux-64', 'py311')]
        self.assertEqual(release.publication_state(files, '2.4.3', 0, {'py312'}), 'missing')

    def test_repository_identity(self):
        self.assertEqual(release.release_identity(), ('2.4.3', 0, {'py312'}))

    def test_invalid_build_and_version_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tools21cm').mkdir()
            (root / '.github/workflows').mkdir(parents=True)
            (root / '.github/workflows/build_base.yml').write_text('  TOOLS_21CM_VERSION: 2.4.3\n')
            (root / 'tools21cm/conda_build_config.yaml').write_text('python:\n  - 3.12\n')
            for config in ['{"version":"2.4.3", "build_number":true}', '{"version":"2.4.2", "build_number":0}']:
                (root / 'tools21cm/release.json').write_text(config)
                with self.assertRaises(ValueError):
                    release.release_identity(root)


if __name__ == '__main__':
    unittest.main()
