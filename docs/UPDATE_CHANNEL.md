# 官方更新渠道（阶段 2：官方更新代办）

本文记录 Claude Desktop 官方更新渠道**实测**得到的地址、协议和判定逻辑。
所有结论都标注了来源等级：**已验证**（本机实际请求过）、**静态推断**
（从官方 app.asar 源码读出，未单独验证）、**未验证**。

> 位置说明：`docs/` 不在本 git 仓库里，实际位置是
> `/Users/canner13/Desktop/Claude-Native-Unlock/docs/`。这份文件先在
> worktree 里写好，需要由主会话搬到那个目录。

对应《维护方案 修订版 v2》阶段 2。配套脚本：

- `scripts/update_channel.py` —— 只读探测渠道
- `scripts/rehearse_update.py` —— 只读演练一次更新代办
- `tests/test_update_channel.py` —— 21 条离线回归测试

---

## 1. 渠道地址（已验证）

之前猜过的 `latest-mac.yml` / `RELEASES` 全部 404，因为 **Claude 不用
Electron 的 YAML 更新格式**，而是用 Squirrel.Mac 的 `json` 服务：

```js
a.autoUpdater.setFeedURL({url: $q(), serverType: "json"})
```

地址由官方函数 `ZQr()` 拼出（**静态推断**，源码位于主 chunk 的更新模块）：

```
{base}/api/desktop/{platform}/{arch}/squirrel/update
    ?device_id=…&version=…&os_version=…[&channelUuid=…]
```

其中：

| 部分 | 取值 | 来源 |
|---|---|---|
| `base` | `autoUpdate.updateApiBase` → 应用配置的 API 基址 → `https://releases.claude.com` | 静态推断（`RQr()`） |
| `platform` | `darwin` | 静态推断（`process.platform`） |
| `arch` | `universal`（另有 `arm64`/`x64` 被官方承认） | 静态推断（`BQr` 白名单） |
| `device_id` | 官方用本机设备标识；本工具用固定探针值 | 静态推断（`yin()`） |
| `version` | 本机 Claude 版本 | 静态推断 |
| `os_version` | 本机 macOS 版本 | 静态推断 |
| `channelUuid` | stable 不带；latest / staggered 见下表 | 静态推断（`AQr`） |

**关键发现**：`base` 取的是**应用当前配置的 API 基址**。本机日志
`~/Library/Logs/Claude/main.log` 里可以直接读到实际值（**已验证**）：

```
[info] [updater] Update URL: https://api.anthropic.com
```

也就是说，切换到第三方 API 部署后，Claude 会去**那个 API 基址**查桌面更新。
这一点对维护有实际影响：渠道可用性取决于用户配置的 API 服务是否实现
`/api/desktop/...` 这组端点，而不是 Anthropic 的公共下载站。

### 渠道标识（已验证，2026-10-24 实测）

| 渠道 | channelUuid | 实测 `currentRelease` | 下载包路径 |
|---|---|---|---|
| stable | （无） | 3.6556.2 | `downloads.claude.ai/releases/…` |
| latest | `77a14fc4-808e-4345-ac3e-090be671bf94` | 3.9683.0 | `downloads.claude.ai/releases-latest/…` |
| staggered | `51760d71-67e2-4366-9bf5-4221cda21f61` | 3.6556.2（`releases` 为空） | — |

staggered 返回空 `releases` 是**正常形状**，不是错误：它表示「当前没有
正在灰度下发的构建」。脚本把这种情况如实显示，不当成渠道故障。

---

## 2. 响应格式（已验证）

```json
{
  "currentRelease": "3.6556.2",
  "releases": [{
    "version": "3.6556.2",
    "updateTo": {
      "name": "Claude 3.6556.2",
      "version": "3.6556.2",
      "pub_date": "2026-10-23T00:36:12.758104",
      "url": "https://downloads.claude.ai/releases/darwin/universal/3.6556.2/Claude-20c019a1e61fa5b687616af57d31f97bb3181723.zip",
      "notes": "Production Release - No Notes",
      "sha256": "5f3e36bd0c500f0e02cca84848f5caf453372117ec664153db187b372754c67a",
      "size": 378072300
    }
  }]
}
```

