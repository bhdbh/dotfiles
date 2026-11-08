use std::{collections::HashMap, env, fs, io, path::PathBuf, sync::Arc, time::{Duration, Instant}};

use futures::StreamExt;
use globber::Pattern;
use index::{GetPath, Index};
use inotify::{EventMask, Inotify, WatchDescriptor};
use kidex_common::{IndexEntry, IpcCommand, IpcResponse, DEFAULT_SOCKET};
use serde::{de::Error, Deserialize, Deserializer};
use signal_hook::consts::TERM_SIGNALS;
use signal_hook_tokio::Signals;
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufStream},
    net::UnixListener,
    sync::{
        mpsc::{self, Receiver, Sender},
        Mutex,
    },
};

mod index;
mod mounts;
mod ignore_rules;

#[derive(Clone, Deserialize)]
pub struct Config {
    directories: Vec<WatchDir>,
    #[serde(deserialize_with = "parse_pattern_vec")]
    ignored: Vec<Pattern>,
    #[serde(default)]
    monitor_usb_mounts: bool,
    #[serde(default)]
    drive_links: Option<WatchDir>,
    #[serde(default, deserialize_with = "deserialize_optional_path")]
    ignore_file: Option<PathBuf>,
    #[serde(skip)]
    active_usb_roots: Vec<PathBuf>,
    #[serde(skip)]
    active_drive_roots: Vec<PathBuf>,
}

fn with_usb_mounts(config: &Config, mounts: &[mounts::UsbMount]) -> Config {
    let mut effective = config.clone();
    effective.active_usb_roots.clear();
    effective.active_drive_roots.clear();
    if config.monitor_usb_mounts {
        for mount in mounts {
            effective.active_usb_roots.push(mount.path.clone());
        }
    }
    if let Some(links) = &config.drive_links {
        if let Ok(entries) = fs::read_dir(&links.path) {
            let mut roots: Vec<_> = entries.filter_map(Result::ok).filter_map(|entry| {
                if !entry.file_type().ok()?.is_symlink() { return None; }
                let target = fs::canonicalize(entry.path()).ok()?;
                if !target.is_dir() || !mounts.iter().any(|mount| mount.path == target) {
                    return None;
                }
                Some(entry.path())
            }).collect();
            roots.sort();
            for path in roots {
                effective.active_drive_roots.push(path.clone());
                let mut watch = links.clone();
                watch.path = path;
                effective.directories.push(watch);
            }
        }
    }
    effective
}

/// Custom parser to handle the patterns
fn parse_pattern_vec<'de, D>(deserializer: D) -> Result<Vec<Pattern>, D::Error>
where
    D: Deserializer<'de>,
{
    let vec = Vec::<String>::deserialize(deserializer)?;
    let mut final_vec = Vec::new();

    for string in vec {
        final_vec.push(match Pattern::new(&string) {
            Ok(pattern) => pattern,
            Err(why) => {
                return Err(D::Error::custom(why));
            }
        });
    }

    Ok(final_vec)
}

fn deserialize_path<'de, D>(deserializer: D) -> Result<PathBuf, D::Error>
where
    D: Deserializer<'de>,
{
    let s = String::deserialize(deserializer)?;
    return expanduser::expanduser(&s).map_err(serde::de::Error::custom);
}

fn deserialize_optional_path<'de, D: Deserializer<'de>>(deserializer: D) -> Result<Option<PathBuf>, D::Error> {
    Option::<String>::deserialize(deserializer)?.map(|path|
        expanduser::expanduser(&path).map_err(serde::de::Error::custom)).transpose()
}

/// Describes a directory that is watched for changes
#[derive(Clone, Debug, Deserialize)]
pub struct WatchDir {
    /// Path of the directory
    #[serde(deserialize_with = "deserialize_path")]
    path: PathBuf,
    /// Ignored patterns
    #[serde(deserialize_with = "parse_pattern_vec")]
    ignored: Vec<Pattern>,
    /// Recursively watch directories
    recurse: bool,
}

