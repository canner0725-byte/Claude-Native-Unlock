#!/usr/bin/env python3
"""生成维护代理的独立配置与 worktree；**不写入任何密钥**。

对应《维护方案 修订版 v2》第七节。这个脚本只做两件事：
1. 写出维护代理专用的 `CLAUDE_CONFIG_DIR` 和 settings JSON（权限、拒绝规则、沙箱）；
2. 在 `~/Developer/` 下准备一个独立的 git worktree 目录。

密钥不由本脚本处理：它只生成一个 0600 的占位文件和 `apiKeyHelper` 配置，
由你自己把密钥写进去（或者用环境变量）。这样密钥永远不经过 Git、日志或本脚本。

用法：
    python3 scripts/maintenance_setup.py --write      # 生成配置和 worktree
    python3 scripts/maintenance_setup.py --check      # 只检查，不写入
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import stat
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME = pathlib.Path.home()
CONFIG_DIR = HOME / '.claude-maintenance'
WORKTREE = HOME / 'Developer/Claude-Native-Unlock-maintenance'
KEY_FILE = CONFIG_DIR / 'api-key'
SETTINGS_FILE = CONFIG_DIR / 'settings.json'
# 第三方 API 网关域名，一行一个；不写进仓库，也不含密钥。
GATEWAY_FILE = CONFIG_DIR / 'gateway-hosts'

# 维护代理必须碰不到的东西。这些路径在任何情况下都不该出现在它的可读范围里。
DENY_READ = [
    str(HOME / 'Library/Application Support/Claude-3p'),
    str(HOME / 'Library/Application Support/Claude 原生助手/work/signing'),
    str(HOME / '.ssh'),
    '/Volumes/T7',
    '/Applications/Claude.app',
    str(HOME / '.claude'),          # 日常使用的配置、会话和记忆
    str(HOME / '.claude.json'),
]

# 会弹确认或造成不可逆影响的命令：直接拒绝，不等待、不重试。
DENY_COMMANDS = [
    'Bash(/usr/bin/security:*)',
    'Bash(security:*)',
    'Bash(/bin/launchctl:*)',
    'Bash(launchctl:*)',
    'Bash(/usr/bin/osascript:*)',
    'Bash(osascript:*)',
    'Bash(/usr/bin/pkill:*)',
    'Bash(pkill:*)',
    'Bash(/usr/bin/killall:*)',
    'Bash(killall:*)',
    'Bash(/usr/bin/sudo:*)',
    'Bash(sudo:*)',
    'Bash(/usr/bin/open:*)',
    'Bash(open:*)',
    'Bash(git push:*)',
    'Bash(git tag:*)',
    'Bash(gh release:*)',
    'Bash(rm:*)',
]

# 后端的安装/恢复命令：维护代理不得调用，那是在场段由你确认的事。
# 注意规则是**按命令开头**匹配的，`Bash(*backend.py enable*)` 这种带前导
# 通配的写法永远匹配不上；真正的调用都以解释器开头，所以按解释器前缀写。
def _deny_backend():
    """四个会改安装状态的子命令，两种调用形状都要拦住。

    `backend.py official_unlock enable` 和 `official_unlock.py enable`
    中间隔着模块名，所以通配符要跨过它；写成 `backend.py enable` 这种
    紧邻形式是匹配不上的——验证脚本第一版就漏在这里。
    """
    rules = []
    for verb in ('enable', 'update', 'disable', 'prepare-update'):
        # 解释器路径不固定：python3、.venv/bin/python、绝对路径都见过，
        # 所以通配符要跨过解释器，只认“哪个脚本 + 哪个子命令”。
        rules += [
            f'Bash(*backend.py *official_unlock {verb}*)',
            f'Bash(*official_unlock.py {verb}*)',
        ]
    return rules


DENY_BACKEND = _deny_backend()

# 文件工具的直接写入/读取也要挡住，命令规则拦不住 Read/Edit/Write。
DENY_TOOLS = [
    'Read(/Applications/Claude.app/**)',
    'Read(/Volumes/T7/**)',
    # 文件工具只认 Edit(...) 规则，写 Write(...) 会被判成无效规则，
    # 而**一条无效规则会让整份设置文件不加载**——保护会全部失效。
    'Edit(/Applications/**)',
    'Edit(/Volumes/T7/**)',
    'Read(**/Library/Application Support/Claude-3p/**)',
    'Read(**/.ssh/**)',
    # 签名私钥等于 Claude 的系统授权：维护代理绝不能读。
    'Read(**/Claude 原生助手/work/signing/**)',
    'Edit(**/Claude 原生助手/work/signing/**)',
    # 日常使用的配置、会话和记忆，与维护配置完全分开。
    'Read(**/.claude/**)',
    'Read(**/.claude.json)',
    # 动态层验证用的诱饵目录：里面只有一个随机标记文件。让维护代理去读它，
    # 读到了就说明拒绝规则没生效——用诱饵而不是真实密钥，最坏情况只泄漏标记。
    'Read(**/.claude-maintenance/canary/**)',
]

ALLOW = [
    'Read(//' + str(WORKTREE).lstrip('/') + '/**)',
    'Edit(//' + str(WORKTREE).lstrip('/') + '/**)',
    'Bash(python3 scripts/maint/*)',
    'Bash(.venv/bin/python -m unittest:*)',
    'Bash(git status:*)',
    'Bash(git diff:*)',
    'Bash(git log:*)',
    'Bash(git add:*)',
    'Bash(git commit:*)',
    'Bash(git switch:*)',
    'Bash(git checkout:*)',
    'Bash(git worktree:*)',
]


def gateway_domains():
    """从本机 0600 文件读第三方 API 网关域名；没有就只留 GitHub。

    网关地址不能写进仓库：那是用户自己的部署信息。这里只读域名，
    不读密钥，也不把结果打印出来。
    """
    domains = ['github.com', 'api.github.com', 'codeload.github.com']
    if not GATEWAY_FILE.exists():
        return domains
    for line in GATEWAY_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        host = line.split('//')[-1].split('/')[0].split(':')[0].strip()
        if host and host not in domains:
            domains.append(host)
    return domains


def settings():
    """维护代理的 settings JSON。

    `dontAsk` 是关键：凡是会弹确认的调用一律直接拒绝，不会卡住等人。
    沙箱是第二道防线——权限规则按命令前缀匹配，变形命令可能绕过。
    """
    return {
        '$schema': 'https://json.schemastore.org/claude-code-settings.json',
        'permissions': {
            'defaultMode': 'dontAsk',
            'disableBypassPermissionsMode': 'disable',
            'allow': ALLOW,
            'deny': DENY_TOOLS + DENY_COMMANDS + DENY_BACKEND,
            'additionalDirectories': [str(WORKTREE)],
            'blockReadsOutsideWorkingDirectories': True,
        },
        'sandbox': {
            'enabled': True,
            'autoAllowBashIfSandboxed': True,
            'allowUnsandboxedCommands': False,
            'failIfUnavailable': True,
            'excludedCommands': [],
            'filesystem': {
                'allowRead': [str(WORKTREE)],
                'allowWrite': [str(WORKTREE)],
                'denyRead': DENY_READ,
                'allowManagedReadPathsOnly': True,
            },
            'network': {
                'strictAllowlist': True,
                'allowManagedDomainsOnly': True,
                'allowedDomains': gateway_domains(),
                'deniedDomains': [],
            },
        },
        'apiKeyHelper': str(KEY_FILE),
    }


def check_config():
    """只读检查：配置、权限、worktree 是否就绪。返回问题列表。"""
    problems = []
    if not CONFIG_DIR.is_dir():
        problems.append(f'缺少配置目录 {CONFIG_DIR}')
        return problems
    mode = CONFIG_DIR.stat().st_mode & 0o777
    if mode != 0o700:
        problems.append(f'{CONFIG_DIR} 权限是 {oct(mode)}，应为 0o700')
    if not SETTINGS_FILE.exists():
        problems.append(f'缺少 {SETTINGS_FILE}')
    else:
        try:
            data = json.loads(SETTINGS_FILE.read_text())
        except Exception as error:
            problems.append(f'{SETTINGS_FILE} 读不出来：{error}')
            return problems
        if data.get('permissions', {}).get('defaultMode') != 'dontAsk':
            problems.append('permissions.defaultMode 不是 dontAsk')
        if not data.get('sandbox', {}).get('enabled'):
            problems.append('沙箱未开启')
        if data.get('sandbox', {}).get('allowUnsandboxedCommands'):
            problems.append('allowUnsandboxedCommands 必须为 false')
    if KEY_FILE.exists():
        # apiKeyHelper 要执行它，所以必须是 0700 而不是 0600：
        # 只有你能读和执行，别人既读不到也跑不了。
        key_mode = KEY_FILE.stat().st_mode & 0o777
        if key_mode != 0o700:
            problems.append(f'{KEY_FILE} 权限是 {oct(key_mode)}，应为 0o700')
    if not WORKTREE.exists():
        problems.append(f'缺少 worktree {WORKTREE}')
    return problems


def write_config():
    """写出配置；已存在的文件不覆盖（避免把用户的密钥或改动冲掉）。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(CONFIG_DIR, 0o700)
    if SETTINGS_FILE.exists():
        print(f'已存在，未覆盖：{SETTINGS_FILE}')
    else:
        SETTINGS_FILE.write_text(json.dumps(settings(), ensure_ascii=False, indent=2) + '\n')
        os.chmod(SETTINGS_FILE, 0o600)
        print(f'已写入：{SETTINGS_FILE}')
    if not KEY_FILE.exists():
        # 占位文件：apiKeyHelper 要求它可执行，并且自己输出密钥。
        # 这里只写一个明确的报错，绝不写入任何真实密钥。
        KEY_FILE.write_text(
            '#!/bin/sh\n'
            '# 把本文件内容替换成读取你自己密钥的命令，例如：\n'
            '#   cat ~/.claude-maintenance/secret\n'
            '# 本脚本不会写入密钥；密钥不要进 Git、日志或输出。\n'
            'echo "维护代理密钥未配置" >&2\n'
            'exit 1\n')
        os.chmod(KEY_FILE, 0o700)
        print(f'已写入占位密钥脚本（不含密钥）：{KEY_FILE}')
    if not GATEWAY_FILE.exists():
        # 沙箱开了 strictAllowlist，网关不在名单里就连不上模型。
        # 这里只建空文件并说明格式，域名由你自己填，不经过仓库。
        GATEWAY_FILE.write_text(
            '# 一行一个域名，例如 api.example.com\n'
            '# 用来放行第三方 API 网关；本文件不含密钥。\n')
        os.chmod(GATEWAY_FILE, 0o600)
        print(f'已写入网关域名占位文件：{GATEWAY_FILE}')
    prepare_worktree()


