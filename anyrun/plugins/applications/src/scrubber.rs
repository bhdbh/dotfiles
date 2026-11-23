use std::{collections::{HashMap, HashSet}, env, ffi::OsStr, fs, path::PathBuf};

use crate::Config;

#[derive(Clone, Debug)]
pub struct DesktopEntry {
    pub exec: String,
    pub desktop_file: PathBuf,
    pub path: Option<PathBuf>,
    pub name: String,
    pub localized_name: Option<String>,
    pub keywords: Vec<String>,
    pub localized_keywords: Option<Vec<String>>,
    pub desc: Option<String>,
    pub icon: String,
    pub term: bool,
    pub offset: i64,
    pub is_action: bool,
}


impl DesktopEntry {
    pub fn localized_name(&self) -> String {
        self.localized_name
            .clone()
            .unwrap_or_else(|| self.name.clone())
    }

    fn from_dir_entry(
        entry: &fs::DirEntry,
        config: &Config,
        lang_choices: &LangChoices,
    ) -> Vec<Self> {
        if entry.path().extension() == Some(OsStr::new("desktop")) {
            let content = match fs::read_to_string(entry.path()) {
                Ok(content) => content,
                Err(_) => return Vec::new(),
            };

            let lines = content.lines().collect::<Vec<_>>();

            let sections = lines
                .split_inclusive(|line| line.starts_with('['))
                .collect::<Vec<_>>();

            let mut line = None;
            let mut new_sections = Vec::new();

            for (i, section) in sections.iter().enumerate() {
                if let Some(line) = line {
                    let mut section = section.to_vec();
                    section.insert(0, line);

                    // Only pop the last redundant entry if it isn't the last item
                    if i < sections.len() - 1 {
                        section.pop();
                    }
                    new_sections.push(section);
                }
                line = Some(section.last().unwrap_or(&""));
            }

            let mut ret = Vec::new();

            let entry = match new_sections.iter().find_map(|section| {
                if section[0].starts_with("[Desktop Entry]") {
                    let mut map = HashMap::new();

                    for line in section.iter().skip(1) {
                        if let Some((key, val)) = line.split_once('=') {
                            map.insert(key, val);
                        }
                    }

                    if map.get("Type")? == &"Application" && visible(&map)
                    {
                        Some(DesktopEntry {
                            exec: map.get("Exec")?.to_string(),
                            desktop_file: entry.path(),
                            path: map.get("Path").map(PathBuf::from),
                            name: map.get("Name")?.to_string(),
                            localized_name: lang_choices
                                .localized_keys("Name")
                                .find_map(|key| map.get(&*key))
                                .map(ToString::to_string),
                            keywords: map
                                .get("Keywords")
                                .map(|keywords| {
                                    keywords
                                        .split(';')
                                        .map(|s| s.to_owned())
                                        .collect::<Vec<_>>()
                                })
                                .unwrap_or_default(),
                            localized_keywords: lang_choices
                                .localized_keys("Keywords")
                                .find_map(|key| map.get(&*key))
                                .map(|keywords| {
                                    keywords
                                        .split(';')
                                        .map(|s| s.to_owned())
                                        .collect::<Vec<_>>()
                                }),
                            desc: lang_choices
                                .localized_keys("Comment")
                                .find_map(|key| map.get(&*key))
                                .or_else(|| map.get("Comment"))
                                .map(ToString::to_string),
                            icon: map
                                .get("Icon")
                                .unwrap_or(&"application-x-executable")
                                .to_string(),
                            term: map
                                .get("Terminal")
                                .map(|val| val.to_lowercase() == "true")
                                .unwrap_or(false),
                            offset: 0,
                            is_action: false,
                        })
                    } else {
                        None
                    }
                } else {
                    None
                }
            }) {
                Some(entry) => entry,
                None => return Vec::new(),
            };

            if config.desktop_actions {
                for (i, section) in new_sections.iter().enumerate() {
                    let mut map = HashMap::new();

                    for line in section.iter().skip(1) {
                        if let Some((key, val)) = line.split_once('=') {
                            map.insert(key, val);
                        }
                    }

                    if section[0].starts_with("[Desktop Action") {
                        ret.push(DesktopEntry {
                            exec: match map.get("Exec") { Some(exec) => exec.to_string(), None => continue },
                            desktop_file: entry.desktop_file.clone(),
                            path: entry.path.clone(),
                            name: match map.get("Name") {
                                Some(name) => name.to_string(),
                                None => continue,
                            },
                            localized_name: lang_choices
                                .localized_keys("Name")
                                .find_map(|key| map.get(&*key))
                                .map(ToString::to_string),
                            keywords: map
                                .get("Keywords")
                                .map(|keywords| {
                                    keywords
                                        .split(';')
                                        .map(|s| s.to_owned())
                                        .collect::<Vec<_>>()
                                })
                                .unwrap_or_default(),
                            localized_keywords: lang_choices
                                .localized_keys("Keywords")
                                .find_map(|key| map.get(&*key))
                                .map(|keywords| {
                                    keywords
                                        .split(';')
                                        .map(|s| s.to_owned())
                                        .collect::<Vec<_>>()
                                }),
                            desc: Some(entry.localized_name()),
                            icon: entry.icon.clone(),
                            term: map
                                .get("Terminal")
                                .map(|val| val.to_lowercase() == "true")
                                .unwrap_or(false),
                            offset: i as i64,
                            is_action: true,
                        })
                    }
                }
            }

            ret.push(entry);
            ret
        } else {
            Vec::new()
        }
    }
}

