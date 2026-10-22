import AppKit

// 一个窗口 + 菜单栏入口的小工具。
// 所有真正的补丁操作都在内置 Python 后端里完成；这里只负责显示状态、
// 收集用户意图，并把后端返回的 JSON 转成中文提示。
//
// 界面只保留“现在能不能用”和“点哪里”：主开关一张卡片、原生配置一列、
// 底部两个常用按钮。更新补丁只在真的可更新时淡入；准备官方更新、系统
// 授权说明、使用说明等低频动作用菜单栏菜单承载，窗口里不再堆文字。
class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var contentStack: NSStackView!
    /// Stack 底边与内容视图的约束；量高度时要临时断开，否则它把高度钉在窗口上。
    var contentBottomPin: NSLayoutConstraint!

    // 主开关：安装 / 恢复正式版可用性补丁。
    let toggle = NSSwitch()
    let statusLabel = NSTextField(wrappingLabelWithString: "正在检测…")
    let metaLabel = NSTextField(labelWithString: "正在检测…")

    // 底部操作。
    let openButton = NSButton(title: "打开 Claude", target: nil, action: nil)
    let refreshButton = NSButton(title: "重新检测", target: nil, action: nil)

    // 只在可更新时出现。
    let updateButton = NSButton(title: "更新补丁", target: nil, action: nil)
    // 打完补丁后出现：一键跳到系统设置的隐私页面。
    let permissionButton = NSButton(title: "去授权", target: nil, action: nil)

    let progress = NSProgressIndicator()

    // 后端最近一次检测结果。
    var enabled = false
    var busy = false
    var officialRunning = false
    var officialSupported = false
    var backupVerified = false
    var officialMessage = "正在检测…"
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
    var featureRestorable: [Bool] = Array(repeating: false, count: 8)

    // 一键退出 Claude 并应用修改。
    var quittingClaude = false
    var quitTimer: Timer?
    var quitFinalizeDeadline = Date.distantPast
    var pendingAfterQuit: (() -> Void)?
    var relaunchAfterAction = false
    var lastStatusAt = Date.distantPast
    // 刚开启/更新补丁后，短时间内在窗口里提示一次系统权限；过期自动消失。
    var enabledHintUntil = Date.distantPast
    var justEnabledHint: Bool { Date() < enabledHintUntil }
    /// 刚改过配置开关：Claude 重启后的第一份自检出来之前，给一句“重启后更新”。
    var featureHintUntil = Date.distantPast
    var justChangedFeature: Bool { Date() < featureHintUntil }
    var permissionReGrantLikely = false

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
        .appendingPathComponent("Library/Application Support/Claude 原生助手")

    let featureIDs = ["file-analysis", "browser", "scheduled-tasks", "auto-approve",
                      "desktop-extensions", "tool-search", "webfetch-preflight",
                      "code-auto-dream"]
    let featureTitles = ["Chat 与高级文件分析", "官方内置浏览器", "官方定时任务",
                         "Cowork 自动审批选项", "插件里的桌面扩展", "MCP 工具按需加载",
                         "WebFetch 免域名预检", "Code 自动整理"]
    // 说明只在悬停提示里出现，窗口里保持一行一个开关。
    let featureDescriptions = [
        "Chat 使用标准工作台，启用官方高级文件分析。",
        "启用 Claude 官方内置浏览器（不是 Dia / Chrome）。",
        "允许官方定时任务；是否执行仍由 Claude 决定。",
        "显示 Cowork 官方自动审批选项；动作审查保留。",
        "允许插件里打包的桌面扩展（.mcpb）加载；第三方部署没有组织策略后端时不能直接安装 .mcpb，Code 插件也不接受。签名检查仍按官方策略。",
        "MCP 工具改为按需加载，不再全部塞进上下文；网关不支持时走官方旧路径。",
        "跳过 Code 里 WebFetch 对 api.anthropic.com 的域名预检；抓取仍走官方 WebFetch。",
        "只解除 Code 自动整理的可用性限制；用户开关保持。"
    ]
    /// Code 自动整理在列表里的位置；它走 CLI 后端，不是 3P 配置项。
    var codeAutoDreamIndex: Int { featureIDs.firstIndex(of: "code-auto-dream") ?? featureIDs.count - 1 }
    var featureSwitches: [NSSwitch] = []
    var featureLabels: [NSTextField] = []
    var featureHints: [NSTextField] = []
    var featureRows: [NSView] = []
    var featureEnabled: [Bool] = Array(repeating: false, count: 8)
    /// 当前 Claude 版本是否还有这个配置键；没有就禁用而不是装作能点。
    var featureAvailable: [Bool] = Array(repeating: true, count: 8)
    // 只在出错或尚未适配时显示，平时留空，避免每行都挂一句说明。
    let featureSummary = NSTextField(wrappingLabelWithString: "")

    // 版本号从 Info.plist 读取，避免以后升级时忘记改界面文案。
    var assistantVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "未知"
    }
    var assistantBuild: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "未知"
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        // Dock 图标直接从包内读取：系统图标缓存有时仍显示旧图标。
        if let iconURL = Bundle.main.url(forResource: "AppIcon", withExtension: "icns"),
           let icon = NSImage(contentsOf: iconURL) {
            NSApp.applicationIconImage = icon
        }

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
        appMenu.addItem(withTitle: "更新解锁补丁", action: #selector(confirmUpdate), keyEquivalent: "u").target = self
        appMenu.addItem(withTitle: "为官方更新做准备", action: #selector(prepareUpdate), keyEquivalent: "p").target = self
        appMenu.addItem(withTitle: "系统授权说明", action: #selector(showPermissionGuide), keyEquivalent: "").target = self
        appMenu.addItem(withTitle: "使用说明", action: #selector(openFeatureReport), keyEquivalent: "").target = self
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
        // 内容自己决定高度，所以先给一个够用的初始尺寸，随后按内容收敛。
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 520),
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

        // 标题栏透明、标题在内容里，接近 macOS 自带设置窗口的观感。
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.styleMask.insert(.fullSizeContentView)

        // 全部内容放在一个纵向 Stack 里：改文案、隐藏元素都能平滑过渡，
        // 窗口高度自己跟着内容走，不再靠一堆固定约束硬撑。
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 0
        stack.edgeInsets = NSEdgeInsets(top: 34, left: 22, bottom: 20, right: 22)
        contentStack = stack

        let title = NSTextField(labelWithString: "Claude 原生助手")
        title.font = .systemFont(ofSize: 20, weight: .semibold)
        metaLabel.font = .systemFont(ofSize: 11)
        metaLabel.textColor = .tertiaryLabelColor

        let header = NSStackView(views: [title, metaLabel])
        header.orientation = .vertical
        header.alignment = .leading
        header.spacing = 3

        // 主开关卡片：一行标题 + 一行状态。
        let mainTitle = NSTextField(labelWithString: "Computer Use")
        mainTitle.font = .systemFont(ofSize: 15, weight: .medium)
        mainTitle.toolTip = "安装或恢复 Computer Use、Code 工作台等原生可用性补丁。"
        statusLabel.font = .systemFont(ofSize: 12)
        statusLabel.textColor = .secondaryLabelColor
        statusLabel.maximumNumberOfLines = 2
        statusLabel.lineBreakMode = .byWordWrapping

        toggle.target = self
        toggle.action = #selector(switchChanged)
        toggle.setAccessibilityLabel("Computer Use 解锁开关")
        toggle.isEnabled = false

        let mainText = NSStackView(views: [mainTitle, statusLabel])
        mainText.orientation = .vertical
        mainText.alignment = .leading
        mainText.spacing = 4
        mainText.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let mainRow = NSStackView(views: [mainText, toggle])
        mainRow.orientation = .horizontal
        mainRow.alignment = .centerY
        mainRow.spacing = 16
        mainRow.distribution = .fill

        // 原生配置卡片：一行一个开关，说明放在悬停提示里。
        let rows = NSStackView()
        rows.orientation = .vertical
        rows.alignment = .leading
        rows.spacing = 0
        for (index, name) in featureTitles.enumerated() {
            let label = NSTextField(labelWithString: name)
            label.font = .systemFont(ofSize: 14)
            label.toolTip = featureDescriptions[index]
            let detail = NSTextField(labelWithString: "")
            detail.font = .systemFont(ofSize: 11)
            detail.textColor = .systemOrange
            detail.lineBreakMode = .byTruncatingTail
            detail.isHidden = true

            // 只用于“装没装插件、有没有 .mcpb”这类可核对的事实，不是错误。
            let hint = NSTextField(labelWithString: "")
            hint.font = .systemFont(ofSize: 11)
            hint.textColor = .secondaryLabelColor
            hint.lineBreakMode = .byTruncatingTail
            hint.isHidden = true
            // 说明文字不能把整行撑宽，否则右对齐的开关会被挤到中间。
            detail.setContentCompressionResistancePriority(.init(1), for: .horizontal)
            hint.setContentCompressionResistancePriority(.init(1), for: .horizontal)

            let control = NSSwitch()
            control.tag = index
            control.target = self
            control.action = #selector(featureChanged(_:))
            control.setAccessibilityLabel(name + " 配置开关")
            control.isEnabled = false
            // 开关始终贴右；说明文字变长时只压缩文字，不移动开关。
            control.setContentHuggingPriority(.required, for: .horizontal)
            control.setContentCompressionResistancePriority(.required, for: .horizontal)
            featureSwitches.append(control)
            featureLabels.append(detail)
            featureHints.append(hint)

            let text = NSStackView(views: [label, detail, hint])
            text.orientation = .vertical
            text.alignment = .leading
            text.spacing = 2
            text.setContentHuggingPriority(.defaultLow, for: .horizontal)
            text.setContentCompressionResistancePriority(.init(1), for: .horizontal)

            // 弹簧视图吃掉多余宽度，保证开关始终贴右，不被说明文字推走。
            let spacer = NSView()
            spacer.setContentHuggingPriority(.init(1), for: .horizontal)
            spacer.setContentCompressionResistancePriority(.init(1), for: .horizontal)

            let row = NSStackView(views: [text, spacer, control])
            row.orientation = .horizontal
            row.alignment = .centerY
            row.spacing = 16
            row.edgeInsets = NSEdgeInsets(top: 9, left: 14, bottom: 9, right: 14)
            if index > 0 {
                let hairline = NSBox()
                hairline.boxType = .separator
                rows.addArrangedSubview(hairline)
                hairline.widthAnchor.constraint(equalTo: rows.widthAnchor).isActive = true
            }
            rows.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: rows.widthAnchor).isActive = true
            featureRows.append(row)
        }

        let section = NSTextField(labelWithString: "原生配置")
        section.font = .systemFont(ofSize: 11, weight: .semibold)
        section.textColor = .secondaryLabelColor

        featureSummary.font = .systemFont(ofSize: 11)
        featureSummary.textColor = .systemOrange
        featureSummary.isHidden = true

        for button in [openButton, refreshButton, updateButton, permissionButton] {
            button.bezelStyle = .rounded
            button.target = self
        }
        openButton.action = #selector(openClaude)
        refreshButton.action = #selector(refresh)
        updateButton.action = #selector(confirmUpdate)
        updateButton.toolTip = "把补丁更新到最新 revision；只保留最新版和当前官方备份。"
        permissionButton.action = #selector(openPrivacySettings)
        permissionButton.toolTip = "打开系统设置的隐私页面，给 Claude 勾选辅助功能与屏幕录制。"
        permissionButton.bezelStyle = .rounded
        permissionButton.isHidden = true
        updateButton.isEnabled = false
        // 可更新时再淡入，平时不占视觉位置。
        updateButton.alphaValue = 0
        updateButton.isHidden = true

        progress.style = .spinning
        progress.controlSize = .small
        progress.isDisplayedWhenStopped = false

        let footerSpacer = NSView()
        footerSpacer.setContentHuggingPriority(.defaultLow, for: .horizontal)
        let footer = NSStackView(views: [openButton, refreshButton, footerSpacer,
                                         permissionButton, updateButton, progress])
        footer.orientation = .horizontal
        footer.alignment = .centerY
        footer.spacing = 10

        let mainCard = makeCard(mainRow)
        let rowsCard = makeCard(rows)

        for view in [header, mainCard, section, rowsCard, featureSummary, footer] {
            view.translatesAutoresizingMaskIntoConstraints = false
            stack.addArrangedSubview(view)
            view.widthAnchor.constraint(equalTo: stack.widthAnchor,
                                        constant: -44).isActive = true
        }
        stack.setCustomSpacing(18, after: header)
        stack.setCustomSpacing(22, after: mainCard)
        stack.setCustomSpacing(8, after: section)
        stack.setCustomSpacing(10, after: rowsCard)
        stack.setCustomSpacing(16, after: featureSummary)

        stack.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            stack.topAnchor.constraint(equalTo: content.topAnchor)
        ])
        contentBottomPin = stack.bottomAnchor.constraint(equalTo: content.bottomAnchor)
        contentBottomPin.isActive = true

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        resizeToFit(animated: false)
        execute("status-all", backend: "all")
    }

    /// 窗口高度跟着内容走；内容显隐后调用，顶部保持不动，过渡平滑。
    func resizeToFit(animated: Bool = true) {
        guard window.isVisible, let content = window.contentView else { return }
        content.layoutSubtreeIfNeeded()
        // 底边约束会把 Stack 高度钉死在窗口上，量出来的永远是当前高度。
        // 先断开再量，才能拿到内容真正需要的高度。
        contentBottomPin.isActive = false
        let height = ceil(contentStack.fittingSize.height)
        contentBottomPin.isActive = true
        guard height > 100 else { return }
        let current = window.frame.height
        guard abs(height - current) > 0.5 else { return }
        var frame = window.frame
        frame.origin.y += current - height
        frame.size.height = height
        window.setFrame(frame, display: true, animate: animated)
    }

    /// 圆角卡片：窗口背景上做一层浅底，和系统设置的分组观感一致。
    ///
    /// 不用 NSBox：它的 contentView 不参与 Auto Layout，卡片量出来的高度是 0，
    /// 内容会从卡片里溢出来压到上面的标题上。自己画一层，尺寸由内容决定。
    func makeCard(_ inner: NSView) -> CardView {
        let card = CardView()
        inner.translatesAutoresizingMaskIntoConstraints = false
        card.addSubview(inner)
        NSLayoutConstraint.activate([
            inner.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 14),
            inner.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -14),
            inner.topAnchor.constraint(equalTo: card.topAnchor, constant: 12),
            inner.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -12)
        ])
        return card
    }

    /// 内容变化时统一走这里：淡入淡出加轻微位移，避免界面“跳一下”。
    func animate(_ duration: TimeInterval = 0.18, _ changes: () -> Void) {
        NSAnimationContext.runAnimationGroup { context in
            context.duration = duration
            context.allowsImplicitAnimation = true
            changes()
        }
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
        featureHintUntil = Date().addingTimeInterval(120)
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
        statusItem.button?.image = menuBarIcon(.unlocked)
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
        trayMenu.addItem(withTitle: "去系统设置授权", action: #selector(openPrivacySettings), keyEquivalent: "").target = self
        trayMenu.addItem(withTitle: "系统授权说明", action: #selector(showPermissionGuide), keyEquivalent: "").target = self
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

        let state: MenuBarIconState
        if blocked {
            state = .busy
        } else if !statusLoaded {
            state = .unlocked
        } else if recoveryRequired || (enabled && !backupVerified) || !officialSupported {
            state = .alert
        } else {
            state = enabled ? .unlocked : .locked
        }
        statusItem.button?.image = menuBarIcon(state)
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

    /// 一键跳到系统设置的隐私页面。macOS 只允许系统设置写授权，
    /// 所以这里只能把用户送到对应页面，不能代替勾选。
    @objc func openPrivacySettings() {
        let urls = [
            "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension",
            "x-apple.systempreferences:com.apple.preference.security",
        ]
        for candidate in urls {
            if let url = URL(string: candidate), NSWorkspace.shared.open(url) {
                recordOperation("open-privacy-settings", backend: "ui",
                                result: ["message": candidate])
                return
            }
        }
        showPermissionGuide()
    }

    /// 权限是否需要重新授权：打完补丁后签名身份变了，系统就不再认旧授权。
    func updatePermissionButton() {
        let needed = enabled && !busy && !quittingClaude && permissionReGrantLikely
        guard permissionButton.isHidden == !needed else { return }
        if needed {
            permissionButton.isHidden = false
            permissionButton.alphaValue = 0
            animate { self.permissionButton.animator().alphaValue = 1 }
        } else {
            animate { self.permissionButton.animator().alphaValue = 0 }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                if self.permissionButton.alphaValue < 0.01 {
                    self.permissionButton.isHidden = true
                }
            }
        }
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
        for control in featureSwitches { control.isEnabled = false }

        progress.startAnimation(nil)
        statusLabel.textColor = .secondaryLabelColor
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

    /// 状态文案统一从这里写，顺带做一次淡入，避免文字突然跳变。
    func setStatus(_ text: String, color: NSColor = .secondaryLabelColor) {
        guard text != statusLabel.stringValue else {
            statusLabel.textColor = color
            return
        }
        statusLabel.stringValue = text
        statusLabel.textColor = color
        statusLabel.alphaValue = 0
        animate(0.22) { self.statusLabel.animator().alphaValue = 1 }
    }

    /// 更新按钮只在真的能更新时出现，出现和消失都走动画。
    func setUpdateVisible(_ visible: Bool) {
        guard updateButton.isHidden == visible else {
            if !visible { updateButton.isEnabled = true }
            return
        }
        if visible {
            updateButton.isHidden = false
            updateButton.alphaValue = 0
            animate { self.updateButton.animator().alphaValue = 1 }
        } else {
            animate { self.updateButton.animator().alphaValue = 0 }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                if self.updateButton.alphaValue < 0.01 {
                    self.updateButton.isHidden = true
                    self.window.contentView?.layoutSubtreeIfNeeded()
                }
            }
        }
    }

    /// 所有控件是否可用只在这里决定，避免多处状态不一致。
    func refreshButtons() {
        let interactive = !busy && !quittingClaude
        let configReady = officialSupported && !recoveryRequired && (!enabled || backupVerified)
        // Claude 正在运行时也允许操作；确认后由助手先正常退出。
        toggle.isEnabled = interactive && officialSupported && (!enabled || backupVerified)
        // 打开 Claude 不依赖适配状态：官方更新后仍应能正常使用。
        openButton.isEnabled = interactive && targetExists && !recoveryRequired
        updateButton.isEnabled = interactive && updateAvailable
        updateButton.title = availableRevision > 0 ? "更新到 r\(availableRevision)" : "更新补丁"
        setUpdateVisible(interactive && updateAvailable)
        for (index, control) in featureSwitches.enumerated() {
            if featureIDs[index] == "code-auto-dream" {
                control.isEnabled = interactive && codeMemorySupported
            } else {
                control.isEnabled = interactive && featureConfigWritable && configReady
                    && featureAvailable[index]
                    && (!featureEnabled[index] || featureRestorable[index])
            }
        }
        updatePermissionButton()
        // 按钮显隐会改变高度，等动画结束后让窗口重新贴合内容。
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.24) { self.resizeToFit() }
    }

    func applyOfficial(_ result: [String: Any]) {
        statusLoaded = true
        if let error = result["error"] as? String {
            lastError = error
            officialSupported = false
            updateAvailable = false
            setStatus(error, color: .systemOrange)
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
        permissionReGrantLikely = result["permissionReGrantLikely"] as? Bool ?? false
        toggle.state = enabled ? .on : .off
        refreshButtons()

        officialMessage = result["message"] as? String ?? "检测完成"
        statusLabel.toolTip = officialMessage
        if recoveryRequired {
            setStatus("安装中断 · 关闭开关即可恢复官方备份", color: .systemOrange)
        } else if !supported {
            setStatus(targetExists ? "此版本尚未适配 · 不会修改 Claude" : "未找到 Claude",
                      color: .systemOrange)
        } else if !enabled {
            setStatus("未开启", color: .secondaryLabelColor)
        } else if !backupVerified {
            setStatus("已解锁 · 官方备份异常，恢复已停用", color: .systemOrange)
        } else if updateAvailable {
            setStatus("已解锁 · 有新版本可更新", color: .systemBlue)
        } else {
            setStatus("已解锁 · 文件校验通过", color: .secondaryLabelColor)
        }
        let patch = enabled && patchRevision > 0 ? " · 补丁 r\(patchRevision)" : ""
        metaLabel.stringValue = "Claude \(result["version"] as? String ?? "未知")" + patch
    }

    func applyCLI(_ result: [String: Any]) {
        let index = codeAutoDreamIndex
        featureEnabled[index] = result["enabled"] as? Bool ?? false
        codeMemorySupported = result["supported"] as? Bool ?? false
        featureSwitches[index].state = featureEnabled[index] ? .on : .off
        setFeatureDetail(index, text: result["error"] as? String ?? "")
    }

    /// 刚改过开关时，用正在运行的 Code 会话核对桌面端是否真的把设置传了下去。
    /// 只在“还没带上”时给一行提示；确认带上后保持安静。
    func codeSessionDetail(id: String, sessions: [String: Any]) -> String {
        let count = sessions["count"] as? Int ?? 0
        if id == "tool-search" {
            if count == 0 { return "重启后新开的 Code 会话会带上这个设置。" }
            if sessions["toolSearch"] as? Bool != true {
                return "运行中的 Code 会话早于这次修改；新会话才会带上。"
            }
        } else if id == "webfetch-preflight" {
            if count == 0 { return "重启后新开的 Code 会话会带上这个设置。" }
            if sessions["preflight"] as? Bool != true {
                return "运行中的 Code 会话早于这次修改；新会话才会带上。"
            }
        }
        return ""
    }

    /// 每行的补充说明只在出错时出现；正常状态保持一行一个开关。
    func setFeatureDetail(_ index: Int, text: String) {
        let label = featureLabels[index]
        label.stringValue = text
        // 当前可见状态已经和文字是否为空一致时不用再动。
        if label.isHidden == text.isEmpty { return }
        animate { label.animator().isHidden = text.isEmpty }
    }

    func setFeatureHint(_ index: Int, text: String) {
        let label = featureHints[index]
        label.stringValue = text
        if label.isHidden == text.isEmpty { return }
        animate { label.animator().isHidden = text.isEmpty }
    }

    /// 桌面扩展只能随插件安装；数出插件和 .mcpb 数量，比“开关已打开”更能说明现状。
    func desktopExtensionHint(_ result: [String: Any]) -> String {
        guard let counts = result["desktopExtensions"] as? [String: Any] else { return "" }
        let plugins = counts["plugins"] as? Int ?? 0
        let bundles = counts["bundles"] as? Int ?? 0
        if bundles > 0 {
            return "\(plugins) 个插件 · \(bundles) 个 .mcpb 扩展"
        }
        if plugins > 0 {
            return "\(plugins) 个插件 · 没有 .mcpb 扩展"
        }
        return "未检测到 Cowork 插件"
    }

    func applyFeatures(_ result: [String: Any]) {
        statusLoaded = true
        if let error = result["error"] as? String {
            featureConfigWritable = false
            if let index = featureIDs.firstIndex(of: "desktop-extensions") {
                setFeatureHint(index, text: "")
            }
            setSummary(error + " 请重新检测。")
            if lastError == nil { setStatus(error, color: .systemOrange) }
            return
        }

        featureConfigWritable = result["configWritable"] as? Bool ?? false
        officialRunning = result["running"] as? Bool ?? officialRunning
        let features = result["features"] as? [[String: Any]] ?? []
        let sessions = result["codeSessions"] as? [String: Any]
        for (index, id) in featureIDs.enumerated() {
            guard let feature = features.first(where: { $0["id"] as? String == id }) else { continue }
            let on = feature["enabled"] as? Bool ?? false
            let available = feature["available"] as? Bool ?? true
            featureEnabled[index] = on
            featureAvailable[index] = available
            featureRestorable[index] = feature["canRestore"] as? Bool ?? false
            featureSwitches[index].state = on ? .on : .off
            var detail = available ? "" : (feature["message"] as? String ?? "此版本没有这个设置")
            if detail.isEmpty, on, justChangedFeature, let sessions = sessions {
                detail = codeSessionDetail(id: id, sessions: sessions)
            }
            setFeatureDetail(index, text: detail)
        }

        if let index = featureIDs.firstIndex(of: "desktop-extensions") {
            let hint = featureEnabled[index] && featureAvailable[index]
                ? desktopExtensionHint(result) : ""
            setFeatureHint(index, text: hint)
            if !hint.isEmpty {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.24) { self.resizeToFit() }
            }
        }

        let health = result["configHealth"] as? [String: Any]
        if !featureConfigWritable {
            setSummary(result["message"] as? String ?? "配置不可修改。")
        } else if let health = health, (health["healthy"] as? Bool) == false {
            // Claude 自己的配置自检不健康；开关已经写入，但不能说“没问题”。
            setSummary(result["message"] as? String ?? "Claude 的配置自检未通过；请检查连接或配置。")
        } else if let health = health, (health["stale"] as? Bool) == true, justChangedFeature {
            setSummary(result["message"] as? String ?? "开关已写入；Claude 下次启动后配置自检会更新。")
        } else if !recoveryRequired && officialSupported && (!enabled || backupVerified) {
            // 正常路径不再重复主状态，只在需要时提示一次权限。
            setSummary(justEnabledHint ? "如 Computer Use 无响应，请在系统设置里给 Claude 开启辅助功能与屏幕录制。"
                                       : "")
        } else {
            setSummary("")
        }
    }

    func setSummary(_ text: String) {
        guard featureSummary.stringValue != text || featureSummary.isHidden != text.isEmpty else { return }
        featureSummary.stringValue = text
        animate { self.featureSummary.animator().isHidden = text.isEmpty }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.24) { self.resizeToFit() }
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
            // 只在窗口里提示一行；完整步骤留在菜单栏的“系统授权说明”。
            enabledHintUntil = Date().addingTimeInterval(90)
            if permissionReGrantLikely { openPrivacySettings() }
        }
        if command == "prepare-update", result["preparedForUpdate"] as? Bool == true {
            showUpdateGuide()
        }
        refreshAllStatus()
    }

}

