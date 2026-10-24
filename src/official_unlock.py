#!/usr/bin/env python3
"""正式版 Claude 的可用性补丁管理。

只做三件事：
1. 安装、更新或撤回一个只改“功能是否可用”的补丁；
2. 修改前保留完整官方应用，之后可以原样恢复；
3. 检测版本、指纹、签名和备份是否仍然匹配。

不修改 Claude 的逐应用授权、系统权限、截图过滤、拒绝、中断、
动作审查或用户数据。未知版本一律拒绝写入。
"""
import argparse
import fcntl
import json
import pathlib
import platform
import plistlib
import re
import shutil
import struct
import sys
import time

import native_unlock as core

TARGET = pathlib.Path('/Applications/Claude.app')
STATE = core.WORK / 'official-install'
MAIN = '.vite/build/index.chunk-Cyt2WTko.js'
ASAR = 'Contents/Resources/app.asar'

# 改动“安装进 Claude 的东西”时把这个数字加 1：
# 新增“只改可用性条件”的功能，或者改变签名方式这类影响安装产物的改动。
# 升级判断只看它，不再把历史条件一层层拼在一起，
# 这样以后新增功能不会被旧逻辑误判为“已经是最新”。
# revision 11：补丁改用固定的本地签名身份（见 native_unlock 的说明）。
# revision 12：适配 Desktop 3.1623.0，并按表内实际内容决定追加哪些开关。
# revision 13：3.1623.0 补回状态分组（3858164079）与每文件夹工作树（1235690166）
# 的渲染进程可用性默认值；只改可用性，不改官方分桶/工作树行为。
# revision 14：适配 Desktop 3.6556.2（官方已升到新版）；补回这一版被删掉的
# 渲染进程默认值：状态分组、每文件夹工作树、PR 自动修复、PR 面板、
# Code 权限请求分类。渲染进程表改为“形状不同即拒绝”，不覆盖官方 when/follows。
PATCH_REVISION = 14

# 只读诊断会读取这些固定文件；不会读取配置、密钥或会话。
DIAGNOSTIC_SOURCE_FILES = [
    'Contents/Info.plist',
    'Contents/MacOS/Claude',
    'Contents/Resources/app.asar.unpacked/node_modules/@ant/claude-swift/build/Release/computer_use.node',
]

# 诊断时统计这些已知的可用性开关，方便维护者判断新版是否换了位置。
KNOWN_FEATURE_FLAGS = [
    '1291166712',  # Computer Use 基础开关
    '1346958739',  # macOS 版本上限
    '2371478310',  # Code 新版工作台工具
    '857846362',   # Code 新版工作台框架
    '2120747855',  # 官方“提示建议”设置
    '1235690166',  # 每文件夹工作树
    '3264789062',  # 渲染进程：Code 工作台
    '3335190042',  # 渲染进程：Code 框架
    '3858164079',  # 渲染进程：状态分组
]


def running():
    """Claude 主进程或 Helper 仍在运行时，拒绝改文件。"""
    prefix = str(TARGET / 'Contents') + '/'
    return any(prefix in line for line in core.run('ps', '-axo', 'comm=').stdout.decode().splitlines())


def host_machine():
    """本机架构；单独成函数，测试可以换成确定值，不受宿主机影响。"""
    return platform.machine()


def host_mac_version():
    """本机 macOS 版本；同上。"""
    return platform.mac_ver()[0]


def manifest():
    """读取本工具自己的安装记录；没有就返回 None。"""
    path = STATE / 'manifest.json'
    return json.loads(path.read_text()) if path.exists() else None


def save(data):
    """先写临时文件再原子替换，避免中途断电留下半个 JSON。"""
    temp = STATE / 'manifest.pending'
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(STATE / 'manifest.json')


def verify_patched(app, data):
    """确认“补丁版”仍然是我们安装的那一份，并且签名有效。"""
    data = effective_record(app, data)
    for path, digest in data['installedFiles'].items():
        if core.sha((app / path).read_bytes()) != digest:
            raise ValueError('已安装文件发生变化；停止操作：' + path)
    core.run('codesign', '--verify', '--deep', '--strict', str(app))


def effective_record(app, data):
    """更新在改名前后中断时，识别当前实际仍是哪一个已校验版本。"""
    previous = data.get('upgradeFromFiles')
    if data.get('phase') == 'upgrade-prepared' and previous and core.sha((app / ASAR).read_bytes()) == previous[ASAR]:
        return dict(data, installedFiles=previous, patchRevision=data.get('upgradeFromRevision', 1))
    return data


CONFIG_KEY_CACHE = {}


