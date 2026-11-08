use std::{
    collections::BTreeSet,
    ffi::OsString,
    io,
    path::Path,
    process::{Child, Command, Stdio},
};

/// Launch a user command independently of the launcher's service lifecycle.
/// Transient services preserve the command's environment and working directory,
/// keep all its descendants alive, and give it independent journal streams.
pub fn spawn(command: &mut Command) -> io::Result<Child> {
    let systemd_run = Path::new("/usr/bin/systemd-run");
    if !Path::new("/run/systemd/system").exists() || !systemd_run.exists() {
        return command.spawn();
    }

    let mut launch = Command::new(systemd_run);
    launch.args([
        "--user", "--quiet", "--collect", "--service-type=exec",
        "--property=ExitType=cgroup", "--expand-environment=no", "--same-dir",
    ]);
    if let Some(directory) = command.get_current_dir() {
        launch.current_dir(directory);
    }

    let mut names: BTreeSet<OsString> = std::env::vars_os().map(|(name, _)| name).collect();
    for (name, value) in command.get_envs() {
        if let Some(value) = value {
            launch.env(name, value);
            names.insert(name.into());
        } else {
            launch.env_remove(name);
            names.remove(name);
        }
    }
    for name in names {
        // The new service receives its own manager-provided descriptors and
        // metadata; don't propagate the launcher's invocation or journal stream.
        if matches!(name.to_str(), Some(
            "INVOCATION_ID" | "JOURNAL_STREAM" | "SYSTEMD_EXEC_PID" |
            "NOTIFY_SOCKET" | "WATCHDOG_PID" | "WATCHDOG_USEC" |
            "LISTEN_PID" | "LISTEN_FDS" | "LISTEN_FDNAMES" |
            "MANAGERPID" | "MANAGERPIDFD"
        )) {
            continue;
        }
        let mut option = OsString::from("--setenv=");
        option.push(name);
        launch.arg(option);
    }
    launch.arg("--").arg(command.get_program()).args(command.get_args());
    launch.stdin(Stdio::null()).spawn()
}
