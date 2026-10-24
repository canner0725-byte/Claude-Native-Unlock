#!/usr/bin/env python3
"""一次性设置的只读体检：逐项报告“已就绪 / 待处理 / 无法自动判断”。

对应《维护方案 修订版 v2》第六节的一次性设置清单。这个脚本**不改任何东西**：
不建身份、不装后台作业、不碰钥匙串、不碰系统设置。它只读，给出每项的真实现状
和下一步该由谁做——能在终端判断的自动判断，需要你在场点一次的明确写出来。

用法：
    python3 scripts/setup_check.py            # 人读的清单
    python3 scripts/setup_check.py --json     # 机器读的结果
    python3 scripts/setup_check.py --strict   # 有未就绪项时退出码 1
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import plistlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

READY = 'ready'
PENDING = 'pending'
UNKNOWN = 'unknown'

TARGET_APP = pathlib.Path('/Applications/Claude.app')
ASSISTANT_APP = pathlib.Path('/Applications/Claude 原生助手.app')
BACKEND = ASSISTANT_APP / 'Contents/Resources/Backend/NativeUnlockBackend'
THIRD_PARTY = pathlib.Path.home() / 'Library/Application Support/Claude-3p'
PROTECTED_VOLUME = pathlib.Path('/Volumes/T7')
DEVELOPER = pathlib.Path.home() / 'Developer'


def run(*args, timeout=20):
    """跑一条只读命令；失败返回 None，不抛出。"""
    try:
        done = subprocess.run([str(a) for a in args], capture_output=True, timeout=timeout)
    except Exception:
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode(errors='replace')


def item(key, title, state, detail, action=''):
    return {'key': key, 'title': title, 'state': state, 'detail': detail, 'action': action}


def designated_requirement(path):
    """读二进制的指定要求；ad-hoc 是 cdhash，固定身份是证书根。"""
    try:
        done = subprocess.run(['/usr/bin/codesign', '-d', '-r-', str(path)],
                              capture_output=True, timeout=20)
    except Exception:
        return None
    text = (done.stdout + done.stderr).decode(errors='replace')
    for line in text.splitlines():
        if 'designated =>' in line:
            return line.split('designated =>', 1)[1].strip()
    return None


def screen_locked():
    """屏幕锁定时钥匙串读不到也不会弹框，这决定了哪些步骤必须在你面前做。

    必须读 `IOConsoleLocked` **这一项的值**，不能只判断输出里有没有 `<true/>`：
    同一份 ioreg 输出里还有别的布尔项，只搜 `<true/>` 会让屏幕解锁时也报“已锁定”。
    这个 bug 真的发生过——解锁状态下体检仍报锁定，把“该你做的步骤”全部挡住。
    """
    import re
    try:
        # ioreg 在 /usr/sbin，不在 /usr/bin。
        done = subprocess.run(['/usr/sbin/ioreg', '-n', 'Root', '-d1', '-a'],
                              capture_output=True, timeout=10)
    except Exception:
        return None
    if done.returncode != 0:
        return None
    match = re.search(rb'<key>IOConsoleLocked</key>\s*<(true|false)\s*/>', done.stdout)
    if not match:
        return None
    return match.group(1) == b'true'


def check_project_location():
    home = pathlib.Path.home()
    protected = [home / 'Desktop', home / 'Documents', home / 'Downloads']
    inside = next((p for p in protected if ROOT == p or p in ROOT.parents), None)
    if inside:
        return item('project_location', '项目不在受隐私保护的目录',
                    PENDING,
                    f'当前在 {ROOT}，位于受保护的 {inside.name}（macOS 会要求逐个程序授权这个目录）',
                    f'移到 {DEVELOPER / "Claude-Native-Unlock"}，并同步改 AGENTS.md 第 5 条')
    return item('project_location', '项目不在受隐私保护的目录', READY, str(ROOT))


def check_signing_identity(core):
    identity = core.local_identity()
    if not identity:
        return item('signing_identity', '固定本地签名身份已建立', PENDING,
                    '还没有身份；打补丁时会退回 ad-hoc，每次更新都要重新授权',
                    '在助手里开启一次补丁，助手会自动建立')
    return item('signing_identity', '固定本地签名身份已建立', READY, identity)


def check_password_location(core):
    """报告签名密码存在哪，并说明为什么这是已知的、暂时无解的风险。

    这里**故意不报“待处理”**：2026-10-24 实测确认，登录钥匙串的 ACL 对
    同用户进程不提供隔离，数据保护钥匙串需要的 entitlement 自签证书拿不到。
    两条路都堵死，把它列成待处理只会让人反复尝试一个做不成的事。
    风险本身写进 README，由用户决定要不要接受。
    """
    where = core.signing_password_location()
    if where == 'plaintext':
        return item('signing_password', '签名密码的保护（已知无解，非待办）', UNKNOWN,
                    '密码以 0600 明文存放；登录钥匙串与数据保护钥匙串两条路都已实测否决',
                    '无需操作。风险见 README「签名身份等于 Claude 的系统授权」')
    if where == 'keychain':
        # 理论上不该出现；出现了也要如实报告，不要假装是保护生效了。
        return item('signing_password', '签名密码的保护（已知无解，非待办）', UNKNOWN,
                    '密码在登录钥匙串里，但已实测该 ACL 不隔离同用户进程',
                    '无需操作。风险见 README「签名身份等于 Claude 的系统授权」')
    return item('signing_password', '签名密码的保护（已知无解，非待办）', UNKNOWN,
                '既没有钥匙串条目也没有明文文件；下一次签名会退回 ad-hoc')


def check_assistant_signing():
    """助手和后端是否用固定身份签名。

    ad-hoc 的指定要求是 cdhash，助手每次重新构建都会变，macOS 会当成另一个
    应用，用户的辅助功能与屏幕录制授权要重新确认一次。固定身份的要求只跟
    证书有关，重建多少次都不变。
    """
    if not ASSISTANT_APP.exists():
        return item('assistant_signing', '助手用固定身份签名', UNKNOWN,
                    '没有找到已安装的助手')
    app_req = designated_requirement(ASSISTANT_APP)
    backend_req = designated_requirement(BACKEND) if BACKEND.exists() else None
    problems = []
    if not app_req or 'cdhash' in app_req:
        problems.append('助手本体是 ad-hoc 签名（指定要求是 cdhash）')
    if backend_req and 'cdhash' in backend_req:
        problems.append('后端可执行文件是 ad-hoc 签名')
    if problems:
        return item('assistant_signing', '助手用固定身份签名', PENDING,
                    '；'.join(problems) + '；更新助手后系统授权要重新确认',
                    '用 scripts/build.py 重新构建并安装到 /Applications')
    return item('assistant_signing', '助手用固定身份签名', READY, '助手与后端都是固定身份')


def check_background_job():
    """固定后台作业：先看有没有登记，不替用户创建。

    已登记还要看定义是否合格：`~/Library/LaunchAgents/` 里的 plist 是
    launchd 直接读的，手改坏了不会有任何提示，只会静默不按计划跑。
    """
    sys.path.insert(0, str(ROOT / 'scripts'))
    import maintenance_job as job
    plist = job.INSTALLED
    if not plist.exists():
        staged = ('定义已生成，等你批准登记' if job.STAGED.exists()
                  else '定义还没生成（运行 scripts/maintenance_job.py --write）')
        return item('background_job', '固定后台作业已登记', PENDING,
                    f'还没有登记；{staged}',
                    '先通过零弹窗验证，再把定义拷进 ~/Library/LaunchAgents 并在登录项批准')
    try:
        data = plistlib.loads(plist.read_bytes())
    except Exception:
        return item('background_job', '固定后台作业已登记', PENDING,
                    f'{plist.name} 存在但读不出来', '用 scripts/maintenance_job.py 重新生成')
    problems = job.problems_in(data)
    if problems:
        return item('background_job', '固定后台作业已登记', PENDING,
                    '已登记但定义不合格：' + '；'.join(problems),
                    '用 scripts/maintenance_job.py 重新生成后覆盖')
    return item('background_job', '固定后台作业已登记', READY,
                f'{plist.name}：{data.get("Label", "")}')


def check_maintenance_config():
    """维护代理的独立配置目录、密钥文件和沙箱设置。"""
    config = pathlib.Path.home() / '.claude-maintenance'
    if not config.is_dir():
        return item('maintenance_config', '维护代理有独立配置目录', PENDING,
                    f'{config} 不存在',
                    '运行 scripts/maintenance_setup.py --write 生成（不写入任何密钥）')
    mode = oct(config.stat().st_mode & 0o777)
    if mode != '0o700':
        return item('maintenance_config', '维护代理有独立配置目录', PENDING,
                    f'{config} 权限是 {mode}，应为 0o700',
                    f'chmod 700 {config}')
    return item('maintenance_config', '维护代理有独立配置目录', READY, str(config))


def check_independent_cli():
    """维护代理必须用独立安装的官方 CLI，不能用被本项目打过补丁的那份。"""
    patched = THIRD_PARTY / 'claude-code'
    candidates = [pathlib.Path.home() / '.local/bin/claude',
                  pathlib.Path('/opt/homebrew/bin/claude'),
                  pathlib.Path('/usr/local/bin/claude')]
    found = next((p for p in candidates if p.exists()), None)
    if not found:
        return item('independent_cli', '有独立安装的官方 CLI', UNKNOWN,
                    '没在常见位置找到 claude 命令')
    if patched.exists() and patched in found.resolve().parents:
        return item('independent_cli', '有独立安装的官方 CLI', PENDING,
                    f'{found} 指向被本项目打过补丁的那份，会和 check_idle() 互相卡住')
    return item('independent_cli', '有独立安装的官方 CLI', READY, str(found))


def check_third_party_denied():
    """维护代理必须读不到 3P 配置。

    只报告“拒绝规则是否真的覆盖了这个路径”，不靠“配置文件存在”当结论：
    配置存在不等于规则生效，上一版的教训就是一条无效规则让整份文件不加载。
    """
    if not THIRD_PARTY.exists():
        return item('third_party_denied', '维护代理读不到 3P 配置', READY,
                    '本机没有 3P 配置目录；拒绝规则仍然在配置里')
    sys.path.insert(0, str(ROOT / 'scripts'))
    import maintenance_setup as setup
    import maintenance_probe as probe
    try:
        data = json.loads(setup.SETTINGS_FILE.read_text())
    except Exception as error:
        return item('third_party_denied', '维护代理读不到 3P 配置', PENDING,
                    f'维护代理配置读不出来：{error}',
                    '运行 scripts/maintenance_setup.py --write 重新生成')
    deny = data.get('permissions', {}).get('deny', [])
    rule = probe.rule_matches(deny, 'Read', str(THIRD_PARTY / 'Local State'))
    if not rule:
        return item('third_party_denied', '维护代理读不到 3P 配置', PENDING,
                    '拒绝规则没有覆盖 3P 配置目录',
                    '运行 scripts/maintenance_setup.py --write 重新生成')
    return item('third_party_denied', '维护代理读不到 3P 配置', READY, f'由 {rule} 挡住')


def check_eagle():
    """Eagle 素材库永远不进入维护范围。

    盘挂着的时候，唯一挡得住的是拒绝规则本身——所以这里查规则，不查挂载。
    """
    sys.path.insert(0, str(ROOT / 'scripts'))
    import maintenance_setup as setup
    import maintenance_probe as probe
    target = '/Volumes/T7/十六珠宝进姐短视频运营/十六珠宝素材库.library'
    try:
        data = json.loads(setup.SETTINGS_FILE.read_text())
    except Exception as error:
        return item('eagle_protected', 'Eagle 素材库不在维护范围内', PENDING,
                    f'维护代理配置读不出来，无法确认挡得住：{error}',
                    '运行 scripts/maintenance_setup.py --write 重新生成')
    deny = data.get('permissions', {}).get('deny', [])
    rule = probe.rule_matches(deny, 'Read', target)
    if not rule:
        return item('eagle_protected', 'Eagle 素材库不在维护范围内', PENDING,
                    '拒绝规则没有覆盖 Eagle 素材库',
                    '运行 scripts/maintenance_setup.py --write 重新生成')
    mounted = '已挂载' if PROTECTED_VOLUME.exists() else '未挂载'
    return item('eagle_protected', 'Eagle 素材库不在维护范围内', READY,
                f'{PROTECTED_VOLUME} {mounted}；由 {rule} 挡住')


def check_screen():
    locked = screen_locked()
    if locked is None:
        return item('screen_state', '屏幕状态（决定哪些步骤要你在场）', UNKNOWN,
                    '读不出屏幕锁定状态')
    if locked:
        return item('screen_state', '屏幕状态（决定哪些步骤要你在场）', PENDING,
                    '屏幕已锁定：系统设置里勾选授权、登录项确认这些步骤都做不了',
                    '解锁屏幕后再给 Claude 授权、批准后台作业')
    return item('screen_state', '屏幕状态（决定哪些步骤要你在场）', READY,
                '屏幕已解锁，可以做需要你确认的步骤')


def configure_like_the_app():
    """按真实入口读状态：CLAUDE.md 要求走 backend.configure()。

    直接 import 模块拿到的是仓库内 work/，不是已安装状态——那样会把
    “本机已装好固定身份”误报成“还没有身份”。
    """
    import backend
    backend.configure()
    import native_unlock as core
    return core


def collect():
    core = configure_like_the_app()
    checks = [
        check_project_location(),
        check_signing_identity(core),
        check_password_location(core),
        check_assistant_signing(),
        check_screen(),
        check_background_job(),
        check_maintenance_config(),
        check_independent_cli(),
        check_third_party_denied(),
        check_eagle(),
    ]
    counts = {state: sum(1 for c in checks if c['state'] == state)
              for state in (READY, PENDING, UNKNOWN)}
    return {'checks': checks, 'counts': counts,
            'ready': counts[PENDING] == 0}


def render(result):
    marks = {READY: '✓', PENDING: '·', UNKNOWN: '?'}
    labels = {READY: '已就绪', PENDING: '待处理', UNKNOWN: '无法判断'}
    lines = ['一次性设置体检（只读，不修改任何东西）', '']
    for check in result['checks']:
        lines.append(f"{marks[check['state']]} {check['title']} — {labels[check['state']]}")
        lines.append(f"    {check['detail']}")
        if check['action']:
            lines.append(f"    下一步：{check['action']}")
    counts = result['counts']
    lines += ['', f"已就绪 {counts[READY]} · 待处理 {counts[PENDING]} · 无法判断 {counts[UNKNOWN]}"]
    if not result['ready']:
        lines.append('无人值守维护尚未就绪：待处理项完成后重新运行本脚本。')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='输出机器可读结果')
    parser.add_argument('--strict', action='store_true', help='有未就绪项时退出码 1')
    args = parser.parse_args()
    result = collect()
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    if args.strict and not result['ready']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
