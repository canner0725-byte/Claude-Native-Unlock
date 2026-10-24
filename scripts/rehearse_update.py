#!/usr/bin/env python3
"""演练一次官方更新代办，全程只读、不写任何东西。

对应《维护方案 修订版 v2》阶段 2「官方更新代办」。这个脚本回答的问题是：

> 如果官方刚发布了新版本，本工具**会不会**知道、会不会安全地让路？

它按真实顺序走一遍判定，但每一步都只观察、不改动：

1. **渠道**：官方更新渠道现在声明了哪个版本？（复用 update_channel.py）
2. **本机**：装的是哪个版本、有没有补丁、是不是中断状态？
3. **对照**：渠道版本 vs 本机版本 vs 已适配版本表，得出「无需动作 /
   需要让路 / 需要适配」的结论。
4. **让路路径**：如果确实需要让路，列出 prepare-update 会做什么、
   会检查什么前提——但**不执行**。真正执行要人来点。

为什么不让脚本自动执行 prepare-update：它会把已安装的 Claude 换回官方原版，
是会影响用户当前正在用的应用的操作。演练脚本只负责把「到时候会发生什么」
讲清楚，执行留给界面上的按钮。

用法：
    python3 scripts/rehearse_update.py                 # 完整演练（联网）
    python3 scripts/rehearse_update.py --offline       # 不联网，只看本机
    python3 scripts/rehearse_update.py --json          # 机器可读
    python3 scripts/rehearse_update.py --app /path/to.app

退出码：0 演练完成；4 联网失败（无法给出结论）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'src'))

import update_channel  # noqa: E402

def version_key(value):
    """版本号排序键：按数字段比，避免 '3.9683.0' 排在 '3.6556.2' 前面。"""
    return [int(p) for p in value.split('.') if p.isdigit()]


def adapted_versions():
    """读出登记表覆盖的版本。

    登记表就是 native_unlock 里的 ADAPTER_* 字典，每个带 version 和固定
    文件指纹。这里只读模块常量，不调用任何写入函数——演练脚本不能有副作用。
    """
    import native_unlock as core
    versions = set()
    for name in dir(core):
        if not name.startswith('ADAPTER_'):
            continue
        adapter = getattr(core, name)
        if isinstance(adapter, dict) and adapter.get('version'):
            versions.add(adapter['version'])
    return sorted(versions, key=version_key)


def local_state(app):
    """读本机安装状态。只读 backend 的 status 结果，不改任何东西。"""
    sys.path.insert(0, str(ROOT / 'src'))
    import backend
    backend.configure()
    import official_unlock
    try:
        return official_unlock.status()
    except Exception as error:  # 状态读不出来也要如实报告，不能当正常
        return {'error': f'{type(error).__name__}: {error}'}


def decide(local, feed, adapted):
    """按渠道版本、本机版本、已适配版本给出下一步。

    结论只有三种，任何一种都必须说清楚依据：
    - up_to_date：渠道没有比本机新的版本；
    - needs_adaptation：渠道有新版，但登记表里没有它——不能打补丁；
    - needs_yield：渠道有新版，且本机装着旧补丁——要先让路。
    """
    if feed is None:
        return {'kind': 'unknown', 'reason': '未探测渠道（--offline），无法判断是否有更新'}
    newest = feed.get('currentRelease')
    installed = (local or {}).get('version')
    if not newest or not installed:
        return {'kind': 'unknown', 'reason': '读不到版本号，无法判断'}
    order = update_channel.compare_versions(newest, installed)
    if order <= 0:
        return {'kind': 'up_to_date',
                'reason': f'渠道版本 {newest} 不高于本机 {installed}'}
    patched = bool((local or {}).get('enabled'))
    if newest not in adapted:
        return {'kind': 'needs_adaptation', 'reason':
                f'渠道有 {newest}，但登记表里没有这个版本；不识别就不写入',
                'patched': patched}
    if patched:
        return {'kind': 'needs_yield', 'reason':
                f'渠道有已适配的 {newest}，本机装着旧补丁；需要先恢复官方原版',
                'patched': True}
    return {'kind': 'ready_to_update', 'reason':
            f'渠道有已适配的 {newest}，本机没有补丁；可以直接安装官方更新',
            'patched': False}


def yield_plan(local):
    """列出 prepare-update 会做和不会做的事——只描述，不执行。"""
    return {
        'command': 'python3 src/backend.py official_unlock prepare-update',
        'executed': False,
        'requires': [
            'Claude 已经完全退出（脚本不会强制结束正在运行的会话）',
            '安装记录处于可恢复阶段（中断状态会先走恢复）',
        ],
        'will': [
            '把打过补丁的 Claude 换回完整官方签名和官方文件',
            '保留安装记录与官方备份，不删除会话或配置',
        ],
        'will_not': [
            '不会下载或安装官方更新（那一步由官方更新器完成）',
            '不会删除官方备份、用户数据或会话',
        ],
        'running': bool((local or {}).get('running')),
        'recoveryRequired': bool((local or {}).get('recoveryRequired')),
    }


def rehearse(app, offline=False, channel='stable'):
    local = local_state(app)
    adapted = adapted_versions()
    feed = None
    if not offline:
        probe = update_channel.probe(app, channel=channel)
        feed = probe['feed']
    decision = decide(local, feed, adapted)
    plan = yield_plan(local) if decision['kind'] in ('needs_yield', 'needs_adaptation') else None
    return {
        'app': str(app),
        'channel': channel,
        'local': local,
        'adapted': adapted,
        'feed': feed,
        'decision': decision,
        'yieldPlan': plan,
    }


def render(result):
    lines = ['官方更新代办演练（只读，不执行任何写操作）', '']
    local = result['local']
    if 'error' in local:
        lines.append('本机状态：读取失败 — ' + local['error'])
    else:
        lines.append(f"本机版本：{local.get('version')}"
                     f" · 补丁 {'已装' if local.get('enabled') else '未装'}"
                     f" · 支持 {'是' if local.get('supported') else '否'}")
        if local.get('running'):
            lines.append('Claude 正在运行（让路前需要先正常退出）')
    lines.append('已适配版本：' + (', '.join(result['adapted']) or '（登记表为空）'))
    feed = result['feed']
    if feed is None:
        lines.append('渠道：未探测（--offline）')
    else:
        lines.append(f"渠道版本：{feed['currentRelease']}（{result['channel']}）")
        for build in feed['builds']:
            lines.append(f"  · {build['version']} · sha256 {build.get('sha256') or '未提供'}")
    decision = result['decision']
    lines.append('')
    lines.append(f"结论：{decision['kind']}")
    lines.append(f"依据：{decision['reason']}")
    plan = result.get('yieldPlan')
    if plan:
        lines.append('')
        lines.append('让路路径（本次未执行）：' + plan['command'])
        lines.append('  前提：')
        for item in plan['requires']:
            lines.append('    - ' + item)
        lines.append('  会做：')
        for item in plan['will']:
            lines.append('    - ' + item)
        lines.append('  不会做：')
        for item in plan['will_not']:
            lines.append('    - ' + item)
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='输出机器可读结果')
    parser.add_argument('--offline', action='store_true', help='不联网，只看本机')
    parser.add_argument('--app', default=str(update_channel.DEFAULT_APP),
                        help='演练用的应用（默认 /Applications/Claude.app）')
    parser.add_argument('--channel', default='stable',
                        choices=sorted(update_channel.CHANNELS), help='渠道')
    args = parser.parse_args()
    try:
        result = rehearse(pathlib.Path(args.app), offline=args.offline,
                          channel=args.channel)
    except update_channel.NetworkError as error:
        print('联网失败，无法给出结论：' + str(error), file=sys.stderr)
        raise SystemExit(4)
    except update_channel.ChannelError as error:
        print('渠道不可用，无法给出结论：' + str(error), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str)
          if args.json else render(result))


if __name__ == '__main__':
    main()
