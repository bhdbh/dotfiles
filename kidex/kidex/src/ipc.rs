//! A malformed or stalled client must not stop the indexer's listener.
use std::{io, sync::Arc, time::Duration};

use kidex_common::{IpcCommand, IpcResponse, IndexEntry};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufStream},
    net::{UnixListener, UnixStream},
    sync::{mpsc::{Receiver, Sender}, Mutex},
    task::JoinSet,
    time::timeout,
};

use crate::{index::{GetPath, Index}, EventLoopMsg};

const MAX_REQUEST_BYTES: u64 = 64 * 1024;
const CLIENT_TIMEOUT: Duration = Duration::from_secs(10);
const MAX_CLIENTS: usize = 32;

pub(crate) async fn serve(
    listener: UnixListener,
    index: Arc<Mutex<Index>>,
    control: Sender<EventLoopMsg>,
    mut shutdown: Receiver<()>,
) -> io::Result<()> {
    let mut clients = JoinSet::new();
    loop {
        tokio::select! {
            _ = shutdown.recv() => break,
            connection = listener.accept(), if clients.len() < MAX_CLIENTS => {
                let (stream, _) = connection?;
                let index = index.clone();
                let control = control.clone();
                clients.spawn(async move {
                    match timeout(CLIENT_TIMEOUT, handle(stream, index, control)).await {
                        Ok(Ok(())) => (),
                        Ok(Err(why)) => log::warn!("Discarding IPC client: {why}"),
                        Err(_) => log::warn!("IPC client timed out"),
                    }
                });
            }
            result = clients.join_next(), if !clients.is_empty() => {
                if let Some(Err(why)) = result {
                    log::error!("IPC client task failed: {why}");
                }
            }
        }
    }
    clients.shutdown().await;
    Ok(())
}

async fn handle(stream: UnixStream, index: Arc<Mutex<Index>>, control: Sender<EventLoopMsg>) -> io::Result<()> {
    let mut stream = BufStream::new(stream);
    let mut bytes = Vec::new();
    (&mut stream).take(MAX_REQUEST_BYTES + 1).read_until(0, &mut bytes).await?;
    if bytes.len() as u64 > MAX_REQUEST_BYTES || bytes.pop() != Some(0) {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "oversized or incomplete IPC request"));
    }
    let command: IpcCommand = serde_json::from_slice(&bytes)
        .map_err(|why| io::Error::new(io::ErrorKind::InvalidData, why))?;
    let mut quit = false;
    let response = match command {
        IpcCommand::GetIndex(path) => {
            let index = index.lock().await;
            let paths = match path {
                Some(path) => index.inner.iter()
                    .find(|(desc, _)| index.inner.get_path(desc) == path)
                    .map(|(desc, _)| index.traverse(desc.clone()).into_iter()
                        .flat_map(|(desc, directory)| {
                            let parent = index.inner.get_path(&desc);
                            directory.children.into_iter().map(move |(path, child)| IndexEntry {
                                path: parent.join(path),
                                directory: matches!(child, crate::ChildIndex::Directory { .. }),
                            })
                        }).collect()),
                None => Some(index.entries()),
            };
            match paths { Some(paths) => IpcResponse::Index(paths), None => IpcResponse::NotFound }
        }
        IpcCommand::FullIndex => {
            control.send(EventLoopMsg::FullIndex).await.map_err(control_closed)?;
            IpcResponse::Success
        }
        IpcCommand::Reload => {
            control.send(EventLoopMsg::Reload).await.map_err(control_closed)?;
            IpcResponse::Success
        }
        IpcCommand::Quit => {
            // Acknowledge before allowing main to shut down our client task.
            quit = true;
            IpcResponse::Success
        }
    };
    let bytes = serde_json::to_vec(&response)
        .map_err(|why| io::Error::new(io::ErrorKind::InvalidData, why))?;
    stream.write_all(&bytes).await?;
    stream.flush().await?;
    if quit { control.send(EventLoopMsg::Quit).await.map_err(control_closed)?; }
    Ok(())
}

fn control_closed(why: tokio::sync::mpsc::error::SendError<EventLoopMsg>) -> io::Error {
    io::Error::new(io::ErrorKind::BrokenPipe, why)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncReadExt;

    #[tokio::test]
    async fn stalled_and_malformed_clients_do_not_block_later_queries() {
        let fixture = crate::mounts::tests::Fixture::new();
        let socket = fixture.0.join("isolated-ipc.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let (control, _events) = tokio::sync::mpsc::channel(32);
        let (shutdown, done) = tokio::sync::mpsc::channel(1);
        let server = tokio::spawn(serve(listener, Arc::new(Mutex::new(Index::new())), control, done));
        let _stalled = UnixStream::connect(&socket).await.unwrap();
        let mut malformed = UnixStream::connect(&socket).await.unwrap();
        malformed.write_all(b"invalid JSON\0").await.unwrap();
        malformed.shutdown().await.unwrap();
        let mut oversized = UnixStream::connect(&socket).await.unwrap();
        oversized.write_all(&vec![b'x'; MAX_REQUEST_BYTES as usize + 1]).await.unwrap();
        drop(oversized);
        for _ in 0..2 {
            let mut client = UnixStream::connect(&socket).await.unwrap();
            client.write_all(b"{\"GetIndex\":null}\0").await.unwrap();
            let mut response = Vec::new();
            timeout(Duration::from_secs(1), client.read_to_end(&mut response)).await.unwrap().unwrap();
            assert!(matches!(serde_json::from_slice(&response).unwrap(), IpcResponse::Index(entries) if entries.is_empty()));
        }
        shutdown.send(()).await.unwrap();
        server.await.unwrap().unwrap();
    }
}
