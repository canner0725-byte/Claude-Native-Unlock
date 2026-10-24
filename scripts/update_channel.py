#!/usr/bin/env python3
"""只读探测 Claude Desktop 的官方更新渠道，登记可用构建的版本与指纹。

对应《维护方案 修订版 v2》阶段 2「官方更新代办」。这个脚本**只读**：
不登录任何账号、不下载安装包、不碰 /Applications/Claude.app、不改任何配置。

渠道地址不是猜的，是从官方 app.asar 里读出来的公式：

    {base}/api/desktop/{platform}/{arch}/squirrel/update
        ?device_id=…&version=…&os_version=…[&channelUuid=…]

其中 base 取 `autoUpdate.updateApiBase`，没有就取应用配置的 API 基址
（所以本机日志里会看到 `[updater] Update URL: https://api.anthropic.com`），
再没有才是 https://releases.claude.com。渠道 UUID 也来自官方源码：
stable 不带参数，latest=77a14fc4-…，staggered=51760d71-…。

响应是 Squirrel.Mac 的 json 服务格式：
    {"currentRelease": "3.6556.2",
     "releases": [{"version": …, "updateTo": {url, sha256, size, pub_date, …}}]}

用法：
    python3 scripts/update_channel.py                 # 探测 stable 并打印摘要
    python3 scripts/update_channel.py --channel latest
    python3 scripts/update_channel.py --json          # 机器可读
    python3 scripts/update_channel.py --offline       # 只读本机，不联网
    python3 scripts/update_channel.py --app /path/to.app

退出码：0 正常；2 找不到渠道；3 渠道响应结构不认识；4 联网失败。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import plistlib
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

DEFAULT_APP = pathlib.Path('/Applications/Claude.app')
OFFICIAL_BUNDLE = 'com.anthropic.claudefordesktop'
OFFICIAL_TEAM = 'Q6L2SF6YDW'

# 渠道基址。顺序就是官方源码 RQr() 的顺序：显式 updateApiBase → 应用配置的
# API 基址 → 这两条默认值。调用方可以用 --base 覆盖，但默认不猜。
DEFAULT_BASES = ('https://api.anthropic.com', 'https://releases.claude.com')

# 渠道标识，抄自官方源码 AQr 表。stable 不带 channelUuid 参数。
CHANNELS = {
    'stable': None,
    'latest': '77a14fc4-808e-4345-ac3e-090be671bf94',
    'staggered': '51760d71-67e2-4366-9bf5-4221cda21f61',
}

# 官方源码 BQr：只有这三种架构会被承认。
ARCHITECTURES = ('universal', 'arm64', 'x64')


class ChannelError(ValueError):
    """渠道不可用或响应结构不认识；调用方应当拒绝继续，而不是换个来源。"""


class NetworkError(ChannelError):
    """联网失败。和「结构不认识」分开，退出码不同。"""


def run(*args):
    return subprocess.run([str(a) for a in args], capture_output=True)


def app_info(app):
    """读应用的版本、包标识和签名团队；任何一项不符都拒绝。"""
    info_path = app / 'Contents/Info.plist'
    if not info_path.is_file():
        raise ChannelError('找不到 Info.plist：' + str(app))
    info = plistlib.loads(info_path.read_bytes())
    if info.get('CFBundleIdentifier') != OFFICIAL_BUNDLE:
        raise ChannelError('包标识不符：' + str(info.get('CFBundleIdentifier')))
    done = run('/usr/bin/codesign', '-dvv', app)
    signature = (done.stdout + done.stderr).decode(errors='replace')
    if 'TeamIdentifier=' + OFFICIAL_TEAM not in signature:
        raise ChannelError('不是官方签名（缺少 TeamIdentifier=' + OFFICIAL_TEAM + '）')
    return {
        'version': info.get('CFBundleShortVersionString'),
        'bundle': info.get('CFBundleIdentifier'),
        'team': OFFICIAL_TEAM,
        'path': str(app),
    }


def local_backups():
    """列出本机已有的官方备份；代办流程需要用它做对照和退路。"""
    support = pathlib.Path.home() / 'Library/Application Support/Claude 原生助手/work/official-install'
    if not support.is_dir():
        return []
    found = []
    for entry in sorted(support.glob('official-backup-*.app')):
        try:
            found.append(app_info(entry))
        except Exception:
            # 备份损坏或不是官方版时不静默跳过：记下来，让调用方看见。
            found.append({'path': str(entry), 'error': '备份无法校验'})
    return found


def system_version():
    """本机 macOS 版本。官方把它作为查询参数发给渠道。"""
    done = run('/usr/bin/sw_vers', '-productVersion')
    value = done.stdout.decode(errors='replace').strip()
    if not value:
        raise ChannelError('读不到 macOS 版本，拼不出更新地址')
    return value


def update_url(base, version, channel='stable', arch='universal',
               platform='darwin', os_version=None, device_id='probe-0000'):
    """按官方公式拼出更新地址。参数名和顺序都跟官方一致。"""
    if channel not in CHANNELS:
        raise ChannelError('未知渠道：' + str(channel))
    if arch not in ARCHITECTURES:
        raise ChannelError('未知架构：' + str(arch))
    if not version:
        raise ChannelError('缺少版本号，拼不出更新地址')
    if not os_version:
        raise ChannelError('缺少系统版本，拼不出更新地址')
    query = (f'device_id={device_id}&version={version}'
             f'&os_version={os_version}')
    uuid = CHANNELS[channel]
    if uuid:
        query += '&channelUuid=' + uuid
    return f'{base}/api/desktop/{platform}/{arch}/squirrel/update?{query}'


def parse_update_json(payload):
    """解析 Squirrel.Mac json 服务的响应。

    只认官方结构：顶层 currentRelease + releases[]，每条 updateTo 里有
    version/url。对不上就抛——不猜、不把空结果当成功。
    """
    if not isinstance(payload, dict):
        raise ChannelError('渠道响应不是 JSON 对象')
    if 'currentRelease' not in payload or 'releases' not in payload:
        raise ChannelError('渠道响应缺少 currentRelease/releases 字段')
    releases = payload['releases']
    if not isinstance(releases, list):
        raise ChannelError('渠道响应的 releases 不是数组')
    builds = []
    for item in releases:
        update = item.get('updateTo') if isinstance(item, dict) else None
        if not isinstance(update, dict):
            raise ChannelError('releases 条目缺少 updateTo')
        version = update.get('version')
        url = update.get('url')
        if not version or not url:
            raise ChannelError('updateTo 缺少 version 或 url')
        builds.append({
            'version': version,
            'url': url,
            'sha256': update.get('sha256'),
            'size': update.get('size'),
            'pub_date': update.get('pub_date'),
            'name': update.get('name'),
            'notes': update.get('notes'),
        })
    return {'currentRelease': payload['currentRelease'], 'builds': builds}


def fetch(url, timeout=20):
    """读一次渠道响应。失败即抛，不返回空结果假装成功。"""
    request = urllib.request.Request(
        url, headers={'User-Agent': 'Claude-Native-Unlock/update-channel'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(4_000_000)
            return {'status': response.status,
                    'content_type': response.headers.get('content-type', ''),
                    'body': body}
    except urllib.error.HTTPError as error:
        raise ChannelError(f'渠道返回 HTTP {error.code}：{url}')
    except Exception as error:
        raise NetworkError(f'联网失败（{type(error).__name__}）：{url}')


def head_size(url, timeout=20):
    """只取响应头，确认安装包真的存在、有多大。不下载正文。"""
    request = urllib.request.Request(
        url, method='HEAD',
        headers={'User-Agent': 'Claude-Native-Unlock/update-channel'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {'status': response.status,
                    'length': response.headers.get('x-goog-stored-content-length')
                    or response.headers.get('content-length')}
    except urllib.error.HTTPError as error:
        return {'status': error.code, 'length': None}
    except Exception as error:
        raise NetworkError(f'联网失败（{type(error).__name__}）：{url}')


def compare_versions(left, right):
    """按数字段比较版本号，返回 -1/0/1。官方版本号是点分整数。"""
    def parts(value):
        return [int(p) for p in re.findall(r'\d+', str(value or ''))]
    a, b = parts(left), parts(right)
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else 0
        y = b[i] if i < len(b) else 0
        if x != y:
            return -1 if x < y else 1
    return 0


def probe(app=DEFAULT_APP, offline=False, channel='stable', base=None,
          os_version=None, verify_download=False, arch='universal'):
    """探测渠道并返回结构化结果；任何一步失败都抛 ChannelError。"""
    local = app_info(app)
    result = {'local': local, 'backups': local_backups(), 'channel': channel,
              'arch': arch, 'feed': None}
    if offline:
        return result
    if not os_version:
        os_version = system_version()
    if base is None:
        reasons = []
        for candidate in DEFAULT_BASES:
            try:
                body = fetch(update_url(candidate, local['version'], channel,
                                        arch=arch, os_version=os_version))['body']
                parsed = parse_update_json(json.loads(body.decode('utf-8')))
            except NetworkError:
                raise
            except (ChannelError, json.JSONDecodeError) as error:
                # 记下每个基址为什么不行，最后一起报；不把失败说成“渠道为空”。
                reasons.append(f'{candidate}：{error}')
                continue
            base = candidate
            result['feed'] = {'base': candidate, 'url': update_url(
                candidate, local['version'], channel, arch=arch,
                os_version=os_version), **parsed}
            break
        if base is None:
            raise ChannelError('所有候选基址都没有给出可识别的更新元数据：'
                               + '；'.join(reasons))
    else:
        url = update_url(base, local['version'], channel, arch=arch,
                         os_version=os_version)
        body = fetch(url)['body']
        result['feed'] = {'base': base, 'url': url,
                          **parse_update_json(json.loads(body.decode('utf-8')))}

    feed = result['feed']
    feed['localIsCurrent'] = compare_versions(local['version'],
                                              feed['currentRelease']) == 0
    if verify_download:
        for build in feed['builds']:
            build['download'] = head_size(build['url'])
    return result


def render(result):
    lines = ['官方更新渠道探测（只读）', '']
    local = result['local']
    lines.append(f"本机：{local['version']} · {local['bundle']} · TeamIdentifier={local['team']}")
    lines.append(f"       {local['path']}")
    backups = result['backups']
    lines.append(f'官方备份：{len(backups)} 个')
    for backup in backups:
        if 'error' in backup:
            lines.append(f"  · {backup['path']} — {backup['error']}")
        else:
            lines.append(f"  · {backup['version']} · {backup['path']}")
    feed = result.get('feed')
    if feed is None:
        lines.append('渠道：未探测（--offline）')
        return '\n'.join(lines)
    lines.append(f"渠道：{result['channel']} · 架构 {result.get('arch', 'universal')}"
                 f" · {feed['base']}")
    lines.append(f"      {feed['url']}")
    lines.append(f"渠道当前版本：{feed['currentRelease']}"
                 + ('（与本机一致）' if feed['localIsCurrent'] else '（与本机不同）'))
    if not feed['builds']:
        lines.append('渠道未声明任何可下载构建（releases 为空）')
    for build in feed['builds']:
        size = build.get('size')
        shown = f'{int(size):,} 字节' if isinstance(size, int) else '大小未知'
        lines.append(f"  · {build['version']} · {shown}")
        lines.append(f"    {build['url']}")
        if build.get('sha256'):
            lines.append(f"    sha256 {build['sha256']}")
        if build.get('pub_date'):
            lines.append(f"    发布 {build['pub_date']}")
        download = build.get('download')
        if download:
            lines.append(f"    下载可达性：HTTP {download['status']}"
                         + (f" · {download['length']} 字节" if download['length'] else ''))
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='输出机器可读结果')
    parser.add_argument('--offline', action='store_true', help='只读本机，不联网')
    parser.add_argument('--app', default=str(DEFAULT_APP), help='要探测的应用')
    parser.add_argument('--channel', default='stable', choices=sorted(CHANNELS),
                        help='渠道（默认 stable，与官方默认一致）')
    parser.add_argument('--base', default=None,
                        help='覆盖渠道基址（默认按官方顺序自动选）')
    parser.add_argument('--arch', default='universal', choices=ARCHITECTURES,
                        help='架构（默认 universal，与官方默认一致）')
    parser.add_argument('--os-version', default=None,
                        help='查询参数里的系统版本（默认取本机）')
    parser.add_argument('--verify-download', action='store_true',
                        help='对每个构建发 HEAD，确认安装包可达（不下载正文）')
    args = parser.parse_args()
    os_version = args.os_version
    if not os_version and not args.offline:
        os_version = subprocess.run(['/usr/bin/sw_vers', '-productVersion'],
                                    capture_output=True, text=True).stdout.strip()
    try:
        result = probe(pathlib.Path(args.app), offline=args.offline,
                       channel=args.channel, base=args.base,
                       os_version=os_version,
                       verify_download=args.verify_download, arch=args.arch)
    except NetworkError as error:
        print('联网失败：' + str(error), file=sys.stderr)
        raise SystemExit(4)
    except ChannelError as error:
        print('渠道不可用：' + str(error), file=sys.stderr)
        raise SystemExit(2)
    except json.JSONDecodeError as error:
        print('渠道响应不是 JSON：' + str(error), file=sys.stderr)
        raise SystemExit(3)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))


if __name__ == '__main__':
    main()
