import AppKit
import CoreText

/// 界面与后端之间的协议版本。后端新增字段不需要改这里；
/// 只有后端改了字段含义或删字段时才加 1，界面据此提示更新助手。
enum BackendProtocol {
    static let version = 1
}

// 一个窗口 + 菜单栏入口的小工具。
// 所有真正的补丁操作都在内置 Python 后端里完成；这里只负责显示状态、
// 收集用户意图，并把后端返回的 JSON 转成中文提示。
//
// 单页窗口：顶部是身份与状态，下面是可滚动的设置列。
// 这个助手只有「全部开启」和「全部关闭」两种状态，所以不再逐项放开关，
// 只在首屏放一个总开关，下面如实列出它包含的每一项及其当前状态。
// 配色、开关、按钮和间距对齐 Claude 桌面端（深色近黑、蓝色开关、灰色按钮）。
class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var scrollView: NSScrollView!
    var scrollFade: ScrollFadeOverlay!
    var headerIdentity: IdentityStatusView!

    // 主开关：一次安装 / 恢复全部可用性补丁。
    let toggle = ClaudeSwitch()
    let statusLabel = NSTextField(wrappingLabelWithString: "正在检测…")
    let backupStateLabel = NSTextField(wrappingLabelWithString: "正在检测…")
    /// 「为官方更新做准备」下面那一行：官方有没有新版。
    let officialUpdateLabel = NSTextField(wrappingLabelWithString: "正在检查官方新版…")

    // 操作按钮。
    let openButton = ClaudeButton(title: "打开", style: .secondary)
    let refreshButton = ClaudeButton(title: "重新检测", style: .secondary)
    let updateButton = ClaudeButton(title: "更新补丁", style: .secondary)
    let prepareButton = ClaudeButton(title: "准备更新", style: .secondary)
    let permissionButton = ClaudeButton(title: "去系统设置", style: .secondary)
    let githubButton = ClaudeButton(title: "打开 GitHub", style: .secondary)
    var refreshIconButton: ClaudeIconButton!

    // 后端最近一次检测结果。
    var enabled = false
    var busy = false
    var officialRunning = false
    var officialSupported = false
    var backupVerified = false
    var officialMessage = "正在检测…"
    var recoveryRequired = false
    var updateAvailable = false
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

    // 官方新版提醒：只读官方更新渠道，不下载、不登录。
    var officialUpdateState = ""
    var officialUpdateInstalled = ""
    var officialUpdateLatest = ""
    var officialUpdateAdapted = false
    var officialUpdateCheckedAt = ""
    var officialUpdateStale = false
    var officialUpdateNote = ""
    var officialUpdateChecking = false
    /// 联网失败后至少等这么久再自动重试；否则每次窗口激活都会再连一次。
    var officialUpdateRetryAfter = Date.distantPast
    /// 用户主动点刷新时，即使缓存没过期也重新联网检查一次。
    var forceOfficialUpdateCheck = false

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
    // 总开关的语义：这些项要么一起开，要么一起关。每一项仍由后端单独校验、
    // 单独写恢复记录；界面只负责如实显示哪些生效、哪些被跳过。
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
    /// 每个功能分组对应的 id，顺序和 featureGroups 一一对应。
    let featureGroupSections = ["workbench", "network", "extensions", "code"]
    /// 每个功能分组右侧的状态文字：总开关统一管理，分组只讲清楚它包含什么、现在是什么状态。
    var featureGroupStatusLabels: [String: NSTextField] = [:]
    /// 分组行下面按需出现的说明（出错、此版本没有该设置）。
    var featureDetailLabels: [String: NSTextField] = [:]
    var featureHintLabels: [String: NSTextField] = [:]
    /// 每个分组里各项的出错说明，按功能下标存放，合并后写进上面那一行。
    var detailLines: [String: [Int: String]] = [:]
    /// Code 自动整理在列表里的位置；它走 CLI 后端，不是 3P 配置项。
    var codeAutoDreamIndex: Int { featureIDs.firstIndex(of: "code-auto-dream") ?? featureIDs.count - 1 }
    var featureEnabled: [Bool] = Array(repeating: false, count: 7)
    /// 当前 Claude 版本是否还有这个配置键；没有就在摘要里点名，而不是装作已开启。
    var featureAvailable: [Bool] = Array(repeating: true, count: 7)
    // 只在出错或尚未适配时显示，平时留空，避免每行都挂一句说明。
    let featureSummary = NSTextField(wrappingLabelWithString: "")
    /// 总开关刚做完时那一句“开了几项、跳过几项”，由下一次检测写进 featureSummary。
    var switchAllSummary = ""
    /// 总开关刚开启：等重新检测回来，确认需要重新授权时再跳系统设置。
    var pendingPermissionPrompt = false

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

        // 主窗口：透明标题栏、全尺寸内容，单页设置列直接延伸到交通灯下面。
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Claude 原生助手"
        window.isReleasedWhenClosed = false
        // 窗口状态不参与系统恢复：强制退出后 macOS 的「要恢复窗口吗」弹窗
        // 会在完成启动之前弹出，把这个只有一扇窗的工具挡在外面。
        window.isRestorable = false
        window.restorationClass = nil
        window.minSize = NSSize(width: 620, height: 560)
        window.backgroundColor = ClaudeTheme.pane
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.styleMask.insert(.fullSizeContentView)
        // 侧栏去掉后窗口变窄，旧的窗口尺寸记录不再适用，换一个新的键。
        let savedKey = "NSWindow Frame ClaudeNativeUnlockSinglePageWindow"
        let savedFrameAtLaunch = UserDefaults.standard.string(forKey: savedKey)
        window.setFrameAutosaveName("ClaudeNativeUnlockSinglePageWindow")
        if savedFrameAtLaunch == nil {
            window.setContentSize(NSSize(width: 760, height: 820))
            window.center()
        }

        let root = FocusRootView()
        root.wantsLayer = true
        window.contentView = root
        let pane = buildContentPane()
        pane.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(pane)
        NSLayoutConstraint.activate([
            pane.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            pane.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            pane.topAnchor.constraint(equalTo: root.topAnchor),
            pane.bottomAnchor.constraint(equalTo: root.bottomAnchor)
        ])
        window.contentMinSize = NSSize(width: 620, height: 560)

        if !NSScreen.screens.contains(where: { $0.visibleFrame.intersects(window.frame) }) {
            window.center()
        }
        refreshHeader()
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
                self.window.setContentSize(NSSize(width: 760, height: 820))
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

    // MARK: - 主界面

    /// 顶部身份栏：星芒头像、名称和实时状态，右侧只有一个刷新图标。
    /// 原来在侧栏左下角的那一块整体搬到这里；窗口里不再有侧栏。
    /// 右上角不再放关闭按钮，也不再另放一个转圈指示器：加载时由刷新图标自己旋转。
    func buildHeader() -> NSView {
        let header = NSView()
        header.translatesAutoresizingMaskIntoConstraints = false

        headerIdentity = IdentityStatusView()
        headerIdentity.translatesAutoresizingMaskIntoConstraints = false
        header.addSubview(headerIdentity)

        refreshIconButton = ClaudeIconButton(symbol: "arrow.clockwise",
                                             accessibility: "重新检测") { [weak self] in
            self?.refresh()
        }
        refreshIconButton.translatesAutoresizingMaskIntoConstraints = false
        header.addSubview(refreshIconButton)

        let line = HairlineView()
        line.translatesAutoresizingMaskIntoConstraints = false
        header.addSubview(line)

        NSLayoutConstraint.activate([
            // 42pt 是给交通灯留的高度；身份块本身 40pt 高，与右侧图标同一条基线。
            headerIdentity.leadingAnchor.constraint(equalTo: header.leadingAnchor,
                                                    constant: ClaudeTheme.Metric.contentInsetX),
            headerIdentity.topAnchor.constraint(equalTo: header.topAnchor, constant: 42),
            headerIdentity.heightAnchor.constraint(equalToConstant: 40),
            headerIdentity.trailingAnchor.constraint(lessThanOrEqualTo: refreshIconButton.leadingAnchor,
                                                     constant: -12),
            refreshIconButton.trailingAnchor.constraint(equalTo: header.trailingAnchor, constant: -16),
            refreshIconButton.centerYAnchor.constraint(equalTo: headerIdentity.centerYAnchor),
            refreshIconButton.widthAnchor.constraint(equalToConstant: 28),
            refreshIconButton.heightAnchor.constraint(equalToConstant: 28),
            line.leadingAnchor.constraint(equalTo: header.leadingAnchor),
            line.trailingAnchor.constraint(equalTo: header.trailingAnchor),
            line.bottomAnchor.constraint(equalTo: header.bottomAnchor),
            line.heightAnchor.constraint(equalToConstant: 1)
        ])
        return header
    }

    func buildContentPane() -> NSView {
        let pane = ContentBackgroundView()
        let header = buildHeader()
        header.translatesAutoresizingMaskIntoConstraints = false
        pane.addSubview(header)

        // 用系统 NSScrollView，而不是自己解释滚轮事件：方向、惯性、橡皮筋回弹
        // 和触控板手势都交给 AppKit，滚动只移动裁剪层，不再每帧触发布局。
        let scroll = NSScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        scroll.scrollerStyle = .overlay
        scroll.horizontalScrollElasticity = .none
        scroll.automaticallyAdjustsContentInsets = false
        scroll.contentInsets = NSEdgeInsets(top: 0, left: 0, bottom: 20, right: 0)
        scrollView = scroll
        pane.addSubview(scroll)

        let fade = ScrollFadeOverlay()
        fade.translatesAutoresizingMaskIntoConstraints = false
        pane.addSubview(fade)
        scrollFade = fade

        NSLayoutConstraint.activate([
            header.leadingAnchor.constraint(equalTo: pane.leadingAnchor),
            header.trailingAnchor.constraint(equalTo: pane.trailingAnchor),
            header.topAnchor.constraint(equalTo: pane.topAnchor),
            header.heightAnchor.constraint(equalToConstant: 94),
            scroll.leadingAnchor.constraint(equalTo: pane.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: pane.trailingAnchor),
            scroll.topAnchor.constraint(equalTo: header.bottomAnchor),
            scroll.bottomAnchor.constraint(equalTo: pane.bottomAnchor),
            fade.leadingAnchor.constraint(equalTo: scroll.leadingAnchor),
            fade.trailingAnchor.constraint(equalTo: scroll.trailingAnchor),
            fade.topAnchor.constraint(equalTo: scroll.topAnchor),
            fade.bottomAnchor.constraint(equalTo: scroll.bottomAnchor)
        ])

        let doc = FlippedView()
        doc.translatesAutoresizingMaskIntoConstraints = false
        scroll.documentView = doc
        scroll.contentView.postsBoundsChangedNotifications = true
        NotificationCenter.default.addObserver(
            self, selector: #selector(scrollBoundsChanged(_:)),
            name: NSView.boundsDidChangeNotification, object: scroll.contentView)

        let content = NSStackView()
        content.orientation = .vertical
        content.alignment = .leading
        content.distribution = .fill
        content.spacing = 0
        // 内容短的时候不能被拉满视口，否则分组之间的行距会被撑开。
        content.setContentHuggingPriority(.required, for: .vertical)
        content.translatesAutoresizingMaskIntoConstraints = false
        doc.addSubview(content)
        NSLayoutConstraint.activate([
            doc.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            doc.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
            doc.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor),
            content.leadingAnchor.constraint(equalTo: doc.leadingAnchor,
                                             constant: ClaudeTheme.Metric.contentInsetX),
            content.trailingAnchor.constraint(equalTo: doc.trailingAnchor,
                                              constant: -ClaudeTheme.Metric.contentInsetX),
            content.topAnchor.constraint(equalTo: doc.topAnchor, constant: 20),
            content.bottomAnchor.constraint(equalTo: doc.bottomAnchor, constant: -32)
        ])

        let page = buildSettingsPage()
        content.addArrangedSubview(page)
        page.widthAnchor.constraint(equalTo: content.widthAnchor).isActive = true
        return pane
    }

    /// 单页设置：总开关、它包含的功能、维护、系统授权和关于。
    /// 每一项都只有一个事实，没有逐项开关——这个助手只有全开和全关两种状态。
    func buildSettingsPage() -> NSView {
        configureDynamicLabel(statusLabel)
        configureDynamicLabel(backupStateLabel)

        let master = settingsRow(
            title: "全部功能",
            descriptionView: statusLabel,
            control: toggle)
        toggle.target = self
        toggle.action = #selector(switchChanged)
        toggle.setAccessibilityLabel("全部功能解锁开关")
        toggle.isEnabled = false


        updateButton.target = self
        updateButton.action = #selector(confirmUpdate)
        updateButton.toolTip = "把补丁更新到最新 revision；只保留最新版和当前官方备份。"
        let updateRow = settingsRow(
            title: "更新补丁",
            description: "安装最新适配 revision。旧补丁会清理，只保留当前官方备份。",
            control: updateButton)

        prepareButton.target = self
        prepareButton.action = #selector(prepareUpdate)
        configureDynamicLabel(officialUpdateLabel)
        let prepareRow = settingsRow(
            title: "为官方更新做准备",
            description: "恢复完整官方签名和官方文件，供 Claude 官方更新器校验。备份、配置和会话都会保留。",
            extraViews: [officialUpdateLabel],
            control: prepareButton)

        let backupRow = settingsRow(title: "官方备份", descriptionView: backupStateLabel)

        openButton.target = self
        openButton.action = #selector(openClaude)
        let openRow = settingsRow(
            title: "打开 Claude",
            description: "启动已安装的 Claude 应用。",
            control: openButton)

        refreshButton.target = self
        refreshButton.action = #selector(refresh)
        let refreshRow = settingsRow(
            title: "重新检测",
            description: "重新核对补丁、官方备份与配置状态。",
            control: refreshButton)

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

        githubButton.target = self
        githubButton.action = #selector(openGitHub)
        let githubRow = settingsRow(
            title: "源码与发布",
            description: "独立开源项目，与 Anthropic 无隶属关系；只分发自有的工具代码，不含 Claude 程序或用户数据。",
            control: githubButton)
        let appVersionRow = settingsRow(
            title: "助手版本",
            description: "Claude 原生助手 " + assistantVersion + " (" + assistantBuild + ")。")
        let dataRow = settingsRow(
            title: "数据位置",
            description: "备份与恢复记录保存在 ~/Library/Application Support/Claude 原生助手/，请勿手动删除。")

        featureSummary.font = ClaudeFont.caption()
        featureSummary.textColor = ClaudeTheme.warning
        featureSummary.isHidden = true
        featureSummary.maximumNumberOfLines = 3
        featureSummary.lineBreakMode = .byWordWrapping

        let groups: [NSView] = [
            featureSummary,
            settingsGroup("解锁", rows: [master]),
            settingsGroup("包含的功能", rows: featureGroups.indices.map { featureGroupRow($0) }),
            settingsGroup("维护", rows: [backupRow, updateRow, prepareRow, openRow, refreshRow]),
            settingsGroup("系统授权", rows: [permissionRow, reviewRow, browserRow]),
            settingsGroup("关于", rows: [appVersionRow, githubRow, dataRow])
        ]
        return section(groups,
                       intro: "总开关一次处理 Computer Use、原生配置与 Code 自动整理。动作授权、拒绝与中断仍由 Claude 处理。")
    }

    /// 一个功能分组的一行：组名 + 它包含的功能 + 当前状态。
    /// 总开关统一管理，所以这里没有开关，只有事实；出错时才多一行说明。
    func featureGroupRow(_ index: Int) -> NSView {
        let entry = featureGroups[index]
        let names = entry.1.compactMap { id in
            featureIDs.firstIndex(of: id).map { featureTitles[$0] }
        }.joined(separator: "、")
        let status = ClaudeFont.label("检测中", font: ClaudeFont.footnote())
        status.textColor = ClaudeTheme.textMuted
        status.alignment = .right
        status.setContentHuggingPriority(.required, for: .horizontal)
        status.setContentCompressionResistancePriority(.required, for: .horizontal)
        featureGroupStatusLabels[entry.0] = status

        let detail = NSTextField(wrappingLabelWithString: "")
        detail.font = ClaudeFont.caption()
        detail.textColor = ClaudeTheme.warning
        detail.maximumNumberOfLines = 4
        detail.lineBreakMode = .byWordWrapping
        detail.isHidden = true
        featureDetailLabels[entry.0] = detail

        let hint = NSTextField(wrappingLabelWithString: "")
        hint.font = ClaudeFont.caption()
        hint.textColor = ClaudeTheme.textMuted
        hint.maximumNumberOfLines = 3
        hint.lineBreakMode = .byWordWrapping
        hint.isHidden = true
        featureHintLabels[entry.0] = hint

        let title = ClaudeFont.label(entry.0, font: ClaudeFont.body())
        title.textColor = ClaudeTheme.textPrimary
        let desc = ClaudeFont.label(names, font: ClaudeFont.body())
        desc.textColor = ClaudeTheme.textMuted
        desc.maximumNumberOfLines = 0
        desc.lineBreakMode = .byWordWrapping
        desc.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return settingsRow(titleView: title, descriptionView: desc,
                           extraViews: [detail, hint], control: status)
    }

    /// 某个功能属于哪个分组；说明文字挂在该分组那一行下面。
    func groupTitle(forFeature index: Int) -> String? {
        guard featureIDs.indices.contains(index) else { return nil }
        return featureGroups.first(where: { $0.1.contains(featureIDs[index]) })?.0
    }

    /// 把每个分组里各项的状态汇总成一句话，不再逐项放开关。
    func updateFeatureGroupStatus() {
        for entry in featureGroups {
            guard let label = featureGroupStatusLabels[entry.0] else { continue }
            let indexes = entry.1.compactMap { featureIDs.firstIndex(of: $0) }
            let total = indexes.count
            let on = indexes.filter { featureEnabled[$0] && featureAvailable[$0] }.count
            let missing = indexes.filter { !featureAvailable[$0] }.count
            let text: String
            let color: NSColor
            if total == 0 {
                text = ""
                color = ClaudeTheme.textMuted
            } else if missing == total {
                text = "此版本没有"
                color = ClaudeTheme.warning
            } else if on == total {
                text = "已开启"
                color = ClaudeTheme.textSecondary
            } else if on == 0 {
                text = missing > 0 ? "未开启 · \(missing) 项不可用" : "未开启"
                color = missing > 0 ? ClaudeTheme.warning : ClaudeTheme.textMuted
            } else {
                text = "部分开启 · \(on)/\(total)"
                color = ClaudeTheme.warning
            }
            label.stringValue = text
            label.textColor = color
            // 悬停能看到逐项事实：开着的是不是本工具改的、关掉时会不会还原。
            // 逐项状态仍然保留，只是不再各占一个开关。
            label.toolTip = indexes.map { index -> String in
                let state: String
                if !featureAvailable[index] {
                    state = "此版本没有这个设置"
                } else if featureEnabled[index] {
                    state = featureRestorable[index] ? "已开启（关闭时还原为官方原值）" : "已开启（不是本工具改的，关闭时不还原）"
                } else {
                    state = "未开启"
                }
                return featureTitles[index] + "：" + state
            }.joined(separator: "\n")
        }
    }

    @objc func scrollBoundsChanged(_ notification: Notification) {
        updateScrollFade()
    }

    func updateScrollFade() {
        guard let scroll = scrollView, let fade = scrollFade else { return }
        let offset = scroll.contentView.bounds.origin.y
        let maximum = max(0, (scroll.documentView?.frame.height ?? 0)
                             - scroll.contentView.bounds.height)
        fade.update(offset: offset, maximum: maximum)
    }

    // MARK: - 设置行与分组

    func section(_ groups: [NSView], intro: String) -> NSView {
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.distribution = .fill
        stack.spacing = 0
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
                // 官方分组之间是 40pt 空白。
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
        let heading = ClaudeFont.label(title, font: ClaudeFont.heading())
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
        settingsRow(title: title, description: description, extraViews: [], control: control)
    }

    func settingsRow(title: String, description: String, extraViews: [NSView],
                     control: NSView? = nil) -> NSView {
        let titleLabel = ClaudeFont.label(title, font: ClaudeFont.body())
        titleLabel.textColor = ClaudeTheme.textPrimary
        let desc = ClaudeFont.label(description, font: ClaudeFont.body())
        desc.textColor = ClaudeTheme.textMuted
        desc.maximumNumberOfLines = 0
        desc.lineBreakMode = .byWordWrapping
        desc.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return settingsRow(titleView: titleLabel, descriptionView: desc,
                           extraViews: extraViews, control: control)
    }

    func settingsRow(title: String, descriptionView: NSView, control: NSView? = nil) -> NSView {
        let titleLabel = ClaudeFont.label(title, font: ClaudeFont.body())
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

    @objc func refresh() {
        // 用户主动刷新时，官方新版提醒也重新联网查一次，不受缓存期限限制。
        guard !busy, !quittingClaude else { return }
        forceOfficialUpdateCheck = true
        execute("status-all", backend: "all")
    }
    @objc func openClaude() { execute("launch") }
    @objc func prepareUpdate() { confirmPrepareUpdate() }

    @objc func switchChanged() {
        // 总开关：一次安装或恢复全部可用性补丁。界面先还原显示，
        // 真正的状态以后端返回的检测结果为准，不靠本地猜测。
        let enabling = toggle.state == .on
        toggle.state = enabled ? .on : .off
        withClaudeQuitIfNeeded(enabling ? "开启全部功能" : "恢复官方原版",
                               relaunch: true) {
            self.execute(enabling ? "enable-all" : "restore-all", backend: "all")
        }
    }

    @objc func openGitHub() {
        if let url = URL(string: "https://github.com/canner0725-byte/Claude-Native-Unlock") {
            NSWorkspace.shared.open(url)
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

    /// 滚动日志：后端出错时写入一行，供“导出诊断”使用。
    ///
    /// 只写命令、耗时、退出码和 stderr 末尾，不写配置内容、密钥或 API 地址。
    /// 单文件超过 512 KB 时轮换到 operations.log.1，最多保留两份。
    func appendLog(_ line: String) {
        let file = storageURL.appendingPathComponent("logs/operations.log")
        let manager = FileManager.default
        try? manager.createDirectory(at: file.deletingLastPathComponent(),
                                     withIntermediateDirectories: true)
        let stamp = ISO8601DateFormatter().string(from: Date())
        let text = "\(stamp) \(line)\n"
        if let size = try? manager.attributesOfItem(atPath: file.path)[.size] as? Int,
           size > 512 * 1024 {
            let rotated = file.deletingLastPathComponent().appendingPathComponent("operations.log.1")
            try? manager.removeItem(at: rotated)
            try? manager.moveItem(at: file, to: rotated)
        }
        if let handle = try? FileHandle(forWritingTo: file) {
            handle.seekToEndOfFile()
            handle.write(Data(text.utf8))
            try? handle.close()
        } else {
            try? Data(text.utf8).write(to: file)
        }
    }

    /// 后端崩溃时 stderr 是唯一的线索；截断到最后的 4 KB 并去掉换行。
    func stderrTail(_ data: Data) -> String {
        let slice = data.suffix(4096)
        let text = String(decoding: slice, as: UTF8.self)
            .replacingOccurrences(of: "\n", with: " ⏎ ")
            .trimmingCharacters(in: .whitespaces)
        return text
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
        setLoading(true)
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
        setLoading(false)
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
        setLoading(false)
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

    /// 顶部身份栏右侧的实时状态；原来在侧栏左下角，现在并入主界面。
    func updateStatusPill() {
        guard headerIdentity != nil else { return }
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
        headerIdentity.update(text: text, tint: tint, pulsing: pulsing)
    }

    /// 身份栏的版本摘要：Claude 版本、补丁 revision 和运行状态。
    func refreshHeader() {
        guard headerIdentity != nil else { return }
        headerIdentity.updateDetail(versionSummary)
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

        setLoading(true)
        statusLabel.textColor = ClaudeTheme.textSecondary
        if command == "enable-all" {
            statusLabel.stringValue = "正在校验、备份并开启全部功能…"
        } else if command == "restore-all" {
            statusLabel.stringValue = "正在逐项恢复官方原版…"
        } else if backend == "all" {
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
        updateBackupState()
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
            // 这三条是后端的顶层命令，不带后端名前缀。
            if ["status-all", "enable-all", "restore-all"].contains(command) {
                process.arguments = [command]
            } else {
                process.arguments = [backend, command] + (feature.map { [$0] } ?? [])
            }
            process.currentDirectoryURL = self.storageURL
            let output = Pipe()
            let errors = Pipe()
            process.standardOutput = output
            // 后端的 Python traceback、PyInstaller 启动错误只会走 stderr；
            // 丢掉它等于把唯一的排查线索也丢掉。
            process.standardError = errors

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
            var failure: String?
            let started = Date()
            do {
                try process.run()
                if command == "status" || command == "status-all" {
                    DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 30, execute: deadline)
                }
                try output.fileHandleForWriting.close()
                try errors.fileHandleForWriting.close()
                // 两个管道要并行读：只读 stdout 时，stderr 写满缓冲区会互相卡住。
                var errorData = Data()
                let errorGroup = DispatchGroup()
                errorGroup.enter()
                DispatchQueue.global(qos: .utility).async {
                    errorData = errors.fileHandleForReading.readDataToEndOfFile()
                    errorGroup.leave()
                }
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                errorGroup.wait()
                if let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    result = value
                } else if !data.isEmpty {
                    failure = "后端返回的内容无法解析。"
                    self.appendLog("decode-failed \(backend) \(command) bytes=\(data.count) "
                                   + "stderr=\(self.stderrTail(errorData))")
                }
                let status = process.terminationStatus
                if result["error"] == nil {
                    self.appendLog("ok \(backend) \(command) exit=\(status) "
                                   + "ms=\(Int(Date().timeIntervalSince(started) * 1000))")
                } else {
                    self.appendLog("error \(backend) \(command) exit=\(status) "
                                   + "message=\(result["error"] as? String ?? "") "
                                   + "stderr=\(self.stderrTail(errorData))")
                }
                if let failure { result = ["error": failure] }
            } catch {
                self.appendLog("launch-failed \(backend) \(command) \(error.localizedDescription)")
                result = ["error": "无法运行本地补丁工具。"]
            }

            deadline.cancel()
            timeoutLock.lock()
            let timedOut = detectionTimedOut
            timeoutLock.unlock()
            if timedOut {
                self.appendLog("timeout \(backend) \(command) after=30s")
                result = ["error": "检测超时（30 秒），已取消这次只读检测；请重新检测。"]
            }

            // 协议版本：后端新增字段时界面照常工作；界面比后端新时明确提示，
            // 而不是把“不认识”显示成错误状态。
            if let version = result["protocol"] as? Int {
                if version > BackendProtocol.version {
                    result = ["error": "内置补丁组件版本比界面新；请更新助手后再操作。"]
                }
            } else if result["error"] == nil {
                // 老后端不带协议号：照常使用，但记一笔，方便排查。
                self.appendLog("no-protocol \(backend) \(command)")
            }

            let finished = result
            DispatchQueue.main.async { completion(finished) }
        }
    }

    func route(_ result: [String: Any], command: String, backend: String) {
        recordOperation(command, backend: backend, result: result)
        if command == "enable-all" || command == "restore-all" {
            completeSwitchAll(result, enabling: command == "enable-all")
        } else if backend == "all" {
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
        setLoading(false)
        refreshButton.isEnabled = true
        refreshButtons()
        updateStatusItem()
    }

    /// 加载状态只体现在右上角那一个图标上：处理中让箭头转圈，结束后停下。
    /// 右上角没有单独的转圈指示器，也没有关闭按钮。
    func setLoading(_ loading: Bool) {
        refreshIconButton?.setSpinning(loading)
        refreshIconButton?.toolTip = loading ? "正在检测…" : "重新检测"
        refreshIconButton?.setAccessibilityLabel(loading ? "正在检测" : "重新检测")
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
        // 总开关只要求正式版可用：个别功能在当前版本不存在时由后端跳过并如实回报，
        // 不会因为一项不可用就把整个开关锁死。
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
            updateBackupState()
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
        refreshHeader()
        updateBackupState()
        updateStatusPill()
    }

    var metaVersion = ""

    /// 版本摘要只在顶部身份栏显示一处，不在下面重复。
    var versionSummary: String {
        let patch = enabled && patchRevision > 0 ? " · 补丁 r\(patchRevision)" : ""
        let running = officialRunning ? " · 运行中" : ""
        return "Claude " + (metaVersion.isEmpty ? "未知" : metaVersion) + patch + running
    }

    func updateBackupState() {
        if !statusLoaded {
            backupStateLabel.stringValue = "正在检测…"
        } else if !enabled {
            backupStateLabel.stringValue = "补丁未安装；开启全部功能时会自动保存完整官方备份。"
        } else if backupVerified {
            backupStateLabel.stringValue = "当前官方备份已校验，可用于恢复官方原版。"
        } else {
            backupStateLabel.stringValue = "官方备份校验异常；恢复和补丁更新已停用。"
        }
    }

    /// 官方新版提醒：只读官方更新渠道，不下载、不登录、不带账号。
    /// 文案分四种：有新版、已是最新、还没查到、读不到本机版本；
    /// 联网失败时保留上次结论并说明原因，不把「没查到」说成「已是最新」。
    func applyOfficialUpdate(_ result: [String: Any]) {
        officialUpdateState = result["state"] as? String ?? "unknown"
        officialUpdateInstalled = result["installed"] as? String ?? ""
        officialUpdateLatest = result["latest"] as? String ?? ""
        officialUpdateAdapted = result["adapted"] as? Bool ?? false
        officialUpdateCheckedAt = result["checkedAt"] as? String ?? ""
        officialUpdateStale = result["stale"] as? Bool ?? false
        officialUpdateNote = result["error"] as? String ?? ""
        updateOfficialUpdateLabel()
    }

    func updateOfficialUpdateLabel() {
        var text: String
        var color = ClaudeTheme.textMuted
        switch officialUpdateState {
        case "available":
            if officialUpdateAdapted {
                text = "官方已有新版 " + officialUpdateLatest + "（本机 " + officialUpdateInstalled +
                    "）。可以先准备更新，再在 Claude 里安装；装好后回到这里重新检测。"
                color = ClaudeTheme.accent
            } else {
                text = "官方已有新版 " + officialUpdateLatest + "（本机 " + officialUpdateInstalled +
                    "）。这个版本还没适配；更新后本工具不会修改它，等适配发布后再开启。"
                color = ClaudeTheme.warning
            }
        case "current":
            text = "官方没有比 " + officialUpdateInstalled + " 更新的版本。"
        case "unknown":
            text = "读不到本机 Claude 版本；重新检测后再看官方新版。"
        default:
            text = "还没检查过官方新版。"
        }
        if !officialUpdateNote.isEmpty {
            text += " " + officialUpdateNote
            color = ClaudeTheme.warning
        } else if officialUpdateState == "available" && officialUpdateStale {
            text += " 这是上次的检查结果，稍后会再查一次。"
        }
        guard officialUpdateLabel.stringValue != text else {
            officialUpdateLabel.textColor = color
            return
        }
        officialUpdateLabel.stringValue = text
        officialUpdateLabel.textColor = color
    }

    func applyCLI(_ result: [String: Any]) {
        let index = codeAutoDreamIndex
        featureEnabled[index] = result["enabled"] as? Bool ?? false
        setFeatureDetail(index, detail: result["error"] as? String ?? "")
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

    /// 分组行下面的补充说明只在出错时出现；正常状态保持一行一个分组。
    /// 同一分组里多项出错时合成一行，不重复堆叠。
    func setFeatureDetail(_ index: Int, detail: String) {
        guard let group = groupTitle(forFeature: index), let label = featureDetailLabels[group] else { return }
        var lines = detailLines[group] ?? [:]
        lines[index] = detail
        detailLines[group] = lines
        // 同一分组里多项出错时合成一段，带上各自的功能名。
        let text = featureIDs.indices.compactMap { key -> String? in
            guard groupTitle(forFeature: key) == group, let value = lines[key], !value.isEmpty else { return nil }
            return featureTitles[key] + "：" + value
        }.joined(separator: "\n")
        label.stringValue = text
        label.invalidateIntrinsicContentSize()
        // 当前可见状态已经和文字是否为空一致时不用再动。
        if label.isHidden == text.isEmpty { return }
        animate { label.animator().isHidden = text.isEmpty }
    }

    func setFeatureHint(_ index: Int, hint text: String) {
        guard let group = groupTitle(forFeature: index), let label = featureHintLabels[group] else { return }
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
                setFeatureHint(index, hint: "")
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
            var detail = available ? "" : (feature["message"] as? String ?? "此版本没有这个设置")
            if detail.isEmpty, on, justChangedFeature, let sessions = sessions {
                detail = codeSessionDetail(id: id, sessions: sessions)
            }
            setFeatureDetail(index, detail: detail)
        }
        updateFeatureGroupStatus()

        if let index = featureIDs.firstIndex(of: "desktop-extensions") {
            let hint = featureEnabled[index] && featureAvailable[index]
                ? desktopExtensionHint(result) : ""
            setFeatureHint(index, hint: hint)
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
        refreshHeader()
    }

    /// 总开关刚做完时保留那一句结果：紧随其后的检测会调用这里并传空字符串，
    /// 正好用它顶上去，显示一次后清空，不会盖住真正的问题也不会一直挂着。
    func setSummary(_ raw: String) {
        var text = raw
        if text.isEmpty, !switchAllSummary.isEmpty { text = switchAllSummary }
        switchAllSummary = ""
        guard featureSummary.stringValue != text
                || featureSummary.isHidden != text.isEmpty else { return }
        featureSummary.stringValue = text
        animate { self.featureSummary.animator().isHidden = text.isEmpty }
    }

    /// 总开关的结果：如实说明哪些完成、哪些被跳过、哪些失败，然后重新检测。
    /// 后端是一条命令做完所有项，所以这里只有一次收尾，不会来回闪。
    func completeSwitchAll(_ result: [String: Any], enabling: Bool) {
        lastStatusAt = Date()
        if let error = result["error"] as? String {
            switchAllSummary = error
        } else {
            let steps = result["steps"] as? [[String: Any]] ?? []
            let done = steps.filter { ($0["state"] as? String) == "done" }.count
            let skipped = steps.compactMap { step -> String? in
                guard (step["state"] as? String) == "skipped" else { return nil }
                return step["title"] as? String
            }
            var text = (enabling ? "已开启 " : "已恢复 ") + "\(done) 项"
            if !skipped.isEmpty {
                text += "；跳过 " + skipped.joined(separator: "、") + "（" +
                    (enabling ? "此版本没有该设置" : "没有本工具的恢复记录") + "）"
            }
            switchAllSummary = text
        }
        if enabling { enabledHintUntil = Date().addingTimeInterval(90) }
        // 权限只在真正装过补丁后才可能变化；重新检测会带回 permissionReGrantLikely。
        pendingPermissionPrompt = enabling
        refreshAllStatus()
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
        // 旧版内置组件不带这一项：保持原有文案，不要误显示成「读不到本机版本」。
        if let update = result["officialUpdate"] as? [String: Any] {
            applyOfficialUpdate(update)
        }
        if pendingPermissionPrompt {
            pendingPermissionPrompt = false
            // 重签后旧授权可能不再适用；只在检测确认可能失效时才把用户送到系统设置。
            if enabled && permissionReGrantLikely { openPrivacySettings() }
        }
        finishInteraction()
        handlePendingRefresh()
        maybeLaunchClaude()
        scheduleOfficialUpdateCheck()
    }

    /// 官方新版提醒：缓存过期（或用户刚点过刷新）时联网检查一次。
    /// 检查在独立的后端进程里跑，不占窗口的忙碌状态，也不转右上角的刷新图标。
    func scheduleOfficialUpdateCheck() {
        guard !busy, !quittingClaude, !officialUpdateChecking else { return }
        let force = forceOfficialUpdateCheck
        guard force || officialUpdateState.isEmpty || officialUpdateState == "unchecked"
                || officialUpdateStale else { return }
        // 读不到本机版本时不必联网：检查只会得到同样的结论。
        guard force || officialUpdateState != "unknown" else { return }
        // 刚失败过就先等一会，避免窗口每激活一次就重连一次。
        guard force || Date() >= officialUpdateRetryAfter else { return }
        forceOfficialUpdateCheck = false
        officialUpdateChecking = true
        officialUpdateLabel.stringValue = "正在检查官方新版…"
        officialUpdateLabel.textColor = ClaudeTheme.textMuted
        runBackend("check", backend: "official_update", feature: nil) { result in
            self.officialUpdateChecking = false
            if result["state"] is String {
                self.applyOfficialUpdate(result)
                if result["error"] == nil {
                    self.officialUpdateRetryAfter = .distantPast
                } else {
                    self.officialUpdateRetryAfter = Date().addingTimeInterval(120)
                }
            } else {
                // 后端连启动都没成功：保留上次结论，只补一句原因。
                self.officialUpdateNote = result["error"] as? String ?? "这次没能检查官方新版。"
                self.updateOfficialUpdateLabel()
                self.officialUpdateRetryAfter = Date().addingTimeInterval(120)
            }
        }
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
        static let rowPaddingY: CGFloat = 12        // --cds-pad-md
        // CDS 的标题与描述是同一个文本块里的两行，靠行高衔接，没有额外间距；
        // 这里留 0，否则每行多出 4pt，七行下来会把概览页顶出首屏。
        static let rowLabelGap: CGFloat = 0
        static let groupGap: CGFloat = 40           // --cds-gap-xl
        static let headerGap: CGFloat = 16          // --cds-gap-md
        static let contentInsetX: CGFloat = 24      // px-xl
        static let contentInsetTop: CGFloat = 28
        // 官方 `--cds-switch-h: calc(1.25rem * var(--cds-rem-scale, 1))`。
        // 包内 CSS 从未给 `--cds-rem-scale` 赋值，官方 `index.html` 也刻意不写
        // 内联字号（注释原文："Deliberately no ... inline font size"），
        // 于是 rem = 16px 恒等，开关高就是 1.25 × 16 = 20pt。
        // 此前按 16/17 缩放写成 19pt 是没有依据的。
        // 宽度官方由组件库在 JS 里算，CSS 查不到，仍按真机截图取 1.8 倍。
        static let switchHeight: CGFloat = 20
        static let switchWidth: CGFloat = 36
    }

    // surface-2 内容面板：单页界面只有这一层底色。
    static let pane = dynamic(rgb(0xFFFFFF), rgb(0x1A1A19))
    // 图层背景用静态色：动态 NSColor 的 cgColor 在 CALayer 上会被解析成系统占位灰。
    static let paneLight = rgb(0xFFFFFF)
    static let paneDark = rgb(0x1A1A19)
    static let iconHover = dynamic(rgb(0x0B0B0B, alpha: 0.05), rgb(0xFFFFFF, alpha: 0.05))

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

    /// 官方 semibold 的 wght 是 580（CSS `--cds-font-weight-semibold: 580`），
    /// 但字体里的 `TextSemibold` 具名实例落在 600（实测可变轴返回值）。
    /// 直接取具名实例会让标题比官方略粗一档，所以这里按官方数值设轴，
    /// 而不是挑最接近的实例名。
    private static let weights: [NSFont.Weight: (name: String, wght: CGFloat)] = [
        .regular: ("AnthropicSansVariable-TextRegular", 400),
        .medium: ("AnthropicSansVariable-TextMedium", 500),
        .semibold: ("AnthropicSansVariable-TextSemibold", 580),
    ]

    // 与 CDS 字号一一对应：caption 12 / footnote 13 / body 14 / heading 15 / title 22。
    static func caption(_ weight: NSFont.Weight = .regular) -> NSFont { ui(12, weight) }
    static func footnote(_ weight: NSFont.Weight = .regular) -> NSFont { ui(13, weight) }
    static func body(_ weight: NSFont.Weight = .regular) -> NSFont { ui(14, weight) }
    static func heading(_ weight: NSFont.Weight = .semibold) -> NSFont { ui(15, weight) }
    static func title() -> NSFont { ui(22, .semibold) }

    /// CDS 的行高和字号不成固定比例（caption 12/17、body 14/20、title 22/28），
    /// 而 `NSTextField` 只按字体自身度量排：14pt 排出来是 18pt 行高，比官方紧 2pt，
    /// 多行说明会明显更挤。这里按官方 `--cds-leading-*` 给出对应行高。
    static func leading(for size: CGFloat) -> CGFloat {
        switch size {
        case ..<12.5: return 17    // caption 12/17、footnote 13/17
        case ..<16.5: return 20    // body 14/20、heading 15/20
        default: return 28         // title 22/28
        }
    }

    static func ui(_ size: CGFloat, _ weight: NSFont.Weight = .regular) -> NSFont {
        registerBundledFonts()
        guard let face = weights[weight],
              let base = NSFont(name: face.name, size: size) else {
            return .systemFont(ofSize: size, weight: weight)
        }
        // 视觉字号轴跟字号走，否则小字会拿到 16pt 那档的字距。
        let descriptor = base.fontDescriptor.addingAttributes([
            .variation: [NSNumber(value: 0x77676874): face.wght,   // 'wght'
                         NSNumber(value: 0x6F70737A): size]        // 'opsz'
        ])
        return NSFont(descriptor: descriptor, size: size) ?? base
    }

    /// 按 CDS 行高排字的标签。
    ///
    /// `NSTextField` 没有 lineHeight，只能用段落样式；而段落样式挂在富文本上，
    /// 谁再写一次 `stringValue = …` 就会被清掉，行高悄悄回到字体自身的 18pt。
    /// 界面里标签文字是随时更新的（检测中 / 已开启 / 版本号…），逐个调用点重套
    /// 一定会漏，所以把重套放进 setter 本身。
    static func label(_ text: String = "", font: NSFont) -> CDSLabel {
        let label = CDSLabel(labelWithString: text)
        label.apply(font)
        return label
    }
}

/// 见 `ClaudeFont.label`：写入文字时自动带上官方行高。
final class CDSLabel: NSTextField {
    private var cdsFont: NSFont?
    private var cdsStyle: NSParagraphStyle?

    func apply(_ font: NSFont) {
        cdsFont = font
        let style = NSMutableParagraphStyle()
        style.minimumLineHeight = ClaudeFont.leading(for: font.pointSize)
        style.maximumLineHeight = style.minimumLineHeight
        cdsStyle = style
        self.font = font
        write(stringValue)
    }

    /// 只有显式改了字体才需要同步行高；这里保持默认的 `font` 行为。
    override var stringValue: String {
        get { super.stringValue }
        set { write(newValue) }
    }

    private func write(_ text: String) {
        guard let font = cdsFont, let style = cdsStyle else {
            super.stringValue = text
            return
        }
        super.attributedStringValue = NSAttributedString(
            string: text, attributes: [.font: font, .paragraphStyle: style])
    }
}

/// 左侧栏底色：直接绘制，滚动/截图/打印时都不会丢。
/// 左侧栏底色：图层化并用 updateLayer 上色。
/// 窗口里只要有一个子视图带图层，整棵子树就进入图层合成，`draw(_:)` 里的
/// 底色会在离屏/滚动合成时丢掉；0.3.34 踩过一次，这里必须保持 updateLayer。
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

final class FlippedView: NSView {
    override var isFlipped: Bool { true }
}

/// 只用来接收初始焦点，避免内容一启动就出现蓝色焦点圈。
final class FocusRootView: NSView {
    override var acceptsFirstResponder: Bool { true }
}

final class ClaudeIconButton: NSButton {
    private let symbolView = NSImageView()
    private let actionClosure: () -> Void
    private var hovered = false
    private var tracking: NSTrackingArea?
    private var spinning = false

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

    /// 加载时让图标自己旋转，代替原来单独放在旁边的转圈指示器。
    /// 旋转只作用在图标图层上，按钮的点击区域和悬停底色都不受影响。
    func setSpinning(_ on: Bool) {
        guard on != spinning else { return }
        spinning = on
        symbolView.wantsLayer = true
        applySpin()
    }

    override func layout() {
        super.layout()
        // 图标尺寸固定，但布局完成后再补一次，保证第一次旋转就绕自己的中心。
        if spinning { applySpin() }
    }

    private func applySpin() {
        guard let layer = symbolView.layer else { return }
        if spinning {
            guard layer.animation(forKey: "spin") == nil else { return }
            // NSView 的图层锚点在左下角，直接转会绕角；把旋转中心平移到图标中心。
            let center = CGPoint(x: symbolView.bounds.midX, y: symbolView.bounds.midY)
            var frames: [NSValue] = []
            for step in 0...4 {
                // 图层坐标里正角度是逆时针；箭头本身表示顺时针，所以角度递减。
                let angle = -Double.pi * 2 * Double(step) / 4
                var transform = CATransform3DIdentity
                transform = CATransform3DTranslate(transform, center.x, center.y, 0)
                transform = CATransform3DRotate(transform, angle, 0, 0, 1)
                transform = CATransform3DTranslate(transform, -center.x, -center.y, 0)
                frames.append(NSValue(caTransform3D: transform))
            }
            let spin = CAKeyframeAnimation(keyPath: "transform")
            spin.values = frames
            spin.keyTimes = [0, 0.25, 0.5, 0.75, 1]
            spin.duration = 0.9
            spin.repeatCount = .infinity
            spin.timingFunctions = Array(repeating: CAMediaTimingFunction(name: .linear), count: 4)
            spin.isRemovedOnCompletion = false
            layer.add(spin, forKey: "spin")
        } else {
            layer.removeAnimation(forKey: "spin")
        }
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
            ClaudeTheme.iconHover.setFill()
            NSBezierPath(roundedRect: bounds, xRadius: 6, yRadius: 6).fill()
        }
    }

    private func updateAppearance() {
        // 旋转时按钮是禁用的（防止重复触发），但图标本身保持正常颜色，
        // 否则正在转的箭头会发灰，看起来像卡住了。
        symbolView.contentTintColor = (isEnabled || spinning) ? ClaudeTheme.textSecondary
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

/// Claude 设置页开关：胶囊轨道 + 白色圆钮，开启为系统蓝。
/// 尺寸取自 `ClaudeTheme.Metric.switchWidth/switchHeight`（注释里写过 38×22，
/// 与实际值不符——以常量那一处为准，这里不再重复写数字）。
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

/// 顶部身份栏：星芒头像 + 名称 + 状态点 + 版本摘要。
/// 原来在侧栏左下角，侧栏取消后并入主界面，成为窗口的第一行。
final class IdentityStatusView: NSView {
    private let avatar = IdentityAvatarView()
    private let title = NSTextField(labelWithString: "Claude 原生助手")
    private let detail = NSTextField(labelWithString: "正在检测…")
    private let statusDot = StatusDotView()
    private let status = NSTextField(labelWithString: "检测中")
    private var tint: NSColor = ClaudeTheme.textMuted
    private var pulseTimer: Timer?

    override init(frame: NSRect) {
        super.init(frame: frame)

        avatar.translatesAutoresizingMaskIntoConstraints = false
        title.font = ClaudeFont.heading(.semibold)
        title.textColor = ClaudeTheme.textPrimary
        title.translatesAutoresizingMaskIntoConstraints = false
        detail.font = ClaudeFont.caption()
        detail.textColor = ClaudeTheme.textMuted
        detail.lineBreakMode = .byTruncatingTail
        detail.translatesAutoresizingMaskIntoConstraints = false
        statusDot.translatesAutoresizingMaskIntoConstraints = false
        status.font = ClaudeFont.caption()
        status.textColor = ClaudeTheme.textSecondary
        status.lineBreakMode = .byTruncatingTail
        status.translatesAutoresizingMaskIntoConstraints = false

        addSubview(avatar)
        addSubview(title)
        addSubview(detail)
        addSubview(statusDot)
        addSubview(status)
        NSLayoutConstraint.activate([
            avatar.leadingAnchor.constraint(equalTo: leadingAnchor),
            avatar.centerYAnchor.constraint(equalTo: centerYAnchor),
            avatar.widthAnchor.constraint(equalToConstant: 34),
            avatar.heightAnchor.constraint(equalToConstant: 34),
            title.leadingAnchor.constraint(equalTo: avatar.trailingAnchor, constant: 11),
            title.topAnchor.constraint(equalTo: topAnchor, constant: 1),
            detail.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            detail.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 1),
            detail.trailingAnchor.constraint(lessThanOrEqualTo: trailingAnchor),
            // 状态点跟在名称后面同一行，不再往下堆第三行。
            statusDot.leadingAnchor.constraint(equalTo: title.trailingAnchor, constant: 10),
            statusDot.centerYAnchor.constraint(equalTo: title.centerYAnchor),
            statusDot.widthAnchor.constraint(equalToConstant: 6),
            statusDot.heightAnchor.constraint(equalToConstant: 6),
            status.leadingAnchor.constraint(equalTo: statusDot.trailingAnchor, constant: 5),
            status.centerYAnchor.constraint(equalTo: title.centerYAnchor),
            status.trailingAnchor.constraint(lessThanOrEqualTo: trailingAnchor)
        ])
        update(text: "检测中", tint: ClaudeTheme.accent, pulsing: true)
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    func updateDetail(_ text: String) {
        guard detail.stringValue != text else { return }
        detail.stringValue = text
    }

    func update(text: String, tint: NSColor, pulsing: Bool) {
        self.tint = tint
        status.stringValue = text
        status.textColor = ClaudeTheme.textSecondary
        statusDot.color = tint
        if pulsing {
            startBreathing()
        } else {
            stopBreathing()
        }
    }

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

/// 身份栏左侧的圆形星芒头像。
final class IdentityAvatarView: NSView {
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

final class StatusDotView: NSView {
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

/// 滚动区上下边缘的渐隐提示：只有还能继续滚的那一侧才压暗。
/// 直接盖在滚动视图上绘制，不参与命中测试，滚轮和点击都穿透到下面的内容。
final class ScrollFadeOverlay: NSView {
    /// 官方 `--cds-scroll-fade-size: 28px`；桌面端 rem-scale 16/17，取 26pt。
    private static let fadeHeight: CGFloat = 26
    private var showsTop = false
    private var showsBottom = false

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override var isFlipped: Bool { true }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    func update(offset: CGFloat, maximum: CGFloat) {
        let top = maximum > 1 && offset > 0.5
        let bottom = maximum > 1 && offset < maximum - 0.5
        guard top != showsTop || bottom != showsBottom else { return }
        showsTop = top
        showsBottom = bottom
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        guard showsTop || showsBottom else { return }
        let fade = min(Self.fadeHeight, bounds.height / 2)
        effectiveAppearance.performAsCurrentDrawingAppearance {
            let pane = effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
                ? ClaudeTheme.paneDark : ClaudeTheme.paneLight
            if showsTop {
                let rect = NSRect(x: 0, y: 0, width: bounds.width, height: fade)
                NSGradient(starting: pane.withAlphaComponent(0.92), ending: pane.withAlphaComponent(0))?
                    .draw(in: rect, angle: -90)
            }
            if showsBottom {
                let rect = NSRect(x: 0, y: bounds.height - fade, width: bounds.width, height: fade)
                NSGradient(starting: pane.withAlphaComponent(0), ending: pane.withAlphaComponent(0.92))?
                    .draw(in: rect, angle: -90)
            }
        }
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
    func renderScrolledPreview(to path: String, offset: CGFloat) {
        NSApp.appearance = NSAppearance(named: .darkAqua)
        window.appearance = NSApp.appearance
        if let state = ProcessInfo.processInfo.environment["PREVIEW_UPDATE_STATE"] {
            applyOfficialUpdate(["state": state, "installed": "3.6556.2",
                                 "latest": "3.9683.0", "adapted": false])
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
            self.scrollView?.contentView.scroll(to: NSPoint(x: 0, y: offset))
            self.scrollView?.reflectScrolledClipView(self.scrollView!.contentView)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                guard let view = self.window.contentView else { return }
                // cacheDisplay 走图层合成，能看到自绘的渐隐提示。
                guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
                view.cacheDisplay(in: view.bounds, to: rep)
                if let data = rep.representation(using: .png, properties: [:]) {
                    try? data.write(to: URL(fileURLWithPath: path))
                    print("captured \(path) \(rep.pixelsWide)x\(rep.pixelsHigh)")
                }
                NSApp.terminate(nil)
            }
        }
    }

    func renderPreview(to path: String, dark: Bool) {
        NSApp.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        window.appearance = NSApp.appearance
        // PREVIEW_LOADING=1 时把右上角图标切成加载态，用来核对转圈的样子。
        if ProcessInfo.processInfo.environment["PREVIEW_LOADING"] != nil {
            setLoading(true)
        }
        // PREVIEW_UPDATE_STATE=available 时用合成结果核对提醒那一行的颜色与换行；
        // 只影响预览构建，不写缓存、不联网。
        if let state = ProcessInfo.processInfo.environment["PREVIEW_UPDATE_STATE"] {
            applyOfficialUpdate(["state": state, "installed": "3.6556.2",
                                 "latest": "3.9683.0", "adapted": false])
        }
        // 每次截图都从顶部开始，避免上一次滚动位置带进来。
        scrollView?.contentView.scroll(to: .zero)
        scrollView?.reflectScrolledClipView(scrollView!.contentView)
        window.contentView?.layoutSubtreeIfNeeded()
        window.displayIfNeeded()
        window.contentView?.subviews.forEach { $0.displayIfNeeded() }
        CATransaction.flush()
        guard let view = window.contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        // 走图层合成：能看到底色和渐隐；PDF 路径会漏掉图层背景，颜色不可信。
        view.cacheDisplay(in: view.bounds, to: rep)
        if let data = rep.representation(using: .png, properties: [:]) {
            try? data.write(to: URL(fileURLWithPath: path))
            print("captured \(path) \(rep.pixelsWide)x\(rep.pixelsHigh)")
        }
    }
}

let previewDelegate = AppDelegate()
let previewApp = NSApplication.shared
previewApp.delegate = previewDelegate
let previewBase = ProcessInfo.processInfo.environment["PREVIEW_PATH"] ?? "/tmp/claude-native-preview"

func previewStep(_ index: Int) {
    // 单页界面只有深浅两张：深色一次看全首屏，浅色核对配色。
    if index == 0 {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) {
            previewDelegate.renderPreview(to: previewBase + "-dark.png", dark: true)
            previewStep(1)
        }
    } else {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            previewDelegate.renderPreview(to: previewBase + "-light.png", dark: false)
            previewApp.terminate(nil)
        }
    }
}
DispatchQueue.main.asyncAfter(deadline: .now() + 5.0) {
    if let raw = ProcessInfo.processInfo.environment["PREVIEW_SCROLL"], let value = Double(raw) {
        previewDelegate.renderScrolledPreview(to: previewBase + "-scrolled.png",
                                              offset: CGFloat(value))
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