`url` 里的那串十六进制是**内容哈希**，每个版本不同，不能靠猜文件名拼地址。
`sha256` 是官方给的校验值，登记新版本时必须用它。

下载地址实测可达（**已验证**）：对 stable 的 zip 发 `HEAD` 得到
`HTTP 200`、`x-goog-stored-content-length: 378072300`，与 JSON 里的
`size` 一致。

---

## 3. 判定逻辑

`scripts/rehearse_update.py` 把渠道版本、本机版本、登记表三者对照，
只给出四种结论之一：

| 结论 | 条件 | 下一步 |
|---|---|---|
| `up_to_date` | 渠道版本 ≤ 本机版本 | 无需动作 |
| `needs_adaptation` | 渠道有新版，登记表里没有 | **拒绝写入**，先人工适配 |
| `needs_yield` | 渠道有**已适配**的新版，本机装着旧补丁 | 先 `prepare-update` 恢复官方原版 |
| `ready_to_update` | 渠道有已适配的新版，本机没有补丁 | 直接装官方更新 |

`--offline` 时结论是 `unknown`，**不是** `up_to_date`——没联网就不能声称
「已经是最新的」。

版本比较按数字段（`3.9683.0 > 3.6556.2`）。按字符串比会正好判反，
测试里钉住了这一条。

---

## 4. 演练脚本为什么不自动执行

`rehearse_update.py` 只描述 `prepare-update` 会做什么，**不调用它**。
原因是那条命令会把已安装的 Claude 换回官方原版——会影响用户当前正在用的
应用，属于「必须由人点」的操作。演练脚本的职责是把「到时候会发生什么」
讲清楚，执行留在界面上。

测试 `RehearsalIsReadOnly` 守着这一点：脚本源码里出现
`official_unlock.enable(` / `.update(` / `.disable(` / `.prepare_update(`
任何一个都算越界。

---

## 5. 本次实测发现的真实情况

**已验证**：官方 latest 渠道现在声明 **3.9683.0**，而本工具登记表最高只到
**3.6556.2**。渠道探测让「有新版本了」在**用户遇到之前**就变成一条可读的结论。

**2026-10-25 更新**：未登记版本不再等于拒绝。用户明确要求官方升级后旧助手
默认照常解锁，所以 3.9683.0 这类未登记构建会走 `discovered_adapter()` 的三重
校验（官方签名、包标识、功能表结构），通过即现场适配。登记表仍然有价值：
省掉结构识别，并提供跨机器一致的验证范围。

### latest 渠道能不能由用户切换（已验证：不能）

用户问过「我想用 latest 早期渠道咋办」。结论是**用户无法自行切换**，只有手动
安装官方包一条路。官方渠道判定公式在 3.6556.2 和 3.9683.0 里完全一致：

```
staggered  若 autoUpdate.releaseChannel === "staggered"
latest     否则若 preferences.ancientJournal === "latest"
           且 GrowthBook 开关 "3424654946" 为开
stable     其余
```

逐条核实过的拦截点：

| 拦截点 | 实测结论 |
|---|---|
| `preferences.ancientJournal` | 属于 `HHa`（MAIN_ONLY_PREFERENCE_KEYS），渲染进程写不了；`requestAncientJournal` 这个 IPC 在整包 JS 里没有任何调用方 |
| GrowthBook 开关 `3424654946` | 匿名 `/api/desktop/features` 返回 `on:false, source:"defaultValue"`；1P 的 `fcache` 没有这个键；3P 静态表（40 项）也没有；`CLAUDE_DEV_FORCE_GATES` 是空实现（`KKt(e,t){return new Set}`），改它无效 |
| 设置界面 | About 窗口只读不写；3P 的 `releaseChannel` 设置只接受 `stable` / `staggered`，**没有 `latest`**；而且 `staggered` 比 stable 更慢，不是更快的早期渠道 |

