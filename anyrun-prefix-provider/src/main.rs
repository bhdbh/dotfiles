use std::{env, io, path::PathBuf};

use anyrun_interface::{
    HandleResult, Match, PluginRef,
    abi_stable::{self, std_types::RVec},
};
use anyrun_provider_ipc::{CONFIG_DIRS, PLUGIN_PATHS, Calculation, HistoryEntry, Request, Response, Socket};
use clap::{Parser, Subcommand};
use tokio::{
    net::{UnixListener, UnixStream},
    sync::mpsc::{self, Receiver},
};

mod routing;
mod history;
mod calculator;

/// The program providing Anyrun plugin search results
#[derive(Parser)]
#[command(version)]
struct Args {
    #[command(subcommand)]
    command: Command,

    #[arg(short, long)]
    plugins: Vec<PathBuf>,
    #[arg(short, long)]
    config_dir: Option<String>,
}

#[derive(Clone, Subcommand)]
enum Command {
    /// Start listening for connections at the provided socket path
    Socket { path: PathBuf },
    /// Connect to the specified socket
    ConnectTo { path: PathBuf },
}

enum WorkerResult {
    Quit,
    Continue,
}

enum Event {
    Matches {
        matches: Option<RVec<Match>>,
        idx: usize,
    },
    Request(Result<Request, io::Error>),
}

struct PluginState {
    plugin: PluginRef,
    rx: Option<Receiver<RVec<Match>>>,
    query: String,
}

struct State {
    // Has to be a Vec to preserve order
    plugins: Vec<PluginState>,
    config_dir: String,
    history: history::History,
    calculator: calculator::CalculatorHistory,
    last_query: String,
    pending: Option<HistoryEntry>,
}

impl State {
    async fn calculate(&mut self, text: &str) -> Option<Calculation> {
        let expression = text.trim_start().strip_prefix('=')?.trim();
        if expression.is_empty() { return None; }
        self.cancel_pending();
        let plugin = self.plugins.iter().find(|state| state.plugin.info()().name == "Rink")?.plugin;
        for _ in 0..20 {
            if let Some(result) = plugin.get_matches()(format!("={expression}").into()).into_iter().next() {
                let calculation = Calculation { expression: expression.to_owned(), result: result.title.to_string() };
                self.calculator.record(calculation.clone());
                self.history.record(HistoryEntry {
                    plugin: plugin.info()(),
                    selection: Match { description: Some(expression.into()).into(), ..result },
                    query: text.to_owned(),
                });
                return Some(calculation);
            }
            tokio::time::sleep(std::time::Duration::from_millis(25)).await;
        }
        None
    }

    fn cancel_pending(&mut self) {
        let Some(entry) = self.pending.take() else { return };
        let Some(plugin) = self.plugins.iter().find(|state| state.plugin.info()().name == entry.plugin.name) else { return };
        let matches = plugin.plugin.get_matches()(entry.query.into());
        if let Some(cancel) = matches.into_iter().find(|item| {
            matches!(item.title.as_str(), "Cancel" | "Back")
        }) {
            plugin.plugin.handle_selection()(cancel);
        } else {
            plugin.plugin.init()(self.config_dir.clone().into());
        }
    }

    fn handle(&mut self, index: usize, selection: Match) -> Response {
        let plugin = self.plugins[index].plugin;
        let info = plugin.info()();
        let entry = self.pending.take().filter(|entry| entry.plugin.name == info.name)
            .unwrap_or_else(|| HistoryEntry {
                plugin: info.clone(),
                selection: selection.clone(),
                query: self.last_query.clone(),
            });
        let result = plugin.handle_selection()(selection.clone());
        match &result {
            HandleResult::Refresh(true) => self.pending = Some(entry),
            HandleResult::Refresh(false) => (),
            _ if selection.icon.as_ref().map(|icon| icon.as_str()) != abi_stable::std_types::ROption::RSome("dialog-error") => {
                self.history.record(entry);
            }
            _ => (),
        }
        Response::Handled { plugin: info, result }
    }
}

async fn resolve_selection(plugin: PluginRef, entry: &HistoryEntry) -> Option<Match> {
    for _ in 0..20 {
        if let Some(selection) = resolve_once(plugin, entry) {
            return Some(selection);
        }
        tokio::time::sleep(std::time::Duration::from_millis(25)).await;
    }
    None
}

fn resolve_once(plugin: PluginRef, entry: &HistoryEntry) -> Option<Match> {
    let query = routing::plugin_query(&entry.query, &entry.plugin.name)?;
    let matches = plugin.get_matches()(query.into());
    if entry.plugin.name == "Rink" {
        return matches.into_iter().next();
    }
    if let Some(selection) = matching_selection(matches, entry) {
        return Some(selection);
    }
    let fallback = match entry.plugin.name.as_str() {
        "Kidex" => entry.selection.description.as_ref().map(|path| path.to_string())
            .unwrap_or_else(|| entry.selection.title.to_string()),
        "Shell" => format!("$ {}", entry.selection.title),
        _ => entry.selection.title.to_string(),
    };
    matching_selection(plugin.get_matches()(fallback.into()), entry)
}

