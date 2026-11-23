import QtQuick
import Quickshell
import Quickshell.I3
import Quickshell.Io

ShellRoot {
    id: root

    readonly property int verticalPadding: 4
    readonly property int screenMargin: 3
    readonly property int fontSize: 22
    readonly property int barHeight: fontSize + verticalPadding * 2
    readonly property real capitalHeight: capitalMetrics.tightBoundingRect.height
    readonly property int spacing: Math.round((barHeight - capitalHeight) / 2)
    readonly property int maxWorkspaces: 9
    readonly property string primaryOutputName: primaryOutput.text().trim()
    readonly property string previewOutputName: previewOutput.loaded ? previewOutput.text().trim() : ""
    readonly property var matchingScreens: Quickshell.screens
        .filter(screen => screen && (screen.name === root.primaryOutputName
            || screen.name === root.previewOutputName))
    // Sway's snapshot stays consistent after renames and monitor moves;
    // the I3 model's derived monitor/active pointers can retain old values.
    readonly property var primaryWorkspaces: I3.workspaces.values
        .map(workspace => workspace?.lastIpcObject ?? {})
        .filter(workspace => workspace.num >= 1
            && workspace.num <= root.maxWorkspaces
            && workspace.output === root.primaryOutputName)
        .map(workspace => ({
            id: workspace.id,
            name: workspace.name,
            number: workspace.num,
            active: workspace.visible,
            output: workspace.output
        }))
        .sort((a, b) => a.number - b.number)
    readonly property int nextWorkspaceNumber: {
        // Fill gaps left by keyboard jumps or closed workspaces.
        for (let number = 1; number <= root.maxWorkspaces; number++) {
            if (!root.primaryWorkspaces.some(workspace => workspace.number === number))
                return number;
        }
        return root.maxWorkspaces + 1;
    }
    readonly property var activePrimaryWorkspace: primaryWorkspaces
        .find(workspace => workspace.active) ?? null
    readonly property bool workspaceCreationAllowed: nextWorkspaceNumber <= maxWorkspaces
        && activePrimaryWorkspace !== null
        && workspaceOccupancy.hasWindows(activePrimaryWorkspace.id)
    property var barScreens: []

    function updateBarScreens() {
        // Read the current screens after monitor/file notifications settle.
        // Variants must not be rebuilt during another component's creation.
        root.barScreens = root.matchingScreens;
    }

    onMatchingScreensChanged: Qt.callLater(root.updateBarScreens)
    Component.onCompleted: Qt.callLater(root.updateBarScreens)

    function highValueColor(value) {
        return value > 85 ? "#ef4444" // red-500
            : value > 75 ? "#f97316" : "#fafafa"; // orange-500 / zinc-50
    }

    // Center the letter body around the bar's midpoint with a shared baseline.
    readonly property int textBaseline: Math.round(spacing - capitalMetrics.tightBoundingRect.y)
    readonly property font textFont: Qt.font({
        family: "scientifica",
        pixelSize: root.fontSize,
        hintingPreference: Font.PreferFullHinting
    })

    TextMetrics {
        id: capitalMetrics
        font: root.textFont
        text: "H"
        renderType: Text.NativeRendering
    }

    component BarText: Text {
        y: root.textBaseline - baselineOffset
        height: root.fontSize
        lineHeight: root.fontSize
        lineHeightMode: Text.FixedHeight
        font: root.textFont
        textFormat: Text.PlainText
        color: "#fafafa" // zinc-50
        renderType: Text.NativeRendering
        antialiasing: false
        clip: false
    }

    component StatusText: Item {
        id: statusText

        required property string text
        property alias color: renderedText.color
        implicitWidth: textMetrics.tightBoundingRect.width
        height: root.barHeight

        TextMetrics {
            id: textMetrics
            font: root.textFont
            text: statusText.text
            renderType: Text.NativeRendering
        }

        BarText {
            id: renderedText
            x: -textMetrics.tightBoundingRect.x
            text: statusText.text
        }
    }

    component StatusItem: Item {
        id: statusItem

        required property string label
        required property int percentage
        property string fallbackText: "--"
        property color labelColor: "#fafafa"
        property bool labelPercentageClickable: false
        property color percentageColor: "#fafafa"
        property bool percentageClickable: false
        property string extraText: ""
        property color extraTextColor: "#fafafa"
        property bool extraTextClickable: false
        property bool showIndicator: false
        property color indicatorColor: "#27272a"
        property bool indicatorClickable: false
        signal labelPercentageClicked()
        signal percentageClicked()
        signal extraTextClicked()
        signal indicatorClicked()

        implicitWidth: labelText.implicitWidth + percentageText.implicitWidth
            + (internalDivider.visible ? internalDivider.width : 0)
            + (extraTextItem.visible ? extraTextItem.implicitWidth : 0)
            + (indicator.visible ? indicator.width : 0)
            + root.spacing * (1 + Number(internalDivider.visible)
                + Number(extraTextItem.visible) + Number(indicator.visible))
        height: root.barHeight

        // Anchor from the right so resizing a percentage never moves its
        // trailing edge away from the pointer while waiting for a Row layout.
        StatusText {
            id: labelText
            anchors.right: percentageText.left
            anchors.rightMargin: root.spacing
            text: statusItem.label + ":"
            color: statusItem.labelColor
        }
        StatusText {
            id: percentageText
            anchors.right: internalDivider.visible ? internalDivider.left : parent.right
            anchors.rightMargin: internalDivider.visible ? root.spacing : 0
            text: statusItem.percentage >= 0 ? statusItem.percentage + "%" : statusItem.fallbackText
            color: statusItem.percentageColor

            MouseArea {
                anchors.fill: parent
                enabled: statusItem.percentageClickable && !statusItem.labelPercentageClickable
                visible: enabled
                hoverEnabled: enabled
                cursorShape: Qt.PointingHandCursor
                onClicked: statusItem.percentageClicked()
                onWheel: wheel => wheel.accepted = false
            }
        }
        MouseArea {
            anchors {
                left: labelText.left
                right: percentageText.right
                top: parent.top
                bottom: parent.bottom
            }
            enabled: statusItem.labelPercentageClickable
            visible: enabled
            hoverEnabled: enabled
            cursorShape: Qt.PointingHandCursor
            onClicked: statusItem.labelPercentageClicked()
            onWheel: wheel => wheel.accepted = false
        }
        InternalDivider {
            id: internalDivider
            anchors.right: extraTextItem.visible ? extraTextItem.left : indicator.left
            anchors.rightMargin: root.spacing
            visible: extraTextItem.visible || indicator.visible
        }
        StatusText {
            id: extraTextItem
            anchors.right: indicator.visible ? indicator.left : parent.right
            anchors.rightMargin: indicator.visible ? root.spacing : 0
            visible: statusItem.extraText.length > 0
            text: statusItem.extraText
            color: statusItem.extraTextColor

            MouseArea {
                anchors.fill: parent
                enabled: statusItem.extraTextClickable
                visible: enabled
                hoverEnabled: enabled
                cursorShape: Qt.PointingHandCursor
                onClicked: statusItem.extraTextClicked()
                onWheel: wheel => wheel.accepted = false
            }
        }
        StatusIndicator {
            id: indicator
            anchors.right: parent.right
            visible: statusItem.showIndicator
            color: statusItem.indicatorColor

            MouseArea {
                anchors.fill: parent
                enabled: statusItem.indicatorClickable
                visible: enabled
                hoverEnabled: enabled
                cursorShape: Qt.PointingHandCursor
                onClicked: statusItem.indicatorClicked()
            }
        }
    }

    component StatusIndicator: Rectangle {
        y: (root.barHeight - height) / 2
        width: root.capitalHeight
        height: width
    }

    component StatusDivider: Rectangle {
        y: (root.barHeight - height) / 2
        width: 3
        height: root.capitalHeight
        color: "#18181b" // zinc-900
    }

    component InternalDivider: StatusDivider {
        height: 10
    }

    component WorkspaceButton: Rectangle {
        id: button

        required property string label
        property bool active: false
        signal clicked()

        width: root.barHeight
        height: root.barHeight
        color: active
            ? (mouseArea.containsMouse ? "#e4e4e7" : "#fafafa") // zinc-200 / zinc-50
            : (mouseArea.containsMouse ? "#27272a" : "#09090b") // zinc-800 / zinc-950

        TextMetrics {
            id: labelMetrics
            font: root.textFont
            text: button.label
            renderType: Text.NativeRendering
        }

        BarText {
            readonly property rect glyphBounds: labelMetrics.tightBoundingRect
            // Balance the horizontal weight of Scientifica's asymmetric digits.
            readonly property int opticalOffsetX: font.family === "scientifica" && font.pixelSize === 22
                ? ({ "3": -1, "4": 1, "7": -1 })[button.label] ?? 0
                : 0

            x: Math.round((button.width - glyphBounds.width) / 2 - glyphBounds.x) + opticalOffsetX
            y: Math.round((button.height - glyphBounds.height) / 2 - glyphBounds.y - baselineOffset)
            text: button.label
            color: button.active ? "#09090b" : "#fafafa"
        }

        MouseArea {
            id: mouseArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: button.clicked()
        }
    }

    component NumberedWorkspaceButton: WorkspaceButton {
        required property int number
        readonly property var workspace: root.primaryWorkspaces
            .find(workspace => workspace.number === number)

        label: String(number)
        visible: workspace !== undefined
        active: workspace?.active ?? false
        onClicked: {
            if (workspace)
                I3.dispatch("workspace --no-auto-back-and-forth " + JSON.stringify(workspace.name));
        }
    }

    function createWorkspace(outputName) {
        const number = root.nextWorkspaceNumber;
        if (!root.workspaceCreationAllowed)
            return;
        I3.dispatch("workspace " + number + " output " + JSON.stringify(outputName)
            + "; workspace --no-auto-back-and-forth number " + number);
    }

    FileView {
        id: primaryOutput
        path: Quickshell.env("XDG_RUNTIME_DIR") + "/sway-outputs/primary-output"
        blockLoading: true
        watchChanges: true
        onFileChanged: reload()
    }

    FileView {
        id: previewOutput
        path: Quickshell.env("XDG_RUNTIME_DIR") + "/quickshell-preview-output"
        printErrors: false
        watchChanges: true
        onFileChanged: reload()
    }

    Scope {
        id: recordingState

        readonly property string destination: recordingMarker.loaded ? recordingMarker.text().trim() : ""
        property string confirmedDestination: ""
        readonly property bool active: destination !== "" && destination === confirmedDestination
        onDestinationChanged: confirmedDestination = ""

        FileView {
            id: recordingMarker
            path: Quickshell.env("XDG_RUNTIME_DIR") + "/sway-screen-recording"
            printErrors: false
            watchChanges: true
            onFileChanged: reload()
        }

        // wf-recorder opens the MP4 only after receiving a frame from the compositor.
        Process {
            id: recordingProbe
            property string checkedDestination: ""
            command: ["/usr/bin/test", "-f", checkedDestination]
            onExited: exitCode => {
                if (exitCode === 0 && checkedDestination === recordingState.destination)
                    recordingState.confirmedDestination = checkedDestination;
            }
        }

        Timer {
            interval: 150
            repeat: true
            triggeredOnStart: true
            running: recordingState.destination !== "" && !recordingState.active
            onTriggered: {
                if (recordingProbe.running)
                    return;

                recordingProbe.checkedDestination = recordingState.destination;
                recordingProbe.running = true;
            }
        }
    }

    SystemClock {
        id: clock
        precision: SystemClock.Minutes
    }

    SystemStats {
        id: systemStats
    }

    MediaUsage {
        id: mediaUsage
    }

    WorkspaceOccupancy {
        id: workspaceOccupancy
    }

    Variants {
        model: root.barScreens

        PanelWindow {
            id: bar

            required property var modelData
            screen: modelData

            anchors {
                top: true
                left: true
                right: true
            }

            margins {
                top: root.screenMargin
                left: root.screenMargin
                right: root.screenMargin
            }

            implicitHeight: root.barHeight
            color: "#09090b" // zinc-950

            Row {
                id: workspaceButtons

                visible: bar.screen?.name === root.primaryOutputName
                anchors {
                    left: parent.left
                    top: parent.top
                }
                spacing: 0

                NumberedWorkspaceButton { number: 1 }
                NumberedWorkspaceButton { number: 2 }
                NumberedWorkspaceButton { number: 3 }
                NumberedWorkspaceButton { number: 4 }
                NumberedWorkspaceButton { number: 5 }
                NumberedWorkspaceButton { number: 6 }
                NumberedWorkspaceButton { number: 7 }
                NumberedWorkspaceButton { number: 8 }
                NumberedWorkspaceButton { number: 9 }

                WorkspaceButton {
                    visible: root.workspaceCreationAllowed
                    label: "+"
                    onClicked: root.createWorkspace(root.primaryOutputName)
                }
            }

            Item {
                id: statusItems

                anchors {
                    left: parent.left
                    right: parent.right
                    rightMargin: root.spacing
                    top: parent.top
                }
                height: root.barHeight

                Row {
                    id: captureIndicators
                    anchors.right: captureDivider.left
                    anchors.rightMargin: root.spacing
                    height: root.barHeight
                    spacing: root.spacing

                    StatusIndicator {
                        id: recordingIndicator

                        property bool blinkOn: true

                        visible: recordingState.active
                        color: blinkOn ? "#ef4444" : "#18181b" // red-500 / zinc-900
                        onVisibleChanged: blinkOn = true

                        Timer {
                            interval: 1000
                            repeat: true
                            running: recordingIndicator.visible
                            onTriggered: recordingIndicator.blinkOn = !recordingIndicator.blinkOn
                        }
                    }
                    StatusIndicator {
                        visible: mediaUsage.cameraInUse
                        color: "#22c55e" // green-500
                    }
                    StatusIndicator {
                        visible: mediaUsage.microphoneInUse
                        color: "#f97316" // orange-500
                    }
                }
                StatusDivider {
                    id: captureDivider
                    anchors.right: cpuStatus.left
                    anchors.rightMargin: root.spacing
                    visible: captureIndicators.width > 0
                }
                StatusItem {
                    id: cpuStatus
                    anchors.right: cpuDivider.left
                    anchors.rightMargin: root.spacing
                    label: "CPU"
                    percentage: systemStats.cpuPercent
                    percentageColor: root.highValueColor(percentage)
                    extraText: systemStats.cpuTemperature === null ? "--°C" : systemStats.cpuTemperature + "°C"
                    extraTextColor: root.highValueColor(systemStats.cpuTemperature)
                }
                StatusDivider {
                    id: cpuDivider
                    anchors.right: ramStatus.left
                    anchors.rightMargin: root.spacing
                }
                StatusItem {
                    id: ramStatus
                    anchors.right: ramDivider.left
                    anchors.rightMargin: root.spacing
                    label: "RAM"
                    percentage: systemStats.ramPercent
                    percentageColor: root.highValueColor(percentage)
                }
                StatusDivider {
                    id: ramDivider
                    anchors.right: networkStatus.left
                    anchors.rightMargin: root.spacing
                }
                StatusItem {
                    id: networkStatus
                    anchors.right: networkDivider.left
                    anchors.rightMargin: root.spacing
                    label: "Network"
                    percentage: systemStats.wifiPercent
                    fallbackText: systemStats.networkFallbackText
                    percentageColor: percentage < 0 ? "#fafafa"
                        : percentage < 25 ? "#ef4444" : percentage < 50 ? "#f97316" : "#fafafa"
                    labelPercentageClickable: true
                    onLabelPercentageClicked: Quickshell.execDetached(["foot", "nmtui"])
                }
                StatusDivider {
                    id: networkDivider
                    anchors.right: volumeStatus.left
                    anchors.rightMargin: root.spacing
                }
                StatusItem {
                    id: volumeStatus
                    anchors.right: volumeDivider.left
                    anchors.rightMargin: root.spacing
                    label: "Volume"
                    percentage: systemStats.volumePercent
                    percentageColor: systemStats.volumePercent === 0 ? "#71717a" : "#fafafa" // zinc-500 / zinc-50
                    labelPercentageClickable: true
                    onLabelPercentageClicked: systemStats.toggleMute()
                    extraText: !systemStats.canCycleAudioOutput ? ""
                        : systemStats.audioOutputNumber > 0 ? String(systemStats.audioOutputNumber) : "--"
                    extraTextClickable: true
                    onExtraTextClicked: systemStats.cycleAudioOutput()

                    MouseArea {
                        anchors.fill: parent
                        // Keep the clickable texts above this scroll area.
                        z: -1
                        acceptedButtons: Qt.NoButton

                        onWheel: wheel => {
                            const delta = wheel.angleDelta.y || wheel.pixelDelta.y;
                            if (delta === 0) {
                                wheel.accepted = false;
                                return;
                            }

                            systemStats.adjustVolume(delta > 0 ? 2 : -2);
                            wheel.accepted = true;
                        }
                    }
                }
                StatusDivider {
                    id: volumeDivider
                    anchors.right: batteryStatus.visible ? batteryStatus.left : dateTimeStatus.left
                    anchors.rightMargin: root.spacing
                }
                StatusItem {
                    id: batteryStatus
                    anchors.right: batteryDivider.left
                    anchors.rightMargin: root.spacing
                    visible: systemStats.batteryPresent
                    label: "Battery"
                    percentage: systemStats.batteryPercent
                    labelColor: systemStats.batteryLabelColor
                    percentageColor: systemStats.batteryPercentageColor
                    labelPercentageClickable: true
                    onLabelPercentageClicked: systemStats.togglePowerProfile()
                    showIndicator: systemStats.batteryPlugged
                    indicatorColor: systemStats.batteryIndicatorColor
                    indicatorClickable: systemStats.batteryHealthMode >= 0
                    onIndicatorClicked: systemStats.toggleBatteryHealthMode()
                }
                StatusDivider {
                    id: batteryDivider
                    anchors.right: dateTimeStatus.left
                    anchors.rightMargin: root.spacing
                    visible: batteryStatus.visible
                }
                Row {
                    id: dateTimeStatus
                    anchors.right: parent.right
                    height: root.barHeight
                    spacing: root.spacing

                    StatusText {
                        text: Qt.formatDateTime(clock.date, "dd-MM-yyyy")
                    }
                    InternalDivider {}
                    StatusText {
                        text: Qt.formatDateTime(clock.date, "hh:mm")
                    }
                }
            }
        }
    }
}
