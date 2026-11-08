use gtk::{pango, prelude::*};
use gtk4 as gtk;

pub const PREFIXES: &[(&str, &str)] = &[
    (">", "Search applications"),
    ("#", "Search directories"),
    ("@", "Search files"),
    (":", "Show system actions"),
    ("$", "Run shell commands"),
    ("=", "Calculator"),
    ("?", "Show available prefixes"),
];

pub fn is_help(text: &str) -> bool { text.trim() == "?" }

pub fn build() -> gtk::ListBox {
    let list = gtk::ListBox::new();
    list.set_css_classes(&["matches", "prefix-help"]);
    list.set_selection_mode(gtk::SelectionMode::None);
    list.set_can_target(false);
    list.set_focusable(false);
    for (prefix, description) in PREFIXES {
        let row = gtk::ListBoxRow::new();
        row.set_css_classes(&["match"]);
        row.set_height_request(32);
        row.set_selectable(false);
        row.set_activatable(false);
        row.set_can_target(false);
        row.set_focusable(false);
        let content = gtk::Box::new(gtk::Orientation::Horizontal, 6);
        content.set_can_target(false);
        let symbol = gtk::Label::new(Some(prefix));
        symbol.set_css_classes(&["prefix"]);
        symbol.set_can_target(false);
        symbol.set_valign(gtk::Align::Baseline);
        let label = gtk::Label::new(Some(description));
        label.set_css_classes(&["description"]);
        label.set_xalign(0.0);
        label.set_hexpand(true);
        label.set_single_line_mode(true);
        label.set_ellipsize(pango::EllipsizeMode::End);
        label.set_valign(gtk::Align::Baseline);
        label.set_can_target(false);
        content.append(&symbol);
        content.append(&label);
        row.set_child(Some(&content));
        list.append(&row);
    }
    list.set_visible(false);
    list
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_only_the_standalone_question_mark() {
        for text in ["?", " ? ", "\t?\n"] { assert!(is_help(text)); }
        for text in ["", "??", "? query", "query?", "= ?"] { assert!(!is_help(text)); }
    }

    #[test]
    #[ignore = "requires a display; inspects widgets without mapping a window"]
    fn help_is_read_only_without_mapping_windows() {
        gtk::init().unwrap();
        let list = build();
        assert!(!list.is_visible() && !list.is_mapped());
        assert_eq!(list.selection_mode(), gtk::SelectionMode::None);
        assert!(!list.can_target() && !list.is_focusable());
        for index in 0..PREFIXES.len() {
            let row = list.row_at_index(index as i32).unwrap();
            assert!(!row.is_selectable() && !row.is_activatable() && !row.can_target());
        }
        assert!(list.row_at_index(PREFIXES.len() as i32).is_none());
        assert!(list.selected_row().is_none());
    }
}
