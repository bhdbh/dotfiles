use gtk::{gdk, gio, glib, prelude::*};
use gtk4 as gtk;
use gtk4_layer_shell::{Edge, KeyboardMode, LayerShell};
use relm4::Sender;
use std::{cell::{Cell, RefCell}, rc::Rc};

use crate::{app::AppMsg, config::{Config, Layer}};

#[derive(Debug)]
pub struct MonitorIdentity {
    pub connector: String,
    pub description: String,
}

pub fn choose_monitor(monitors: &[MonitorIdentity], preferred: Option<&str>, fallback: Option<&str>) -> Option<usize> {
    preferred.and_then(|preferred| monitors.iter().position(|monitor| {
        // wlroots appends the connector to the hardware description. Match the
        // complete hardware identity, including serial, independently of port.
        monitor.description.strip_suffix(&format!(" ({})", monitor.connector))
            .unwrap_or(&monitor.description) == preferred
    })).or_else(|| fallback.and_then(|fallback| monitors.iter().position(|monitor| monitor.connector == fallback)))
        .or_else(|| monitors.iter().position(|monitor| ["eDP-", "LVDS-", "DSI-"].iter()
            .any(|prefix| monitor.connector.starts_with(prefix))))
        .or_else(|| (!monitors.is_empty()).then_some(0))
}

#[derive(Debug, PartialEq, Eq)]
pub struct PanelGeometry {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}

pub fn panel_geometry(config: &Config, monitor_width: u32, monitor_height: u32) -> PanelGeometry {
    let monitor_width = monitor_width.max(1).min(i32::MAX as u32);
    let monitor_height = monitor_height.max(1).min(i32::MAX as u32);
    let width = config.width.to_val(monitor_width).clamp(1, monitor_width as i32);
    let height = config.height.to_val(monitor_height).clamp(1, monitor_height as i32);
    PanelGeometry {
        x: (config.x.to_val(monitor_width) - width / 2).clamp(0, monitor_width as i32 - width),
        y: (config.y.to_val(monitor_height) - height / 2).clamp(0, monitor_height as i32 - height),
        width, height,
    }
}

pub fn constrain_panel(panel: &gtk::Box, geometry: &PanelGeometry, monitor_width: i32, fullscreen: bool) {
    panel.set_halign(gtk::Align::Fill);
    panel.set_hexpand(true);
    panel.set_size_request(geometry.width, geometry.height);
    panel.set_margin_start(if fullscreen { geometry.x } else { 0 });
    panel.set_margin_top(if fullscreen { geometry.y } else { 0 });
    panel.set_margin_end(if fullscreen { monitor_width - geometry.x - geometry.width } else { 0 });
    panel.set_margin_bottom(0);
}

pub struct Screens {
    monitors: gio::ListModel,
    list_handler: Option<glib::SignalHandlerId>,
    monitor_handlers: Vec<(gdk::Monitor, glib::SignalHandlerId)>,
    backdrops: Vec<(gdk::Monitor, gtk::Window)>,
    backdrop_frames: Vec<(gtk::Window, Rc<Cell<bool>>)>,
    pointer_monitor: Option<gdk::Monitor>,
    pointer_pending: bool,
    pointer_resolved: bool,
    sender: Sender<AppMsg>,
}

impl Screens {
    pub fn new(display: &gdk::Display, sender: Sender<AppMsg>) -> Self {
        let monitors = display.monitors();
        let tx = sender.clone();
        let list_handler = monitors.connect_items_changed(move |_, _, _, _| {
            tx.emit(AppMsg::MonitorsChanged);
        });
        Self { monitors, list_handler: Some(list_handler), monitor_handlers: Vec::new(),
            backdrops: Vec::new(), backdrop_frames: Vec::new(), pointer_monitor: None, pointer_pending: false,
            pointer_resolved: false, sender }
    }

    pub fn pointer_resolved(&self) -> bool { self.pointer_resolved }

    pub fn backdrops_ready(&self) -> bool {
        self.backdrop_frames.iter().all(|(_, painted)| painted.get())
    }

