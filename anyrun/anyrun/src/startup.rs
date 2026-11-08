#[derive(Debug, PartialEq, Eq)]
enum Phase {
    Provider,
    History,
    HistoryRows,
    Ready,
    Closed,
}

pub struct Startup {
    phase: Phase,
    needs_history: bool,
}

impl Startup {
    pub fn new(needs_history: bool) -> Self { Self { phase: Phase::Provider, needs_history } }

    /// Returns whether the initial history must be requested now.
    pub fn provider_ready(&mut self) -> bool {
        if self.phase != Phase::Provider { return false; }
        self.phase = if self.needs_history { Phase::History } else { Phase::Ready };
        self.needs_history
    }

    pub fn history_received(&mut self) {
        if self.phase == Phase::History { self.phase = Phase::HistoryRows; }
    }

    pub fn history_rows_ready(&mut self) {
        if self.phase == Phase::HistoryRows { self.phase = Phase::Ready; }
    }

    pub fn provider_stopped(&mut self) {
        if matches!(self.phase, Phase::Provider | Phase::History) { self.phase = Phase::Ready; }
    }

    pub fn ready(&self) -> bool { self.phase == Phase::Ready }
    pub fn close(&mut self) { self.phase = Phase::Closed; }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn waits_for_history_rows_before_presenting() {
        let mut startup = Startup::new(true);
        assert!(!startup.ready());
        assert!(startup.provider_ready());
        assert!(!startup.ready());
        startup.history_rows_ready();
        assert!(!startup.ready());
        startup.history_received();
        assert!(!startup.ready());
        startup.history_rows_ready();
        assert!(startup.ready());
        assert!(!startup.provider_ready());
    }

    #[test]
    fn an_empty_history_finishes_via_the_same_rows_ready_event() {
        let mut startup = Startup::new(true);
        startup.provider_ready();
        startup.history_received();
        startup.provider_stopped();
        assert!(!startup.ready());
        startup.history_rows_ready();
        assert!(startup.ready());
    }

    #[test]
    fn supports_disabled_history_and_provider_failures() {
        let mut startup = Startup::new(false);
        assert!(!startup.provider_ready());
        assert!(startup.ready());
        let mut failed = Startup::new(true);
        failed.provider_stopped();
        assert!(failed.ready());
    }

    #[test]
    fn cannot_reopen_after_closing_during_startup() {
        let mut startup = Startup::new(true);
        startup.close();
        startup.provider_ready();
        startup.history_received();
        startup.history_rows_ready();
        startup.provider_stopped();
        assert!(!startup.ready());
    }
}