def config_keys():
    """只读取已安装 Claude 的 3P 配置 schema，返回它认识的 flatKey 集合。

    配置键是随版本变化的：官方可能在新版里删掉某个设置。这里只读主
    chunk 里的 `flatKey:"…"` 声明，用来判断某个开关在当前版本是否真的
    存在；键不存在时界面会显示“此版本没有这个设置”，而不是给一个点了
    也没用的开关。读取失败返回 None，调用方按“未知”处理，不阻止操作。
    """
    info = plistlib.loads((TARGET / 'Contents/Info.plist').read_bytes())
    version = info.get('CFBundleShortVersionString', 'unknown')
    b, header, base = core.read_asar(TARGET / ASAR)
    digest = core.sha(b)
    if digest in CONFIG_KEY_CACHE:
        return CONFIG_KEY_CACHE[digest]
    adapters = core.adapters_for_version(version)
    if adapters:
        main = adapters[0]['main']
    else:
        main = core.discover_adapter(TARGET)['main']
    text = core.content(b, header, base, main).decode('utf-8', 'replace')
    keys = set(re.findall(r'flatKey:"([A-Za-z0-9_]+)"', text))
    CONFIG_KEY_CACHE[digest] = keys
    return keys


def original_build(app):
    """记录恢复所需的全部官方指纹；写入安装记录，恢复时以它为准。

    恢复不能依赖内置登记表：只要这份记录还在，任何已安装过的构建都能
    还原成官方原版，即使那个版本后来从登记表里删掉了。
    """
    b, header, base = core.read_asar(app / ASAR)
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    files = {}
    for path, child in core.packed_js(header):
        if int(child.get('size', 0)) <= core.PACKED_LIMIT:
            files[path] = core.sha(core.content(b, header, base, path))
    source = {}
    for path in ('Contents/Info.plist', 'Contents/MacOS/Claude', *[
            item for item in DIAGNOSTIC_SOURCE_FILES if item not in
            ('Contents/Info.plist', 'Contents/MacOS/Claude')]):
        target = app / path
        if target.is_file():
            source[path] = core.sha(target.read_bytes())
    return {
        'version': info.get('CFBundleShortVersionString'),
        'asarHash': core.sha(b),
        'files': files,
        'sourceFiles': source,
    }


