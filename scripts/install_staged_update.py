#!/usr/bin/env python3
"""把预先下好、已校验的官方 Claude 换装到 /Applications，并重新打上可用性补丁。

为什么单独一个脚本、而且要脱离 Claude 进程树运行：

`official_unlock.enable()` 的 `check_idle()` 要求 Claude 完全退出，而会话内的
shell 本身就在 Claude 的进程树里——退出即断线，没有机会接着装。所以换装必须
交给一个不依附于 Claude 的进程（launchd 作业），它等 Claude 退出后自己继续。

这个脚本只做四件事，全部可核对：

1. 校验暂存的构建：官方签名（TeamIdentifier）、包标识、版本号、ASAR 指纹。
   任何一项不符就停，不写 /Applications。
2. 等 Claude 退出（有上限，超时就放弃，绝不强制结束）。
3. 换装：把当前 Claude 改名留底 → 暂存构建改名进位 → 校验签名。
4. 调用官方后端的 `official_unlock enable`，让新版本按现有规则解锁。

用法（正常由 launchd 作业调用，也可手动跑）：

    python3 scripts/install_staged_update.py --stage <Claude.app> \
        --expect-version 3.9683.0 --expect-asar <sha256>

    --wait 3600        最多等 Claude 退出多少秒（默认 3600）
    --no-relaunch      装完不重新打开 Claude
    --log <path>       日志位置（默认写用户日志目录）

退出码：0 成功；2 校验不通过；3 等退出超时；4 换装失败；5 打补丁失败。
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import pathlib
import plistlib
import struct
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = pathlib.Path('/Applications/Claude.app')
BACKEND = pathlib.Path(
    '/Applications/Claude 原生助手.app/Contents/Resources/Backend/NativeUnlockBackend')
OFFICIAL_TEAM = 'Q6L2SF6YDW'
BUNDLE_ID = 'com.anthropic.claudefordesktop'

# 这个脚本只用标准库：它要在 launchd 里跑，那时仓库的 .venv 未必还在。
# ASAR 指纹这里自己算，不 import 仓库模块。


def log(message, path=None):
    line = f'{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {message}'
    print(line, flush=True)
    if path:
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')


def run(*args, timeout=None):
    return subprocess.run([str(a) for a in args], capture_output=True,
                          text=True, timeout=timeout)


def claude_running():
    """Claude 主进程或 Helper 还在跑就算没退出。"""
    done = run('ps', '-axo', 'comm=')
    prefix = str(TARGET / 'Contents') + '/'
    return any(prefix in line for line in done.stdout.splitlines())


def verify_stage(stage, version, asar_hash):
    """三重校验：官方签名、包标识、版本与 ASAR 指纹。失败即拒绝。"""
    if not (stage / 'Contents/Info.plist').is_file():
        raise ValueError('暂存目录里没有 Claude.app')
    info = plistlib.loads((stage / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != BUNDLE_ID:
        raise ValueError('包标识不是官方 Claude：' + str(info.get('CFBundleIdentifier')))
    if info.get('CFBundleShortVersionString') != version:
        raise ValueError(f'版本不符：暂存的是 {info.get("CFBundleShortVersionString")}，'
                         f'期望 {version}')
    done = run('codesign', '--verify', '--deep', '--strict', stage)
    if done.returncode != 0:
        raise ValueError('暂存构建的签名校验没通过')
    done = run('codesign', '-dvv', stage)
    if f'TeamIdentifier={OFFICIAL_TEAM}' not in (done.stdout + done.stderr):
        raise ValueError('暂存构建不是 Anthropic 官方签名')
    # ASAR 指纹要和下载时算的一致，确认换装的就是校验过的那一份。
    digest = hashlib.sha256((stage / 'Contents/Resources/app.asar').read_bytes()).hexdigest()
    if digest != asar_hash:
        raise ValueError(f'ASAR 指纹不符：暂存 {digest}，期望 {asar_hash}')
    return digest


def swap(stage, keep):
    """当前 Claude 改名留底，暂存构建进位；第二步失败就退回去。"""
    stamp = str(time.time_ns())
    retired = keep / f'Claude-before-{stamp}.app'
    if TARGET.exists():
        TARGET.rename(retired)
    try:
        stage.rename(TARGET)
    except Exception:
        if retired.exists() and not TARGET.exists():
            retired.rename(TARGET)
        raise
    done = run('codesign', '--verify', '--deep', '--strict', TARGET)
    if done.returncode != 0:
        # 新装的不合格：换回去，绝不留一个装坏的 Claude。
        broken = keep / f'Claude-broken-{stamp}.app'
        TARGET.rename(broken)
        if retired.exists():
            retired.rename(TARGET)
        raise ValueError('换装后签名校验失败；已退回原版')
    return retired


def patch_and_report(log_path):
    """调用官方后端的 enable，让新版本按现有规则解锁。"""
    if not BACKEND.is_file():
        raise ValueError('找不到助手的后端：' + str(BACKEND))
    done = run(BACKEND, 'official_unlock', 'enable', timeout=1800)
    payload = None
    for line in done.stdout.splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                pass
    if payload is None:
        log('后端没有返回可解析的结果：' + done.stdout[:400], log_path)
        if done.stderr:
            log('后端 stderr：' + done.stderr[-800:], log_path)
        raise ValueError('后端没有返回结果')
    if payload.get('error'):
        raise ValueError(payload['error'])
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, type=pathlib.Path)
    parser.add_argument('--expect-version', required=True)
    parser.add_argument('--expect-asar', required=True)
    parser.add_argument('--keep', type=pathlib.Path, default=None,
                        help='留底目录；默认与暂存同级')
    parser.add_argument('--wait', type=int, default=3600)
    parser.add_argument('--no-relaunch', action='store_true')
    parser.add_argument('--log', type=pathlib.Path, default=None)
    args = parser.parse_args()

    log_path = args.log
    keep = args.keep or args.stage.parent
    keep.mkdir(parents=True, exist_ok=True)

    # 单实例：两个作业同时跑会互相把 /Applications 搬来搬去。
    with (keep / 'install.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('另一个安装正在进行；本次退出。', log_path)
            return 0

        try:
            log(f'校验暂存构建 {args.stage}（期望 {args.expect_version}）', log_path)
            verify_stage(args.stage, args.expect_version, args.expect_asar)
            log('校验通过：官方签名、包标识、版本、ASAR 指纹一致', log_path)
        except Exception as error:
            log('拒绝换装：' + str(error), log_path)
            return 2

        log(f'等待 Claude 退出（最多 {args.wait} 秒，不会强制结束）', log_path)
        deadline = time.time() + args.wait
        while claude_running():
            if time.time() > deadline:
                log('等待超时；没有改动 Claude，暂存构建仍保留。', log_path)
                return 3
            time.sleep(2)
        log('Claude 已退出', log_path)

        try:
            retired = swap(args.stage, keep)
            log(f'换装完成；原版留在 {retired}', log_path)
        except Exception as error:
            log('换装失败：' + str(error), log_path)
            return 4

        try:
            result = patch_and_report(log_path)
            log('解锁结果：' + json.dumps(
                {k: result.get(k) for k in
                 ('enabled', 'version', 'message', 'patchRevision', 'backupVerified')},
                ensure_ascii=False), log_path)
        except Exception as error:
            log('打补丁失败：' + str(error) + '（Claude 已是官方 3.9683.0，可手动开启）',
                log_path)
            return 5

        if not args.no_relaunch:
            done = run('/usr/bin/open', TARGET)
            log('已重新打开 Claude' if done.returncode == 0
                else '重新打开失败：' + done.stderr.strip(), log_path)
        log('完成', log_path)
        return 0


if __name__ == '__main__':
    sys.exit(main())
