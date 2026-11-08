use crate::{
    config::{self, Action, Config, Keybind},
    plugin_box::{PluginBox, PluginBoxInput, PluginBoxOutput, PluginMatch},
    provider, Args, DaemonState,
};
use anyrun_interface::HandleResult;
use anyrun_provider_ipc as ipc;
use gtk::{gdk, gio, glib, prelude::*};
use gtk4 as gtk;
use gtk4_layer_shell::{Edge, LayerShell};
use relm4::{prelude::*, ComponentBuilder, Sender};
use serde::{Deserialize, Serialize};
use std::{
    cell::RefCell,
    env, fs,
    io::{self, Write},
    path::{Path, PathBuf},
    rc::Rc,
    sync::Arc,
};
use tokio::sync::mpsc;

const DEFAULT_CSS: &str = include_str!("../res/style.css");

#[derive(Deserialize, Serialize)]
pub enum PostRunAction {
    Stdout(Vec<u8>),
    None,
}

#[derive(Deserialize, Serialize, Debug)]
pub enum ShowError {
    AlreadyShowed,
}

#[derive(Debug)]
pub enum AppMsg {
    Show {
        width: u32,
        height: u32,
    },
    KeyPressed {
        key: gdk::Key,
        modifier: gdk::ModifierType,
    },
    Action(Action),
    EntryChanged(String),
    SearchDue(u64),
    RecallCalculation(String),
    MonitorsChanged,
    PointerMonitor(Option<String>),
    RefreshPointer,
    Present,
    PluginOutput(PluginBoxOutput),
}

#[derive(Deserialize, Serialize)]
pub struct AppInit {
    pub args: Args,
    pub stdin: Vec<u8>,
    pub env: Vec<(String, String)>,
}

pub struct App {
    config: Arc<Config>,
    daemon_ctx: Option<(Rc<RefCell<DaemonState>>, gio::DBusMethodInvocation)>,
    plugins: FactoryVecDeque<PluginBox>,
    post_run_action: PostRunAction,
    tx: mpsc::Sender<anyrun_provider_ipc::Request>,
    css_provider: gtk::CssProvider,
    initial_history: bool,
    calculated_text: Option<String>,
    pending_calculation: Option<String>,
    screens: crate::screens::Screens,
    startup: crate::startup::Startup,
    search: crate::search::Search<abi_stable::std_types::RVec<anyrun_interface::Match>>,
    exclusive_plugin: Option<anyrun_interface::PluginInfo>,
    select_when_ready: bool,
}

impl App {
    fn begin_search(&mut self, revision: u64) {
        if self.search.begin(revision, self.plugins.len().saturating_sub(1)) {
            let _ = self.tx.blocking_send(ipc::Request::Query { text: self.search.text().to_owned() });
        }
    }

    fn handle_selection(&self, plugin: &PluginBox, item: &PluginMatch, entry: &gtk::Text, sender: &ComponentSender<Self>) {
        if crate::prefix_help::is_help(&entry.text()) { return; }
        if !self.initial_history && !self.search.current() { return; }
        let query = item.history.as_ref().map(|(_, query)| query.as_str());
        if plugin.plugin_info.name == "Rink" || query.is_some_and(|query| crate::calculator::expression(query).is_some()) {
            let current = entry.text();
            let prefix = if crate::calculator::expression(&current).is_some() { current.as_str() }
                else { query.unwrap_or("= ") };
            sender.input(AppMsg::RecallCalculation(crate::calculator::result_text(prefix, &item.content.title)));
            return;
        }
        let request = if let Some((index, query)) = &item.history {
            entry.set_text(query);
            ipc::Request::Replay { index: *index }
        } else {
            ipc::Request::Handle {
                plugin: plugin.plugin_info.clone(),
                selection: item.content.clone(),
            }
        };
        let _ = self.tx.blocking_send(request);
    }

