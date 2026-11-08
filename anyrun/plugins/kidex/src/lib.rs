use abi_stable::std_types::{ROption, RString, RVec};
use anyrun_plugin::{anyrun_interface::HandleResult, *};
use kidex_common::IndexEntry;
use serde::Deserialize;
use std::{collections::hash_map::DefaultHasher, fs, hash::{Hash, Hasher}, os::unix::prelude::OsStrExt, process::Command, time::{Duration, Instant}};

mod ranking;

#[derive(Deserialize)]
struct Config {
    max_entries: usize,
}

impl Default for Config {
    fn default() -> Self {
        Self { max_entries: 6 }
    }
}

pub struct State {
    config: Config,
    index: Vec<(u64, IndexEntry)>,
    selection: Option<IndexEntry>,
    last_refresh: Instant,
}

fn indexed_entries(index: Vec<IndexEntry>) -> Vec<(u64, IndexEntry)> {
    index.into_iter().map(|entry| {
        let mut hash = DefaultHasher::new();
        entry.path.hash(&mut hash);
        entry.directory.hash(&mut hash);
        (hash.finish(), entry)
    }).collect()
}

enum IndexAction {
    Open,
    Copy,
    CopyPath,
    Back,
}

impl From<u64> for IndexAction {
    fn from(value: u64) -> Self {
        match value {
            0 => Self::Open,
            1 => Self::Copy,
            2 => Self::CopyPath,
            3 => Self::Back,
            _ => unreachable!(),
        }
    }
}

#[handler]
pub fn handler(selection: Match, state: &mut State) -> HandleResult {
    if state.last_refresh.elapsed() >= Duration::from_millis(500) {
        if let Ok(index) = kidex_common::util::get_index(None) { state.index = indexed_entries(index); }
        state.last_refresh = Instant::now();
    }
    if state.selection.as_ref().is_some_and(|entry| !entry.path.exists()
        || !state.index.iter().any(|(_, current)| current.path == entry.path && current.directory == entry.directory))
    {
        state.selection = None;
        return HandleResult::Refresh(false);
    }
    match &state.selection {
        Some(index_entry) => match selection.id.unwrap().into() {
            IndexAction::Open => {
                if let Err(why) = anyrun_plugin::launch::spawn(Command::new("xdg-open").arg(&index_entry.path)) {
                    eprintln!("[kidex] Error running xdg-open: {why}");
                }
                HandleResult::Close
            }
            IndexAction::Copy => match fs::read(&index_entry.path) {
                Ok(bytes) => HandleResult::Copy(bytes.into()),
                Err(why) => {
                    eprintln!(
                        "[kidex] Error reading file {}: {why}, not copying",
                        index_entry.path.display()
                    );
                    HandleResult::Close
                }
            },
            IndexAction::CopyPath => {
                HandleResult::Copy(index_entry.path.clone().into_os_string().as_bytes().into())
            }
            IndexAction::Back => {
                state.selection = None;
                HandleResult::Refresh(false)
            }
        },
        None => {
            let Some((_, index_entry)) = state
                .index
                .iter()
                .find(|(id, _)| selection.id == ROption::RSome(*id)) else {
                    return HandleResult::Refresh(false);
                };
            if !index_entry.path.exists() {
                return HandleResult::Refresh(false);
            }

            state.selection = Some(index_entry.clone());
            HandleResult::Refresh(true)
        }
    }
}

#[init]
pub fn init(config_dir: RString) -> State {
    let config = match fs::read_to_string(format!("{config_dir}/kidex.ron")) {
        Ok(content) => ron::from_str(&content).unwrap_or_default(),
        Err(_) => Config::default(),
    };
    let index = match kidex_common::util::get_index(None) {
        Ok(index) => indexed_entries(index),
        Err(why) => {
            eprintln!("[kidex] Failed to get kidex index: {why}");
            Vec::new()
        }
    };
    State {
        config,
        index,
        selection: None,
        last_refresh: Instant::now(),
    }
}

#[get_matches]
pub fn get_matches(input: RString, state: &mut State) -> RVec<Match> {
    if state.last_refresh.elapsed() >= Duration::from_millis(500) {
        match kidex_common::util::get_index(None) {
            Ok(index) => state.index = indexed_entries(index),
            Err(why) => eprintln!("[kidex] Failed to refresh index: {why}"),
        }
        state.last_refresh = Instant::now();
    }
    if state.selection.as_ref().is_some_and(|entry| !entry.path.exists()
        || !state.index.iter().any(|(_, current)| current.path == entry.path && current.directory == entry.directory))
    {
        state.selection = None;
    }
    match &state.selection {
        Some(index_entry) => {
            let mut actions = vec![
                Match {
                    title: "Open".into(),
                    description: ROption::RNone,
                    use_pango: false,
                    id: ROption::RSome(IndexAction::Open as u64),
                    icon: ROption::RSome("document-open".into()),
                },
            ];
            if !index_entry.directory {
                actions.push(Match {
                    title: "Copy".into(),
                    description: ROption::RNone,
                    use_pango: false,
                    id: ROption::RSome(IndexAction::Copy as u64),
                    icon: ROption::RSome("edit-copy".into()),
                });
            }
            actions.extend([
                Match {
                    title: "Copy path".into(),
                    description: ROption::RNone,
                    use_pango: false,
                    id: ROption::RSome(IndexAction::CopyPath as u64),
                    icon: ROption::RSome("edit-copy".into()),
                },
                Match {
                    title: "Back".into(),
                    description: ROption::RNone,
                    use_pango: false,
                    id: ROption::RSome(IndexAction::Back as u64),
                    icon: ROption::RSome("edit-undo".into()),
                },
            ]);
            actions.into()
        }
        None => {
            let trimmed = input.trim_start();
            let (query, directory) = if let Some(query) = trimmed.strip_prefix('#') {
                (query.trim_start(), Some(true))
            } else if let Some(query) = trimmed.strip_prefix('@') {
                (query.trim_start(), Some(false))
            } else {
                (input.as_str(), None)
            };
            let matcher = fuzzy_matcher::skim::SkimMatcherV2::default().smart_case();
            let mut index = state
                .index
                .clone()
                .into_iter()
                .filter(|(_, entry)| directory.map_or(true, |directory| entry.directory == directory))
                .filter_map(|(id, index_entry)| {
                    ranking::rank(&index_entry.path, query, &matcher)
                        .map(|val| (index_entry, id, val))
                })
                .collect::<Vec<_>>();

            index.sort_by(|a, b| a.2.cmp(&b.2)
                .then_with(|| a.0.path.cmp(&b.0.path)));

            index
                .into_iter()
                .filter(|(entry, _, _)| entry.path.exists())
                .take(state.config.max_entries)
                .map(|(entry_index, id, _)| Match {
                    title: entry_index
                        .path
                        .file_name()
                        .map(|name| name.to_string_lossy().into())
                        .unwrap_or("N/A".into()),
                    description: ROption::RSome(entry_index.path.display().to_string().into()),
                    use_pango: false,
                    icon: ROption::RSome(if entry_index.directory {
                        "folder".into()
                    } else {
                        "text-x-generic".into()
                    }),
                    id: ROption::RSome(id),
                })
                .collect()
        }
    }
}

#[info]
pub fn info() -> PluginInfo {
    PluginInfo {
        name: "Kidex".into(),
        icon: "folder".into(),
    }
}
