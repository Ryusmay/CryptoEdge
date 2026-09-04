mod lifecycle;

use lifecycle::{explicit_backend_entrypoint, BackendLifecycle};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, WindowEvent,
};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let log_dir = app.path().app_log_dir().unwrap_or_else(|_| std::env::temp_dir().join("CryptoEdge"));
            let lifecycle = BackendLifecycle::new(log_dir.join("tauri-lifecycle.log"));
            lifecycle.log("Tauri lifecycle started; backend autostart disabled until sidecar packaging is defined");
            if explicit_backend_entrypoint(std::env::var("CRYPTOEDGE_BACKEND_ENTRYPOINT").ok().as_deref()).is_some() {
                lifecycle.log("explicit backend entrypoint validated; autostart remains disabled until sidecar packaging is defined");
            }
            app.manage(lifecycle);

            let show = MenuItem::with_id(app, "show", "Pokaż CryptoEdge", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Zakończ", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("CryptoEdge")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => if let Some(window) = app.get_webview_window("main") {
                        let _ = window.show(); let _ = window.unminimize(); let _ = window.set_focus();
                    },
                    "quit" => {
                        app.state::<BackendLifecycle>().request_quit();
                        app.exit(0);
                    },
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let lifecycle = window.state::<BackendLifecycle>();
                if !lifecycle.is_quitting() {
                    api.prevent_close();
                    let _ = window.hide();
                    lifecycle.log("main window hidden to tray");
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running CryptoEdge terminal");
}
