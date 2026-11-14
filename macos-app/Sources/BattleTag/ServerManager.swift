import AppKit
import Foundation

/// Errors surfaced when the bundled server cannot be started or kept alive.
/// Every case carries a user-facing description: these strings reach the
/// native error screen, so they must read like an app message, not a log.
enum ServerError: Error, LocalizedError {
    case bundleIncomplete(detail: String)
    case noFreePort
    case processExitedEarly(exitCode: Int32, logExcerpt: String)
    case healthTimeout(port: Int, logExcerpt: String)
    case launchFailed(underlying: Error)

    var errorDescription: String? {
        switch self {
        case .bundleIncomplete(let detail):
            return "The BattlePew installation is incomplete. \(detail)"
        case .noFreePort:
            return "BattlePew could not find a free local port (tried 3466–3475). Quit another copy of BattlePew, or quit whatever is using those ports, and try again."
        case .processExitedEarly(let code, let excerpt):
            return "The game server stopped unexpectedly (exit code \(code)).\n\n\(excerpt)"
        case .healthTimeout(let port, let excerpt):
            return "The game server started but never answered on port \(port).\n\n\(excerpt)"
        case .launchFailed(let underlying):
            return "BattlePew could not start the game server: \(underlying.localizedDescription)"
        }
    }
}

/// Owns the bundled Python/FastAPI server child process: launch, health
/// checks, port selection, log capture, and guaranteed shutdown.
///
/// Lifecycle contract (see Docs/DESIGN_DECISIONS.md):
/// - Exactly one server process per app launch. Closing the window or
///   quitting the app always stops it: SIGTERM first (uvicorn shuts down
///   gracefully and the FastAPI shutdown hook closes the HID device),
///   SIGKILL after a 5 second grace period. No orphaned uvicorn.
/// - The server binds 0.0.0.0 so phones on the LAN can reach the HUD.
///   Port 3466 is strongly preferred so the QR code URL stays stable; on
///   conflict the shell falls back to 3467–3475 and passes the real port
///   via BATTLETAG_PORT, which the server reads (default 3466). The QR PNG
///   endpoint already uses the request port, so QR codes stay correct.
/// - All callbacks are delivered on the main queue.
final class ServerManager {

    // MARK: - Configuration

    /// First port to try. This must stay 3466: it is the server's default
    /// (`run_battletag.py --port`, `BATTLETAG_PORT`), and matching it keeps
    /// the QR URL stable between the app and script-launched runs.
    static let preferredPort = 3466
    static let portRange = 3466...3475
    /// How long to wait for /api/state to answer after each launch attempt.
    static let healthTimeout: TimeInterval = 20
    /// Grace period between SIGTERM and SIGKILL on shutdown.
    static let stopGracePeriod: TimeInterval = 5

    private let pythonURL: URL
    private let serverDir: URL
    private let logDirectory: URL

    /// Called on the main queue when the server process exits while it is
    /// supposed to be running (crash). Not called for intentional stops.
    var onUnexpectedExit: ((String) -> Void)?

    private(set) var currentPort: Int?
    private(set) var isRunning = false

    private var process: Process?
    private var pipe: Pipe?
    private var logHandle: FileHandle?
    private var stopping = false
    private var shouldBeRunning = false
    private let logLock = NSLock()
    private var recentLines: [String] = []
    private let workQueue = DispatchQueue(label: "com.macsobel.battlepew.server", qos: .userInitiated)

    var logFileURL: URL {
        logDirectory.appendingPathComponent("battletag-server.log")
    }

    init(pythonURL: URL, serverDir: URL, logDirectory: URL) {
        self.pythonURL = pythonURL
        self.serverDir = serverDir
        self.logDirectory = logDirectory
    }

    // MARK: - Preflight checks (before launch)

