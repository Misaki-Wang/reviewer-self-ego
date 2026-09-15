"""Shared policy regressions; fixtures only and no Codex/model invocation."""
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from utility.codex import codex_isolation as isolation


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / 'work'
        self.work.mkdir()
        self.pdfs = [self.root / 'one.pdf', self.root / 'two.pdf']
        for path in self.pdfs:
            path.write_bytes(b'%PDF-synthetic')
        self.third = self.root / 'third.pdf'
        self.third.write_bytes(b'%PDF-unprovided')
        self.skill = self.root / 'skill/SKILL.md'
        self.skill.parent.mkdir()
        self.skill.write_text('Synthetic skill')
        self.codex = self.root / 'fake-codex'
        self.codex.write_text('#!/bin/sh\nexit 0\n')
        self.codex.chmod(0o700)

    def options(self, files=None, **extra):
        return isolation.isolation_options(self.pdfs if files is None else files, self.work,
            codex=str(self.codex), pdf_skill=self.skill, **extra)

    def settings(self, flags):
        return {flags[i + 1].split('=', 1)[0]: flags[i + 1].split('=', 1)[1]
                for i, value in enumerate(flags[:-1]) if value == '-c'}

    def test_only_selected_files_and_private_work_are_granted(self):
        flags, _ = self.options()
        settings = self.settings(flags)
        fs = tomllib.loads('value=' + settings['permissions.isolated_files.filesystem'])['value']
        self.assertEqual(fs[':root'], 'deny')
        self.assertEqual(fs[str(self.work)], 'write')
        self.assertEqual([name for name, access in fs.items() if access == 'write'], [str(self.work)])
        for path in self.pdfs:
            self.assertEqual(fs[str(path)], 'read')
        for path in (self.third, self.root, Path.home()):
            self.assertNotIn(str(path), fs)
        self.assertEqual(fs['/tmp'], 'deny')
        self.assertEqual(fs['/private/tmp'], 'deny')
        self.assertEqual(settings['web_search'], '"disabled"')
        self.assertEqual(settings['permissions.isolated_files.network.enabled'], 'false')

    def test_all_alternative_tool_channels_and_ambient_shell_are_disabled(self):
        flags, env = self.options(base_env={'CODEX_THREAD_ID': 'parent', 'CODEX_SESSION_ID': 'parent',
                                          'CODEX_HOME': '/synthetic/client-state'})
        settings = self.settings(flags)
        for name in ('apps', 'plugins', 'browser_use', 'computer_use', 'multi_agent', 'memories',
                     'workspace_dependencies', 'hooks', 'shell_snapshot'):
            self.assertEqual(settings['features.' + name], 'false')
        self.assertEqual(settings['mcp_servers'], '{}')
        self.assertEqual(settings['shell_environment_policy.inherit'], '"none"')
        self.assertEqual(settings['allow_login_shell'], 'false')
        self.assertEqual(settings['approval_policy'], '"never"')
        self.assertIn('--strict-config', flags)
        self.assertNotIn('--sandbox', flags)
        self.assertEqual(env['TMPDIR'], str(self.work))
        self.assertEqual(env['TMPPREFIX'], str(self.work / 'zsh'))
        self.assertNotIn('CODEX_THREAD_ID', env)
        self.assertNotIn('CODEX_SESSION_ID', env)
        self.assertEqual(env['CODEX_HOME'], '/synthetic/client-state')

    def test_directories_missing_files_and_empty_lists_are_rejected(self):
        for files in ([], [self.root], [self.root / 'absent.pdf'], str(self.pdfs[0])):
            with self.subTest(files=files), self.assertRaises(ValueError):
                self.options(files)

    def test_image_attachments_must_belong_to_this_call(self):
        self.options(image_files=[self.pdfs[0]])
        with self.assertRaisesRegex(ValueError, 'Image attachment'):
            self.options(image_files=[self.third])
        link = self.work / 'provided-image'
        link.symlink_to(self.pdfs[0])
        self.options(image_files=[link])
        link.unlink()
        link.symlink_to(self.third)
        with self.assertRaisesRegex(ValueError, 'Image attachment'):
            self.options(image_files=[link])

    def test_read_only_material_inside_work_overrides_parent_write(self):
        inside = self.work / 'provided.pdf'
        inside.write_bytes(b'%PDF-fixture')
        flags, _ = self.options([inside])
        fs = tomllib.loads('x=' + self.settings(flags)['permissions.isolated_files.filesystem'])['x']
        self.assertEqual(fs[str(inside)], 'read')
        self.assertEqual(fs[str(self.work)], 'write')

    def test_broad_runtime_overlap_is_rejected(self):
        broad_skill = self.root / 'SKILL.md'
        broad_skill.write_text('Do not grant the containing experiment directory')
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            isolation.isolation_options(self.pdfs, self.work, codex=str(self.codex), pdf_skill=broad_skill)

    def test_shared_temporary_work_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='isolation-negative-', dir='/tmp') as work:
            with self.assertRaisesRegex(ValueError, 'shared temporary'):
                isolation.isolation_options(self.pdfs, Path(work), codex=str(self.codex), pdf_skill=self.skill)

    def test_default_runtime_is_under_utility_in_both_helper_layouts(self):
        runtime = self.root / 'utility/.runtime'
        runtime.mkdir(parents=True)
        for layout in ('utility', 'utility/codex'):
            with self.subTest(layout=layout), patch.object(
                    isolation, '__file__', str(self.root / layout / 'codex_isolation.py')):
                self.assertEqual(isolation.default_tool_runtime(), runtime)

    def test_missing_cli_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'Codex executable'):
            isolation.isolation_options(self.pdfs, self.work,
                codex=str(self.root / 'missing-codex'), pdf_skill=self.skill)


if __name__ == '__main__':
    unittest.main()