// MARK: - 菜单栏图标

/// 放射星芒：rays 条从中心向外张开的圆头花瓣。和 App 图标共用同一套形状。
func starburstPath(center: NSPoint, radius: CGFloat, rays: Int,
                   innerRatio: CGFloat = 0.20,
                   innerWidth: CGFloat = 0.075,
                   outerWidth: CGFloat = 0.21) -> NSBezierPath {
    let path = NSBezierPath()
    let r0 = radius * innerRatio
    let w0 = radius * innerWidth / 2
    let w1 = radius * outerWidth / 2
    let tipY = radius - w1
    for index in 0..<rays {
        let petal = NSBezierPath()
        petal.move(to: NSPoint(x: -w0, y: r0))
        petal.curve(to: NSPoint(x: -w1, y: tipY),
                    controlPoint1: NSPoint(x: -w0 * 1.25, y: r0 + (radius - r0) * 0.45),
                    controlPoint2: NSPoint(x: -w1, y: tipY - (radius - r0) * 0.30))
        petal.appendArc(withCenter: NSPoint(x: 0, y: tipY), radius: w1,
                        startAngle: 180, endAngle: 0, clockwise: true)
        petal.curve(to: NSPoint(x: w0, y: r0),
                    controlPoint1: NSPoint(x: w1, y: tipY - (radius - r0) * 0.30),
                    controlPoint2: NSPoint(x: w0 * 1.25, y: r0 + (radius - r0) * 0.45))
        petal.close()
        var transform = AffineTransform()
        transform.translate(x: center.x, y: center.y)
        transform.rotate(byDegrees: CGFloat(index) * 360 / CGFloat(rays))
        petal.transform(using: transform)
        path.append(petal)
    }
    return path
}

