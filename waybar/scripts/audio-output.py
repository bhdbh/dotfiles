"""Show the default audio sink as a one-based output number in Waybar."""

import configparser
import ctypes
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path


CURVES = configparser.ConfigParser()
CURVES.read(Path(__file__).with_name("volume-curves.ini"))


def volume_floor(sink):
    try:
        floor = CURVES.getfloat(sink["name"], "floor", fallback=0)
    except ValueError:
        return 0
    return floor if 0 <= floor < 100 else 0


def curve_ceiling(sink):
    try:
        ceiling = CURVES.getfloat(sink["name"], "ceiling", fallback=100)
    except ValueError:
        return 100
    return ceiling if volume_floor(sink) < ceiling <= 200 else 100


def volume_ceiling(sink):
    return server_volume(sink, 100) * 100


def volume_stretch(sink):
    try:
        start = CURVES.getfloat(sink["name"], "stretch-start", fallback=0)
        end = CURVES.getfloat(sink["name"], "stretch-end", fallback=0)
    except ValueError:
        return None
    return (start, end) if 0 <= start < end <= 100 else None


def stretch_display(sink, percent):
    if (stretch := volume_stretch(sink)) and percent > stretch[0]:
        start, end = stretch
        return start + (percent - start) * (100 - start) / (end - start)
    return percent


def stretch_server(sink, percent):
    if (stretch := volume_stretch(sink)) and percent > stretch[0]:
        start, end = stretch
        return start + (percent - start) * (end - start) / (100 - start)
    return percent


def volume_pivot(sink):
    try:
        pivot = CURVES.getfloat(sink["name"], "pivot", fallback=0)
        level = CURVES.getfloat(sink["name"], "pivot-volume", fallback=0)
        slope = CURVES.getfloat(sink["name"], "pivot-slope", fallback=0)
        start = CURVES.getfloat(sink["name"], "start-slope", fallback=0)
    except ValueError:
        return None
    floor, ceiling = volume_floor(sink), curve_ceiling(sink)
    if 0 < pivot < 100 and floor < level < ceiling:
        if not math.isfinite(start) or start < 0:
            return None
        cubic = slope > 0
        if cubic:
            span = level - floor
            a = 3 * (pivot * (start + slope) - 2 * span)
            b = 2 * (3 * span - pivot * (2 * start + slope))
            c = pivot * start
            if a > 0 and b < 0 and -b < 2 * a and c - b * b / (4 * a) < 0:
                return None
        if not cubic:
            slope = (level - floor) / pivot
        if slope > 3 * (level - floor) / pivot or slope > (ceiling - level) / (100 - pivot):
            return None
        bend = (ceiling - level - slope * (100 - pivot)) / (100 - pivot) ** 2
        return pivot, level, slope, bend, cubic, start
    return None


def lower_volume(percent, floor, pivot, level, slope, cubic, start):
    if not cubic:
        return floor + percent * slope
    t = percent / pivot
    return (floor + t * t * ((3 - 2 * t) * (level - floor) + (t - 1) * pivot * slope)
            + pivot * start * t * (1 - t) ** 2)


def display_volume(sink):
    floor = volume_floor(sink)
    raw = float(sink["volume"]) * 100
    if raw <= floor:
        return 0
    if point := volume_pivot(sink):
        pivot, level, slope, bend, cubic, start = point
        if raw <= level:
            if not cubic:
                return stretch_display(sink, (raw - floor) / slope)
            low, high = 0, pivot
            for _ in range(48):
                mid = (low + high) / 2
                if lower_volume(mid, floor, pivot, level, slope, cubic, start) < raw:
                    low = mid
                else:
                    high = mid
            return stretch_display(sink, (low + high) / 2)
        delta = raw - level
        percent = pivot + 2 * delta / (slope + math.sqrt(slope * slope + 4 * bend * delta))
        return stretch_display(sink, percent)
    return stretch_display(sink, (raw - floor) * 100 / (curve_ceiling(sink) - floor))


def server_volume(sink, percent):
    if percent <= 0:
        return 0
    percent = stretch_server(sink, min(100, percent))
    floor = volume_floor(sink)
    if point := volume_pivot(sink):
        pivot, level, slope, bend, cubic, start = point
        if percent <= pivot:
            return lower_volume(percent, floor, pivot, level, slope, cubic, start) / 100
        delta = percent - pivot
        return (level + slope * delta + bend * delta * delta) / 100
    return (floor + percent * (curve_ceiling(sink) - floor) / 100) / 100


class AudioState(ctypes.Structure):
    _fields_ = [("percent", ctypes.c_double), ("muted", ctypes.c_int),
                ("available", ctypes.c_int)]


