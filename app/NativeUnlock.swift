import AppKit

// 一个窗口 + 菜单栏入口的小工具。
// 所有真正的补丁操作都在内置 Python 后端里完成；这里只负责显示状态、
// 收集用户意图，并把后端返回的 JSON 转成中文提示。
class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!

    // 主开关：安装 / 恢复正式版可用性补丁。
    let toggle = NSSwitch()
    let statusLabel = NSTextField(labelWithString: "正在检测正式版…")
    let versionLabel = NSTextField(labelWithString: "Claude /Applications/Claude.app")

    // 底部操作。
    let openButton = NSButton(title: "打开 Claude", target: nil, action: nil)
    let refreshButton = NSButton(title: "重新检测", target: nil, action: nil)
    let permissionButton = NSButton(title: "系统授权说明", target: nil, action: nil)

    // 补丁操作。只保留“更新到最新补丁”和官方更新前恢复原版。
    let updateButton = NSButton(title: "更新解锁补丁", target: nil, action: nil)
    let prepareButton = NSButton(title: "为官方更新做准备", target: nil, action: nil)

    let progress = NSProgressIndicator()
    let actionSeparator = NSBox()

    // 后端最近一次检测结果。
    var enabled = false
    var busy = false
    var officialRunning = false
    var officialSupported = false
    var backupVerified = false
    var officialMessage = "正在检测正式版…"
    var recoveryRequired = false
    var updateAvailable = false
    var codeMemorySupported = false
    var patchRevision = 0
    var availableRevision = 0
    var targetExists = false
    var statusLoaded = false
    var lastError: String?
    var pendingClaudeRefresh = false
    var featureConfigWritable = false
    var featureRestorable: [Bool] = [false, false, false, false, false]

    // 一键退出 Claude 并应用修改。
    var quittingClaude = false
    var quitTimer: Timer?
    var quitFinalizeDeadline = Date.distantPast
    var pendingAfterQuit: (() -> Void)?
    var relaunchAfterAction = false
    var lastStatusAt = Date.distantPast

    // 菜单栏。
    var statusItem: NSStatusItem!
    let trayMenu = NSMenu()
    var trayStatus: NSMenuItem!
    var trayRefresh: NSMenuItem!
    var trayOpen: NSMenuItem!
    var trayUpdate: NSMenuItem!
    var trayPrepare: NSMenuItem!
    var trayQuit: NSMenuItem!

    let storageURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Claude Native Unlock")

    let featureIDs = ["file-analysis", "browser", "scheduled-tasks", "auto-approve", "code-auto-dream"]
    let featureTitles = ["Chat 与高级文件分析", "官方内置浏览器", "官方定时任务", "Cowork 自动审批选项", "Code 原生自动整理可用性"]
    let featureDescriptions = [
        "Chat 使用标准工作台，启用官方高级文件分析。",
        "启用 Claude 官方内置浏览器（不是 Dia / Chrome）。",
        "允许官方定时任务；是否执行仍由 Claude 决定。",
        "显示 Cowork 官方自动审批选项；动作审查保留。",
        "只解除 Code 自动整理的可用性限制；CLI 2.1.299 已适配，用户开关保持。"
    ]
    var featureSwitches: [NSSwitch] = []
    var featureLabels: [NSTextField] = []
    var featureEnabled: [Bool] = [false, false, false, false, false]
    let featureSummary = NSTextField(wrappingLabelWithString: "正在检测原生配置…")

    // 版本号从 Info.plist 读取，避免以后升级时忘记改界面文案。
    var assistantVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "未知"
    }
    var assistantBuild: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "未知"
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)

        // 应用菜单。
        let menu = NSMenu()
        let item = NSMenuItem()
        menu.addItem(item)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于 Claude 原生助手", action: #selector(showAbout), keyEquivalent: "").target = self
        appMenu.addItem(withTitle: "显示主窗口", action: #selector(showWindow), keyEquivalent: "0").target = self
        appMenu.addItem(withTitle: "重新检测", action: #selector(refresh), keyEquivalent: "r").target = self
        appMenu.addItem(withTitle: "打开 Claude", action: #selector(openClaude), keyEquivalent: "o").target = self
        appMenu.addItem(withTitle: "打开快捷操作菜单", action: #selector(showStatusMenu), keyEquivalent: "m").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出 Claude 原生助手", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu
        NSApp.mainMenu = menu
        installStatusItem()

        // Claude 启动或退出后自动重新检测，开关状态不需要用户手动刷新。
        let workspaceCenter = NSWorkspace.shared.notificationCenter
        workspaceCenter.addObserver(self, selector: #selector(claudeStateChanged(_:)),
                                    name: NSWorkspace.didLaunchApplicationNotification, object: nil)
        workspaceCenter.addObserver(self, selector: #selector(claudeStateChanged(_:)),
                                    name: NSWorkspace.didTerminateApplicationNotification, object: nil)
        // 唤醒后文件状态可能已经变化，顺手刷新一次。
        workspaceCenter.addObserver(self, selector: #selector(systemDidWake(_:)),
                                    name: NSWorkspace.didWakeNotification, object: nil)

        // 主窗口。
        // 780 高度给补丁操作行和最多四行状态留出空间，避免底部按钮重叠。
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 620, height: 780),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Claude 原生助手"
        window.isReleasedWhenClosed = false
        // 记住上次位置；窗口在已断开的显示器上时回到主屏居中。
        window.setFrameAutosaveName("ClaudeNativeUnlockMainWindow")
        if !NSScreen.screens.contains(where: { $0.visibleFrame.intersects(window.frame) }) {
            window.center()
        }
        let content = NSView()
        window.contentView = content

        let title = NSTextField(labelWithString: "Claude 官方原生功能")
        title.font = .systemFont(ofSize: 23, weight: .semibold)
        let subtitle = NSTextField(labelWithString: "为现有第三方 API 模式开启原生能力")
        subtitle.textColor = .secondaryLabelColor
        let feature = NSTextField(labelWithString: "Computer Use · Code 工作台与工作树")
        feature.font = .systemFont(ofSize: 16, weight: .medium)

        toggle.target = self
        toggle.action = #selector(switchChanged)
        toggle.setAccessibilityLabel("Computer Use 解锁开关")
        toggle.isEnabled = false

        statusLabel.font = .systemFont(ofSize: 13)
        statusLabel.maximumNumberOfLines = 4
        statusLabel.lineBreakMode = .byWordWrapping
        versionLabel.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        versionLabel.textColor = .secondaryLabelColor

        // 尽量用短句：用户需要知道“现在能不能用”和“下一步点什么”。
        let note = NSTextField(wrappingLabelWithString:
            "Claude 正在运行时也可以直接操作：助手会先正常退出，完成后再自动打开。\n" +
            "首次使用请在 Claude 设置 → This computer → System 确认辅助功能、屏幕录制均为 Granted。")
        note.font = .systemFont(ofSize: 12)
        note.textColor = .secondaryLabelColor

        for button in [openButton, refreshButton, permissionButton, updateButton, prepareButton] {
            button.bezelStyle = .rounded
            button.target = self
        }
        openButton.action = #selector(openClaude)
        refreshButton.action = #selector(refresh)
        permissionButton.action = #selector(showPermissionGuide)
        updateButton.action = #selector(confirmUpdate)
        prepareButton.action = #selector(prepareUpdate)
        updateButton.isEnabled = false
        prepareButton.isEnabled = false
        toggle.toolTip = "安装或恢复 Computer Use 等原生可用性补丁。"
        updateButton.toolTip = "把当前补丁更新到最新 revision；始终只保留最新版。"
        prepareButton.toolTip = "恢复完整官方签名，供官方更新器使用；不会删除数据。"

        featureSummary.font = .systemFont(ofSize: 12)
        featureSummary.textColor = .secondaryLabelColor

        // 五组官方配置开关。
        let rows = NSStackView()
        rows.orientation = .vertical
        rows.alignment = .leading
        rows.spacing = 12
        for (index, name) in featureTitles.enumerated() {
            let row = NSView()
            let label = NSTextField(labelWithString: name)
            label.font = .systemFont(ofSize: 15, weight: .medium)
            label.toolTip = featureDescriptions[index]
            let detail = NSTextField(labelWithString: "正在检测…")
            detail.font = .systemFont(ofSize: 12)
            detail.textColor = .secondaryLabelColor
            let control = NSSwitch()
            control.tag = index
            control.target = self
            control.action = #selector(featureChanged(_:))
            control.setAccessibilityLabel(name + " 配置开关")
            control.isEnabled = false
            featureSwitches.append(control)
            featureLabels.append(detail)
            for view in [label, detail, control] {
                view.translatesAutoresizingMaskIntoConstraints = false
                row.addSubview(view)
            }
            row.translatesAutoresizingMaskIntoConstraints = false
            rows.addArrangedSubview(row)
            NSLayoutConstraint.activate([
                row.widthAnchor.constraint(equalTo: rows.widthAnchor),
                row.heightAnchor.constraint(equalToConstant: 44),
                label.leadingAnchor.constraint(equalTo: row.leadingAnchor),
                label.topAnchor.constraint(equalTo: row.topAnchor),
                detail.leadingAnchor.constraint(equalTo: row.leadingAnchor),
                detail.topAnchor.constraint(equalTo: label.bottomAnchor, constant: 4),
                control.trailingAnchor.constraint(equalTo: row.trailingAnchor),
                control.centerYAnchor.constraint(equalTo: row.centerYAnchor)
            ])
        }

        progress.style = .spinning
        progress.controlSize = .small
        progress.isDisplayedWhenStopped = false
        actionSeparator.boxType = .separator

        let views: [NSView] = [title, subtitle, feature, toggle, statusLabel, versionLabel, note,
                               openButton, refreshButton, permissionButton,
                               updateButton, prepareButton,
                               progress, rows, featureSummary, actionSeparator]
        for view in views {
            view.translatesAutoresizingMaskIntoConstraints = false
            content.addSubview(view)
        }

        NSLayoutConstraint.activate([
            title.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 28),
            title.topAnchor.constraint(equalTo: content.topAnchor, constant: 25),
            subtitle.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            subtitle.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 7),
            feature.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            feature.topAnchor.constraint(equalTo: subtitle.bottomAnchor, constant: 27),
            toggle.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -28),
            toggle.centerYAnchor.constraint(equalTo: feature.centerYAnchor),
            statusLabel.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            statusLabel.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -28),
            statusLabel.topAnchor.constraint(equalTo: feature.bottomAnchor, constant: 12),
            versionLabel.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            versionLabel.topAnchor.constraint(equalTo: statusLabel.bottomAnchor, constant: 7),
            note.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            note.trailingAnchor.constraint(equalTo: statusLabel.trailingAnchor),
            note.topAnchor.constraint(equalTo: versionLabel.bottomAnchor, constant: 18),
            rows.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            rows.trailingAnchor.constraint(equalTo: statusLabel.trailingAnchor),
            rows.topAnchor.constraint(equalTo: note.bottomAnchor, constant: 20),
            featureSummary.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            featureSummary.trailingAnchor.constraint(equalTo: statusLabel.trailingAnchor),
            featureSummary.topAnchor.constraint(equalTo: rows.bottomAnchor, constant: 18),
            featureSummary.bottomAnchor.constraint(lessThanOrEqualTo: actionSeparator.topAnchor, constant: -12),
            updateButton.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            // 操作按钮放在窗口底部、靠近通用按钮，形成一致的操作区。
            updateButton.bottomAnchor.constraint(equalTo: openButton.topAnchor, constant: -16),
            actionSeparator.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            actionSeparator.trailingAnchor.constraint(equalTo: statusLabel.trailingAnchor),
            actionSeparator.bottomAnchor.constraint(equalTo: updateButton.topAnchor, constant: -14),
            prepareButton.leadingAnchor.constraint(equalTo: updateButton.trailingAnchor, constant: 12),
            prepareButton.centerYAnchor.constraint(equalTo: updateButton.centerYAnchor),
            openButton.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            openButton.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -22),
            refreshButton.leadingAnchor.constraint(equalTo: openButton.trailingAnchor, constant: 12),
            refreshButton.centerYAnchor.constraint(equalTo: openButton.centerYAnchor),
            permissionButton.leadingAnchor.constraint(equalTo: refreshButton.trailingAnchor, constant: 12),
            permissionButton.centerYAnchor.constraint(equalTo: openButton.centerYAnchor),
            progress.trailingAnchor.constraint(equalTo: toggle.trailingAnchor),
            progress.centerYAnchor.constraint(equalTo: openButton.centerYAnchor)
        ])

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        execute("status-all", backend: "all")
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        // 回到助手窗口时顺手刷新；弹窗打开时或 4 秒内不重复检测。
        guard statusLoaded, !busy, !quittingClaude,
              window?.attachedSheet == nil,
              Date().timeIntervalSince(lastStatusAt) > 4 else { return }
        execute("status-all", backend: "all")
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationWillTerminate(_ notification: Notification) {
        quitTimer?.invalidate()
        NSWorkspace.shared.notificationCenter.removeObserver(self)
        NSStatusBar.system.removeStatusItem(statusItem)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        // 安装/恢复或等待 Claude 退出时不退出，避免留下半完成状态。
        return (busy || quittingClaude) ? .terminateCancel : .terminateNow
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow()
        if !busy && !quittingClaude { execute("status-all", backend: "all") }
        return true
    }

    // MARK: - 用户操作

    @objc func claudeStateChanged(_ notification: Notification) {
        guard let application = notification.userInfo?[NSWorkspace.applicationUserInfoKey]
                as? NSRunningApplication,
              application.bundleIdentifier == "com.anthropic.claudefordesktop" else { return }
        // 一键退出流程由自己的计时器收尾，不再重复排队。
        if quittingClaude { return }
        if busy {
            // 当前检测可能早于这次启停；等本轮结束后再刷新一次。
            pendingClaudeRefresh = true
        } else {
            execute("status-all", backend: "all")
        }
    }

    @objc func systemDidWake(_ notification: Notification) {
        guard statusLoaded, !busy, !quittingClaude,
              window?.attachedSheet == nil else { return }
        execute("status-all", backend: "all")
    }

    @objc func refresh() { execute("status-all", backend: "all") }
    @objc func openClaude() { execute("launch") }
    @objc func prepareUpdate() { confirmPrepareUpdate() }

    @objc func switchChanged() {
        let command = toggle.state == .on ? "enable" : "disable"
        toggle.state = enabled ? .on : .off
        withClaudeQuitIfNeeded(command == "enable" ? "开启原生补丁" : "关闭原生补丁",
                               relaunch: true) {
            self.execute(command)
        }
    }

    @objc func openFeatureReport() {
        if let url = Bundle.main.url(forResource: "使用说明", withExtension: "html") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func featureChanged(_ sender: NSSwitch) {
        let index = sender.tag
        let enabling = sender.state == .on
        sender.state = featureEnabled[index] ? .on : .off
        let name = featureTitles[index]
        withClaudeQuitIfNeeded((enabling ? "开启「" : "关闭「") + name + "」", relaunch: true) {
            if self.featureIDs[index] == "code-auto-dream" {
                self.execute(enabling ? "enable" : "restore", backend: "native_cli_unlock")
            } else {
                self.execute(enabling ? "enable" : "restore",
                             backend: "native_features", feature: self.featureIDs[index])
            }
        }
    }

    // MARK: - 自动退出 Claude

    func claudeApplication() -> NSRunningApplication? {
        NSWorkspace.shared.runningApplications.first {
            $0.bundleIdentifier == "com.anthropic.claudefordesktop" && !$0.isTerminated
        }
    }

    /// 只记录命令和错误文案，不写配置内容或密钥；用于排查“点了没反应”。
    func recordOperation(_ command: String, backend: String, result: [String: Any]) {
        var record: [String: Any] = [
            "date": ISO8601DateFormatter().string(from: Date()),
            "command": command,
            "backend": backend,
        ]
        if let error = result["error"] as? String { record["error"] = error }
        if let message = result["message"] as? String { record["message"] = message }
        if let official = result["official"] as? [String: Any] {
            record["officialMessage"] = official["message"]
            record["patchRevision"] = official["patchRevision"]
        }
        if let cli = result["cli"] as? [String: Any] {
            record["cliMessage"] = cli["message"]
        }
        try? FileManager.default.createDirectory(at: storageURL, withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(withJSONObject: record,
                                                 options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: storageURL.appendingPathComponent("last-operation.json"),
                            options: .atomic)
        }
    }

    func recordQuit(_ stage: String, _ detail: String = "") {
        let record: [String: Any] = [
            "date": ISO8601DateFormatter().string(from: Date()),
            "stage": stage,
            "detail": detail,
        ]
        try? FileManager.default.createDirectory(at: storageURL, withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(withJSONObject: record,
                                                 options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: storageURL.appendingPathComponent("last-quit.json"),
                            options: .atomic)
        }
    }

    /// 与后端 official.running() 使用同一判定：主程序退出后，Helper 可能还会存活片刻。
    func claudeProcessesRunning() -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/ps")
        process.arguments = ["-axo", "comm="]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            let text = String(decoding: data, as: UTF8.self)
            return text.split(separator: "\n").contains {
                $0.contains("/Applications/Claude.app/Contents/")
            }
        } catch {
            // 无法确认时按“仍在运行”处理，宁可让后端拒绝也不要冒写文件的风险。
            return true
        }
    }

    /// 写操作要求 Claude 退出；用户在弹窗里确认后由助手正常退出并收尾。
    func withClaudeQuitIfNeeded(_ actionName: String, relaunch: Bool,
                                action: @escaping () -> Void) {
        guard claudeApplication() != nil else {
            action()
            return
        }
        showWindow()
        let alert = NSAlert()
        alert.messageText = actionName + "需要先退出 Claude"
        alert.informativeText =
            "助手会正常退出 Claude（等同于菜单里的“退出”），完成操作后" +
            (relaunch ? "自动重新打开。" : "不会自动打开。") +
            "\n\n正在生成的回答会中断，未发送的输入可能丢失。"
        alert.addButton(withTitle: "退出并继续")
        alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { response in
            guard response == .alertFirstButtonReturn else { return }
            self.quitClaudeThen(relaunch: relaunch, action: action)
        }
    }

    /// 调用方已经弹过确认框时使用，避免连续出现两个相同确认。
    func continueAfterConfirmedQuit(relaunch: Bool, action: @escaping () -> Void) {
        if claudeApplication() != nil {
            quitClaudeThen(relaunch: relaunch, action: action)
        } else {
            action()
        }
    }

    func quitClaudeThen(relaunch: Bool, action: @escaping () -> Void) {
        quitClaudeThen(relaunch: relaunch, action: action,
                       retryUntil: Date().addingTimeInterval(35))
    }

    private func quitClaudeThen(relaunch: Bool, action: @escaping () -> Void,
                                retryUntil: Date) {
        guard !quittingClaude else { return }
        if busy {
            // 用户确认时可能刚好有一次检测在收尾；等它结束再执行，避免“点了没反应”。
            guard Date() < retryUntil else {
                endQuitWait(message: "当前检测仍未结束，请稍后再试。")
                return
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                self.quitClaudeThen(relaunch: relaunch, action: action,
                                    retryUntil: retryUntil)
            }
            return
        }
        guard let application = claudeApplication() else {
            // 应用刚好退出完成：稍等收尾后直接执行，由后端再次确认。
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { action() }
            return
        }
        quittingClaude = true
        pendingAfterQuit = action
        relaunchAfterAction = relaunch
        statusLabel.stringValue = "正在等待 Claude 退出…"
        statusLabel.textColor = .labelColor
        progress.startAnimation(nil)
        refreshButtons()
        updateStatusItem()

        recordQuit("requested", "pid=\(application.processIdentifier)")
        guard application.terminate() else {
            recordQuit("terminate-refused")
            endQuitWait(message: "Claude 没有接受退出请求。请手动退出后再操作。")
            return
        }
        let deadline = Date().addingTimeInterval(20)
        quitFinalizeDeadline = Date().addingTimeInterval(25)
        quitTimer?.invalidate()
        let timer = Timer(timeInterval: 0.25, repeats: true) { [weak self] timer in
            guard let self else {
                timer.invalidate()
                return
            }
            // 必须等主程序和所有 Helper 都退出；后端也会做同样的检查。
            if self.claudeApplication() == nil && !self.claudeProcessesRunning() {
                timer.invalidate()
                self.quitTimer = nil
                self.recordQuit("processes-gone")
                // 给官方应用一点收尾时间，再调用补丁后端。
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                    self.finishQuitAndRun()
                }
            } else if Date() > deadline {
                timer.invalidate()
                self.quitTimer = nil
                self.recordQuit("timeout",
                                "app=\(self.claudeApplication() != nil) helpers=\(self.claudeProcessesRunning())")
                self.endQuitWait(
                    title: "Claude 没有退出",
                    message: "Claude 拒绝了退出请求，通常是因为还有正在生成的回答或活跃的 Code 会话。" +
                        "请等当前任务结束后重试，或手动退出 Claude。")
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        quitTimer = timer
    }

    func finishQuitAndRun() {
        guard let action = pendingAfterQuit else {
            endQuitWait(message: "操作已取消。")
            return
        }
        // 等待期间用户可能又打开了 Claude；不要在半运行状态写入。
        if claudeProcessesRunning() {
            if Date() < quitFinalizeDeadline {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    self.finishQuitAndRun()
                }
            } else {
                recordQuit("finalize-timeout")
                endQuitWait(message: "Claude 的辅助进程仍在退出，请稍后再试。")
            }
            return
        }
        pendingAfterQuit = nil
        quittingClaude = false
        quitFinalizeDeadline = .distantPast
        recordQuit("action-start")
        progress.stopAnimation(nil)
        action()
        refreshButtons()
        updateStatusItem()
    }

    func endQuitWait(title: String = "还差一步", message: String) {
        quitTimer?.invalidate()
        quitTimer = nil
        quittingClaude = false
        quitFinalizeDeadline = .distantPast
        pendingAfterQuit = nil
        relaunchAfterAction = false
        progress.stopAnimation(nil)
        refreshButtons()
        updateStatusItem()
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.addButton(withTitle: "知道了")
        alert.beginSheetModal(for: window)
        execute("status-all", backend: "all")
    }

    // MARK: - 菜单栏

    func installStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let image = NSImage(systemSymbolName: "lock.open", accessibilityDescription: "Claude 原生助手菜单")
        image?.isTemplate = true
        statusItem.button?.image = image
        statusItem.button?.toolTip = "Claude 原生助手 · 正在检测"
        trayMenu.autoenablesItems = false

        let heading = trayMenu.addItem(withTitle: "Claude 原生助手", action: nil, keyEquivalent: "")
        heading.isEnabled = false
        trayStatus = trayMenu.addItem(withTitle: "正在检测…", action: nil, keyEquivalent: "")
        trayStatus.isEnabled = false
        trayMenu.addItem(.separator())
        trayMenu.addItem(withTitle: "显示主窗口", action: #selector(showWindow), keyEquivalent: "").target = self
        trayRefresh = trayMenu.addItem(withTitle: "重新检测", action: #selector(refresh), keyEquivalent: "")
        trayRefresh.target = self
        trayOpen = trayMenu.addItem(withTitle: "打开 Claude", action: #selector(openClaude), keyEquivalent: "")
        trayOpen.target = self
        trayUpdate = trayMenu.addItem(withTitle: "更新解锁补丁", action: #selector(confirmUpdate), keyEquivalent: "")
        trayUpdate.target = self
        trayPrepare = trayMenu.addItem(withTitle: "为官方更新做准备", action: #selector(prepareUpdate), keyEquivalent: "")
        trayPrepare.target = self
        trayMenu.addItem(withTitle: "使用说明", action: #selector(openFeatureReport), keyEquivalent: "").target = self
        trayMenu.addItem(withTitle: "关于", action: #selector(showAbout), keyEquivalent: "").target = self
        trayMenu.addItem(.separator())
        trayQuit = trayMenu.addItem(withTitle: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        statusItem.menu = trayMenu
    }

    func updateStatusItem() {
        let blocked = busy || quittingClaude
        let revision = enabled && patchRevision > 0 ? " · r\(patchRevision)" : ""
        let text = quittingClaude ? "正在等待 Claude 退出…"
            : busy ? "正在处理…"
            : recoveryRequired ? "安装中断 · 需要恢复"
            : officialSupported ? (enabled
                ? (backupVerified ? "原生补丁已安装" + revision : "原生补丁已安装 · 备份异常")
                : "原生补丁未开启")
            : (targetExists ? "版本待适配" : "未找到 Claude")

        let symbol: String
        if blocked {
            symbol = "hourglass"
        } else if !statusLoaded {
            symbol = "lock.open"
        } else if recoveryRequired || (enabled && !backupVerified) || !officialSupported {
            symbol = "exclamationmark.triangle"
        } else {
            symbol = enabled ? "lock.open" : "lock"
        }
        let image = NSImage(systemSymbolName: symbol, accessibilityDescription: "Claude 原生助手菜单")
        image?.isTemplate = true
        statusItem.button?.image = image
        statusItem.button?.toolTip = "Claude 原生助手 · " + text
        trayStatus.title = text
        trayRefresh.isEnabled = !blocked
        trayOpen.isEnabled = !blocked && targetExists
        trayUpdate.isEnabled = !blocked && updateAvailable
        trayUpdate.title = updateAvailable && availableRevision > 0
            ? "更新补丁到 r\(availableRevision)" : "更新解锁补丁"
        trayPrepare.isEnabled = !blocked && officialSupported && !recoveryRequired
            && (!enabled || backupVerified)
        trayQuit.isEnabled = !busy
    }

    @objc func showStatusMenu() {
        showWindow()
        DispatchQueue.main.async {
            guard let view = self.window.contentView else { return }
            self.trayMenu.popUp(positioning: nil,
                at: NSPoint(x: view.bounds.width - 24, y: view.bounds.height - 24), in: view)
        }
    }

    @objc func showWindow() {
        NSApp.setActivationPolicy(.regular)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func showAbout() {
        showWindow()
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Claude 原生助手",
            .applicationVersion: assistantVersion,
            .version: assistantBuild,
            .credits: NSAttributedString(string: "独立开源项目，与 Anthropic 无隶属关系。\n仅解除已适配版本的原生功能可用性条件。")
        ])
    }

    @objc func showPermissionGuide() {
        showWindow()
        let alert = NSAlert()
        alert.messageText = "确认 Claude 的系统授权"
        alert.informativeText =
            "在 Claude 设置 → This computer → System 中，开启 Enable computer use，" +
            "并确认 Accessibility 和 Screen recording 都显示 Granted。\n\n" +
            "如果系统开关已开，Claude 仍未授权：\n" +
            "1. 在系统设置的设备控制和数据访问、录屏与系统录音中，重新添加当前 /Applications/Claude.app 并开启。\n" +
            "2. 完全退出并重新打开 Claude；等待权限的任务需要重新发送。\n" +
            "3. 返回 Claude 再确认两项都为 Granted。\n\n" +
            "Dia、Chrome 等外部浏览器受官方只读授权限制，Computer Use 不能点击或输入。" +
            "网页操作请使用官方内置浏览器或 Claude in Chrome；内置浏览器的登录状态与 Dia 分开。\n\n" +
            "补丁安装与恢复会改变应用的签名身份，旧授权可能不再适用。系统密码或 Touch ID 由你亲自处理。"
        alert.addButton(withTitle: "知道了")
        alert.beginSheetModal(for: window)
    }

    @objc func confirmUpdate() {
        showWindow()
        let alert = NSAlert()
        alert.messageText = availableRevision > 0 ? "更新补丁到 r\(availableRevision)？" : "更新解锁补丁？"
        alert.informativeText =
            "会先校验当前补丁和官方备份，再安装新 revision。旧补丁会清理，只保留最新版和当前官方备份。" +
            quitHint(relaunch: true)
        alert.addButton(withTitle: "更新")
        alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { response in
            if response == .alertFirstButtonReturn {
                self.continueAfterConfirmedQuit(relaunch: true) {
                    self.execute("update")
                }
            }
        }
    }

    func confirmPrepareUpdate() {
        showWindow()
        let alert = NSAlert()
        alert.messageText = "为官方更新做准备？"
        alert.informativeText =
            "会恢复完整官方签名和官方文件，供官方更新器校验。\n\n" +
            "备份、配置和会话都会保留。完成后可以打开 Claude 安装更新；" +
            "更新后回到本工具点“重新检测”。" + quitHint(relaunch: false)
        alert.addButton(withTitle: "继续")
        alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { response in
            if response == .alertFirstButtonReturn {
                self.continueAfterConfirmedQuit(relaunch: false) {
                    self.execute("prepare-update")
                }
            }
        }
    }

    func quitHint(relaunch: Bool) -> String {
        guard claudeApplication() != nil else { return "" }
        return "\n\nClaude 正在运行；继续后助手会先正常退出，完成操作后"
            + (relaunch ? "自动重新打开。" : "不会自动打开。")
    }

    func showUpdateGuide() {
        showWindow()
        let alert = NSAlert()
        alert.messageText = "已准备官方更新"
        alert.informativeText =
            "现在可以打开 Claude，使用官方更新器下载并安装。\n\n" +
            "更新完成后：\n" +
            "1. 回到本工具点“重新检测”。\n" +
            "2. 如果显示“尚未适配”，等待维护者发布对应版本；本工具不会修改未知版本。\n\n" +
            "如果 Claude 提示需要重新授权辅助功能或屏幕录制，按系统正常界面操作即可。"
        alert.addButton(withTitle: "知道了")
        alert.beginSheetModal(for: window)
    }

    // MARK: - 调用内置后端

    func execute(_ command: String, backend: String = "official_unlock", feature: String? = nil) {
        guard !busy && !quittingClaude else { return }
        busy = true
        prepareForBackend(command, backend: backend)
        runBackend(command, backend: backend, feature: feature) { result in
            self.route(result, command: command, backend: backend)
        }
    }

    func prepareForBackend(_ command: String, backend: String) {
        try? FileManager.default.createDirectory(at: storageURL, withIntermediateDirectories: true)

        // 执行期间禁用所有会影响状态的控件，避免并发操作。
        toggle.isEnabled = false
        openButton.isEnabled = false
        refreshButton.isEnabled = false
        updateButton.isEnabled = false
        prepareButton.isEnabled = false
        for control in featureSwitches { control.isEnabled = false }

        progress.startAnimation(nil)
        statusLabel.textColor = .labelColor
        if backend == "all" {
            statusLabel.stringValue = "正在检测…"
        } else if backend == "native_features" {
            statusLabel.stringValue = command == "status" ? "正在检测原生配置…" : "正在保存可恢复的官方配置…"
        } else if backend == "native_cli_unlock" {
            statusLabel.stringValue = command == "status" ? "正在检测 Code 自动整理…" : "正在保存 CLI 恢复信息…"
        } else if command == "enable" {
            statusLabel.stringValue = "正在校验、备份并开启…"
        } else if command == "update" {
            statusLabel.stringValue = "正在更新解锁补丁，只保留最新版…"
        } else if command == "disable" {
            statusLabel.stringValue = "正在恢复官方原版…"
        } else if command == "prepare-update" {
            statusLabel.stringValue = "正在恢复完整官方签名，准备更新…"
        } else if command == "launch" {
            statusLabel.stringValue = "正在打开 Claude…"
        } else {
            statusLabel.stringValue = "正在检测正式版…"
        }
        updateStatusItem()
    }

    func runBackend(_ command: String, backend: String, feature: String?,
                    completion: @escaping ([String: Any]) -> Void) {
        guard let resources = Bundle.main.resourceURL else {
            completion(["error": "应用资源缺失，请重新安装。"])
            return
        }
        let helper = resources.appendingPathComponent("Backend/NativeUnlockBackend")
        guard FileManager.default.isExecutableFile(atPath: helper.path) else {
            completion(["error": "内置补丁组件缺失，请重新安装。"])
            return
        }

        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = helper
            if backend == "all" && command == "status-all" {
                process.arguments = ["status-all"]
            } else {
                process.arguments = [backend, command] + (feature.map { [$0] } ?? [])
            }
            process.currentDirectoryURL = self.storageURL
            let output = Pipe()
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice

            // 只读检测最多等 30 秒；安装、恢复和诊断不设这个期限。
            let timeoutLock = NSLock()
            var detectionTimedOut = false
            let deadline = DispatchWorkItem {
                if process.isRunning {
                    timeoutLock.lock()
                    detectionTimedOut = true
                    timeoutLock.unlock()
                    process.terminate()
                }
            }

            var result: [String: Any] = ["error": "无法运行本地补丁工具。"]
            do {
                try process.run()
                if command == "status" || command == "status-all" {
                    DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 30, execute: deadline)
                }
                try output.fileHandleForWriting.close()
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                if let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    result = value
                }
            } catch { }

            deadline.cancel()
            timeoutLock.lock()
            let timedOut = detectionTimedOut
            timeoutLock.unlock()
            if timedOut {
                result = ["error": "文件读取等待超时，已取消只读检测。可处理系统提示后重新检测，或在终端运行检测命令。"]
            }

            let finished = result
            DispatchQueue.main.async { completion(finished) }
        }
    }

    func route(_ result: [String: Any], command: String, backend: String) {
        recordOperation(command, backend: backend, result: result)
        if backend == "all" {
            completeAll(result)
        } else if backend == "native_features" {
            applyFeatures(result)
            refreshAllStatus()
        } else if backend == "native_cli_unlock" {
            applyCLI(result)
            refreshAllStatus()
        } else {
            complete(result, command: command)
        }
    }

    /// 写操作后在同一轮忙碌状态里继续合并检测，避免三次进程启动和界面闪烁。
    func refreshAllStatus() {
        guard busy else {
            execute("status-all", backend: "all")
            return
        }
        statusLabel.stringValue = "正在检测…"
        statusLabel.textColor = .labelColor
        updateStatusItem()
        runBackend("status-all", backend: "all", feature: nil) { result in
            self.completeAll(result)
        }
    }

    func finishInteraction() {
        busy = false
        progress.stopAnimation(nil)
        refreshButton.isEnabled = true
        refreshButtons()
        updateStatusItem()
    }

    func maybeLaunchClaude() {
        guard relaunchAfterAction, !busy, !quittingClaude else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            guard !self.busy && !self.quittingClaude else { return }
            self.relaunchAfterAction = false
            self.execute("launch")
        }
    }

    func handlePendingRefresh() {
        guard pendingClaudeRefresh, !busy else { return }
        pendingClaudeRefresh = false
        execute("status-all", backend: "all")
    }

    // MARK: - 处理后端结果

    func officialStatusText(_ base: String) -> String {
        if officialRunning && officialSupported && !recoveryRequired {
            return base + " · 修改时自动退出并重开 Claude"
        }
        return base
    }

    /// 所有按钮是否可用只在这里决定，避免多处状态不一致。
    func refreshButtons() {
        let interactive = !busy && !quittingClaude
        let configReady = officialSupported && !recoveryRequired && (!enabled || backupVerified)
        // Claude 正在运行时也允许操作；确认后由助手先正常退出。
        toggle.isEnabled = interactive && officialSupported && (!enabled || backupVerified)
        // 打开 Claude 不依赖适配状态：官方更新后仍应能正常使用。
        openButton.isEnabled = interactive && targetExists && !recoveryRequired
        updateButton.isEnabled = interactive && updateAvailable
        prepareButton.isEnabled = interactive && configReady
        updateButton.title = updateAvailable && availableRevision > 0
            ? "更新补丁到 r\(availableRevision)" : "更新解锁补丁"
        for (index, control) in featureSwitches.enumerated() {
            if index == 4 {
                control.isEnabled = interactive && codeMemorySupported
            } else {
                control.isEnabled = interactive && featureConfigWritable && configReady
                    && (!featureEnabled[index] || featureRestorable[index])
            }
        }
    }

    func applyOfficial(_ result: [String: Any]) {
        statusLoaded = true
        if let error = result["error"] as? String {
            lastError = error
            officialSupported = false
            updateAvailable = false
            statusLabel.stringValue = error
            statusLabel.textColor = .systemOrange
            refreshButtons()
            toggle.isEnabled = false
            return
        }

        lastError = nil
        enabled = result["enabled"] as? Bool ?? false
        let supported = result["supported"] as? Bool ?? false
        officialSupported = supported
        officialRunning = result["running"] as? Bool ?? false
        backupVerified = result["backupVerified"] as? Bool ?? false
        recoveryRequired = result["recoveryRequired"] as? Bool ?? false
        patchRevision = result["patchRevision"] as? Int ?? 0
        availableRevision = result["availableRevision"] as? Int ?? 0
        targetExists = result["targetExists"] as? Bool ?? false
        updateAvailable = enabled && supported && backupVerified && !recoveryRequired
            && (result["updateAvailable"] as? Bool ?? false)
        toggle.state = enabled ? .on : .off
        refreshButtons()

        officialMessage = result["message"] as? String ?? "检测完成"
        statusLabel.stringValue = officialStatusText(officialMessage)
        statusLabel.textColor = supported && (!enabled || backupVerified) ? .labelColor : .systemOrange
        let patchText = enabled && patchRevision > 0 ? " · 补丁 r\(patchRevision)" : ""
        let updateText = updateAvailable && availableRevision > 0 ? " · 可更新到 r\(availableRevision)" : ""
        versionLabel.stringValue = "Claude \(result["version"] as? String ?? "未知") · 原生助手 \(assistantVersion)"
            + patchText + updateText
    }

    func applyCLI(_ result: [String: Any]) {
        let index = 4
        featureEnabled[index] = result["enabled"] as? Bool ?? false
        codeMemorySupported = result["supported"] as? Bool ?? false
        featureSwitches[index].state = featureEnabled[index] ? .on : .off
        featureLabels[index].stringValue = result["error"] as? String
            ?? result["message"] as? String ?? "CLI 检测失败"
    }

    func applyFeatures(_ result: [String: Any]) {
        statusLoaded = true
        if let error = result["error"] as? String {
            featureConfigWritable = false
            featureSummary.stringValue = error + " 请重新检测。"
            if lastError == nil {
                statusLabel.stringValue = error
                statusLabel.textColor = .systemOrange
            }
            return
        }

        featureConfigWritable = result["configWritable"] as? Bool ?? false
        officialRunning = result["running"] as? Bool ?? officialRunning
        let features = result["features"] as? [[String: Any]] ?? []
        for (index, id) in featureIDs.enumerated() {
            guard let feature = features.first(where: { $0["id"] as? String == id }) else { continue }
            let on = feature["enabled"] as? Bool ?? false
            let restorable = feature["canRestore"] as? Bool ?? false
            featureEnabled[index] = on
            featureRestorable[index] = restorable
            featureSwitches[index].state = on ? .on : .off
            featureLabels[index].stringValue = on
                ? (restorable ? "已启用 · 可关闭恢复原值" : "已有官方配置 · 保持原值")
                : "未启用 · 开启后重启 Claude"
        }

        if let error = lastError {
            // 主状态检测失败时，后续配置检测不能把错误覆盖成旧状态。
            statusLabel.stringValue = error
            statusLabel.textColor = .systemOrange
        } else if !recoveryRequired && officialSupported && (!enabled || backupVerified) {
            let base = enabled
                ? (updateAvailable ? "Computer Use 已安装 · 解锁补丁待更新" : "原生补丁与官方备份均已校验")
                : "Computer Use 未开启"
            statusLabel.stringValue = officialStatusText(base)
            statusLabel.textColor = .labelColor
        } else {
            statusLabel.stringValue = officialStatusText(officialMessage)
            statusLabel.textColor = officialSupported && (!enabled || backupVerified)
                ? .labelColor : .systemOrange
        }
        featureSummary.stringValue =
            "这些开关只改官方配置，不替代执行器。系统权限、逐应用授权、拒绝、中断和动作审查都保留。" +
            "Design、Security 和账号云端功能仍需官方服务。"
        if officialRunning {
            featureSummary.stringValue += " 运行时也可直接切换，助手会先正常退出 Claude。"
        }
        if !featureConfigWritable {
            featureSummary.stringValue = result["message"] as? String ?? "配置不可修改。"
        }
    }

    func completeAll(_ result: [String: Any]) {
        lastStatusAt = Date()
        if let error = result["error"] as? String {
            applyOfficial(["error": error])
            finishInteraction()
            handlePendingRefresh()
            maybeLaunchClaude()
            return
        }
        let officialResult = result["official"] as? [String: Any]
            ?? ["error": "正式版检测未完成；请重新检测。"]
        applyOfficial(officialResult)
        applyCLI(result["cli"] as? [String: Any] ?? [:])
        applyFeatures(result["features"] as? [String: Any] ?? [:])
        finishInteraction()
        handlePendingRefresh()
        maybeLaunchClaude()
    }

    func complete(_ result: [String: Any], command: String = "status") {
        lastStatusAt = Date()
        applyOfficial(result)
        if lastError != nil {
            finishInteraction()
            handlePendingRefresh()
            maybeLaunchClaude()
            return
        }

        if (command == "enable" || command == "update") && enabled {
            showPermissionGuide()
        }
        if command == "prepare-update", result["preparedForUpdate"] as? Bool == true {
            showUpdateGuide()
        }
        refreshAllStatus()
    }

}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