def verify_backup(data, version=None):
    """备份必须在本工具目录内、通过官方签名，并与安装记录的原始指纹一致。

    判断基准是安装记录里保存的 `original`（启用时读到的官方构建指纹），
    不是内置登记表：登记表会随版本淘汰，记录不会。签名仍然必须来自官方
    （TeamIdentifier），所以被替换过的备份依旧会被拒绝。
    """
    backup = pathlib.Path(data['backup'])
    if (backup.parent != STATE or not backup.name.startswith('official-backup-')
            or backup.resolve() != backup.absolute()):
        raise ValueError('备份路径异常；停止操作。')
    core.verify_official_signature(backup)
    expected = data.get('version', core.VERSION)
    info = plistlib.loads((backup / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleShortVersionString') != expected or (
            version is not None and expected != version):
        raise ValueError('备份版本与安装记录或当前 Claude 不一致；停止操作。')
    original = data.get('original')
    if not original:
        # 旧记录没有保存原始指纹：用可信备份本身补一份并写回记录，
        # 之后恢复就不再依赖内置登记表，也不会因为版本被淘汰而失效。
        original = original_build(backup)
        data['original'] = original
        try:
            if STATE.is_dir() and not STATE.is_symlink():
                save(data)
        except OSError:
            pass
    if core.sha((backup / ASAR).read_bytes()) != original['asarHash']:
        raise ValueError('备份与安装记录的官方构建不一致；停止操作。')
    for path, digest in (original.get('sourceFiles') or {}).items():
        target = backup / path
        if not target.is_file() or core.sha(target.read_bytes()) != digest:
            raise ValueError('备份的官方组件与安装记录不一致；停止操作：' + path)
    return backup


def status():
    """只读检测。UI 和命令行都靠这一份结果决定按钮是否可用。"""
    result = {
        'enabled': False,
        'supported': False,
        'running': running(),
        'version': '未安装',
        'message': '',
        'target': str(TARGET),
        'targetExists': TARGET.exists(),
        'backupVerified': False,
        'patchRevision': 0,
        'availableRevision': PATCH_REVISION,
        'rollbackRevision': 0,
        'diagnoseAvailable': TARGET.exists(),
    }
    detected_version = None
    try:
        data = manifest()
        if not TARGET.exists() and data:
            verify_backup(data)
            result.update(
                enabled=True,
                supported=True,
                recoveryRequired=True,
                backupVerified=True,
                version=data.get('version', core.VERSION),
                patchRevision=data.get('patchRevision', 1),
                diagnoseAvailable=False,
                message='安装中断 · 请关闭开关以恢复已校验的官方备份',
            )
            return result

        info = plistlib.loads((TARGET / 'Contents/Info.plist').read_bytes())
        detected_version = info['CFBundleShortVersionString']
        result['version'] = detected_version

        if data:
            data = effective_record(TARGET, data)

        if data and core.sha((TARGET / ASAR).read_bytes()) == data['installedFiles'][ASAR]:
            verify_patched(TARGET, data)
            revision = data.get('patchRevision', 1)
            result.update(
                enabled=True,
                supported=True,
                patchRevision=revision,
                message='补丁已安装 · 文件校验通过',
            )
            result['availabilityOnly'] = revision == 1 or revision >= 4
            result['nativeCodeUI'] = revision >= 5 and detected_version in core.CODE_UI_VERSIONS
            result['nativeCodeStateUI'] = revision >= 6 and detected_version in core.CODE_UI_VERSIONS
            result['nativeCodeSideSessions'] = revision >= 7 and detected_version in core.CODE_UI_VERSIONS
            result['nativeCodeFolderWorktrees'] = revision >= 8 and detected_version in core.CODE_UI_VERSIONS
            # 签名身份变了（例如第一次启用固定本地身份）也要重打一次补丁，
            # 否则旧安装会一直停在 ad-hoc 签名上，每次更新都得重新授权。
            result['signingUpdateAvailable'] = (
                data.get('signingIdentity') != core.local_identity())
            # 密码该在登录钥匙串里。迁移没成功时仍会退回 0600 明文文件，
            # 这是旧行为，必须让用户看得见，而不是悄悄继续。
            result['signingPasswordLocation'] = core.signing_password_location()
            # 补丁签名和本地身份一致时，重打补丁不会改变系统授权；
            # 否则（官方升级后重签）这次需要重新授权一次。
            result['permissionReGrantLikely'] = bool(
                result['signingUpdateAvailable'])
            result['updateAvailable'] = (revision < PATCH_REVISION
                                         or result['signingUpdateAvailable'])
            result['rollbackAvailable'] = (
                revision > data.get('upgradeFromRevision', PATCH_REVISION)
                and bool(data.get('retired'))
            )
            if result['rollbackAvailable']:
                result['rollbackRevision'] = data.get('upgradeFromRevision', 0)
            try:
                verify_backup(data, detected_version)
                result['backupVerified'] = True
            except Exception:
                # 补丁本身还能用，但缺少可信备份时不能恢复，也不能继续更新。
                result['message'] = '补丁文件校验通过 · 官方备份异常，已停用恢复和补丁更新；请检查备份目录。'
                result['updateAvailable'] = False
        else:
            adapter = core.source_adapter(TARGET)
            core.verify_source(TARGET, adapter)
            result.update(supported=True, message='未开启 · 官方原版校验通过')
    except core.UnregisteredBuild:
        # 未知构建只诊断、不写入：给出可交给维护侧的只读报告入口。
        result['diagnoseAvailable'] = bool(detected_version)
        result['unregisteredBuild'] = True
        if detected_version is not None and not result['enabled']:
            result['message'] = (
                f'检测到 Claude {detected_version}，这个构建尚未登记规则；'
                '只做只读诊断，不会修改 Claude。'
            )
        else:
            result['message'] = '版本尚未登记；只做只读诊断，不会修改 Claude。'
    except core.FingerprintMismatch:
        result['buildMismatch'] = True
        result['diagnoseAvailable'] = bool(detected_version)
        result['message'] = '构建与登记不一致；已停止修改。请重新安装官方 Claude 或交给维护侧核对。'
    except Exception:
        if detected_version is not None and not result['enabled']:
            result['message'] = (
                f'检测到 Claude {detected_version}，但这个版本或文件尚未适配；'
                '已停止修改。可以打开 Claude 或恢复官方备份。'
            )
        else:
            result['message'] = '版本、文件或签名不匹配；已停止修改。'
        result['diagnoseAvailable'] = bool(detected_version)
    return result


def check_idle():
    """任何写操作前都要求 Claude 完全退出。"""
    if running():
        raise ValueError('请先正常退出 Claude，再操作开关。不会强制结束正在运行的会话。')


def verify_gates(text, anchor, entries):
    """补丁写完后，按表内实际生效的定义核对每个目标开关。

    对象字面量里后定义获胜，`table_definition` 取的就是最后一份，也就是
    Claude 真正会读到的值。这里逐条比对；任何一条不一致就拒绝安装，
    避免把“表里写了、运行时没生效”的补丁装到正式应用上。
    """
    for key, body in entries:
        actual = core.table_definition(text, anchor, key)
        if actual is None or actual.replace(' ', '') != body.replace(' ', ''):
            raise ValueError('补丁写入后开关未生效；停止安装：' + key)


def build(app):
    """在应用副本上写入可用性条件，重算 ASAR 完整性并临时签名。

    只改主进程功能表里目标功能的部署条件；官方提示、截图、
    授权和执行器保持原样。
    """
    adapter = core.source_adapter(app)
    original, header, base = core.verify_source(app, adapter)
    data = bytearray(original[base:])

    # Computer Use 基础开关、系统版本上限（按登记范围计算），以及各版本自己
    # 的可用性条件。两张表都只做“不存在才插入”：官方已有相同定义时跳过，
    # 有不同定义时停止——覆盖官方取值等于改官方行为。
    entries = core.split_entries(
        '1291166712:{rule:"forced",value:{enabled:!0,appScopedEnabled:!0}},'
        '1346958739:{rule:"forced",value:' + core.system_ceiling(adapter) + '},'
        + core.main_availability(adapter)
    )
    gates = {}
    for path in [adapter['main']]:
        old = core.content(original, header, base, path).decode()
        changed = core.insert_entries(old, adapter['featureAnchor'], entries, gates)
        web_availability = core.web_availability(adapter)
        web_entries = core.split_entries(web_availability)
        if web_entries:
            # 渲染进程表里的 when/follows 是官方语义（例如“仅本机会话”“跟随
            # 定时任务开关”）。同形状跳过，形状不同一律拒绝，绝不覆盖。
            changed = core.insert_entries(changed, adapter['webFeatureAnchor'],
                                          web_entries, gates, conflict='reject')
        # 改完先用系统自带的 JavaScriptCore 只解析被改动的两张表：语法不成立
        # 就绝不安装（找不到检查器同样拒绝）。
        core.check_table_syntax(changed, adapter['featureAnchor'])
        if web_entries:
            core.check_table_syntax(changed, adapter['webFeatureAnchor'])
        # 语法通过还不够：再读回表里**实际生效**的那一份定义，确认每个目标
        # 开关真的解析成我们写的值。官方改了形状或换了取值时，这里会先失败，
        # 而不是装完才发现“入口在、开关没开”。
        verify_gates(changed, adapter['featureAnchor'], entries)
        if web_entries:
            verify_gates(changed, adapter['webFeatureAnchor'], web_entries)
        # 装了 node 时再把整份文件复核一遍，能发现表以外的损坏；没有 node 不影响。
        core.check_js(changed)
        new = changed.encode()
        node = core.node(header, path)
        node['offset'], node['size'] = str(len(data)), len(new)
        block = node.get('integrity', {}).get('blockSize', 4194304)
        node['integrity'] = {
            'algorithm': 'SHA256',
            'hash': core.sha(new),
            'blockSize': block,
            'blocks': [core.sha(new[i:i+block]) for i in range(0, len(new), block)],
        }
        data.extend(new)

    # 重新打包 ASAR：固定头 + 文件索引 + 所有文件内容。
    raw = json.dumps(header, separators=(',', ':'), ensure_ascii=False).encode()
    payload = ((len(raw)+7)//4)*4
    packed = struct.pack('<IIII', 4, payload+4, payload, len(raw)) + raw + b'\0'*(payload-4-len(raw)) + data
    (app / ASAR).write_bytes(packed)

    # Info.plist 里的 ASAR 完整性摘要也要同步更新。
    info_path = app / 'Contents/Info.plist'
    info = plistlib.loads(info_path.read_bytes())
    info['ElectronAsarIntegrity']['Resources/app.asar']['hash'] = core.sha(raw)
    info_path.write_bytes(plistlib.dumps(info))

    # 没有官方私钥，只能临时签名；去掉依赖官方身份的权限项，
    # 保留运行所需的最小项。系统权限可能需要用户重新绑定。
    ent = plistlib.loads(core.run('codesign', '-d', '--entitlements', ':-', str(app)).stdout)
    for key in ['com.apple.application-identifier', 'com.apple.developer.team-identifier', 'keychain-access-groups']:
        ent.pop(key, None)
    ent['com.apple.security.cs.disable-library-validation'] = True
    ent_path = STATE / 'entitlements.plist'
    ent_path.write_bytes(plistlib.dumps(ent))
    # 优先用固定的本地身份，这样更新后系统权限不用重新授权；没有就退回 ad-hoc。
    core.sign_app(app, ent_path)

    return ({path: core.sha((app / path).read_bytes()) for path in [ASAR, *adapter['sourceFiles']]},
            gates)


def prepare_identity():
    """第一次打补丁时建立固定的本地签名身份；建不出来就退回 ad-hoc。

    只影响“更新后要不要重新授权系统权限”，不影响补丁能否使用，
    所以这里失败不报错，交给签名环节自动降级。旧版把密码明文放在签名
    目录里，这里顺手搬进登录钥匙串；搬不进去就保留原文件继续用。
    """
    if core.local_identity() is None:
        core.create_local_identity()
    core.migrate_keychain_password()


def migrate_password():
    """把签名密码从明文文件搬进登录钥匙串；已迁移时什么也不做。

    单独做成命令，是因为“已是最新 revision 且签名没变”时 update() 会直接
    返回，不会走到 prepare_identity()，用户就没有任何入口触发迁移。这条
    命令只动密码存放位置，不碰 /Applications、不改补丁、不重新签名。
    """
    before = core.signing_password_location()
    if before == 'keychain':
        return {'migrated': False, 'location': 'keychain',
                'message': '签名密码已在登录钥匙串里，无需迁移。'}
    if before == 'missing':
        return {'migrated': False, 'location': 'missing',
                'message': '没有找到签名密码；下次签名会退回 ad-hoc。'}
    allowed, reason = core.keychain_writer_is_trustworthy()
    if not allowed:
        # 闸门挡住时保留明文文件，签名照常可用；说清楚为什么不做。
        return {'migrated': False, 'location': before, 'blocked': True,
                'message': '暂时不能迁移：' + reason + '。明文文件已保留，签名仍可用。'}
    if not core.migrate_keychain_password():
        return {'migrated': False, 'location': before, 'blocked': True,
                'message': '登录钥匙串拒绝保存；明文文件已保留，签名仍可用。'}
    return {'migrated': True, 'location': core.signing_password_location(),
            'message': '签名密码已搬进登录钥匙串，明文文件已删除。'}


def clone(source, destination):
    """用 APFS 写时复制生成副本，避免重复占用一整份应用空间。"""
    if destination.exists():
        raise ValueError('目标已存在；停止覆盖：' + str(destination))
    core.run('/bin/cp', '-cR', str(source), str(destination))


def prune_old_state(keep_backup=None):
    """只保留当前官方备份；旧补丁、旧备份和旧报告都清理。

    只在补丁切换成功、安装记录已经落盘后调用。当前记录引用的备份
    永远保留；符号链接一律跳过，避免误删状态目录之外的文件。
    """
    keep = pathlib.Path(keep_backup).name if keep_backup else None
    # prepared / upgrade-prepared / restore-prepared / rollback-prepared 都是
    # 操作中途的临时副本：成功时已被改名成最终目录，留下的就是中断残留。
    prefixes = ('retired-patched-', 'rollback-retired-',
                'adaptation-report-', 'previous-version-manifest-',
                'prepared-', 'upgrade-prepared-', 'restore-prepared-',
                'rollback-prepared-')
    for entry in STATE.iterdir():
        if entry.is_symlink():
            continue
        if entry.name.startswith(prefixes) or (
                entry.name.startswith('official-backup-') and entry.name != keep):
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()


def enable():
    """安装补丁；已安装旧 revision 时自动走“更新补丁”。"""
    current = status()
    if current.get('recoveryRequired'):
        raise ValueError('请先关闭开关恢复官方备份，再重新开启。')
    if current['enabled']:
        if (current.get('patchRevision', 1) >= PATCH_REVISION
                and not current.get('signingUpdateAvailable')):
            return current
        return update()

    check_idle()
    prepare_identity()
    adapter = core.source_adapter(TARGET)
    core.check_system(adapter, host_machine(), host_mac_version())
    core.verify_source(TARGET, adapter)
    if STATE.stat().st_dev != TARGET.parent.stat().st_dev:
        raise ValueError('备份与应用不在同一卷；无法执行可恢复替换。')
    if shutil.disk_usage(STATE).free < 2_000_000_000:
        raise ValueError('可用空间不足 2 GB；停止修改。')

    stamp = str(time.time_ns())
    stage = STATE / ('prepared-' + stamp + '.app')
    backup = STATE / ('official-backup-' + stamp + '.app')
    # 先记下官方原版的完整指纹：恢复以记录为准，不依赖内置登记表。
    original = original_build(TARGET)
    clone(TARGET, stage)
    installed, gates = build(stage)
    record = {
        'version': adapter['version'],
        'patchRevision': PATCH_REVISION,
        'backup': str(backup),
        'original': original,
        'installedFiles': installed,
        'backgroundExperimental': True,
        'phase': 'prepared',
        'stage': str(stage),
        'signingIdentity': core.app_signing_identity(stage),
        'gates': gates,
    }
    # 先把恢复信息写盘，之后才做第一次改名。
    previous = manifest()
    if previous and previous.get('version') != record['version']:
        (STATE / ('previous-version-manifest-' + stamp + '.json')).write_text(
            json.dumps(previous, indent=2) + '\n')
    save(record)

    core.verify_source(TARGET, adapter)
    check_idle()
    TARGET.rename(backup)
    try:
        stage.rename(TARGET)
    except Exception:
        backup.rename(TARGET)
        raise
    record['phase'] = 'enabled'
    save(record)
    prune_old_state(record.get('backup'))
    return status()


def update():
    """把旧 revision 的补丁更新到当前 revision，只保留最新版和官方备份。"""
    current = status()
    if not current['enabled'] or not current['supported'] or current.get('recoveryRequired'):
        raise ValueError('仅能更新已校验的完整旧补丁；异常安装请先恢复。')

    # 已经是最新 revision、签名身份也没变时直接返回，不要求用户先退出 Claude。
    if (current.get('patchRevision', 1) >= PATCH_REVISION
            and not current.get('signingUpdateAvailable')):
        return current
    check_idle()
    prepare_identity()
    previous = manifest()
    if previous is None:
        raise ValueError('缺少安装记录；请先恢复官方原版并重新安装。')
    # 目标已经是补丁版，ASAR 与官方指纹必然不同，不能拿它去认适配器；
    # 系统范围按安装记录里的版本查。版本没登记就拒绝更新。
    core.check_platform(
        core.VERIFIED_SYSTEM.get(previous.get('version') or current.get('version')),
        host_machine(), host_mac_version())

    backup = verify_backup(previous, current['version'])
    verify_patched(TARGET, previous)
    if STATE.stat().st_dev != TARGET.parent.stat().st_dev or shutil.disk_usage(STATE).free < 2_000_000_000:
        raise ValueError('卷或可用空间不满足可恢复更新要求；停止修改。')

    stamp = str(time.time_ns())
    stage = STATE / ('upgrade-prepared-' + stamp + '.app')
    retired = STATE / ('retired-patched-' + stamp + '.app')
    # 旧记录没有 `original` 时，从可信备份补上；恢复从此不再依赖登记表。
    original = previous.get('original') or original_build(backup)
    clone(backup, stage)
    installed, gates = build(stage)
    old = effective_record(TARGET, previous)
    record = dict(
        previous,
        original=original,
        installedFiles=installed,
        patchRevision=PATCH_REVISION,
        signingIdentity=core.app_signing_identity(stage),
        gates=gates,
        phase='upgrade-prepared',
        stage=str(stage),
        retired=str(retired),
        upgradeFromFiles=old['installedFiles'],
        upgradeFromRevision=old.get('patchRevision', 1),
    )
    save(record)

    verify_patched(TARGET, old)
    check_idle()
    TARGET.rename(retired)
    try:
        stage.rename(TARGET)
    except Exception:
        retired.rename(TARGET)
        save(previous)
        raise
    record['phase'] = 'enabled'
    # 撤回功能已从界面移除；更新成功后不再保留旧补丁链。
    record.pop('retired', None)
    record.pop('upgradeFromFiles', None)
    record.pop('upgradeFromRevision', None)
    save(record)
    prune_old_state(record.get('backup'))
    return status()


def rollback_update():
    """撤回上一次补丁更新，回到之前的补丁版本；不动用户数据。"""
    check_idle()
    current = status()
    if not current['enabled'] or not current['supported'] or current.get('recoveryRequired'):
        raise ValueError('仅能撤回已校验的完整更新；异常安装请先恢复。')
    if not current.get('rollbackAvailable'):
        raise ValueError('当前没有可撤回的补丁更新。')

    previous = manifest()
    if previous is None:
        raise ValueError('缺少安装记录；无法撤回补丁更新。')
    prior = pathlib.Path(previous.get('retired', ''))
    if prior.parent != STATE or not prior.name.startswith('retired-patched-') or prior.is_symlink():
        raise ValueError('旧补丁路径异常；停止撤回。')
    old = dict(
        previous,
        installedFiles=previous['upgradeFromFiles'],
        patchRevision=previous['upgradeFromRevision'],
        phase='enabled',
    )
    verify_patched(prior, old)
    verify_patched(TARGET, previous)
    if STATE.stat().st_dev != TARGET.parent.stat().st_dev or shutil.disk_usage(STATE).free < 2_000_000_000:
        raise ValueError('卷或可用空间不满足可恢复撤回要求；停止修改。')

    stamp = str(time.time_ns())
    stage = STATE / ('rollback-prepared-' + stamp + '.app')
    retired = STATE / ('rollback-retired-' + stamp + '.app')
    clone(prior, stage)
    verify_patched(stage, old)
    record = dict(
        old,
        phase='upgrade-prepared',
        stage=str(stage),
        retired=str(retired),
        restoredFrom=str(prior),
        rollbackOfRevision=previous.get('patchRevision'),
        upgradeFromFiles=previous['installedFiles'],
        upgradeFromRevision=previous.get('patchRevision', 1),
    )
    save(record)

    verify_patched(TARGET, previous)
    check_idle()
    TARGET.rename(retired)
    try:
        stage.rename(TARGET)
    except Exception:
        retired.rename(TARGET)
        save(previous)
        raise
    record['phase'] = 'enabled'
    save(record)
    return status()


def disable():
    """恢复完整官方应用；保留所有备份和记录，便于再次开启。"""
    check_idle()
    record = manifest()
    if not record:
        try:
            core.verify_source(TARGET)
        except core.UnregisteredBuild:
            # 没有安装记录 = 从没打过补丁：无需恢复，只做只读说明。
            return status()
        return status()

    # 恢复也覆盖“改名前中断”的情况，不需要打开用户数据。
    backup = verify_backup(record)
    if TARGET.exists():
        target_version = plistlib.loads((TARGET / 'Contents/Info.plist').read_bytes()).get('CFBundleShortVersionString')
        if target_version != record.get('version', core.VERSION):
            raise ValueError('Claude 已升级；不会用旧版本备份覆盖新版。请重新检测。')
        # 判断目标是否已经是官方原版：拿安装记录里的原始 ASAR 指纹比，
        # 不再查内置登记表（那个版本可能已经从登记表里淘汰）。
        original = record.get('original')
        official_asar_hash = (original or {}).get('asarHash')
        if official_asar_hash is None:
            official_asar_hash = core.registered_adapter(backup)['asarHash']
        if core.sha((TARGET / ASAR).read_bytes()) == official_asar_hash:
            core.verify_official_signature(TARGET)
            # 恢复在改名后中断时，目标已经是官方原版；补记完成状态。
            if record.get('phase') != 'disabled':
                record['phase'] = 'disabled'
                save(record)
            return status()
        verify_patched(TARGET, record)

    stamp = str(time.time_ns())
    stage = STATE / ('restore-prepared-' + stamp + '.app')
    clone(backup, stage)
    core.verify_official_signature(stage)
    check_idle()
    retired = STATE / ('retired-patched-' + stamp + '.app')
    if TARGET.exists():
        TARGET.rename(retired)
    try:
        stage.rename(TARGET)
    except Exception:
        if retired.exists():
            retired.rename(TARGET)
        raise
    record.update(phase='disabled', retired=str(retired))
    save(record)
    core.verify_official_signature(TARGET)
    return status()


def prepare_update():
    """一键把 Claude 恢复成完整官方原版，供官方更新器校验。

    这是避免“官方更新一直卡在下载/签名失败”的关键步骤。
    只恢复本工具安装过的补丁，不删除备份、配置或会话。
    """
    if not TARGET.exists():
        raise ValueError('没有找到 /Applications/Claude.app。')

    current = status()
    if current.get('recoveryRequired'):
        # 目标缺失但记录仍在：这正是中断恢复要的场景，交给 disable() 处理。
        result = disable()
        result['preparedForUpdate'] = True
        result['message'] = '安装中断已恢复为完整官方原版，可以继续安装官方更新。'
        return result
    if current['running']:
        raise ValueError('请先正常退出 Claude，再为官方更新做准备。不会强制结束正在运行的会话。')

    record = manifest()
    if current['enabled']:
        result = disable()
        if result.get('enabled'):
            raise ValueError('恢复后仍检测到补丁；已停止，请重新检测。')
        result['preparedForUpdate'] = True
        result['message'] = (
            '已恢复完整官方签名和官方文件。现在可以打开 Claude 安装官方更新；'
            '更新完成后请回来重新检测。'
        )
        return result

    if record and record.get('phase') != 'disabled':
        if current.get('supported'):
            # 目标已经通过官方校验，不需要备份或恢复；补记记录即可。
            record['phase'] = 'disabled'
            save(record)
        else:
            raise ValueError(
                '检测到安装记录，但当前 Claude 不是已校验的官方版本；'
                '请先重新检测，不要直接覆盖。'
            )

    result = status()
    result['preparedForUpdate'] = True
    if result.get('supported'):
        result['message'] = '当前已是完整官方原版，可以直接打开 Claude 安装官方更新。'
    else:
        result['message'] = (
            '没有安装本工具的补丁，无需恢复。当前版本尚未适配；'
            '可以直接更新，更新后请重新检测。'
        )
    return result


def launch():
    """打开已安装的 Claude。

    未适配的新版本也可以打开；本工具只拒绝修改，不阻止用户使用
    官方应用。这里只调用 open，不写文件、不读取用户数据。
    """
    if not TARGET.exists():
        raise ValueError('没有找到 /Applications/Claude.app。')
    core.run('/usr/bin/open', str(TARGET))
    result = status()
    result['opened'] = True
    if not result.get('supported'):
        result['message'] = 'Claude 已打开。当前版本尚未适配，本工具不会修改它。'
    return result


def _packed_js_candidates(b, header, base):
    """在 ASAR 里找出包含“部署功能表”的 JS，供新版本适配参考。

    只读，不执行文件内容；跳过过大文件和已解包文件。
    """
    candidates = []

    def walk(nodes, prefix=''):
        for name, child in nodes.get('files', {}).items():
            path = prefix + '/' + name if prefix else name
            if 'files' in child:
                yield from walk(child, path)
            elif not child.get('unpacked') and 'offset' in child and path.endswith('.js'):
                yield path, child

    for path, child in walk(header):
        if int(child.get('size', 0)) > 20_000_000:
            continue
        raw = core.content(b, header, base, path)
        if b'rule:"forced"' not in raw:
            continue
        text = raw.decode('utf-8', 'replace')
        anchors = []
        for match in re.finditer(r'var ([A-Za-z0-9$_]{1,16})=\{', text):
            if 'rule:"forced"' in text[match.end():match.end()+400]:
                anchors.append('var ' + match.group(1) + '={')
        references = {flag: text.count(flag) for flag in KNOWN_FEATURE_FLAGS}
        definitions = {
            flag: len(re.findall(re.escape(flag) + r':\{', text))
            for flag in KNOWN_FEATURE_FLAGS
        }
        candidates.append({
            'path': path,
            'sha256': core.sha(raw),
            'size': len(raw),
            'featureTableAnchors': anchors[:20],
            'forcedRuleCount': text.count('rule:"forced"'),
            # references 是出现次数，definitions 才是功能表里真正写了几次。
            'knownFlagCounts': {flag: count for flag, count in references.items() if count},
            'knownFlagDefinitions': {flag: count for flag, count in definitions.items() if count},
        })
    candidates.sort(key=lambda item: item['forcedRuleCount'], reverse=True)
    return candidates


def diagnose():
    """生成只读适配报告；不修改 Claude，也不读取用户配置。"""
    if not TARGET.exists():
        raise ValueError('没有找到 /Applications/Claude.app。')

    info = plistlib.loads((TARGET / 'Contents/Info.plist').read_bytes())
    version = info.get('CFBundleShortVersionString', 'unknown')
    b, header, base = core.read_asar(TARGET / ASAR)
    report = {
        'createdAt': time.strftime('%Y-%m-%d %H:%M:%S'),
        'version': version,
        'bundleIdentifier': info.get('CFBundleIdentifier'),
        'asarSha256': core.sha(b),
        'asarSize': len(b),
        # 直接问适配表，新增版本时不会漏改这里。
        'knownVersion': bool(core.adapters_for_version(version)),
        'candidates': _packed_js_candidates(b, header, base),
        'sourceFiles': {},
        'message': '这是只读报告，没有修改 Claude。把报告交给维护者用于新版本适配。',
    }
    for path in DIAGNOSTIC_SOURCE_FILES:
        file = TARGET / path
        if file.exists():
            report['sourceFiles'][path] = core.sha(file.read_bytes())

    safe_version = re.sub(r'[^0-9A-Za-z._-]', '_', version)[:40] or 'unknown'
    report_path = STATE / ('adaptation-report-' + safe_version + '-' + str(time.time_ns()) + '.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    report['reportPath'] = str(report_path)
    report['message'] = f'已生成 {version} 的只读适配报告；未修改 Claude。报告位置：{report_path}'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'command',
        choices=['status', 'enable', 'update', 'rollback-update', 'disable',
                 'launch', 'prepare-update', 'diagnose', 'migrate-password'],
    )
    args = parser.parse_args()
    if STATE.is_symlink():
        core.emit({'error': '安装记录目录是符号链接；停止操作。'})
        sys.exit(1)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with (STATE / 'operation.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == 'status':
                result = status()
            elif args.command == 'enable':
                result = enable()
            elif args.command == 'disable':
                result = disable()
            elif args.command == 'update':
                result = update()
            elif args.command == 'rollback-update':
                result = rollback_update()
            elif args.command == 'prepare-update':
                result = prepare_update()
            elif args.command == 'diagnose':
                result = diagnose()
            elif args.command == 'migrate-password':
                result = migrate_password()
            elif args.command == 'launch':
                result = launch()
            core.emit(result)
    except Exception as error:
        # 不输出子进程内容、配置或凭据；只给用户可操作的中文提示。
        message = str(error) if isinstance(error, ValueError) else (
            '另一个检测或安装正在进行；请稍后重新检测。'
            if isinstance(error, BlockingIOError)
            else '操作未完成；已保留备份和现场。请检查应用是否退出、目录权限和磁盘空间。'
        )
        core.emit({'error': message})
        sys.exit(1)


if __name__ == '__main__':
    # 直接命令行运行也要走真实的用户数据目录，否则会读不到安装记录、
    # 把已打补丁的应用误报成未适配。backend 在同一个包里，导入即可。
    import backend
    backend.configure_module('official_unlock')
    main()
