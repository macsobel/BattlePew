import AppKit
import UniformTypeIdentifiers
import WebKit

/// App delegate: builds the native window, menu bar, and view states, and
/// drives the launch sequence (preflight → hardware authorization → server
/// start → web UI). The web UI itself is unchanged: this shell only hosts
/// it in a chromeless WKWebView.
///
/// Window/lifecycle contract (see Docs/DESIGN_DECISIONS.md):
/// - The window enters fullscreen shortly after launch. Fullscreen is a
///   launch behavior, not a lock: the user can exit with Esc, the green
///   traffic light, or View > Toggle Full Screen, and the app keeps
///   running windowed.
/// - No window restoration: every launch is fresh (the server restarts
///   from scratch, so restoring a stale window would be a lie).
/// - Closing the window quits the app, and quitting always stops the
///   server first. There is no close-to-background and no orphaned server.
/// - Sudden termination is deliberately NOT enabled: macOS must always
///   run the normal quit path so the server shuts down cleanly.
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKScriptMessageHandler {

    private static let fullscreenDefaultsKey = "BattlePewWasFullscreen"
    private static let windowAutosaveName = "BattlePewMainWindow"

    private var window: NSWindow!
    private var containerView: NSView!
    private var loadingView: NSView!
    private var loadingLabel: NSTextField!
    private var webView: WKWebView!
    private var errorView: NSView!
    private var errorTitleLabel: NSTextField!
    private var errorDetailField: NSTextField!
    private var errorRelaunchButton: NSButton!

    private var serverManager: ServerManager!
    private var allowHardware = false
    private var relaunchCount = 0
    private var didEnterFullscreen = false
    private var isQuitting = false

    // MARK: - Launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        showLoading(message: "Starting BattlePew…")
        let shouldStartFullscreen = (UserDefaults.standard.object(forKey: Self.fullscreenDefaultsKey) as? Bool) ?? true
        if shouldStartFullscreen {
            enterFullscreenSoon()
        }

        guard let resources = Bundle.main.resourceURL else {
            showError(title: "BattlePew could not start.",
                      detail: "The app bundle is missing its Resources directory.",
                      allowRelaunch: false)
            return
        }
        let logDirectory = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first!
            .appendingPathComponent("Logs/BattlePew")
        serverManager = ServerManager(
            pythonURL: resources.appendingPathComponent("python/bin/python3"),
            serverDir: resources.appendingPathComponent("server"),
            logDirectory: logDirectory)
        serverManager.onUnexpectedExit = { [weak self] detail in
            guard let self = self, !self.isQuitting else { return }
            self.webView.stopLoading()
            self.showError(title: "The game server stopped.",
                           detail: detail + "\n\nYour match state was lost. Relaunching starts a fresh server — guns stay paired only if you re-pair them.",
                           allowRelaunch: true)
        }

        runStartupSequence()
    }

    /// Preflight → server start → web UI. No dialog is ever shown: a plain
    /// launch always starts in hardware mode (the server gets
    /// --confirm-write; if no UbiConnect is attached, the web UI shows its
    /// "Connect Your UbiConnect" modal and hot-plug detection activates when
    /// one is plugged in). Hold the Option key while launching to run in
    /// simulator mode instead, without touching any hardware.
    private func runStartupSequence() {
        // Sampled here, synchronously at launch: the already-running check
        // below is asynchronous, and the key may be released by the time
        // its callback runs.
        let simulatorRequested = NSEvent.modifierFlags.contains(.option)
        serverManager.checkAlreadyRunning { [weak self] alreadyRunning in
            guard let self = self else { return }
            if alreadyRunning {
                self.showAlreadyRunningAlert()
                return
            }
            self.allowHardware = !simulatorRequested
            self.startServerAndShowUI()
        }
    }

    private func startServerAndShowUI() {
        showLoading(message: allowHardware
            ? "Starting the game server (hardware control allowed)…"
            : "Starting the game server (simulator mode)…")
        serverManager.start(allowHardware: allowHardware) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let port):
                let url = URL(string: "http://127.0.0.1:\(port)/")!
                self.showWebView(url: url)
            case .failure(let error):
                self.showError(title: "BattlePew could not start the game server.",
                               detail: error.localizedDescription,
                               allowRelaunch: true)
            }
        }
    }

    private func showAlreadyRunningAlert() {
        let alert = NSAlert()
        alert.messageText = "BattlePew is already running."
        alert.informativeText = "Another copy of BattlePew is already serving the game on this Mac. Switch to it instead of starting a second server."
        alert.addButton(withTitle: "OK")
        alert.alertStyle = .informational
        alert.runModal()
        NSApp.terminate(nil)
    }

    // MARK: - Termination

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        // Closing the window quits the app. There is no background mode:
        // a hidden server with no window would strand the HID device and
        // confuse the next launch's port check.
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Synchronous stop: this is the last chance to shut the server
        // down cleanly (SIGTERM → uvicorn graceful shutdown → FastAPI
        // shutdown hook closes the HID device). Blocking here briefly is
        // correct; an orphaned server is worse than a slow quit.
        isQuitting = true
        serverManager?.stopSynchronously()
    }

    // MARK: - Window + views

    private func buildWindow() {
        let frame = NSRect(x: 0, y: 0, width: 1280, height: 800)
        window = NSWindow(contentRect: frame,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered,
                          defer: false)
        window.title = "BattlePew"
        window.minSize = NSSize(width: 900, height: 600)
        window.delegate = self
        if !window.setFrameUsingName(Self.windowAutosaveName) {
            window.center()
        }
        window.setFrameAutosaveName(Self.windowAutosaveName)
        window.collectionBehavior = [.fullScreenPrimary]
        window.isRestorable = false
        window.isReleasedWhenClosed = false

        containerView = NSView(frame: window.contentView!.bounds)
        containerView.autoresizingMask = [.width, .height]
        window.contentView = containerView

        buildLoadingView()
        buildWebView()
        buildErrorView()
    }

    private func enterFullscreenSoon() {
        // toggleFullScreen before the window is visible misbehaves, so wait
        // a beat after ordering the window front. Once only, per launch.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            guard let self = self, !self.didEnterFullscreen else { return }
            self.didEnterFullscreen = true
            if !self.window.styleMask.contains(.fullScreen) {
                self.window.toggleFullScreen(nil)
            }
        }
    }

    private func fillPane(_ view: NSView) {
        view.frame = containerView.bounds
        view.autoresizingMask = [.width, .height]
        containerView.addSubview(view)
    }

    private func buildLoadingView() {
        let view = NSView()
        view.wantsLayer = true
        view.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor

        let spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .large
        spinner.isIndeterminate = true
        spinner.startAnimation(nil)
        spinner.translatesAutoresizingMaskIntoConstraints = false

        loadingLabel = NSTextField(labelWithString: "Starting BattlePew…")
        loadingLabel.font = .systemFont(ofSize: 15)
        loadingLabel.alignment = .center
        loadingLabel.translatesAutoresizingMaskIntoConstraints = false

        view.addSubview(spinner)
        view.addSubview(loadingLabel)
        NSLayoutConstraint.activate([
            spinner.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            spinner.centerYAnchor.constraint(equalTo: view.centerYAnchor, constant: -16),
            spinner.widthAnchor.constraint(equalToConstant: 32),
            spinner.heightAnchor.constraint(equalToConstant: 32),
            loadingLabel.topAnchor.constraint(equalTo: spinner.bottomAnchor, constant: 16),
            loadingLabel.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            loadingLabel.leadingAnchor.constraint(greaterThanOrEqualTo: view.leadingAnchor, constant: 40),
            loadingLabel.trailingAnchor.constraint(lessThanOrEqualTo: view.trailingAnchor, constant: -40),
        ])
        loadingView = view
        fillPane(view)
    }

    private func buildWebView() {
        let config = WKWebViewConfiguration()
        // CRITICAL: announcer lines, music, and UI sounds are triggered by
        // game events, not by user gestures. Without this, WKWebView stays
        // silent and the game loses its entire audio identity.
        config.mediaTypesRequiringUserActionForPlayback = []
        config.defaultWebpagePreferences.allowsContentJavaScript = true
        config.websiteDataStore = .default()
        config.userContentController.add(self, name: "battlepew")

        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.allowsMagnification = false
        webView.allowsBackForwardNavigationGestures = false
        // No toolbar, address bar, or tab UI exists: the web content fills
        // the window exactly like a native view.
        webView.isHidden = true
        fillPane(webView)
    }

    private func buildErrorView() {
        let view = NSView()
        view.wantsLayer = true
        view.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor

        errorTitleLabel = NSTextField(labelWithString: "")
        errorTitleLabel.font = .boldSystemFont(ofSize: 17)
        errorTitleLabel.alignment = .center
        errorTitleLabel.translatesAutoresizingMaskIntoConstraints = false

        errorDetailField = NSTextField(wrappingLabelWithString: "")
        errorDetailField.font = .systemFont(ofSize: 13)
        errorDetailField.textColor = .secondaryLabelColor
        errorDetailField.alignment = .center
        errorDetailField.translatesAutoresizingMaskIntoConstraints = false

        errorRelaunchButton = NSButton(title: "Relaunch Server", target: self, action: #selector(relaunchServer))
        errorRelaunchButton.bezelStyle = .rounded
        errorRelaunchButton.keyEquivalent = "\r"
        errorRelaunchButton.translatesAutoresizingMaskIntoConstraints = false

        let logButton = NSButton(title: "Show Server Log", target: self, action: #selector(showServerLog))
        logButton.bezelStyle = .rounded
        logButton.translatesAutoresizingMaskIntoConstraints = false

        let quitButton = NSButton(title: "Quit", target: self, action: #selector(quitApp))
        quitButton.bezelStyle = .rounded
        quitButton.translatesAutoresizingMaskIntoConstraints = false

        let stack = NSStackView(views: [errorRelaunchButton, logButton, quitButton])
        stack.orientation = .horizontal
        stack.spacing = 12
        stack.alignment = .centerX
        stack.translatesAutoresizingMaskIntoConstraints = false

        view.addSubview(errorTitleLabel)
        view.addSubview(errorDetailField)
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            errorTitleLabel.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            errorTitleLabel.centerYAnchor.constraint(equalTo: view.centerYAnchor, constant: -60),
            errorTitleLabel.leadingAnchor.constraint(greaterThanOrEqualTo: view.leadingAnchor, constant: 60),
            errorTitleLabel.trailingAnchor.constraint(lessThanOrEqualTo: view.trailingAnchor, constant: -60),
            errorDetailField.topAnchor.constraint(equalTo: errorTitleLabel.bottomAnchor, constant: 12),
            errorDetailField.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            errorDetailField.widthAnchor.constraint(lessThanOrEqualToConstant: 640),
            stack.topAnchor.constraint(equalTo: errorDetailField.bottomAnchor, constant: 24),
            stack.centerXAnchor.constraint(equalTo: view.centerXAnchor),
        ])
        errorView = view
        errorView.isHidden = true
        fillPane(view)
    }

    private func showLoading(message: String) {
        loadingLabel.stringValue = message
        loadingView.isHidden = false
        webView.isHidden = true
        errorView.isHidden = true
    }

    private func showWebView(url: URL) {
        loadingView.isHidden = true
        errorView.isHidden = true
        webView.isHidden = false
        webView.load(URLRequest(url: url))
    }

    private func showError(title: String, detail: String, allowRelaunch: Bool) {
        errorTitleLabel.stringValue = title
        errorDetailField.stringValue = detail
        errorRelaunchButton.isHidden = !allowRelaunch
        loadingView.isHidden = true
        webView.isHidden = true
        errorView.isHidden = false
    }

    // MARK: - Error view actions

    @objc private func relaunchServer() {
        // User-initiated only, never automatic: a crash loop must not spin
        // the server forever. The launch's hardware choice is reused — the
        // user already authorized this session.
        relaunchCount += 1
        showLoading(message: "Relaunching the game server (attempt \(relaunchCount + 1))…")
        serverManager.stop { [weak self] in
            self?.startServerAndShowUI()
        }
    }

    @objc private func showServerLog() {
        serverManager.revealLogInFinder()
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }

    // MARK: - NSWindowDelegate

    func windowDidEnterFullScreen(_ notification: Notification) {
        UserDefaults.standard.set(true, forKey: Self.fullscreenDefaultsKey)
    }

    func windowDidExitFullScreen(_ notification: Notification) {
        UserDefaults.standard.set(false, forKey: Self.fullscreenDefaultsKey)
    }

    // MARK: - WKScriptMessageHandler

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        let action: String?
        if let body = message.body as? [String: Any] {
            action = body["action"] as? String
        } else {
            action = message.body as? String
        }
        switch action {
        case "quit":
            quitApp()
        case "selectInstaller":
            presentInstallerOpenPanel()
        default:
            break
        }
    }

    private func presentInstallerOpenPanel() {
        let panel = NSOpenPanel()
        panel.title = "Select BattleTag_Installer.zip or Data1.cab"
        panel.message = "Choose the official Ubisoft BattleTag installer ZIP (or Data1.cab) to extract audio assets."
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        var types: [UTType] = [.zip]
        if let cab = UTType(filenameExtension: "cab") {
            types.append(cab)
        }
        panel.allowedContentTypes = types

        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self = self, response == .OK, let url = panel.url else { return }
            if let data = try? JSONSerialization.data(withJSONObject: [url.path]),
               let jsonArray = String(data: data, encoding: .utf8) {
                // Pass single-element JSON array and index [0] to safely escape any path characters.
                let js = "window.__onInstallerSelected && window.__onInstallerSelected((\(jsonArray))[0]);"
                self.webView.evaluateJavaScript(js, completionHandler: nil)
            }
        }
    }

    // MARK: - WKNavigationDelegate

    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        // The shell hosts exactly one origin: the local server. Anything
        // else (the UI has no external links today) opens in the user's
        // real browser instead of navigating the app window away.
        if url.scheme == "about" || url.host == "127.0.0.1" || url.host == "localhost" || url.host == "::1" {
            decisionHandler(.allow)
        } else if url.scheme == "http" || url.scheme == "https" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        } else {
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView,
                 didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        // A failed load with a dead server means the crash screen; a failed
        // load with a live server is transient (e.g. a reload racing a
        // restart) and the web UI's own reconnect logic covers it.
        guard !isQuitting, serverManager != nil, !serverManager.isRunning else { return }
        showError(title: "The game server stopped.",
                  detail: "BattlePew lost its connection to the game server.\n\n\(serverManager.recentLogText(maxLines: 10))",
                  allowRelaunch: true)
    }

    // MARK: - Menu bar

    @objc private func reloadWebView() {
        guard !webView.isHidden else { return }
        webView.reload()
    }

    @objc private func handleSelectAll(_ sender: Any?) {
        if !allowHardware, !webView.isHidden {
            let js = "typeof window.__battlepewTryAddSimPlayer === 'function' ? window.__battlepewTryAddSimPlayer() : false;"
            webView.evaluateJavaScript(js) { result, _ in
                if (result as? Bool) != true {
                    NSApp.sendAction(#selector(NSText.selectAll(_:)), to: nil, from: sender)
                }
            }
            return
        }
        NSApp.sendAction(#selector(NSText.selectAll(_:)), to: nil, from: sender)
    }

    @objc private func showHelpAlert() {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        let alert = NSAlert()
        alert.messageText = "BattlePew \(version)"
        alert.informativeText = """
            Arena Master for the 2010 Battle Tag laser-tag system.

            Phones join by scanning the QR code on the main screen. If something goes wrong, choose Show Server Log from any error screen, or look in ~/Library/Logs/BattlePew/.

            Hold the Option (⌥) key while launching BattlePew to run in simulator mode without touching any hardware.
            """
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func buildMenu() {
        let mainMenu = NSMenu()

        // BattlePew menu
        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(title: "About BattlePew",
                                   action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
                                   keyEquivalent: ""))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Hide BattlePew",
                                   action: #selector(NSApplication.hide(_:)),
                                   keyEquivalent: "h"))
        appMenu.addItem(NSMenuItem(title: "Hide Others",
                                   action: #selector(NSApplication.hideOtherApplications(_:)),
                                   keyEquivalent: "h"))
        appMenu.addItem(NSMenuItem(title: "Show All",
                                   action: #selector(NSApplication.unhideAllApplications(_:)),
                                   keyEquivalent: ""))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Quit BattlePew",
                                   action: #selector(NSApplication.terminate(_:)),
                                   keyEquivalent: "q"))
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)

        // Edit menu (standard selectors reach the web view's text fields,
        // e.g. pasting a player name in the profile editor).
        let editMenuItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem(title: "Select All", action: #selector(handleSelectAll(_:)), keyEquivalent: "a"))
        editMenuItem.submenu = editMenu
        mainMenu.addItem(editMenuItem)

        // View menu
        let viewMenuItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(NSMenuItem(title: "Reload", action: #selector(reloadWebView), keyEquivalent: "r"))
        let fullscreenItem = NSMenuItem(title: "Toggle Full Screen",
                                        action: #selector(NSWindow.toggleFullScreen(_:)),
                                        keyEquivalent: "f")
        fullscreenItem.keyEquivalentModifierMask = [.command, .control]
        viewMenu.addItem(fullscreenItem)
        viewMenuItem.submenu = viewMenu
        mainMenu.addItem(viewMenuItem)

        // Window menu
        let windowMenuItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(NSMenuItem(title: "Minimize", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m"))
        windowMenu.addItem(NSMenuItem(title: "Zoom", action: #selector(NSWindow.zoom(_:)), keyEquivalent: ""))
        windowMenu.addItem(.separator())
        windowMenu.addItem(NSMenuItem(title: "Bring All to Front",
                                      action: #selector(NSApplication.arrangeInFront(_:)),
                                      keyEquivalent: ""))
        windowMenuItem.submenu = windowMenu
        mainMenu.addItem(windowMenuItem)

        // Help menu
        let helpMenuItem = NSMenuItem()
        let helpMenu = NSMenu(title: "Help")
        helpMenu.addItem(NSMenuItem(title: "BattlePew Help", action: #selector(showHelpAlert), keyEquivalent: ""))
        helpMenuItem.submenu = helpMenu
        mainMenu.addItem(helpMenuItem)

        NSApp.mainMenu = mainMenu
    }
}