    pub fn refresh_pointer(&self) {
        if !self.pointer_pending || std::env::var_os("SWAYSOCK").is_none() { return; }
        // Sway may retain the old pointer focus until motion after a layer maps.
        // A zero-distance move updates its hit test without changing the cursor
        // position or keyboard focus. Run only after the surface enters an output.
        let _ = std::process::Command::new("swaymsg")
            .args(["-q", "seat * cursor move 0 0"])
            .stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null())
            .status();
    }

    pub fn locate_pointer(&mut self, root: &gtk::Window, config: &Config) {
        if self.pointer_pending || self.pointer_resolved { return; }
        // Map the non-keyboard overlays first. Wayland sends pointer-enter to
        // the one under the mouse, even when keyboard focus is on another output.
        self.pointer_pending = true;
        self.sync(root, config);
        self.show_backdrops(root);
        let tx = self.sender.clone();
        glib::timeout_add_local_once(std::time::Duration::from_millis(250), move || {
            // Preserve the configured fallback if no pointer is available.
            tx.emit(AppMsg::PointerMonitor(None));
        });
    }

    pub fn resolve_pointer(&mut self, connector: Option<String>) -> bool {
        if !self.pointer_pending { return false; }
        self.pointer_pending = false;
        self.pointer_resolved = true;
        self.pointer_monitor = connector.and_then(|connector| {
            (0..self.monitors.n_items())
                .filter_map(|index| self.monitors.item(index)?.downcast::<gdk::Monitor>().ok())
                .find(|monitor| monitor.is_valid() && monitor.connector().as_deref() == Some(connector.as_str()))
        });
        true
    }

    pub fn sync(&mut self, root: &gtk::Window, config: &Config) -> Option<gdk::Monitor> {
        for (monitor, handler) in self.monitor_handlers.drain(..) { monitor.disconnect(handler); }
        let monitors: Vec<gdk::Monitor> = (0..self.monitors.n_items())
            .filter_map(|index| self.monitors.item(index)?.downcast::<gdk::Monitor>().ok())
            .filter(|monitor| monitor.is_valid()).collect();
        for monitor in &monitors {
            let tx = self.sender.clone();
            self.monitor_handlers.push((monitor.clone(), monitor.connect_geometry_notify(move |_| {
                tx.emit(AppMsg::MonitorsChanged);
            })));
            let tx = self.sender.clone();
            self.monitor_handlers.push((monitor.clone(), monitor.connect_description_notify(move |_| {
                tx.emit(AppMsg::MonitorsChanged);
            })));
            let tx = self.sender.clone();
            self.monitor_handlers.push((monitor.clone(), monitor.connect_connector_notify(move |_| {
                tx.emit(AppMsg::MonitorsChanged);
            })));
        }
        let identities: Vec<_> = monitors.iter().map(|monitor| MonitorIdentity {
            connector: monitor.connector().map(|s| s.to_string()).unwrap_or_default(),
            description: monitor.description().map(|s| s.to_string()).unwrap_or_default(),
        }).collect();
        let selected = if self.pointer_pending { None } else {
            self.pointer_monitor.as_ref().filter(|monitor| monitors.contains(monitor)).cloned()
                .or_else(|| choose_monitor(&identities, config.preferred_monitor.as_deref(), config.fallback_monitor.as_deref())
                    .map(|index| monitors[index].clone()))
        };
        if root.monitor() != selected { root.set_monitor(selected.as_ref()); }

        self.backdrops.retain(|(monitor, window)| {
            // Keep the selected output's backdrop too: it already owns the dim
            // buffer used to locate the pointer. Replacing it before the main
            // window's first buffer exposes the desktop between those commits.
            let keep = monitors.contains(monitor) &&
                (config.close_on_click || self.pointer_pending && selected.as_ref() != Some(monitor));
            if !keep { window.destroy(); }
            keep
        });
        self.backdrop_frames.retain(|(window, _)| self.backdrops.iter().any(|(_, backdrop)| backdrop == window));
        if config.close_on_click || self.pointer_pending {
            for monitor in &monitors {
                if !config.close_on_click && selected.as_ref() == Some(monitor)
                    || self.backdrops.iter().any(|(existing, _)| existing == monitor) {
                    continue;
                }
                let window = gtk::Window::new();
                window.init_layer_shell();
                window.set_monitor(Some(monitor));
                window.set_layer(match config.layer {
                    Layer::Background => gtk4_layer_shell::Layer::Background,
                    Layer::Bottom => gtk4_layer_shell::Layer::Bottom,
                    Layer::Top => gtk4_layer_shell::Layer::Top,
                    Layer::Overlay => gtk4_layer_shell::Layer::Overlay,
                });
                window.set_namespace(Some("anyrun-backdrop"));
                window.set_keyboard_mode(KeyboardMode::None);
                window.set_focusable(false);
                window.set_decorated(false);
                window.set_exclusive_zone(-1);
                for edge in [Edge::Left, Edge::Right, Edge::Top, Edge::Bottom] { window.set_anchor(edge, true); }
                if config.close_on_click {
                    window.set_css_classes(&["background", "fullscreen", "anyrun-backdrop"]);
                } else {
                    let css = gtk::CssProvider::new();
                    css.load_from_string("window { background: transparent; }");
                    #[allow(deprecated)]
                    window.style_context().add_provider(&css, gtk::STYLE_PROVIDER_PRIORITY_USER);
                }
                // A full-size target captures pointer input, including motion,
                // without receiving keyboard focus or forwarding input to apps.
                let surface = gtk::Box::new(gtk::Orientation::Vertical, 0);
                surface.set_hexpand(true);
                surface.set_vexpand(true);
                surface.set_can_target(true);
                window.set_child(Some(&surface));
                // Mapping only schedules a buffer. Wait for the compositor's
                // first frame acknowledgement before releasing the panel, so
                // a slower output cannot acquire its backdrop after the field.
                let painted = Rc::new(Cell::new(false));
                let first_frame = Rc::new(Cell::new(None));
                let ready = painted.clone();
                let submitted = first_frame.clone();
                let tx = self.sender.clone();
                window.add_tick_callback(move |_, clock| {
                    if submitted.get().and_then(|frame| clock.timings(frame)).is_some_and(|timings| timings.is_complete()) {
                        ready.set(true);
                        tx.emit(AppMsg::Present);
                        glib::ControlFlow::Break
                    } else {
                        glib::ControlFlow::Continue
                    }
                });
                window.connect_map(move |window| {
                    let Some(clock) = window.frame_clock() else { return; };
                    let window = window.downgrade();
                    let submitted = first_frame.clone();
                    let handler = Rc::new(RefCell::new(None));
                    let registered = handler.clone();
                    *handler.borrow_mut() = Some(clock.connect_after_paint(move |clock| {
                        let window = window.upgrade();
                        if window.is_none() || window.as_ref().is_some_and(|window| window.is_mapped() && window.width() > 0 && window.height() > 0) {
                            if let Some(handler) = registered.borrow_mut().take() { clock.disconnect(handler); }
                            if window.is_none() { return; }
                            submitted.set(Some(clock.frame_counter()));
                        }
                    }));
                });
                self.backdrop_frames.push((window.clone(), painted));
                let tx = self.sender.clone();
                window.connect_realize(move |window| {
                    if let Some(surface) = window.surface() {
                        let tx = tx.clone();
                        surface.connect_enter_monitor(move |_, _| tx.emit(AppMsg::RefreshPointer));
                        surface.connect_layout(|surface, width, height| {
                            let bounds = gtk::cairo::RectangleInt::new(0, 0, width, height);
                            surface.set_input_region(&gtk::cairo::Region::create_rectangle(&bounds));
                        });
                    }
                });
                let click = gtk::GestureClick::new();
                click.set_button(0);
                let tx = self.sender.clone();
                click.connect_pressed(move |_, _, _, _| {
                    tx.emit(AppMsg::Action(crate::config::Action::Close));
                });
                window.add_controller(click);
                let motion = gtk::EventControllerMotion::new();
                let tx = self.sender.clone();
                let target = monitor.connector().map(|connector| connector.to_string());
                motion.connect_enter(move |_, _, _| {
                    tx.emit(AppMsg::PointerMonitor(target.clone()));
                });
                window.add_controller(motion);
                self.backdrops.push((monitor.clone(), window));
            }
        }
        if root.is_visible() { self.show_backdrops(root); }
        selected
    }

    pub fn show_backdrops(&self, root: &gtk::Window) {
        let application = root.application();
        for (_, window) in &self.backdrops {
            if window.application().is_none() { window.set_application(application.as_ref()); }
            window.set_visible(true);
        }
    }

    pub fn close(&mut self) {
        self.pointer_pending = false;
        if let Some(handler) = self.list_handler.take() { self.monitors.disconnect(handler); }
        for (monitor, handler) in self.monitor_handlers.drain(..) { monitor.disconnect(handler); }
        for (_, window) in self.backdrops.drain(..) { window.destroy(); }
        self.backdrop_frames.clear();
    }
}

