use abi_stable::std_types::{ROption, RString, RVec};
use anyrun_plugin::{anyrun_interface::HandleResult, *};
use fuzzy_matcher::FuzzyMatcher;
use scrubber::DesktopEntry;
use serde::Deserialize;
use std::{fs, io, path::PathBuf, process::Command};

#[derive(Deserialize)]
pub struct Config {
    desktop_actions: bool,
    max_entries: usize,
    #[serde(default)]
    hide_description: bool,
    terminal: Option<Terminal>,
    preprocess_exec_script: Option<PathBuf>,
}

#[derive(Deserialize)]
pub struct Terminal {
    command: String,
    args: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            desktop_actions: false,
            max_entries: 5,
            hide_description: false,
            preprocess_exec_script: None,
            terminal: None,
        }
    }
}

pub struct State {
    config: Config,
    entries: Vec<(DesktopEntry, u64)>,
}

mod scrubber;

#[handler]
pub fn handler(selection: Match, state: &State) -> HandleResult {
    let Some(entry) = state.entries.iter().find_map(|(entry, id)| (Some(*id) == selection.id.into_option()).then_some(entry)) else {
        return HandleResult::Refresh(false);
    };
    let result = command_for(entry, &state.config)
        .and_then(|mut command| anyrun_plugin::launch::spawn(&mut command));
    if let Err(why) = result { eprintln!("[applications] Failed to launch desktop entry: {why}"); }
    HandleResult::Close
}

fn command_for(entry: &DesktopEntry, config: &Config) -> io::Result<Command> {
    fn invalid(why: impl ToString) -> io::Error { io::Error::new(io::ErrorKind::InvalidInput, why.to_string()) }
    let mut arguments = entry.arguments(&entry.exec).map_err(invalid)?;
    if let Some(script) = &config.preprocess_exec_script {
        // Explicit argv retains the hook without interpreting desktop Exec as
        // shell source. Scripts receive term/no-term followed by the app argv.
        let output = Command::new(script).arg(if entry.term { "term" } else { "no-term" })
            .args(&arguments).output()?;
        if !output.status.success() { return Err(io::Error::new(io::ErrorKind::Other, "Exec preprocessing failed")); }
        let text = String::from_utf8(output.stdout).map_err(invalid)?;
        arguments = entry.arguments(text.trim()).map_err(invalid)?;
    }
    let mut command = if entry.term {
        let detected;
        let terminal = match &config.terminal {
            Some(terminal) => terminal,
            None => {
                let program = ["alacritty", "foot", "kitty", "wezterm", "wterm", "ghostty"]
                    .into_iter().find(|program| scrubber::executable_path(program).is_some())
                    .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "no supported terminal found"))?;
                detected = Terminal { command: program.into(), args: "-e {}".into() };
                &detected
            }
        };
        let mut command = Command::new(&terminal.command);
        for argument in shlex::split(&terminal.args).ok_or_else(|| invalid("invalid terminal arguments"))? {
            if argument == "{}" { command.args(&arguments); }
            else if argument.contains("{}") {
                let quoted = shlex::try_join(arguments.iter().map(String::as_str)).map_err(invalid)?;
                command.arg(argument.replace("{}", &quoted));
            } else { command.arg(argument); }
        }
        command
    } else {
        let mut command = Command::new(&arguments[0]);
        command.args(&arguments[1..]);
        command
    };
    if let Some(directory) = entry.path.as_ref().filter(|path| path.is_dir()) { command.current_dir(directory); }
    Ok(command)
}

#[init]
pub fn init(config_dir: RString) -> State {
    let config: Config = match fs::read_to_string(format!("{}/applications.ron", config_dir)) {
        Ok(content) => ron::from_str(&content).unwrap_or_else(|why| {
            eprintln!(
                "[applications] Error parsing config, using default: {}",
                why
            );
            Config::default()
        }),
        Err(why) => {
            eprintln!(
                "[applications] Error reading config, using default: {}",
                why
            );
            Config::default()
        }
    };

    let entries = scrubber::scrubber(&config).unwrap_or_else(|why| {
        eprintln!("[applicatiosn] Failed to load desktop entries: {}", why);
        Vec::new()
    });

    State { config, entries }
}

#[get_matches]
pub fn get_matches(input: RString, state: &State) -> RVec<Match> {
    let matcher = fuzzy_matcher::skim::SkimMatcherV2::default().ignore_case();
    let mut entries = state
        .entries
        .iter()
        .filter_map(|(entry, id)| {
            let name_score = matcher.fuzzy_match(&entry.name, &input).unwrap_or(0).max(
                matcher
                    .fuzzy_match(&entry.localized_name(), &input)
                    .unwrap_or(0),
            );
            let desc_score = entry
                .desc
                .as_ref()
                .and_then(|desc| matcher.fuzzy_match(desc, &input))
                .unwrap_or(0);

            let keyword_score = (entry.keywords.iter())
                .chain(entry.localized_keywords.iter().flat_map(|k| k.iter()))
                .filter_map(|keyword| matcher.fuzzy_match(keyword, &input))
                .max()
                .unwrap_or(0);

            let mut score = (name_score * 10 + desc_score + keyword_score) - entry.offset;

            // prioritize actions
            if entry.is_action {
                score *= 2;
            }

            // Score cutoff
            if score > 0 {
                Some((entry, *id, score))
            } else {
                None
            }
        })
        .collect::<Vec<_>>();

    entries.sort_by(|a, b| b.2.cmp(&a.2).then(a.0.name.cmp(&b.0.name)));

    entries.truncate(state.config.max_entries);
    entries
        .into_iter()
        .map(|(entry, id, _)| Match {
            title: entry.localized_name().into(),
            description: if state.config.hide_description {
                ROption::RNone
            } else {
                entry.desc.clone().map(|desc| desc.into()).into()
            },
            use_pango: false,
            icon: ROption::RSome(entry.icon.clone().into()),
            id: ROption::RSome(id),
        })
        .collect()
}

#[info]
pub fn info() -> PluginInfo {
    PluginInfo {
        name: "Applications".into(),
        icon: "application-x-executable".into(),
    }
}

#[cfg(test)]
mod launch_tests {
    use super::*;

    #[test]
    fn desktop_exec_arguments_are_not_interpreted_by_a_shell() {
        let entry = scrubber::tests::entry(r#"/usr/bin/printf "%%s\\n" "$HOME" "$(printf injected)""#, false);
        let mut command = command_for(&entry, &Config::default()).unwrap();
        let output = command.output().unwrap();
        assert!(output.status.success());
        assert_eq!(String::from_utf8(output.stdout).unwrap(), "$HOME\n$(printf injected)\n");
    }

    #[test]
    fn configured_foot_receives_each_argument_and_working_directory() {
        let mut entry = scrubber::tests::entry(r#"/usr/bin/app "two words" "$HOME""#, true);
        entry.path = Some(std::env::temp_dir());
        let config = Config { terminal: Some(Terminal { command: "/usr/bin/foot".into(), args: "-e {}".into() }), ..Config::default() };
        let command = command_for(&entry, &config).unwrap();
        assert_eq!(command.get_program(), "/usr/bin/foot");
        assert_eq!(command.get_args().collect::<Vec<_>>(), ["-e", "/usr/bin/app", "two words", "$HOME"]);
        assert_eq!(command.get_current_dir(), entry.path.as_deref());
    }
}
