//! Cache live rates across the short-lived providers without disabling them.
use std::{env, fs, io::{self, Read, Write}, os::unix::fs::OpenOptionsExt, path::{Path, PathBuf}, time::{Duration, SystemTime, UNIX_EPOCH}};
use serde::{Deserialize, Serialize};

const MAX_PAYLOAD: u64 = 1024 * 1024;
const FRESH_FOR: u64 = 60 * 60;
const RETRY_AFTER: u64 = 15 * 60;

#[derive(Default, Deserialize, Serialize)]
struct Cache {
    fetched_at: u64,
    attempted_at: Option<u64>,
    data: Option<String>,
}

pub fn load(mut valid: impl FnMut(&str) -> bool) -> Option<String> {
    let directory = env::var_os("XDG_CACHE_HOME").map(PathBuf::from)
        .or_else(|| env::var_os("HOME").map(|home| PathBuf::from(home).join(".cache")));
    let path = directory.map(|directory| directory.join("anyrun/rink-currency.ron"));
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    load_at(path.as_deref(), now, fetch, &mut valid)
}

fn load_at(path: Option<&Path>, now: u64, fetch: impl FnOnce() -> io::Result<String>, valid: &mut impl FnMut(&str) -> bool) -> Option<String> {
    let mut cache = path.and_then(|path| {
        let file = fs::File::open(path).ok()?;
        if file.metadata().ok()?.len() > MAX_PAYLOAD * 2 { return None; }
        ron::de::from_reader(file.take(MAX_PAYLOAD * 2)).ok()
    }).unwrap_or_else(Cache::default);
    cache.data = cache.data.filter(|data| data.len() as u64 <= MAX_PAYLOAD && valid(data));
    if cache.data.is_some() && now.saturating_sub(cache.fetched_at) < FRESH_FOR {
        return cache.data;
    }
    if cache.attempted_at.is_some_and(|attempt| now.saturating_sub(attempt) < RETRY_AFTER) {
        return cache.data;
    }
    cache.attempted_at = Some(now);
    // Record failed attempts too, so opening the launcher offline does not
    // repeat a network timeout on every invocation.
    if let Some(path) = path { save(path, &cache); }
    match fetch() {
        Ok(data) if data.len() as u64 <= MAX_PAYLOAD && valid(&data) => {
            cache.data = Some(data);
            cache.fetched_at = now;
            if let Some(path) = path { save(path, &cache); }
        }
        Ok(_) => eprintln!("[rink] Invalid live currency data; using cached or bundled definitions"),
        Err(why) => eprintln!("[rink] Currency refresh failed; using cached or bundled definitions: {why}"),
    }
    cache.data
}

fn fetch() -> io::Result<String> {
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(3)).connect_timeout(Duration::from_secs(2))
        .user_agent(concat!("dotfiles-anyrun-rink/", env!("CARGO_PKG_VERSION")))
        .build().map_err(io::Error::other)?;
    let response = client.get("https://rinkcalc.app/data/currency.json").send()
        .and_then(reqwest::blocking::Response::error_for_status).map_err(io::Error::other)?;
    let mut bytes = Vec::new();
    response.take(MAX_PAYLOAD + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_PAYLOAD {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "currency response too large"));
    }
    String::from_utf8(bytes).map_err(|why| io::Error::new(io::ErrorKind::InvalidData, why))
}

fn save(path: &Path, cache: &Cache) {
    let result = (|| -> io::Result<()> {
        fs::create_dir_all(path.parent().unwrap())?;
        let temporary = path.with_extension(format!("{}.tmp", std::process::id()));
        let mut file = fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&temporary)?;
        file.write_all(ron::ser::to_string(cache).map_err(io::Error::other)?.as_bytes())?;
        fs::rename(temporary, path)
    })();
    if let Err(why) = result { eprintln!("[rink] Could not save currency cache: {why}"); }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
            let path = env::temp_dir().join(format!("dotfiles-rink-{}-{}", std::process::id(), NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)));
            fs::create_dir_all(&path).unwrap(); Self(path)
        }
    }
    impl Drop for Fixture { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }

    #[test]
    fn fresh_cache_avoids_network_and_stale_cache_survives_offline_refresh() {
        let fixture = Fixture::new(); let path = fixture.0.join("rates.ron");
        let mut valid = |data: &str| data.starts_with("valid");
        assert_eq!(load_at(Some(&path), 10_000, || Ok("valid rates".into()), &mut valid).as_deref(), Some("valid rates"));
        assert_eq!(load_at(Some(&path), 10_001, || panic!("fresh cache must avoid network"), &mut valid).as_deref(), Some("valid rates"));
        assert_eq!(load_at(Some(&path), 10_000 + FRESH_FOR, || Err(io::Error::other("offline")), &mut valid).as_deref(), Some("valid rates"));
        assert_eq!(load_at(Some(&path), 10_001 + FRESH_FOR, || panic!("failed refresh must be rate limited"), &mut valid).as_deref(), Some("valid rates"));
    }

    #[test]
    fn invalid_response_is_not_cached_as_success_and_can_be_retried() {
        let fixture = Fixture::new(); let path = fixture.0.join("rates.ron");
        let mut valid = |data: &str| data == "valid";
        assert!(load_at(Some(&path), 10_000, || Ok("invalid".into()), &mut valid).is_none());
        assert!(load_at(Some(&path), 10_001, || panic!("retry is bounded"), &mut valid).is_none());
        assert_eq!(load_at(Some(&path), 10_000 + RETRY_AFTER, || Ok("valid".into()), &mut valid).as_deref(), Some("valid"));
    }
    #[test]
    fn cached_live_rates_still_support_real_currency_conversion_offline() {
        let fixture = Fixture::new(); let path = fixture.0.join("rates.ron");
        let data = r#"[{"name":"USD","doc":null,"category":"currencies","type":"unit","expr":"(1 / 2) EUR"}]"#;
        let base = rink_core::CURRENCY_FILE.unwrap();
        let mut validate = |data: &str| rink_core::simple_context().unwrap().load_currency(Some(data), base).is_ok();
        load_at(Some(&path), 10_000, || Ok(data.into()), &mut validate).unwrap();
        let cached = load_at(Some(&path), 10_000 + FRESH_FOR, || Err(io::Error::other("offline")), &mut validate).unwrap();
        let mut context = rink_core::simple_context().unwrap();
        context.load_currency(Some(&cached), base).unwrap();
        let (result, _) = crate::everyday::calculate(&mut context, "2 USD to EUR").unwrap();
        assert_eq!(result, "1 euro");
    }

}
