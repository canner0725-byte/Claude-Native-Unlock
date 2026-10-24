#!/usr/bin/env python3
"""只读检查 Claude 官方桌面端有没有新版本。

渠道公式、响应解析和版本比较原本在 `scripts/update_channel.py` 里；那个
脚本只在维护机上跑，不会打进助手。助手要在「为官方更新做准备」旁边提醒，
就需要同一套逻辑随后端分发，所以把它放到 `src/`，脚本改为复用这里。

只读：不登录、不带账号、不下载安装包、不改 Claude 的任何文件。
请求只带本机 Claude 版本、macOS 版本和一个固定的探针标识，与官方更新器
自己发的查询同类；响应只取版本号。唯一写入的是本工具自己的检查结果缓存
`work/official-install/official-update.json`，用于下次启动直接显示。

失败即如实报告：联网失败、渠道响应不认识都返回 `unknown`，绝不把
「没查到」说成「已是最新」；旧缓存保留，不会被一次失败抹掉。
"""
from __future__ import annotations

import json
import platform
import plistlib
import re
import subprocess
import time

import native_unlock as core
import official_unlock as official

# 渠道基址。顺序就是官方源码 RQr() 的顺序：显式 updateApiBase → 应用配置的
# API 基址 → 这两条默认值。助手只用官方默认基址，不读取用户的第三方配置，
# 避免把用户的 API 地址带进请求或日志。
DEFAULT_BASES = ('https://api.anthropic.com', 'https://releases.claude.com')

# 渠道标识，抄自官方源码 AQr 表。stable 不带 channelUuid 参数。
CHANNELS = {
    'stable': None,
    'latest': '77a14fc4-808e-4345-ac3e-090be671bf94',
    'staggered': '51760d71-67e2-4366-9bf5-4221cda21f61',
}

# 官方源码 BQr：只有这三种架构会被承认。
ARCHITECTURES = ('universal', 'arm64', 'x64')

CHANNEL = 'stable'
# 固定探针标识：不读取官方设备标识，也不带任何用户信息。
DEVICE_ID = 'probe-0000'
USER_AGENT = 'Claude-Native-Unlock/update-channel'
# 单次请求的超时；助手启动时的自动检查最多等这么久。
TIMEOUT = 8
# 自动检查的间隔。界面只在缓存过期时才联网，手动点「检查」不受它限制。
CACHE_TTL = 6 * 3600

CACHE_NAME = 'official-update.json'


class ChannelError(ValueError):
    """渠道不可用或响应结构不认识；调用方应当拒绝给出结论。"""


class NetworkError(ChannelError):
    """联网失败。和「结构不认识」分开，提示文案不同。"""


def update_url(base, version, channel='stable', arch='universal',
               platform_name='darwin', os_version=None, device_id=DEVICE_ID):
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
    return f'{base}/api/desktop/{platform_name}/{arch}/squirrel/update?{query}'


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


# 用系统 curl 发请求，而不是 Python 的 urllib：打包后的后端用的是随包
# 附带的 Python，它的 ssl 模块在本机实测加载证书库失败（FileNotFoundError），
# 而 curl 用系统信任库，不需要在包里再带一份证书。
CURL = '/usr/bin/curl'


def fetch(url, timeout=TIMEOUT):
    """读一次渠道响应。失败即抛，不返回空结果假装成功。

    错误信息里不带完整地址：地址虽然只有版本号，但没必要写进日志。
    curl 的退出码 22 是 HTTP 错误（4xx/5xx），6/7/28/35/60 是网络或 TLS
    问题；其余退出码也按联网失败处理，不猜具体原因。
    """
    try:
        done = subprocess.run(
            [CURL, '--silent', '--show-error', '--fail', '--location',
             '--max-time', str(timeout), '--max-filesize', '4000000',
             '--user-agent', USER_AGENT, url],
            capture_output=True, timeout=timeout + 5)
    except Exception as error:
        raise NetworkError(f'联网失败（{type(error).__name__}）')
    if done.returncode == 0:
        return {'body': done.stdout}
    if done.returncode == 22:
        raise ChannelError('渠道返回 HTTP 错误')
    raise NetworkError('联网失败（curl ' + str(done.returncode) + '）')


def system_version():
    """本机 macOS 版本。官方把它作为查询参数发给渠道。"""
    value = platform.mac_ver()[0].strip()
    if not value:
        raise ChannelError('读不到 macOS 版本，拼不出更新地址')
    return value