impl Drop for Screens {
    fn drop(&mut self) { self.close(); }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::RelativeNum::{Absolute, Fraction};

    const PREFERRED: &str = "Acer Technologies QG241Y EXAMPLE000001";

    fn laptop() -> MonitorIdentity { MonitorIdentity {
        connector: "eDP-1".into(), description: "AU Optronics 0x81EC (eDP-1)".into(),
    }}

    fn hdmi(serial: &str) -> MonitorIdentity { MonitorIdentity {
        connector: "HDMI-A-1".into(), description: format!("Acer Technologies QG241Y {serial} (HDMI-A-1)"),
    }}

    #[test]
    fn selects_the_known_hdmi_independent_of_monitor_order() {
        assert_eq!(choose_monitor(&[laptop(), hdmi("EXAMPLE000001")], Some(PREFERRED), Some("eDP-1")), Some(1));
        assert_eq!(choose_monitor(&[hdmi("EXAMPLE000001"), laptop()], Some(PREFERRED), Some("eDP-1")), Some(0));
    }

    #[test]
    fn another_hdmi_even_with_the_same_model_does_not_take_preference() {
        assert_eq!(choose_monitor(&[hdmi("OTHER-SERIAL"), laptop()], Some(PREFERRED), Some("eDP-1")), Some(1));
        assert_eq!(choose_monitor(&[laptop()], Some(PREFERRED), Some("eDP-1")), Some(0));
    }

