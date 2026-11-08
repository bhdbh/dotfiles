"""Compare ACPI cache latency; restore the cache and charge limit on every exit."""
import json
import os
from pathlib import Path
import statistics
import subprocess
import time

CACHE = Path('/sys/module/battery/parameters/cache_time')
MODE = Path('/sys/bus/wmi/drivers/acer-wmi-battery/health_mode')
BATTERY = Path('/sys/class/power_supply/BAT1')
HELPER = '/usr/local/libexec/waybar-acer-charge'

def read(path):
    return path.read_text().strip()

def toggle():
    subprocess.run([HELPER],check=True,capture_output=True,timeout=10)

def idle():
    deadline = time.monotonic()+8
    while time.monotonic()<deadline:
        if read(BATTERY/'status')=='Not charging' and int(read(BATTERY/'current_now'))==0:
            return
        time.sleep(.05)
    raise RuntimeError('A bateria não estabilizou em Not charging; teste interrompido.')

def trial(cache_ms):
    CACHE.write_text(str(cache_ms)+'\n')
    idle()
    # Prime the cache before the mode change, reproducing the stale-status case.
    read(BATTERY/'status')
    start = time.monotonic()
    toggle()
    assert read(MODE)=='0'
    confirmed = time.monotonic()-start
    charging = current = None
    deadline = start+8
    while time.monotonic()<deadline:
        status = read(BATTERY/'status')
        rate = int(read(BATTERY/'current_now'))
        elapsed = time.monotonic()-start
        if charging is None and status=='Charging': charging=elapsed
        if current is None and rate>0: current=elapsed
        if charging is not None and current is not None: break
        time.sleep(.025)
    result = dict(cache_ms=cache_ms,limit_confirmed_s=round(confirmed,3),
                  charging_s=round(charging,3) if charging is not None else None,
                  positive_current_s=round(current,3) if current is not None else None)
    toggle()
    assert read(MODE)=='1'
    idle()
    print(json.dumps(result),flush=True)
    return result

def main():
    if os.geteuid()!=0:
        raise SystemExit('Este teste precisa de sudo para alterar temporariamente o cache ACPI.')
    original_cache=read(CACHE)
    original_mode=read(MODE)
    if original_mode!='1' or read(Path('/sys/class/power_supply/ACAD/online'))!='1':
        raise SystemExit('Teste exige tomada conectada e limite atual de 80%; nenhum ajuste foi feito.')
    if not 80<=int(read(BATTERY/'capacity'))<100:
        raise SystemExit('Teste exige bateria entre 80% e 99%; nenhum ajuste foi feito.')
    original_config=Path('/etc/modprobe.d/waybar-acer-charge.conf').read_bytes()
    results=[]
    try:
        for value in [int(original_cache),100,100,int(original_cache)]:
            results.append(trial(value))
    finally:
        if read(MODE)!=original_mode: toggle()
        CACHE.write_text(original_cache+'\n')
        config=Path('/etc/modprobe.d/waybar-acer-charge.conf')
        if config.read_bytes()!=original_config: config.write_bytes(original_config)
        assert read(MODE)==original_mode and read(CACHE)==original_cache
        print('Restaurados: limite de 80%, cache ACPI original e configuração persistente.',flush=True)
    for value in [int(original_cache),100]:
        samples=[r['charging_s'] for r in results if r['cache_ms']==value and r['charging_s'] is not None]
        if samples: print(f'Cache {value}ms: mediana até Charging {statistics.median(samples):.3f}s',flush=True)

if __name__=='__main__': main()
