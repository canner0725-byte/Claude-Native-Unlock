# Claude 原生助手 · Claude Native Unlock

中文原生 macOS 应用，为 Claude Desktop 第三方 API（3P）部署管理已适配的原生功能可用性。Swift / AppKit 界面，带菜单栏图标；运行所需 Python 已内置，无需安装 Python、Xcode 或打开终端。

这是独立社区项目，与 Anthropic 无隶属关系。仅分发我们编写的工具；不分发 Claude 安装包、原生模块、第三方 API 密钥、会话数据或个人备份。本工具界面为中文，不代表将 Claude 本体全部汉化。

## 功能

- Computer Use 与新版 Code 工作台的固定版本可用性补丁。
- Code 原生自动记忆整理的可用性补丁；保留用户开关和官方触发条件。
- 官方 Chat/文件分析、内置浏览器、定时任务和 Cowork 自动审批选项的配置管理。
- 原版指纹、签名、版本检查；备份、恢复、补丁更新和撤回。
- 菜单栏可显示主窗口、检测状态、打开 Claude、打开备份和退出。关闭主窗口不会退出菜单栏。

只调整目标可用性条件，不提供替代 Computer Use 执行器或云服务。不改变逐应用授权、系统权限、拒绝、中断、截图过滤或动作审查。不会自动登录或收集 API 密钥。

## 下载与安装

在 GitHub Releases 下载 Apple Silicon 的 `.dmg`，打开后将 Claude Native Unlock 拖入 Applications，也可使用 `.zip`。双击应用即可使用。首次发布使用临时签名，**没有 Apple Developer ID 签名或公证**；macOS 可能阻止首次打开。请自行核对源码和校验和，通过系统正常界面决定是否允许，不需要关闭 Gatekeeper 或 SIP。

状态及备份位于 `~/Library/Application Support/Claude Native Unlock/`。移动应用、删除源码目录、更新本工具均不应删除此目录；恢复 Claude 原版需要其中的备份。工具不会自动清理备份。

### 已验证范围

| 组件 | 固定版本 / 条件 |
| --- | --- |
| Claude Desktop | 2.31226.1、2.37337.1 原始文件指纹 |
| Code 新界面 | 2.37337.1；原生工作台、启动框架、状态分组 |
| 内置 Claude Code | 2.1.295 的指定缓存目录与完整指纹 |
| 补丁动作平台 | Apple Silicon、macOS 27.0.1 |

应用界面可以在更早的 macOS 启动，但补丁只接受表中实际适配的平台；未知版本或文件变化会拒绝写入。Claude 安装目录必须对当前用户可写，工具不自动提权。菜单栏图标表示补丁管理工具运行，**不代表 Claude 已取得系统权限**。

### 使用顺序

1. 正常退出 Claude，打开本工具检测。
2. 选择所需功能，等待备份和校验完成，再打开 Claude。
3. 使用 Computer Use 前，在 Claude 设置的 System 页确认 Accessibility、Screen recording 均为 Granted；签名变化可能要求重新绑定同一应用并重启。
4. Claude 官方更新前，先退出 Claude、关闭桌面补丁以恢复完整官方签名；更新后重新检测版本。不能直接将旧补丁套到新版本。

既有配置仅在本工具拥有恢复记录时还原；外部修改或管理员策略会阻止覆盖。所有系统密码、Touch ID、独立屏幕接管确认继续由用户处理。

## 边界与已知问题

- 官方账号历史检索、夜间摘要、Design、Security 等云端服务不会因本地补丁而变成可用。
- 蓝色代码水波仅在官方宣传画面观察到；尚未定位本版原生开启入口，未包含自制替代动画。
- 第三方模型理解截图和规划动作的能力取决于模型，解锁可用性不等于任意应用操作成功。
- 新版界面状态分组和系统权限已在本机实际验证；其余功能的运行证据分别来自对应版本，不能解读为所有版本、所有应用均经过验收。

## 从源码构建

构建需要 Apple Silicon Mac、Xcode Command Line Tools 和 Python 3.12。运行打包后的应用无需这些开发依赖。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python scripts/build.py
```

产物在 `dist/`，包括原生 `.app`、DMG、ZIP 和 `SHA256SUMS.txt`。图标由仓库中的 Swift 矢量代码生成，不使用 Claude 官方图标。内置补丁组件使用 PyInstaller 打包，参见[官方说明](https://pyinstaller.org/en/stable/usage.html)。

```sh
.venv/bin/python -m unittest discover -s tests -v
```

公开测试使用临时合成数据，不需要 Claude 安装包、密钥或登录。真实安装验收必须在明确版本的隔离应用副本进行，禁止对工作文档做测试。

早期项目目录版本用户可以在确认当前安装与原版备份匹配后运行：

```sh
.venv/bin/python scripts/migrate_state.py /path/to/legacy-project
```

迁移只复制并核验恢复记录及必要备份，保留源目录，不复制账号、密钥和聊天数据。迁移后使用 Applications 中的新助手，不再交替运行旧项目目录工具。不覆盖已经存在的目标记录，失败现场保留供诊断。

## 开源与贡献

MIT 许可证仅覆盖本项目代码；Claude 及其商标属于各自权利人，内置运行时许可证另见 `THIRD_PARTY_NOTICES.md`。新增适配必须包含版本和原始指纹，保持审批与系统权限检查，不接受全局强制开启所有功能的改动。提 Issue 前请移除密钥、用户文件路径和会话正文。
