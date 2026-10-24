#!/usr/bin/env python3
"""生成固定后台作业的定义；**只写到 dist/，不登记、不加载**。

对应《维护方案 修订版 v2》第六节第 5 条与第八节「调度」。

为什么要区分“写定义”和“登记”：把 plist 直接放进 `~/Library/LaunchAgents/`
就已经算登记了——下次登录 launchd 会加载它，系统会弹“已添加后台项目”。
所以 `--write` 只把定义写到仓库的 `dist/maintenance/`，登记由你在系统设置里
批准一次，脚本不碰 `~/Library/LaunchAgents/`，也不执行 `launchctl`。

调度要求（方案第八节）：每 6 小时一次、`StartCalendarInterval`、休眠期间错过
的检查在唤醒后补跑一次、不唤醒电脑、单实例。这几条里：

- 补跑：`StartCalendarInterval` 本身就是这个语义，不需要额外配置。
- 不唤醒电脑：不设 `WakeSystem`，它默认就是 false。
- 单实例：`StartCalendarInterval` 作业在上一轮还没结束时，launchd 不会再起
  一个，这是默认行为。
- `RunAtLoad` 关掉：登录时不要立刻跑一次。

用法：
    python3 scripts/maintenance_job.py --write   # 写 dist/maintenance/*.plist
    python3 scripts/maintenance_job.py --check   # 只读校验
    python3 scripts/maintenance_job.py --json    # 打印定义
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import plistlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME = pathlib.Path.home()
LABEL = 'local.claude.native-unlock.maintenance'
STAGED = ROOT / 'dist/maintenance' / (LABEL + '.plist')
INSTALLED = HOME / 'Library/LaunchAgents' / (LABEL + '.plist')
RUNNER = ROOT / 'scripts/maintenance_run.sh'
LOG_DIR = HOME / 'Library/Logs'
LOG = LOG_DIR / 'claude-native-unlock-maintenance.log'
STATE_DIR = HOME / 'Library/Application Support/Claude 原生助手/maintenance'
MAINTENANCE_CONFIG = HOME / '.claude-maintenance'

# 每 6 小时：0 点、6 点、12 点、18 点。
SCHEDULE = [{'Hour': hour, 'Minute': 0} for hour in (0, 6, 12, 18)]


def job_plist():
    """后台作业定义。

    `ProgramArguments` 指向外层脚本，不是维护代理本身：方案要求整个流程由
    外层脚本驱动，维护代理只是其中一步（第七节「职责分开」）。外层脚本现在
    是 `scripts/maintenance_run.sh`，它在零弹窗验证通过前会直接跳过。
    """
    return {
        'Label': LABEL,
        'ProgramArguments': ['/bin/sh', str(RUNNER)],
        'WorkingDirectory': str(ROOT),
        'StartCalendarInterval': SCHEDULE,
        # 不随登录立刻跑；只在计划时间或系统唤醒补跑时触发。
        'RunAtLoad': False,
        'StandardOutPath': str(LOG),
        'StandardErrorPath': str(LOG),
        'ProcessType': 'Background',
        'LowPriorityIO': True,
        'Nice': 10,
        'EnvironmentVariables': {
            # 维护代理用独立配置，不碰日常的 ~/.claude。
            'CLAUDE_CONFIG_DIR': str(MAINTENANCE_CONFIG),
            'CNU_MAINTENANCE_STATE': str(STATE_DIR),
        },
    }


def problems_in(data):
    """校验一份作业定义；返回问题列表，空列表表示合格。"""
    problems = []
    if data.get('Label') != LABEL:
        problems.append(f'Label 是 {data.get("Label")!r}，应为 {LABEL!r}')
    if data.get('StartCalendarInterval') != SCHEDULE:
        problems.append('StartCalendarInterval 不是「每 6 小时」')
    if data.get('RunAtLoad'):
        problems.append('RunAtLoad 应为 false：登录时不要立刻跑一次')
    if data.get('KeepAlive'):
        problems.append('KeepAlive 应为空：崩溃时不要无限重启')
    if data.get('WakeSystem'):
        problems.append('WakeSystem 应为空：不许唤醒电脑')
    env = data.get('EnvironmentVariables') or {}
    if env.get('CLAUDE_CONFIG_DIR') != str(MAINTENANCE_CONFIG):
        problems.append('CLAUDE_CONFIG_DIR 没有指向维护代理的独立配置')
    args = data.get('ProgramArguments') or []
    if not any(str(a).endswith('maintenance_run.sh') for a in args):
        problems.append('ProgramArguments 没有指向外层脚本 maintenance_run.sh')
    return problems


def check():
    """只读校验；返回 (问题列表, 是否已登记)。"""
    problems = []
    if not STAGED.exists():
        problems.append(f'尚未生成：{STAGED}（运行 --write）')
    else:
        try:
            data = plistlib.loads(STAGED.read_bytes())
        except Exception as error:
            problems.append(f'{STAGED} 读不出来：{error}')
        else:
            problems.extend(problems_in(data))
    if not RUNNER.exists():
        problems.append(f'作业指向的外层脚本还不存在：{RUNNER}')
    elif not os.access(RUNNER, os.X_OK):
        problems.append(f'外层脚本没有执行权限：{RUNNER}')
    return problems, INSTALLED.exists()


def write():
    """写出定义到 dist/；不碰 ~/Library/LaunchAgents/，不执行 launchctl。"""
    STAGED.parent.mkdir(parents=True, exist_ok=True)
    STAGED.write_bytes(plistlib.dumps(job_plist()))
    print(f'已写出：{STAGED}')
    print('尚未登记。登记是你在场的一次动作：')
    print(f'  cp "{STAGED}" "{INSTALLED}"')
    print('然后在 系统设置 → 通用 → 登录项 里批准。本脚本不执行这一步。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='写出定义到 dist/（不登记）')
    parser.add_argument('--check', action='store_true', help='只读校验')
    parser.add_argument('--json', action='store_true', help='打印作业定义')
    args = parser.parse_args()
    if args.json:
        print(json.dumps(job_plist(), ensure_ascii=False, indent=2))
        return
    if args.write:
        write()
    problems, installed = check()
    if problems:
        print('\n定义未就绪：')
        for problem in problems:
            print('  · ' + problem)
        raise SystemExit(1)
    print('\n定义合格。')
    print('已登记：是（' + str(INSTALLED) + '）' if installed
          else '已登记：否——零弹窗验证通过前不要登记。')


if __name__ == '__main__':
    main()