所以想让 Claude 跑 3.9683.0，只能下载官方包手动换装——也就是
`scripts/install_staged_update.py` 做的事。

---

## 6. 尚未完成（如实记录）

- **未验证**：`autoUpdate.updateApiBase` 这个配置项在本机是否为非空。
  实测走的是「应用配置的 API 基址」这条分支（日志显示
  `https://api.anthropic.com`）。如果用户切换 API 基址，渠道地址会跟着变，
  本脚本默认只用两条官方默认基址，**不会**自动读用户配置的基址——
  避免把密钥或私有网关地址写进输出。
- **已验证**：`arm64` / `x64` 是**真的不同的包**。同一版本 3.6556.2，
  `universal` 的 sha256 是 `5f3e36bd…`，`arm64` 是 `e30f6df1…`，
  文件名相同但目录不同（`/releases/darwin/arm64/`）。所以登记指纹时必须
  固定架构，不能拿 universal 的校验值去核 arm64 的包。
- **未验证**：`releases` 数组出现多条（多版本同时下发）时的行为。
  当前实测都只有一条，解析器支持多条但没有真实样本。
- **未验证**：`maxVersion` 查询参数（官方在 `autoUpdate.dangerousMaxVersion`
  存在时才带）。本机未配置该项，未实测。
- **未实现**：把渠道版本自动写进登记表。按固定规则，新版本必须先通过
  签名、包标识、功能表结构三重校验才允许适配，这一步不能自动化跳过。
  （2026-10-25 起，未登记版本本身不再阻止适配——三重校验通过就现场适配；
  登记仍然建议做，但不再是解锁的前置条件。）

### 换装的执行方式（已验证）

`official_unlock.enable()` 的 `check_idle()` 要求 Claude 完全退出，而会话内的
shell 就在 Claude 进程树里，退出即断线。所以换装拆成两步：

- 退出请求由用户自己发（⌘Q）。从会话里发 AppleEvent 也会被官方 before-quit
  拦截器否决——实测日志是 `beforeQuit: handler fired` 紧跟
  `vetoed by before-quit interceptor`，因为还有活跃会话。
- 换装交给**脱离 Claude 进程树**的进程：`scripts/install_staged_update.py`
  用 `nohup … & disown` 启动，实测 `PPID=1`，Claude 退出不会把它带走。

它只做四件事：三重校验暂存构建 → 等 Claude 退出（有上限，绝不强制结束）→
换装（失败退回原版）→ 调用后端 `official_unlock enable`。四条路径（正确输入、
错误版本、错误 ASAR、非官方应用）都实测过。

---

## 7. 用法

```bash
# 探测 stable 渠道（默认），并确认安装包真的可达
.venv/bin/python scripts/update_channel.py --verify-download

# 探测 latest 渠道
.venv/bin/python scripts/update_channel.py --channel latest

# 探测 arm64 包（与 universal 内容不同，校验值也不同）
.venv/bin/python scripts/update_channel.py --arch arm64

# 只读本机，不联网
.venv/bin/python scripts/update_channel.py --offline

# 演练一次更新代办（不执行任何写操作）
.venv/bin/python scripts/rehearse_update.py

# 机器可读
.venv/bin/python scripts/update_channel.py --json
```

已安装的 `/Applications/Claude.app` 如果打过补丁，签名会变成 ad-hoc，
脚本会**拒绝**用它探测（`不是官方签名`）。这是设计意图：探测必须对着
完整官方版，通常用官方备份：

```bash
.venv/bin/python scripts/update_channel.py \
  --app "$HOME/Library/Application Support/Claude 原生助手/work/official-install/official-backup-<id>.app"
```

退出码：`0` 正常；`2` 渠道不可用或结构不认识；`3` 响应不是 JSON；
`4` 联网失败。