def query(version, os_version=None, channel=CHANNEL, arch='universal',
          bases=DEFAULT_BASES, timeout=TIMEOUT):
    """按官方顺序尝试基址，返回第一个可识别的渠道结果。

    联网失败直接抛 NetworkError（换基址也没用）；结构不认识则记下原因，
    继续试下一个基址，最后一起报，不把失败说成“渠道为空”。
    """
    if not os_version:
        os_version = system_version()
    reasons = []
    for base in bases:
        url = update_url(base, version, channel, arch=arch, os_version=os_version)
        try:
            body = fetch(url, timeout=timeout)['body']
            parsed = parse_update_json(json.loads(body.decode('utf-8')))
        except NetworkError:
            raise
        except (ChannelError, json.JSONDecodeError) as error:
            reasons.append(f'{base}：{error}')
            continue
        return {'base': base, 'url': url, **parsed}
    raise ChannelError('所有候选基址都没有给出可识别的更新元数据：'
                       + '；'.join(reasons))


def installed_version():
    """读已安装 Claude 的版本号。

    这里只读 Info.plist，不校验签名：打过补丁的 Claude 是本工具的本地签名，
    校验官方 TeamIdentifier 必然失败，而提醒只需要版本号。
    """
    info = official.TARGET / 'Contents/Info.plist'
    if not info.is_file():
        return None
    try:
        value = plistlib.loads(info.read_bytes()).get('CFBundleShortVersionString')
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def cache_path():
    """检查结果的缓存位置；和安装记录放在一起。"""
    return official.STATE / CACHE_NAME


def read_cache():
    """读上次成功的检查结果；没有或损坏都当作没有，不抛异常。"""
    try:
        data = json.loads(cache_path().read_text())
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get('latest'), str):
        return None
    if not data['latest']:
        return None
    return data


def write_cache(data):
    """先写临时文件再原子替换；目录是符号链接时不写。"""
    state = official.STATE
    if state.is_symlink():
        return
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = state / (CACHE_NAME + '.pending')
    temp.write_text(json.dumps(data, ensure_ascii=False) + '\n')
    temp.replace(state / CACHE_NAME)


def stamp(seconds):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(seconds))


def summarise(installed, cache, now=None):
    """把「本机版本 + 上次渠道结果」变成界面要显示的状态。

    state：
    - unchecked：还没成功检查过；
    - current：渠道版本不高于本机；
    - available：渠道有新版本（adapted 表示本工具是否已适配它）；
    - unknown：读不到本机版本，给不出结论。
    """
    now = time.time() if now is None else now
    result = {'installed': installed or '', 'latest': '', 'state': 'unchecked',
              'adapted': False, 'checkedAt': '', 'stale': False,
              'channel': CHANNEL, 'message': ''}
    if cache:
        checked = cache.get('checkedAtEpoch')
        result.update(latest=cache['latest'],
                      checkedAt=cache.get('checkedAt', ''),
                      stale=(not isinstance(checked, (int, float))
                             or now - checked > CACHE_TTL))
    if not installed:
        result.update(state='unknown',
                      message='读不到本机 Claude 版本；先重新检测。')
        return result
    if not cache:
        result['message'] = '还没检查过官方新版。'
        return result
    if compare_versions(cache['latest'], installed) <= 0:
        result['state'] = 'current'
        return result
    result['state'] = 'available'
    result['adapted'] = bool(core.adapters_for_version(cache['latest']))
    return result


def cached_status(application=None):
    """只读缓存，不联网。合并检测每次都会调用它。

    本机版本取合并检测已经拿到的结果，不在这里读 /Applications：只读检测
    不应因为这一项再碰一次应用文件。
    """
    installed = None
    if isinstance(application, dict):
        value = application.get('version')
        if isinstance(value, str) and value and value != '未安装':
            installed = value
    return summarise(installed, read_cache())


def short_reason(error):
    """给界面看的短句；不回显原始异常，避免把地址写进界面或日志。"""
    if isinstance(error, NetworkError):
        return '没能连上官方更新渠道；检查网络后可以再试。'
    text = str(error)
    if 'HTTP' in text:
        return '官方更新渠道返回错误；稍后可以再试。'
    return '官方更新渠道的响应不认识；已忽略这次结果。'


def check(channel=CHANNEL, timeout=TIMEOUT):
    """联网检查一次官方渠道，并把成功结果写进缓存。

    失败时保留旧缓存：返回上次的结论并附带一句原因，绝不把“没查到”
    说成“已是最新”。
    """
    installed = installed_version()
    now = time.time()
    if not installed:
        return summarise(None, read_cache(), now)
    try:
        feed = query(installed, channel=channel, timeout=timeout)
    except ChannelError as error:
        result = summarise(installed, read_cache(), now)
        result['checked'] = False
        result['error'] = short_reason(error)
        return result
    fresh = {'latest': feed['currentRelease'], 'channel': channel,
             'checkedAt': stamp(now), 'checkedAtEpoch': now}
    write_cache(fresh)
    # 结论直接用这次的结果，不再回读缓存：状态目录不可写时这一轮仍然能提醒，
    # 只是下次启动要重新查。
    result = summarise(installed, fresh, now)
    result['checked'] = True
    return result