    pub fn launch(
        app: &gtk::Application,
        app_init: AppInit,
        daemon_ctx: Option<(Rc<RefCell<DaemonState>>, gio::DBusMethodInvocation)>,
    ) -> Sender<AppMsg> {
        let builder = ComponentBuilder::<App>::default();

        let connector = builder.launch((app_init, daemon_ctx));

        let mut controller = connector.detach();
        let window = controller.widget();
        app.add_window(window);

        controller.detach_runtime();
        controller.sender().clone()
    }

    /// Helper function to get the combined matches of all the plugins
    fn combined_matches(&self) -> Vec<(&PluginBox, &PluginMatch)> {
        self.plugins
            .iter()
            .flat_map(|plugin| {
                plugin
                    .matches
                    .iter()
                    .map(|plugin_match| (plugin, plugin_match))
                    .collect::<Vec<_>>()
            })
            .collect()
    }

    fn current_selection(&self) -> Option<(usize, &PluginBox, &PluginMatch)> {
        self.plugins
            .iter()
            .find_map(|plugin| {
                plugin
                    .matches
                    .widget()
                    .selected_row()
                    .map(|row| (plugin, row))
            })
            .map(|(plugin, row)| {
                let (i, plugin_match) = self
                    .combined_matches()
                    .iter()
                    .enumerate()
                    .find_map(|(i, (_, plugin_match))| {
                        if plugin_match.row == row {
                            Some((i, *plugin_match))
                        } else {
                            None
                        }
                    })
                    .unwrap(); // Unwrap is safe since we just obtained the selected one
                (i, plugin, plugin_match)
            })
    }
}

#[relm4::component(pub)]
impl Component for App {
    type Input = AppMsg;
    type Output = ();
    type Init = (
        AppInit,
        Option<(Rc<RefCell<DaemonState>>, gio::DBusMethodInvocation)>,
    );
    type CommandOutput = anyrun_provider_ipc::Response;

    view! {
        gtk::Window {
            init_layer_shell: (),
            set_layer: match config.layer {
                config::Layer::Background => gtk4_layer_shell::Layer::Background,
                config::Layer::Bottom => gtk4_layer_shell::Layer::Bottom,
                config::Layer::Top => gtk4_layer_shell::Layer::Top,
                config::Layer::Overlay => gtk4_layer_shell::Layer::Overlay,
            },
            set_keyboard_mode: match config.keyboard_mode {
                config::KeyboardMode::Exclusive => gtk4_layer_shell::KeyboardMode::Exclusive,
                config::KeyboardMode::OnDemand => gtk4_layer_shell::KeyboardMode::OnDemand,
            },
            set_namespace: Some("anyrun"),
            set_exclusive_zone: if config.ignore_exclusive_zones {
                -1
            } else {
                0
            },

            connect_realize[sender] => move |win| {
                let surface = win.surface().unwrap();
                let sender = sender.clone();
                let window = win.downgrade();
                surface.connect_enter_monitor(move |_, monitor| {
                    if let Some(window) = window.upgrade() {
                        if window.monitor().as_ref().is_some_and(|target| target != monitor) { return; }
                    }
                    sender.input(AppMsg::Show {
                        width: monitor.geometry().width() as u32,
                        height: monitor.geometry().height() as u32,
                    });
                });
            },

            add_controller = gtk::EventControllerKey {
                set_propagation_phase: gtk::PropagationPhase::Capture,
                connect_key_pressed[sender] => move |_, key, _, modifier| {
                    // Handle navigation before GTK can move or activate a focused row.
                    if matches!(key, gdk::Key::Up | gdk::Key::Down | gdk::Key::KP_Up | gdk::Key::KP_Down) {
                        return glib::Propagation::Stop;
                    }
                    sender.input(AppMsg::KeyPressed { key, modifier });
                    match key {
                        gdk::Key::Return | gdk::Key::KP_Enter | gdk::Key::Tab => glib::Propagation::Stop,
                        _ => glib::Propagation::Proceed,
                    }
                }
            },

            add_controller = gtk::GestureClick {
                connect_pressed[sender, config] => move |gesture, _, x, y| {
                    let mut hit = gesture.widget().and_then(|widget| {
                        widget.pick(x, y, gtk::PickFlags::DEFAULT)
                    });
                    let mut inside_main = false;

                    while let Some(widget) = hit {
                        if widget.has_css_class("main") {
                            inside_main = true;
                            break;
                        }
                        hit = widget.parent();
                    }

                    if config.close_on_click && !inside_main {
                        sender.input(AppMsg::Action(Action::Close));
                    }
                }
            },

            #[name = "canvas"]
            gtk::Overlay {
                set_hexpand: true,
                set_vexpand: true,

                #[name = "main"]
                add_overlay = &gtk::Box {
                    set_orientation: gtk::Orientation::Vertical,
                    set_halign: gtk::Align::Fill,
                    set_valign: gtk::Align::Start,
                    set_vexpand: false,
                    set_hexpand: true,
                    set_css_classes: &["main"],

                    #[name = "entry"]
                    gtk::Text {
                        set_hexpand: true,
                        set_activates_default: false,
                        connect_changed[sender] => move |entry| {
                            sender.input(AppMsg::EntryChanged(entry.text().into()));
                        },
                    },
                    #[local]
                    plugins -> gtk::Box {
                        set_orientation: gtk::Orientation::Vertical,
                        set_can_focus: false,
                        set_css_classes: &["matches"],
                        set_hexpand: true,
                    },
                    #[local]
                    help -> gtk::ListBox {
                        set_visible: false,
                    }
                }
            }
        }
    }

