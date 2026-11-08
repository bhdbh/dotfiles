use crate::{app::{App, AppInit, AppMsg}, config::Action, Args};
use clap::Parser;
use gtk::{gio, glib, prelude::*};
use gtk4 as gtk;
use std::{env, fs, time::{Duration, Instant}};

fn pump_until(mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(10);
    let context = glib::MainContext::default();
    loop {
        while context.pending() { context.iteration(false); }
        if condition() { return; }
        assert!(Instant::now() < deadline, "Timed out waiting for search UI");
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn pump_for(milliseconds: u64, mut check: impl FnMut()) {
    let until = Instant::now() + Duration::from_millis(milliseconds);
    pump_until(|| { check(); Instant::now() >= until });
}

fn labels(widget: &gtk::Widget) -> Vec<gtk::Label> {
    let mut result = Vec::new();
    if let Some(label) = widget.downcast_ref::<gtk::Label>() {
        if label.has_css_class("title") && label.is_drawable() { result.push(label.clone()); }
    }
    let mut child = widget.first_child();
    while let Some(current) = child {
        result.extend(labels(&current));
        child = current.next_sibling();
    }
    result
}

#[test]
#[ignore = "requires an isolated compositor and temporary action history"]
fn actions_history_ui_flow() {
    gtk::init().unwrap();
    let config = env::var("ANYRUN_UI_CONFIG").unwrap();
    let marker = std::path::Path::new(&config).join("executed");
    let app = gtk::Application::new(Some("org.anyrun.actions.history.test"), gio::ApplicationFlags::NON_UNIQUE);
    app.register(None::<&gio::Cancellable>).unwrap();
    let sender = App::launch(&app, AppInit {
        args: Args::parse_from(["anyrun", "--config-dir", &config]),
        stdin: Vec::new(), env: Vec::new(),
    }, None);
    let window = app.windows().into_iter().next().unwrap();
    let canvas = window.child().unwrap().downcast::<gtk::Overlay>().unwrap();
    let main = canvas.first_child().unwrap().downcast::<gtk::Box>().unwrap();
    let titles = || labels(main.upcast_ref()).iter().map(|label| label.text().to_string()).collect::<Vec<_>>();
    pump_until(|| window.is_mapped());
    assert_eq!(titles(), ["Suspend", "Custom action", "Reboot"], "Saved actions must appear without a title allowlist");
    if let Ok(path) = env::var("ANYRUN_HISTORY_SCREENSHOT") {
        pump_for(200, || assert!(window.is_mapped()));
        assert!(std::process::Command::new("grim").arg(path).status().unwrap().success());
    }
    sender.emit(AppMsg::Action(Action::Select));
    pump_until(|| titles() == ["Confirm", "Back"]);
    assert!(!marker.exists(), "Replaying history must ask for confirmation before executing");
    sender.emit(AppMsg::Action(Action::Close));
    pump_until(|| !window.is_visible());
}

#[test]
#[ignore = "requires an isolated compositor and delayed fixture provider"]
fn search_debounce_ui_flow() {
    gtk::init().unwrap();
    let config = env::var("ANYRUN_UI_CONFIG").unwrap();
    let log = env::var("ANYRUN_SEARCH_LOG").unwrap();
    let app = gtk::Application::new(Some("org.anyrun.search.test"), gio::ApplicationFlags::NON_UNIQUE);
    app.register(None::<&gio::Cancellable>).unwrap();
    let sender = App::launch(&app, AppInit {
        args: Args::parse_from(["anyrun", "--config-dir", &config]),
        stdin: Vec::new(), env: Vec::new(),
    }, None);
    let window = app.windows().into_iter().next().unwrap();
    let canvas = window.child().unwrap().downcast::<gtk::Overlay>().unwrap();
    let main = canvas.first_child().unwrap().downcast::<gtk::Box>().unwrap();
    let entry = main.first_child().unwrap().downcast::<gtk::Text>().unwrap();
    let titles = || labels(main.upcast_ref()).iter().map(|l| l.text().to_string()).collect::<Vec<_>>();
    let assert_seed = || {
        assert_eq!(titles(), ["History seed"]);
        assert!(main.has_css_class("has-results"));
    };
    pump_until(|| window.is_mapped() && titles() == ["History seed"]);
    entry.set_text("b"); pump_for(35, assert_seed);
    entry.set_text("br"); pump_for(35, assert_seed);
    entry.set_text("brave"); pump_for(75, assert_seed);
    assert!(!fs::read_to_string(&log).unwrap().contains("Query"));
    // The fixture answers one plugin immediately and the second 250ms later.
    pump_for(120, assert_seed);
    pump_until(|| titles() == ["A:brave"]);
    let requests = fs::read_to_string(&log).unwrap();
    assert!(requests.contains("brave") && !requests.contains("\"b\"") && !requests.contains("\"br\""));

    entry.set_text("next");
    pump_for(180, || assert_eq!(titles(), ["A:brave"]));
    entry.set_text("latest");
    pump_for(200, || assert_eq!(titles(), ["A:brave"]));
    pump_until(|| titles() == ["A:latest"]);

    entry.set_text("stable1"); pump_until(|| titles() == ["Stable"]);
    let row = labels(main.upcast_ref())[0].ancestor(gtk::ListBoxRow::static_type()).unwrap();
    entry.set_text("stable2");
    pump_for(500, || assert_eq!(titles(), ["Stable"]));
    assert_eq!(row, labels(main.upcast_ref())[0].ancestor(gtk::ListBoxRow::static_type()).unwrap(), "Unchanged results should reuse their widgets");

    entry.set_text("clear-me"); pump_for(20, || {});
    entry.set_text(""); pump_for(30, || {});
    assert!(titles().is_empty() && !main.has_css_class("has-results"));
    pump_for(450, || assert!(titles().is_empty()));
    assert!(!fs::read_to_string(&log).unwrap().contains("clear-me"));

    entry.set_text("menu"); pump_until(|| titles() == ["A:menu", "B:menu"]);
    sender.emit(AppMsg::Action(Action::Select));
    pump_until(|| titles() == ["Open"]);
    sender.emit(AppMsg::Action(Action::Select));
    pump_until(|| titles() == ["A:menu", "B:menu"]);

    entry.set_text("enter-now"); pump_for(20, || {});
    sender.emit(AppMsg::Action(Action::Select));
    pump_until(|| !window.is_visible());
    let requests = fs::read_to_string(&log).unwrap();
    let handled: Vec<serde_json::Value> = requests.lines().filter_map(|line| {
        let value: serde_json::Value = serde_json::from_str(line).unwrap();
        value.get("Handle").cloned()
    }).collect();
    assert_eq!(handled.last().unwrap()["selection"]["title"], "A:enter-now", "Enter must wait for the new results instead of launching an old row");
}


#[test]
#[ignore = "requires an isolated compositor and navigation fixture provider"]
fn enter_always_uses_first_result_ui_flow() {
    use gtk::gdk;

    gtk::init().unwrap();
    let config = env::var("ANYRUN_UI_CONFIG").unwrap();
    let log = env::var("ANYRUN_SEARCH_LOG").unwrap();
    let case = env::var("ANYRUN_NAV_CASE").unwrap();
    let app = gtk::Application::new(Some("org.anyrun.navigation.test"), gio::ApplicationFlags::NON_UNIQUE);
    app.register(None::<&gio::Cancellable>).unwrap();
    let sender = App::launch(&app, AppInit {
        args: Args::parse_from(["anyrun", "--config-dir", &config]),
        stdin: Vec::new(), env: Vec::new(),
    }, None);
    let window = app.windows().into_iter().next().unwrap();
    let canvas = window.child().unwrap().downcast::<gtk::Overlay>().unwrap();
    let main = canvas.first_child().unwrap().downcast::<gtk::Box>().unwrap();
    let entry = main.first_child().unwrap().downcast::<gtk::Text>().unwrap();
    let controllers = window.observe_controllers();
    let keys = (0..controllers.n_items()).find_map(|i| {
        controllers.item(i)?.downcast::<gtk::EventControllerKey>().ok()
    }).unwrap();
    assert_eq!(keys.propagation_phase(), gtk::PropagationPhase::Capture);
    let press = |key: gdk::Key, modifier: gdk::ModifierType| {
        keys.emit_by_name::<bool>("key-pressed", &[&key, &0u32, &modifier])
    };
    let titles = || labels(main.upcast_ref()).iter().map(|label| label.text().to_string()).collect::<Vec<_>>();
    let rows = || labels(main.upcast_ref()).iter().map(|label| {
        label.ancestor(gtk::ListBoxRow::static_type()).unwrap().downcast::<gtk::ListBoxRow>().unwrap()
    }).collect::<Vec<_>>();
    pump_until(|| window.is_mapped() && titles() == ["History first", "History second"]);

    if case != "history" {
        entry.set_text("choices");
        pump_until(|| titles() == ["First:choices", "Second:choices"]);
    }
    let visible_rows = rows();
    for modifier in [gdk::ModifierType::empty(), gdk::ModifierType::SHIFT_MASK, gdk::ModifierType::CONTROL_MASK] {
        for key in [gdk::Key::Down, gdk::Key::Up, gdk::Key::KP_Down, gdk::Key::KP_Up] {
            assert!(press(key, modifier), "Arrow events must not propagate to GTK navigation");
            sender.emit(AppMsg::KeyPressed { key, modifier });
            pump_for(20, || {});
            assert!(visible_rows[0].is_selected() && !visible_rows[1].is_selected(), "Arrow keys changed the selected result");
        }
    }

    // A selection changed by the pointer or Tab must not affect Enter.
    let list = visible_rows[1].parent().unwrap().downcast::<gtk::ListBox>().unwrap();
    list.select_row(Some(&visible_rows[1]));
    pump_until(|| visible_rows[1].is_selected() && !visible_rows[0].is_selected());
    if case == "pending" {
        entry.set_text("next");
        pump_for(20, || {});
    }
    visible_rows[1].grab_focus();
    assert!(press(if case == "keypad" { gdk::Key::KP_Enter } else { gdk::Key::Return }, gdk::ModifierType::empty()), "Enter must not propagate to the focused row");
    pump_until(|| !window.is_visible());

    let requests: Vec<serde_json::Value> = fs::read_to_string(log).unwrap().lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    if case == "history" {
        assert!(requests.iter().any(|request| request["Replay"]["index"] == 0), "Enter must replay the first history item");
    } else {
        let handled: Vec<_> = requests.iter().filter_map(|request| request.get("Handle")).collect();
        assert_eq!(handled.len(), 1);
        let expected = if case == "pending" { "First:next" } else { "First:choices" };
        assert_eq!(handled[0]["selection"]["title"], expected, "Enter must activate the first result for the current query");
    }
}
