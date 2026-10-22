#!/usr/bin/env python3
"""官方第三方 API（3P）配置的可用性开关。

只改 Claude 已有配置里的几个非秘密字段，不新增执行器、服务或账号：
- desktopHome
- builtinBrowserEnabled
- scheduledTasksEnabled
- autoModeEnabled
- isDesktopExtensionEnabled
- toolSearchEnabled
- skipWebFetchPreflight

所有修改都先留恢复记录，支持原值回滚；管理员策略、配置服务器、
符号链接和并发修改都会被拒绝。
"""
import argparse
import fcntl
import json
import os
import pathlib
import re
import shlex
import stat
import sys
import time

import official_unlock as official

DATA = pathlib.Path.home() / 'Library/Application Support/Claude-3p'
STATE = official.core.WORK / 'native-features'

# 这些版本的记录可以互相读取；新增正式版适配时在这里补上版本号。
SUPPORTED_VERSIONS = {
    official.core.VERSION,
    official.core.ADAPTER_37337['version'],
    official.core.ADAPTER_42304['version'],
    official.core.ADAPTER_42304_2['version'],
    official.core.ADAPTER_31623['version'],
}

FEATURES = {
    'file-analysis': {'title': 'Chat 与高级文件分析', 'key': 'desktopHome', 'value': 'standard'},
    'browser': {'title': '官方内置浏览器', 'key': 'builtinBrowserEnabled', 'value': True},
    'scheduled-tasks': {'title': '官方定时任务', 'key': 'scheduledTasksEnabled', 'value': True},
    'auto-approve': {'title': 'Cowork 自动审批选项', 'key': 'autoModeEnabled', 'value': True},
    # 官方文档：桌面扩展（.mcpb）默认不加载，把这一项设成 true 才允许插件里
    # 打包的扩展运行；扩展本身仍由官方加载器和签名策略检查。
    'desktop-extensions': {'title': '插件里的桌面扩展', 'key': 'isDesktopExtensionEnabled', 'value': True},
    # 官方文档：MCP 工具定义默认全部塞进上下文；打开后改为按需加载。
    # 第三方网关若支持，桌面端会额外注入 ENABLE_TOOL_SEARCH；不支持时
    # 走官方旧路径，不会伪造工具或替换检索服务。
    'tool-search': {'title': 'MCP 工具按需加载', 'key': 'toolSearchEnabled', 'value': True},
    # 官方文档：Code 会话里的 WebFetch 默认先向 api.anthropic.com 做域名
    # 预检；第三方部署连不上那个域名，WebFetch 就一直失败。打开这一项
    # 让官方跳过预检，抓取仍走官方 WebFetch 与既有 egress 规则。
    'webfetch-preflight': {'title': 'WebFetch 免域名预检', 'key': 'skipWebFetchPreflight', 'value': True},
}


def safe_read(path):
    """只读普通文件；拒绝符号链接，避免被重定向到别处的配置。"""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError('配置必须是普通文件；停止操作。')
        return file.read()


def active_config():
    """读取当前生效的配置文件和元数据；拒绝符号链接与非法 ID。"""
    library = DATA / 'configLibrary'
    if library.is_symlink() or DATA.is_symlink():
        raise ValueError('配置目录是符号链接；停止操作。')
    metadata = safe_read(library / '_meta.json')
    identifier = json.loads(metadata)['appliedId']
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifier):
        raise ValueError('当前配置 ID 不合法；停止操作。')
    path = library / (identifier + '.json')
    raw = safe_read(path)
    config = json.loads(raw)
    if not isinstance(config, dict):
        raise ValueError('当前配置格式异常；停止操作。')
    return path, config, raw, metadata


def managed():
    paths = [pathlib.Path('/Library/Managed Preferences/com.anthropic.claudefordesktop.plist'),
             pathlib.Path('/Library/Managed Preferences') / pathlib.Path.home().name / 'com.anthropic.claudefordesktop.plist']
    return any(path.exists() or path.is_symlink() for path in paths)


