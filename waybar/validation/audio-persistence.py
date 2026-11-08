"""Check output identities and the saved calibration across device discovery."""
import importlib.util
import ctypes
from itertools import permutations
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LAPTOP = "alsa_output.pci-0000_03_00.6.analog-stereo"
HEADSET = "alsa_output.usb---_KTMicro_--_Audio_hs_2.0_headset_2024-05-21-0000-0000-0000-00.analog-stereo"


def load():
    spec = importlib.util.spec_from_file_location("audio_output", ROOT / "scripts/audio-output.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def status(items, selected):
    return "Audio\n ├─ Sinks:\n" + "".join(
        f" │  {'*' if name == selected else ' '} {index}. {name} [vol: 1.00]\n"
        for index, name in items
    ) + " │\n ├─ Sources:\nVideo\n"


def main():
    for ids in [(53, 58), (120, 42), (999, 1)]:
        # A new process must load the same calibration after restarting.
        audio = load()
        for items in permutations(zip(ids, [HEADSET, LAPTOP])):
            for selected, number in [(LAPTOP, "1"), (HEADSET, "2")]:
                snapshot = status(items, selected)
                assert [sink["name"] for sink in audio.audio_sinks(snapshot)] == [LAPTOP, HEADSET]
                assert audio.volume_status(snapshot)["text"] == number
                assert audio.widget_status(snapshot).splitlines()[1] == number
                with (patch.object(audio, "read_status", return_value=snapshot),
                      patch.object(audio.subprocess, "run") as run,
                      patch.object(audio.sys, "argv", ["audio-output.py", "--next"])):
                    audio.main()
                    target = next(str(index) for index, name in items if name != selected)
                    run.assert_called_once_with(["wpctl", "set-default", target], check=True)
        assert audio.volume_status(status([(58, LAPTOP)], LAPTOP))["text"] == "1"
        assert audio.audio_sinks("") == []
        for name, floor, ceiling in [(LAPTOP, 6, 100), (HEADSET, 0, 186)]:
            sink = {"name": name}
            assert audio.volume_floor(sink) == floor
            assert audio.curve_ceiling(sink) == ceiling
            expected_max = 100 if name == LAPTOP else 37.31555555555556
            assert abs(audio.volume_ceiling(sink) - expected_max) < 1e-10
            for percent in [0, 1, 2, 25, 39.99, 40, 40.01, 50, 75, 100]:
                raw = audio.server_volume(sink, percent)
                assert abs(audio.display_volume({**sink, "volume": raw}) - percent) < 1e-8
        assert abs(audio.server_volume({"name": HEADSET}, 40) - .1744) < 1e-10
        assert abs(audio.display_volume({"name": HEADSET, "volume": .1744}) - 40) < 1e-10

    with tempfile.TemporaryDirectory(prefix="waybar-curves-") as directory:
        directory = Path(directory)
        source = directory / "curve.c"
        source.write_text('#include "volume-curve.h"\n'
                          'double display(double r,double f,double c,double p,double l,double s,double a,double x,double y) {'
                          'return volume_curve_stretch_display(volume_curve_display(r,f,c,p,l,s,a),x,y);}\n'
                          'double server(double v,double f,double c,double p,double l,double s,double a,double x,double y) {'
                          'return volume_curve_server(volume_curve_stretch_server(v,x,y),f,c,p,l,s,a);}\n')
        library = directory / "curve.so"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                        "-I", str(ROOT / "scripts"), str(source), "-o", str(library), "-lm"], check=True)
        native = ctypes.CDLL(str(library))
        for function in [native.display, native.server]:
            function.argtypes = [ctypes.c_double] * 9
            function.restype = ctypes.c_double
        for name, curve in [(LAPTOP, (6, 100, 0, 0, 0, 0, 0, 0)), (HEADSET, (0, 186, 40, 17.44, .086, 1.354836565096953, 50, 60))]:
            sink = {"name": name}
            previous = -1
            for percent in [x / 10 for x in range(1001)]:
                raw = native.server(percent, *curve)
                assert raw > previous, (name, percent)
                previous = raw
                assert abs(raw / 100 - audio.server_volume(sink, percent)) < 1e-10
                assert abs(native.display(raw, *curve) - percent) < 1e-8
                # Real PulseAudio quantization must still let 2% steps advance,
                # including the first step up from complete silence.
                if percent.is_integer() and percent % 2 == 0:
                    quantized = round(raw * 65536 / 100) * 100 / 65536
                    assert abs(native.display(quantized, *curve) - percent) < .02

        # Guard the listening range: low settings must approach silence,
        # rather than retaining the previous 14% minimum server gain.
        headset = {"name": HEADSET}
        assert audio.server_volume(headset, 0) == 0
        # The first step must now match the level previously reached at 10%.
        assert abs(audio.server_volume(headset, 2) - .0256375) < 1e-10
        assert abs(audio.display_volume({**headset, "volume": .0256375}) - 2) < 1e-10
        # Preserve 40–50%, then spread the approved 50–60% over 50–100%.
        for percent in [x / 10 for x in range(400, 1001)]:
            reference = percent if percent <= 50 else 50 + (percent - 50) / 5
            delta = reference - 40
            old_slope = (17.44 - 14) / 40
            old_bend = (186 - 17.44 - old_slope * 60) / (60 * 60)
            previous_raw = (17.44 + old_slope * delta + old_bend * delta * delta) / 100
            assert abs(audio.server_volume(headset, percent) - previous_raw) < 1e-10
        assert abs(audio.server_volume(headset, 50) - .2283888888888889) < 1e-10
        assert abs(audio.server_volume(headset, 100) - .3731555555555556) < 1e-10
        assert audio.server_volume(headset, 102) == audio.server_volume(headset, 100)
        # No jump in value or slope at the join.
        epsilon = 1e-4
        left = (audio.server_volume(headset, 40) - audio.server_volume(headset, 40 - epsilon)) / epsilon
        right = (audio.server_volume(headset, 40 + epsilon) - audio.server_volume(headset, 40)) / epsilon
        assert abs(left - right) < 1e-7

    # wpctl prints 0.17 for the reference volume. The exact native level is
    # needed so the keyboard and the native scroll use the same starting point.
    snapshot = status([(61, HEADSET), (78, LAPTOP)], HEADSET).replace("[vol: 1.00]", "[vol: 0.17]", 1)
    exact = .1743927
    with (patch.object(audio.subprocess, "check_output", return_value=snapshot),
          patch.object(audio, "precise_volume", return_value=exact)):
        accurate = audio.read_status()
    assert audio.widget_status(accurate).splitlines()[0] == "Volume: 40%"
    with (patch.object(audio, "read_status", return_value=accurate),
          patch.object(audio.subprocess, "run") as run,
          patch.object(audio.sys, "argv", ["audio-output.py", "--up"])):
        audio.main()
        command = run.call_args.args[0]
        target = audio.server_volume({"name": HEADSET}, 42)
        assert command[:5] == ["wpctl", "set-volume", "-l", "0.37315556", "61"]
        assert abs(float(command[5]) - target) < 1e-8
    print("PASS: 0–50% preserved; new 100% equals previous 60%; effective volume limit; silence; monotonic curve; C/Python parity; quantized steps; keyboard precision; output numbering.")


if __name__ == "__main__":
    main()