    /// True when another BattlePew server already answers on port 3466.
    /// Prevents a second copy of the app from stealing the port or
    /// confusing the QR code. Best-effort: any error means "not running".
    func checkAlreadyRunning(completion: @escaping (Bool) -> Void) {
        guard let url = URL(string: "http://127.0.0.1:\(Self.preferredPort)/api/state") else {
            completion(false)
            return
        }
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 1.5
        config.timeoutIntervalForResource = 1.5
        let session = URLSession(configuration: config)
        let task = session.dataTask(with: url) { data, response, _ in
            var running = false
            if let http = response as? HTTPURLResponse, http.statusCode == 200,
               let data = data, let text = String(data: data, encoding: .utf8),
               text.contains("\"phase\"") {
                running = true
            }
            DispatchQueue.main.async { completion(running) }
        }
        task.resume()
    }

    /// Count UbiConnect devices (VID 0x044F / PID 0xB365) using the bundled
    /// Python's hidapi. Read-only enumeration; never opens the device.
    /// Returns nil when the probe itself fails (caller treats nil as
    /// "unknown" and asks the user instead of guessing).
    func probeHIDDeviceCount(completion: @escaping (Int?) -> Void) {
        workQueue.async { [weak self] in
            guard let self = self else { return }
            let probe = Process()
            probe.executableURL = self.pythonURL
            probe.arguments = ["-c", "import hid; print(len(hid.enumerate(0x044F, 0xB365)))"]
            probe.currentDirectoryURL = self.serverDir
            probe.environment = self.environment(port: Self.preferredPort, allowHardware: false)
            let outPipe = Pipe()
            probe.standardOutput = outPipe
            probe.standardError = FileHandle.nullDevice
            do {
                try probe.run()
            } catch {
                DispatchQueue.main.async { completion(nil) }
                return
            }
            // Watchdog: never let the probe hang the launch.
            let watchdog = DispatchWorkItem { [weak probe] in
                if let probe = probe, probe.isRunning { probe.terminate() }
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + 8, execute: watchdog)
            probe.waitUntilExit()
            watchdog.cancel()
            let data = outPipe.fileHandleForReading.readDataToEndOfFile()
            let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let count = Int(text)
            DispatchQueue.main.async { completion(count) }
        }
    }

    // MARK: - Start

    /// Launch the server, waiting until it answers /api/state. Tries ports
    /// in order; a bind failure on one port moves to the next. The
    /// completion reports the port the server actually bound.
    func start(allowHardware: Bool, completion: @escaping (Result<Int, ServerError>) -> Void) {
        workQueue.async { [weak self] in
            guard let self = self else { return }
            // Validate the bundle before spawning anything: a missing
            // interpreter or entry script is an install problem, not a
            // server problem, and must not be retried as one.
            let fileManager = FileManager.default
            let runScript = self.serverDir.appendingPathComponent("run_battletag.py")
            guard fileManager.isExecutableFile(atPath: self.pythonURL.path) else {
                self.finishOnMain(.failure(.bundleIncomplete(detail: "The embedded Python interpreter is missing.")),
                                   completion: completion)
                return
            }
            guard fileManager.fileExists(atPath: runScript.path) else {
                self.finishOnMain(.failure(.bundleIncomplete(detail: "The server entry point (run_battletag.py) is missing.")),
                                   completion: completion)
                return
            }
            self.openLogFile()
            self.appendLogLine("=== BattlePew launch \(Date()) allowHardware=\(allowHardware) ===")

            var lastError: ServerError = .noFreePort
            for port in Self.portRange {
                guard self.portLooksFree(port) else { continue }
                let outcome = self.launchOnce(port: port, allowHardware: allowHardware)
                switch outcome {
                case .success:
                    self.currentPort = port
                    self.isRunning = true
                    self.shouldBeRunning = true
                    self.finishOnMain(.success(port), completion: completion)
                    return
                case .failure(let error):
                    lastError = error
                    // Only a bind conflict justifies trying the next port.
                    // Any other early failure will repeat identically.
                    if case .processExitedEarly(_, let excerpt) = error,
                       excerpt.localizedCaseInsensitiveContains("address already in use") {
                        continue
                    }
                    self.finishOnMain(.failure(error), completion: completion)
                    return
                }
            }
            self.finishOnMain(.failure(lastError), completion: completion)
        }
    }

