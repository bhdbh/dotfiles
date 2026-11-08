use anyrun_provider_ipc::HistoryEntry;
use serde::{Deserialize, Serialize};
use std::{env, fs, io::Write, os::unix::fs::OpenOptionsExt, path::PathBuf};

const LIMIT: usize = 6;

pub struct History {
    pub entries: Vec<HistoryEntry>,
    path: PathBuf,
    usage: Vec<UsageEntry>,
}

#[derive(Deserialize, Serialize)]
struct UsageEntry {
    entry: HistoryEntry,
    count: u64,
    last_used: u64,
}

fn same_item(left: &HistoryEntry, right: &HistoryEntry) -> bool {
    left.plugin.name == right.plugin.name
        && left.selection.title == right.selection.title
        && left.selection.description == right.selection.description
        && left.selection.icon == right.selection.icon
}

fn tracks_usage(entry: &HistoryEntry) -> bool {
    usage_kind(entry).is_some()
}

fn usage_kind(entry: &HistoryEntry) -> Option<usize> {
    match entry.plugin.name.as_str() {
        "Applications" => Some(0),
        "Shell" => Some(1),
        "Kidex" => match entry.selection.icon.as_ref().map(|icon| icon.as_str()).into_option() {
            Some("folder") => Some(2),
            Some("text-x-generic") => Some(3),
            _ => None,
        },
        _ => None,
    }
}

fn prune_usage(usage: &mut Vec<UsageEntry>) {
    // Keep six recent distinct items for each prefix; frequency still orders
    // suggestions within that bounded history. New items can replace old ones.
    usage.sort_by(|left, right| right.last_used.cmp(&left.last_used));
    let mut counts = [0; 4];
    let mut seen: Vec<HistoryEntry> = Vec::new();
    usage.retain(|item| {
        let Some(kind) = usage_kind(&item.entry) else { return false; };
        if counts[kind] == LIMIT || seen.iter().any(|entry| same_item(entry, &item.entry)) {
            return false;
        }
        counts[kind] += 1;
        seen.push(item.entry.clone());
        true
    });
}

impl History {
    pub fn load() -> Self {
        let directory = env::var_os("XDG_STATE_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env::var_os("HOME").unwrap()).join(".local/state"));
        let path = directory.join("anyrun/history.json");
        let mut entries: Vec<HistoryEntry> = fs::read(&path)
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_default();
        let original_entries = entries.len();
        entries.truncate(LIMIT);
        let mut usage: Vec<UsageEntry> = fs::read(path.with_file_name("usage.json"))
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_default();
        let original_usage = usage.len();
        let mut seeded = false;
        // Seed newly tracked kinds from known recent selections without
        // changing counters that were already recorded.
        for entry in entries.iter().rev().filter(|entry| tracks_usage(entry)) {
            if !usage.iter().any(|item| same_item(&item.entry, entry)) {
                let last_used = usage.iter().map(|item| item.last_used).max().unwrap_or(0).saturating_add(1);
                usage.push(UsageEntry { entry: entry.clone(), count: 1, last_used });
                seeded = true;
            }
        }
        prune_usage(&mut usage);
        let changed = seeded || entries.len() != original_entries || usage.len() != original_usage;
        let history = Self { entries, path, usage };
        if changed {
            if let Err(why) = history.save() {
                eprintln!("[history] Failed to prune saved histories: {why}");
            }
        }
        history
    }

    pub fn record(&mut self, entry: HistoryEntry) {
        if tracks_usage(&entry) {
            let last_used = self.usage.iter().map(|usage| usage.last_used).max()
                .unwrap_or(0).saturating_add(1);
            if let Some(usage) = self.usage.iter_mut().find(|usage| same_item(&usage.entry, &entry)) {
                usage.entry = entry.clone();
                usage.count = usage.count.saturating_add(1);
                usage.last_used = last_used;
            } else {
                self.usage.push(UsageEntry { entry: entry.clone(), count: 1, last_used });
            }
        }
        self.entries.retain(|previous| !same_item(previous, &entry));
        self.entries.insert(0, entry);
        self.entries.truncate(LIMIT);
        prune_usage(&mut self.usage);
        if let Err(why) = self.save() {
            eprintln!("[history] Failed to save recent selections: {why}");
        }
    }

    pub fn most_used(&self, prefix: char) -> Vec<HistoryEntry> {
        let mut usage: Vec<_> = self.usage.iter()
            .filter(|usage| match prefix {
                '>' => usage.entry.plugin.name == "Applications",
                '$' => usage.entry.plugin.name == "Shell",
                '#' | '@' if usage.entry.plugin.name == "Kidex" => {
                    let icon = if prefix == '#' { "folder" } else { "text-x-generic" };
                    usage.entry.selection.icon.as_ref().map(|value| value.as_str())
                        == anyrun_interface::abi_stable::std_types::ROption::RSome(icon)
                }
                _ => false,
            }).collect();
        usage.sort_by(|left, right| right.count.cmp(&left.count)
            .then(right.last_used.cmp(&left.last_used)));
        usage.into_iter().map(|usage| usage.entry.clone()).collect()
    }

    fn save(&self) -> std::io::Result<()> {
        fs::create_dir_all(self.path.parent().unwrap())?;
        save_json(&self.path, &self.entries)?;
        save_json(&self.path.with_file_name("usage.json"), &self.usage)
    }
}

pub(crate) fn save_json(path: &std::path::Path, value: &impl Serialize) -> std::io::Result<()> {
        let temporary = path.with_extension("json.tmp");
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&temporary)?;
        file.write_all(&serde_json::to_vec(value)?)?;
        file.sync_all()?;
        fs::rename(temporary, path)
}
