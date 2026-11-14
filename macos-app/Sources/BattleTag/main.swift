import AppKit

/// BattlePew entry point.
///
/// The app is a thin native shell: it starts the bundled Python/FastAPI
/// server as a child process and hosts the Arena Master web UI in a
/// borderless WKWebView. There is no storyboard, no XIB, and no
/// NSPrincipalClass — the delegate builds the window in code so the whole
/// shell compiles with a single `swiftc` invocation (see build_macos_app.sh).
@main
struct BattleTagMain {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