/// A "top-level" object representing a directory being watched, and keeping track of it's children
#[derive(Debug, Clone)]
pub struct DirectoryIndex {
    path: PathBuf,
    children: HashMap<PathBuf, ChildIndex>,
    /// Reference to the underlying `WatchDir` object containing some
    /// configuration details
    watch_dir: Arc<WatchDir>,
    parent: Option<WatchDescriptor>,
}

/// A child of an indexed directory
#[derive(Debug, Clone, Hash, Eq, PartialEq)]
pub enum ChildIndex {
    File {},
    Directory { descriptor: Option<WatchDescriptor> },
}

/// Sent from the IPC listener to the main event loop
#[derive(Debug)]
enum EventLoopMsg {
    FullIndex,
    Quit,
    Reload,
}

#[tokio::main]
async fn main() {
    env_logger::init();

    let config_path = env::var("XDG_CONFIG_HOME")
        .map(|config_home| format!("{config_home}/kidex.ron"))
        .unwrap_or(format!(
            "{}/.config/kidex.ron",
            match env::var("HOME") {
                Ok(home) => home,
                Err(why) => {
                    log::error!("Failed to determine home directory: {}", why);
                    return;
                }
            }
        ));

    let mut inotify = Inotify::init().expect("Failed to init inotify");
    let mut config: Config = ron::from_str(&fs::read_to_string(&config_path).unwrap()).unwrap();
    let mut usb_mounts = if config.monitor_usb_mounts { mounts::discover().unwrap_or_default() } else { Vec::new() };
    let mut effective_config = with_usb_mounts(&config, &usb_mounts);
    let mut last_mount_check = Instant::now();
    let mut ignore_fingerprint = ignore_rules::fingerprint(config.ignore_file.as_deref());
    let mut index = Index::new();

    index
        .full_index(&mut inotify, &effective_config)
        .expect("Failed to complete initial index!");

    let index = Arc::new(Mutex::new(index));

    let socket_path = env::var("SOCKET_PATH").unwrap_or(DEFAULT_SOCKET.to_string());
    // Delete the socket file if it is lingering around
    let _ = fs::remove_file(&socket_path);

    let listener = UnixListener::bind(&socket_path).expect("Failed to create unix socket listener");

    // Create necessary communication channels
    let (ipc_tx, mut events_rx) = mpsc::channel::<EventLoopMsg>(32);
    let (events_tx, ipc_rx) = mpsc::channel::<()>(32);

    // Spawn task listening for termination signals
    tokio::spawn(signal_task(
        ipc_tx.clone(),
        Signals::new(TERM_SIGNALS).unwrap(),
    ));
    // Spawn IPC task
    tokio::spawn(ipc_task(listener, index.clone(), ipc_tx, ipc_rx));

    // Buffer used by inotify
    let mut buffer = [0; 1024];

    'event_loop: loop {
        // Sleep for a while to not keep the thread busy all the time
        tokio::time::sleep(Duration::from_millis(10)).await;
        if last_mount_check.elapsed() >= Duration::from_secs(1) {
            last_mount_check = Instant::now();
            let fingerprint = ignore_rules::fingerprint(config.ignore_file.as_deref());
            let current = if config.monitor_usb_mounts {
                mounts::discover().unwrap_or_else(|why| {
                    log::error!("Failed to inspect USB mounts: {why}");
                    usb_mounts.clone()
                })
            } else { Vec::new() };
            let updated = with_usb_mounts(&config, &current);
            if current != usb_mounts || updated.active_drive_roots != effective_config.active_drive_roots
                || fingerprint != ignore_fingerprint {
                    log::info!("USB mounts, drive links or ignore rules changed; updating index");
                    usb_mounts = current;
                    effective_config = updated;
                    ignore_fingerprint = fingerprint;
                    index.lock().await.full_index(&mut inotify, &effective_config).unwrap();
                }
        }
        match events_rx.try_recv() {
            Ok(event) => match event {
                EventLoopMsg::FullIndex => {
                    index
                        .lock()
                        .await
                        .full_index(&mut inotify, &effective_config)
                        .unwrap();
                }
                EventLoopMsg::Quit => break,
                EventLoopMsg::Reload => {
                    match ron::from_str::<Config>(&fs::read_to_string(&config_path).unwrap())
                    {
                        Ok(new_config) => {
                            config = new_config;
                            ignore_fingerprint = ignore_rules::fingerprint(config.ignore_file.as_deref());
                            usb_mounts = if config.monitor_usb_mounts { mounts::discover().unwrap_or_default() } else { Vec::new() };
                            effective_config = with_usb_mounts(&config, &usb_mounts);
                            // Reindex everything if the config was reloaded
                            index
                                .lock()
                                .await
                                .full_index(&mut inotify, &effective_config)
                                .unwrap();
                        }
                        Err(why) => {
                            log::error!("Failed to load config: {}", why);
                        }
                    }
                }
            },
            Err(mpsc::error::TryRecvError::Empty) => (),
            Err(why) => {
                log::error!("Failed receive message from IPC task: {}", why);
            }
        }

        let events = match inotify.read_events(&mut buffer) {
            Ok(events) => events,
            // The next event(s) is/are not ready yet if the error is WouldBlock
            // so it counterintuitively is not actually an error.
            Err(why) => {
                if why.kind() != io::ErrorKind::WouldBlock {
                    log::error!("Error reading inotify events: {}", why);
                }
                continue 'event_loop;
            }
        };

        for event in events {
            let mut index = index.lock().await;

            if index.inner.get(&event.wd).is_none() {
                log::warn!("Event received from nonexistent watcher: {:?}", event.name);
                continue;
            }

            let path = if let Some(name) = event.name {
                PathBuf::from(name)
            } else {
                log::warn!("Event received with no name!");
                continue;
            };

            let path_str = format!(
                "{}/{}",
                index.inner.get_path(&event.wd).display(),
                path.display()
            );

            if event.mask.contains(EventMask::CREATE) {
                log::info!("File created: {}", path_str);
                index.create_index(&mut inotify, &path, &event);
            }
            if event.mask.contains(EventMask::DELETE) {
                log::info!("File deleted: {}", path_str);
                index.remove_index(&mut inotify, &path, &event);
            }
            if event.mask.contains(EventMask::MOVED_FROM) {
                log::info!("File moved from: {}", path_str);
                index.remove_index(&mut inotify, &path, &event);
            }
            if event.mask.contains(EventMask::MOVED_TO) {
                log::info!("File moved to: {}", path_str);
                index.create_index(&mut inotify, &path, &event);
            }
        }
    }

    if let Err(why) = index.lock().await.clear_index(&mut inotify) {
        log::error!("Failed to clear index: {}", why);
    }

    events_tx.send(()).await.unwrap();
}

