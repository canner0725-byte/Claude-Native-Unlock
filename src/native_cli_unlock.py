#!/usr/bin/env python3
"""Pinned availability gate for the shipped Code memory consolidator.

No settings, model instructions, executor, timing, or permission policy changes.
"""
import argparse
import fcntl
import json
import pathlib
import platform
import plistlib
import struct
import sys
import time

import native_unlock as core

VERSION = '2.1.295'
SOURCE_HASH = '494870c9763b5ea370b6165e7e04af589a7b2472bf0de18ffb25477f8230bdf6'
BINARY = 'Contents/MacOS/claude'
MODULE = b'/$bunfs/root/chunk-fxnac2ty.js'
OLD = b'function vFt(){let e=Q();return e?.enabled===!0||e?.available===!0}'
NEW = b'function vFt(){return!0}'.ljust(len(OLD), b' ')
TRAILER = b'\n---- Bun! ----\n'


def locations(profile):
    if profile not in ('test', 'official'):
        raise ValueError('Unknown profile')
    parent = (core.WORK / 'profile' if profile == 'test' else
              pathlib.Path.home() / 'Library/Application Support/Claude-3p')
    target = parent / 'claude-code' / VERSION / '9d6405864fdc/claude.app'
    state = core.WORK / 'native-code-memory' / profile
    desktop = core.APP if profile == 'test' else pathlib.Path('/Applications/Claude.app')
    for path in (target, state):
        # Refuse redirected caches/backups, including a symlink in any parent.
        if path.resolve() != path.absolute():
            raise ValueError('Symbolic-link path; no modification')
    return target, state, desktop