    /// One launch attempt on one port: spawn, then poll /api/state until
    /// healthy, the process exits, or the timeout expires. Blocking; call
    /// only on `workQueue`.
    private func launchOnce(port: Int, allowHardware: Bool) -> Result<Void, ServerError> {
        let proc = Process()
        proc.executableURL = pythonURL
        var args = [serverDir.appendingPathComponent("run_battletag.py").path,
                    "--host", "0.0.0.0",
                    "--port", String(port)]
        if allowHardware {
            // The --confirm-write flag is the server's existing hardware
            // authorization mechanism (run_battletag.py sets
            // BATTLETAG_CONFIRM_WRITE). The shell passes it only after the
            // user explicitly allows hardware control for this launch.
            args.append("--confirm-write")
        }
        proc.arguments = args
        proc.currentDirectoryURL = serverDir
        proc.environment = environment(port: port, allowHardware: allowHardware)

        let combinedPipe = Pipe()
        proc.standardOutput = combinedPipe
        proc.standardError = combinedPipe
        self.pipe = combinedPipe
        self.process = proc

        combinedPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let self = self else { return }
            self.appendLogData(data)
        }
        proc.terminationHandler = { [weak self] finished in
            guard let self = self else { return }
            let wasExpected = self.stopping || !self.shouldBeRunning
            self.isRunning = false
            if !wasExpected {
                let excerpt = self.recentLogText(maxLines: 20)
                let detail = "The game server stopped unexpectedly (exit code \(finished.terminationStatus)).\n\n\(excerpt)"
                DispatchQueue.main.async { self.onUnexpectedExit?(detail) }
            }
        }

        do {
            try proc.run()
        } catch {
            return .failure(.launchFailed(underlying: error))
        }

