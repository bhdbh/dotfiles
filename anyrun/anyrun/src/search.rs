/// Keep the displayed rows until every plugin has answered the current search.
pub struct Search<T> {
    revision: u64,
    text: String,
    requested: bool,
    replies: Option<Vec<Option<T>>>,
    rows_pending: usize,
    displayed: Option<u64>,
}

impl<T> Default for Search<T> {
    fn default() -> Self {
        Self { revision: 0, text: String::new(), requested: false,
            replies: None, rows_pending: 0, displayed: None }
    }
}

impl<T> Search<T> {
    pub fn change(&mut self, text: String) -> u64 {
        self.revision += 1;
        self.text = text;
        self.requested = false;
        self.replies = None;
        self.rows_pending = 0;
        self.displayed = None;
        self.revision
    }

    pub fn revision(&self) -> u64 { self.revision }
    pub fn text(&self) -> &str { &self.text }
    pub fn current(&self) -> bool { self.displayed == Some(self.revision) }

    pub fn begin(&mut self, revision: u64, plugins: usize) -> bool {
        if revision != self.revision || self.requested || plugins == 0 { return false; }
        self.requested = true;
        self.replies = Some((0..plugins).map(|_| None).collect());
        true
    }

    pub fn reply(&mut self, text: &str, plugin: usize, matches: T) -> Option<Vec<T>> {
        if text != self.text { return None; }
        let replies = self.replies.as_mut()?;
        let slot = replies.get_mut(plugin)?;
        if slot.is_some() { return None; }
        *slot = Some(matches);
        if replies.iter().any(Option::is_none) { return None; }
        let replies = self.replies.take()?;
        self.rows_pending = replies.len();
        Some(replies.into_iter().map(Option::unwrap).collect())
    }

    pub fn rows_ready(&mut self, revision: u64) -> bool {
        if revision != self.revision || self.rows_pending == 0 { return false; }
        self.rows_pending -= 1;
        if self.rows_pending != 0 { return false; }
        self.displayed = Some(revision);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::Search;

    #[test]
    fn waits_for_all_plugins_including_empty_results() {
        let mut search = Search::default();
        let revision = search.change("brave".into());
        assert!(search.begin(revision, 2));
        assert!(search.reply("brave", 1, vec!["Brave"]).is_none());
        assert!(search.reply("brave", 1, vec!["duplicate"]).is_none());
        assert_eq!(search.reply("brave", 0, vec![]), Some(vec![vec![], vec!["Brave"]]));
        assert!(!search.current());
        assert!(!search.rows_ready(revision));
        assert!(search.rows_ready(revision));
        assert!(search.current());
        assert!(!search.begin(revision, 2));
    }

    #[test]
    fn typing_invalidates_timers_replies_and_widget_notifications() {
        let mut search = Search::default();
        let previous = search.change("br".into());
        assert!(search.begin(previous, 2));
        search.reply("br", 0, 1);
        let current = search.change("brave".into());
        assert!(!search.begin(previous, 2));
        assert!(search.reply("br", 1, 2).is_none());
        assert!(!search.rows_ready(previous));
        assert!(search.begin(current, 2));
        assert!(search.reply("br", 0, 3).is_none());
        search.reply("brave", 0, 4);
        assert_eq!(search.reply("brave", 1, 5), Some(vec![4, 5]));
        search.change(String::new());
        assert!(!search.rows_ready(current));
        assert!(!search.current());
    }
}
