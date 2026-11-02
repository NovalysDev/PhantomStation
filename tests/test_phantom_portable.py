import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phantom_portable import configure_portable_tools, local_ps1_dependencies, stage_ps1_archives


class PortableToolsTest(unittest.TestCase):
    def test_missing_repositories_leave_environment_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = {'PATH': 'unchanged'}
            self.assertIsNone(local_ps1_dependencies(temporary, environment))
            self.assertEqual(environment, {'PATH': 'unchanged'})

    def test_local_repositories_extend_existing_git_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('psxrecomp', 'recomp-ui', 'recomp-net', 'rbengine'):
                repository = root / 'outils' / 'dependances-git' / (name + '.git')
                repository.mkdir(parents=True)
                (repository / 'HEAD').write_text('ref: refs/heads/main\n')
            environment = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.test',
                           'GIT_CONFIG_VALUE_0': 'unchanged'}
            repositories = local_ps1_dependencies(root, environment)
            self.assertEqual(environment['GIT_CONFIG_COUNT'], '4')
            self.assertEqual(environment['GIT_CONFIG_VALUE_0'], 'unchanged')
            self.assertEqual(environment['GIT_CONFIG_VALUE_1'], 'always')
            self.assertIn(repositories['recomp-net'].as_uri(), environment['GIT_CONFIG_KEY_2'])

    def test_git_portable_is_preferred_without_modifying_absent_installations(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'PATH': 'original'}):
            root = Path(temporary)
            configure_portable_tools(root)
            self.assertEqual(os.environ['PATH'], 'original')
            git = root / 'outils/git-portable/cmd/git.exe'
            git.parent.mkdir(parents=True)
            git.touch()
            configure_portable_tools(root)
            self.assertEqual(os.environ['PATH'].split(os.pathsep)[0], str(git.parent))

    def test_modified_webp_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'psxrecomp/third_party/libwebp-4fa21912338357f89e4fd51cf2368325b59e9bd9.tar.gz'
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b'not the expected archive')
            with self.assertRaises(ValueError):
                stage_ps1_archives(root, root / 'project')
            self.assertFalse((root / 'project').exists())


if __name__ == '__main__':
    unittest.main()