    #[test]
    fn identity_is_independent_of_the_connector_and_supports_bare_descriptions() {
        let moved = MonitorIdentity { connector: "DP-2".into(), description: format!("{PREFERRED} (DP-2)") };
        let bare = MonitorIdentity { connector: "HDMI-A-2".into(), description: PREFERRED.into() };
        assert_eq!(choose_monitor(&[laptop(), moved], Some(PREFERRED), Some("eDP-1")), Some(1));
        assert_eq!(choose_monitor(&[laptop(), bare], Some(PREFERRED), Some("eDP-1")), Some(1));
    }

    #[test]
    fn falls_back_to_an_available_screen_when_the_laptop_is_disabled() {
        assert_eq!(choose_monitor(&[hdmi("OTHER")], Some(PREFERRED), Some("eDP-1")), Some(0));
        assert_eq!(choose_monitor(&[], Some(PREFERRED), Some("eDP-1")), None);
    }

    #[test]
    fn recognizes_other_builtin_connector_names() {
        let builtin = MonitorIdentity { connector: "eDP-2".into(), description: "Laptop".into() };
        assert_eq!(choose_monitor(&[hdmi("OTHER"), builtin], Some(PREFERRED), Some("eDP-1")), Some(1));
    }

    #[test]
    fn placement_uses_only_the_selected_monitors_logical_dimensions() {
        let config = Config { width: Absolute(480), height: Absolute(1), x: Fraction(0.5), y: Fraction(0.5), ..Config::default() };
        assert_eq!(panel_geometry(&config, 1920, 1080), PanelGeometry { x: 720, y: 540, width: 480, height: 1 });
        assert_eq!(panel_geometry(&config, 1366, 768), PanelGeometry { x: 443, y: 384, width: 480, height: 1 });
        assert_eq!(panel_geometry(&config, 1536, 864).x, 528);
    }

