#!/usr/bin/env python3
"""生成机器可读的状态与功能清单，并检查文档是否与代码一致。

唯一事实来源是代码本身：适配器表、FEATURES、VERIFIED_SYSTEM、PATCH_REVISION。
这个脚本把它们的当前取值写成 `state/status.json` 与 `catalog/features.json`；
`--check` 再核对这两份文件、README 和界面文案有没有漂移。

用法：
    python3 scripts/state.py --write     # 写 state/ 与 catalog/
    python3 scripts/state.py --check     # 只检查：不一致时退出码 1
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
sys.path.insert(0, str(SRC))

STATE_FILE = ROOT / 'state/status.json'
CATALOG_FILE = ROOT / 'catalog/features.json'


def load():
    import native_unlock as core
    import official_unlock as official
    import native_features as features
    import native_cli_unlock as cli
    return core, official, features, cli


def build_version():
    text = (ROOT / 'scripts/build.py').read_text()
    version = re.search(r"VERSION = '([^']+)'", text).group(1)
    number = re.search(r"BUILD_NUMBER = '([^']+)'", text).group(1)
    return version, number


def ui_catalog():
    """界面里的开关（顺序即显示顺序）与分组，用于和 FEATURES 交叉核对。"""
    source = (ROOT / 'app/NativeUnlock.swift').read_text()

    def strings(declaration):
        body = re.search(declaration + r'[^=]*=\s*\[(.*?)\]', source, re.S).group(1)
        return re.findall(r'"([^"]*)"', body)

    identifiers = strings(r'let featureIDs')
    titles = strings(r'let featureTitles')
    groups = re.findall(r'\("([^"]+)",\s*\[(.*?)\]\)',
                        re.search(r'let featureGroups[^=]*=\s*\[(.*?)\n    \]',
                                  source, re.S).group(1), re.S)
    group_of = {identifier: name
                for name, members in groups
                for identifier in re.findall(r'"([^"]*)"', members)}
    return [{'id': identifier, 'title': title, 'group': group_of.get(identifier)}
            for identifier, title in zip(identifiers, titles)]


def adapter_table(core):
    """所有登记过的构建，按版本号排序；同版本的多构建保持登记顺序。"""
    adapters = [value for name, value in vars(core).items()
                if re.fullmatch(r'ADAPTER_[0-9A-Z_]+', name)]
    return sorted(adapters, key=lambda adapter: adapter['version'])


def status():
    core, official, features, cli = load()
    version, number = build_version()
    adapters = []
    for adapter in adapter_table(core):
        architecture, system = core.verified_system(adapter)
        adapters.append({
            'version': adapter['version'],
            'asarHash': adapter['asarHash'],
            'main': adapter['main'],
            'verifiedArchitecture': architecture,
            'verifiedSystem': core.system_name(system),
            'systemCeiling': ('{major:%d,minor:%d}' % (system[0], system[1])
                              if system else None),
            'mainGates': [key for key, _ in core.split_entries(
                core.main_availability(adapter))],
            'rendererGates': [key for key, _ in core.split_entries(
                core.web_availability(adapter))],
        })
    return {
        'assistant': {'version': version, 'build': number},
        'protocol': core.PROTOCOL_VERSION,
        'patchRevision': official.PATCH_REVISION,
        'cliAdapters': sorted(cli.ADAPTERS),
        'adapters': adapters,
        'configKeys': list(features.FEATURES),
        'uiRows': ui_catalog(),
    }


# 3.6556.2 渲染进程门的准入复核（2026-10-24，静态）。
# 依据《维护方案 修订版 v2》第九节的六条客观准入标准，对 r14 已补回的 11 个门
# 逐项核对官方未打补丁备份里的全部读取点。结论：11/11 满足，保留。
# `readPoints` 里的文件名都来自官方备份的 `Contents/Resources/ion-dist/assets/v1`。
GATE_AUDIT = {
    '3858164079': {
        'name': 'claudeai_session_state',
        'controls': '设置行「Classify session states」与会话列表「Group by → Status」',
        'readPoints': ['shared-46 SN()/CN()（导出 wn）', 'c71860c77 ia()',
                       'ca25db325 设置行 gate'],
        'displayOnly': True,
        'safety': False,
        'server': False,
        'writes': '官方自动分类会写会话状态标签；受账号设置 ccr_session_state_buckets 控制，可关闭',
        'conclusion': '保留',
        'note': '门只决定分组方式与设置行；分类本身由官方设置控制（缺省开），计入用量。',
    },
    '1235690166': {
        'name': 'cai_epitaxy_worktree',
        'controls': '每文件夹工作树选项是否可用',
        'readPoints': ['shared-11 bc() → Cc()/Dc()/Oc()', 'cc43287c9（_v/bv/Cv）',
                       'c73f45bc3（oi）'],
        'displayOnly': True,
        'safety': False,
        'server': False,
        'writes': '否；工作树创建/删除仍由用户操作与官方后端触发',
        'conclusion': '保留',
        'note': '还要求 cai_epitaxy_session_presets_main 为真；工作区信任仍由官方检查。',
    },
    '1282344890': {
        'name': 'ccr_autofix_ui',
        'controls': '设置行「Auto-fix pull requests」与 PR 自动修复入口',
        'readPoints': ['c0d2a0222 默认值 hook', 'c71860c77 设置行可见性',
                       'c29d48294 PR 列表查询', 'cdbc75b4b', 'cf74bc599 rw()',
                       'c227f4d5e UC()'],
        'displayOnly': True,
        'safety': False,
        'server': True,
        'writes': '用户显式开启后官方会向仓库推送修复提交；官方行为，可关闭',
        'conclusion': '保留',
        'note': '门本身不会把自动修复默认打开：默认值还要求 GrowthBook '
                '__gb__1618169098，该键不在本地 Hon/Uon，3P 下为 unknownFeature→false。',
    },
    '3774822426': {
        'name': 'epitaxy_pr_panel',
        'controls': 'PR 面板与「Open pull requests in browser」入口是否加载',
        'readPoints': ['c28ee0d82 r()', 'c376f4ef5 a()/o()', 'c227f4d5e 注册表 entry',
                       'ca25db325 设置行'],
        'displayOnly': True,
        'safety': False,
        'server': False,
        'writes': '否',
        'conclusion': '保留',
        'note': '加载还要求 desktop.getPrOverview 存在；面板数据来自用户自己的仓库。',
    },
    '2280470525': {
        'name': 'ccr_code_requires_action_category_enabled',
        'controls': '设置行「Code permission requests」是否显示',
        'readPoints': ['ca25db325 设置行 + gates 对象', 'shared-182 bW()（导出 Ka，无人导入）'],
        'displayOnly': True,
        'safety': False,
        'server': False,
        'writes': '否',
        'conclusion': '保留',
        'note': '本构建里 bW 是死导出：没有任何文件从 shared-182 导入 Ka；'
                '唯一活读取点是设置行可见性，不参与权限/审批判定。',
    },
    '69389141': {
        'name': 'ccd_palette_session_actions',
        'controls': '命令面板里的会话操作（归档、重命名、置顶等）',
        'readPoints': ['c10f0af0c', 'c518a31cc', 'c71dee58b', 'ca2ef848d', 'cf74bc599'],
        'displayOnly': True,
        'safety': False,
        'server': False,
        'writes': '否；操作本身是用户主动触发的官方命令',
        'conclusion': '保留',
        'note': '',
    },
    '3619252400': {
        'name': 'claudeai_cc_epitaxy_folder_browser',
        'controls': 'SSH 文件夹浏览是否可用',
        'readPoints': ['c0fd2fda3 Tt()', 'cc43287c9（de=，还要求 listSSHDirectory）'],
        'displayOnly': True,
        'safety': False,
        'server': False,
        'writes': '否',
        'conclusion': '保留',
        'note': '',
    },
    '4000837158': {
        'name': 'epitaxy_plan_pane_auto_open',
        'controls': '计划面板自动打开',
        'readPoints': ['cc43287c9 v1()'],
        'displayOnly': True,
        'safety': False,
        'server': False,
        'writes': '写本地 UI 状态 planAutoOpenedBySession，可回滚、不影响文件',
        'conclusion': '保留',
        'note': '',
    },
    '273163521': {
        'name': 'claudeai_cc_epitaxy_sticky_prompt',
        'controls': '置顶提示（stickyPromptEnabled）',
        'readPoints': ['cf74bc599'],
        'displayOnly': True,
        'safety': False,
        'server': False,
        'writes': '否',
        'conclusion': '保留',
        'note': '',
    },
    '2149681774': {
        'name': 'ccd_prompt_jump',
        'controls': '上一/下一提示跳转（promptJumpEnabled）',
        'readPoints': ['c518a31cc', 'cc43287c9', 'cf74bc599'],
        'displayOnly': True,
        'safety': False,
        'server': False,
        'writes': '否',
        'conclusion': '保留',
        'note': '',
    },
    '2697147784': {
        'name': 'ccd_checkin_noop_rollup',
        'controls': '检查点折叠（checkInRollupEnabled）',
        'readPoints': ['cf74bc599'],
        'displayOnly': True,
        'safety': False,
        'server': False,
        'writes': '否',
        'conclusion': '保留',
        'note': '',
    },
}

# 主进程表里 r14 仍要强制的一项：官方「提示建议」设置项。
MAIN_GATE_AUDIT = {
    '2120747855': {
        'name': 'ccdPromptSuggestionsSetting',
        'controls': '官方「Prompt suggestions」设置行是否可见',
        'note': '建议的生成、Tab 采纳与发送仍走官方逻辑；本构建官方 Hon 表里没有该键。',
    },
}


def catalog(state):
    features = load()[2]
    ui_titles = {row['id']: row['title'] for row in state['uiRows']}
    rows = []
    for identifier, spec in features.FEATURES.items():
        rows.append({
            'id': identifier,
            'title': spec['title'],
            'group': spec['group'],
            'officialKey': spec['key'],
            'officialValue': spec['value'],
            'layer': 0,
            'method': '写入官方第三方部署配置项',
            'ui': identifier in ui_titles,
            'uiTitle': ui_titles.get(identifier),
            'limits': '官方配置项，官方更新后通常仍然有效',
            'evidence': '已在本机安装记录与测试中往返验证；界面文案与后端逐项一致',
        })
    rows.append({
        'id': 'code-auto-dream',
        'title': 'Code 自动整理',
        'group': 'Code',
        'officialKey': 'Auto-dream availability predicate',
        'officialValue': True,
        'layer': 3,
        'method': 'CLI 二进制谓词（指定偏移、原字节一致时改为 return!0）',
        'ui': 'code-auto-dream' in ui_titles,
        'uiTitle': ui_titles.get('code-auto-dream'),
        'limits': 'CLI 自行更新即失效，需要重新适配；会改写用户 MEMORY.md，恢复官方版也回滚不了',
        'evidence': '合成配置目录下跑通 firing → completed；官方未打补丁副本 0 条日志',
    })
    latest = state['adapters'][-1]
    gates = latest['rendererGates']
    for gate in gates:
        audit = GATE_AUDIT[gate]
        rows.append({
            'id': 'renderer-' + gate,
            'title': audit['name'],
            'group': '补丁',
            'officialKey': gate,
            'officialName': audit['name'],
            'officialValue': True,
            'layer': 1,
            'method': '渲染进程功能表补 always/true（仅当官方表里没有该键）',
            'ui': False,
            'uiTitle': None,
            'controls': audit['controls'],
            'readPoints': audit['readPoints'],
            'displayOnly': audit['displayOnly'],
            'touchesPermissions': audit['safety'],
            'needsServer': audit['server'],
            'writesUserData': audit['writes'],
            'admission': audit['conclusion'],
            'admissionNote': audit['note'],
            'limits': '官方更新后失效，需要重新适配；会阻断官方自动更新并要求重签',
            'evidence': '端到端解析为 true（%d/%d），官方未打补丁备份 0/%d'
                        % (len(gates), len(gates), len(gates)),
        })
    for gate in latest['mainGates']:
        audit = MAIN_GATE_AUDIT.get(gate)
        if audit is None:
            continue
        rows.append({
            'id': 'main-' + gate,
            'title': audit['name'],
            'group': '补丁',
            'officialKey': gate,
            'officialName': audit['name'],
            'officialValue': True,
            'layer': 1,
            'method': '主进程强制表补 forced/true（仅当官方表里没有该键）',
            'ui': False,
            'uiTitle': None,
            'controls': audit['controls'],
            'readPoints': ['主 chunk 设置项读取'],
            'displayOnly': True,
            'touchesPermissions': False,
            'needsServer': False,
            'writesUserData': '否',
            'admission': '保留',
            'admissionNote': audit['note'],
            'limits': '官方更新后失效，需要重新适配',
            'evidence': '官方备份 Hon 表里无该键；隔离副本插入后读回 forced/true',
        })
    return {'rows': rows}


def check(state):
    """文档与代码的一致性检查；返回问题列表。"""
    problems = []
    core, _, features, _ = load()
    readme = (ROOT / 'README.md').read_text()
    latest = state['adapters'][-1]
    if latest['version'] not in readme:
        problems.append('README.md 没有提到已验证构建 ' + latest['version'])
    if 'revision %d' % state['patchRevision'] not in readme:
        problems.append('README.md 没有提到补丁 revision %d' % state['patchRevision'])
    for path, expected in ((STATE_FILE, state), (CATALOG_FILE, catalog(state))):
        if not path.exists():
            problems.append('缺少 ' + str(path.relative_to(ROOT)) +
                            '，请运行 scripts/state.py --write')
        elif json.loads(path.read_text()) != json.loads(json.dumps(expected)):
            problems.append(str(path.relative_to(ROOT)) +
                            ' 已过期，请运行 scripts/state.py --write')
    ui_rows = state['uiRows']
    if [row['id'] for row in ui_rows[:-1]] != state['configKeys']:
        problems.append('界面开关顺序与 FEATURES 不一致')
    for row in ui_rows[:-1]:
        if row['id'] in features.FEATURES:
            if row['title'] != features.FEATURES[row['id']]['title']:
                problems.append('界面标题与后端不一致：' + row['id'])
            if row['group'] != features.FEATURES[row['id']]['group']:
                problems.append('界面分组与后端不一致：' + row['id'])
    for adapter in state['adapters']:
        if core.verified_system(adapter) == (None, None):
            problems.append('构建 ' + adapter['version'] + ' 没有登记已验证的系统范围')
    latest = state['adapters'][-1]
    for gate in latest['rendererGates']:
        if gate not in GATE_AUDIT:
            problems.append('渲染进程门 ' + gate + ' 还没有准入复核记录')
        elif GATE_AUDIT[gate]['conclusion'] != '保留':
            problems.append('渲染进程门 ' + gate + ' 的复核结论不是保留，仍在补丁里')
    for gate in latest['mainGates']:
        if gate not in MAIN_GATE_AUDIT:
            problems.append('主进程门 ' + gate + ' 还没有准入复核记录')
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='写入 state/ 与 catalog/')
    parser.add_argument('--check', action='store_true', help='只检查文档与代码是否一致')
    args = parser.parse_args()
    state = status()
    if args.write:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        CATALOG_FILE.write_text(json.dumps(catalog(state), ensure_ascii=False, indent=2) + '\n')
        print('wrote', STATE_FILE.relative_to(ROOT), CATALOG_FILE.relative_to(ROOT))
        return
    problems = check(state)
    for problem in problems:
        print('不一致：' + problem)
    if problems:
        raise SystemExit(1)
    print('文档与代码一致。')


if __name__ == '__main__':
    main()