def check_local(config):
    if managed() or config.get('bootstrapUrl') or config.get('bootstrapEnabled'):
        raise ValueError('此连接由管理配置或配置服务器控制；请在官方配置来源修改，不覆盖策略。')
    if config.get('inferenceProvider') not in ['gateway', 'anthropic', 'bedrock', 'mantle', 'vertex', 'foundry']:
        raise ValueError('未识别官方第三方推理配置；停止操作。')


def check_idle():
    if official.running():
        raise ValueError('请先正常退出 Claude，再修改原生配置；不会强制结束会话。')


def atomic_write(path, value):
    """写临时文件并原子替换；权限 0600，避免其他用户读取。"""
    temp = path.with_name(path.name + '.native-unlock-' + str(time.time_ns()))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(value)
        file.flush()
        os.fsync(file.fileno())
    temp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def record_path(path, feature):
    return STATE / (path.stem + '-' + feature + '.json')


def load_record(path, feature):
    """读取并校验恢复记录；记录缺字段或被改动过就拒绝继续。"""
    journal = record_path(path, feature)
    if not journal.exists():
        return None
    record = json.loads(safe_read(journal))
    spec = FEATURES[feature]
    if (record.get('version') not in SUPPORTED_VERSIONS or record.get('configId') != path.stem
            or record.get('feature') != feature or record.get('key') != spec['key']
            or type(record.get('value')) is not type(spec['value']) or record.get('value') != spec['value']
            or record.get('phase') not in ['prepared', 'enabled', 'restored']
            or not isinstance(record.get('beforePresent'), bool)):
        raise ValueError('恢复记录与当前配置不匹配；停止操作。')
    if record['beforePresent'] and not valid_value(feature, record.get('before')):
        raise ValueError('恢复记录中的原值异常；停止操作。')
    return record


def valid_value(feature, value):
    return value in ['standard', 'simple', 'no-vm', 'off'] if feature == 'file-analysis' else type(value) is bool


def effective(feature, config):
    if feature == 'file-analysis':
        home = config.get('desktopHome')
        if home is not None:
            return home in ['standard', 'simple']
        return config.get('chatTabEnabled') is True and config.get('chatAdvancedFileAnalysisEnabled') is True
    if feature == 'scheduled-tasks':
        return config.get('scheduledTasksEnabled', True) is True
    return config.get(FEATURES[feature]['key']) is True


def matches(config, key, present, value):
    return (key in config) == present and (not present or type(config[key]) is type(value) and config[key] == value)


LOG_TAIL_BYTES = 262144
CLI_MARKER = 'claude-code/'


def _argument(args, name):
    """取 `--name value` 或 `--name=value` 的值；没有就返回空串。"""
    for index, item in enumerate(args):
        if item == name and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(name + '='):
            return item[len(name) + 1:]
    return ''


def code_session_flags():
    """只读正在运行的 Code CLI 进程，确认桌面把设置传给了会话。

    桌面端实际用两个参数下发：`--settings` 带 skipWebFetchPreflight，
    `--managed-settings` 带 `{env:{ENABLE_TOOL_SEARCH:…}}`（旧路径也可能
    直接放进进程环境）。这里只读 argv/env，用来判断“开关打开后，新会话
    是否真的带上了”；不创建会话，也不向会话发送内容。读不到返回 None。
    """
    try:
        listing = official.core.run('ps', '-axo', 'pid=,command=').stdout.decode()
    except Exception:
        return None
    sessions = []
    for line in listing.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, command = parts
        if CLI_MARKER not in command or 'Contents/MacOS/claude' not in command:
            continue
        if '--output-format' not in command:
            continue
        try:
            args = shlex.split(command)
        except ValueError:
            args = command.split()
        settings = _argument(args, '--settings')
        managed = _argument(args, '--managed-settings')
        try:
            env = official.core.run('ps', 'eww', '-p', pid).stdout.decode()
        except Exception:
            env = ''
        sessions.append({'pid': pid,
                         'preflight': ('skipWebFetchPreflight' in settings
                                       or 'skipWebFetchPreflight' in managed),
                         'toolSearch': ('ENABLE_TOOL_SEARCH' in managed
                                        or 'ENABLE_TOOL_SEARCH=' in env)})
    return {'count': len(sessions),
            'preflight': any(item['preflight'] for item in sessions),
            'toolSearch': any(item['toolSearch'] for item in sessions)}


