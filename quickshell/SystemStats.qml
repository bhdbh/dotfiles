import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Networking
import Quickshell.Services.Pipewire
import Quickshell.Services.UPower

Scope {
    id: stats

    property int cpuPercent: -1
    property var cpuTemperature: null
    property int ramPercent: -1
    property real previousCpuTotal: -1
    property real previousCpuIdle: 0
    property int batteryHealthMode: -1
    property int desiredBatteryHealthMode: -1
    property int applyingBatteryHealthMode: -1
    property int confirmingBatteryHealthMode: -1
    property bool batteryHealthReadPending: false
    property int desiredPowerProfile: -1
    property int applyingPowerProfile: -1
    property string batteryStatus: ""
    property bool ethernetConnected: false

    readonly property var battery: UPower.displayDevice
    readonly property var physicalBattery: UPower.devices.values.find(device => device.isLaptopBattery) ?? null
    readonly property var audioOutput: Pipewire.defaultAudioSink
    // Stable numbers: 1 = notebook, 2 = Headset 2.0. HDMI is excluded.
    readonly property var audioOutputPrefixes: [
        "alsa_output.pci-0000_03_00.6.",
        "alsa_output.usb---_KTMicro_--_Audio_hs_2.0_headset_"
    ]
    readonly property var audioOutputs: Pipewire.nodes.values
        .filter(node => node.isSink && !node.isStream && node.audio && outputNumber(node) > 0)
        .sort((a, b) => outputNumber(a) - outputNumber(b))
    readonly property var readyAudioOutputs: audioOutputs.filter(output => output.ready)
    readonly property bool canCycleAudioOutput: readyAudioOutputs.length > 1
    readonly property int audioOutputNumber: outputNumber(audioOutput)
    readonly property var wifiDevice: Networking.devices.values.find(device => device.type === DeviceType.Wifi && device.connected) ?? null
    readonly property var wifiNetwork: wifiDevice?.networks.values.find(network => network.connected) ?? null

    readonly property bool batteryPresent: battery.ready && battery.isPresent
        && battery.type === UPowerDeviceType.Battery
    readonly property int batteryPercent: batteryPresent ? Math.round(battery.percentage * 100) : -1
    readonly property bool batteryPlugged: batteryPresent && !UPower.onBattery
    readonly property bool batteryCharging: batteryPresent
        && (batteryStatus.length > 0 ? batteryChargeSync.charging : battery.state === UPowerDeviceState.Charging)
    readonly property int displayedPowerProfile: desiredPowerProfile >= 0
        ? desiredPowerProfile : PowerProfiles.profile
    readonly property bool batteryBalanced: displayedPowerProfile === PowerProfile.Balanced
    readonly property color batteryLabelColor: displayedPowerProfile === PowerProfile.PowerSaver ? "#f97316" : "#fafafa"
    readonly property color batteryPercentageColor: batteryCharging ? "#22c55e"
        : batteryPercent >= 0 && batteryPercent < 5 ? "#ef4444" : batteryLabelColor
    readonly property int displayedBatteryHealthMode: desiredBatteryHealthMode >= 0
        ? desiredBatteryHealthMode : batteryHealthMode
    readonly property color batteryIndicatorColor: displayedBatteryHealthMode === 0 ? "#22c55e" : "#27272a"
    readonly property int volumePercent: audioOutput?.ready && audioOutput.audio
        ? Math.round((audioOutput.audio.muted ? 0 : audioOutput.audio.volume) * 100) : -1
    readonly property int wifiPercent: wifiNetwork
        ? Math.round(wifiNetwork.signalStrength * 100) : -1
    readonly property string networkFallbackText: wifiDevice ? "--" : ethernetConnected ? "Online" : "Offline"

    function outputNumber(output) {
        return output ? audioOutputPrefixes.findIndex(prefix => output.name.startsWith(prefix)) + 1 : 0;
    }

    function cycleAudioOutput() {
        if (!canCycleAudioOutput)
            return;

        const next = (readyAudioOutputs.indexOf(audioOutput) + 1) % readyAudioOutputs.length;
        Pipewire.preferredDefaultAudioSink = readyAudioOutputs[next];
    }

    function adjustVolume(delta) {
        if (!audioOutput?.ready || !audioOutput.audio)
            return;

        const audio = audioOutput.audio;
        const percentage = Math.round(audio.volume * 100);
        audio.volume = Math.max(0, Math.min(100, percentage + delta)) / 100;
    }

    function toggleMute() {
        if (!audioOutput?.ready || !audioOutput.audio)
            return;

        audioOutput.audio.muted = !audioOutput.audio.muted;
    }

    function togglePowerProfile() {
        desiredPowerProfile = batteryBalanced ? PowerProfile.PowerSaver : PowerProfile.Balanced;
        applyPowerProfile();
    }

    function applyPowerProfile() {
        if (powerProfileProcess.running || desiredPowerProfile < 0)
            return;

        profileConfirmation.stop();
        applyingPowerProfile = desiredPowerProfile;
        powerProfileProcess.command = ["busctl", "--system", "set-property",
            "org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles",
            "org.freedesktop.UPower.PowerProfiles", "ActiveProfile", "s",
            applyingPowerProfile === PowerProfile.Balanced ? "balanced" : "power-saver"];
        powerProfileProcess.running = true;
    }

    function toggleBatteryHealthMode() {
        if (batteryHealthMode < 0)
            return;

        batteryChargeSync.cancel();
        desiredBatteryHealthMode = 1 - displayedBatteryHealthMode;
        applyBatteryHealthMode();
    }

    function applyBatteryHealthMode() {
        if (chargeModeProcess.running || batteryHealthReadPending || desiredBatteryHealthMode < 0)
            return;

        if (desiredBatteryHealthMode === batteryHealthMode) {
            desiredBatteryHealthMode = -1;
            return;
        }

        applyingBatteryHealthMode = 1 - batteryHealthMode;
        chargeModeProcess.running = true;
    }

    function refreshBatteryStatus() {
        batteryStatusFile.reload();
        chargingPoll.remaining = 25;
        chargingPoll.restart();
    }

    Connections {
        target: PowerProfiles
        function onProfileChanged() {
            if (!powerProfileProcess.running && desiredPowerProfile === PowerProfiles.profile) {
                desiredPowerProfile = -1;
                profileConfirmation.stop();
            }
        }
    }

    Process {
        id: powerProfileProcess
        stderr: StdioCollector {
            onStreamFinished: {
                if (text.trim().length > 0)
                    console.warn("Battery power profile:", text.trim());
            }
        }
        onExited: (exitCode, exitStatus) => {
            if (exitCode !== 0) {
                desiredPowerProfile = -1;
                console.warn("Could not change power profile; exit code", exitCode);
            } else if (desiredPowerProfile !== applyingPowerProfile) {
                stats.applyPowerProfile();
            } else if (desiredPowerProfile === PowerProfiles.profile) {
                desiredPowerProfile = -1;
            } else {
                profileConfirmation.restart();
            }
        }
    }

    Timer {
        id: profileConfirmation
        interval: 5000
        onTriggered: {
            if (desiredPowerProfile >= 0 && desiredPowerProfile !== PowerProfiles.profile)
                console.warn("Power profile was not confirmed by the service");
            desiredPowerProfile = -1;
        }
    }

    FileView {
        id: batteryHealthFile
        path: "/sys/bus/wmi/drivers/acer-wmi-battery/health_mode"
        printErrors: false
        onLoaded: {
            const value = text().trim();
            stats.batteryHealthMode = value === "0" || value === "1" ? Number(value) : -1;
            stats.batteryHealthReadPending = false;
            if (stats.confirmingBatteryHealthMode >= 0) {
                if (stats.batteryHealthMode === stats.confirmingBatteryHealthMode
                    && stats.desiredBatteryHealthMode === stats.batteryHealthMode)
                    batteryChargeSync.confirm(stats.batteryHealthMode);
                stats.confirmingBatteryHealthMode = -1;
            }
            if (stats.batteryHealthMode < 0)
                stats.desiredBatteryHealthMode = -1;
            else
                stats.applyBatteryHealthMode();
        }
        onLoadFailed: {
            stats.batteryHealthMode = -1;
            stats.desiredBatteryHealthMode = -1;
            stats.confirmingBatteryHealthMode = -1;
            stats.batteryHealthReadPending = false;
        }
    }

    BatteryChargeSync {
        id: batteryChargeSync
        mode: stats.batteryHealthMode
        plugged: stats.batteryPlugged
        percentage: stats.batteryPercent
        status: stats.batteryStatus
    }

    FileView {
        id: batteryStatusFile
        path: stats.physicalBattery?.nativePath
            ? "/sys/class/power_supply/" + stats.physicalBattery.nativePath + "/status" : ""
        printErrors: false
        onLoaded: stats.batteryStatus = text().trim()
        onLoadFailed: stats.batteryStatus = ""
    }

    Timer {
        id: chargingPoll
        property int remaining: 0
        interval: 200
        repeat: true
        onTriggered: {
            batteryStatusFile.reload();
            if (--remaining <= 0)
                stop();
        }
    }

    Connections {
        target: stats.battery
        function onStateChanged() { stats.refreshBatteryStatus(); }
    }

    Process {
        id: chargeModeProcess
        command: ["sudo", "-n", "/usr/local/libexec/quickshell-battery-charge", "toggle"]
        stderr: StdioCollector {
            onStreamFinished: {
                if (text.trim().length > 0)
                    console.warn("Battery charge mode:", text.trim());
            }
        }
        onExited: (exitCode, exitStatus) => {
            confirmingBatteryHealthMode = exitCode === 0 ? applyingBatteryHealthMode : -1;
            if (exitCode !== 0) {
                batteryChargeSync.cancel();
                desiredBatteryHealthMode = -1;
                console.warn("Could not change battery charge mode; exit code", exitCode);
            }
            batteryHealthReadPending = true;
            batteryHealthFile.reload();
            stats.refreshBatteryStatus();
        }
    }

    PwObjectTracker {
        objects: [stats.audioOutput, ...stats.audioOutputs.filter(output => output !== stats.audioOutput)]
    }

    // Quickshell's networking model exposes Wi-Fi devices only.
    Process {
        id: ethernetStatus
        command: ["nmcli", "-t", "-f", "TYPE,STATE", "device", "status"]
        environment: ({ LC_ALL: "C" })
        running: true
        stdout: StdioCollector { id: ethernetStatusOutput }
        onExited: exitCode => {
            stats.ethernetConnected = exitCode === 0 && ethernetStatusOutput.text.split("\n")
                .some(line => line === "ethernet:connected" || line === "ethernet:connected (externally)");
        }
    }

    Process {
        id: networkMonitor
        command: ["nmcli", "monitor"]
        running: true
        stdout: SplitParser { onRead: ethernetRefresh.start() }
        onExited: ethernetRefresh.start()
    }

    Timer {
        id: ethernetRefresh
        interval: 100
        onTriggered: {
            if (ethernetStatus.running)
                start();
            else
                ethernetStatus.running = true;
        }
    }

    Process {
        command: ["sh", Quickshell.shellPath("scripts/cpu-temperature-path.sh")]
        running: true
        stdout: StdioCollector {
            onStreamFinished: cpuTemperatureFile.path = text.trim()
        }
    }

    FileView {
        id: cpuTemperatureFile
        onLoaded: {
            const milliDegrees = Number.parseFloat(text());
            stats.cpuTemperature = Number.isFinite(milliDegrees)
                ? Math.round(milliDegrees / 1000) : null;
        }
        onLoadFailed: stats.cpuTemperature = null
    }

    FileView {
        id: cpuFile
        path: "/proc/stat"

        onLoaded: {
            // guest and guest_nice are already included in user and nice.
            const values = text().split("\n", 1)[0].trim().split(/\s+/).slice(1, 9).map(Number);
            if (values.length !== 8 || !values.every(Number.isFinite))
                return;

            const total = values.reduce((sum, value) => sum + value, 0);
            const idle = values[3] + values[4];
            const elapsed = total - stats.previousCpuTotal;
            if (stats.previousCpuTotal >= 0 && elapsed > 0) {
                const busy = 1 - (idle - stats.previousCpuIdle) / elapsed;
                stats.cpuPercent = Math.round(Math.max(0, Math.min(1, busy)) * 100);
            }
            stats.previousCpuTotal = total;
            stats.previousCpuIdle = idle;
        }
        onLoadFailed: stats.cpuPercent = -1
    }

    FileView {
        id: ramFile
        path: "/proc/meminfo"

        onLoaded: {
            const contents = text();
            const total = /^MemTotal:\s+(\d+)/m.exec(contents);
            const available = /^MemAvailable:\s+(\d+)/m.exec(contents);
            if (total && available && Number(total[1]) > 0) {
                const used = 1 - Number(available[1]) / Number(total[1]);
                stats.ramPercent = Math.round(Math.max(0, Math.min(1, used)) * 100);
            }
        }
        onLoadFailed: stats.ramPercent = -1
    }

    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: {
            cpuFile.reload();
            ramFile.reload();
            batteryHealthFile.reload();
            batteryStatusFile.reload();
            if (!networkMonitor.running)
                networkMonitor.running = true;
            if (cpuTemperatureFile.path !== "")
                cpuTemperatureFile.reload();
        }
    }
}
