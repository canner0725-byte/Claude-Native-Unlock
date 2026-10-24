import AppKit
import CoreText

// 一个窗口 + 菜单栏入口的小工具。
// 所有真正的补丁操作都在内置 Python 后端里完成；这里只负责显示状态、
// 收集用户意图，并把后端返回的 JSON 转成中文提示。
//
// 窗口按 Claude 设置页的布局重做：左侧搜索/分区导航，右侧分组设置行。
// 配色、开关、按钮和间距对齐 Claude 桌面端（深色近黑、蓝色开关、灰色按钮）。
class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var scrollView: PlainScrollView!
    var contentTitleLabel: NSTextField!
    var sectionViews: [String: NSView] = [:]
    var sidebarRows: [String: SidebarRowView] = [:]
    var sidebarHeaders: [(view: NSView, ids: [String])] = []
    var selectedSection = "general"
    var searchBox: SearchBoxView!

    // 主开关：安装 / 恢复正式版可用性补丁。
    let toggle = ClaudeSwitch()
    let statusLabel = NSTextField(wrappingLabelWithString: "正在检测…")
    let metaLabel = NSTextField(wrappingLabelWithString: "正在检测…")
    let sidebarAccount = SidebarAccountView()

    // 右侧设置行里的动态状态。
    let patchStateLabel = NSTextField(wrappingLabelWithString: "正在检测…")
    let patchMetaLabel = NSTextField(wrappingLabelWithString: "正在检测…")
    let backupStateLabel = NSTextField(wrappingLabelWithString: "正在检测…")

    // 操作按钮。
    let openButton = ClaudeButton(title: "打开", style: .secondary)
    let refreshButton = ClaudeButton(title: "重新检测", style: .secondary)
    let updateButton = ClaudeButton(title: "更新补丁", style: .secondary)
    let prepareButton = ClaudeButton(title: "准备更新", style: .secondary)
    let permissionButton = ClaudeButton(title: "去系统设置", style: .secondary)
    let guideButton = ClaudeButton(title: "打开说明", style: .secondary)
    let githubButton = ClaudeButton(title: "打开 GitHub", style: .secondary)
    var refreshIconButton: ClaudeIconButton!
    var closeButton: ClaudeIconButton!

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
    var featureRestorable: [Bool] = Array(repeating: false, count: 7)

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

    // 顺序必须和 native_features.FEATURES 完全一致，最后一项是走 CLI 后端的
    // Code 自动整理；tests/test_features.py 会逐项核对。
    //
    // 这里只列官方第三方部署下真正默认关闭的项。官方默认就开启的功能
    // （Cowork/Code 工作台、定时任务、插件市场、Skills、保持唤醒、快速输入、
    // 记住授权等）本来就没有封印，列进来只会让界面显得能开开关却什么也没变。
    let featureIDs = ["file-analysis", "browser", "auto-approve",
                      "desktop-extensions", "tool-search", "webfetch-preflight",
                      "code-auto-dream"]
    let featureTitles = ["Chat 与高级文件分析", "官方内置浏览器",
                         "Cowork 自动审批选项", "插件里的桌面扩展", "MCP 工具按需加载",
                         "WebFetch 免域名预检", "Code 自动整理"]
    let featureDescriptions = [
        "Chat 使用标准工作台，启用官方高级文件分析。",
        "启用 Claude 官方内置浏览器（不是 Dia / Chrome）。",
        "显示 Cowork 官方自动审批选项；动作审查保留。",
        "允许插件里打包的桌面扩展（.mcpb）加载；第三方部署没有组织策略后端时不能直接安装 .mcpb，Code 插件也不接受。签名检查仍按官方策略。",
        "MCP 工具改为按需加载，不再全部塞进上下文；网关不支持时走官方旧路径。",
        "跳过 Code 里 WebFetch 对 api.anthropic.com 的域名预检；抓取仍走官方 WebFetch。",
        "只解除 Code 自动整理的可用性限制；用户开关保持。"
    ]
    /// 官方设置页式的分组：标题 + 该组里的功能 id（按界面顺序排列）。
    let featureGroups: [(String, [String])] = [
        ("工作台", ["file-analysis"]),
        ("工具与网络", ["browser", "auto-approve", "tool-search", "webfetch-preflight"]),
        ("扩展与插件", ["desktop-extensions"]),
        ("Code", ["code-auto-dream"])
    ]
    /// 每个功能分组对应的侧栏 id，顺序和 featureGroups 一一对应。
    let featureGroupSections = ["workbench", "network", "extensions", "code"]
    let featureGroupSymbols = ["square.grid.2x2", "network", "puzzlepiece.extension",
                               "chevron.left.forwardslash.chevron.right"]
    /// 概览页里的分组容器：侧栏点分组名就滚到这里，开关本身始终留在概览首屏。
    var featureGroupViews: [String: NSView] = [:]
    /// 侧栏行的固定顺序；字典遍历顺序不固定，搜索命中时要靠它挑第一项。
    var sidebarOrder: [String] = []
    /// 按 featureIDs 顺序建好每一行：开关数组的下标必须和 featureIDs 对齐，
    /// 所以不能按分组的顺序去建，只能先全建好再分发到各自分组。
    lazy var featureRowViews: [NSView] = (0..<featureIDs.count).map { featureRow($0) }
    /// Code 自动整理在列表里的位置；它走 CLI 后端，不是 3P 配置项。
    var codeAutoDreamIndex: Int { featureIDs.firstIndex(of: "code-auto-dream") ?? featureIDs.count - 1 }
    var featureSwitches: [ClaudeSwitch] = []
    var featureLabels: [NSTextField] = []
    var featureHints: [NSTextField] = []
    var featureRows: [NSView] = []
    var featureEnabled: [Bool] = Array(repeating: false, count: 7)
    /// 当前 Claude 版本是否还有这个配置键；没有就禁用而不是装作能点。
    var featureAvailable: [Bool] = Array(repeating: true, count: 7)
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

        // 主窗口：透明标题栏、全尺寸内容，左侧栏直接延伸到交通灯下面。
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Claude 原生助手"
        window.isReleasedWhenClosed = false
        // 窗口状态不参与系统恢复：强制退出后 macOS 的「要恢复窗口吗」弹窗
        // 会在完成启动之前弹出，把这个只有一扇窗的工具挡在外面。
        window.isRestorable = false
        window.restorationClass = nil
        window.minSize = NSSize(width: 880, height: 600)
        window.backgroundColor = ClaudeTheme.pane
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.styleMask.insert(.fullSizeContentView)
        let savedFrameAtLaunch = UserDefaults.standard.string(
            forKey: "NSWindow Frame ClaudeNativeUnlockSettingsWindowV2")
        window.setFrameAutosaveName("ClaudeNativeUnlockSettingsWindowV2")
        // 早期版本可能留下过被 Auto Layout 压窄的窗口尺寸；小于最小宽度就重置。
        let savedKey = "NSWindow Frame ClaudeNativeUnlockSettingsWindowV2"
        if let saved = UserDefaults.standard.string(forKey: savedKey) {
            let parts = saved.split(separator: " ")
            if parts.count >= 4, let width = Double(parts[2]), width < 880 {
                UserDefaults.standard.removeObject(forKey: savedKey)
                window.setContentSize(NSSize(width: 1000, height: 820))
                window.center()
            }
        } else {
            window.setContentSize(NSSize(width: 1000, height: 820))
            window.center()
        }

        let root = FocusRootView()
        root.wantsLayer = true
        window.contentView = root
        let sidebar = buildSidebar()
        let pane = buildContentPane()
        sidebar.translatesAutoresizingMaskIntoConstraints = false
        pane.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(sidebar)
        root.addSubview(pane)
        NSLayoutConstraint.activate([
            sidebar.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            sidebar.topAnchor.constraint(equalTo: root.topAnchor),
            sidebar.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            sidebar.widthAnchor.constraint(equalToConstant: ClaudeTheme.Metric.navWidth),
            pane.leadingAnchor.constraint(equalTo: sidebar.trailingAnchor),
            pane.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            pane.topAnchor.constraint(equalTo: root.topAnchor),
            pane.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            pane.widthAnchor.constraint(greaterThanOrEqualToConstant: 600)
        ])
        window.contentMinSize = NSSize(width: 880, height: 600)

        if !NSScreen.screens.contains(where: { $0.visibleFrame.intersects(window.frame) }) {
            window.center()
        }
        selectSection("general")
        updateStatusPill()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        window.initialFirstResponder = root
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            self.window.makeFirstResponder(root)
        }
        if savedFrameAtLaunch == nil {
            // Auto Layout 会把窗口压到最小宽度；首次启动时在布局完成后恢复到目标尺寸。
            DispatchQueue.main.async {
                self.window.setContentSize(NSSize(width: 1000, height: 820))
                self.window.center()
            }
        }
        #if PREVIEW
        // 预览构建不跑后端：一是避免在用户机器上做真实检测，二是能稳定截到静态界面。
        if ProcessInfo.processInfo.environment["PREVIEW_NO_BACKEND"] == nil {
            execute("status-all", backend: "all")
        }
        #else
        execute("status-all", backend: "all")
        #endif
    }

    // MARK: - 左侧栏

    func buildSidebar() -> NSView {
        let sidebar = SidebarBackgroundView()
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.distribution = .fill
        stack.spacing = 0
        stack.wantsLayer = true
        // 官方设置页侧栏：p-2 外边距，搜索框与导航行左右再收 8pt。
        stack.edgeInsets = NSEdgeInsets(top: 44, left: 16, bottom: 12, right: 16)
        stack.translatesAutoresizingMaskIntoConstraints = false
        sidebar.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: sidebar.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: sidebar.trailingAnchor),
            stack.topAnchor.constraint(equalTo: sidebar.topAnchor),
            stack.bottomAnchor.constraint(equalTo: sidebar.bottomAnchor)
        ])

        // 与 Claude 设置页一致：搜索框直接放在交通灯下面，不在侧栏顶部加品牌行。
        searchBox = SearchBoxView()
        searchBox.onChange = { [weak self] in self?.filterSidebar() }
        searchBox.translatesAutoresizingMaskIntoConstraints = false
        stack.addArrangedSubview(searchBox)
        // `-16` 是本文件里侧栏子视图的统一写法：内容区从左边距 16 开始、宽 = 侧栏宽 - 16，
        // 与下面的导航行完全对齐。改成 `stack.width` 会让搜索框比导航行宽出 16pt 并左边越界
        // （预览像素实测：左边界从 22pt 跑到 5pt）。搜索框内部四周另有 6pt 绘制留白，属设计。
        searchBox.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -16).isActive = true
        searchBox.heightAnchor.constraint(equalToConstant: 32).isActive = true
        stack.setCustomSpacing(18, after: searchBox)

        addSidebarHeader("设置", ids: ["general", "patch"], to: stack)
        addSidebarRow("general", title: "概览", symbol: "slider.horizontal.3", to: stack)
        addSidebarRow("patch", title: "补丁与更新", symbol: "arrow.triangle.2.circlepath", to: stack)

        // 功能分组在侧栏各有入口：点一下回到概览并滚到该组。
        // 开关本身始终留在概览首屏，侧栏这里是目录，不是另一个页面。
        addSidebarHeader("功能", ids: featureGroupSections, to: stack)
        for (index, entry) in featureGroups.enumerated() {
            let id = featureGroupSections[index]
            addSidebarRow(id, title: entry.0, symbol: featureGroupSymbols[index], to: stack) {
                [weak self] in self?.jumpToFeatureGroup(id)
            }
        }

        addSidebarHeader("本机", ids: ["system", "about"], to: stack)
        addSidebarRow("system", title: "系统授权", symbol: "lock.shield", to: stack)
        addSidebarRow("about", title: "关于", symbol: "info.circle", to: stack)

        addSidebarHeader("更多", ids: ["shortcuts", "guide", "github"], to: stack)
        addSidebarRow("shortcuts", title: "快捷键", symbol: "command", to: stack)
        addSidebarRow("guide", title: "使用说明", symbol: "book", to: stack) { [weak self] in
            self?.openFeatureReport()
        }
        addSidebarRow("github", title: "源码仓库", symbol: "link", to: stack) { [weak self] in
            self?.openGitHub()
        }

        let spacer = NSView()
        spacer.setContentHuggingPriority(.init(1), for: .vertical)
        stack.addArrangedSubview(spacer)

        let accountLine = HairlineView()
        accountLine.translatesAutoresizingMaskIntoConstraints = false
        stack.addArrangedSubview(accountLine)
        accountLine.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -16).isActive = true
        accountLine.heightAnchor.constraint(equalToConstant: 1).isActive = true
        stack.setCustomSpacing(8, after: accountLine)

        sidebarAccount.translatesAutoresizingMaskIntoConstraints = false
        sidebarAccount.onAbout = { [weak self] in self?.selectSection("about") }
        stack.addArrangedSubview(sidebarAccount)
        sidebarAccount.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -16).isActive = true
        sidebarAccount.heightAnchor.constraint(equalToConstant: 48).isActive = true

        return sidebar
    }

    func addSidebarHeader(_ title: String, ids: [String], to stack: NSStackView) {
        let label = NSTextField(labelWithString: title)
        label.font = ClaudeFont.caption(.medium)
        label.textColor = ClaudeTheme.textMuted
        let wrap = NSView()
        label.translatesAutoresizingMaskIntoConstraints = false
        wrap.addSubview(label)
        NSLayoutConstraint.activate([
            label.leadingAnchor.constraint(equalTo: wrap.leadingAnchor, constant: 8),
            label.topAnchor.constraint(equalTo: wrap.topAnchor, constant: 16),
            label.bottomAnchor.constraint(equalTo: wrap.bottomAnchor, constant: -6)
        ])
        stack.addArrangedSubview(wrap)
        wrap.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -16).isActive = true
        sidebarHeaders.append((wrap, ids))
    }

    func addSidebarRow(_ id: String, title: String, symbol: String, to stack: NSStackView,
                       action: (() -> Void)? = nil) {
        let row = SidebarRowView(id: id, title: title, symbol: symbol) { [weak self] selected in
            if let action = action {
                action()
            } else {
                self?.selectSection(selected)
            }
        }
        row.translatesAutoresizingMaskIntoConstraints = false
        stack.addArrangedSubview(row)
        row.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -16).isActive = true
        row.heightAnchor.constraint(equalToConstant: ClaudeTheme.Metric.controlHeight).isActive = true
        sidebarRows[id] = row
        sidebarOrder.append(id)
    }

    /// 侧栏的分组名是概览页里的锚点：回到概览并滚到该组，高亮留在分组名上。
    func jumpToFeatureGroup(_ id: String) {
        guard let group = featureGroupViews[id] else { return }
        selectSection("general", highlight: id)
        // 切页后要等布局完成，分组才有正确的 frame。
        DispatchQueue.main.async {
            guard let document = self.scrollView?.documentView else { return }
            self.scrollView?.scrollToRect(group.convert(group.bounds, to: document))
        }
    }

    func selectSection(_ id: String, highlight: String? = nil) {
        guard sectionViews[id] != nil else { return }
        selectedSection = id
        let highlighted = highlight ?? id
        for (rowID, row) in sidebarRows {
            row.isSelected = rowID == highlighted
        }
        for (sectionID, view) in sectionViews {
            view.isHidden = sectionID != id
        }
        var titles = [
            "general": "概览",
            "patch": "补丁与更新",
            "system": "系统授权",
            "shortcuts": "快捷键",
            "about": "关于"
        ]
        for (index, groupTitle) in featureGroups.map({ $0.0 }).enumerated() {
            titles[featureGroupSections[index]] = groupTitle
        }
        contentTitleLabel?.stringValue = titles[id] ?? ""
        // 等这次布局完成后再回到顶部；滚动容器高度刚变化时立即 scroll 会停在旧位置。
        // 锚点跳转要滚到分组，不能在这里再回顶部，否则会把目标位置覆盖掉。
        if highlight == nil {
            DispatchQueue.main.async {
                self.scrollView?.scrollToTop()
            }
        }
    }

    // MARK: - 右侧内容

    func buildContentPane() -> NSView {
        let pane = ContentBackgroundView()
        let topBar = NSView()
        topBar.translatesAutoresizingMaskIntoConstraints = false
        pane.addSubview(topBar)

        contentTitleLabel = NSTextField(labelWithString: "概览")
        contentTitleLabel.font = ClaudeFont.title()
        contentTitleLabel.textColor = ClaudeTheme.textPrimary
        contentTitleLabel.translatesAutoresizingMaskIntoConstraints = false
        // 与官方设置页一致：内容顶部是 22pt semibold 的页面标题，右侧放关闭按钮。
        topBar.addSubview(contentTitleLabel)

        progress.style = .spinning
        progress.controlSize = .small
        progress.isDisplayedWhenStopped = false
        progress.translatesAutoresizingMaskIntoConstraints = false
        topBar.addSubview(progress)

        refreshIconButton = ClaudeIconButton(symbol: "arrow.clockwise",
                                             accessibility: "重新检测") { [weak self] in
            self?.refresh()
        }
        closeButton = ClaudeIconButton(symbol: "xmark", accessibility: "关闭窗口") { [weak self] in
            self?.window.performClose(nil)
        }
        refreshIconButton.translatesAutoresizingMaskIntoConstraints = false
        closeButton.translatesAutoresizingMaskIntoConstraints = false
        topBar.addSubview(refreshIconButton)
        topBar.addSubview(closeButton)

        let scroller = PlainScrollView()
        scrollView = scroller
        scroller.translatesAutoresizingMaskIntoConstraints = false
        pane.addSubview(scroller)

        NSLayoutConstraint.activate([
            topBar.leadingAnchor.constraint(equalTo: pane.leadingAnchor),
            topBar.trailingAnchor.constraint(equalTo: pane.trailingAnchor),
            topBar.topAnchor.constraint(equalTo: pane.topAnchor),
            topBar.heightAnchor.constraint(equalToConstant: 60),
            contentTitleLabel.leadingAnchor.constraint(equalTo: topBar.leadingAnchor,
                                                       constant: ClaudeTheme.Metric.contentInsetX),
            contentTitleLabel.centerYAnchor.constraint(equalTo: topBar.centerYAnchor, constant: 8),
            progress.trailingAnchor.constraint(equalTo: refreshIconButton.leadingAnchor, constant: -12),
            progress.centerYAnchor.constraint(equalTo: topBar.centerYAnchor, constant: 8),
            closeButton.trailingAnchor.constraint(equalTo: topBar.trailingAnchor, constant: -16),
            closeButton.centerYAnchor.constraint(equalTo: topBar.centerYAnchor, constant: 8),
            closeButton.widthAnchor.constraint(equalToConstant: 28),
            closeButton.heightAnchor.constraint(equalToConstant: 28),
            refreshIconButton.trailingAnchor.constraint(equalTo: closeButton.leadingAnchor, constant: -4),
            refreshIconButton.centerYAnchor.constraint(equalTo: topBar.centerYAnchor, constant: 8),
            refreshIconButton.widthAnchor.constraint(equalToConstant: 28),
            refreshIconButton.heightAnchor.constraint(equalToConstant: 28),
            scrollView.leadingAnchor.constraint(equalTo: pane.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: pane.trailingAnchor),
            scrollView.topAnchor.constraint(equalTo: topBar.bottomAnchor),
            scrollView.bottomAnchor.constraint(equalTo: pane.bottomAnchor)
        ])

        let doc = FlippedView()
        doc.translatesAutoresizingMaskIntoConstraints = false
        scrollView.documentView = doc
        let sections = NSStackView()
        sections.orientation = .vertical
        sections.alignment = .leading
        sections.distribution = .fill
        sections.spacing = 0
        // 内容短的分区不能被拉到视口高度：外层 .fill 会把多余高度摊给可见的分区，
        // 「系统授权」这类只有两组的页面行距就会被撑成两倍。
        sections.setContentHuggingPriority(.required, for: .vertical)
        sections.translatesAutoresizingMaskIntoConstraints = false
        doc.addSubview(sections)
        NSLayoutConstraint.activate([
            doc.leadingAnchor.constraint(equalTo: scrollView.contentView.leadingAnchor),
            doc.trailingAnchor.constraint(equalTo: scrollView.contentView.trailingAnchor),
            doc.topAnchor.constraint(equalTo: scrollView.contentView.topAnchor),
            doc.widthAnchor.constraint(equalTo: scrollView.contentView.widthAnchor),
            doc.heightAnchor.constraint(greaterThanOrEqualTo: scrollView.contentView.heightAnchor),
            sections.leadingAnchor.constraint(equalTo: doc.leadingAnchor,
                                              constant: ClaudeTheme.Metric.contentInsetX),
            sections.trailingAnchor.constraint(equalTo: doc.trailingAnchor,
                                               constant: -ClaudeTheme.Metric.contentInsetX),
            sections.topAnchor.constraint(equalTo: doc.topAnchor, constant: 4),
            // 用「不超过」而不是「等于」：等于会把文档高度反推给分区，逼着它撑满视口。
            sections.bottomAnchor.constraint(lessThanOrEqualTo: doc.bottomAnchor, constant: -40),
            doc.heightAnchor.constraint(greaterThanOrEqualTo: sections.heightAnchor, constant: 44)
        ])

        let builders: [(String, NSView)] = [
            ("general", buildGeneralSection()),
            ("patch", buildPatchSection()),
            ("system", buildSystemSection()),
            ("shortcuts", buildShortcutsSection()),
            ("about", buildAboutSection())
        ]
        for (id, view) in builders {
            sections.addArrangedSubview(view)
            view.widthAnchor.constraint(equalTo: sections.widthAnchor).isActive = true
            sectionViews[id] = view
        }
        return pane
    }

    func buildGeneralSection() -> NSView {
        configureDynamicLabel(statusLabel)
        configureDynamicLabel(metaLabel)

        let computerUse = settingsRow(
            title: "Computer Use",
            descriptionView: statusLabel,
            control: toggle)
        toggle.target = self
        toggle.action = #selector(switchChanged)
        toggle.setAccessibilityLabel("Computer Use 解锁开关")
        toggle.isEnabled = false

        // 标题原来只写「Claude」，和内容开头的「Claude 3.6556.2…」重复，
        // 也没说明这一行是什么。改成说明行本身（版本/补丁/运行状态）。
        let versionRow = settingsRow(
            title: "已安装版本",
            descriptionView: metaLabel)

        let openRow = settingsRow(
            title: "打开 Claude",
            description: "启动已安装的 Claude 应用。",
            control: openButton)
        openButton.target = self
        openButton.action = #selector(openClaude)

        let refreshRow = settingsRow(
            title: "重新检测",
            description: "重新核对补丁、官方备份与配置状态。",
            control: refreshButton)
        refreshButton.target = self
        refreshButton.action = #selector(refresh)

        featureSummary.font = ClaudeFont.caption()
        featureSummary.textColor = ClaudeTheme.warning
        featureSummary.isHidden = true
        featureSummary.maximumNumberOfLines = 3
        featureSummary.lineBreakMode = .byWordWrapping

        // 概览首屏保留全部功能开关（用户明确要求），按官方设置页的方式分组：
        // 每组一个 15pt 标题、带 1px 分隔线的行；侧栏的分组名是这些组的锚点。
        var groups: [NSView] = [featureSummary,
                                settingsGroup("解锁", rows: [computerUse, versionRow])]
        for (index, entry) in featureGroups.enumerated() {
            let rows = entry.1.compactMap { id in
                featureIDs.firstIndex(of: id).map { featureRowViews[$0] }
            }
            guard !rows.isEmpty else { continue }
            let group = settingsGroup(entry.0, rows: rows)
            featureGroupViews[featureGroupSections[index]] = group
            groups.append(group)
        }
        groups.append(settingsGroup("操作", rows: [openRow, refreshRow]))

        return section(groups,
                       intro: "Computer Use、原生配置与 Code 自动整理的可用性开关。动作授权、拒绝与中断仍由 Claude 处理。")
    }

    func featureRow(_ index: Int) -> NSView {
        let title = NSTextField(labelWithString: featureTitles[index])
        title.font = ClaudeFont.body()
        title.textColor = ClaudeTheme.textPrimary
        title.toolTip = featureDescriptions[index]

        let desc = NSTextField(wrappingLabelWithString: featureDescriptions[index])
        desc.font = ClaudeFont.body()
        desc.textColor = ClaudeTheme.textMuted
        desc.maximumNumberOfLines = 0
        desc.lineBreakMode = .byWordWrapping
        desc.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)

        let detail = NSTextField(wrappingLabelWithString: "")
        detail.font = ClaudeFont.caption()
        detail.textColor = ClaudeTheme.warning
        detail.maximumNumberOfLines = 2
        detail.lineBreakMode = .byWordWrapping
        detail.isHidden = true

        let hint = NSTextField(wrappingLabelWithString: "")
        hint.font = ClaudeFont.caption()
        hint.textColor = ClaudeTheme.textMuted
        hint.maximumNumberOfLines = 2
        hint.lineBreakMode = .byWordWrapping
        hint.isHidden = true

        let control = ClaudeSwitch()
        control.tag = index
        control.target = self
        control.action = #selector(featureChanged(_:))
        control.setAccessibilityLabel(featureTitles[index] + " 配置开关")
        control.isEnabled = false
        control.setContentHuggingPriority(.required, for: .horizontal)
        control.setContentCompressionResistancePriority(.required, for: .horizontal)
        featureSwitches.append(control)
        featureLabels.append(detail)
        featureHints.append(hint)

        let row = settingsRow(titleView: title, descriptionView: desc,
                              extraViews: [detail, hint], control: control)
        featureRows.append(row)
        return row
    }

    func buildPatchSection() -> NSView {
        configureDynamicLabel(patchStateLabel)
        configureDynamicLabel(patchMetaLabel)
        configureDynamicLabel(backupStateLabel)

        let stateRow = settingsRow(title: "补丁状态", descriptionView: patchStateLabel)
        let versionRow = settingsRow(title: "Claude 版本", descriptionView: patchMetaLabel)
        let backupRow = settingsRow(title: "官方备份", descriptionView: backupStateLabel)

        updateButton.target = self
        updateButton.action = #selector(confirmUpdate)
        updateButton.toolTip = "把补丁更新到最新 revision；只保留最新版和当前官方备份。"
        let updateRow = settingsRow(
            title: "更新补丁",
            description: "安装最新适配 revision。旧补丁会清理，只保留当前官方备份。",
            control: updateButton)

        prepareButton.target = self
        prepareButton.action = #selector(prepareUpdate)
        let prepareRow = settingsRow(
            title: "为官方更新做准备",
            description: "恢复完整官方签名和官方文件，供 Claude 官方更新器校验。备份、配置和会话都会保留。",
            control: prepareButton)

        return section([
            settingsGroup("状态", rows: [stateRow, versionRow, backupRow]),
            settingsGroup("维护", rows: [updateRow, prepareRow])
        ], intro: "补丁只在已适配的 Claude 版本上安装；更新或恢复都会先校验当前文件和官方备份。")
    }

    func buildSystemSection() -> NSView {
        permissionButton.target = self
        permissionButton.action = #selector(openPrivacySettings)
        permissionButton.toolTip = "打开系统设置的隐私页面，给 Claude 勾选辅助功能与屏幕录制。"

        let permissionRow = settingsRow(
            title: "辅助功能与屏幕录制",
            description: "在系统设置里给 Claude 勾选这两项，然后在 Claude 设置 → This computer → System 确认显示 Granted。",
            control: permissionButton)

        let reviewRow = settingsRow(
            title: "逐应用授权",
            description: "Computer Use 的逐应用授权、应用拦截、安全输入保护和用户拒绝都由 Claude 保留；本工具不绕过这些检查。")

        let browserRow = settingsRow(
            title: "外部浏览器",
            description: "Dia、Chrome 等外部浏览器受官方只读授权限制，Computer Use 不能点击或输入。网页操作请用 Claude 官方内置浏览器或 Claude in Chrome。")

        return section([
            settingsGroup("权限", rows: [permissionRow]),
            settingsGroup("边界", rows: [reviewRow, browserRow])
        ], intro: "系统授权由 macOS 管理，只有你本人在系统设置里才能勾选；本工具只能把你带到对应页面。")
    }

    func buildShortcutsSection() -> NSView {
        func keycap(_ text: String) -> NSTextField {
            let label = NSTextField(labelWithString: text)
            label.font = .monospacedSystemFont(ofSize: 12, weight: .medium)
            label.textColor = ClaudeTheme.textSecondary
            return label
        }
        let rows = [
            settingsRow(title: "重新检测", description: "重新核对补丁、官方备份与配置状态。", control: keycap("⌘R")),
            settingsRow(title: "打开 Claude", description: "启动已安装的 Claude 应用。", control: keycap("⌘O")),
            settingsRow(title: "显示主窗口", description: "从菜单栏回到这个窗口。", control: keycap("⌘0")),
            settingsRow(title: "快捷操作菜单", description: "在窗口右上角弹出菜单栏菜单。", control: keycap("⌘M")),
            settingsRow(title: "更新解锁补丁", description: "安装最新适配 revision。", control: keycap("⌘U")),
            settingsRow(title: "为官方更新做准备", description: "恢复官方签名和文件，供官方更新器校验。", control: keycap("⌘P")),
            settingsRow(title: "退出助手", description: "菜单栏图标也会一起退出。", control: keycap("⌘Q"))
        ]
        return section([
            settingsGroup("键盘", rows: rows)
        ], intro: "这些快捷键在助手窗口激活时可用；菜单栏菜单里也有同样的入口。")
    }

    func buildAboutSection() -> NSView {
        guideButton.target = self
        guideButton.action = #selector(openFeatureReport)
        githubButton.target = self
        githubButton.action = #selector(openGitHub)

        let versionRow = settingsRow(
            title: "版本",
            description: "Claude 原生助手 " + assistantVersion + " (" + assistantBuild + ")。")
        let guideRow = settingsRow(
            title: "使用说明",
            description: "打开随包的中文说明，包含开关含义、官方更新顺序和恢复步骤。",
            control: guideButton)
        let githubRow = settingsRow(
            title: "源码与发布",
            description: "独立开源项目，与 Anthropic 无隶属关系；只分发自有的工具代码，不含 Claude 程序或用户数据。",
            control: githubButton)
        let dataRow = settingsRow(
            title: "数据位置",
            description: "备份与恢复记录保存在 ~/Library/Application Support/Claude 原生助手/，请勿手动删除。")

        return section([
            settingsGroup("应用", rows: [versionRow, guideRow, githubRow]),
            settingsGroup("数据", rows: [dataRow])
        ], intro: "只解除已适配版本的原生功能可用性条件，不修改官方功能行为。")
    }

    // MARK: - 设置行与分组

    func section(_ groups: [NSView], intro: String) -> NSView {
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.distribution = .fill
        stack.spacing = 0
        // 内容短的分区不能被拉高：外层 .fill 会把多余高度分给视图，
        // 结果「系统授权」这种只有两组的页面行距被撑成两倍。
        stack.setContentHuggingPriority(.required, for: .vertical)

        // 官方设置页的分组说明：13pt 次级文字，位于页面标题下方。
        let introLabel = NSTextField(wrappingLabelWithString: intro)
        introLabel.font = ClaudeFont.footnote()
        introLabel.textColor = ClaudeTheme.textSecondary
        introLabel.maximumNumberOfLines = 0
        introLabel.lineBreakMode = .byWordWrapping
        introLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        introLabel.translatesAutoresizingMaskIntoConstraints = false
        introLabel.setContentHuggingPriority(.required, for: .vertical)
        stack.addArrangedSubview(introLabel)
        introLabel.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        stack.setCustomSpacing(32, after: introLabel)

        for (index, group) in groups.enumerated() {
            stack.addArrangedSubview(group)
            group.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
            if index < groups.count - 1 {
                // 官方分组之间是 40pt 空白加 1px 分隔线。
                stack.setCustomSpacing(ClaudeTheme.Metric.groupGap, after: group)
            }
        }
        return stack
    }

    func settingsGroup(_ title: String, rows: [NSView]) -> NSView {
        let group = NSStackView()
        group.orientation = .vertical
        group.alignment = .leading
        group.distribution = .fill
        group.spacing = 0
        let heading = NSTextField(labelWithString: title)
        heading.font = ClaudeFont.heading()
        heading.textColor = ClaudeTheme.textPrimary
        heading.setContentHuggingPriority(.required, for: .vertical)
        group.addArrangedSubview(heading)
        group.setCustomSpacing(ClaudeTheme.Metric.headerGap, after: heading)
        for (index, row) in rows.enumerated() {
            row.setContentHuggingPriority(.required, for: .vertical)
            group.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: group.widthAnchor).isActive = true
            if index < rows.count - 1 {
                // 官方设置行之间是 1px 细分隔线，没有额外间距。
                let line = HairlineView()
                line.translatesAutoresizingMaskIntoConstraints = false
                group.addArrangedSubview(line)
                line.widthAnchor.constraint(equalTo: group.widthAnchor).isActive = true
                line.heightAnchor.constraint(equalToConstant: 1).isActive = true
            }
        }
        return group
    }

    func settingsRow(title: String, description: String, control: NSView? = nil) -> NSView {
        let titleLabel = NSTextField(labelWithString: title)
        titleLabel.font = ClaudeFont.body()
        titleLabel.textColor = ClaudeTheme.textPrimary
        let desc = NSTextField(wrappingLabelWithString: description)
        desc.font = ClaudeFont.body()
        desc.textColor = ClaudeTheme.textMuted
        desc.maximumNumberOfLines = 0
        desc.lineBreakMode = .byWordWrapping
        desc.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return settingsRow(titleView: titleLabel, descriptionView: desc,
                           extraViews: [], control: control)
    }

    func settingsRow(title: String, descriptionView: NSView, control: NSView? = nil) -> NSView {
        let titleLabel = NSTextField(labelWithString: title)
        titleLabel.font = ClaudeFont.body()
        titleLabel.textColor = ClaudeTheme.textPrimary
        return settingsRow(titleView: titleLabel, descriptionView: descriptionView,
                           extraViews: [], control: control)
    }

    func settingsRow(titleView: NSTextField, descriptionView: NSView,
                     extraViews: [NSView], control: NSView?) -> NSView {
        let row = NSView()
        let text = NSStackView()
        text.orientation = .vertical
        text.alignment = .leading
        text.spacing = ClaudeTheme.Metric.rowLabelGap
        text.translatesAutoresizingMaskIntoConstraints = false
        text.addArrangedSubview(titleView)
        text.addArrangedSubview(descriptionView)
        for view in extraViews { text.addArrangedSubview(view) }
        // 说明文字允许换行但不要被拉伸；否则 NSTextField 的默认竖直抗压缩会把行高撑大。
        for view in text.arrangedSubviews {
            view.setContentHuggingPriority(.defaultHigh, for: .vertical)
        }
        row.addSubview(text)

        if let control = control {
            control.translatesAutoresizingMaskIntoConstraints = false
            row.addSubview(control)
            NSLayoutConstraint.activate([
                control.trailingAnchor.constraint(equalTo: row.trailingAnchor),
                control.centerYAnchor.constraint(equalTo: row.centerYAnchor),
                text.trailingAnchor.constraint(equalTo: control.leadingAnchor, constant: -24)
            ])
        } else {
            text.trailingAnchor.constraint(equalTo: row.trailingAnchor).isActive = true
        }
        // 官方设置行：上下各 12pt（--cds-pad-md）。
        NSLayoutConstraint.activate([
            text.leadingAnchor.constraint(equalTo: row.leadingAnchor),
            text.topAnchor.constraint(equalTo: row.topAnchor,
                                      constant: ClaudeTheme.Metric.rowPaddingY),
            text.bottomAnchor.constraint(equalTo: row.bottomAnchor,
                                         constant: -ClaudeTheme.Metric.rowPaddingY)
        ])
        return row
    }

    func configureDynamicLabel(_ label: NSTextField) {
        label.font = ClaudeFont.body()
        label.textColor = ClaudeTheme.textMuted
        label.maximumNumberOfLines = 0
        label.lineBreakMode = .byWordWrapping
    }

    // MARK: - 搜索

    func filterSidebar() {
        let query = searchBox.field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        // 命中某个开关时，它所在的分组页也要留下来，否则搜索完点进去是空的。
        let hitIndex = query.isEmpty ? nil : featureTitles.firstIndex { title in
            title.lowercased().contains(query)
                || featureDescriptions[featureTitles.firstIndex(of: title) ?? 0].lowercased().contains(query)
        }
        let hitSection = hitIndex.flatMap { featureGroupSection(for: featureIDs[$0]) }
        var firstVisible: String?
        for id in sidebarOrder {
            guard let row = sidebarRows[id] else { continue }
            let matches = query.isEmpty || row.titleText.lowercased().contains(query) || id == hitSection
            row.isHidden = !matches
            if matches, firstVisible == nil { firstVisible = id }
        }
        for (view, ids) in sidebarHeaders {
            view.isHidden = !ids.contains { sidebarRows[$0]?.isHidden == false }
        }
        if let current = sidebarRows[selectedSection], current.isHidden, let id = firstVisible {
            selectSection(id)
        }
        if let index = hitIndex {
            selectSection("general", highlight: featureGroupSection(for: featureIDs[index]))
            scrollToFeature(index)
        }
    }

    /// 某个开关属于哪个侧栏分区。
    func featureGroupSection(for id: String) -> String? {
        guard let group = featureGroups.firstIndex(where: { $0.1.contains(id) }),
              featureGroupSections.indices.contains(group) else { return nil }
        return featureGroupSections[group]
    }

    /// 搜索命中某个原生配置时，滚到那一行，而不是只换页面。
    func scrollToFeature(_ index: Int) {
        guard featureRows.indices.contains(index) else { return }
        // 切页后要等布局完成，行才有正确的 frame。
        DispatchQueue.main.async {
            guard let document = self.scrollView?.documentView else { return }
            let row = self.featureRows[index]
            self.scrollView?.scrollToRect(row.convert(row.bounds, to: document))
        }
    }

    // MARK: - 生命周期

    func applicationDidBecomeActive(_ notification: Notification) {
        #if PREVIEW
        // 预览构建不需要后台刷新，避免截图时正好读到“正在检测”。
        if true { return }
        #endif
        // 回到助手窗口时顺手刷新；弹窗打开时或 4 秒内不重复检测。
        guard statusLoaded, !busy, !quittingClaude,
              window?.attachedSheet == nil,
              Date().timeIntervalSince(lastStatusAt) > 4 else { return }
        execute("status-all", backend: "all")
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    // 这个工具只有一个窗口，窗口状态没有恢复价值；强制退出或崩溃后，
    // 系统的「要恢复窗口吗」弹窗会挡住启动（而且它出现在完成启动之前）。
    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { true }
    func applicationShouldSaveApplicationState(_ app: NSApplication) -> Bool { false }
    func applicationShouldRestoreApplicationState(_ app: NSApplication) -> Bool { false }

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

    @objc func openGitHub() {
        if let url = URL(string: "https://github.com/canner0725-byte/Claude-Native-Unlock") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func featureChanged(_ sender: ClaudeSwitch) {
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
        statusLabel.textColor = ClaudeTheme.textSecondary
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
        updateStatusPill()
    }

    /// 左侧栏底部的状态胶囊。
    func updateStatusPill() {
        guard sidebarAccount.superview != nil else { return }
        let blocked = busy || quittingClaude
        let text: String
        let tint: NSColor
        var pulsing = false
        if blocked {
            text = "处理中"; tint = ClaudeTheme.accent; pulsing = true
        } else if !statusLoaded {
            text = "检测中"; tint = ClaudeTheme.accent; pulsing = true
        } else if recoveryRequired {
            text = "需要恢复"; tint = ClaudeTheme.warning
        } else if !officialSupported {
            text = targetExists ? "待适配" : "未找到 Claude"; tint = ClaudeTheme.warning
        } else if enabled && !backupVerified {
            text = "备份异常"; tint = ClaudeTheme.warning
        } else if enabled {
            text = patchRevision > 0 ? "已解锁 · 补丁 r\(patchRevision)" : "已解锁"
            tint = ClaudeTheme.accent
        } else {
            text = "未开启"; tint = ClaudeTheme.textMuted
        }
        sidebarAccount.update(text: text, tint: tint, pulsing: pulsing)
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

    /// 权限按钮始终在“系统授权”页可见；能不能点由忙碌状态决定。
    func updatePermissionButton() {
        permissionButton.isHidden = false
        permissionButton.isEnabled = !busy && !quittingClaude
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
        refreshIconButton.isEnabled = false
        for control in featureSwitches { control.isEnabled = false }

        progress.startAnimation(nil)
        statusLabel.textColor = ClaudeTheme.textSecondary
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
        mirrorPatchStatus()
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
        statusLabel.textColor = ClaudeTheme.textSecondary
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
    func setStatus(_ text: String, color: NSColor = ClaudeTheme.textMuted) {
        guard text != statusLabel.stringValue else {
            statusLabel.textColor = color
            return
        }
        statusLabel.stringValue = text
        statusLabel.textColor = color
        statusLabel.alphaValue = 0
        animate(0.22) { self.statusLabel.animator().alphaValue = 1 }
    }

    /// 旧界面里更新按钮会按可用性淡入；设置页里始终展示，只用启用状态表达。
    func setUpdateVisible(_ visible: Bool) {
        updateButton.isHidden = false
        updateButton.alphaValue = 1
    }

    /// 所有控件是否可用只在这里决定，避免多处状态不一致。
    func refreshButtons() {
        let interactive = !busy && !quittingClaude
        let configReady = officialSupported && !recoveryRequired && (!enabled || backupVerified)
        // Claude 正在运行时也允许操作；确认后由助手先正常退出。
        toggle.isEnabled = interactive && officialSupported && (!enabled || backupVerified)
        // 打开 Claude 不依赖适配状态：官方更新后仍应能正常使用。
        openButton.isEnabled = interactive && targetExists && !recoveryRequired
        refreshButton.isEnabled = interactive
        refreshIconButton.isEnabled = interactive
        updateButton.isEnabled = interactive && updateAvailable
        updateButton.title = updateAvailable && availableRevision > 0
            ? "更新到 r\(availableRevision)" : "已是最新"
        updateButton.invalidateIntrinsicContentSize()
        prepareButton.isEnabled = interactive && officialSupported && !recoveryRequired
            && (!enabled || backupVerified)
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
    }

    func applyOfficial(_ result: [String: Any]) {
        statusLoaded = true
        if let error = result["error"] as? String {
            lastError = error
            officialSupported = false
            updateAvailable = false
            setStatus(error, color: ClaudeTheme.warning)
            refreshButtons()
            toggle.isEnabled = false
            mirrorPatchStatus()
            updateStatusPill()
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
            setStatus("安装中断 · 关闭开关即可恢复官方备份", color: ClaudeTheme.warning)
        } else if !supported {
            setStatus(targetExists ? "此版本尚未适配 · 不会修改 Claude" : "未找到 Claude",
                      color: ClaudeTheme.warning)
        } else if !enabled {
            setStatus("未开启", color: ClaudeTheme.textSecondary)
        } else if !backupVerified {
            setStatus("已解锁 · 官方备份异常，恢复已停用", color: ClaudeTheme.warning)
        } else if updateAvailable {
            setStatus("已解锁 · 有新版本可更新", color: ClaudeTheme.accent)
        } else {
            setStatus("已解锁 · 文件校验通过", color: ClaudeTheme.textSecondary)
        }
        metaVersion = result["version"] as? String ?? metaVersion
        refreshMeta()
        mirrorPatchStatus()
        updateStatusPill()
    }

    var metaVersion = ""

    func refreshMeta() {
        let patch = enabled && patchRevision > 0 ? " · 补丁 r\(patchRevision)" : ""
        let running = officialRunning ? " · 运行中" : ""
        let text = "Claude " + (metaVersion.isEmpty ? "未知" : metaVersion) + patch + running
        metaLabel.stringValue = text
        patchMetaLabel.stringValue = text
    }

    /// 补丁页和概览页共享同一份状态文案。
    func mirrorPatchStatus() {
        patchStateLabel.stringValue = statusLabel.stringValue
        patchStateLabel.textColor = statusLabel.textColor
        patchMetaLabel.stringValue = metaLabel.stringValue
        if !statusLoaded {
            backupStateLabel.stringValue = "正在检测…"
        } else if !enabled {
            backupStateLabel.stringValue = "补丁未安装；安装后会自动保存完整官方备份。"
        } else if backupVerified {
            backupStateLabel.stringValue = "当前官方备份已校验，可用于恢复官方原版。"
        } else {
            backupStateLabel.stringValue = "官方备份校验异常；恢复和补丁更新已停用。"
        }
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
        label.invalidateIntrinsicContentSize()
        // 当前可见状态已经和文字是否为空一致时不用再动。
        if label.isHidden == text.isEmpty { return }
        animate { label.animator().isHidden = text.isEmpty }
    }

    func setFeatureHint(_ index: Int, text: String) {
        let label = featureHints[index]
        label.stringValue = text
        label.invalidateIntrinsicContentSize()
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
            if lastError == nil { setStatus(error, color: ClaudeTheme.warning) }
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
            // 默认开启、又没有本工具恢复记录的项不能从界面关闭；用悬停提示说清楚，
            // 否则一个“亮着但点不动”的开关看起来像坏了。
            featureSwitches[index].toolTip = !available
                ? "此版本没有这个设置。"
                : (on && !featureRestorable[index] && id != "code-auto-dream"
                    ? "这一项由 Claude 默认开启；本工具没有它的恢复记录，因此不从这里关闭。"
                    : featureDescriptions[index])
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
        refreshMeta()
    }

    func setSummary(_ text: String) {
        guard featureSummary.stringValue != text || featureSummary.isHidden != text.isEmpty else { return }
        featureSummary.stringValue = text
        animate { self.featureSummary.animator().isHidden = text.isEmpty }
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
        metaVersion = officialResult["version"] as? String ?? metaVersion
        applyOfficial(officialResult)
        applyCLI(result["cli"] as? [String: Any] ?? [:])
        applyFeatures(result["features"] as? [String: Any] ?? [:])
        finishInteraction()
        handlePendingRefresh()
        maybeLaunchClaude()
    }

    func complete(_ result: [String: Any], command: String = "status") {
        lastStatusAt = Date()
        metaVersion = result["version"] as? String ?? metaVersion
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

    func animate(_ duration: TimeInterval = 0.18, _ changes: () -> Void) {
        NSAnimationContext.runAnimationGroup { context in
            context.duration = duration
            context.timingFunction = CAMediaTimingFunction(controlPoints: 0.22, 1, 0.36, 1)
            context.allowsImplicitAnimation = true
            changes()
        }
    }
}

// MARK: - Claude 设置页视觉

/// 数值取自 Claude 3.6556.2 内置的 CDS 设计系统（desktop / comfortable 密度）：
/// --cds-h-control 2rem、--cds-pad-md .75rem、--cds-gap-xl 2.5rem、
/// --cds-font-size-body .875rem、--cds-font-size-title 1.375rem、--cds-switch-h 1.25rem。
/// 颜色取自同一份 gray/blue 色阶与 alpha 叠加规则，深浅色各一套。
enum ClaudeTheme {
    static func dynamic(_ light: NSColor, _ dark: NSColor) -> NSColor {
        NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
        }
    }

    static func rgb(_ value: UInt32, alpha: CGFloat = 1) -> NSColor {
        NSColor(srgbRed: CGFloat((value >> 16) & 0xFF) / 255,
                green: CGFloat((value >> 8) & 0xFF) / 255,
                blue: CGFloat(value & 0xFF) / 255,
                alpha: alpha)
    }

    // 尺寸与间距（pt）。
    enum Metric {
        static let controlHeight: CGFloat = 32      // --cds-h-control
        static let navWidth: CGFloat = 232
        static let rowPaddingY: CGFloat = 12        // --cds-pad-md
        static let rowLabelGap: CGFloat = 4         // gap-1
        static let groupGap: CGFloat = 40           // --cds-gap-xl
        static let headerGap: CGFloat = 16          // --cds-gap-md
        static let contentInsetX: CGFloat = 24      // px-xl
        static let contentInsetTop: CGFloat = 28
        static let switchWidth: CGFloat = 36        // 1.8 × --cds-switch-h
        static let switchHeight: CGFloat = 20
    }

    // surface-1 侧栏 / surface-2 内容面板；深色下内容比侧栏略亮。
    static let pane = dynamic(rgb(0xFFFFFF), rgb(0x1A1A19))
    // 图层背景用静态色：动态 NSColor 的 cgColor 在 CALayer 上会被解析成系统占位灰。
    static let paneLight = rgb(0xFFFFFF)
    static let paneDark = rgb(0x1A1A19)
    static let sidebarLight = rgb(0xFCFCFB)
    static let sidebarDark = rgb(0x151515)
    static let sidebar = dynamic(rgb(0xFCFCFB), rgb(0x151515))
    static let sidebarHover = dynamic(rgb(0x0B0B0B, alpha: 0.05), rgb(0xFFFFFF, alpha: 0.05))
    static let sidebarSelected = dynamic(rgb(0x0B0B0B, alpha: 0.10), rgb(0xFFFFFF, alpha: 0.10))
    static let searchFill = dynamic(rgb(0xFFFFFF), rgb(0xFFFFFF, alpha: 0.05))

    static let textPrimary = dynamic(rgb(0x0B0B0B), rgb(0xF0EFEC))
    static let textSecondary = dynamic(rgb(0x5F5E5A), rgb(0xC3C2B7))
    static let textMuted = dynamic(rgb(0x898781), rgb(0x6D6B67))
    static let border = dynamic(rgb(0x0B0B0B, alpha: 0.10), rgb(0xFFFFFF, alpha: 0.10))
    static let separator = dynamic(rgb(0x0B0B0B, alpha: 0.05), rgb(0xFFFFFF, alpha: 0.05))

    // --cds-role-accent-fill = blue-450；悬停取相邻一档。
    static let accent = rgb(0x2A78D6)
    static let accentHover = dynamic(rgb(0x256ABF), rgb(0x3987E5))
    static let switchOff = dynamic(rgb(0x0B0B0B, alpha: 0.20), rgb(0xFFFFFF, alpha: 0.10))
    static let switchOffHover = dynamic(rgb(0x0B0B0B, alpha: 0.35), rgb(0xFFFFFF, alpha: 0.20))
    static let switchKnob = rgb(0xFFFFFF)

    static let buttonFill = dynamic(rgb(0xEDECE8), rgb(0x2C2C2A))
    static let buttonHover = dynamic(rgb(0xE4E3DD), rgb(0x383835))
    static let buttonPressed = dynamic(rgb(0xD2D1C7), rgb(0x20201F))
    static let buttonBorder = dynamic(rgb(0x0B0B0B, alpha: 0.10), rgb(0xFFFFFF, alpha: 0.10))

    static let warning = dynamic(rgb(0x863311), rgb(0xEC835A))
    static let clay = rgb(0xD97757)
    static let claySoft = dynamic(rgb(0xFBF3EF), rgb(0xD97757, alpha: 0.18))
}

/// 官方界面用的是 Anthropic Sans 可变字体（随 Claude 安装包提供）。
/// 运行时注册并优先使用它，取不到时退回系统字体，保证工具单独运行时也不缺字。
enum ClaudeFont {
    private static var registered = false

    static func registerBundledFonts() {
        guard !registered else { return }
        registered = true
        let candidates = [
            "/Applications/Claude.app/Contents/Resources/fonts/AnthropicSans-Romans-Variable-25x258.ttf",
            NSHomeDirectory() + "/Applications/Claude.app/Contents/Resources/fonts/AnthropicSans-Romans-Variable-25x258.ttf",
        ]
        for path in candidates where FileManager.default.fileExists(atPath: path) {
            var error: Unmanaged<CFError>?
            _ = CTFontManagerRegisterFontsForURL(URL(fileURLWithPath: path) as CFURL, .process, &error)
            break
        }
    }

    private static let faces: [NSFont.Weight: String] = [
        .regular: "AnthropicSansVariable-TextRegular",
        .medium: "AnthropicSansVariable-TextMedium",
        .semibold: "AnthropicSansVariable-TextSemibold",
    ]

    static func ui(_ size: CGFloat, _ weight: NSFont.Weight = .regular) -> NSFont {
        registerBundledFonts()
        if let name = faces[weight], let font = NSFont(name: name, size: size) {
            return font
        }
        return .systemFont(ofSize: size, weight: weight)
    }

    // 与 CDS 字号一一对应：caption 12 / footnote 13 / body 14 / heading 15 / title 22。
    static func caption(_ weight: NSFont.Weight = .regular) -> NSFont { ui(12, weight) }
    static func footnote(_ weight: NSFont.Weight = .regular) -> NSFont { ui(13, weight) }
    static func body(_ weight: NSFont.Weight = .regular) -> NSFont { ui(14, weight) }
    static func heading(_ weight: NSFont.Weight = .semibold) -> NSFont { ui(15, weight) }
    static func title() -> NSFont { ui(22, .semibold) }
}

/// 左侧栏底色：直接绘制，滚动/截图/打印时都不会丢。
/// 左侧栏底色：图层化并用 updateLayer 上色。
/// 窗口里只要有一个子视图带图层，整棵子树就进入图层合成，`draw(_:)` 里的
/// 底色会在离屏/滚动合成时丢掉；0.3.34 踩过一次，这里必须保持 updateLayer。
final class SidebarBackgroundView: NSView {
    override var wantsUpdateLayer: Bool { true }

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func updateLayer() {
        let resolved = effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            ? ClaudeTheme.sidebarDark : ClaudeTheme.sidebarLight
        layer?.backgroundColor = resolved.cgColor
    }
}

/// 右侧面板底色：与侧栏一样走 updateLayer。
final class ContentBackgroundView: NSView {
    override var wantsUpdateLayer: Bool { true }

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func updateLayer() {
        // 动态色（NSColor(name:dynamicProvider:)）取 .cgColor 时拿不到当前外观，
        // 会退化成系统占位灰；这里先按当前外观取静态色再转 CGColor。
        let dark = effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        layer?.backgroundColor = (dark ? ClaudeTheme.paneDark : ClaudeTheme.paneLight).cgColor
    }
}

/// 普通滚动容器：一个裁剪层加一个文档视图，自己处理滚轮，不用 NSScrollView。
/// 需要的是「不透明、无材质、可裁剪」：设置页内容整块跟着滚，不需要系统滚动条。
/// （右侧面板曾整块发灰，原因不在这个容器，而是 HairlineView 用 dirtyRect
/// 填充，图层化后 dirtyRect 可能是整块图层，十几条分隔线叠成了灰雾。）
final class PlainScrollView: NSView {
    /// 裁剪层：普通 NSView + clipsToBounds，只负责裁掉视口外的内容。
    private let clip = FlippedView()
    private var offset: CGFloat = 0
    private weak var document: NSView?
    private var documentTop: NSLayoutConstraint?

    var documentView: NSView? {
        get { document }
        set {
            document?.removeFromSuperview()
            documentTop?.isActive = false
            document = newValue
            guard let newValue else { return }
            newValue.translatesAutoresizingMaskIntoConstraints = false
            clip.addSubview(newValue)
            // 文档位置用一条 top 约束的 constant 表示，而不是改 frame：
            // 文档本身受 Auto Layout 管理，直接改 frame 会被下一次布局还原。
            let top = newValue.topAnchor.constraint(equalTo: clip.topAnchor)
            documentTop = top
            NSLayoutConstraint.activate([
                newValue.leadingAnchor.constraint(equalTo: clip.leadingAnchor),
                newValue.trailingAnchor.constraint(equalTo: clip.trailingAnchor),
                top,
                newValue.widthAnchor.constraint(equalTo: clip.widthAnchor)
            ])
        }
    }

    var contentView: NSView { clip }

    override init(frame: NSRect) {
        super.init(frame: frame)
        clipsToBounds = true
        clip.translatesAutoresizingMaskIntoConstraints = false
        addSubview(clip)
        NSLayoutConstraint.activate([
            clip.leadingAnchor.constraint(equalTo: leadingAnchor),
            clip.trailingAnchor.constraint(equalTo: trailingAnchor),
            clip.topAnchor.constraint(equalTo: topAnchor),
            clip.bottomAnchor.constraint(equalTo: bottomAnchor)
        ])
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    private var maximumOffset: CGFloat {
        guard let document else { return 0 }
        return max(0, document.frame.height - bounds.height)
    }

    private func applyOffset() {
        guard document != nil else { return }
        // 只夹住实际画出来的位置，不改写 offset 本身：切页时文档高度会短暂变小，
        // 若把夹过的值写回去，刚设好的滚动目标就被抹成 0 了。
        let clamped = min(max(0, offset), maximumOffset)
        if documentTop?.constant != -clamped {
            documentTop?.constant = -clamped
        }
    }

    override func layout() {
        super.layout()
        applyOffset()
    }

    override func scrollWheel(with event: NSEvent) {
        let delta = event.hasPreciseScrollingDeltas ? event.scrollingDeltaY : event.deltaY * 10
        guard delta != 0 else { return }
        offset += delta
        applyOffset()
    }

    func scrollToTop() {
        offset = 0
        applyOffset()
    }

    /// 把文档坐标里的一段滚进视口，顶部留 8pt 呼吸。
    /// 目标位置存在 `offset` 里，布局完成、文档高度更新后由 `layout()` 再应用一次。
    func scrollToRect(_ rect: NSRect) {
        offset = rect.minY - 8
        applyOffset()
        #if PREVIEW
        print("[scroll] target=\(offset) max=\(maximumOffset) doc=\(document?.frame.height ?? -1) bounds=\(bounds.height)")
        #endif
    }
}

final class FlippedView: NSView {
    override var isFlipped: Bool { true }
}

/// 只用来接收初始焦点，避免搜索框一启动就出现蓝色焦点圈。
final class FocusRootView: NSView {
    override var acceptsFirstResponder: Bool { true }
}

/// 自绘搜索框：避免 NSSearchField 在浅色窗口里自动画出蓝色焦点圈。
final class SearchBoxView: NSView, NSTextFieldDelegate {
    let field = NSTextField()
    var onChange: (() -> Void)?
    private let icon = NSImageView()
    private var focused = false

    override init(frame: NSRect) {
        super.init(frame: frame)
        if let image = NSImage(systemSymbolName: "magnifyingglass", accessibilityDescription: nil) {
            icon.image = image
            icon.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 12, weight: .regular)
        }
        icon.contentTintColor = ClaudeTheme.textMuted
        icon.translatesAutoresizingMaskIntoConstraints = false

        field.isBordered = false
        field.isBezeled = false
        field.drawsBackground = false
        field.focusRingType = .none
        field.font = ClaudeFont.body()
        field.textColor = ClaudeTheme.textPrimary
        field.placeholderAttributedString = NSAttributedString(
            string: "搜索设置",
            attributes: [.foregroundColor: ClaudeTheme.textMuted,
                         .font: ClaudeFont.body()])
        field.delegate = self
        field.translatesAutoresizingMaskIntoConstraints = false

        addSubview(icon)
        addSubview(field)
        NSLayoutConstraint.activate([
            icon.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 10),
            icon.centerYAnchor.constraint(equalTo: centerYAnchor),
            icon.widthAnchor.constraint(equalToConstant: 16),
            icon.heightAnchor.constraint(equalToConstant: 16),
            field.leadingAnchor.constraint(equalTo: icon.trailingAnchor, constant: 7),
            field.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -10),
            field.centerYAnchor.constraint(equalTo: centerYAnchor)
        ])
        applyColors()
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func mouseDown(with event: NSEvent) {
        window?.makeFirstResponder(field)
    }

    func controlTextDidChange(_ obj: Notification) {
        onChange?()
    }

    func controlTextDidBeginEditing(_ obj: Notification) {
        focused = true
        applyColors()
    }

    func controlTextDidEndEditing(_ obj: Notification) {
        focused = false
        applyColors()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        applyColors()
    }

    override func draw(_ dirtyRect: NSRect) {
        let path = NSBezierPath(roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5), xRadius: 8, yRadius: 8)
        effectiveAppearance.performAsCurrentDrawingAppearance {
            ClaudeTheme.searchFill.setFill()
            path.fill()
            (focused ? ClaudeTheme.accent : ClaudeTheme.border).setStroke()
            path.lineWidth = focused ? 1.5 : 1
            path.stroke()
        }
    }

    private func applyColors() {
        needsDisplay = true
    }
}

/// 设置页左侧的一行：图标 + 标题，选中时灰底白字。
final class SidebarRowView: NSView {
    let id: String
    let titleText: String
    private let iconView = NSImageView()
    private let label = NSTextField(labelWithString: "")
    private let onSelect: (String) -> Void
    private var hovered = false
    private var tracking: NSTrackingArea?

    var isSelected = false {
        didSet { updateAppearance() }
    }

    init(id: String, title: String, symbol: String, onSelect: @escaping (String) -> Void) {
        self.id = id
        self.titleText = title
        self.onSelect = onSelect
        super.init(frame: .zero)
        wantsLayer = true
        layer?.cornerRadius = 7

        if let image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil) {
            iconView.image = image
            iconView.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 15, weight: .regular)
        }
        iconView.translatesAutoresizingMaskIntoConstraints = false
        iconView.contentTintColor = ClaudeTheme.textSecondary
        label.stringValue = title
        label.font = ClaudeFont.body()
        label.textColor = ClaudeTheme.textSecondary
        label.translatesAutoresizingMaskIntoConstraints = false
        addSubview(iconView)
        addSubview(label)
        NSLayoutConstraint.activate([
            iconView.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 8),
            iconView.centerYAnchor.constraint(equalTo: centerYAnchor),
            iconView.widthAnchor.constraint(equalToConstant: 20),
            iconView.heightAnchor.constraint(equalToConstant: 20),
            label.leadingAnchor.constraint(equalTo: iconView.trailingAnchor, constant: 8),
            label.trailingAnchor.constraint(lessThanOrEqualTo: trailingAnchor, constant: -8),
            label.centerYAnchor.constraint(equalTo: centerYAnchor)
        ])
        updateAppearance()
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking = tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(rect: bounds,
                                  options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
                                  owner: self, userInfo: nil)
        addTrackingArea(area)
        tracking = area
    }

    override func mouseEntered(with event: NSEvent) {
        hovered = true
        updateAppearance()
    }

    override func mouseExited(with event: NSEvent) {
        hovered = false
        updateAppearance()
    }

    override func mouseDown(with event: NSEvent) {
        onSelect(id)
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateAppearance()
    }

    override func draw(_ dirtyRect: NSRect) {
        // 选中/悬停底色直接绘制：滚动、截图和打印时都不会丢。
        let background: NSColor
        if isSelected {
            background = ClaudeTheme.sidebarSelected
        } else if hovered {
            background = ClaudeTheme.sidebarHover
        } else {
            background = .clear
        }
        guard background.alphaComponent > 0 else { return }
        effectiveAppearance.performAsCurrentDrawingAppearance {
            background.setFill()
            NSBezierPath(roundedRect: bounds, xRadius: 6, yRadius: 6).fill()
        }
    }

    private func updateAppearance() {
        // 动态颜色必须在外观上下文里解析，否则选中行可能取到另一种外观的取值，
        // 深色底上出现近黑文字、看起来像“灰掉了”。
        effectiveAppearance.performAsCurrentDrawingAppearance {
            let color = isSelected ? ClaudeTheme.textPrimary : ClaudeTheme.textSecondary
            label.textColor = color
            iconView.contentTintColor = color
        }
        label.font = ClaudeFont.body(isSelected ? .medium : .regular)
        needsDisplay = true
    }
}