/// 挂锁：锁体填充 + 锁梁描边；open = true 时锁梁右侧抬起。
func drawPadlock(rect: NSRect, open: Bool) {
    let width = rect.width
    let stroke = width * 0.15
    let shackleRadius = width * 0.30
    let bodyHeight = max(rect.height - shackleRadius - stroke, width * 0.40)
    let body = NSRect(x: rect.minX, y: rect.minY, width: width, height: bodyHeight)
    NSBezierPath(roundedRect: body, xRadius: width * 0.22, yRadius: width * 0.22).fill()

    let shackle = NSBezierPath()
    shackle.appendArc(withCenter: NSPoint(x: rect.midX, y: body.maxY),
                      radius: shackleRadius, startAngle: 180,
                      endAngle: open ? 45 : 0, clockwise: true)
    shackle.lineWidth = stroke
    shackle.lineCapStyle = .round
    shackle.stroke()
}

enum MenuBarIconState { case unlocked, locked, alert, busy }

/// 星芒 + 状态徽章，画进 18×18 的点坐标系。
func drawMenuBarIcon(_ state: MenuBarIconState) {
    let size: CGFloat = 18
    NSColor.black.setFill()
    NSColor.black.setStroke()
    if state == .busy {
        NSColor(white: 0, alpha: 0.45).setFill()
        starburstPath(center: NSPoint(x: size * 0.5, y: size * 0.5),
                      radius: size * 0.42, rays: 8).fill()
        return
    }
    starburstPath(center: NSPoint(x: size * 0.35, y: size * 0.65),
                  radius: size * 0.36, rays: 12,
                  innerWidth: 0.06, outerWidth: 0.19).fill()
    switch state {
    case .alert:
        let bar = NSRect(x: size * 0.72, y: size * 0.30, width: size * 0.12, height: size * 0.32)
        NSBezierPath(roundedRect: bar, xRadius: bar.width / 2, yRadius: bar.width / 2).fill()
        let dot = NSRect(x: size * 0.72, y: size * 0.14, width: size * 0.12, height: size * 0.12)
        NSBezierPath(ovalIn: dot).fill()
    default:
        let box = NSRect(x: size * 0.47, y: size * 0.02, width: size * 0.50, height: size * 0.57)
        // 先挖一圈空隙，锁和星芒才不会糊在一起。
        if let context = NSGraphicsContext.current?.cgContext {
            context.setBlendMode(.clear)
            NSBezierPath(ovalIn: box.insetBy(dx: -size * 0.045, dy: -size * 0.045)).fill()
            context.setBlendMode(.normal)
        }
        NSColor.black.setFill()
        NSColor.black.setStroke()
        drawPadlock(rect: box, open: state == .unlocked)
    }
}

