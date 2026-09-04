use std::{
    fs::{create_dir_all, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    process::Child,
    sync::{atomic::{AtomicBool, Ordering}, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

/// Owns only a backend explicitly spawned by this application. An already
/// running backend is never adopted and therefore never killed on shutdown.
pub struct BackendLifecycle {
    child: Mutex<Option<Child>>,
    quitting: AtomicBool,
    log_path: PathBuf,
}

impl BackendLifecycle {
    pub fn new(log_path: PathBuf) -> Self {
        Self { child: Mutex::new(None), quitting: AtomicBool::new(false), log_path }
    }

    pub fn is_quitting(&self) -> bool { self.quitting.load(Ordering::SeqCst) }

    pub fn request_quit(&self) {
        self.quitting.store(true, Ordering::SeqCst);
        self.stop_owned_backend();
        self.log("application shutdown requested");
    }

    pub fn stop_owned_backend(&self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
                self.log("owned backend stopped");
            }
        }
    }

    pub fn log(&self, message: &str) {
        if let Some(parent) = self.log_path.parent() { let _ = create_dir_all(parent); }
        if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&self.log_path) {
            let timestamp = SystemTime::now().duration_since(UNIX_EPOCH).map(|v| v.as_secs()).unwrap_or_default();
            let _ = writeln!(file, "{timestamp} {message}");
        }
    }
}

impl Drop for BackendLifecycle {
    fn drop(&mut self) { self.stop_owned_backend(); }
}

/// Packaged startup is intentionally disabled until Python is bundled as a
/// Tauri sidecar/resource. This resolver accepts only an explicit regular file
/// and never searches PATH or guesses from the current working directory.
pub fn explicit_backend_entrypoint(value: Option<&str>) -> Option<PathBuf> {
    let path = Path::new(value?.trim());
    if path.is_absolute() && path.is_file() { Some(path.to_path_buf()) } else { None }
}

#[cfg(test)]
mod tests {
    use super::explicit_backend_entrypoint;

    #[test]
    fn rejects_missing_relative_and_empty_entrypoints() {
        assert!(explicit_backend_entrypoint(None).is_none());
        assert!(explicit_backend_entrypoint(Some("")).is_none());
        assert!(explicit_backend_entrypoint(Some("app.py")).is_none());
    }
}