PLUGIN_SCAN_LIMIT = 4000


def _bundle_count(root, budget):
    """数一个插件目录里的 .mcpb/.dxt 文件和解包后的扩展清单。

    只读、跳过符号链接和官方自己的 `.mcpb-cache`；`budget` 是总扫描上限，
    防止插件目录很大时拖慢状态检测。返回 (扩展数, 剩余预算)。
    """
    count = 0
    stack = [(root, 0)]
    while stack and budget > 0:
        directory, depth = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            budget -= 1
            if budget <= 0:
                break
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name == '.mcpb-cache' or depth >= 3:
                        continue
                    manifest = pathlib.Path(entry.path) / 'manifest.json'
                    if manifest.is_file() and not manifest.is_symlink():
                        try:
                            if manifest.stat().st_size <= 262144:
                                info = json.loads(safe_read(manifest))
                                if isinstance(info, dict) and (
                                        'dxt_version' in info or 'manifest_version' in info):
                                    count += 1
                                    continue
                        except (OSError, ValueError):
                            pass
                    stack.append((pathlib.Path(entry.path), depth + 1))
                elif entry.name.lower().endswith(('.mcpb', '.dxt')):
                    count += 1
            except OSError:
                continue
    return count, budget


def plugin_extensions(base=None):
    """只读统计官方插件里打包的桌面扩展（.mcpb）。

    桌面扩展只能随插件安装；这里读官方 `installed_plugins.json` 和插件
    目录，返回插件数量与检测到的扩展数量。只返回计数，不返回插件名、
    路径或内容；读不到返回 None，不猜测。
    """
    base = pathlib.Path(base) if base is not None else (
        DATA / 'local-agent-mode-sessions')
    try:
        base = base.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    registries = []
    layout = False
    try:
        accounts = [path for path in base.iterdir() if path.is_dir() and not path.is_symlink()]
    except OSError:
        return None
    for account in accounts:
        try:
            orgs = [path for path in account.iterdir() if path.is_dir() and not path.is_symlink()]
        except OSError:
            continue
        layout = layout or bool(orgs)
        for org in orgs:
            registry = org / 'cowork_plugins' / 'installed_plugins.json'
            if registry.is_file() and not registry.is_symlink():
                registries.append(registry)
    if not registries:
        # 目录结构对得上、但没有安装记录：确实是 0 个插件，不是“读不到”。
        return {'plugins': 0, 'bundles': 0, 'complete': True} if layout else None
    plugins = bundles = 0
    budget = PLUGIN_SCAN_LIMIT
    read = False
    for registry in registries:
        try:
            data = json.loads(safe_read(registry))
        except (OSError, ValueError):
            continue
        read = True
        entries = data.get('plugins') if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            continue
        for value in entries.values():
            for record in value if isinstance(value, list) else [value]:
                if not isinstance(record, dict):
                    continue
                install = record.get('installPath')
                if not isinstance(install, str):
                    continue
                path = pathlib.Path(install)
                if not path.is_absolute() or path.is_symlink():
                    continue
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if base != resolved and base not in resolved.parents:
                    continue
                if not resolved.is_dir():
                    continue
                plugins += 1
                found, budget = _bundle_count(resolved, budget)
                bundles += found
    if not read:
        return None
    return {'plugins': plugins, 'bundles': bundles, 'complete': budget > 0}


