use std::{path::PathBuf, sync::Arc};

use abi_stable::std_types::{ROption, RVec};
use anyrun_interface::{Match, PluginInfo};
use anyrun_provider_ipc::HistoryEntry;
use gtk::{pango, prelude::*};
use gtk4 as gtk;
use relm4::prelude::*;

use crate::Config;

pub struct PluginMatch {
    pub content: Match,
    pub row: gtk::ListBoxRow,
    config: Arc<Config>,
    prefix: &'static str,
    description: Option<String>,
    calculation_operation: Option<String>,
    pub history: Option<(usize, String)>,
}

#[relm4::factory(pub)]
impl FactoryComponent for PluginMatch {
    type Init = (Match, Arc<Config>, PluginInfo, Option<(usize, String)>);
    type Input = ();
    type Output = ();
    type CommandOutput = ();
    type ParentWidget = gtk::ListBox;
    view! {
        gtk::ListBoxRow {
            set_css_classes: &["match"],
            set_height_request: 32,
            gtk::Box {
                set_orientation: gtk::Orientation::Horizontal,
                set_spacing: 10,
                set_css_classes: &["match"],
                set_hexpand: true,

                #[name = "icon"]
                gtk::Image {
                    set_pixel_size: 32,
                    set_visible: false,
                    set_css_classes: &["match"]
                },

                #[name = "text"]
                gtk::Box {
                    set_orientation: gtk::Orientation::Vertical,
                    set_css_classes: &["match", "text-fields"],
                    set_valign: gtk::Align::Center,
                    set_hexpand: true,
                    set_vexpand: true,

                    gtk::Box {
                        set_orientation: gtk::Orientation::Horizontal,
                        set_css_classes: &["title-row"],
                        set_spacing: 6,
                        set_hexpand: true,

                        gtk::Label {
                            set_css_classes: &["prefix"],
                            set_label: self.prefix,
                            set_visible: !self.prefix.is_empty(),
                            set_valign: gtk::Align::Baseline,
                        },

                        gtk::Label {
                            set_css_classes: &["calculation-operation"],
                            set_label: self.calculation_operation.as_deref().unwrap_or(""),
                            set_visible: self.calculation_operation.is_some(),
                            set_valign: gtk::Align::Baseline,
                            set_single_line_mode: true,
                            set_ellipsize: pango::EllipsizeMode::Middle,
                        },

                        gtk::Label {
                            set_css_classes: &["calculation-equals"],
                            set_label: "=",
                            set_visible: self.calculation_operation.is_some(),
                            set_valign: gtk::Align::Baseline,
                        },

                        gtk::Label {
                            set_css_classes: &["match", "title"],
                            set_halign: gtk::Align::Start,
                            set_valign: gtk::Align::Baseline,
                            set_xalign: 0.0,
                            set_single_line_mode: true,
                            set_ellipsize: pango::EllipsizeMode::End,
                            set_use_markup: self.content.use_pango,
                            set_label: &self.content.title,
                        },

                        #[name = "description"]
                        gtk::Label {
                            set_css_classes: &["match", "description"],
                            set_hexpand: true,
                            set_single_line_mode: true,
                            set_ellipsize: pango::EllipsizeMode::Middle,
                            set_xalign: 0.0,
                            set_use_markup: self.content.use_pango,
                            set_valign: gtk::Align::Baseline,
                        },
                    }
                }
            }
        }
    }

    fn init_widgets(
        &mut self,
        _index: &Self::Index,
        root: Self::Root,
        _returned_widget: &<Self::ParentWidget as relm4::factory::FactoryView>::ReturnedWidget,
        _sender: FactorySender<Self>,
    ) -> Self::Widgets {
        let widgets = view_output!();

        self.row = root;

        if !self.config.hide_icons {
            if let ROption::RSome(icon) = &self.content.icon {
                widgets.icon.set_visible(true);
                let path = PathBuf::from(icon.to_string());
                if path.is_absolute() {
                    widgets.icon.set_from_file(Some(path));
                } else {
                    widgets.icon.set_icon_name(Some(icon));
                }
            }
        }

        match &self.description {
            Some(desc) => widgets.description.set_label(desc),
            None => widgets.description.set_visible(false),
        }

        widgets
    }

    fn init_model(
        (content, config, plugin, history): Self::Init,
        _index: &Self::Index,
        _sender: FactorySender<Self>,
    ) -> Self {
        let row = gtk::ListBoxRow::default();
        let calculation_operation = if plugin.name == "Rink" {
            content.description.as_ref().map(|expression| expression.trim().to_owned()).into_option()
        } else { None };
        let prefix = match plugin.name.as_str() {
            "Applications" => ">",
            "Shell" => "$",
            "Actions" => ":",
            "Rink" => "",
            "Kidex" => match &content.icon {
                ROption::RSome(icon) if icon == "folder" => "#",
                ROption::RSome(icon) if icon == "text-x-generic" => "@",
                _ => "",
            },
            _ => "",
        };
        let description = match (&content.description, plugin.name.as_str()) {
            (_, "Actions" | "Applications" | "Rink") => None,
            (ROption::RSome(desc), _) if desc.trim().is_empty() => None,
            (ROption::RSome(desc), "Kidex") => {
                let home = std::env::var_os("HOME").map(PathBuf::from);
                Some(crate::paths::display_path(desc, home.as_deref()))
            }
            (ROption::RSome(desc), _) => Some(format!("({desc})")),
            _ => None,
        };

        Self {
            row,
            content,
            config,
            prefix,
            description,
            calculation_operation,
            history,
        }
    }
}