    #[test]
    fn keeps_the_panel_inside_small_or_unusually_configured_screens() {
        let config = Config { width: Absolute(480), height: Absolute(1), x: Fraction(1.0), y: Fraction(-1.0), ..Config::default() };
        assert_eq!(panel_geometry(&config, 320, 240), PanelGeometry { x: 0, y: 0, width: 320, height: 1 });
    }

    #[test]
    fn reads_the_installed_preference_without_initializing_a_display() {
        let config: Config = ron::from_str(include_str!("../../../home/.config/anyrun/config.ron")).unwrap();
        assert_eq!(config.preferred_monitor.as_deref(), Some("HDMI-A-1"));
        assert_eq!(config.fallback_monitor.as_deref(), Some("eDP-1"));
    }

    #[test]
    fn follow_mouse_is_optional_and_can_be_enabled() {
        let old: Config = ron::from_str("Config()").unwrap();
        assert!(!old.follow_mouse);
        let enabled: Config = ron::from_str("Config(follow_mouse: true)").unwrap();
        assert!(enabled.follow_mouse);
    }

    #[test]
    #[ignore = "requires isolated two-output Sway with focus_follows_mouse no"]
    fn follows_pointer_keeps_other_overlays_and_latches_selection() {
        gtk::init().unwrap();
        let display = gdk::Display::default().unwrap();
        let context = glib::MainContext::default();
        let config = Config {
            follow_mouse: true, close_on_click: true,
            fallback_monitor: Some("HEADLESS-1".into()), ..Config::default()
        };
        for (target, x, other) in [("HEADLESS-1", 100, "HEADLESS-2"), ("HEADLESS-2", 900, "HEADLESS-1")] {
            let command = format!("seat seat0 cursor set {x} 100; focus output {other}");
            assert!(std::process::Command::new("swaymsg").arg(command).output().unwrap().status.success());
            let (sender, receiver) = relm4::channel();
            let root = gtk::Window::new();
            root.init_layer_shell();
            let mut screens = Screens::new(&display, sender);
            screens.locate_pointer(&root, &config);
            assert_eq!(screens.backdrops.len(), 2);
            let monitor = context.block_on(async {
                loop {
                    match receiver.recv().await {
                        Some(AppMsg::PointerMonitor(monitor)) => break monitor,
                        Some(AppMsg::RefreshPointer) => screens.refresh_pointer(),
                        _ => (),
                    }
                }
            });
            let connector = monitor.expect("Expected a real pointer-enter event, not the timeout fallback");
            assert_eq!(connector, target);
            assert!(screens.resolve_pointer(Some(connector)));
            let monitor = screens.sync(&root, &config).unwrap();
            assert_eq!(root.monitor().as_ref(), Some(&monitor));
            assert_eq!(screens.backdrops.len(), 2);
            let selected_backdrop = screens.backdrops.iter().find(|(candidate, _)| candidate == &monitor).unwrap().1.clone();
            assert!(selected_backdrop.is_visible() && selected_backdrop.is_mapped(),
                "The pointer backdrop must stay mapped while the main window is still hidden");
            assert!(!root.is_visible());
            let (other_monitor, backdrop) = screens.backdrops.iter().find(|(candidate, _)| candidate != &monitor).unwrap();
            assert_eq!(other_monitor.connector().as_deref(), Some(other));
            assert!(backdrop.is_visible());
            assert_eq!(backdrop.keyboard_mode(), KeyboardMode::None);
            assert!(!backdrop.is_focusable());
            // Later pointer events must not move an already opened launcher.
            assert!(!screens.resolve_pointer(other_monitor.connector().map(|connector| connector.to_string())));
            assert_eq!(screens.sync(&root, &config), Some(monitor));
            screens.close();
            assert!(screens.backdrops.is_empty());
            root.destroy();
            while context.pending() { context.iteration(false); }
        }
    }