    fn init(
        (app_init, daemon_ctx): Self::Init,
        root: Self::Root,
        sender: relm4::ComponentSender<Self>,
    ) -> relm4::ComponentParts<Self> {
        let user_dir = env::var("XDG_CONFIG_HOME")
            .map(|c| format!("{c}/anyrun"))
            .or_else(|_| env::var("HOME").map(|h| format!("{h}/.config/anyrun")))
            .unwrap();

        let config_dir = app_init
            .args
            .config_dir
            .clone()
            .map(Some)
            .unwrap_or_else(|| {
                if PathBuf::from(&user_dir).exists() {
                    Some(user_dir.clone())
                } else {
                    ipc::CONFIG_DIRS
                        .iter()
                        .map(|path| path.to_string())
                        .find(|path| PathBuf::from(path).exists())
                }
            });

        let css_provider = gtk::CssProvider::new();

        let mut config = if let Some(config_dir) = &config_dir {
            let css_path = Path::new(&config_dir).join("style.css");
            if css_path.exists() {
                css_provider.load_from_path(css_path);
            } else {
                eprintln!("[anyrun] {config_dir}/style.css does not exist");
                css_provider.load_from_string(DEFAULT_CSS);
            };
            match fs::read(format!("{config_dir}/config.ron")) {
                Ok(content) => ron::de::from_bytes(&content).unwrap_or_else(|why| {
                    eprintln!("[anyrun] Failed to parse config file, using default values: {why}");
                    Config::default()
                }),
                Err(why) => {
                    eprintln!("[anyrun] Failed to read config file, using default values: {why}");
                    Config::default()
                }
            }
        } else {
            eprintln!("[anyrun] No config found in any searched paths");
            css_provider.load_from_string(DEFAULT_CSS);
            Config::default()
        };

        gtk::style_context_add_provider_for_display(
            &WidgetExt::display(&root),
            &css_provider,
            gtk::STYLE_PROVIDER_PRIORITY_USER,
        );

        config.merge_opt(app_init.args.config.clone());

        let config = Arc::new(config);

        let plugins = gtk::Box::builder().build();

        let plugins_factory = FactoryVecDeque::<PluginBox>::builder()
            .launch(plugins.clone())
            .forward(sender.input_sender(), AppMsg::PluginOutput);

        let (tx, rx) = mpsc::channel(10);

        sender.spawn_command(glib::clone!(
            #[strong]
            config,
            #[strong]
            config_dir,
            #[strong(rename_to = stdin)]
            app_init.stdin,
            #[strong(rename_to = env)]
            app_init.env,
            move |sender| {
                if let Err(why) = provider::worker(config, config_dir, rx, sender.clone(), stdin, env) {
                    eprintln!("[anyrun] IPC worker returned an error: {why}");
                }
                sender.emit(ipc::Response::ProviderUnavailable);
            }
        ));

        let help = crate::prefix_help::build();
        let widgets = view_output!();
        widgets.canvas.set_measure_overlay(&widgets.main, !config.close_on_click);
        if config.close_on_click {
            for edge in [Edge::Left, Edge::Right, Edge::Top, Edge::Bottom] { root.set_anchor(edge, true); }
            // A persistent backdrop owns the dimming on every output. The main
            // window adds only its panel, keeping the selected backdrop visible
            // throughout startup without blending the dim color a second time.
            let transparent = gtk::CssProvider::new();
            transparent.load_from_string("window { background: transparent; }");
            #[allow(deprecated)]
            root.style_context().add_provider(&transparent, gtk::STYLE_PROVIDER_PRIORITY_USER + 1);
        }
        let mut screens = crate::screens::Screens::new(&WidgetExt::display(&root), sender.input_sender().clone());
        // Select the monitor before the layer surface can be mapped by Sway.
        screens.sync(&root, &config);
        let startup = crate::startup::Startup::new(config.show_results_immediately);
        let model = Self {
            daemon_ctx,
            config,
            plugins: plugins_factory,
            post_run_action: PostRunAction::None,
            tx,
            css_provider,
            initial_history: true,
            calculated_text: None,
            pending_calculation: None,
            screens,
            startup,
            search: crate::search::Search::default(),
            exclusive_plugin: None,
            select_when_ready: false,
        };

        ComponentParts { model, widgets }
    }

