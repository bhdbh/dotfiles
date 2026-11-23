#!/usr/bin/env python3
"""Install generated configuration atomically without following a target symlink."""
import os
from pathlib import Path
import shutil
import shlex
import stat
import sys
import tempfile


def write_config(path, content, suffix):
    path = Path(path)
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() and not path.is_symlink() else 0o644
    if path.exists() or path.is_symlink():
        backup = Path(str(path) + '.bak.' + suffix)
        number = 0
        while backup.exists() or backup.is_symlink():
            number += 1
            backup = Path(str(path) + '.bak.' + suffix + '.' + str(number))
        shutil.copy2(path, backup, follow_symlinks=False)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def ensure_include(path, directory, suffix):
    path, directory = Path(path), Path(directory)
    text = path.read_text() if path.exists() else ''
    wanted = str(directory / '*.conf')
    for line in text.splitlines():
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            continue
        if len(tokens) == 2 and tokens[0] == 'include':
            # Recognize the repository base's POSIX fallback without evaluating shell code.
            pattern = tokens[1].replace('${XDG_CONFIG_HOME:-$HOME/.config}',
                                        os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config'))
            expanded = os.path.expandvars(os.path.expanduser(pattern))
            if expanded == wanted:
                return False
    quoted = wanted.replace('\\', '\\\\').replace('"', '\\"')
    content = text.rstrip() + '\ninclude "' + quoted + '"\n'
    return write_config(path, content.encode(), suffix)


def cursor_settings(repo, config_dir, data_dir, home_dir, suffix, theme=None, size=None):
    repo, config_dir, data_dir, home_dir = map(Path, (repo, config_dir, data_dir, home_dir))
    if theme is None:
        source = config_dir / 'environment.d/91-cursor.conf'
        if not source.exists():
            source = repo / 'cursors/environment.conf'
        values = dict(line.split('=', 1) for line in source.read_text().splitlines()
                      if line and not line.lstrip().startswith('#') and '=' in line)
        theme, size = values.get('XCURSOR_THEME'), values.get('XCURSOR_SIZE')
    sizes = {'Bibata-Modern-Zinc': '24', 'Pixelarticons-Light': '32', 'Pixelarticons-Dark': '32'}
    if theme not in sizes or str(size) != sizes[theme]:
        raise ValueError('Unsupported cursor theme or size')
    gtk_text = (repo / 'gtk/settings.ini').read_text().rstrip() + '\n'
    gtk_text += f'gtk-cursor-theme-name={theme}\ngtk-cursor-theme-size={size}\n'
    settings = {
        config_dir / 'environment.d/91-cursor.conf': f'XCURSOR_THEME={theme}\nXCURSOR_SIZE={size}\n',
        config_dir / 'gtk-3.0/settings.ini': gtk_text,
        config_dir / 'gtk-4.0/settings.ini': gtk_text,
        config_dir / 'sway/config.d/57-cursor.conf': f'seat * xcursor_theme {theme} {size}\n',
        data_dir / 'icons/default/index.theme': f'[Icon Theme]\nName=Default\nInherits={theme}\n',
        home_dir / '.icons/default/index.theme': f'[Icon Theme]\nName=Default\nInherits={theme}\n',
    }
    for target, content in settings.items():
        write_config(target, content.encode(), suffix)


if __name__ == '__main__':
    if sys.argv[1] == 'write':
        write_config(sys.argv[2], sys.stdin.buffer.read(), sys.argv[3])
    elif sys.argv[1] == 'include':
        ensure_include(*sys.argv[2:])
    elif sys.argv[1] == 'cursor':
        cursor_settings(*sys.argv[2:])
    else:
        raise SystemExit('Expected write, include or cursor')