    #[test]
    #[ignore = "requires an isolated compositor and fixture provider"]
    fn launcher_uses_pointer_output_and_closes_all_overlays() {
        use crate::{app::{App, AppInit}, config::Action, Args};
        use clap::Parser;
        use std::time::{Duration, Instant};

        gtk::init().unwrap();
        let config = std::env::var("ANYRUN_UI_CONFIG").unwrap();
        let target = std::env::var("ANYRUN_POINTER_OUTPUT").unwrap();
        let app = gtk::Application::new(Some("org.anyrun.pointer.test"), gio::ApplicationFlags::NON_UNIQUE);
        app.register(None::<&gio::Cancellable>).unwrap();
        let sender = App::launch(&app, AppInit {
            args: Args::parse_from(["anyrun", "--config-dir", &config]),
            stdin: Vec::new(), env: Vec::new(),
        }, None);
        let root = app.windows().into_iter().next().unwrap();
        let context = glib::MainContext::default();
        let deadline = Instant::now() + Duration::from_secs(10);
        let on_target = || root.is_mapped() && root.surface()
            .and_then(|surface| WidgetExt::display(&root).monitor_at_surface(&surface))
            .and_then(|monitor| monitor.connector()).as_deref() == Some(target.as_str());
        while !on_target() {
            while context.pending() { context.iteration(false); }
            assert!(Instant::now() < deadline, "Launcher did not appear");
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(root.monitor().unwrap().connector().as_deref(), Some(target.as_str()));
        let actual = WidgetExt::display(&root).monitor_at_surface(&root.surface().unwrap()).unwrap();
        assert_eq!(actual.connector().as_deref(), Some(target.as_str()));
        let windows = app.windows();
        assert_eq!(windows.len(), 3);
        let backdrops: Vec<_> = windows.iter().filter(|window| **window != root).collect();
        assert!(backdrops.iter().any(|window| window.monitor() == root.monitor()));
        assert!(backdrops.iter().all(|window| window.is_visible() && window.keyboard_mode() == KeyboardMode::None));
        if let Ok(x) = std::env::var("ANYRUN_POINTER_OTHER_X") {
            let command = format!("seat seat0 cursor set {x} 100");
            assert!(std::process::Command::new("swaymsg").arg(command).output().unwrap().status.success());
            let wait = Instant::now() + Duration::from_millis(80);
            while Instant::now() < wait {
                while context.pending() { context.iteration(false); }
                std::thread::sleep(Duration::from_millis(5));
            }
            assert_eq!(root.monitor().unwrap().connector().as_deref(), Some(target.as_str()));
            assert!(std::process::Command::new("swaymsg")
                .arg("seat seat0 cursor press button1; seat seat0 cursor release button1")
                .output().unwrap().status.success());
        } else {
            sender.emit(AppMsg::Action(Action::Close));
        }
        while root.is_visible() {
            while context.pending() { context.iteration(false); }
            assert!(Instant::now() < deadline, "Launcher did not close");
        }
        assert!(backdrops.iter().all(|window| !window.is_visible()));
    }

    #[test]
    #[ignore = "requires a Wayland connection; checks properties without mapping any windows"]
    fn live_monitor_pinning_without_mapping_windows() {
        gtk::init().unwrap();
        let display = gdk::Display::default().unwrap();
        let (sender, _receiver) = relm4::channel();
        let root = gtk::Window::new();
        root.init_layer_shell();
        let mut screens = Screens::new(&display, sender);
        let mut config: Config = ron::from_str(include_str!("../../../home/.config/anyrun/config.ron")).unwrap();
        let chosen = screens.sync(&root, &config).unwrap();
        assert_eq!(root.monitor().as_ref(), Some(&chosen));
        assert!(!root.is_visible() && !root.is_mapped());
        let monitors: Vec<gdk::Monitor> = (0..screens.monitors.n_items())
            .filter_map(|index| screens.monitors.item(index)?.downcast().ok()).collect();
        assert_eq!(screens.backdrops.len(), monitors.len());
        for (monitor, window) in &screens.backdrops {
            assert_eq!(window.monitor().as_ref(), Some(monitor));
            assert_eq!(window.keyboard_mode(), KeyboardMode::None);
            assert_eq!(window.layer(), gtk4_layer_shell::Layer::Overlay);
            assert!(!window.is_focusable());
            assert!(!window.is_visible() && !window.is_mapped());
            for edge in [Edge::Left, Edge::Right, Edge::Top, Edge::Bottom] { assert!(window.is_anchor(edge)); }
        }
        // Simulate a different HDMI identity using the real connected screens.
        config.preferred_monitor = Some("Acer Technologies QG241Y DIFFERENT-SERIAL".into());
        let fallback = screens.sync(&root, &config).unwrap();
        if let Some(builtin) = monitors.iter().find(|monitor| monitor.connector().as_deref() == Some("eDP-1")) {
            assert_eq!(&fallback, builtin);
        }
        assert_eq!(screens.backdrops.len(), monitors.len());
        assert!(screens.backdrops.iter().all(|(_, window)| !window.is_visible() && !window.is_mapped()));
        screens.close();
        assert!(screens.backdrops.is_empty());
        assert!(screens.monitor_handlers.is_empty() && screens.list_handler.is_none());
        root.destroy();
    }

    #[test]
    #[ignore = "requires an isolated two-output compositor"]
    fn backdrop_readiness_requires_a_drawn_frame_and_resets_when_closed() {
        use std::time::{Duration, Instant};
        gtk::init().unwrap();
        let display = gdk::Display::default().unwrap();
        let (sender, _receiver) = relm4::channel();
        let root = gtk::Window::new();
        root.init_layer_shell();
        let mut screens = Screens::new(&display, sender);
        let config = Config { close_on_click: true, ..Config::default() };
        screens.sync(&root, &config);
        assert!(!screens.backdrops.is_empty());
        assert!(!screens.backdrops_ready());
        screens.show_backdrops(&root);
        assert!(!screens.backdrops_ready(), "Making a window visible must not release the panel before its first buffer");
        let context = glib::MainContext::default();
        let deadline = Instant::now() + Duration::from_secs(5);
        while !screens.backdrops_ready() {
            while context.pending() { context.iteration(false); }
            assert!(Instant::now() < deadline, "Backdrops never completed their first paint");
            std::thread::sleep(Duration::from_millis(1));
        }
        assert!(!root.is_visible());
        assert!(screens.backdrops.iter().all(|(_, window)| window.is_mapped()));
        screens.close();
        assert!(screens.backdrop_frames.is_empty());
        assert!(screens.backdrops_ready());
        root.destroy();
    }

    #[test]
    #[ignore = "requires a display; measures layout without mapping a window"]
    fn panel_shrinks_long_content_without_mapping_windows() {
        gtk::init().unwrap();
        let canvas = gtk::Overlay::new();
        let panel = gtk::Box::new(gtk::Orientation::Vertical, 0);
        panel.set_valign(gtk::Align::Start);
        let label = gtk::Label::new(Some(&"a-very-long-filename/".repeat(100)));
        label.set_ellipsize(gtk::pango::EllipsizeMode::Middle);
        panel.append(&label);
        canvas.add_overlay(&panel);
        canvas.set_measure_overlay(&panel, false);
        let geometry = PanelGeometry { x: 720, y: 540, width: 480, height: 1 };
        constrain_panel(&panel, &geometry, 1920, true);
        canvas.measure(gtk::Orientation::Horizontal, -1);
        canvas.measure(gtk::Orientation::Vertical, 1920);
        canvas.allocate(1920, 1080, -1, None);
        assert_eq!(panel.width(), 480);
        assert_eq!(label.width(), 480);
        assert!(!canvas.is_mapped() && !panel.is_mapped());
    }
}
