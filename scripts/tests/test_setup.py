"""Installer contracts verified in disposable profiles, never the live session."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('config_files', REPO / 'scripts/lib/config-files.py')
files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(files)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dotfiles-setup-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home with spaces'
        self.config = self.home / '.config'
        self.data = self.home / '.local/share'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'commands.jsonl'
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.config),
                        XDG_DATA_HOME=str(self.data), XDG_CACHE_HOME=str(self.home / '.cache'),
                        PATH=str(self.bin) + ':/usr/bin:/bin', TEST_COMMAND_LOG=str(self.log))
        for key in ['SWAYSOCK', 'WAYLAND_DISPLAY', 'DISPLAY', 'ANYRUN_SYSROOT', 'CARGO_TARGET_DIR']:
            self.env.pop(key, None)
        for name in ['systemctl', 'swaymsg', 'busctl', 'gsettings', 'dbus-update-activation-environment']:
            self.mock_command(name, 77)

    def mock_command(self, name, status=0):
        path = self.bin / name
        path.write_text('#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\n'
                        'with Path(os.environ["TEST_COMMAND_LOG"]).open("a") as f:\n'
                        ' f.write(json.dumps(sys.argv) + "\\n")\n'
                        f'raise SystemExit({status})\n')
        path.chmod(0o755)

    def run_script(self, name, *args):
        result = subprocess.run(['bash', str(REPO / 'scripts' / name), *args], env=self.env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_generated_file_replaces_link_without_modifying_source(self):
        source = self.root / 'source'
        source.write_text('original')
        target = self.root / 'generated'
        target.symlink_to(source)
        self.assertTrue(files.write_config(target, b'generated', 'one'))
        self.assertEqual(source.read_text(), 'original')
        self.assertFalse(target.is_symlink())
        self.assertTrue(Path(str(target) + '.bak.one').is_symlink())
        before = target.stat().st_mtime_ns
        self.assertFalse(files.write_config(target, b'generated', 'two'))
        self.assertEqual(target.stat().st_mtime_ns, before)

    def test_include_handles_custom_path_and_is_idempotent(self):
        target = self.config / 'sway/config'
        directory = self.config / 'sway/config.d'
        self.assertTrue(files.ensure_include(target, directory, 'one'))
        self.assertIn(str(directory / '*.conf'), target.read_text())
        self.assertFalse(files.ensure_include(target, directory, 'two'))
        self.assertEqual(target.read_text().count('include '), 1)

    def test_session_files_install_without_contacting_manager(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in REPO.rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts}
        self.run_script('setup-session.sh', '--no-activate')
        self.assertTrue((self.config / 'systemd/user/sway-outputs.service').is_symlink())
        self.assertTrue((self.config / 'systemd/user/sway-logout.service').is_symlink())
        self.assertTrue(os.access(self.config / 'sway/scripts/logout.sh', os.X_OK))
        self.assertTrue((self.config / 'sway/scripts/start-session.sh').is_symlink())
        self.assertIn('Bibata-Modern-Zinc', (self.config / 'sway/config.d/57-cursor.conf').read_text())
        self.assertFalse(self.commands())
        self.run_script('setup-session.sh', '--no-activate')
        self.assertEqual(list(self.config.rglob('*.bak.*')), [])
        for path, digest in before.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, str(path))

    def test_existing_base_is_preserved_unless_replacement_selected(self):
        target = self.config / 'sway/config'
        target.parent.mkdir(parents=True)
        target.write_text('set $mod Mod1\n')
        self.run_script('setup-session.sh', '--no-activate')
        self.assertTrue(target.read_text().startswith('set $mod Mod1\n'))
        self.run_script('setup-session.sh', '--no-activate', '--replace-base')
        self.assertTrue(list(target.parent.glob('config.bak.*')))
        self.assertIn('set $mod Mod4', target.read_text())
        self.assertFalse(self.commands())

    def test_component_bootstrap_does_not_overwrite_appearance(self):
        gtk = self.config / 'gtk-3.0/settings.ini'
        gtk.parent.mkdir(parents=True)
        gtk.write_text('[Settings]\ngtk-font-name=Local Choice 12\n')
        hotkeys = self.config / 'sway/config.d/60-hotkeys.conf'
        hotkeys.parent.mkdir(parents=True)
        hotkeys.write_text('# local bindings\n')
        self.run_script('setup-session.sh', '--no-activate', '--lifecycle-only')
        self.assertEqual(gtk.read_text(), '[Settings]\ngtk-font-name=Local Choice 12\n')
        self.assertEqual(hotkeys.read_text(), '# local bindings\n')
        self.assertTrue((hotkeys.parent / '90-session.conf').is_symlink())
        self.assertFalse(self.commands())

    def test_cursor_selection_does_not_modify_templates_and_survives_reapply(self):
        for theme in ['Pixelarticons-Light', 'Pixelarticons-Dark', 'Bibata-Modern-Zinc']:
            directory = self.data / 'icons' / theme / 'cursors'
            directory.mkdir(parents=True)
            for cursor in ['left_ptr', 'hand2', 'xterm', 'left_ptr_watch', 'watch']:
                (directory / cursor).write_bytes(b'fixture')
        original = (REPO / 'gtk/settings.ini').read_bytes()
        self.run_script('setup-cursor.sh', '--skip-build', '--theme', 'Pixelarticons-Light', '--no-activate')
        for version in ['3.0', '4.0']:
            self.assertIn('Pixelarticons-Light', (self.config / f'gtk-{version}/settings.ini').read_text())
        self.run_script('setup-session.sh', '--no-activate')
        self.assertIn('Pixelarticons-Light 32', (self.config / 'sway/config.d/57-cursor.conf').read_text())
        self.assertEqual((REPO / 'gtk/settings.ini').read_bytes(), original)
        self.assertFalse(self.commands())

    def test_pixelart_build_installs_both_existing_theme_variants(self):
        cache = Path(self.env['XDG_CACHE_HOME']) / 'cursor-build'
        for theme in ['Pixelarticons-Light', 'Pixelarticons-Dark']:
            directory = cache / theme / 'cursors'
            directory.mkdir(parents=True)
            for cursor in ['left_ptr', 'hand2', 'xterm', 'left_ptr_watch', 'watch']:
                (directory / cursor).write_bytes(b'fixture')
        self.run_script('setup-cursor.sh', '--from-build', '--theme', 'Pixelarticons-Dark', '--no-activate')
        for theme in ['Pixelarticons-Light', 'Pixelarticons-Dark']:
            self.assertTrue((self.data / 'icons' / theme / 'cursors/left_ptr').exists())
        self.assertFalse(self.commands())

    def test_portal_install_reuses_build_without_rebuilding_or_activating(self):
        build = Path(self.env['XDG_CACHE_HOME']) / 'termfilechooser-build'
        for relative in ['venv/bin', 'portal-build',
                         'source-ed89c164c28f353d636a8ac68c7e35ce37f882bd', 'inih-r62']:
            (build / relative).mkdir(parents=True)
        (build / 'source-ed89c164c28f353d636a8ac68c7e35ce37f882bd/LICENSE').write_text('license')
        (build / 'inih-r62/LICENSE.txt').write_text('license')
        meson = build / 'venv/bin/meson'
        meson.write_text('#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\n'
                         'assert "--no-rebuild" in sys.argv\n'
                         'with Path(os.environ["TEST_COMMAND_LOG"]).open("a") as f:\n'
                         ' f.write(json.dumps(sys.argv) + "\\n")\n')
        meson.chmod(0o755)
        for command in ['yazi', 'foot']:
            self.mock_command(command)
        portal = self.home / '.local/libexec/xdg-desktop-portal-termfilechooser'
        wrapper = self.data / 'xdg-desktop-portal-termfilechooser/yazi-wrapper.sh'
        unit_dir = self.home / '.local/lib/systemd/user'
        unit_dir.mkdir(parents=True)
        (unit_dir / 'xdg-desktop-portal-termfilechooser.service').write_text('[Service]\nExecStart=/bin/true\n')
        for executable in [portal, wrapper]:
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text('#!/bin/sh\nexit 0\n')
            executable.chmod(0o755)
        pkg_config = self.bin / 'pkg-config'
        pkg_config.write_text('#!/usr/bin/python3\nprint(' + repr(str(unit_dir)) + ')\n')
        pkg_config.chmod(0o755)
        self.run_script('setup-filechooser.sh', '--from-build', '--no-activate')
        commands = self.commands()
        self.assertEqual(len(commands), 1)
        self.assertIn('--no-rebuild', commands[0])

    def test_launcher_build_only_uses_locked_selected_manifests(self):
        self.mock_command('cargo')
        self.mock_command('pkg-config')
        self.run_script('build-anyrun.sh')
        commands = self.commands()
        self.assertEqual([Path(c[0]).name for c in commands], ['pkg-config', 'cargo', 'cargo', 'cargo'])
        builds = commands[1:]
        self.assertTrue(all('--locked' in command for command in builds))
        self.assertEqual([Path(command[command.index('--manifest-path') + 1]).parent.name
                          for command in builds], ['anyrun', 'anyrun-prefix-provider', 'kidex'])
        self.assertFalse(self.config.exists())


if __name__ == '__main__':
    unittest.main()
