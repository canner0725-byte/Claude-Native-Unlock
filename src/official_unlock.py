#!/usr/bin/env python3
"""Local, version-pinned switch for the installed Claude application."""
import argparse
import fcntl
import json
import pathlib
import platform
import plistlib
import shutil
import struct
import sys
import time

import native_unlock as core

TARGET = pathlib.Path('/Applications/Claude.app')
STATE = core.WORK / 'official-install'
MAIN = '.vite/build/index.chunk-Cyt2WTko.js'
ASAR = 'Contents/Resources/app.asar'
PATCH_REVISION = 6


def running():
    prefix = str(TARGET / 'Contents') + '/'
    return any(prefix in line for line in core.run('ps', '-axo', 'comm=').stdout.decode().splitlines())


def manifest():
    path = STATE / 'manifest.json'
    return json.loads(path.read_text()) if path.exists() else None


def save(data):
    temp = STATE / 'manifest.pending'
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(STATE / 'manifest.json')


def verify_patched(app, data):
    data = effective_record(app, data)
    for path, digest in data['installedFiles'].items():
        if core.sha((app / path).read_bytes()) != digest:
            raise ValueError('已安装文件发生变化；停止操作：' + path)
    core.run('codesign', '--verify', '--deep', '--strict', str(app))


def effective_record(app, data):
    # If an update stopped before the swap, the previous verified install is still usable.
    previous = data.get('upgradeFromFiles')
    if data.get('phase') == 'upgrade-prepared' and previous and core.sha((app / ASAR).read_bytes()) == previous[ASAR]:
        return dict(data, installedFiles=previous, patchRevision=data.get('upgradeFromRevision', 1))
    return data


def status():
    result = {'enabled': False, 'supported': False, 'running': running(), 'version': '未安装',
              'message': '', 'target': str(TARGET)}
    try:
        data = manifest()
        if not TARGET.exists() and data:
            backup = pathlib.Path(data['backup'])
            if backup.parent == STATE and backup.name.startswith('official-backup-'):
                core.verify_source(backup)
                result.update(enabled=True, supported=True, recoveryRequired=True, version=data.get('version', core.VERSION),
                              message='安装中断 · 请关闭开关以恢复已校验的官方备份')
                return result
        result['version'] = plistlib.loads((TARGET / 'Contents/Info.plist').read_bytes())['CFBundleShortVersionString']
        if data:
            data = effective_record(TARGET, data)
        if data and core.sha((TARGET / ASAR).read_bytes()) == data['installedFiles'][ASAR]:
            verify_patched(TARGET, data)
            result.update(enabled=True, supported=True, message='补丁已安装 · 文件校验通过')
            result['quickEntryFixed'] = data.get('patchRevision', 1) in (2, 3)
            result['cuGuidanceFixed'] = data.get('patchRevision', 1) == 3
            result['availabilityOnly'] = data.get('patchRevision', 1) == 1 or data.get('patchRevision', 1) >= 4
            result['nativeCodeUI'] = data.get('patchRevision', 1) >= 5 and result['version'] == core.ADAPTER_37337['version']
            result['nativeCodeStateUI'] = data.get('patchRevision', 1) >= 6 and result['version'] == core.ADAPTER_37337['version']
            result['rollbackAvailable'] = (data.get('patchRevision', 1) > data.get('upgradeFromRevision', PATCH_REVISION)
                                           and bool(data.get('retired')))
        else:
            core.verify_source(TARGET)
            result.update(supported=True, message='未开启 · 官方原版校验通过')
    except Exception:
        result['message'] = '版本、文件或签名不匹配；已停止修改。'
    return result


def check_idle():
    if running():
        raise ValueError('请先正常退出 Claude，再操作开关。不会强制结束正在运行的会话。')