async fn most_used_matches(plugin: PluginRef, entries: Vec<HistoryEntry>) -> RVec<Match> {
    if entries.is_empty() { return RVec::new() }
    for attempt in 0..2 {
        let mut matches = RVec::new();
        for entry in &entries {
            if entry.plugin.name == "Kidex" && !entry.selection.description.as_ref()
                .map(|path| {
                    if entry.selection.icon.as_ref().map(|icon| icon.as_str())
                        == abi_stable::std_types::ROption::RSome("folder")
                    { std::path::Path::new(path.as_str()).is_dir() }
                    else { std::path::Path::new(path.as_str()).is_file() }
                }).unwrap_or(false)
            {
                continue;
            }
            if let Some(selection) = resolve_once(plugin, entry) {
                matches.push(selection);
                if matches.len() == 6 { break }
            }
        }
        if !matches.is_empty() || attempt == 1 { return matches }
        // Plugin initialization is asynchronous, so allow its initial state to arrive.
        tokio::time::sleep(std::time::Duration::from_millis(25)).await;
    }
    RVec::new()
}

fn matching_selection(matches: RVec<Match>, entry: &HistoryEntry) -> Option<Match> {
    let mut candidates = matches.into_iter().filter(|item| {
        item.title == entry.selection.title
            && item.description == entry.selection.description
            && item.icon == entry.selection.icon
    });
    let selection = candidates.next()?;
    // Avoid replaying a different item when two entries look identical.
    candidates.next().is_none().then_some(selection)
}

#[tokio::main]
async fn main() {
    let args = Args::parse();

    let user_dir = env::var("XDG_CONFIG_HOME")
        .map(|c| format!("{c}/anyrun"))
        .or_else(|_| env::var("HOME").map(|h| format!("{h}/.config/anyrun")))
        .unwrap();

    let config_dir = args
        .config_dir
        .map(Some)
        .unwrap_or_else(|| {
            if PathBuf::from(&user_dir).exists() {
                Some(user_dir.clone())
            } else {
                CONFIG_DIRS
                    .iter()
                    .map(|path| path.to_string())
                    .find(|path| PathBuf::from(path).exists())
            }
        })
        .unwrap_or(CONFIG_DIRS[0].into());

    let mut plugin_dirs = vec![
        env::var("XDG_DATA_HOME")
            .map(|d| format!("{d}/anyrun/plugins"))
            .or_else(|_| env::var("HOME").map(|h| format!("{h}/.local/share/anyrun/plugins")))
            .unwrap(),
        format!("{user_dir}/plugins"),
    ];

    // Used mainly in Nix packaging
    if let Ok(path) = env::var("ANYRUN_PLUGINS") {
        plugin_dirs.push(path);
    }

    plugin_dirs.extend(PLUGIN_PATHS.iter().map(|p| p.to_string()));

    let mut state = State {
        plugins: Vec::new(),
        config_dir,
        history: history::History::load(),
        calculator: calculator::CalculatorHistory::load(),
        last_query: String::new(),
        pending: None,
    };

    for plugin in &args.plugins {
        let path = if plugin.is_absolute() {
            plugin.clone()
        } else {
            let Some(path) = plugin_dirs.iter().find_map(|dir| {
                let mut path = PathBuf::from(dir);
                path.extend(plugin);

                if path.exists() {
                    return Some(path);
                }

                let mut path = PathBuf::from(dir);
                path.extend(&PathBuf::from(format!(
                    "lib{}.so",
                    plugin.to_string_lossy().replace("-", "_")
                )));

                if path.exists() {
                    return Some(path);
                }

                None
            }) else {
                eprintln!(
                    "[anyrun-provider] Failed to locate library for plugin {}, not loading",
                    plugin.display()
                );
                continue;
            };

            path
        };

        let Ok(header) = abi_stable::library::lib_header_from_path(&path) else {
            eprintln!(
                "[anyrun-provider] Failed to load plugin `{}` header",
                path.display()
            );
            continue;
        };

        let Ok(plugin) = header.init_root_module::<PluginRef>() else {
            eprintln!(
                "[anyrun-provider] Failed to init plugin `{}` root module",
                path.display()
            );
            continue;
        };

        plugin.init()(state.config_dir.clone().into());

        state.plugins.push(PluginState { plugin, rx: None, query: String::new() });
    }

    state.plugins.sort_by_key(|plugin| plugin.plugin.info()().name == "Actions");

    match args.command {
        Command::Socket { path } => {
            let listener = UnixListener::bind(path).unwrap();

            while let Ok((stream, _)) = listener.accept().await {
                match worker(stream, &mut state).await {
                    Ok(res) => match res {
                        WorkerResult::Quit => break,
                        WorkerResult::Continue => (),
                    },
                    Err(why) => eprintln!("[anyrun-provider] Worker returned an error: {why}"),
                }
            }
        }
        Command::ConnectTo { path } => {
            let stream = UnixStream::connect(path).await.unwrap();

            if let Err(why) = worker(stream, &mut state).await {
                eprintln!("[anyrun-provider] Worker returned an error: {why}");
            }
        }
    }
}