/// 圆形图标按钮，用于右上角的刷新和关闭。
final class ClaudeIconButton: NSButton {
    private let symbolView = NSImageView()
    private let actionClosure: () -> Void
    private var hovered = false
    private var tracking: NSTrackingArea?

    init(symbol: String, accessibility: String, action: @escaping () -> Void) {
        self.actionClosure = action
        super.init(frame: .zero)
        isBordered = false
        bezelStyle = .regularSquare
        title = ""
        target = self
        self.action = #selector(performAction)
        setAccessibilityLabel(accessibility)
        toolTip = accessibility
        if let image = NSImage(systemSymbolName: symbol, accessibilityDescription: accessibility) {
            symbolView.image = image
            symbolView.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 12, weight: .medium)
        }
        symbolView.translatesAutoresizingMaskIntoConstraints = false
        symbolView.contentTintColor = ClaudeTheme.textSecondary
        addSubview(symbolView)
        NSLayoutConstraint.activate([
            symbolView.centerXAnchor.constraint(equalTo: centerXAnchor),
            symbolView.centerYAnchor.constraint(equalTo: centerYAnchor),
            symbolView.widthAnchor.constraint(equalToConstant: 18),
            symbolView.heightAnchor.constraint(equalToConstant: 18)
        ])
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    @objc private func performAction() { actionClosure() }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking = tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(rect: bounds,
                                  options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
                                  owner: self, userInfo: nil)
        addTrackingArea(area)
        tracking = area
    }

    override func mouseEntered(with event: NSEvent) {
        hovered = true
        updateAppearance()
    }

    override func mouseExited(with event: NSEvent) {
        hovered = false
        updateAppearance()
    }

    override var isEnabled: Bool {
        didSet { updateAppearance() }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateAppearance()
    }

    override func draw(_ dirtyRect: NSRect) {
        guard hovered, isEnabled else { return }
        effectiveAppearance.performAsCurrentDrawingAppearance {
            ClaudeTheme.sidebarHover.setFill()
            NSBezierPath(roundedRect: bounds, xRadius: 6, yRadius: 6).fill()
        }
    }

    private func updateAppearance() {
        symbolView.contentTintColor = isEnabled ? ClaudeTheme.textSecondary
            : ClaudeTheme.textMuted.withAlphaComponent(0.5)
        needsDisplay = true
    }
}