async fn signal_task(signal_tx: Sender<EventLoopMsg>, mut signals: Signals) {
    // Wait for a signal to arrive, we only listen for termination signals so any
    // received event will be one we should act on
    signals.next().await;

    log::info!("Termination signal received! Quitting...");

    signal_tx.send(EventLoopMsg::Quit).await.unwrap();
}

async fn ipc_task(
    listener: UnixListener,
    index: Arc<Mutex<Index>>,
    ipc_tx: Sender<EventLoopMsg>,
    mut ipc_rx: Receiver<()>,
) {
    loop {
        tokio::select! {
            Ok((stream, _)) = listener.accept() => {
                let mut buf = Vec::new();
                let mut stream = BufStream::new(stream);
                stream.read_until(0x0, &mut buf).await.unwrap();
                buf.pop(); // Remove the delimiting null byte

                match serde_json::from_slice::<IpcCommand>(&buf).unwrap() {
                    IpcCommand::FullIndex => {
                        ipc_tx.send(EventLoopMsg::FullIndex).await.unwrap();
                        if let Err(why) = stream.write_all(&serde_json::to_vec(&IpcResponse::Success).unwrap()).await {
                            log::error!("Error writing reply to stream: {}", why);
                        }
                    }
                    IpcCommand::Quit => {
                        ipc_tx.send(EventLoopMsg::Quit).await.unwrap();
                        if let Err(why) = stream.write_all(&serde_json::to_vec(&IpcResponse::Success).unwrap()).await {
                            log::error!("Error writing reply to stream: {}", why);
                        }
                        break;
                    }
                    IpcCommand::Reload => {
                        ipc_tx.send(EventLoopMsg::Reload).await.unwrap();
                        if let Err(why) = stream.write_all(&serde_json::to_vec(&IpcResponse::Success).unwrap()).await {
                            log::error!("Error writing reply to stream: {}", why);
                        }
                    }
                    IpcCommand::GetIndex(path) => {
                        let index = index.lock().await;
                        let paths = match path {
                            Some(path) => {
                                index
                                    .inner
                                    .iter()
                                    .find(|(desc, _)| index.inner.get_path(&desc) == path)
                                    .map(|(desc, _)| index.traverse(desc.clone())
                                        .into_iter()
                                        .flat_map(|(desc, dir)| {
                                            let parent_path = index.inner.get_path(&desc);
                                            dir.children.into_iter().map(move |(path, child)|
                                                IndexEntry {
                                                    path: parent_path.iter().chain(path.iter()).collect(),
                                                    directory: matches!(child, ChildIndex::Directory {..})
                                                }
                                            )
                                        })
                                        .collect::<Vec<_>>()
                                    )
                            }
                            None => Some(index.entries()),
                        };

                        let buf = serde_json::to_vec(&match paths {
                            Some(paths) => IpcResponse::Index(paths),
                            None => IpcResponse::NotFound,
                        }).unwrap();

                        stream.write_all(&buf).await.unwrap();
                    },
                }
                stream.flush().await.unwrap();
            }
            _ = ipc_rx.recv() => break
        }
    }
}

