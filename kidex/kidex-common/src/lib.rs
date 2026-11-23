use std::path::PathBuf;

use serde::{Deserialize, Serialize};

pub mod query;

pub const DEFAULT_SOCKET: &str = "/tmp/kidex.sock";

#[derive(Deserialize, Serialize)]
pub enum IpcCommand {
    FullIndex,
    Quit,
    Reload,
    GetIndex(Option<PathBuf>),
}

#[derive(Deserialize, Serialize)]
pub enum IpcResponse {
    Success,
    NotFound,
    Index(Vec<IndexEntry>),
}

#[derive(Deserialize, Serialize, Clone)]
pub struct IndexEntry {
    pub path: PathBuf,
    pub directory: bool,
}

#[cfg(feature = "util")]
pub mod util {
    use std::{
        env,
        fmt::Display,
        io::{self, Read, Write},
        os::unix::net::UnixStream,
        path::PathBuf,
        time::{Duration, Instant},
    };

    use super::{IndexEntry, IpcCommand, IpcResponse, DEFAULT_SOCKET};

    #[derive(Debug)]
    pub enum Error {
        Io(io::Error),
        Serde(serde_json::Error),
        NotFound,
        Unknown,
    }

    impl From<io::Error> for Error {
        fn from(value: io::Error) -> Self {
            Self::Io(value)
        }
    }

    impl From<serde_json::Error> for Error {
        fn from(value: serde_json::Error) -> Self {
            Self::Serde(value)
        }
    }

    impl Display for Error {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            match self {
                Error::Io(why) => write!(f, "An IO error occurred: {}", why),
                Error::Serde(why) => write!(f, "A se/deserialization error occurred: {}", why),
                Error::NotFound => write!(f, "Requested path not found"),
                Error::Unknown => write!(f, "An unknown error occurred"),
            }
        }
    }

    impl std::error::Error for Error {}

    const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);
    const MAX_RESPONSE_BYTES: usize = 256 * 1024 * 1024;

    fn fetch(command: &IpcCommand) -> Result<IpcResponse, Error> {
        let stream = UnixStream::connect(env::var("SOCKET_PATH").unwrap_or(DEFAULT_SOCKET.to_string()))?;
        exchange(stream, command, REQUEST_TIMEOUT, MAX_RESPONSE_BYTES)
    }

    fn exchange(mut stream: UnixStream, command: &IpcCommand, timeout: Duration, limit: usize) -> Result<IpcResponse, Error> {
        stream.set_write_timeout(Some(timeout))?;
        let mut request = serde_json::to_vec(command)?;
        request.push(0);
        stream.write_all(&request)?;
        let deadline = Instant::now() + timeout;
        let mut response = Vec::new();
        let mut chunk = [0; 64 * 1024];
        loop {
            let remaining = deadline.checked_duration_since(Instant::now())
                .filter(|duration| !duration.is_zero())
                .ok_or_else(|| io::Error::new(io::ErrorKind::TimedOut, "Kidex response timed out"))?;
            stream.set_read_timeout(Some(remaining))?;
            let count = stream.read(&mut chunk)?;
            if count == 0 { break; }
            if response.len().saturating_add(count) > limit {
                return Err(io::Error::new(io::ErrorKind::InvalidData, "Kidex response exceeds size limit" ).into());
            }
            response.extend_from_slice(&chunk[..count]);
        }
        Ok(serde_json::from_slice(&response)?)
    }

    pub fn get_index(path: Option<PathBuf>) -> Result<Vec<IndexEntry>, Error> {
        match fetch(&IpcCommand::GetIndex(path))? {
            IpcResponse::Index(index) => Ok(index),
            IpcResponse::NotFound => Err(Error::NotFound),
            _ => Err(Error::Unknown),
        }
    }

    pub fn regenerate_index() -> Result<(), Error> {
        match fetch(&IpcCommand::FullIndex)? {
            IpcResponse::Success => Ok(()),
            _ => Err(Error::Unknown),
        }
    }

    pub fn shutdown_server() -> Result<(), Error> {
        match fetch(&IpcCommand::Quit)? {
            IpcResponse::Success => Ok(()),
            _ => Err(Error::Unknown),
        }
    }

    pub fn reload_config() -> Result<(), Error> {
        match fetch(&IpcCommand::Reload)? {
            IpcResponse::Success => Ok(()),
            _ => Err(Error::Unknown),
        }
    }
    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn accepts_fragmented_response_and_rejects_oversized_response() {
            for oversized in [false, true] {
                let (client, mut server) = UnixStream::pair().unwrap();
                let worker = std::thread::spawn(move || {
                    let mut request = [0; 128];
                    let _ = server.read(&mut request).unwrap();
                    server.write_all(b"\"Suc").unwrap();
                    server.write_all(b"cess\"").unwrap();
                });
                let result = exchange(client, &IpcCommand::Reload, Duration::from_secs(1), if oversized { 4 } else { 128 });
                if oversized { assert!(matches!(result, Err(Error::Io(ref why)) if why.kind() == io::ErrorKind::InvalidData)); }
                else { assert!(matches!(result, Ok(IpcResponse::Success))); }
                worker.join().unwrap();
            }
        }

        #[test]
        fn a_stalled_server_has_a_deadline() {
            let (client, _server) = UnixStream::pair().unwrap();
            let start = Instant::now();
            let result = exchange(client, &IpcCommand::Reload, Duration::from_millis(30), 128);
            assert!(matches!(result, Err(Error::Io(ref why)) if matches!(why.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut)));
            assert!(start.elapsed() < Duration::from_secs(1));
        }
    }

}
