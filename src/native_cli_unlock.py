#!/usr/bin/env python3
"""Code 原生自动整理（Auto-dream）的可用性开关。

Claude Code 的 Bun 单文件里有一个“功能是否可用”的判断函数。这里只把
这个判断改成可用，并让 Bun 重新解析这一个模块；不修改用户开关、
整理策略、计时、权限或执行器。CLI 版本和二进制指纹必须完全匹配，
否则拒绝写入。

已适配：
- 2.1.295：Desktop 2.37337.1
- 2.1.299：Desktop 2.42304.0 / 2.42304.2（2.42304.2 仍内置 2.1.299 的清单）

选择桌面端已安装的最新 CLI；如果最新版尚未适配就拒绝操作，
不会退回旧版本制造“已启用”的假象。
"""
import argparse
import fcntl
import json
import pathlib
import platform
import plistlib
import re
import struct
import sys
import time

import native_unlock as core

BINARY = 'Contents/MacOS/claude'
TRAILER = b'\n---- Bun! ----\n'

# 每个适配器只描述“官方原版”的固定指纹和唯一可替换位置。
# 新增 CLI 版本时，先用只读扫描确认这些字段，再在副本上验证。
ADAPTER_21295 = {
    'version': '2.1.295',
    'subdir': '9d6405864fdc',
    'sourceHash': '494870c9763b5ea370b6165e7e04af589a7b2472bf0de18ffb25477f8230bdf6',
    'sectionOffset': 68206592,
    'graphLength': 170791102,
    'metadataOffsets': (170791054, 170634605, 134992, 6, 170791053, 0, 8191),
    'modulePath': b'/$bunfs/root/chunk-fxnac2ty.js',
    'moduleIndex': 627,
    'moduleFields': (170517643, 30, 134666818, 15888, 0, 0, 0, 0, 64635000, 0, 170517674, 30, 1, 1, 1, 0),
    'oldPredicate': b'function vFt(){let e=Q();return e?.enabled===!0||e?.available===!0}',
    'newPredicate': b'function vFt(){return!0}',
}

ADAPTER_21299 = {
    'version': '2.1.299',
    'subdir': '4fa9694b7975',
    'sourceHash': '05f5000df42e8d85d468e345539d7bbadb8516c447b00cfb5e10dc61b7786ad2',
    'sectionOffset': 75120640,
    'graphLength': 172082281,
    'metadataOffsets': (172082233, 171923208, 137228, 6, 172082232, 0, 8191),
    'modulePath': b'/$bunfs/root/chunk-1gzz1vqg.js',
    'moduleIndex': 646,
    'moduleFields': (171804825, 30, 135342673, 14608, 0, 0, 65248632, 23368, 65272000, 0, 171804856, 30, 1, 1, 1, 0),
    'oldPredicate': b'function vjt(){let e=J();return e?.enabled===!0||e?.available===!0}',
    'newPredicate': b'function vjt(){return!0}',
}

ADAPTERS = {adapter['version']: adapter for adapter in (ADAPTER_21295, ADAPTER_21299)}
SUPPORTED_DESKTOP_VERSIONS = {
    core.VERSION,
    core.ADAPTER_37337['version'],
    core.ADAPTER_42304['version'],
    core.ADAPTER_42304_2['version'],
}


def adapter_for_version(version):
    """返回指定 CLI 版本的适配器；未知版本直接拒绝。"""
    adapter = ADAPTERS.get(version)
    if adapter is None:
        raise ValueError('CLI ' + version + ' 尚未适配；停止修改')
    return adapter


def installed_versions(parent):
    """列出桌面端实际下载的 CLI 版本目录，按版本号升序。

    只接受数字版本目录，并且目录里必须存在 claude.app；符号链接和
    其他名字都忽略。这样新版本出现时不会被旧适配器悄悄顶替。
    """
    root = parent / 'claude-code'
    if not root.is_dir() or root.is_symlink():
        return []
    versions = []
    for entry in root.iterdir():
        if not entry.is_dir() or entry.is_symlink():
            continue
        if not re.fullmatch(r'\d+(?:\.\d+){1,3}', entry.name):
            continue
        if any(child.is_dir() and not child.is_symlink() and (child / 'claude.app').exists()
               for child in entry.iterdir()):
            versions.append(entry.name)
    return sorted(versions, key=lambda value: tuple(int(part) for part in value.split('.')))


