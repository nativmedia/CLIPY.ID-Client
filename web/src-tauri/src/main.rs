#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    let builder = tauri::Builder::default();

    // CI opens the real webview and closes it once the local app has loaded.
    #[cfg(debug_assertions)]
    let builder = builder.on_page_load(|webview, payload| {
        if std::env::var_os("CLIPY_DESKTOP_SMOKE").is_some()
            && payload.event() == tauri::webview::PageLoadEvent::Finished
            && payload.url().host_str() == Some("127.0.0.1")
            && payload.url().port() == Some(4757)
            && webview.window().is_visible().unwrap_or(false)
        {
            println!("CLIPY_DESKTOP_WINDOW_READY");
            webview.window().close().expect("CLIPY.ID gagal ditutup");
        }
    });

    builder
        .run(tauri::generate_context!())
        .expect("CLIPY.ID gagal dibuka");
}