/// 分组之间的 1px 分隔线。直接绘制，滚动区域里也能稳定显示。
final class HairlineView: NSView {
    override var isOpaque: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        // 只填自己这一条。图层化之后 AppKit 给的 dirtyRect 可能是整块图层
        // （实测 768×540），直接 dirtyRect.fill() 会把 5% 白色铺满整个面板，
        // 叠十几层就是 #A7A7A6 的灰。这里始终用 bounds 与 dirtyRect 的交集。
        ClaudeTheme.separator.setFill()
        bounds.intersection(dirtyRect).fill()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
    }
}

/// Claude 设置页开关：38×22 胶囊轨道 + 白色圆钮，开启为系统蓝。
/// 直接绘制而不是用 CALayer：滚动区域里刚显示的分组也能可靠地画出来。
final class ClaudeSwitch: NSControl {
    var state: NSControl.StateValue = .off {
        didSet {
            guard state != oldValue else { return }
            animateKnob()
            setAccessibilityValue(state == .on)
        }
    }

    private var hovered = false
    private var tracking: NSTrackingArea?
    private var knobFraction: CGFloat = 0
    private var knobTimer: Timer?

    override init(frame: NSRect) {
        super.init(frame: frame)
        setAccessibilityRole(.checkBox)
        setAccessibilityLabel("开关")
        knobFraction = state == .on ? 1 : 0
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override var intrinsicContentSize: NSSize {
        NSSize(width: ClaudeTheme.Metric.switchWidth, height: ClaudeTheme.Metric.switchHeight)
    }

    override var isEnabled: Bool {
        didSet { needsDisplay = true }
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking = tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(rect: bounds,
                                  options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
                                  owner: self, userInfo: nil)
        addTrackingArea(area)
        tracking = area
    }

    override func mouseEntered(with event: NSEvent) {
        hovered = true
        needsDisplay = true
    }

    override func mouseExited(with event: NSEvent) {
        hovered = false
        needsDisplay = true
    }

    override func mouseDown(with event: NSEvent) {
        guard isEnabled else { return }
        window?.makeFirstResponder(self)
        state = state == .on ? .off : .on
        if let action = action {
            NSApp.sendAction(action, to: target, from: self)
        }
    }

    override var acceptsFirstResponder: Bool { isEnabled }

    override func keyDown(with event: NSEvent) {
        guard event.keyCode == 49 || event.keyCode == 36 else {
            super.keyDown(with: event)
            return
        }
        guard isEnabled else { return }
        state = state == .on ? .off : .on
        if let action = action {
            NSApp.sendAction(action, to: target, from: self)
        }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        // 官方开关：高 20pt、宽 1.8 倍，圆钮 16pt，两端各留 2pt。
        let height = ClaudeTheme.Metric.switchHeight
        let width = ClaudeTheme.Metric.switchWidth
        let track = NSRect(x: (bounds.width - width) / 2, y: (bounds.height - height) / 2,
                           width: width, height: height)
        let alpha: CGFloat = isEnabled ? 1 : 0.42
        let on = state == .on
        effectiveAppearance.performAsCurrentDrawingAppearance {
            let trackColor = on
                ? (hovered ? ClaudeTheme.accentHover : ClaudeTheme.accent)
                : (hovered ? ClaudeTheme.switchOffHover : ClaudeTheme.switchOff)
            trackColor.withAlphaComponent(alpha).setFill()
            NSBezierPath(roundedRect: track, xRadius: height / 2, yRadius: height / 2).fill()

            let knobSize = height - 4
            let travel = track.width - knobSize - 4
            let knobX = track.minX + 2 + travel * knobFraction
            let knobRect = NSRect(x: knobX, y: track.minY + 2,
                                  width: knobSize, height: knobSize)
            let shadow = NSShadow()
            shadow.shadowColor = NSColor.black.withAlphaComponent(0.22)
            shadow.shadowBlurRadius = 1.5
            shadow.shadowOffset = NSSize(width: 0, height: -0.5)
            NSGraphicsContext.saveGraphicsState()
            shadow.set()
            ClaudeTheme.switchKnob.withAlphaComponent(alpha).setFill()
            NSBezierPath(ovalIn: knobRect).fill()
            NSGraphicsContext.restoreGraphicsState()
        }
        if window?.firstResponder === self {
            let ring = NSBezierPath(roundedRect: bounds.insetBy(dx: -1, dy: -1),
                                    xRadius: bounds.height / 2 + 1,
                                    yRadius: bounds.height / 2 + 1)
            ClaudeTheme.accent.withAlphaComponent(0.60).setStroke()
            ring.lineWidth = 1.5
            ring.stroke()
        }
    }

    private func animateKnob() {
        knobTimer?.invalidate()
        let target: CGFloat = state == .on ? 1 : 0
        let start = knobFraction
        guard abs(target - start) > 0.001 else {
            knobFraction = target
            needsDisplay = true
            return
        }
        let startTime = Date()
        let timer = Timer(timeInterval: 1.0 / 60.0, repeats: true) { [weak self] timer in
            guard let self else {
                timer.invalidate()
                return
            }
            let t = min(1, Date().timeIntervalSince(startTime) / 0.18)
            // ease-out cubic：起步快、收尾稳，接近 macOS 开关的手感。
            let eased = 1 - pow(1 - t, 3)
            self.knobFraction = start + (target - start) * eased
            self.needsDisplay = true
            if t >= 1 {
                timer.invalidate()
                self.knobTimer = nil
                self.knobFraction = target
                self.needsDisplay = true
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        knobTimer = timer
    }
}

enum ClaudeButtonStyle { case primary, secondary, soft, ghost, ghostAccent }

/// Claude 设置页按钮：灰色圆角填充、白色文字；悬停与按下有轻微变化。
final class ClaudeButton: NSButton {
    let style: ClaudeButtonStyle
    private var hovered = false
    private var tracking: NSTrackingArea?

    init(title: String, style: ClaudeButtonStyle) {
        self.style = style
        super.init(frame: .zero)
        self.title = title
        isBordered = false
        bezelStyle = .regularSquare
        wantsLayer = true
        focusRingType = .none
        font = ClaudeFont.footnote(.medium)
        setButtonType(.momentaryPushIn)
        setContentHuggingPriority(.required, for: .horizontal)
        setContentCompressionResistancePriority(.required, for: .horizontal)
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override var intrinsicContentSize: NSSize {
        let width = (title as NSString).size(withAttributes: [.font: font ?? ClaudeFont.footnote()]).width
        return NSSize(width: ceil(width) + 30, height: 30)
    }

    override var isEnabled: Bool {
        didSet { needsDisplay = true }
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking = tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(rect: bounds,
                                  options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
                                  owner: self, userInfo: nil)
        addTrackingArea(area)
        tracking = area
    }

    override func mouseEntered(with event: NSEvent) {
        hovered = true
        needsDisplay = true
    }

    override func mouseExited(with event: NSEvent) {
        hovered = false
        needsDisplay = true
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        let pressed = cell?.isHighlighted == true
        let alpha: CGFloat = isEnabled ? 1 : 0.40
        let rect = bounds.insetBy(dx: 0.5, dy: 0.5)
        let radius: CGFloat = 8
        let path = NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
        var fill: NSColor
        var text: NSColor
        switch style {
        case .primary, .secondary, .soft:
            fill = pressed ? ClaudeTheme.buttonPressed : (hovered ? ClaudeTheme.buttonHover : ClaudeTheme.buttonFill)
            text = ClaudeTheme.textPrimary
        case .ghost, .ghostAccent:
            fill = hovered || pressed ? ClaudeTheme.buttonHover : .clear
            text = style == .ghostAccent ? ClaudeTheme.accent : ClaudeTheme.textSecondary
        }

        effectiveAppearance.performAsCurrentDrawingAppearance {
            let solidFill = fill.usingColorSpace(.sRGB) ?? fill
            solidFill.withAlphaComponent(solidFill.alphaComponent * alpha).setFill()
            path.fill()
            if style != .ghost && style != .ghostAccent {
                let solidBorder = ClaudeTheme.buttonBorder.usingColorSpace(.sRGB) ?? ClaudeTheme.buttonBorder
                solidBorder.withAlphaComponent(alpha).setStroke()
                path.lineWidth = 1
                path.stroke()
            }
            let solidText = text.usingColorSpace(.sRGB) ?? text
            let attributes: [NSAttributedString.Key: Any] = [
                .font: font ?? ClaudeFont.footnote(.medium),
                .foregroundColor: solidText.withAlphaComponent(alpha),
                .paragraphStyle: {
                    let style = NSMutableParagraphStyle()
                    style.alignment = .center
                    return style
                }(),
            ]
            let textSize = (title as NSString).size(withAttributes: attributes)
            let textRect = NSRect(x: 0, y: (bounds.height - textSize.height) / 2 - 0.5,
                                  width: bounds.width, height: textSize.height)
            (title as NSString).draw(in: textRect, withAttributes: attributes)
        }
        if window?.firstResponder === self {
            ClaudeTheme.accent.withAlphaComponent(0.55).setStroke()
            let ring = NSBezierPath(roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5),
                                    xRadius: radius + 1, yRadius: radius + 1)
            ring.lineWidth = 1.5
            ring.stroke()
        }
    }

    override var acceptsFirstResponder: Bool { isEnabled }
}

/// 账户栏里的小圆点；直接绘制，避免滚动容器/印刷渲染丢图层。
final class SidebarDotView: NSView {
    var color: NSColor = .clear {
        didSet { needsDisplay = true }
    }

    override func draw(_ dirtyRect: NSRect) {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            color.setFill()
            NSBezierPath(ovalIn: bounds).fill()
        }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
    }
}

/// 左侧栏底部的账户式状态栏：头像 + 两行文字 + 关于按钮。
final class SidebarAccountView: NSView {
    var onAbout: (() -> Void)?
    private let avatar = AccountAvatarView()
    private let title = NSTextField(labelWithString: "Claude 原生助手")
    private let statusDot = SidebarDotView()
    private let status = NSTextField(labelWithString: "检测中")
    private let gear: ClaudeIconButton
    private var tint: NSColor = ClaudeTheme.textMuted
    private var hovered = false
    private var tracking: NSTrackingArea?

    override init(frame: NSRect) {
        gear = ClaudeIconButton(symbol: "gearshape", accessibility: "关于") {}
        super.init(frame: frame)

        avatar.translatesAutoresizingMaskIntoConstraints = false
        title.font = ClaudeFont.footnote(.medium)
        title.textColor = ClaudeTheme.textPrimary
        title.translatesAutoresizingMaskIntoConstraints = false

        statusDot.translatesAutoresizingMaskIntoConstraints = false
        status.font = ClaudeFont.caption()
        status.textColor = ClaudeTheme.textSecondary
        status.lineBreakMode = .byTruncatingTail
        status.translatesAutoresizingMaskIntoConstraints = false

        gear.translatesAutoresizingMaskIntoConstraints = false
        gear.target = self
        gear.action = #selector(showAbout)
        addSubview(avatar)
        addSubview(title)
        addSubview(statusDot)
        addSubview(status)
        addSubview(gear)
        NSLayoutConstraint.activate([
            avatar.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 6),
            avatar.centerYAnchor.constraint(equalTo: centerYAnchor),
            avatar.widthAnchor.constraint(equalToConstant: 30),
            avatar.heightAnchor.constraint(equalToConstant: 30),
            title.leadingAnchor.constraint(equalTo: avatar.trailingAnchor, constant: 10),
            title.topAnchor.constraint(equalTo: topAnchor, constant: 9),
            title.trailingAnchor.constraint(lessThanOrEqualTo: gear.leadingAnchor, constant: -6),
            statusDot.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            statusDot.centerYAnchor.constraint(equalTo: status.centerYAnchor),
            statusDot.widthAnchor.constraint(equalToConstant: 6),
            statusDot.heightAnchor.constraint(equalToConstant: 6),
            status.leadingAnchor.constraint(equalTo: statusDot.trailingAnchor, constant: 5),
            status.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 3),
            status.trailingAnchor.constraint(lessThanOrEqualTo: gear.leadingAnchor, constant: -6),
            gear.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -2),
            gear.centerYAnchor.constraint(equalTo: centerYAnchor),
            gear.widthAnchor.constraint(equalToConstant: 24),
            gear.heightAnchor.constraint(equalToConstant: 24)
        ])
        update(text: "检测中", tint: ClaudeTheme.accent, pulsing: true)
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    @objc private func showAbout() { onAbout?() }