def locations(profile, version=None):
    """返回目标 CLI、备份目录、桌面应用和适配器。

    version 为空时选择桌面端已安装的最新 CLI；如果这个最新版本尚未
    适配，直接拒绝，而不是退回旧版本。否则官方更新后界面可能显示
    “已启用”，实际却只改了不再使用的旧 CLI。
    显式传 version 供旧记录迁移使用。拒绝符号链接路径。
    """
    if profile not in ('test', 'official'):
        raise ValueError('Unknown profile')
    parent = (core.WORK / 'profile' if profile == 'test'
              else pathlib.Path.home() / 'Library/Application Support/Claude-3p')
    if version is None:
        versions = installed_versions(parent)
        if not versions:
            raise ValueError('未找到 Claude Code CLI 安装；停止修改')
        version = versions[-1]
        adapter = ADAPTERS.get(version)
        if adapter is None:
            raise ValueError('CLI ' + version + ' 尚未适配；停止修改')
        target = parent / 'claude-code' / version / adapter['subdir'] / 'claude.app'
        if not target.exists():
            raise ValueError('CLI 目录结构与适配记录不一致；停止修改')
    else:
        adapter = adapter_for_version(version)
        target = parent / 'claude-code' / version / adapter['subdir'] / 'claude.app'
    state = core.WORK / 'native-code-memory' / profile / adapter['version']
    desktop = core.APP if profile == 'test' else pathlib.Path('/Applications/Claude.app')
    for path in (target, state):
        # 拒绝被重定向的缓存/备份路径，包括父目录里的符号链接。
        if path.resolve() != path.absolute():
            raise ValueError('CLI 路径包含符号链接；停止修改')
    return target, state, desktop, adapter


