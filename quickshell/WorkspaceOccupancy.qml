import QtQuick
import Quickshell
import Quickshell.I3
import Quickshell.Io
import Quickshell.Wayland

Scope {
    id: state

    property bool enabled: true
    property bool ready: false
    property var windowCounts: ({})
    property bool refreshPending: false

    function hasWindows(workspaceId) {
        return ready && (windowCounts[String(workspaceId)] ?? 0) > 0;
    }

    function countWindows(node) {
        let count = (node.type === "con" || node.type === "floating_con")
            && ((node.pid ?? 0) > 0 || (node.window ?? null) !== null
                || (node.app_id ?? null) !== null || (node.shell ?? null) !== null) ? 1 : 0;
        for (const child of [...(node.nodes ?? []), ...(node.floating_nodes ?? [])])
            count += countWindows(child);
        return count;
    }

    function readTree(tree) {
        const counts = {};
        function visit(node) {
            if (node.type === "workspace") {
                counts[String(node.id)] = state.countWindows(node);
                return;
            }
            for (const child of [...(node.nodes ?? []), ...(node.floating_nodes ?? [])])
                visit(child);
        }
        visit(tree);
        windowCounts = counts;
        ready = true;
    }

    function refresh(invalidate = true) {
        if (!enabled)
            return;
        if (invalidate)
            ready = false;
        if (treeProcess.running)
            refreshPending = true;
        else
            treeProcess.running = true;
    }

    Component.onCompleted: refresh()
    onEnabledChanged: refresh()

    I3IpcListener {
        subscriptions: state.enabled ? ["workspace", "window", "output"] : []
        onIpcEvent: event => {
            // The I3 model can retain an old name/number after Sway renames
            // an isolated workspace, or an old monitor during hotplug.
            if (event.type === "output") {
                I3.refreshMonitors();
                I3.refreshWorkspaces();
                state.refresh();
                return;
            }
            const change = JSON.parse(event.data).change;
            if (event.type === "workspace") {
                I3.refreshWorkspaces();
                state.refresh();
            } else if (["new", "close", "move", "floating"].includes(change)) {
                state.refresh();
            }
        }
    }

    // Sway can omit window::move when a window is dragged into an empty workspace.
    Instantiator {
        model: state.enabled ? ToplevelManager.toplevels : null

        delegate: Connections {
            required property Toplevel modelData
            target: modelData

            function onScreensChanged() { Qt.callLater(state.refresh); }
        }
    }

    // A floating window can change workspaces while it still spans the same screens.
    Timer {
        interval: 500
        repeat: true
        running: state.enabled && ToplevelManager.toplevels.values.some(window => window.screens.length > 1)
        onTriggered: {
            if (!treeProcess.running)
                state.refresh(false);
        }
    }

    Connections {
        target: I3.monitors
        function onValuesChanged() {
            I3.refreshWorkspaces();
            state.refresh();
        }
    }

    Connections {
        target: I3
        function onConnected() { state.refresh(); }
    }

    Process {
        id: treeProcess
        command: ["swaymsg", "-r", "-t", "get_tree"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    state.readTree(JSON.parse(text));
                } catch (error) {
                    state.ready = false;
                    console.warn("Could not read workspace windows:", error);
                }
            }
        }
        onExited: (exitCode, exitStatus) => {
            if (exitCode !== 0)
                state.ready = false;
            if (state.refreshPending) {
                state.refreshPending = false;
                Qt.callLater(state.refresh);
            }
        }
    }
}
