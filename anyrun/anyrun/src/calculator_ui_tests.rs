use crate::{app::{App, AppInit, AppMsg}, config::Action, Args};
use gtk::{gdk, gio, glib, prelude::*};
use gtk4 as gtk;
use std::{env, path::PathBuf};
use clap::Parser;
use std::time::{Duration, Instant};

#[track_caller]
fn pump_until(mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(15);
    let context = glib::MainContext::default();
    loop {
        while context.pending() { context.iteration(false); }
        if condition() { return; }
        assert!(Instant::now() < deadline, "Timed out waiting for the calculator UI");
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn pump_for(milliseconds: u64) {
    let until = Instant::now() + Duration::from_millis(milliseconds);
    pump_until(|| Instant::now() >= until);
}

fn labels(widget: &gtk::Widget) -> Vec<gtk::Label> {
    let mut result = Vec::new();
    if let Some(label) = widget.downcast_ref::<gtk::Label>() { result.push(label.clone()); }
    let mut child = widget.first_child();
    while let Some(current) = child {
        result.extend(labels(&current));
        child = current.next_sibling();
    }
    result
}

#[test]
#[allow(deprecated)]
#[ignore = "requires an isolated Wayland compositor and temporary profile"]
fn calculator_ui_flow() {
    gtk::init().unwrap();
    let config = env::var("ANYRUN_UI_CONFIG").expect("temporary config");
    let app = gtk::Application::new(Some("org.anyrun.calculator.test"), gio::ApplicationFlags::NON_UNIQUE);
    app.register(None::<&gio::Cancellable>).unwrap();
    let sender = App::launch(&app, AppInit {
        args: Args::parse_from(["anyrun", "--config-dir", &config]),
        stdin: Vec::new(), env: Vec::new(),
    }, None);
    let window = app.windows().into_iter().next().unwrap();
    let canvas = window.child().unwrap().downcast::<gtk::Overlay>().unwrap();
    let main = canvas.first_child().unwrap().downcast::<gtk::Box>().unwrap();
    let entry = main.first_child().unwrap().downcast::<gtk::Text>().unwrap();
    let enter = || sender.emit(AppMsg::KeyPressed { key: gdk::Key::Return, modifier: gdk::ModifierType::empty() });
    let snapshot = |name: &str| {
        if let Some(directory) = env::var_os("ANYRUN_UI_PREVIEW") {
            let bounds = main.compute_bounds(&window).unwrap();
            let geometry = format!("{},{} {}x{}", bounds.x() as i32 - 4, bounds.y() as i32 - 4,
                bounds.width() as i32 + 8, bounds.height() as i32 + 8);
            let status = std::process::Command::new("grim").args(["-g", &geometry])
                .arg(PathBuf::from(directory).join(name)).status().unwrap();
            assert!(status.success());
        }
    };
    pump_until(|| window.is_mapped());
    pump_for(300);
    assert!(!main.has_css_class("has-results"));
    assert_eq!(entry.style_context().border().bottom(), 0);
    assert_eq!(main.style_context().border().bottom(), 2);
    snapshot("empty-border.png");

    entry.set_text("=2+2");
    pump_for(150);
    assert!(!main.has_css_class("has-results"), "Typing must not produce a live result row");
    enter();
    pump_until(|| entry.text() == "=4" && main.has_css_class("has-results"));
    assert!(window.is_visible());
    assert_eq!(entry.position(), 2);
    assert!(entry.has_css_class("calculated"));
    assert_eq!(entry.style_context().border().bottom(), 2);
    enter();
    pump_for(150);
    assert_eq!(labels(main.upcast_ref()).iter().filter(|l| l.has_css_class("calculation-operation")).count(), 1);

    entry.set_text("=4*3");
    pump_for(100);
    enter();
    pump_until(|| entry.text() == "=12" && main.has_css_class("has-results"));
    entry.set_text("=2 +");
    pump_for(100);
    enter();
    pump_for(650);
    assert_eq!(entry.text(), "=2 +");
    assert_eq!(labels(main.upcast_ref()).iter().filter(|l| l.has_css_class("calculation-operation")).count(), 2);

    entry.set_text("=");
    pump_until(|| labels(main.upcast_ref()).iter().filter(|l| l.has_css_class("calculation-operation")).count() == 2);
    let operation_labels: Vec<_> = labels(main.upcast_ref()).into_iter().filter(|l| l.has_css_class("calculation-operation")).collect();
    assert_eq!(operation_labels[0].text(), "4*3");
    assert_eq!(operation_labels[1].text(), "2+2");
    assert_eq!(operation_labels[0].pango_context().font_description().unwrap().size(), 16 * gtk::pango::SCALE);
    snapshot("calculator-history.png");
    enter();
    pump_until(|| entry.text() == "=12");
    assert!(entry.has_css_class("calculated"));

    entry.set_text("no-results-for-this-search");
    pump_until(|| !main.has_css_class("has-results"));
    assert!(!main.has_css_class("has-results"));
    assert_eq!(entry.style_context().border().bottom(), 0);
    entry.set_text("");
    pump_for(100);
    assert!(!main.has_css_class("has-results"));
    sender.emit(AppMsg::Action(Action::Close));
    pump_until(|| !window.is_visible());
}
