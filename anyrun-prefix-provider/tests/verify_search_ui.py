#!/usr/bin/python3
"""Verify debounced search and calculator behavior in a private compositor."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

home = Path.home()
with tempfile.TemporaryDirectory(prefix='anyrun-search-ui-') as temporary:
    root = Path(temporary)
    runtime = root / 'runtime'
    runtime.mkdir(mode=0o700)
    config = root / 'config'
    config.mkdir()
    fixture = Path(__file__).with_name('search_fixture_provider.py')
    fixture.chmod(0o755)
    query_log = root / 'queries.jsonl'
    query_log.touch()
    def write_config(provider, plugins):
        (config / 'config.ron').write_text(f'''Config(
            width: Absolute(480), height: Absolute(1),
            x: Fraction(0.5), y: Fraction(0.5),
            hide_icons: true, hide_plugin_info: true,
            ignore_exclusive_zones: true, close_on_click: true,
            show_results_immediately: true, max_entries: Some(6), search_delay_ms: 150,
            provider: "{provider}", plugins: [{','.join(chr(34)+str(p)+chr(34) for p in plugins)}],
        )''')
    write_config(fixture, [])
    shutil.copy2(home / '.config/anyrun/style.css', config / 'style.css')
    sway_config = root / 'sway.conf'
    sway_config.write_text('output HEADLESS-1 mode 1280x800\nseat seat0 fallback true\nxwayland disable\nswaybg_command -\n')
    env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), XDG_STATE_HOME=str(root / 'state'),
               XDG_CACHE_HOME=str(root / 'cache'), XDG_CONFIG_HOME=str(root / 'xdg-config'),
               WLR_BACKENDS='headless', WLR_HEADLESS_OUTPUTS='1', WLR_RENDERER='pixman',
               WLR_LIBINPUT_NO_DEVICES='1', GTK_A11Y='none', GDK_BACKEND='wayland',
               GSK_RENDERER='cairo', ANYRUN_UI_CONFIG=str(config), ANYRUN_SEARCH_LOG=str(query_log))
    env.pop('WAYLAND_DISPLAY', None)
    env.pop('SWAYSOCK', None)
    env.pop('ANYRUN_UI_PREVIEW', None)
    with (root / 'sway.log').open('w+') as log:
        compositor = subprocess.Popen(['sway', '-c', str(sway_config)], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while True:
                sockets = list(runtime.glob('sway-ipc.*.sock'))
                wayland = [p for p in runtime.glob('wayland-*') if not p.name.endswith('.lock')]
                if sockets and wayland:
                    break
                if compositor.poll() is not None or time.monotonic() >= deadline:
                    log.seek(0)
                    raise RuntimeError(log.read())
                time.sleep(0.05)
            env.update(SWAYSOCK=str(sockets[0]), WAYLAND_DISPLAY=wayland[0].name)
            command = ['cargo', 'test', '--offline', '--locked', '--release', '--manifest-path',
                       str(home / '.local/src/anyrun/Cargo.toml'), '--package', 'anyrun', '--target-dir',
                       str(home / '.cache/anyrun-prefix-build')]
            subprocess.run(command + ['search_debounce_ui_flow', '--', '--ignored', '--nocapture', '--test-threads=1'], env=env, check=True, timeout=90)
            write_config(home / '.local/libexec/anyrun-prefix-provider', [home / '.config/anyrun/plugins/librink.so'])
            (config / 'rink.ron').write_text('Config(prefix: "=", pull_currencies: false)\n')
            subprocess.run(command + ['calculator_ui_flow', '--', '--ignored', '--nocapture', '--test-threads=1'], env=env, check=True, timeout=90)
            print('PASS: debounce, retained rows, complete batches, stale responses, Enter, menus and calculator')
        finally:
            if compositor.poll() is None:
                compositor.terminate()
                compositor.wait(timeout=5)
