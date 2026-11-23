import QtQuick
import Quickshell

Scope {
    BatteryChargeSync { id: sync }

    function check(condition, message) {
        if (!condition)
            throw new Error(message);
    }

    function reset(mode, status, percentage = 80, plugged = true) {
        sync.cancel();
        sync.mode = mode;
        sync.status = status;
        sync.percentage = percentage;
        sync.plugged = plugged;
    }

    Timer {
        interval: 10
        running: true
        onTriggered: {
            reset(0, "Not charging");
            check(!sync.charging && !sync.active, "No anticipation before confirmation");
            sync.confirm(1);
            check(!sync.active, "Mismatched firmware confirmation");
            sync.confirm(0);
            check(sync.active && sync.charging, "Full charge bridges stale idle status");
            sync.status = "Charging";
            check(!sync.active && sync.charging, "Actual charging settles transition");

            reset(1, "Charging");
            sync.confirm(1);
            check(sync.active && !sync.charging, "80% limit bridges stale charging status");
            sync.status = "Not charging";
            check(!sync.active && !sync.charging, "Actual idle settles transition");

            reset(1, "Charging", 79);
            sync.confirm(1);
            check(!sync.active && sync.charging, "Below 80% charging continues normally");
            reset(1, "Charging");
            sync.confirm(1);
            sync.percentage = 79;
            check(!sync.active && sync.charging, "Falling below the limit cancels transition");

            for (const status of ["Full", "Unknown", "Discharging", ""]) {
                reset(0, status);
                sync.confirm(0);
                check(!sync.active, "Invalid state must not anticipate: " + status);
            }
            for (const percentage of [-1, 100]) {
                reset(0, "Not charging", percentage);
                sync.confirm(0);
                check(!sync.active, "Invalid or full capacity must not anticipate");
            }
            reset(0, "Not charging", 80, false);
            sync.confirm(0);
            check(!sync.active && !sync.charging, "Unplugged confirmation must not anticipate");

            reset(0, "Not charging");
            sync.confirm(0);
            sync.plugged = false;
            check(!sync.active && !sync.charging, "Unplug cancels transition");
            sync.plugged = true;
            check(!sync.active, "Replug cannot restart an old transition");
            sync.confirm(0);
            sync.mode = 1;
            check(!sync.active && !sync.charging, "External mode change cancels transition");
            reset(0, "Not charging");
            sync.confirm(0);
            sync.percentage = 100;
            check(!sync.active, "Reaching full cancels transition");
            reset(0, "Not charging");
            sync.confirm(0);
            sync.status = "Discharging";
            check(!sync.active && !sync.charging, "Discharging cancels transition");

            reset(0, "Not charging");
            sync.confirm(0);
            sync.cancel();
            check(!sync.active && !sync.charging, "New click or failed helper cancels transition");
            reset(1, "Charging");
            sync.confirm(1);
            check(sync.active && !sync.charging, "Fast reversal uses the latest mode");
            sync.cancel();

            reset(0, "Not charging");
            sync.confirm(0);
            check(sync.deadline.interval === 5000, "Transition must be bounded at 5 seconds");
            timeoutCheck.start();
        }
    }

    Timer {
        id: timeoutCheck
        interval: 5200
        onTriggered: {
            check(!sync.active && !sync.charging, "Timeout restores the reported state");
            console.info("PASS battery charge synchronization");
            Qt.quit();
        }
    }
}
