import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Services.Pipewire

Scope {
    id: usage

    property bool directCameraInUse: false

    readonly property var captureLinks: Pipewire.linkGroups.values.filter(link => {
        const type = link.source?.type;
        return type === PwNodeType.AudioSource || type === PwNodeType.VideoSource;
    })
    readonly property var captureSources: Pipewire.nodes.values.filter(node =>
        node.type === PwNodeType.AudioSource || node.type === PwNodeType.VideoSource)

    // System audio comes from the output's monitor ports, on an AudioSink.
    readonly property var microphonePcms: captureLinks
        .filter(link => link.state === PwLinkState.Active && link.source?.type === PwNodeType.AudioSource)
        .map(link => link.source.properties)
        .filter(properties => properties["media.class"])
        .map(properties => usage.alsaCapturePcm(properties["api.alsa.path"]))
    readonly property string alsaCapturePcms: [...new Set(microphonePcms.filter(Boolean))].sort().join("\n")
    onAlsaCapturePcmsChanged: microphoneProbe.capturing = false
    readonly property bool microphoneInUse: microphonePcms.includes("") || microphoneProbe.capturing
    readonly property bool pipewireCameraInUse: captureLinks.some(link => {
        if (link.state !== PwLinkState.Active || link.source?.type !== PwNodeType.VideoSource)
            return false;

        const properties = link.source.properties;
        const api = properties["device.api"];
        return properties["media.role"] === "Camera" || api === "v4l2" || api === "libcamera";
    })
    readonly property bool cameraInUse: pipewireCameraInUse || directCameraInUse

    function alsaCapturePcm(path) {
        // Use the PCM address rather than the card that owns the UCM profile.
        const address = /^(?:plug)?hw:(?:CARD=)?([A-Za-z0-9_-]+)(?:,(?:DEV=)?(\d+))?(?:,(?:SUBDEV=)?\d+)?$/.exec(path);
        if (!address)
            return "";

        const card = /^\d+$/.test(address[1]) ? `card${address[1]}` : address[1];
        return `/proc/asound/${card}/pcm${address[2] ?? "0"}c`;
    }

    PwObjectTracker {
        objects: [...usage.captureLinks, ...usage.captureSources]
    }

    Process {
        id: microphoneProbe
        property string checkedPcms: ""
        property bool capturing: false

        command: ["sh", "-c", `
            for pcm; do
                for status in "$pcm"/sub*/status; do
                    read -r field state 2>/dev/null < "$status" || continue
                    [ "$field" = state: ] && [ "$state" = RUNNING ] && exit 0
                done
            done
            exit 1
        `, "sh", ...checkedPcms.split("\n")]
        onExited: exitCode => {
            if (checkedPcms === usage.alsaCapturePcms)
                capturing = exitCode === 0;
        }
    }

    Timer {
        interval: 200
        repeat: true
        triggeredOnStart: true
        running: usage.alsaCapturePcms !== ""
        onTriggered: {
            if (microphoneProbe.running)
                return;

            microphoneProbe.checkedPcms = usage.alsaCapturePcms;
            microphoneProbe.running = true;
        }
    }

    // The uvcvideo driver selects a nonzero alternate setting for isochronous video streaming.
    Process {
        id: cameraStreamProbe
        command: ["sh", "-c", `
            for interface in /sys/bus/usb/drivers/uvcvideo/*:*; do
                read -r subclass 2>/dev/null < "$interface/bInterfaceSubClass" || continue
                [ "$subclass" = 02 ] || continue
                read -r alternate 2>/dev/null < "$interface/bAlternateSetting" || continue
                [ "$alternate" -gt 0 ] && exit 0
            done
            exit 1
        `]
        onExited: exitCode => usage.directCameraInUse = exitCode === 0
    }

    Timer {
        interval: 1000
        repeat: true
        running: true
        triggeredOnStart: true
        onTriggered: cameraStreamProbe.running = true
    }
}