    fn update_with_view(
        &mut self,
        widgets: &mut Self::Widgets,
        message: Self::Input,
        sender: ComponentSender<Self>,
        root: &Self::Root,
    ) {
        match message {
            AppMsg::Show {
                width: mon_width,
                height: mon_height,
            } => {
                let geometry = crate::screens::panel_geometry(&self.config, mon_width, mon_height);
                let crate::screens::PanelGeometry { x, y, width, height } = geometry;

                // Position the panel inside the selected output's full-size canvas.
                // Keep positioning relative to the output chosen for this opening.
                root.set_anchor(Edge::Left, true);
                root.set_anchor(Edge::Top, true);
                crate::screens::constrain_panel(&widgets.main, &geometry, mon_width as i32, self.config.close_on_click);

                if self.config.close_on_click {
                    root.set_default_size(mon_width as i32, mon_height as i32);
                } else {
                    root.set_default_size(width, height);
                    root.child().unwrap().set_size_request(width, height);
                    root.set_margin(Edge::Left, x);
                    root.set_margin(Edge::Top, y);
                }
                if self.startup.ready() {
                    self.screens.show_backdrops(root);
                    if !root.is_visible() && !self.screens.backdrops_ready() { return; }
                    root.set_visible(true);
                    widgets.entry.grab_focus_without_selecting();
                }
            }
            AppMsg::Present => {
                if self.startup.ready() && !root.is_visible() {
                    if self.config.follow_mouse && !self.screens.pointer_resolved() {
                        self.screens.locate_pointer(root, &self.config);
                        return;
                    }
                    if let Some(monitor) = self.screens.sync(root, &self.config) {
                        let geometry = monitor.geometry();
                        sender.input(AppMsg::Show { width: geometry.width() as u32, height: geometry.height() as u32 });
                    } else {
                        sender.input(AppMsg::Action(Action::Close));
                    }
                }
            }
            AppMsg::MonitorsChanged => {
                if self.config.follow_mouse && !self.screens.pointer_resolved() { return; }
                if let Some(monitor) = self.screens.sync(root, &self.config) {
                    let geometry = monitor.geometry();
                    sender.input(AppMsg::Show { width: geometry.width() as u32, height: geometry.height() as u32 });
                } else {
                    sender.input(AppMsg::Action(Action::Close));
                }
            }
            AppMsg::PointerMonitor(monitor) => {
                if self.screens.resolve_pointer(monitor) {
                    sender.input(AppMsg::Present);
                }
            }
            AppMsg::RefreshPointer => self.screens.refresh_pointer(),
            AppMsg::KeyPressed { key, modifier } => {
                if let Some(Keybind { action, .. }) = self.config.keybinds.iter().find(|keybind| {
                    keybind.key == key
                        && keybind.ctrl == modifier.contains(gdk::ModifierType::CONTROL_MASK)
                        && keybind.alt == modifier.contains(gdk::ModifierType::ALT_MASK)
                        && keybind.shift == modifier.contains(gdk::ModifierType::SHIFT_MASK)
                }) {
                    sender.input(AppMsg::Action(*action));
                }
            }
            AppMsg::Action(action) => {
                if crate::prefix_help::is_help(&widgets.entry.text()) && !matches!(action, Action::Close) { return; }
                match action {
                Action::Close => {
                    self.search.change(String::new());
                    self.select_when_ready = false;
                    self.startup.close();
                    root.set_visible(false);
                    self.screens.close();
                    if let Some((daemon_state, invocation)) = self.daemon_ctx.clone() {
                        invocation.return_value(Some(
                            &(
                                serde_json::to_vec(&Ok::<_, ShowError>(&self.post_run_action))
                                    .unwrap(),
                            )
                                .to_variant(),
                        ));
                        daemon_state.borrow_mut().sender = None;
                    } else {
                        match &self.post_run_action {
                            PostRunAction::Stdout(bytes) => {
                                io::stdout().lock().write_all(bytes).unwrap()
                            }
                            PostRunAction::None => (),
                        }
                        root.application().unwrap().quit();
                    }
                    // Unload the style so a new one can be loaded on next show
                    gtk::style_context_remove_provider_for_display(
                        &WidgetExt::display(root),
                        &self.css_provider,
                    );
                    root.close();
                    // FIXME: Make sure the worker has actually correctly shut down before
                    // exiting
                    let _ = self.tx.blocking_send(ipc::Request::Quit);
                    relm4::runtime_util::shutdown_all();
                }
                Action::Select => {
                    let text = widgets.entry.text().to_string();
                    if crate::calculator::expression(&text).is_some_and(|expression| !expression.is_empty()) {
                        if self.calculated_text.as_deref() != Some(text.as_str())
                            && self.pending_calculation.as_deref() != Some(text.as_str())
                        {
                            self.pending_calculation = Some(text.clone());
                            let _ = self.tx.blocking_send(ipc::Request::Calculate { text });
                        }
                    } else if !self.initial_history && !self.search.current() && !text.trim().is_empty() {
                        self.select_when_ready = true;
                        self.begin_search(self.search.revision());
                    } else if let Some((plugin, plugin_match)) = self.combined_matches().first().copied() {
                        self.handle_selection(plugin, plugin_match, &widgets.entry, &sender);
                    }
                }
                Action::Up => {
                    if !self.initial_history && !self.search.current() { return; }
                    if let Some((i, plugin, _)) = self.current_selection() {
                        let matches = self.combined_matches();
                        plugin
                            .matches
                            .widget()
                            .select_row(Option::<&gtk::ListBoxRow>::None);
                        if i > 0 {
                            let (plugin, plugin_match) = matches[i - 1];
                            plugin.matches.widget().select_row(Some(&plugin_match.row));
                        } else {
                            let (plugin, plugin_match) = matches.last().unwrap();
                            plugin.matches.widget().select_row(Some(&plugin_match.row));
                        }
                    }
                }
                Action::Down => {
                    if !self.initial_history && !self.search.current() { return; }
                    if let Some((i, plugin, _)) = self.current_selection() {
                        let matches = self.combined_matches();
                        plugin
                            .matches
                            .widget()
                            .select_row(Option::<&gtk::ListBoxRow>::None);
                        if let Some((plugin, plugin_match)) = matches.get(i + 1) {
                            plugin.matches.widget().select_row(Some(&plugin_match.row));
                        } else {
                            let (plugin, plugin_match) = matches[0];
                            plugin.matches.widget().select_row(Some(&plugin_match.row));
                        }
                    }
                }
                }
            },
            AppMsg::EntryChanged(text) => {
                // Replacing GtkText contents may queue an intermediate empty
                // value. Only the current value should clear state or query.
                if widgets.entry.text().as_str() != text { return; }
                self.initial_history = false;
                self.select_when_ready = false;
                self.exclusive_plugin = None;
                let revision = self.search.change(text.clone());
                if self.calculated_text.as_deref() != Some(text.as_str()) {
                    self.calculated_text = None;
                    widgets.entry.remove_css_class("calculated");
                }
                let help = crate::prefix_help::is_help(&text);
                widgets.help.set_visible(help);
                if help || text.trim().is_empty() {
                    self.plugins.broadcast(PluginBoxInput::Clear);
                    if help { widgets.main.add_css_class("has-results"); }
                    else { widgets.main.remove_css_class("has-results"); }
                    let _ = self.tx.blocking_send(ipc::Request::Query { text });
                } else {
                    // Keep the current rows on screen while typing and while the
                    // provider is collecting the replacement batch.
                    let sender = sender.input_sender().clone();
                    glib::timeout_add_local_once(std::time::Duration::from_millis(self.config.search_delay_ms), move || {
                        sender.emit(AppMsg::SearchDue(revision));
                    });
                }
            }
            AppMsg::SearchDue(revision) => {
                if !widgets.entry.text().trim().is_empty() && !crate::prefix_help::is_help(&widgets.entry.text()) {
                    self.begin_search(revision);
                }
            }
            AppMsg::RecallCalculation(text) => {
                self.calculated_text = Some(text.clone());
                widgets.entry.set_text(&text);
                widgets.entry.set_position(-1);
                widgets.entry.add_css_class("calculated");
                widgets.entry.grab_focus_without_selecting();
            }
            AppMsg::PluginOutput(PluginBoxOutput::MatchesLoaded { history, revision }) => {
                if let Some(revision) = revision {
                    if !self.search.rows_ready(revision) { return; }
                }
                let matches = self.combined_matches();
                if let Some((plugin, plugin_match)) = self.combined_matches().first() {
                    plugin.matches.widget().select_row(Some(&plugin_match.row));
                }

                for (index, (_plugin, plugin_match)) in matches.iter().enumerate() {
                    plugin_match.row.set_visible(self.config.max_entries.is_none_or(|max| index < max as usize));
                }
                self.plugins.broadcast(PluginBoxInput::MaybeHide);
                if (!matches.is_empty() && self.config.max_entries != Some(0)) || crate::prefix_help::is_help(&widgets.entry.text()) {
                    widgets.main.add_css_class("has-results");
                } else {
                    widgets.main.remove_css_class("has-results");
                }
                if history {
                    self.startup.history_rows_ready();
                    sender.input(AppMsg::Present);
                } else if self.select_when_ready {
                    self.select_when_ready = false;
                    sender.input(AppMsg::Action(Action::Select));
                }
            }
            AppMsg::PluginOutput(PluginBoxOutput::RowActivated(index, row_index)) => {
                if let Some(plugin) = self.plugins.iter().nth(index.current_index()) {
                    if let Some(plugin_match) = plugin.matches.iter().nth(row_index) {
                        self.handle_selection(plugin, plugin_match, &widgets.entry, &sender);
                    }
                }
            }
            // Handle clicked selections
            AppMsg::PluginOutput(PluginBoxOutput::RowSelected(index)) => {
                for (i, plugin) in self.plugins.iter().enumerate() {
                    if i != index.current_index() {
                        plugin
                            .matches
                            .widget()
                            .select_row(Option::<&gtk::ListBoxRow>::None);
                    }
                }
            }
        }
        self.update_view(widgets, sender);
    }

