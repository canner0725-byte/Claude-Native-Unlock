import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let toggle = NSSwitch()
    let statusLabel = NSTextField(labelWithString: "正在检测正式版…")
    let versionLabel = NSTextField(labelWithString: "Claude /Applications/Claude.app")
    let openButton = NSButton(title: "打开 Claude", target: nil, action: nil)
    let refreshButton = NSButton(title: "重新检测", target: nil, action: nil)
    let updateButton = NSButton(title: "更新解锁补丁", target: nil, action: nil)
    let rollbackButton = NSButton(title: "撤回补丁更新", target: nil, action: nil)
    let permissionButton = NSButton(title: "系统授权说明", target: nil, action: nil)
    let projectButton = NSButton(title: "打开备份目录", target: nil, action: nil)
    let progress = NSProgressIndicator()
    var enabled = false
    var busy = false
    var officialSupported = false
    var recoveryRequired = false
    var updateAvailable = false
    var rollbackAvailable = false
    var codeMemorySupported = false
    var statusItem: NSStatusItem!
    let trayMenu = NSMenu()
    var trayStatus: NSMenuItem!
    var trayRefresh: NSMenuItem!
    var trayOpen: NSMenuItem!
    var trayQuit: NSMenuItem!
    let storageURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Claude Native Unlock")
    let featureIDs = ["file-analysis", "browser", "scheduled-tasks", "auto-approve", "code-auto-dream"]
    let featureTitles = ["Chat 与高级文件分析", "官方内置浏览器", "官方定时任务", "Cowork 自动审批选项", "Code 原生自动整理可用性"]
    var featureSwitches: [NSSwitch] = []
    var featureLabels: [NSTextField] = []
    var featureEnabled: [Bool] = [false, false, false, false, false]
    let featureSummary = NSTextField(wrappingLabelWithString: "正在检测原生配置…")
    let reportButton = NSButton(title: "全部功能验收说明", target: nil, action: nil)

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let menu = NSMenu()
        let item = NSMenuItem()
        menu.addItem(item)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于 Claude 原生助手", action: #selector(showAbout), keyEquivalent: "").target = self
        appMenu.addItem(withTitle: "显示主窗口", action: #selector(showWindow), keyEquivalent: "0").target = self
        appMenu.addItem(withTitle: "打开快捷操作菜单", action: #selector(showStatusMenu), keyEquivalent: "m").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出 Claude 原生助手", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu
        NSApp.mainMenu = menu
        installStatusItem()

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 620, height: 780),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Claude 原生助手"
        window.isReleasedWhenClosed = false
        window.center()
        let content = NSView()
        window.contentView = content
        let title = NSTextField(labelWithString: "Claude 官方原生功能")
        title.font = .systemFont(ofSize: 23, weight: .semibold)
        let subtitle = NSTextField(labelWithString: "为现有第三方 API 模式开启原生能力")
        subtitle.textColor = .secondaryLabelColor
        let feature = NSTextField(labelWithString: "Computer Use · Code 原生界面")
        feature.font = .systemFont(ofSize: 16, weight: .medium)
        toggle.target = self
        toggle.action = #selector(switchChanged)
        toggle.setAccessibilityLabel("Computer Use 解锁开关")
        toggle.isEnabled = false
        statusLabel.font = .systemFont(ofSize: 13)
        statusLabel.maximumNumberOfLines = 3
        statusLabel.lineBreakMode = .byWordWrapping
        versionLabel.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        versionLabel.textColor = .secondaryLabelColor
        let note = NSTextField(wrappingLabelWithString: "使用前请在 Claude 设置中确认：辅助功能、屏幕录制均为 Granted。本开关只检测补丁安装状态。\n操作开关前请退出 Claude。官方更新前需关闭本补丁，恢复完整官方签名；更新后重新核对适配。\n现有配置和会话保留。新版同时开放官方 Code 工作台；安装后可能需重新确认系统授权。")
        note.font = .systemFont(ofSize: 12)
        note.textColor = .secondaryLabelColor
        openButton.target = self
        openButton.action = #selector(openClaude)
        openButton.bezelStyle = .rounded
        refreshButton.target = self
        refreshButton.action = #selector(refresh)
        refreshButton.bezelStyle = .rounded
        updateButton.target = self
        updateButton.action = #selector(updatePatch)
        updateButton.bezelStyle = .rounded
        updateButton.isEnabled = false
        rollbackButton.target = self
        rollbackButton.action = #selector(rollbackPatch)
        rollbackButton.bezelStyle = .rounded
        rollbackButton.isEnabled = false
        permissionButton.target = self
        permissionButton.action = #selector(showPermissionGuide)
        permissionButton.bezelStyle = .rounded
        projectButton.target = self
        projectButton.action = #selector(openBackupFolder)
        projectButton.bezelStyle = .rounded
        reportButton.target = self
        reportButton.action = #selector(openFeatureReport)
        reportButton.bezelStyle = .rounded
        featureSummary.font = .systemFont(ofSize: 12)
        featureSummary.textColor = .secondaryLabelColor
        let rows = NSStackView()
        rows.orientation = .vertical
        rows.alignment = .leading
        rows.spacing = 15
        for (index, name) in featureTitles.enumerated() {
            let row = NSView()
            let label = NSTextField(labelWithString: name)
            label.font = .systemFont(ofSize: 15, weight: .medium)
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
                row.heightAnchor.constraint(equalToConstant: 46),
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
        for view in [title, subtitle, feature, toggle, statusLabel, versionLabel, note, openButton, refreshButton, updateButton, rollbackButton, permissionButton, projectButton, progress, rows, featureSummary, reportButton] {
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
            rows.topAnchor.constraint(equalTo: note.bottomAnchor, constant: 22),
            featureSummary.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            featureSummary.trailingAnchor.constraint(equalTo: statusLabel.trailingAnchor),
            featureSummary.topAnchor.constraint(equalTo: rows.bottomAnchor, constant: 18),
            reportButton.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            reportButton.topAnchor.constraint(equalTo: featureSummary.bottomAnchor, constant: 12),
            updateButton.leadingAnchor.constraint(equalTo: reportButton.trailingAnchor, constant: 12),
            updateButton.centerYAnchor.constraint(equalTo: reportButton.centerYAnchor),
            rollbackButton.leadingAnchor.constraint(equalTo: updateButton.trailingAnchor, constant: 12),
            rollbackButton.centerYAnchor.constraint(equalTo: reportButton.centerYAnchor),
            openButton.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            openButton.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -22),
            refreshButton.leadingAnchor.constraint(equalTo: openButton.trailingAnchor, constant: 12),
            refreshButton.centerYAnchor.constraint(equalTo: openButton.centerYAnchor),
            permissionButton.leadingAnchor.constraint(equalTo: refreshButton.trailingAnchor, constant: 12),
            permissionButton.centerYAnchor.constraint(equalTo: openButton.centerYAnchor),
            projectButton.leadingAnchor.constraint(equalTo: permissionButton.trailingAnchor, constant: 12),
            projectButton.centerYAnchor.constraint(equalTo: openButton.centerYAnchor),
            progress.trailingAnchor.constraint(equalTo: toggle.trailingAnchor),
            progress.centerYAnchor.constraint(equalTo: openButton.centerYAnchor)
        ])
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        execute("status")
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationWillTerminate(_ notification: Notification) {
        NSStatusBar.system.removeStatusItem(statusItem)
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        return busy ? .terminateCancel : .terminateNow
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow()
        if !busy { execute("status") }
        return true
    }

    @objc func refresh() { execute("status") }
    @objc func openClaude() { execute("launch") }
    @objc func updatePatch() { execute("update") }
    @objc func rollbackPatch() { execute("rollback-update") }
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
        trayMenu.addItem(withTitle: "打开备份目录", action: #selector(openBackupFolder), keyEquivalent: "").target = self
        trayMenu.addItem(withTitle: "使用说明", action: #selector(openFeatureReport), keyEquivalent: "").target = self
        trayMenu.addItem(withTitle: "关于", action: #selector(showAbout), keyEquivalent: "").target = self
        trayMenu.addItem(.separator())
        trayQuit = trayMenu.addItem(withTitle: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        statusItem.menu = trayMenu
    }
    func updateStatusItem() {
        let text = busy ? "正在处理…" : officialSupported ? (enabled ? "原生补丁已安装" : "原生补丁未开启") : "当前安装需检查"
        trayStatus.title = text
        statusItem.button?.toolTip = "Claude 原生助手 · " + text
        trayRefresh.isEnabled = !busy
        trayOpen.isEnabled = !busy && officialSupported && !recoveryRequired
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
    @objc func openBackupFolder() {
        do {
            try FileManager.default.createDirectory(at: storageURL, withIntermediateDirectories: true)
            NSWorkspace.shared.open(storageURL)
        } catch {
            statusLabel.stringValue = "无法打开备份目录，请检查目录权限。"
        }
    }
    @objc func showAbout() {
        showWindow()
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Claude 原生助手",
            .applicationVersion: "0.3.0",
            .version: "12",
            .credits: NSAttributedString(string: "独立开源项目，与 Anthropic 无隶属关系。\n仅解除已适配版本的原生功能可用性条件。")
        ])
    }
    @objc func showPermissionGuide() {
        showWindow()
        let alert = NSAlert()
        alert.messageText = "确认 Claude 的系统授权"
        alert.informativeText = "在 Claude 设置 → This computer → System 中，开启 Enable computer use，并确认 Accessibility 和 Screen recording 都显示 Granted。\n\n如果系统开关已开，Claude 仍未授权：\n1. 在系统设置的设备控制和数据访问、录屏与系统录音中，重新添加当前 /Applications/Claude.app 并开启。\n2. 完全退出并重新打开 Claude；等待权限的任务需要重新发送。\n3. 返回 Claude 再确认两项都为 Granted。\n\nDia、Chrome 等外部浏览器受官方只读授权限制，Computer Use 不能点击或输入。网页操作请使用官方内置浏览器或 Claude in Chrome；内置浏览器的登录状态与 Dia 分开。开通系统权限不会改变这个应用授权档位。\n\n补丁安装与恢复会改变应用的签名身份，旧授权可能不再适用。测试副本的授权不属于正式 Claude。系统密码或 Touch ID 由你亲自处理。"
        alert.addButton(withTitle: "知道了")
        alert.beginSheetModal(for: window)
    }
    @objc func switchChanged() {
        let command = toggle.state == .on ? "enable" : "disable"
        toggle.state = enabled ? .on : .off
        execute(command)
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
        if featureIDs[index] == "code-auto-dream" {
            execute(enabling ? "enable" : "restore", backend: "native_cli_unlock")
        } else {
            execute(enabling ? "enable" : "restore", backend: "native_features", feature: featureIDs[index])
        }
    }

    func execute(_ command: String, backend: String = "official_unlock", feature: String? = nil) {
        guard !busy else { return }
        busy = true
        try? FileManager.default.createDirectory(at: storageURL, withIntermediateDirectories: true)
        toggle.isEnabled = false
        openButton.isEnabled = false
        refreshButton.isEnabled = false
        updateButton.isEnabled = false
        rollbackButton.isEnabled = false
        projectButton.isEnabled = false
        for control in featureSwitches { control.isEnabled = false }
        progress.startAnimation(nil)
        statusLabel.stringValue = backend == "native_features"
            ? (command == "status" ? "正在检测原生配置…" : "正在保存可恢复的官方配置…")
            : (command == "enable" ? "正在校验、备份并开启…" : command == "update" ? "正在更新解锁补丁，保留旧补丁与官方备份…" : command == "rollback-update" ? "正在撤回更新，保留修复版本…" : command == "disable" ? "正在恢复官方原版…" : "正在检测正式版…")
        updateStatusItem()
        guard let resources = Bundle.main.resourceURL else {
            complete(["error": "应用资源缺失，请重新安装。"])
            return
        }
        let helper = resources.appendingPathComponent("Backend/NativeUnlockBackend")
        guard FileManager.default.isExecutableFile(atPath: helper.path) else {
            complete(["error": "内置补丁组件缺失，请重新安装。"])
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = helper
            process.arguments = [backend, command] + (feature.map { [$0] } ?? [])
            process.currentDirectoryURL = self.storageURL
            let output = Pipe()
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            let timeoutLock = NSLock()
            var detectionTimedOut = false
            let deadline = DispatchWorkItem {
                // A blocked read-only detector must not leave the UI busy forever.
                // Installation and recovery commands are never terminated by this deadline.
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
                if command == "status" {
                    DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 30, execute: deadline)
                }
                try output.fileHandleForWriting.close()
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                if let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] { result = value }
            } catch { }
            deadline.cancel()
            timeoutLock.lock()
            let timedOut = detectionTimedOut
            timeoutLock.unlock()
            if timedOut {
                result = ["error": "文件读取等待超时，已取消只读检测。可处理系统提示后重新检测，或在终端运行检测命令。"]
            }
            let finished = result
            DispatchQueue.main.async {
                if backend == "native_features" { self.completeFeatures(finished) }
                else if backend == "native_cli_unlock" { self.completeCodeMemory(finished, command: command) }
                else { self.complete(finished, command: command) }
            }
        }
    }

    func complete(_ result: [String: Any], command: String = "status") {
        busy = false
        defer { updateStatusItem() }
        progress.stopAnimation(nil)
        refreshButton.isEnabled = true
        projectButton.isEnabled = true
        if let error = result["error"] as? String {
            officialSupported = false
            statusLabel.stringValue = error
            statusLabel.textColor = .systemOrange
            // Keep the previous state visible, and require a fresh check after a failed operation.
            toggle.isEnabled = false
            return
        }
        enabled = result["enabled"] as? Bool ?? false
        let supported = result["supported"] as? Bool ?? false
        officialSupported = supported
        recoveryRequired = result["recoveryRequired"] as? Bool ?? false
        updateAvailable = enabled && supported && !recoveryRequired && !(result["availabilityOnly"] as? Bool ?? false)
        rollbackAvailable = supported && !recoveryRequired && (result["rollbackAvailable"] as? Bool ?? false)
        toggle.state = enabled ? .on : .off
        toggle.isEnabled = supported
        openButton.isEnabled = supported && !recoveryRequired
        statusLabel.stringValue = result["message"] as? String ?? "检测完成"
        statusLabel.textColor = supported ? .labelColor : .systemOrange
        versionLabel.stringValue = "Claude \(result["version"] as? String ?? "未知") · 原生助手 0.3.0"
        if (command == "enable" || command == "update") && enabled { showPermissionGuide() }
        execute("status", backend: "native_cli_unlock")
    }
    func completeCodeMemory(_ result: [String: Any], command: String) {
        busy = false
        defer { updateStatusItem() }
        let index = 4
        featureEnabled[index] = result["enabled"] as? Bool ?? false
        codeMemorySupported = result["supported"] as? Bool ?? false
        featureSwitches[index].state = featureEnabled[index] ? .on : .off
        featureLabels[index].stringValue = result["error"] as? String ?? result["message"] as? String ?? "CLI 检测失败"
        if command == "status" { execute("status", backend: "native_features") }
        else { execute("status") }
    }
    func completeFeatures(_ result: [String: Any]) {
        busy = false
        defer { updateStatusItem() }
        progress.stopAnimation(nil)
        refreshButton.isEnabled = true
        projectButton.isEnabled = true
        if let error = result["error"] as? String {
            featureSummary.stringValue = error + " 请重新检测。"
            return
        }
        let writable = result["configWritable"] as? Bool ?? false
        let supported = result["supported"] as? Bool ?? false
        let features = result["features"] as? [[String: Any]] ?? []
        for (index, id) in featureIDs.enumerated() {
            guard let feature = features.first(where: { $0["id"] as? String == id }) else { continue }
            let on = feature["enabled"] as? Bool ?? false
            let restorable = feature["canRestore"] as? Bool ?? false
            featureEnabled[index] = on
            featureSwitches[index].state = on ? .on : .off
            featureSwitches[index].isEnabled = writable && supported && (!on || restorable)
            featureLabels[index].stringValue = on
                ? (restorable ? "配置已启用 · 关闭恢复原值" : "已有官方配置 · 保持原值")
                : "未启用 · 开启后重启 Claude"
        }
        featureSwitches[4].isEnabled = codeMemorySupported
        toggle.isEnabled = officialSupported
        openButton.isEnabled = officialSupported && !recoveryRequired
        updateButton.isEnabled = updateAvailable
        rollbackButton.isEnabled = rollbackAvailable
        if !recoveryRequired && officialSupported {
            statusLabel.stringValue = enabled ? (updateAvailable ? "Computer Use 已安装 · 解锁补丁待更新" : "原生补丁已安装 · 文件校验通过") : "Computer Use 未开启"
        }
        featureSummary.stringValue = "Code 自动整理只解除可用性限制，原生用户开关和运行门槛保持。Chat 历史检索、账号夜间摘要、Design、Security 仍依赖官方服务；联网搜索使用现有 API。保留官方授权与动作审查。"
        if !writable { featureSummary.stringValue = result["message"] as? String ?? "配置不可修改。" }
    }

}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
