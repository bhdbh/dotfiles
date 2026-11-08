#!/usr/bin/env python3
"""Preview user dotfiles; use --apply to copy them and --force to replace files.

Build components first with ./build.sh. This does not install system services,
change the login shell, or enable user services. @HOME@ and @RUNTIME_DIR@ in the
templates are rendered for the destination user.
"""
import argparse
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home(), help='Destination home directory')
    parser.add_argument('--runtime-dir', type=Path, help='MPD Unix socket runtime directory')
    parser.add_argument('--apply', action='store_true', help='Write files; default only previews')
    parser.add_argument('--force', action='store_true', help='Allow replacing existing files')
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    runtime = args.runtime_dir or Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    entries = {p.relative_to(ROOT / 'home'): p for p in (ROOT / 'home').rglob('*')
               if p.is_symlink() or p.is_file()}
    for p in (ROOT / 'waybar/scripts').iterdir():
        if p.is_file() and p.suffix in {'.py', '.ini', '.c', '.h'}:
            entries[Path('.config/waybar/scripts') / p.name] = p
    for p in (ROOT / '.build/waybar').glob('*.so'):
        entries[Path('.config/waybar/scripts') / p.name] = p
    for name in ['anyrun', 'kidex', 'kidex-client']:
        p = ROOT / '.build/cargo/release' / name
        if p.is_file():
            entries[Path('.cargo/bin') / name] = p
    provider = ROOT / '.build/cargo/release/anyrun-provider'
    if provider.is_file():
        entries[Path('.local/libexec/anyrun-prefix-provider')] = provider
    for name in ['applications', 'shell', 'rink', 'kidex', 'actions']:
        p = ROOT / '.build/cargo/release' / f'lib{name}.so'
        if p.is_file():
            entries[Path('.config/anyrun/plugins') / p.name] = p
    sway = ROOT / '.build/sway/sway/sway'
    if sway.is_file():
        entries[Path('.local/bin/sway-native')] = sway
    for relative in entries:
        target = home / relative
        if not target.parent.resolve().is_relative_to(home):
            raise SystemExit(f'Destination escapes home through a symlink: {relative}')
    existing = [p for p in entries if (home / p).exists() or (home / p).is_symlink()]
    print(f'{len(entries)} files; {len(existing)} existing destinations; home: {home}')
    if not args.apply:
        print('Preview only. Use --apply to copy; --force permits replacing existing files.')
        return
    if existing and not args.force:
        raise SystemExit('Existing files found. Review the destination before using --force.')
    for relative, source in sorted(entries.items()):
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        if source.is_symlink():
            if target.exists():
                target.unlink()
            target.symlink_to(source.readlink())
            continue
        data = source.read_bytes()
        try:
            data = (data.decode('utf-8').replace('@HOME@', str(home))
                    .replace('@RUNTIME_DIR@', str(runtime)).encode('utf-8'))
        except UnicodeError:
            pass
        target.write_bytes(data)
        shutil.copymode(source, target)
    print('User dotfiles copied. Build outputs were included where available.')


if __name__ == '__main__':
    main()