#[cfg(test)]
mod usb_index_tests {
    use super::*;
    use std::os::unix::fs::symlink;

    fn fixture(home_mount: bool) -> (mounts::tests::Fixture, PathBuf, PathBuf, Config, Vec<mounts::UsbMount>) {
        let fixture = mounts::tests::Fixture::new();
        let home = fixture.0.join("home");
        let usb = if home_mount { home.join("physical-volume") } else { fixture.0.join("USB Files") };
        let link = home.join("Drives/Files");
        fs::create_dir_all(link.parent().unwrap()).unwrap();
        fs::create_dir_all(usb.join("project/subdir")).unwrap();
        fs::write(home.join("local.txt"), "local").unwrap();
        fs::write(usb.join("project/report.txt"), "usb").unwrap();
        fs::write(usb.join(".hidden.txt"), "hidden").unwrap();
        symlink(&usb, &link).unwrap();
        symlink(&usb, usb.join("project/loop")).unwrap();
        let block = mounts::tests::block_fixture(&fixture.0);
        let table = format!("10 1 8:1 / {} rw - exfat /dev/sda1 rw", usb.display().to_string().replace(' ', "\\040"));
        let mounted = mounts::discover_from(&table, &block);
        let config: Config = ron::from_str(&format!(
            "Config(monitor_usb_mounts: true, drive_links: Some(WatchDir(path: {:?}, recurse: true, ignored: [])), ignored: [\".*\"], directories: [WatchDir(path: {:?}, recurse: true, ignored: [])])",
            link.parent().unwrap().to_str().unwrap(), home.to_str().unwrap()
        )).unwrap();
        (fixture, usb, link, config, mounted)
    }

    #[test]
    fn indexes_contents_through_the_link_and_cleans_up_after_unmount() {
        let (_fixture, usb, link, config, mounted) = fixture(false);
        let mut inotify = Inotify::init().unwrap();
        let mut index = Index::new();
        index.full_index(&mut inotify, &with_usb_mounts(&config, &mounted)).unwrap();
        let entries = index.entries();
        assert!(entries.iter().any(|e| e.path == link && e.directory));
        assert!(entries.iter().any(|e| e.path == link.join("project") && e.directory));
        assert!(entries.iter().any(|e| e.path == link.join("project/report.txt") && !e.directory));
        assert!(!entries.iter().any(|e| e.path.starts_with(&usb)));
        assert!(!entries.iter().any(|e| e.path.file_name().unwrap() == ".hidden.txt"));
        assert!(!entries.iter().any(|e| e.path.components().any(|c| c.as_os_str() == "loop")));
        fs::remove_file(&link).unwrap();
        fs::remove_dir(link.parent().unwrap()).unwrap();
        index.full_index(&mut inotify, &with_usb_mounts(&config, &[])).unwrap();
        assert!(!index.entries().iter().any(|e| e.path.starts_with(link.parent().unwrap())));
        assert!(usb.join("project/report.txt").exists());
        index.clear_index(&mut inotify).unwrap();
    }

