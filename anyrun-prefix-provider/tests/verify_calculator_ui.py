#!/usr/bin/python3
"""Run the real calculator UI in a private headless Sway compositor."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

base = Path.home()
preview = base / '.local/state/anyrun/previews/calculator-field'
preview.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix='anyrun-calculator-ui-') as temporary:
    root = Path(temporary)
    runtime = root / 'runtime'
    runtime.mkdir(mode=0o700)
    config = root / 'config'
    config.mkdir()
    (config / 'config.ron').write_text(f'''Config(
    width: Absolute(480), height: Absolute(1),
    x: Fraction(0.5), y: Fraction(0.5),
    hide_icons: true, hide_plugin_info: true,
    ignore_exclusive_zones: true, close_on_click: true,
    show_results_immediately: true, max_entries: Some(6),
    provider: "{base}/.cache/anyrun-prefix-build/release/anyrun-provider",
    plugins: ["{base}/.config/anyrun/plugins/librink.so"],
)''')
    (config / 'rink.ron').write_text('Config(prefix: "=", pull_currencies: false)\n')
    shutil.copy2(base / '.config/anyrun/style.css', config / 'style.css')
    sway_config = root / 'sway.conf'
    sway_config.write_text('output HEADLESS-1 mode 1280x800\nseat seat0 fallback true\nxwayland disable\nswaybg_command -\n')
    environment = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), XDG_STATE_HOME=str(root / 'state'),
                       XDG_CACHE_HOME=str(root / 'cache'), XDG_CONFIG_HOME=str(root / 'xdg-config'),
                       WLR_BACKENDS='headless', WLR_HEADLESS_OUTPUTS='1', WLR_RENDERER='pixman',
                       WLR_LIBINPUT_NO_DEVICES='1', GTK_A11Y='none', GDK_BACKEND='wayland', GSK_RENDERER='cairo',
                       ANYRUN_UI_CONFIG=str(config), ANYRUN_UI_PREVIEW=str(preview))
    environment.pop('WAYLAND_DISPLAY', None)
    environment.pop('SWAYSOCK', None)
    with (root / 'sway.log').open('w+') as log:
        compositor = subprocess.Popen(['sway', '--unsupported-gpu', '-c', str(sway_config)], env=environment,
                                      stdout=log, stderr=subprocess.STDOUT)
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
            environment.update(SWAYSOCK=str(sockets[0]), WAYLAND_DISPLAY=wayland[0].name)
            command = ['cargo', 'test', '--offline', '--locked', '--release', '--manifest-path',
                       str(base / '.local/src/anyrun/Cargo.toml'), '--package', 'anyrun', '--target-dir',
                       str(base / '.cache/anyrun-prefix-build'), 'calculator_ui_flow', '--', '--ignored',
                       '--nocapture', '--test-threads=1']
            subprocess.run(command, env=environment, check=True, timeout=60)
            print(f'Interface e bordas verificadas. Prévias: {preview}')
        finally:
            if compositor.poll() is None:
                compositor.terminate()
                compositor.wait(timeout=5)
