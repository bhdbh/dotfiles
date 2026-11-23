import QtQuick

QtObject {
    id: sync

    property int mode: -1
    property bool plugged: false
    property int percentage: -1
    property string status: ""
    property int transitionMode: -1

    readonly property bool active: transitionMode >= 0
    readonly property bool charging: active ? transitionMode === 0 : status === "Charging"
    readonly property bool valid: plugged && percentage >= 0 && percentage < 100
        && (status === "Charging" || status === "Not charging")

    // Called only after a successful helper and a matching health_mode read.
    // Bridge the ACPI cache while waiting for the actual charging status.
    function confirm(confirmedMode) {
        cancel();
        if (!valid || mode !== confirmedMode)
            return;

        if ((mode === 0 && status === "Not charging")
            || (mode === 1 && percentage >= 80 && status === "Charging")) {
            transitionMode = mode;
            deadline.restart();
        }
    }

    function cancel() {
        transitionMode = -1;
        deadline.stop();
    }

    function reconcile() {
        if (active && (!valid || mode !== transitionMode
            || (transitionMode === 0 && status === "Charging")
            || (transitionMode === 1 && (percentage < 80 || status !== "Charging"))))
            cancel();
    }

    onModeChanged: reconcile()
    onPercentageChanged: reconcile()
    onValidChanged: reconcile()
    onStatusChanged: reconcile()

    readonly property Timer deadline: Timer {
        interval: 5000
        onTriggered: sync.cancel()
    }
}