    #[test]
    fn raw_mount_inside_home_does_not_duplicate_the_drive_contents() {
        let (_fixture, usb, link, config, mounted) = fixture(true);
        let mut inotify = Inotify::init().unwrap();
        let mut index = Index::new();
        index.full_index(&mut inotify, &with_usb_mounts(&config, &mounted)).unwrap();
        let entries = index.entries();
        assert!(!entries.iter().any(|e| e.path.starts_with(&usb)));
        assert_eq!(entries.iter().filter(|e| e.path.file_name().unwrap() == "report.txt").count(), 1);
        assert!(entries.iter().any(|e| e.path == link.join("project/report.txt")));
        index.clear_index(&mut inotify).unwrap();
    }

    #[test]
    fn rejects_links_that_do_not_point_to_a_mounted_usb() {
        let (_fixture, _, link, config, mounted) = fixture(false);
        fs::remove_file(&link).unwrap();
        symlink(link.parent().unwrap().parent().unwrap(), &link).unwrap();
        let effective = with_usb_mounts(&config, &mounted);
        assert!(effective.active_drive_roots.is_empty());
    }

    #[test]
    fn discovers_links_created_after_mount_detection() {
        let (_fixture, usb, link, config, mounted) = fixture(false);
        fs::remove_file(&link).unwrap();
        assert!(with_usb_mounts(&config, &mounted).active_drive_roots.is_empty());
        symlink(&usb, &link).unwrap();
        assert_eq!(with_usb_mounts(&config, &mounted).active_drive_roots, vec![link]);
    }

    #[test]
    fn watches_new_nested_content_renames_and_deletions_through_drive_links() {
        fn events(index: &mut Index, inotify: &mut Inotify) {
            let mut buffer = [0; 8192];
            loop {
                let events = match inotify.read_events(&mut buffer) {
                    Ok(events) => events,
                    Err(why) if why.kind() == io::ErrorKind::WouldBlock => break,
                    Err(why) => panic!("{why}"),
                };
                for event in events {
                    if !index.inner.contains_key(&event.wd) { continue; }
                    let Some(name) = event.name else { continue; };
                    let path = PathBuf::from(name);
                    if event.mask.intersects(EventMask::CREATE | EventMask::MOVED_TO) {
                        index.create_index(inotify, &path, &event);
                    }
                    if event.mask.intersects(EventMask::DELETE | EventMask::MOVED_FROM) {
                        index.remove_index(inotify, &path, &event);
                    }
                }
            }
        }
        let (_fixture, usb, link, config, mounted) = fixture(false);
        let mut inotify = Inotify::init().unwrap();
        let mut index = Index::new();
        index.full_index(&mut inotify, &with_usb_mounts(&config, &mounted)).unwrap();
        fs::create_dir_all(usb.join("project/new/deep")).unwrap();
        fs::write(usb.join("project/new/deep/dynamic.txt"), "new").unwrap();
        fs::write(usb.join("project/.secret"), "hidden").unwrap();
        events(&mut index, &mut inotify);
        assert!(index.entries().iter().any(|e| e.path == link.join("project/new/deep/dynamic.txt")));
        assert!(!index.entries().iter().any(|e| e.path.file_name().unwrap() == ".secret"));
        fs::rename(usb.join("project/new"), usb.join("project/renamed")).unwrap();
        events(&mut index, &mut inotify);
        assert!(index.entries().iter().any(|e| e.path == link.join("project/renamed/deep/dynamic.txt")));
        assert!(!index.entries().iter().any(|e| e.path.starts_with(link.join("project/new"))));
        fs::remove_dir_all(usb.join("project/renamed")).unwrap();
        events(&mut index, &mut inotify);
        assert!(!index.entries().iter().any(|e| e.path.starts_with(link.join("project/renamed"))));
        index.clear_index(&mut inotify).unwrap();
    }
}