    override func mouseDown(with event: NSEvent) {
        onAbout?()
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking = tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(rect: bounds,
                                  options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
                                  owner: self, userInfo: nil)
        addTrackingArea(area)
        tracking = area
    }

    override func mouseEntered(with event: NSEvent) {
        hovered = true
        applyColors()
    }

    override func mouseExited(with event: NSEvent) {
        hovered = false
        applyColors()
    }

    func update(text: String, tint: NSColor, pulsing: Bool) {
        self.tint = tint
        status.stringValue = text
        status.textColor = ClaudeTheme.textSecondary
        statusDot.color = tint
        applyColors()
        if pulsing {
            startBreathing()
        } else {
            stopBreathing()
        }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        applyColors()
    }

    override func draw(_ dirtyRect: NSRect) {
        guard hovered else { return }
        effectiveAppearance.performAsCurrentDrawingAppearance {
            ClaudeTheme.sidebarHover.setFill()
            NSBezierPath(roundedRect: bounds, xRadius: 8, yRadius: 8).fill()
        }
    }

    private func applyColors() {
        needsDisplay = true
    }

    private var pulseTimer: Timer?

    private func startBreathing() {
        guard pulseTimer == nil else { return }
        let timer = Timer(timeInterval: 1.2, repeats: true) { [weak self] _ in
            guard let self else { return }
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.6
                context.allowsImplicitAnimation = true
                self.statusDot.animator().alphaValue = self.statusDot.alphaValue > 0.7 ? 0.35 : 1
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        pulseTimer = timer
    }

    private func stopBreathing() {
        pulseTimer?.invalidate()
        pulseTimer = nil
        statusDot.alphaValue = 1
    }
}

/// 账户栏左侧的圆形星芒头像。
final class AccountAvatarView: NSView {
    override func draw(_ dirtyRect: NSRect) {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            ClaudeTheme.claySoft.setFill()
            NSBezierPath(ovalIn: bounds).fill()
            ClaudeTheme.clay.setFill()
            let radius = min(bounds.width, bounds.height) * 0.28
            starburstPath(center: NSPoint(x: bounds.midX, y: bounds.midY),
                          radius: radius, rays: 12,
                          innerWidth: 0.065, outerWidth: 0.195).fill()
        }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
    }
}