/// Returns whether or not the provider should quit
async fn worker(stream: UnixStream, state: &mut State) -> io::Result<WorkerResult> {
    let mut socket = Socket::new(stream);

    socket
        .send(&Response::Ready {
            info: state
                .plugins
                .iter()
                .map(|plugin_state| plugin_state.plugin.info()())
                .collect(),
        })
        .await?;

    loop {
        let rx_futures = state
            .plugins
            .iter_mut()
            .enumerate()
            .filter_map(|(idx, plugin)| {
                plugin
                    .rx
                    .as_mut()
                    .map(|rx| Box::pin(async move { (rx.recv().await, idx) }))
            })
            .collect::<Vec<_>>();

        let event = if !rx_futures.is_empty() {
            tokio::select! {
                ((matches, idx), _, _) = futures::future::select_all(rx_futures.into_iter()) => {
                    Event::Matches { matches, idx }
                }
                req = socket.recv() => {
                    Event::Request(req)
                }
            }
        } else {
            // Drop it to drop the previous mutable borrow
            std::mem::drop(rx_futures);
            Event::Request(socket.recv().await)
        };

        match event {
            Event::Request(Ok(request)) => match request {
                Request::Calculate { text } => {
                    let calculation = state.calculate(&text).await;
                    socket.send(&Response::Calculated { text, calculation }).await?;
                }
                Request::History => {
                    socket.send(&Response::History { entries: state.history.entries.clone() }).await?;
                }
                Request::Replay { index } => {
                    let Some(entry) = state.history.entries.get(index).cloned() else { continue };
                    state.cancel_pending();
                    let Some(plugin_index) = state.plugins.iter().position(|plugin| plugin.plugin.info()().name == entry.plugin.name) else { continue };
                    if let Some(selection) = resolve_selection(state.plugins[plugin_index].plugin, &entry).await {
                        state.last_query = entry.query;
                        let response = state.handle(plugin_index, selection);
                        socket.send(&response).await?;
                    } else {
                        eprintln!("[history] Recent selection is no longer available");
                    }
                }
                Request::Reset => {
                    state.pending = None;
                    for plugin_state in &mut state.plugins {
                        plugin_state.plugin.init()(state.config_dir.clone().into());

                        plugin_state.rx = None;
                    }
                }
                Request::Query { text } => {
                    if state.pending.as_ref().is_some_and(|entry| entry.query != text) {
                        state.cancel_pending();
                    }
                    state.last_query = text.clone();
                    let frequent_plugin = if state.pending.is_none() {
                        match text.trim() {
                            ">" => Some(('>', "Applications")),
                            "#" => Some(('#', "Kidex")),
                            "@" => Some(('@', "Kidex")),
                            "$" => Some(('$', "Shell")),
                            _ => None,
                        }
                    } else { None };
                    for plugin_state in &mut state.plugins {
                        plugin_state.query = text.clone();
                        let (tx, rx) = mpsc::channel(10);
                        let plugin = plugin_state.plugin;
                        let info = plugin.info()();
                        let query = routing::plugin_query(&text, &info.name).map(str::to_owned);
                        let frequent = frequent_plugin.filter(|(_, name)| *name == info.name.as_str())
                            .map(|(prefix, _)| state.history.most_used(prefix));
                        let calculations = (text.trim_start().starts_with('=') && info.name == "Rink")
                            .then(|| state.calculator.matches());

                        tokio::spawn(async move {
                            let matches = if let Some(calculations) = calculations {
                                calculations
                            } else { match (frequent, query) {
                                (Some(entries), _) => most_used_matches(plugin, entries).await,
                                (None, Some(text)) => plugin.get_matches()(text.into()),
                                (None, None) => RVec::new(),
                            }};
                            // Every plugin must respond, including suppressed ones, to clear
                            // results left by the previous query in the UI.
                            let _ = tx.send(matches).await;
                        });

                        plugin_state.rx = Some(rx);
                    }
                }
                Request::Handle { plugin, selection } => {
                    let Some(index) = state
                        .plugins
                        .iter()
                        .position(|plugin_state| plugin_state.plugin.info()() == plugin) else { continue };
                    let response = state.handle(index, selection);
                    socket.send(&response).await?;
                }
                Request::Quit => {
                    return Ok(WorkerResult::Quit);
                }
            },
            Event::Request(Err(why)) => match why.kind() {
                // This occurs when a subscriber disconnects
                io::ErrorKind::UnexpectedEof => {
                    break;
                }
                _ => {
                    eprintln!("[anyrun-provider] Unexpected socket error: {why}");
                    break;
                }
            },
            Event::Matches { matches, idx } => {
                let plugin = &mut state.plugins[idx];
                plugin.rx = None;
                if let Some(matches) = matches {
                    socket
                        .send(&Response::Matches {
                            query: plugin.query.clone(),
                            plugin: plugin.plugin.info()(),
                            matches,
                        })
                        .await?;
                }
            }
        }
    }

    Ok(WorkerResult::Continue)
}