pub struct PluginBox {
    pub plugin_info: PluginInfo,
    pub matches: FactoryVecDeque<PluginMatch>,
    config: Arc<Config>,
    visible: bool,
    enabled: bool,
}

#[derive(Debug, Clone)]
pub enum PluginBoxInput {
    Matches { matches: RVec<Match>, enabled: bool, revision: u64 },
    History(Vec<HistoryEntry>),
    Clear,
    /// Sent when there is a possibility that the plugin may need to hide, aka
    /// all its matches have already been hidden
    MaybeHide,
}

#[derive(Debug)]
pub enum PluginBoxOutput {
    MatchesLoaded { history: bool, revision: Option<u64> },
    RowSelected(<PluginBox as FactoryComponent>::Index),
    RowActivated(<PluginBox as FactoryComponent>::Index, usize),
}

#[relm4::factory(pub)]
impl FactoryComponent for PluginBox {
    type Init = (PluginInfo, Arc<Config>);
    type Input = PluginBoxInput;
    type Output = PluginBoxOutput;
    type CommandOutput = (u64, RVec<Match>);
    type ParentWidget = gtk::Box;

    view! {
        gtk::Box {
            #[watch]
            set_visible: self.visible,
            set_css_classes: &["plugin"],

            gtk::Box {
                set_visible: !self.config.hide_plugin_info,
                set_css_classes: &["plugin", "info"],
                set_orientation: gtk::Orientation::Vertical,

                gtk::Box {
                    set_orientation: gtk::Orientation::Horizontal,
                    set_expand: false,

                    gtk::Image {
                        set_css_classes: &["plugin", "info"],
                        set_icon_name: Some(&self.plugin_info.icon),
                        set_visible: !self.config.hide_icons,
                        set_halign: gtk::Align::Start,
                        set_valign: gtk::Align::Start,
                        set_pixel_size: 32,
                    },
                    gtk::Label {
                        set_css_classes: &["plugin", "info"],
                        set_label: &self.plugin_info.name,
                        set_halign: gtk::Align::Start,
                        set_valign: gtk::Align::Center,
                    }
                }
            },

            #[local_ref]
            matches -> gtk::ListBox {
                set_css_classes: &["plugin"],
                set_hexpand: true,
                connect_row_selected[index, sender = sender.clone()] => move |_list, row| {
                    if row.is_some() {
                        sender.output(PluginBoxOutput::RowSelected(index.clone())).unwrap();
                    }
                },
                connect_row_activated[index] => move |_list, row| {
                    sender.clone().output(PluginBoxOutput::RowActivated(
                        index.clone(),
                        row.index() as usize,
                    )).unwrap();
                }
            }
        }
    }

    fn init_widgets(
        &mut self,
        index: &Self::Index,
        root: Self::Root,
        _returned_widget: &<Self::ParentWidget as relm4::factory::FactoryView>::ReturnedWidget,
        sender: FactorySender<Self>,
    ) -> Self::Widgets {
        let matches = self.matches.widget();

        let widgets = view_output!();

        widgets
    }

    fn init_model(
        (plugin_info, config): Self::Init,
        _index: &Self::Index,
        _sender: FactorySender<Self>,
    ) -> Self {
        let matches = FactoryVecDeque::builder()
            .launch(gtk::ListBox::default())
            .detach();

        Self {
            plugin_info,
            matches,
            config,
            visible: false,
            enabled: true,
        }
    }

    fn update_with_view(
        &mut self,
        widgets: &mut Self::Widgets,
        message: Self::Input,
        sender: FactorySender<Self>,
    ) {
        match message {
            PluginBoxInput::Clear => {
                self.matches.guard().clear();
                self.visible = false;
                self.enabled = true;
            }
            PluginBoxInput::History(entries) => {
                self.enabled = true;
                self.visible = false;
                {
                    let mut guard = self.matches.guard();
                    guard.clear();
                    for (index, mut entry) in entries.into_iter().enumerate() {
                        if entry.plugin.name == "Rink" {
                            entry.selection.description = crate::calculator::expression(&entry.query)
                                .map(|expression| expression.to_owned().into()).into();
                        }
                        if entry.plugin.name == "Kidex" && entry.selection.description.as_ref()
                            .map(|path| !std::path::Path::new(path.as_str()).exists()).unwrap_or(true)
                        {
                            continue;
                        }
                        guard.push_back((entry.selection, self.config.clone(), entry.plugin, Some((index, entry.query))));
                        self.visible = true;
                    }
                }
                sender.output(PluginBoxOutput::MatchesLoaded { history: true, revision: None }).unwrap();
            }
            PluginBoxInput::Matches { matches, enabled, revision } => {
                let unchanged = self.enabled == enabled
                    && self.matches.len() == matches.len()
                    && self.matches.iter().zip(matches.iter()).all(|(row, item)| row.history.is_none() && row.content == *item);
                self.enabled = enabled;
                self.visible = enabled && !matches.is_empty();
                if !unchanged {
                    let mut guard = self.matches.guard();
                    guard.clear();
                    if enabled {
                        for _match in matches {
                            guard.push_back((_match, self.config.clone(), self.plugin_info.clone(), None));
                        }
                    }
                }
                sender.output(PluginBoxOutput::MatchesLoaded { history: false, revision: Some(revision) }).unwrap();
            }
            PluginBoxInput::MaybeHide => {
                let mut hide = true;

                for plugin_match in self.matches.iter() {
                    if plugin_match.row.get_visible() {
                        hide = false;
                        break;
                    }
                }

                self.visible = !hide;
            }
        }

        self.update_view(widgets, sender);
    }
}