        // Poll health until success, early exit, or timeout.
        let deadline = Date().addingTimeInterval(Self.healthTimeout)
        while Date() < deadline {
            if !proc.isRunning {
                let excerpt = recentLogText(maxLines: 20)
                return .failure(.processExitedEarly(exitCode: proc.terminationStatus, logExcerpt: excerpt))
            }
            if pollHealthOnce(port: port) { return .success(()) }
            Thread.sleep(forTimeInterval: 0.25)
        }
        // Timed out with the process still alive: stop it so the port is
        // free for the next attempt, then report the timeout.
        stopProcess(proc, gracePeriod: 2)
        return .failure(.healthTimeout(port: port, logExcerpt: recentLogText(maxLines: 20)))
    }

    /// Synchronous single health probe. Used from `workQueue` only.
    private func pollHealthOnce(port: Int) -> Bool {
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/state") else { return false }
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 1.0
        config.timeoutIntervalForResource = 1.0
        let session = URLSession(configuration: config)
        var healthy = false
        let semaphore = DispatchSemaphore(value: 0)
        let task = session.dataTask(with: url) { data, response, _ in
            if let http = response as? HTTPURLResponse, http.statusCode == 200,
               let data = data, let text = String(data: data, encoding: .utf8),
               text.contains("\"phase\"") {
                healthy = true
            }
            semaphore.signal()
        }
        task.resume()
        _ = semaphore.wait(timeout: .now() + 1.5)
        return healthy
    }

    // MARK: - Stop

    /// Graceful async stop: SIGTERM, grace period, then SIGKILL.
    func stop(completion: @escaping () -> Void = {}) {
        workQueue.async { [weak self] in
            guard let self = self else { return }
            self.stopping = true
            self.shouldBeRunning = false
            if let proc = self.process, proc.isRunning {
                self.stopProcess(proc, gracePeriod: Self.stopGracePeriod)
            }
            self.process = nil
            self.isRunning = false
            self.closeLogFile()
            self.stopping = false
            DispatchQueue.main.async { completion() }
        }
    }

    /// Blocking stop for `applicationWillTerminate`, where there is no
    /// run loop left to deliver an async completion. Never called on the
    /// main queue from normal code paths.
    func stopSynchronously(timeout: TimeInterval = ServerManager.stopGracePeriod + 1) {
        stopping = true
        shouldBeRunning = false
        if let proc = process, proc.isRunning {
            stopProcess(proc, gracePeriod: min(timeout, Self.stopGracePeriod))
        }
        process = nil
        isRunning = false
        closeLogFile()
        stopping = false
    }

    /// SIGTERM, wait, SIGKILL. Blocking; call only off the main queue
    /// (or from applicationWillTerminate).
    private func stopProcess(_ proc: Process, gracePeriod: TimeInterval) {
        proc.terminate() // SIGTERM: uvicorn shuts down gracefully and the
                         // FastAPI shutdown hook closes the HID device.
        let deadline = Date().addingTimeInterval(gracePeriod)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }
        if proc.isRunning {
            // Last resort: no orphaned uvicorn may survive the app.
            kill(proc.processIdentifier, SIGKILL)
            let killDeadline = Date().addingTimeInterval(2)
            while proc.isRunning && Date() < killDeadline {
                Thread.sleep(forTimeInterval: 0.1)
            }
        }
        pipe?.fileHandleForReading.readabilityHandler = nil
    }

    // MARK: - Port probing

    /// Best-effort "is this port bindable right now" check. A race remains
    /// between this probe and the server's bind; the bind-error retry in
    /// `start` covers it. Never treats a probe failure as fatal.
    private func portLooksFree(_ port: Int) -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP)
        guard fd >= 0 else { return true }
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(port).bigEndian
        addr.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        var reuse: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))
        let result = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                bind(fd, sockPtr, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        return result == 0
    }

    // MARK: - Environment

    private func environment(port: Int, allowHardware: Bool) -> [String: String] {
        var env: [String: String] = [:]
        let parent = ProcessInfo.processInfo.environment
        for key in ["HOME", "TMPDIR", "LANG", "LC_ALL", "USER"] {
            if let value = parent[key] { env[key] = value }
        }
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1" // Never write __pycache__ into the bundle.
        env["PYTHONPATH"] = serverDir.appendingPathComponent(".deps").path
        env["BATTLETAG_PORT"] = String(port)
        env["BATTLETAG_LOG_DIR"] = logDirectory.path
        _ = allowHardware // Authorization travels via --confirm-write, not env.
        return env
    }

    // MARK: - Logging

    private func openLogFile() {
        do {
            try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
            let url = logFileURL
            if !FileManager.default.fileExists(atPath: url.path) {
                FileManager.default.createFile(atPath: url.path, contents: nil)
            }
            logHandle = try FileHandle(forWritingTo: url)
            logHandle?.seekToEndOfFile()
        } catch {
            logHandle = nil // Logging must never break the launch.
        }
    }

    private func closeLogFile() {
        logLock.lock()
        defer { logLock.unlock() }
        appendLogLineLocked("=== BattlePew server stopped \(Date()) ===")
        try? logHandle?.close()
        logHandle = nil
    }

    private func appendLogData(_ data: Data) {
        logLock.lock()
        defer { logLock.unlock() }
        logHandle?.write(data)
        if let text = String(data: data, encoding: .utf8) {
            for line in text.components(separatedBy: .newlines) where !line.isEmpty {
                recentLines.append(line)
            }
            if recentLines.count > 200 { recentLines.removeFirst(recentLines.count - 200) }
        }
    }

    private func appendLogLine(_ line: String) {
        logLock.lock()
        defer { logLock.unlock() }
        appendLogLineLocked(line)
    }

    private func appendLogLineLocked(_ line: String) {
        if let data = (line + "\n").data(using: .utf8) {
            logHandle?.write(data)
        }
        recentLines.append(line)
        if recentLines.count > 200 { recentLines.removeFirst(recentLines.count - 200) }
    }

    /// Last lines of server output, for the native error screen.
    func recentLogText(maxLines: Int) -> String {
        logLock.lock()
        defer { logLock.unlock() }
        let tail = recentLines.suffix(maxLines)
        if tail.isEmpty { return "(No server output was captured. See the server log for details.)" }
        return tail.joined(separator: "\n")
    }

    func revealLogInFinder() {
        NSWorkspace.shared.activateFileViewerSelecting([logFileURL])
    }

    private func finishOnMain(_ result: Result<Int, ServerError>,
                              completion: @escaping (Result<Int, ServerError>) -> Void) {
        DispatchQueue.main.async { completion(result) }
    }
}