#[derive(Debug, Default)]
struct LangChoices<'a> {
    whole: Option<&'a str>,
    prefix: Option<&'a str>,
    short: Option<&'a str>,
}

impl<'a> LangChoices<'a> {
    fn new(lang: Option<&'a str>) -> Self {
        let mut ret = Self::default();

        // example: en_US.UTF-8
        let Some(whole) = lang else {
            return ret;
        };
        ret.whole = Some(whole);

        // example: en_US
        let Some((prefix, _)) = whole.split_once('.') else {
            return ret;
        };
        ret.prefix = Some(prefix);

        // example: en
        let Some((short, _)) = prefix.split_once('_') else {
            return ret;
        };
        ret.short = Some(short);

        ret
    }

    fn localized_keys(&self, key: &'a str) -> impl Iterator<Item = String> + 'a {
        let choices = (self.whole.into_iter())
            .chain(self.prefix)
            .chain(self.short);
        choices.map(move |choice| format!("{key}[{choice}]"))
    }
}

fn visible(map: &HashMap<&str, &str>) -> bool {
    let desktop = env::var("XDG_CURRENT_DESKTOP").unwrap_or_default();
    visible_on_desktop(map, &desktop)
}

fn visible_on_desktop(map: &HashMap<&str, &str>, desktop: &str) -> bool {
    if ["Hidden", "NoDisplay"].iter().any(|key| map.get(key).is_some_and(|value| *value == "true")) { return false; }
    let desktops: Vec<_> = desktop.split(':').collect();
    if map.get("OnlyShowIn").is_some_and(|value| !value.split(';').any(|item| !item.is_empty() && desktops.contains(&item))) { return false; }
    if map.get("NotShowIn").is_some_and(|value| value.split(';').any(|item| !item.is_empty() && desktops.contains(&item))) { return false; }
    if let Some(program) = map.get("TryExec") {
        if executable_path(program).is_none() { return false; }
    }
    true
}

pub(crate) fn executable_path(program: &str) -> Option<PathBuf> {
    use std::os::unix::fs::PermissionsExt;
    let executable = |path: &PathBuf| path.metadata().is_ok_and(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0);
    let path = PathBuf::from(program);
    if path.is_absolute() { return executable(&path).then_some(path); }
    env::var_os("PATH").and_then(|paths| env::split_paths(&paths).map(|directory| directory.join(program)).find(executable))
}

pub fn scrubber(config: &Config) -> Result<Vec<(DesktopEntry, u64)>, Box<dyn std::error::Error>> {
    let user = env::var_os("XDG_DATA_HOME").map(PathBuf::from)
        .or_else(|| env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/share")));
    let system = env::var_os("XDG_DATA_DIRS").unwrap_or_else(|| "/usr/local/share:/usr/share".into());
    let directories = user.into_iter().chain(env::split_paths(&system));
    let lang = env::var("LANG").ok();
    let lang_choices = LangChoices::new(lang.as_deref());
    Ok(collect_entries(config, directories, &lang_choices))
}

fn collect_entries(config: &Config, directories: impl Iterator<Item = PathBuf>, lang_choices: &LangChoices) -> Vec<(DesktopEntry, u64)> {
    let mut seen = HashSet::new();
    let mut entries = Vec::new();
    for directory in directories {
        let Ok(files) = fs::read_dir(directory.join("applications")) else { continue; };
        for file in files.filter_map(Result::ok) {
            // The first desktop ID wins, even if the overriding entry is hidden.
            // Two applications sharing a label/icon are still distinct IDs.
            if file.path().extension() != Some(OsStr::new("desktop")) || !seen.insert(file.file_name()) { continue; }
            entries.extend(DesktopEntry::from_dir_entry(&file, config, &lang_choices));
        }
    }
    entries.into_iter().enumerate().map(|(id, entry)| (entry, id as u64)).collect()
}

impl DesktopEntry {
    pub fn arguments(&self, exec: &str) -> Result<Vec<String>, String> {
        // Decode the desktop-file string escaping before argv quoting. There is
        // deliberately no shell expansion or command substitution here.
        let mut decoded = String::new();
        let mut chars = exec.chars();
        while let Some(character) = chars.next() {
            if character != '\\' { decoded.push(character); continue; }
            match chars.next() {
                Some('s') => decoded.push(' '), Some('n') => decoded.push('\n'),
                Some('t') => decoded.push('\t'), Some('r') => decoded.push('\r'),
                Some('\\') => decoded.push('\\'),
                Some(other) => { decoded.push('\\'); decoded.push(other); }
                None => return Err("unfinished desktop-file escape".into()),
            }
        }
        let words = shlex::split(&decoded).ok_or("invalid Exec quoting")?;
        let mut arguments = Vec::new();
        for word in words {
            if word == "%i" {
                if !self.icon.is_empty() { arguments.extend(["--icon".into(), self.icon.clone()]); }
                continue;
            }
            if ["%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%v", "%m"].contains(&word.as_str()) { continue; }
            let mut result = String::new();
            let mut chars = word.chars();
            while let Some(character) = chars.next() {
                if character != '%' { result.push(character); continue; }
                match chars.next() {
                    Some('%') => result.push('%'),
                    Some('c') => result.push_str(&self.localized_name()),
                    Some('k') => result.push_str(&self.desktop_file.to_string_lossy()),
                    Some('f' | 'F' | 'u' | 'U' | 'd' | 'D' | 'n' | 'N' | 'v' | 'm') => (),
                    _ => return Err("unsupported Exec field code".into()),
                }
            }
            arguments.push(result);
        }
        if arguments.first().is_none_or(String::is_empty) { return Err("empty Exec command".into()); }
        Ok(arguments)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub(crate) fn entry(exec: &str, terminal: bool) -> DesktopEntry {
        DesktopEntry {
            exec: exec.into(), desktop_file: "/tmp/app with spaces.desktop".into(),
            path: None, name: "Example App".into(), localized_name: None,
            keywords: vec![], localized_keywords: None, desc: None, icon: "example".into(),
            term: terminal, offset: 0, is_action: false,
        }
    }

    #[test]
    fn field_codes_expand_after_tokenization_and_percent_is_literal() {
        let entry = entry(r#"/usr/bin/app %c %k %i %F %%f "$HOME" "$(printf injected)""#, false);
        assert_eq!(entry.arguments(&entry.exec).unwrap(), [
            "/usr/bin/app", "Example App", "/tmp/app with spaces.desktop", "--icon", "example", "%f", "$HOME", "$(printf injected)"
        ]);
        assert!(entry.arguments("app \"unterminated").is_err());
        assert!(entry.arguments("app %z").is_err());
    }

    #[test]
    fn desktop_visibility_respects_session_filters() {
        let only = HashMap::from([("OnlyShowIn", "GNOME;" )]);
        assert!(!visible_on_desktop(&only, "sway"));
        assert!(visible_on_desktop(&only, "sway:GNOME"));
        assert!(!visible_on_desktop(&HashMap::from([("NotShowIn", "sway;")]), "sway"));
        assert!(!visible_on_desktop(&HashMap::from([("Hidden", "true")]), "sway"));
    }

    #[test]
    fn user_hidden_override_masks_system_id_but_equal_labels_are_distinct() {
        let root = env::temp_dir().join(format!("dotfiles-desktop-{}", std::process::id()));
        let user = root.join("user/applications"); let system = root.join("system/applications");
        fs::create_dir_all(&user).unwrap(); fs::create_dir_all(&system).unwrap();
        let text = "[Desktop Entry]\nType=Application\nName=Same Name\nIcon=example\nExec=/usr/bin/true\n";
        fs::write(system.join("hidden.desktop"), text).unwrap();
        fs::write(user.join("hidden.desktop"), "[Desktop Entry]\nType=Application\nName=Hidden\nHidden=true\n").unwrap();
        fs::write(system.join("first.desktop"), text).unwrap();
        fs::write(system.join("second.desktop"), text).unwrap();
        let entries = collect_entries(&Config::default(), [root.join("user"), root.join("system")].into_iter(), &LangChoices::default());
        assert_eq!(entries.len(), 2);
        assert!(entries.iter().all(|(entry, _)| entry.name == "Same Name"));
        fs::remove_dir_all(root).unwrap();
    }
}
