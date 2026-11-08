"""Persist the measured 100ms ACPI cache, including built-in battery drivers."""
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import tempfile

CACHE = Path('/sys/module/battery/parameters/cache_time')
CONFIG = Path('/etc/tmpfiles.d/waybar-battery-cache.conf')
CONTENT = '''# ACPI battery state cache; verified against the local Acer battery.
# The built-in battery driver is ready before systemd-tmpfiles-setup.
w /sys/module/battery/parameters/cache_time - - - - 100
'''

def main():
    if os.geteuid()!=0:
        raise SystemExit('Este ajuste precisa de sudo para gravar o parâmetro e sua configuração de boot.')
    old_cache = CACHE.read_text()
    old_config = CONFIG.read_bytes() if CONFIG.exists() else None
    if old_config is not None and old_config != CONTENT.encode():
        saved = CONFIG.with_name(CONFIG.name+'.backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        saved.write_bytes(old_config)
        print('Configuração anterior preservada em',saved,flush=True)
    try:
        with tempfile.NamedTemporaryFile(dir=CONFIG.parent,prefix='.waybar-battery-cache-',delete=False) as file:
            temporary=Path(file.name)
            file.write(CONTENT.encode())
        temporary.chmod(0o644)
        os.replace(temporary,CONFIG)
        subprocess.run(['systemd-tmpfiles','--create',str(CONFIG)],check=True)
        assert CACHE.read_text().strip()=='100'
    except BaseException:
        CACHE.write_text(old_cache)
        if old_config is None: CONFIG.unlink(missing_ok=True)
        else: CONFIG.write_bytes(old_config)
        raise
    print('Aplicado: cache ACPI de 100ms.',flush=True)
    print('Persistência de boot:',CONFIG,flush=True)
    print('O limite de carga e o plano de energia não foram alterados.',flush=True)

if __name__=='__main__': main()