def prepare_worktree():
    """在 ~/Developer 下建一个独立 worktree，不放在桌面/文稿/下载。"""
    if WORKTREE.exists():
        print(f'已存在，未改动：{WORKTREE}')
        return
    WORKTREE.parent.mkdir(parents=True, exist_ok=True)
    branch = 'maintenance/agent'
    existing = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--verify', branch],
                              capture_output=True)
    args = ['git', '-C', str(ROOT), 'worktree', 'add']
    if existing.returncode == 0:
        args += [str(WORKTREE), branch]
    else:
        args += ['-b', branch, str(WORKTREE)]
    done = subprocess.run(args, capture_output=True)
    if done.returncode != 0:
        print('worktree 创建失败：' + done.stderr.decode(errors='replace').strip(),
              file=sys.stderr)
        return
    print(f'已创建 worktree：{WORKTREE}（分支 {branch}）')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='生成配置和 worktree')
    parser.add_argument('--check', action='store_true', help='只检查')
    args = parser.parse_args()
    if args.write:
        write_config()
    problems = check_config()
    if problems:
        print('\n未就绪：')
        for problem in problems:
            print('  · ' + problem)
        raise SystemExit(1)
    print('\n维护代理配置已就绪：' + str(CONFIG_DIR))


if __name__ == '__main__':
    main()