/// 侧栏顶部的 12 光线星芒，用黏土橙填充。
final class StarburstLogoView: NSView {
    override func draw(_ dirtyRect: NSRect) {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            ClaudeTheme.clay.setFill()
            let radius = min(bounds.width, bounds.height) / 2 - 0.5
            starburstPath(center: NSPoint(x: bounds.midX, y: bounds.midY),
                          radius: radius, rays: 12,
                          innerWidth: 0.065, outerWidth: 0.195).fill()
        }
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
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

#if PREVIEW
extension AppDelegate {
    /// 预览构建用：把真实窗口渲染成 PNG，不依赖屏幕录制授权。
    func renderPreview(to path: String, dark: Bool) {
        NSApp.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        window.appearance = NSApp.appearance
        window.contentView?.layoutSubtreeIfNeeded()
        #if PREVIEW
        if let scroll = previewDelegate.scrollView {
            print("[render] docOrigin=\(scroll.documentView?.frame.origin.y ?? 0) docH=\(scroll.documentView?.frame.height ?? 0) boundsH=\(scroll.bounds.height)")
        }
        #endif
        window.displayIfNeeded()
        window.contentView?.subviews.forEach { $0.displayIfNeeded() }
        CATransaction.flush()
        guard let view = window.contentView else { return }
        // layer.render 会漏掉滚动容器里的自绘内容，PDF 路径是完整的；再从 PDF 光栅化出 PNG。
        let pdf = view.dataWithPDF(inside: view.bounds)
        try? pdf.write(to: URL(fileURLWithPath: path + ".pdf"))
        guard let provider = CGDataProvider(data: pdf as CFData),
              let document = CGPDFDocument(provider),
              let page = document.page(at: 1) else { return }
        let scale: CGFloat = 2
        let width = Int(view.bounds.width * scale)
        let height = Int(view.bounds.height * scale)
        guard let context = CGContext(data: nil, width: width, height: height,
                                      bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return }
        context.setFillColor(CGColor(red: 0, green: 0, blue: 0, alpha: 0))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        context.scaleBy(x: scale, y: scale)
        context.drawPDFPage(page)
        guard let image = context.makeImage() else { return }
        let rep = NSBitmapImageRep(cgImage: image)
        if let data = rep.representation(using: .png, properties: [:]) {
            try? data.write(to: URL(fileURLWithPath: path))
        }
    }
}

let previewDelegate = AppDelegate()
let previewApp = NSApplication.shared
previewApp.delegate = previewDelegate
let previewBase = ProcessInfo.processInfo.environment["PREVIEW_PATH"] ?? "/tmp/claude-native-preview"

/// 预览构建专用：抓真实窗口的离屏快照。
/// 注意：PDF 与 cacheDisplay 两条路径都会漏掉图层背景色，layer.render 对
/// 滚动层也不可靠；这里只用来核对文字排版，颜色一律以真机截图为准。
func debugCapture(to path: String) {
    let window = previewDelegate.window!
    print("NSApp=\(NSApp.effectiveAppearance.name.rawValue) window=\(window.effectiveAppearance.name.rawValue)")
    guard let view = window.contentView, let layer = view.layer else { return }
    let scale: CGFloat = 2
    let width = Int(view.bounds.width * scale)
    let height = Int(view.bounds.height * scale)
    guard let context = CGContext(data: nil, width: width, height: height,
                                  bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return }
    context.scaleBy(x: scale, y: scale)
    layer.render(in: context)
    guard let image = context.makeImage() else { return }
    let rep = NSBitmapImageRep(cgImage: image)
    if let data = rep.representation(using: .png, properties: [:]) {
        try? data.write(to: URL(fileURLWithPath: path))
        print("captured \(path) \(rep.pixelsWide)x\(rep.pixelsHigh)")
    }
}

func previewStep(_ index: Int) {
    // 每一页都渲染一张：侧栏分区多了以后，漏掉一页就等于漏掉一整个分组的核对。
    let sections = ["general", "patch", "system", "shortcuts", "about"]
    let names = ["dark", "patch", "system", "shortcuts", "about"]
    // 概览页里的功能分组是锚点，各滚一次，确认侧栏点分组能落到对应位置。
    let anchors = previewDelegate.featureGroupSections
    if index < sections.count {
        previewDelegate.selectSection(sections[index])
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) {
            previewDelegate.renderPreview(to: previewBase + "-" + names[index] + ".png",
                                          dark: true)
            previewStep(index + 1)
        }
    } else if index - sections.count < anchors.count {
        let anchor = anchors[index - sections.count]
        previewDelegate.jumpToFeatureGroup(anchor)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) {
            previewDelegate.renderPreview(to: previewBase + "-anchor-" + anchor + ".png",
                                          dark: true)
            previewStep(index + 1)
        }
    } else {
        previewDelegate.selectSection("general")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            previewDelegate.renderPreview(to: previewBase + "-light.png", dark: false)
            previewApp.terminate(nil)
        }
    }
}
DispatchQueue.main.asyncAfter(deadline: .now() + 5.0) {
    if ProcessInfo.processInfo.environment["DEBUG_CAPTURE"] != nil {
        NSApp.appearance = NSAppearance(named: .darkAqua)
        previewDelegate.window.appearance = NSApp.appearance
        previewDelegate.selectSection("general")
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
            debugCapture(to: previewBase + "-debug-dark.png")
            NSApp.appearance = NSAppearance(named: .aqua)
            previewDelegate.window.appearance = NSApp.appearance
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                debugCapture(to: previewBase + "-debug-light.png")
                previewApp.terminate(nil)
            }
        }
    } else {
        previewStep(0)
    }
}
previewApp.run()
#else
// 这个工具只有一扇窗，不需要系统恢复窗口；关掉之后，即使上次是被强制退出，
// macOS 也不会在启动时弹出「要恢复窗口吗」并挡住界面。
UserDefaults.standard.register(defaults: ["NSQuitAlwaysKeepsWindows": false])
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
#endif
