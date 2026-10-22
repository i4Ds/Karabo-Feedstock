import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('aotools_release', Path(__file__).parents[1] / 'aotools_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def artifact(py, build=0, version='1.0.8', label='main', platform='linux-64'):
    return {'version': version, 'basename': platform + '/aotools.conda',
            'attrs': {'build_number': build, 'build': py + 'h123_0'}, 'labels': [label]}


class ReleaseTests(unittest.TestCase):
    def test_complete_release_is_skipped(self):
        self.assertEqual(release.publication_state([artifact(p) for p in ['py310','py311','py312']], '1.0.8', 0, {'py310','py311','py312'}), 'complete')

    def test_partial_release_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'Partially'):
            release.publication_state([artifact('py312')], '1.0.8', 0, {'py310','py311','py312'})

    def test_wrong_version_build_channel_and_platform_do_not_count(self):
        files = [artifact('py312', version='1.0.7'), artifact('py312',build=1), artifact('py312',label='dev'), artifact('py312',platform='linux-aarch64')]
        self.assertEqual(release.publication_state(files,'1.0.8',0,{'py312'}),'missing')

    def test_repository_identity_and_variants(self):
        version, build, variants = release.release_identity(Path(__file__).resolve().parents[3])
        self.assertEqual(variants, {'py310','py311','py312'})
        self.assertGreaterEqual(build,0)
        self.assertRegex(version,r'^\d+\.\d+\.\d+$')

    def test_invalid_build_number_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'aotools').mkdir()
            (root/'aotools/release.json').write_text('{"build_number": true}')
            with self.assertRaisesRegex(ValueError,'non-negative'):
                release.release_identity(root)