    fn update_cmd_with_view(
        &mut self,
        widgets: &mut Self::Widgets,
        message: Self::CommandOutput,
        sender: ComponentSender<Self>,
        root: &Self::Root,
    ) {
        match message {
            ipc::Response::ProviderUnavailable => {
                self.startup.provider_stopped();
                sender.input(AppMsg::Present);
            }
            ipc::Response::Calculated { text, calculation } => {
                if self.pending_calculation.as_deref() == Some(text.as_str()) {
                    self.pending_calculation = None;
                }
                if widgets.entry.text().as_str() != text { return; }
                if let Some(calculation) = calculation {
                    let updated = crate::calculator::result_text(&text, &calculation.result);
                    self.calculated_text = Some(updated.clone());
                    widgets.entry.set_text(&updated);
                    widgets.entry.set_position(-1);
                    widgets.entry.add_css_class("calculated");
                    if updated == text {
                        let revision = self.search.change(updated);
                        self.begin_search(revision);
                    }
                }
            }
            ipc::Response::Ready { info } => {
                let mut guard = self.plugins.guard();
                guard.push_back((anyrun_interface::PluginInfo {
                    name: "History".into(),
                    icon: "document-open-recent".into(),
                }, self.config.clone()));
                for info in info {
                    guard.push_back((info, self.config.clone()));
                }
                drop(guard);
                if self.startup.provider_ready() {
                    let _ = self.tx.blocking_send(ipc::Request::History);
                }
                sender.input(AppMsg::Present);
            }
            ipc::Response::History { entries } => {
                if self.initial_history && widgets.entry.text().trim().is_empty() {
                    self.startup.history_received();
                    self.plugins.send(0, PluginBoxInput::History(entries));
                }
            }
            ipc::Response::Matches { query, plugin, matches } => {
                // Discard queued results after clearing; only the explicit initial
                // history response may populate a launcher with an empty entry.
                if widgets.entry.text().trim().is_empty() || query != widgets.entry.text().as_str()
                    || crate::prefix_help::is_help(&widgets.entry.text()) {
                    return;
                }
                let i = self
                    .plugins
                    .iter()
                    .enumerate()
                    .find_map(|(i, plugin_box)| {
                        if plugin_box.plugin_info == plugin {
                            Some(i)
                        } else {
                            None
                        }
                    })
                    .unwrap();

                if let Some(batch) = self.search.reply(&query, i - 1, matches) {
                    self.plugins.send(0, PluginBoxInput::Clear);
                    let revision = self.search.revision();
                    for (index, matches) in batch.into_iter().enumerate() {
                        let enabled = self.exclusive_plugin.as_ref().is_none_or(|plugin| {
                            &self.plugins.get(index + 1).unwrap().plugin_info == plugin
                        });
                        self.plugins.send(index + 1, PluginBoxInput::Matches { matches, enabled, revision });
                    }
                }
            }
            ipc::Response::Handled { plugin, result } => {
                match result {
                    HandleResult::Close => sender.input(AppMsg::Action(Action::Close)),
                    HandleResult::Refresh(exclusive) => {
                        self.exclusive_plugin = exclusive.then_some(plugin);
                        self.select_when_ready = false;
                        let revision = self.search.change(widgets.entry.text().into());
                        self.begin_search(revision);
                    }
                    HandleResult::Copy(rvec) => {
                        let vec = rvec.to_vec();
                        let mime = tree_magic_mini::from_u8(&rvec);
                        if match mime {
                            "TEXT" | "STRING" | "UTF8_STRING" => true,
                            mime if mime.starts_with("text/") => true,
                            _ => false,
                        } {
                            root.clipboard().set_text(&String::from_utf8_lossy(&rvec));
                        } else {
                            let content = gdk::ContentProvider::for_bytes(
                                mime,
                                &glib::Bytes::from_owned(vec.clone()),
                            );
                            if let Err(why) = root.clipboard().set_content(Some(&content)) {
                                eprintln!("[anyrun] Error setting clipboard content: {why}");
                            }
                        }
                        sender.input(AppMsg::Action(Action::Close));
                    }
                    HandleResult::Stdout(rvec) => {
                        io::stdout().lock().write_all(&rvec).unwrap();
                        self.post_run_action = PostRunAction::Stdout(rvec.into());
                        sender.input(AppMsg::Action(Action::Close));
                    }
                }
            }
        }
        self.update_view(widgets, sender);
    }
}