def module_layout(data):
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
    if sections != [68206592]:
        raise ValueError('Unsupported Bun section')
    section = sections[0]
    length = struct.unpack_from('<Q', data, section)[0]
    base = section + 8
    if length != 170791102 or base + length > len(data) or data[base+length-16:base+length] != TRAILER:
        raise ValueError('Unsupported Bun graph')
    offsets = struct.unpack_from('<QIIIIII', data, base + length - 48)
    if offsets != (170791054, 170634605, 134992, 6, 170791053, 0, 8191):
        raise ValueError('Unsupported module metadata')
    modules = base + offsets[1]
    matches = []
    for index in range(offsets[2] // 52):
        table = modules + index * 52
        fields = struct.unpack_from('<12I4B', data, table)
        for field in range(0, 12, 2):
            if fields[field] + fields[field+1] > offsets[0]:
                raise ValueError('Invalid module pointer')
        if data[base+fields[0]:base+fields[0]+fields[1]] == MODULE:
            matches.append((index, table, fields))
    if len(matches) != 1 or matches[0][0] != 627:
        raise ValueError('Target module locator mismatch')
    index, table, fields = matches[0]
    if fields != (170517643, 30, 134666818, 15888, 0, 0, 64607864, 27136, 64635000, 0, 170517674, 30, 1, 1, 1, 0):
        raise ValueError('Target module fingerprint mismatch')
    start = base + fields[2]
    source = data[start:start+fields[3]]
    if source.count(OLD) != 1:
        raise ValueError('Availability predicate match count != 1')
    return start + source.index(OLD), table + 24, modules + offsets[2] + index * 4


def patch_binary(data):
    if core.sha(data) != SOURCE_HASH:
        raise ValueError('Unknown CLI fingerprint; no patch')
    predicate, bytecode, source_hash = module_layout(data)
    patched = bytearray(data)
    patched[predicate:predicate+len(OLD)] = NEW
    # Bun must reparse this one module. Other module bytecode/hash records stay intact.
    patched[bytecode:bytecode+8] = b'\0' * 8
    patched[source_hash:source_hash+4] = b'\0' * 4
    return bytes(patched)


def verify_source(app):
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleShortVersionString') != VERSION or info.get('CFBundleIdentifier') != 'com.anthropic.claude-code':
        raise ValueError('Unsupported CLI version or identity')
    data = (app / BINARY).read_bytes()
    if core.sha(data) != SOURCE_HASH:
        raise ValueError('CLI fingerprint mismatch')
    module_layout(data)
    core.run('codesign', '--verify', '--deep', '--strict', str(app))
    if 'TeamIdentifier=Q6L2SF6YDW' not in core.run('codesign', '-dvv', str(app)).stderr.decode():
        raise ValueError('Not the expected official signature')
    return data


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
    target, state, desktop = locations(profile)
    result = {'profile': profile, 'target': str(target), 'version': VERSION, 'enabled': False,
              'supported': False, 'running': running(target, desktop), 'scope': 'Code Auto-dream availability only'}
    try:
        desktop_info = plistlib.loads((desktop / 'Contents/Info.plist').read_bytes())
        if desktop_info.get('CFBundleShortVersionString') not in (core.VERSION, core.ADAPTER_37337['version']):
            raise ValueError('Unsupported Desktop version')
        record = read_record(state)
        if not target.exists() and record:
            verify_source(backup_path(state, record))
            result.update(supported=True, recoveryRequired=True, message='替换中断；可恢复原版CLI')
        elif record and files(target) == record.get('installedFiles'):
            verify_installed(target, record)
            verify_source(backup_path(state, record))
            result.update(supported=True, enabled=True, message='原生Code记忆整理可用性已解锁；用户开关保持')
        else:
            verify_source(target)
            result.update(supported=True, message='官方CLI原版；支持此版本')
    except Exception:
        result['message'] = 'CLI版本、文件或签名不匹配；停止修改'
    return result


def build(app, state):
    original = verify_source(app)
    before = files(app)
    ent = plistlib.loads(core.run('codesign', '-d', '--entitlements', ':-', str(app)).stdout)
    ent_path = state / 'entitlements.plist'
    ent_path.write_bytes(plistlib.dumps(ent))
    (app / BINARY).write_bytes(patch_binary(original))
    core.run('codesign', '--force', '--sign', '-', '--options', 'runtime', '--entitlements', str(ent_path), str(app))
    core.run('codesign', '--verify', '--deep', '--strict', str(app))
    actual_ent = plistlib.loads(core.run('codesign', '-d', '--entitlements', ':-', str(app)).stdout)
    if actual_ent != ent:
        raise ValueError('CLI entitlements changed')
    after = files(app)
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    if not changed <= {BINARY, 'Contents/_CodeSignature/CodeResources'}:
        raise ValueError('Unexpected CLI resource modification')
    # The original graph stays byte-identical outside the three allowed spans, even after signing.
    base, length = 68206600, 170791102
    if (app / BINARY).read_bytes()[base:base+length] != patch_binary(original)[base:base+length]:
        raise ValueError('Bun graph differs from availability-only patch')
    return after


def enable(profile):
    target, state, desktop = locations(profile)
    current = status(profile)
    if current['enabled']:
        return current
    if not current['supported'] or current.get('recoveryRequired'):
        raise ValueError('Unsupported or interrupted installation; restore first')
    # Desktop update must not receive an old CLI adapter automatically.
    info = plistlib.loads((desktop / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleShortVersionString') not in (core.VERSION, core.ADAPTER_37337['version']):
        raise ValueError('Unsupported Desktop version')
    check_idle(target, desktop)
    if platform.machine() != 'arm64' or platform.mac_ver()[0] != '27.0.1':
        raise ValueError('Only arm64 / macOS 27.0.1 verified; no modification')
    verify_source(target)
    if state.stat().st_dev != target.parent.stat().st_dev:
        raise ValueError('Backup must be on the same volume')
    stamp = str(time.time_ns())
    stage, backup = state / ('prepared-' + stamp + '.app'), state / ('original-' + stamp + '.app')
    core.run('/bin/cp', '-cR', str(target), str(stage))
    installed = build(stage, state)
    record = {'version': VERSION, 'desktopVersion': info['CFBundleShortVersionString'], 'sourceHash': SOURCE_HASH,
              'scope': 'availability only; native user switch, timing, permissions and runner unchanged',
              'backup': str(backup), 'installedFiles': installed, 'stage': str(stage), 'phase': 'prepared'}
    save(state, record)
    verify_source(target)
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
    target, state, desktop = locations(profile)
    check_idle(target, desktop)
    record = read_record(state)
    if not record:
        verify_source(target)
        return status(profile)
    backup = backup_path(state, record)
    if target.exists() and core.sha((target / BINARY).read_bytes()) == SOURCE_HASH:
        verify_source(target)
        (state / 'manifest.json').rename(state / ('unused-manifest-' + str(time.time_ns()) + '.json'))
        return status(profile)
    verify_source(backup)
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
    verify_source(target)
    (state / 'manifest.json').rename(state / ('restored-manifest-' + str(time.time_ns()) + '.json'))
    return status(profile)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('status', 'enable', 'restore'))
    parser.add_argument('--profile', choices=('test', 'official'), default='official')
    args = parser.parse_args()
    try:
        _, state, _ = locations(args.profile)
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