def build(app):
    # Only deployment availability; shipped guidance, capture and execution stay intact.
    original, header, base = core.verify_source(app)
    adapter = core.source_adapter(app)
    data = bytearray(original[base:])
    feature = '1291166712:{rule:"forced",value:{enabled:!0,appScopedEnabled:!0}},1346958739:{rule:"forced",value:{major:27,minor:0}},'
    for path in [adapter['main']]:
        old = core.content(original, header, base, path).decode()
        changed = core.replace_once(old, adapter['featureAnchor'], adapter['featureAnchor'] + feature)
        if adapter.get('webAvailability'):
            changed = core.replace_once(changed, adapter['webFeatureAnchor'], adapter['webFeatureAnchor'] + adapter['webAvailability'])
        new = changed.encode()
        node = core.node(header, path)
        node['offset'], node['size'] = str(len(data)), len(new)
        block = node.get('integrity', {}).get('blockSize', 4194304)
        node['integrity'] = {'algorithm': 'SHA256', 'hash': core.sha(new), 'blockSize': block,
                             'blocks': [core.sha(new[i:i+block]) for i in range(0, len(new), block)]}
        data.extend(new)
    raw = json.dumps(header, separators=(',', ':'), ensure_ascii=False).encode()
    payload = ((len(raw)+7)//4)*4
    packed = struct.pack('<IIII', 4, payload+4, payload, len(raw)) + raw + b'\0'*(payload-4-len(raw)) + data
    (app / ASAR).write_bytes(packed)
    info_path = app / 'Contents/Info.plist'
    info = plistlib.loads(info_path.read_bytes())
    info['ElectronAsarIntegrity']['Resources/app.asar']['hash'] = core.sha(raw)
    info_path.write_bytes(plistlib.dumps(info))
    ent = plistlib.loads(core.run('codesign', '-d', '--entitlements', ':-', str(app)).stdout)
    for key in ['com.apple.application-identifier', 'com.apple.developer.team-identifier', 'keychain-access-groups']:
        ent.pop(key, None)
    ent['com.apple.security.cs.disable-library-validation'] = True
    ent_path = STATE / 'entitlements.plist'
    ent_path.write_bytes(plistlib.dumps(ent))
    core.run('codesign', '--force', '--sign', '-', '--options', 'runtime', '--entitlements', str(ent_path), str(app))
    core.run('codesign', '--verify', '--deep', '--strict', str(app))
    return {path: core.sha((app / path).read_bytes()) for path in [ASAR, *adapter['sourceFiles']]}


def clone(source, destination):
    if destination.exists():
        raise ValueError('目标已存在；停止覆盖：' + str(destination))
    # APFS copy-on-write avoids repeatedly allocating a full app; no original is deleted.
    core.run('/bin/cp', '-cR', str(source), str(destination))


def enable():
    current = status()
    if current.get('recoveryRequired'):
        raise ValueError('请先关闭开关恢复官方备份，再重新开启。')
    if current['enabled']:
        return current if current.get('availabilityOnly') and (current['version'] != core.ADAPTER_37337['version'] or current.get('nativeCodeStateUI')) else update()
    check_idle()
    if platform.machine() != 'arm64' or platform.mac_ver()[0] != '27.0.1':
        raise ValueError('当前适配仅验证 arm64 / macOS 27.0.1；停止修改。')
    core.verify_source(TARGET)
    if STATE.stat().st_dev != TARGET.parent.stat().st_dev:
        raise ValueError('备份与应用不在同一卷；无法执行可恢复替换。')
    if shutil.disk_usage(STATE).free < 2_000_000_000:
        raise ValueError('可用空间不足 2 GB；停止修改。')
    stamp = str(time.time_ns())
    stage = STATE / ('prepared-' + stamp + '.app')
    backup = STATE / ('official-backup-' + stamp + '.app')
    clone(TARGET, stage)
    installed = build(stage)
    record = {'version': core.source_adapter(TARGET)['version'], 'patchRevision': PATCH_REVISION, 'backup': str(backup), 'installedFiles': installed,
              'backgroundExperimental': True, 'phase': 'prepared', 'stage': str(stage)}
    # Persist recovery information before the first rename.
    previous = manifest()
    if previous and previous.get('version') != record['version']:
        (STATE / ('previous-version-manifest-' + stamp + '.json')).write_text(json.dumps(previous, indent=2) + '\n')
    save(record)
    core.verify_source(TARGET)
    check_idle()
    TARGET.rename(backup)
    try:
        stage.rename(TARGET)
    except Exception:
        backup.rename(TARGET)
        raise
    record['phase'] = 'enabled'
    save(record)
    return status()


def update():
    check_idle()
    current = status()
    if not current['enabled'] or not current['supported'] or current.get('recoveryRequired'):
        raise ValueError('仅能更新已校验的完整旧补丁；异常安装请先恢复。')
    if current.get('availabilityOnly') and (current['version'] != core.ADAPTER_37337['version'] or current.get('nativeCodeStateUI')):
        return current
    if platform.machine() != 'arm64' or platform.mac_ver()[0] != '27.0.1':
        raise ValueError('当前适配仅验证 arm64 / macOS 27.0.1；停止修改。')
    previous = manifest()
    backup = pathlib.Path(previous['backup'])
    if backup.parent != STATE or not backup.name.startswith('official-backup-'):
        raise ValueError('备份路径异常；停止更新。')
    core.verify_source(backup)
    verify_patched(TARGET, previous)
    if STATE.stat().st_dev != TARGET.parent.stat().st_dev or shutil.disk_usage(STATE).free < 2_000_000_000:
        raise ValueError('卷或可用空间不满足可恢复更新要求；停止修改。')
    stamp = str(time.time_ns())
    stage = STATE / ('upgrade-prepared-' + stamp + '.app')
    retired = STATE / ('retired-patched-' + stamp + '.app')
    clone(backup, stage)
    installed = build(stage)
    old = effective_record(TARGET, previous)
    record = dict(previous, installedFiles=installed, patchRevision=PATCH_REVISION,
                  phase='upgrade-prepared', stage=str(stage), retired=str(retired),
                  upgradeFromFiles=old['installedFiles'], upgradeFromRevision=old.get('patchRevision', 1))
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
    save(record)
    return status()


def rollback_update():
    """Restore the previous verified patch without re-signing or touching user data."""
    check_idle()
    current = status()
    if not current['enabled'] or not current['supported'] or current.get('recoveryRequired'):
        raise ValueError('仅能撤回已校验的完整更新；异常安装请先恢复。')
    if not current.get('rollbackAvailable'):
        raise ValueError('当前没有可撤回的补丁更新。')
    previous = manifest()
    prior = pathlib.Path(previous.get('retired', ''))
    if prior.parent != STATE or not prior.name.startswith('retired-patched-') or prior.is_symlink():
        raise ValueError('旧补丁路径异常；停止撤回。')
    old = dict(previous, installedFiles=previous['upgradeFromFiles'],
               patchRevision=previous['upgradeFromRevision'], phase='enabled')
    verify_patched(prior, old)
    verify_patched(TARGET, previous)
    if STATE.stat().st_dev != TARGET.parent.stat().st_dev or shutil.disk_usage(STATE).free < 2_000_000_000:
        raise ValueError('卷或可用空间不满足可恢复撤回要求；停止修改。')
    stamp = str(time.time_ns())
    stage = STATE / ('rollback-prepared-' + stamp + '.app')
    retired = STATE / ('rollback-retired-' + stamp + '.app')
    clone(prior, stage)
    verify_patched(stage, old)
    record = dict(old, phase='upgrade-prepared', stage=str(stage), retired=str(retired),
                  restoredFrom=str(prior), rollbackOfRevision=previous.get('patchRevision'),
                  upgradeFromFiles=previous['installedFiles'], upgradeFromRevision=previous.get('patchRevision', 1))
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
    check_idle()
    record = manifest()
    if not record:
        core.verify_source(TARGET)
        return status()
    backup = pathlib.Path(record['backup'])
    if backup.parent != STATE or not backup.name.startswith('official-backup-'):
        raise ValueError('备份路径异常；停止恢复。')
    # Recovery also covers an interrupted rename, without opening user data.
    core.verify_source(backup)
    if TARGET.exists():
        target_version = plistlib.loads((TARGET / 'Contents/Info.plist').read_bytes()).get('CFBundleShortVersionString')
        if target_version != record.get('version', core.VERSION):
            raise ValueError('Claude已升级；拒绝用旧版本备份覆盖新版。')
        if core.sha((TARGET / ASAR).read_bytes()) == core.source_adapter(TARGET)['asarHash']:
            core.verify_source(TARGET)
            return status()
        verify_patched(TARGET, record)
    stamp = str(time.time_ns())
    stage = STATE / ('restore-prepared-' + stamp + '.app')
    clone(backup, stage)
    core.verify_source(stage)
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
    core.verify_source(TARGET)
    return status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'enable', 'update', 'rollback-update', 'disable', 'launch'])
    args = parser.parse_args()
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
            else:
                result = status()
                if not result['supported']:
                    raise ValueError(result['message'])
                core.run('/usr/bin/open', str(TARGET))
            print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        # Never emit subprocess output, configuration contents, or credentials.
        message = str(error) if isinstance(error, ValueError) else ('另一个检测或安装正在进行；请稍后重新检测。' if isinstance(error, BlockingIOError) else '操作未完成；已保留备份和现场。请检查应用是否退出、目录权限和磁盘空间。')
        print(json.dumps({'error': message}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