def module_layout(data, adapter):
    """在 Bun 的 Mach-O 文件里定位目标模块。

    Bun 把所有 JS 模块打包进 __BUN 段：段头记录总长度，末尾有模块表。
    这里逐项核对段偏移、表大小、模块路径、模块索引和模块表字段；
    任何一项不匹配都说明不是已验证的 CLI，直接停止。
    """
    if struct.unpack_from('<I', data)[0] != 0xfeedfacf:
        raise ValueError('Unsupported Mach-O')
    ncmds = struct.unpack_from('<I', data, 16)[0]
    pos, sections = 32, []
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, pos)
        if size < 8 or pos + size > len(data):
            raise ValueError('Invalid Mach-O command')
        if cmd == 0x19:
            count = struct.unpack_from('<I', data, pos + 64)[0]
            if 72 + count * 80 > size:
                raise ValueError('Invalid segment')
            for index in range(count):
                section = pos + 72 + index * 80
                name = data[section:section+16].split(b'\0')[0]
                segment = data[section+16:section+32].split(b'\0')[0]
                if name == b'__bun' and segment == b'__BUN':
                    sections.append(struct.unpack_from('<I', data, section + 48)[0])
        pos += size
    if sections != [adapter['sectionOffset']]:
        raise ValueError('Unsupported Bun section')
    section = sections[0]
    length = struct.unpack_from('<Q', data, section)[0]
    base = section + 8
    if (length != adapter['graphLength'] or base + length > len(data)
            or data[base+length-16:base+length] != TRAILER):
        raise ValueError('Unsupported Bun graph')
    offsets = struct.unpack_from('<QIIIIII', data, base + length - 48)
    if offsets != adapter['metadataOffsets']:
        raise ValueError('Unsupported module metadata')

    modules = base + offsets[1]
    matches = []
    for index in range(offsets[2] // 52):
        table = modules + index * 52
        fields = struct.unpack_from('<12I4B', data, table)
        for field in range(0, 12, 2):
            if fields[field] + fields[field+1] > offsets[0]:
                raise ValueError('Invalid module pointer')
        if data[base+fields[0]:base+fields[0]+fields[1]] == adapter['modulePath']:
            matches.append((index, table, fields))
    if len(matches) != 1 or matches[0][0] != adapter['moduleIndex']:
        raise ValueError('Target module locator mismatch')
    index, table, fields = matches[0]
    if fields != adapter['moduleFields']:
        raise ValueError('Target module fingerprint mismatch')
    start = base + fields[2]
    source = data[start:start+fields[3]]
    old = adapter['oldPredicate']
    if source.count(old) != 1:
        raise ValueError('Availability predicate match count != 1')
    if len(adapter['newPredicate']) > len(old):
        raise ValueError('Replacement predicate is longer than the original')
    return start + source.index(old), table + 24, modules + offsets[2] + index * 4


def patch_binary(data, adapter):
    """只改可用性判断，并让 Bun 重新解析这一个模块。

    改动范围恰好三处：
    1. 把原判断函数替换成“始终可用”；
    2. 清空该模块的字节码缓存指针；
    3. 清空该模块的源码哈希，让 Bun 重新编译它。
    其他模块、用户设置和运行逻辑都不动。
    """
    if core.sha(data) != adapter['sourceHash']:
        raise ValueError('Unknown CLI fingerprint; no patch')
    predicate, bytecode, source_hash = module_layout(data, adapter)
    patched = bytearray(data)
    patched[predicate:predicate+len(adapter['oldPredicate'])] = adapter['newPredicate'].ljust(
        len(adapter['oldPredicate']), b' ')
    patched[bytecode:bytecode+8] = b'\0' * 8
    patched[source_hash:source_hash+4] = b'\0' * 4
    return bytes(patched)


def verify_source(app, adapter=None):
    """确认 CLI 版本、bundle ID、二进制指纹和官方签名全部匹配。"""
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    version = info.get('CFBundleShortVersionString')
    adapter = adapter or ADAPTERS.get(version)
    if adapter is None or version != adapter['version'] or info.get('CFBundleIdentifier') != 'com.anthropic.claude-code':
        raise ValueError('Unsupported CLI version or identity')
    data = (app / BINARY).read_bytes()
    if core.sha(data) != adapter['sourceHash']:
        raise ValueError('CLI fingerprint mismatch')
    module_layout(data, adapter)
    core.run('codesign', '--verify', '--deep', '--strict', str(app))
    if 'TeamIdentifier=Q6L2SF6YDW' not in core.run('codesign', '-dvv', str(app)).stderr.decode():
        raise ValueError('Not the expected official signature')
    return data, adapter


def files(app):
    return {str(p.relative_to(app)): core.sha(p.read_bytes()) for p in app.rglob('*') if p.is_file()}


def read_record(state):
    path = state / 'manifest.json'
    return json.loads(path.read_text()) if path.exists() else None


def save(state, record):
    temp = state / 'manifest.pending'
    temp.write_text(json.dumps(record, indent=2) + '\n')
    temp.replace(state / 'manifest.json')


def backup_path(state, record):
    path = pathlib.Path(record['backup'])
    if path.parent != state or not path.name.startswith('original-') or path.resolve() != path.absolute():
        raise ValueError('Unsafe backup record')
    return path


def verify_installed(app, record):
    if files(app) != record['installedFiles']:
        raise ValueError('Installed CLI changed; refuse overwrite')
    core.run('codesign', '--verify', '--deep', '--strict', str(app))


def running(target, desktop):
    prefixes = [str(p / 'Contents') + '/' for p in (target, desktop)]
    return any(any(prefix in line for prefix in prefixes)
               for line in core.run('ps', '-axo', 'comm=').stdout.decode().splitlines())


def check_idle(target, desktop):
    if running(target, desktop):
        raise ValueError('请先正常退出对应 Claude；不会强制结束会话。')


def status(profile):
    """只读检测：原版、已开启、安装中断，还是版本不匹配。"""
    target, state, desktop, adapter = locations(profile)
    result = {
        'profile': profile,
        'target': str(target),
        'version': adapter['version'],
        'enabled': False,
        'supported': False,
        'running': running(target, desktop),
        'scope': 'Code Auto-dream availability only (CLI ' + adapter['version'] + ')',
    }
    try:
        desktop_info = plistlib.loads((desktop / 'Contents/Info.plist').read_bytes())
        desktop_version = desktop_info.get('CFBundleShortVersionString', 'unknown')
        if desktop_version not in SUPPORTED_DESKTOP_VERSIONS:
            result['message'] = 'Desktop ' + desktop_version + ' 尚未适配；停止修改'
            return result
        record = read_record(state)
        if record and record.get('version') != adapter['version']:
            raise ValueError('Recovery record version mismatch')
        if not target.exists() and record:
            verify_source(backup_path(state, record), adapter)
            result.update(supported=True, recoveryRequired=True, message='替换中断；可恢复原版CLI')
        elif record and files(target) == record.get('installedFiles'):
            verify_installed(target, record)
            verify_source(backup_path(state, record), adapter)
            result.update(supported=True, enabled=True, message='原生Code记忆整理可用性已解锁；用户开关保持')
        else:
            verify_source(target, adapter)
            result.update(supported=True, message='官方CLI原版；支持此版本')
    except Exception:
        result['message'] = 'CLI版本、文件或签名不匹配；停止修改'
    return result


def build(app, state, adapter):
    """在 CLI 副本上打补丁、重新签名，并确认没有改到其他资源。"""
    original, adapter = verify_source(app, adapter)
    before = files(app)
    ent = plistlib.loads(core.run('codesign', '-d', '--entitlements', ':-', str(app)).stdout)
    ent_path = state / 'entitlements.plist'
    ent_path.write_bytes(plistlib.dumps(ent))
    (app / BINARY).write_bytes(patch_binary(original, adapter))
    core.run('codesign', '--force', '--sign', '-', '--options', 'runtime', '--entitlements', str(ent_path), str(app))
    core.run('codesign', '--verify', '--deep', '--strict', str(app))
    actual_ent = plistlib.loads(core.run('codesign', '-d', '--entitlements', ':-', str(app)).stdout)
    if actual_ent != ent:
        raise ValueError('CLI entitlements changed')
    after = files(app)
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    if not changed <= {BINARY, 'Contents/_CodeSignature/CodeResources'}:
        raise ValueError('Unexpected CLI resource modification')
    # 签名后再次核对：除三处允许的改动外，Bun 模块图必须逐字节不变。
    base = adapter['sectionOffset'] + 8
    length = adapter['graphLength']
    if (app / BINARY).read_bytes()[base:base+length] != patch_binary(original, adapter)[base:base+length]:
        raise ValueError('Bun graph differs from availability-only patch')
    return after


def enable(profile):
    """安装可用性补丁；先备份原版 CLI，再替换。"""
    target, state, desktop, adapter = locations(profile)
    current = status(profile)
    if current['enabled']:
        return current
    if not current['supported'] or current.get('recoveryRequired'):
        raise ValueError('Unsupported or interrupted installation; restore first')
    # Desktop 更新后不能把旧 CLI 适配器套到新版本。
    info = plistlib.loads((desktop / 'Contents/Info.plist').read_bytes())
    desktop_version = info.get('CFBundleShortVersionString', 'unknown')
    if desktop_version not in SUPPORTED_DESKTOP_VERSIONS:
        raise ValueError('Desktop ' + desktop_version + ' 尚未适配；停止修改')
    check_idle(target, desktop)
    if platform.machine() != 'arm64' or platform.mac_ver()[0] != '27.0.1':
        raise ValueError('Only arm64 / macOS 27.0.1 verified; no modification')
    verify_source(target, adapter)
    if state.stat().st_dev != target.parent.stat().st_dev:
        raise ValueError('Backup must be on the same volume')
    stamp = str(time.time_ns())
    stage, backup = state / ('prepared-' + stamp + '.app'), state / ('original-' + stamp + '.app')
    core.run('/bin/cp', '-cR', str(target), str(stage))
    installed = build(stage, state, adapter)
    record = {
        'version': adapter['version'],
        'desktopVersion': info['CFBundleShortVersionString'],
        'sourceHash': adapter['sourceHash'],
        'scope': 'availability only; native user switch, timing, permissions and runner unchanged',
        'backup': str(backup),
        'installedFiles': installed,
        'stage': str(stage),
        'phase': 'prepared',
    }
    save(state, record)
    verify_source(target, adapter)
    check_idle(target, desktop)
    target.rename(backup)
    try:
        stage.rename(target)
    except Exception:
        backup.rename(target)
        raise
    record['phase'] = 'enabled'
    save(state, record)
    return status(profile)


def restore(profile):
    """恢复原版 CLI；保留退役补丁和恢复记录。"""
    target, state, desktop, adapter = locations(profile)
    check_idle(target, desktop)
    record = read_record(state)
    if not record:
        verify_source(target, adapter)
        return status(profile)
    if record.get('version') != adapter['version']:
        raise ValueError('Recovery record version mismatch')
    backup = backup_path(state, record)
    if target.exists() and core.sha((target / BINARY).read_bytes()) == adapter['sourceHash']:
        verify_source(target, adapter)
        (state / 'manifest.json').rename(state / ('unused-manifest-' + str(time.time_ns()) + '.json'))
        return status(profile)
    verify_source(backup, adapter)
    retired = state / ('retired-' + str(time.time_ns()) + '.app')
    if target.exists():
        verify_installed(target, record)
        target.rename(retired)
    try:
        backup.rename(target)
    except Exception:
        if retired.exists():
            retired.rename(target)
        raise
    verify_source(target, adapter)
    (state / 'manifest.json').rename(state / ('restored-manifest-' + str(time.time_ns()) + '.json'))
    return status(profile)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('status', 'enable', 'restore'))
    parser.add_argument('--profile', choices=('test', 'official'), default='official')
    args = parser.parse_args()
    try:
        _, state, _, _ = locations(args.profile)
        state.mkdir(parents=True, exist_ok=True)
        with (state / 'operation.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = {'status': status, 'enable': enable, 'restore': restore}[args.command](args.profile)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
