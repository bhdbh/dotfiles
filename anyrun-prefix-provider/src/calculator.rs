use std::{env, fs, path::PathBuf};

use anyrun_interface::{Match, abi_stable::std_types::{ROption, RVec}};
use anyrun_provider_ipc::{Calculation, HistoryEntry};

pub struct CalculatorHistory {
    entries: Vec<Calculation>,
    path: PathBuf,
}

impl CalculatorHistory {
    pub fn load() -> Self {
        let directory = env::var_os("XDG_STATE_HOME").map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env::var_os("HOME").unwrap()).join(".local/state"))
            .join("anyrun");
        let path = directory.join("calculations.json");
        let mut entries: Vec<Calculation> = fs::read(&path).ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_else(|| {
                let legacy: Vec<HistoryEntry> = fs::read(directory.join("history.json")).ok()
                    .and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default();
                legacy.into_iter().filter(|entry| entry.plugin.name == "Rink")
                    .filter_map(|entry| Some(Calculation {
                        expression: entry.query.trim_start().strip_prefix('=')?.trim().to_owned(),
                        result: entry.selection.title.to_string(),
                    })).collect()
            });
        let original_entries = entries.len();
        entries.retain(|entry| !entry.expression.is_empty() && !entry.result.is_empty());
        entries.truncate(6);
        let changed = entries.len() != original_entries;
        let history = Self { entries, path };
        if changed {
            if let Err(why) = history.save() {
                eprintln!("[calculator] Failed to prune saved operations: {why}");
            }
        }
        history
    }

    pub fn record(&mut self, calculation: Calculation) {
        self.entries.retain(|previous| previous != &calculation);
        self.entries.insert(0, calculation);
        self.entries.truncate(6);
        if let Err(why) = self.save() {
            eprintln!("[calculator] Failed to save operations: {why}");
        }
    }

    fn save(&self) -> std::io::Result<()> {
        fs::create_dir_all(self.path.parent().unwrap())?;
        crate::history::save_json(&self.path, &self.entries)
    }

    pub fn matches(&self) -> RVec<Match> {
        self.entries.iter().enumerate().map(|(index, entry)| Match {
            title: entry.result.clone().into(),
            description: ROption::RSome(entry.expression.clone().into()),
            use_pango: false,
            icon: ROption::RNone,
            id: ROption::RSome(index as u64),
        }).collect()
    }
}