def precise_volume(sink):
    """Read the native client's exact level; wpctl rounds it to two decimals."""
    client = None
    try:
        lib = ctypes.CDLL(str(Path(__file__).with_name("volume-control.so")))
        lib.audio_client_new.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p]
        lib.audio_client_new.restype = ctypes.c_void_p
        lib.audio_client_state.argtypes = [ctypes.c_void_p]
        lib.audio_client_state.restype = AudioState
        lib.audio_client_raw_volume.argtypes = [ctypes.c_void_p]
        lib.audio_client_raw_volume.restype = ctypes.c_double
        lib.audio_client_free.argtypes = [ctypes.c_void_p]
        client = lib.audio_client_new(sink["name"].encode(), None, None)
        if client:
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                if lib.audio_client_state(client).available:
                    return lib.audio_client_raw_volume(client)
                time.sleep(.005)
    except (OSError, AttributeError):
        pass
    finally:
        if client:
            lib.audio_client_free(client)
    return float(sink["volume"])


def sink_order(sink):
    try:
        order = CURVES.getint(sink["name"], "order", fallback=1000)
    except ValueError:
        order = 1000
    return order, sink["name"]


SINK = re.compile(
    r"(?P<selected>\*)?\s*(?P<id>\d+)\.\s+(?P<name>.+?)"
    r"\s+\[vol:\s*(?P<volume>\d+(?:\.\d+)?)(?P<muted>\s+MUTED)?\]"
)


def audio_sinks(status):
    in_audio = False
    in_sinks = False
    sinks = []
    for line in status.splitlines():
        if line == "Audio":
            in_audio = True
            continue
        if not in_audio:
            continue
        if line and not line[0].isspace():
            break
        if "Sinks:" in line:
            in_sinks = True
            continue
        if in_sinks and line.rstrip().endswith(":"):
            break
        if in_sinks and (match := SINK.search(line)):
            sinks.append(match.groupdict())

    # Object IDs and discovery order change after rebooting or reconnecting.
    # Use the same stable order for the indicator, tooltip and output cycling.
    return sorted(sinks, key=sink_order)


def volume_status(status):
    sinks = audio_sinks(status)
    for number, sink in enumerate(sinks, 1):
        if sink["selected"]:
            outputs = "\n".join(
                f"{index}: {output['name']}" for index, output in enumerate(sinks, 1)
            )
            return {
                "text": str(number),
                "tooltip": f"Saída atual: {number} — {sink['name']}"
                + (" (mudo)" if sink["muted"] else "")
                + f"\n\n{outputs}",
                "class": "muted" if sink["muted"] else "active",
            }
    return {
        "text": "--",
        "tooltip": "Nenhuma saída de áudio disponível.",
        "class": "unavailable",
    }


def widget_status(status):
    sinks = audio_sinks(status)
    for number, sink in enumerate(sinks, 1):
        if sink['selected']:
            volume = round(display_volume(sink))
            return f'Volume: {volume}%\n{number}\n' + ('muted' if sink['muted'] else 'active') + f'\n{len(sinks)}'
    return f'Volume: --%\n--\nmuted\n{len(sinks)}'


def read_status():
    try:
        status = subprocess.check_output(
            ["wpctl", "status", "-n"],
            env={**os.environ, "LC_ALL": "C"},
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        sink = next((sink for sink in audio_sinks(status) if sink["selected"]), None)
        if sink:
            raw = precise_volume(sink)
            for match in SINK.finditer(status):
                if match["name"] == sink["name"]:
                    start, end = match.span("volume")
                    status = status[:start] + f"{raw:.12f}" + status[end:]
                    break
        return status
    except (OSError, subprocess.SubprocessError):
        return ""


def main():
    action = next((arg for arg in sys.argv[1:] if arg in ('--next', '--up', '--down')), None)
    status = read_status()
    if action:
        if action == '--next':
            sinks = audio_sinks(status)
            if len(sinks) < 2:
                return
            selected = next((index for index, sink in enumerate(sinks) if sink["selected"]), -1)
            target = sinks[(selected + 1) % len(sinks)]
            subprocess.run(["wpctl", "set-default", target["id"]], check=True)
        else:
            sink = next((sink for sink in audio_sinks(status) if sink['selected']), None)
            if sink is None:
                return
            percent = max(0, min(100, round(display_volume(sink)) + (2 if action == '--up' else -2)))
            raw = server_volume(sink, percent)
            limit = f'{volume_ceiling(sink) / 100:.8f}'
            subprocess.run(['wpctl', 'set-volume', '-l', limit, sink['id'], f'{raw:.8f}'], check=True)
        if '--widget' not in sys.argv:
            return
        status = read_status()
    if '--widget' in sys.argv:
        print(widget_status(status))
    else:
        print(json.dumps(volume_status(status), ensure_ascii=False))


if __name__ == "__main__":
    main()