def config_health(path=None):
    """只读 Claude 自己日志里的 3P 配置自检结果。

    Claude 启动或配置变化后会写一行 `[custom-3p] ConfigHealth recomputed
    { state: '…' }`。这里只读日志尾部，用来告诉用户“Claude 是否接受了
    这份配置”；读不到就返回 None，不猜测。
    """
    path = pathlib.Path(path) if path is not None else (
        pathlib.Path.home() / 'Library/Logs/Claude-3p/main.log')
    try:
        with path.open('rb') as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - LOG_TAIL_BYTES))
            text = handle.read().decode('utf-8', 'replace')
    except OSError:
        return None
    matches = re.findall(
        r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[info\] \[custom-3p\] ConfigHealth recomputed \{ state: '([a-z_]+)'",
        text)
    if not matches:
        return None
    at, state = matches[-1]
    health = {'state': state, 'at': at, 'healthy': state == 'healthy'}
    # 这次自检早于最近一次开关修改时，不能拿旧结果当新配置的证明。
    try:
        newest = max((p.stat().st_mtime for p in STATE.glob('*.json') if p.is_file()), default=None)
        checked = time.mktime(time.strptime(at, '%Y-%m-%d %H:%M:%S'))
        health['stale'] = bool(newest is not None and checked < newest - 1)
    except (OSError, ValueError):
        health['stale'] = False
    return health


def report(application=None):
    """返回 UI 需要的配置状态；不包含密钥或完整配置内容。

    合并检测时可传入已经算好的正式版状态，避免重复校验签名和备份。
    """
    application = official.status() if application is None else application
    result = {'version': application.get('version', '未知'),
              'supported': application.get('supported', False),
              'running': application.get('running', False),
              'features': [], 'message': ''}
    try:
        path, config, _, _ = active_config()
        check_local(config)
        result['configWritable'] = os.access(path.parent, os.W_OK)
        # 官方可能在新版里删掉某个设置；只读核对键是否还在。
        try:
            keys = official.config_keys()
        except Exception:
            keys = None
        for identifier, spec in FEATURES.items():
            record = load_record(path, identifier)
            owned = bool(record and record['phase'] != 'restored')
            available = keys is None or spec['key'] in keys
            enabled = effective(identifier, config) and available
            result['features'].append({'id': identifier, 'title': spec['title'],
                'enabled': enabled, 'canRestore': owned, 'available': available,
                'message': ('此版本没有这个设置' if not available
                            else '已启用' if enabled else '未启用'),
                'managedByUnlocker': owned})
        # 桌面扩展只能随插件安装；给出计数，让“这一项到底有没有东西可加载”可判断。
        try:
            desktop_extensions = plugin_extensions()
        except Exception:
            desktop_extensions = None
        if desktop_extensions is not None:
            result['desktopExtensions'] = desktop_extensions
        result['message'] = '官方配置可用；开关只改配置，实际功能以 Claude 为准。'
        sessions = code_session_flags()
        if sessions is not None:
            result['codeSessions'] = sessions
        health = config_health()
        if health is not None:
            result['configHealth'] = health
            if not health['healthy']:
                result['message'] = ('Claude 的配置自检结果是 ' + health['state'] +
                                     '；开关仍已写入，请按提示检查连接或配置。')
            elif health.get('stale'):
                result['message'] = '开关已写入；Claude 下次启动后配置自检会更新。'
        if not application['supported'] or application.get('recoveryRequired'):
            result['configWritable'] = False
            result['message'] = application['message']
    except Exception:
        result.update(configWritable=False, message='配置无法安全读取或受管理策略控制；停止修改。')
    result['boundaries'] = [
        {'title': '本地记忆', 'message': 'Cowork 与 Code 的项目记忆按官方逻辑运行。'},
        {'title': '历史检索', 'message': 'Code 有官方会话检索；Chat 的跨对话检索仍需官方账号。'},
        {'title': '夜间摘要', 'message': 'Chat 的夜间摘要需要官方账号；Code 的自动整理使用独立可用性开关。'},
        {'title': '跳过全部审批', 'message': '保留官方动作审查，只提供官方自动审批选项。'},
        {'title': 'Claude Design', 'message': '需要官方 Design 授权；第三方 API 不能替代。'},
        {'title': 'Claude Security', 'message': '需要官方组织服务与 GitHub 连接。'},
        {'title': '联网搜索', 'message': '沿用官方 Web Search；能否使用取决于你的 API 网关。'},
        {'title': '语音输入', 'message': 'Dictation 要求网关支持交互式 OIDC 并提供 user/dictation 端点；'
                                       '只开配置开关无法补齐。'},
        {'title': '桌面扩展', 'message': '桌面扩展只能随插件加载；本地 .mcpb 直接安装还需要官方'
                                       '组织策略后端，第三方部署没有。'},
    ]
    return result


def change(feature, restore=False):
    """开启或恢复一个功能；先写恢复记录，再改配置。"""
    if feature not in FEATURES:
        raise ValueError('不支持的功能；停止操作。')
    check_idle()
    application = official.status()
    if not application['supported'] or application.get('recoveryRequired'):
        raise ValueError('正式应用校验未通过或需要恢复；停止修改原生配置。')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if STATE.is_symlink():
        raise ValueError('恢复目录异常；停止操作。')
    path, config, raw, metadata = active_config()
    check_local(config)
    spec = FEATURES[feature]
    key, value = spec['key'], spec['value']
    if not restore:
        try:
            keys = official.config_keys()
        except Exception:
            keys = None
        if keys is not None and key not in keys:
            raise ValueError('当前 Claude 版本没有这个设置；停止修改。')
    record = load_record(path, feature)
    if restore:
        if not record or record['phase'] == 'restored':
            raise ValueError('此功能没有本工具的恢复记录；不修改既有用户配置。')
        if matches(config, key, record['beforePresent'], record.get('before')):
            record['phase'] = 'restored'
            atomic_write(record_path(path, feature), (json.dumps(record, indent=2) + '\n').encode())
            return report()
        if not matches(config, key, True, value):
            raise ValueError('功能配置已被其他操作修改；停止恢复以保留新值。')
        if record['beforePresent']:
            config[key] = record['before']
        else:
            config.pop(key, None)
    else:
        if key in config and not valid_value(feature, config[key]):
            raise ValueError('现有功能值异常；停止修改。')
        if matches(config, key, True, value):
            return report()
        if record and record['phase'] != 'restored':
            # Covers a crash after journaling and before configuration replacement.
            if not matches(config, key, record['beforePresent'], record.get('before')):
                raise ValueError('未完成操作与当前配置冲突；停止修改。')
        else:
            if record:
                history = STATE / (record_path(path, feature).stem + '-history-' + str(time.time_ns()) + '.json')
                atomic_write(history, (json.dumps(record, indent=2) + '\n').encode())
            record = {'version': application['version'], 'configId': path.stem, 'feature': feature,
                      'key': key, 'value': value, 'beforePresent': key in config,
                      'before': config.get(key), 'phase': 'prepared'}
        # Journal contains only the changed non-secret field, never the whole connection.
        atomic_write(record_path(path, feature), (json.dumps(record, indent=2) + '\n').encode())
        config[key] = value
    check_idle()
    current_path, current, current_raw, current_meta = active_config()
    if current_path != path or current_raw != raw or current_meta != metadata:
        raise ValueError('配置在操作期间变化；停止覆盖，请重新检测。')
    check_local(current)
    atomic_write(path, (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode())
    record['phase'] = 'restored' if restore else 'enabled'
    atomic_write(record_path(path, feature), (json.dumps(record, indent=2) + '\n').encode())
    return report()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'enable', 'restore'])
    parser.add_argument('feature', nargs='?', choices=list(FEATURES))
    args = parser.parse_args()
    try:
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        if STATE.is_symlink():
            raise ValueError('恢复目录异常；停止操作。')
        with (STATE / 'operation.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command != 'status' and not args.feature:
                raise ValueError('请指定要操作的功能。')
            result = report() if args.command == 'status' else change(args.feature, args.command == 'restore')
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else '操作未完成；保留配置和恢复记录，请重新检测。'
        # JSON errors and subprocess output may contain credentials: never echo them.
        if isinstance(error, json.JSONDecodeError):
            message = '配置或恢复记录格式异常；停止操作。'
        print(json.dumps({'error': message}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