/// 模板图：1x / 2x 两份表示，系统按菜单栏明暗自动着色。
var menuBarIconCache: [String: NSImage] = [:]

func menuBarIcon(_ state: MenuBarIconState) -> NSImage {
    let key = String(describing: state)
    if let cached = menuBarIconCache[key] { return cached }
    let points: CGFloat = 18
    let image = NSImage(size: NSSize(width: points, height: points))
    for scale in [1, 2] {
        let pixels = Int(points) * scale
        guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                         bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                         isPlanar: false, colorSpaceName: .deviceRGB,
                                         bytesPerRow: 0, bitsPerPixel: 0) else { continue }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        // 位图上下文以像素为单位；先按像素放大绘制，再标点尺寸。
        // 顺序反了会让 18pt 的设计在 18pt 的画布里被放大 2 倍后裁掉。
        NSGraphicsContext.current?.cgContext.scaleBy(x: CGFloat(scale), y: CGFloat(scale))
        drawMenuBarIcon(state)
        NSGraphicsContext.restoreGraphicsState()
        rep.size = NSSize(width: points, height: points)
        image.addRepresentation(rep)
    }
    image.isTemplate = true
    menuBarIconCache[key] = image
    return image
}

/// 圆角卡片：浅底 + 细描边，高度跟着内容走。
final class CardView: NSView {
    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.cornerRadius = 10
        layer?.borderWidth = 1
        applyColors()
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        applyColors()
    }

    private func applyColors() {
        // 动态颜色要在当前外观下解析，否则切到深色模式还是浅色底。
        effectiveAppearance.performAsCurrentDrawingAppearance {
            layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
            layer?.borderColor = NSColor.separatorColor.cgColor
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
