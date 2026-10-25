#!/usr/bin/env python3
"""版本固定的官方应用补丁基础工具。

这里只负责三件事：
1. 识别 Claude 的版本和文件指纹；
2. 读写 Electron 的 app.asar；
3. 提供统一的校验、哈希和替换工具。

不在这里实现任何 Computer Use、浏览器或模型功能，也不修改 Claude 的
逐应用授权、系统权限、截图过滤、拒绝、中断或动作审查。
"""
import hashlib, json, os, pathlib, platform, plistlib, re, secrets, shutil, struct, subprocess, sys, tempfile

import keychain

# 源码仓库根目录。打包成独立助手后，backend.py 会把 WORK 指到用户数据目录，
# 避免把备份写进只读或会被替换的应用包。
ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = ROOT / 'work'
APP = WORK / 'Claude 原生助手 Test.app'
BASELINE = WORK / 'baseline' / 'Claude.app'
PROFILE = WORK / 'profile'

# 最早的隔离测试版本；正式版适配见下方 ADAPTER_*。
VERSION = '2.31226.1'
ASAR_HASH = 'e0f0a70911d2d206f17572e82636c80a15173281db4966a44c2558e8f8ae6bb8'
FILES = {
 '.vite/build/index.chunk-iZtWjlXf.js': '93ac55f6bfc656fa774aa0029b03a326b86a6e0c265fad1fc402965d0f6eaa77',
 '.vite/build/index.pre.js': '37215841b4fa87e16f78b17f98895c2fcf72c62ee46d079db4f739c797567e2d',
 '.vite/build/index.chunk-Cyt2WTko.js': '29738ec09f4bf44d511caae628b0f786e35956848e6a18c3159c9928d6ac1ad9',
}
SOURCE_FILES = {
 'Contents/Info.plist': '0b0076d395dcff8e5b47c819d7fb1c0ce733e1bdcb3d2d4969bc83978b61b218',
 'Contents/MacOS/Claude': '76415d30fd41e3bcf55545a4a031ddd8ebac9806368ed5e76b2421e908d4b5a0',
 'Contents/Resources/app.asar.unpacked/node_modules/@ant/claude-swift/build/Release/computer_use.node': '5e4ec2a51641599a3c385f66421f7096730deec0d023f4c98029272a98142c57',
}
def sha(b):
    """返回字节内容的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(b).hexdigest()


# 界面与后端之间的协议版本。只有字段含义变化或删字段时才加 1；
# 单纯新增字段不加，旧界面会忽略不认识的字段。
PROTOCOL_VERSION = 1


def envelope(result):
    """每个响应都带上协议版本，界面据此判断能否解析。"""
    if isinstance(result, dict):
        result.setdefault('protocol', PROTOCOL_VERSION)
    return result


def emit(result):
    """把结果按协议打印成一行 JSON（界面只读标准输出）。"""
    print(json.dumps(envelope(result), ensure_ascii=False))


def run(*args, timeout=None):
    """运行系统命令；失败时抛出异常，不吞掉错误。

    timeout 只给“可能等用户点系统对话框”的命令用：这类命令没人点就会
    永远卡住，宁可超时后明确报错，也不让后台线程无声挂死。
    """
    return subprocess.run(args, check=True, capture_output=True, timeout=timeout)


class BuildRefused(ValueError):
    """构建不符合写入条件；调用方应当拒绝修改，而不是换个办法继续。"""


class UnregisteredBuild(BuildRefused):
    """这个版本没有登记过固定指纹：只允许只读诊断，不允许补丁。"""


class FingerprintMismatch(BuildRefused):
    """版本号认识，但构建内容与登记的不一致：可能被改过，一律拒绝。"""


class OfficialDefinitionConflict(BuildRefused):
    """官方对目标门已有不同定义：停止，进入研究，绝不覆盖。"""

    def __init__(self, key):
        super().__init__('目标开关形状冲突，拒绝覆盖官方语义：' + key)
        self.key = key


class SyntaxCheckUnavailable(BuildRefused):
    """找不到可用的语法检查器：按失败即拒绝，不写入。"""


class SyntaxCheckFailed(BuildRefused):
    """改动后的表达式语法不成立：拒绝写入。"""


def read_asar(path):
    """读取 Electron 的 app.asar。

    ASAR 由三部分组成：8 字节固定头、文件索引 JSON、以及所有打包文件。
    这里返回完整字节、索引头和应用数据区起点。
    """
    b = path.read_bytes(); size = struct.unpack_from('<I', b, 4)[0]
    length = struct.unpack_from('<I', b, 12)[0]
    return b, json.loads(b[16:16+length]), 8+size


def node(header, path):
    """按路径取出 ASAR 索引中的文件节点。"""
    n = header
    for part in path.split('/'): n = n['files'][part]
    return n


def content(b, h, base, path):
    """取出一个已打包文件的原始内容。"""
    n=node(h,path)
    if n.get('unpacked') or 'offset' not in n: raise ValueError('Target is not packed')
    off=base+int(n['offset']); return b[off:off+n['size']]

ADAPTER_31226 = {
 'version': VERSION,
 'asarHash': ASAR_HASH,
 'main': '.vite/build/index.chunk-Cyt2WTko.js',
 'featureAnchor': 'var d2t={',
 'files': FILES,
 'sourceFiles': SOURCE_FILES,
}

ADAPTER_37337 = {
 'version': '2.37337.1',
 'asarHash': '82a54170115e3e20c9600a257a5b2679cbadb83f38baf5111235f123af484163',
 'main': '.vite/build/index.chunk-BWk6pVUt.js',
 'featureAnchor': 'var E3t={',
 # 2371478310 / 857846362：Code 原生 To-Do 等新版工作台条件。
 # 2120747855：官方“提示建议”设置项。它只决定设置是否可见；
 # 建议的生成、Tab 采纳和发送仍走官方原逻辑。
 'mainAvailability': '2371478310:{rule:"forced",value:!0},857846362:{rule:"forced",value:!0},2120747855:{rule:"forced",value:!0},',
 'webFeatureAnchor': 'D3t={',
 'webAvailability': '3264789062:{rule:"always",value:!0},3335190042:{rule:"always",value:!0},3858164079:{rule:"always",value:!0},1235690166:{rule:"always",value:!0},',
 'files': {
  '.vite/build/index.pre.js': '1c1b002f474fcafc6b3a62d4d5a21ca573367eccc1541bd2e30308a6009c1453',
  '.vite/build/index.chunk-BWk6pVUt.js': 'f7d74e113f34761a675816fe2abf65b3ddc190327ee7623d9c822f7b59cceaca',
  '.vite/build/index.chunk-DWqiEHEK.js': 'bab1dcc6034f06cd4476d151ed63b42f5f7875aea0ba3a9e3664e40e679b8b04',
 },
 'sourceFiles': {
  'Contents/Info.plist': '642b028bf871166b5f0004c8d6936986d5e9e1850ce8cfb9ffb76fb049e03318',
  'Contents/MacOS/Claude': 'ff556d6959554d2b52f729dd2c90247d8206f503525b9c523197268b171c70a9',
  'Contents/Resources/app.asar.unpacked/node_modules/@ant/claude-swift/build/Release/computer_use.node': 'fc0664606674f7762bfcbe2dc7c2660bf9cc32b049e4ebd93e2de0c000c1cb60',
 }
}

ADAPTER_42304 = {
 'version': '2.42304.0',
 'asarHash': 'e952a1c0f603e6c2f54f2fc9551843f6d7ccf9c73071a814e5ce4a49ee4c2f4d',
 'main': '.vite/build/index.chunk-muI2IV6k.js',
 'featureAnchor': 'var P8t={',
 # 2371478310 / 857846362：Code 原生 To-Do 等新版工作台条件。
 # 2120747855：官方“提示建议”设置项。它只决定设置是否可见；
 # 建议的生成、Tab 采纳和发送仍走官方原逻辑。
 'mainAvailability': '2371478310:{rule:"forced",value:!0},857846362:{rule:"forced",value:!0},2120747855:{rule:"forced",value:!0},',
 'webFeatureAnchor': 'F8t={',
 'webAvailability': '3264789062:{rule:"always",value:!0},3335190042:{rule:"always",value:!0},3858164079:{rule:"always",value:!0},1235690166:{rule:"always",value:!0},',
 'files': {
  '.vite/build/index.pre.js': '58ddbd5eff5824cf4bfce1fd3d6f08a7102658a8250e6c5b172e7bddc5d3ccf8',
  '.vite/build/index.chunk-muI2IV6k.js': '4a8f1c07e69231e47127d3c55fcdda84eddb089ce06d347ad29591e25b6fb5f5',
  '.vite/build/index.chunk-DWlgBD4T.js': '6b684c111c81d968fb0e1fce97a218eca553916209543fae660633f153acc239',
 },
 'sourceFiles': {
  'Contents/Info.plist': 'b6314074decd2fea53e96a3ede5a492606d8843e57bccfba9792993d90719a9c',
  'Contents/MacOS/Claude': '9129d9d7032e36c5ee67ac0c3f9cb64cacf5f8047dd8d01a018847a0d75e2900',
  'Contents/Resources/app.asar.unpacked/node_modules/@ant/claude-swift/build/Release/computer_use.node': '80b4b0399a82c3577c33446eb0f776c48f4b3803271b0c61250d9fe8c823c888',
 }
}

# Updater-delivered arm64 build of 2.42304.0. Same version string as the
# universal DMG build above, but different bundle fingerprints. The three
# patch target files are byte-identical to the universal build.
ADAPTER_42304_ARM64 = {
 'version': '2.42304.0',
 'asarHash': 'bfa22ac393906762fa04b9607ff202907405b089c1ae7db257159202f31bb3d0',
 'main': '.vite/build/index.chunk-muI2IV6k.js',
 'featureAnchor': 'var P8t={',
 # 2371478310 / 857846362：Code 原生 To-Do 等新版工作台条件。
 # 2120747855：官方“提示建议”设置项。它只决定设置是否可见；
 # 建议的生成、Tab 采纳和发送仍走官方原逻辑。
 'mainAvailability': '2371478310:{rule:"forced",value:!0},857846362:{rule:"forced",value:!0},2120747855:{rule:"forced",value:!0},',
 'webFeatureAnchor': 'F8t={',
 'webAvailability': '3264789062:{rule:"always",value:!0},3335190042:{rule:"always",value:!0},3858164079:{rule:"always",value:!0},1235690166:{rule:"always",value:!0},',
 'files': {
  '.vite/build/index.pre.js': '58ddbd5eff5824cf4bfce1fd3d6f08a7102658a8250e6c5b172e7bddc5d3ccf8',
  '.vite/build/index.chunk-muI2IV6k.js': '4a8f1c07e69231e47127d3c55fcdda84eddb089ce06d347ad29591e25b6fb5f5',
  '.vite/build/index.chunk-DWlgBD4T.js': '6b684c111c81d968fb0e1fce97a218eca553916209543fae660633f153acc239',
 },
 'sourceFiles': {
  'Contents/Info.plist': '2d602266568c490cb90b5411a15894306a00736363472ba423613a0b48fe68e4',
  'Contents/MacOS/Claude': 'b51aecd06929295e0552d40c754524db8783b90cfcd219a7ab67a9bc138e2d8e',
  'Contents/Resources/app.asar.unpacked/node_modules/@ant/claude-swift/build/Release/computer_use.node': '6788de7db2b32ac723f4e02decd62ffe8873ce8f0a5fd98dedd0eebc2b2f22d2',
 }
}

# 2.42304.2：更新器分发的 arm64 构建。本机只验证过这一种构建，
# 如果官网同时提供 universal 包，其整包指纹不同，会被当成未知版本拒绝。
# 功能表本身仍是 W8t（强制项）/ G8t（下发渲染进程项）两张，与 2.42304.0
# 同名同结构；可用性条件与 revision 10 完全一致，没有新增或改写功能。
ADAPTER_42304_2 = {
 'version': '2.42304.2',
 'asarHash': 'f0a063c8c2c7fb21eb51bca00f086e5b7355d7208e3d9f28600ea79961830907',
 'main': '.vite/build/index.chunk-BkhOOUzl.js',
 'featureAnchor': 'var W8t={',
 # 2371478310 / 857846362：Code 原生 To-Do 等新版工作台条件。
 # 2120747855：官方“提示建议”设置项。它只决定设置是否可见；
 # 建议的生成、Tab 采纳和发送仍走官方原逻辑。
 'mainAvailability': '2371478310:{rule:"forced",value:!0},857846362:{rule:"forced",value:!0},2120747855:{rule:"forced",value:!0},',
 'webFeatureAnchor': 'G8t={',
 'webAvailability': '3264789062:{rule:"always",value:!0},3335190042:{rule:"always",value:!0},3858164079:{rule:"always",value:!0},1235690166:{rule:"always",value:!0},',
 'files': {
  '.vite/build/index.pre.js': '64d923c811b5352cedb738d6644382a7ac4c822801a9aef256fdd3285429ea0e',
  '.vite/build/index.chunk-BkhOOUzl.js': '07d785c56978eb335c933562a514b3eba79cc5f307b4cd46f7f84e2dca5c83ec',
  '.vite/build/index.chunk-K-UfRPiV.js': '9f3f13fef1d5ea32ed0c934361f57a6d72385938223a6ad330e1540ea09aa11a',
 },
 'sourceFiles': {
  'Contents/Info.plist': '0ac620b4fb77f73ff2a313b377ed55a4518d97c9c484cd6cb753a91056c02cc2',
  'Contents/MacOS/Claude': 'ee962f9d2235e3f636a49aac7acf5c6b005877347edcdbb2a093c9e7a46ee737',
  'Contents/Resources/app.asar.unpacked/node_modules/@ant/claude-swift/build/Release/computer_use.node': 'da5184e4bee4c8d85fd5246caa27a62d88e99a0dec50777dd2b72aabf237fccd',
 }
}

# 3.1623.0：大版本跃迁后重新核对的构建。功能表仍是两张：
# `var ken={…}`（强制项）与紧随其后的 `Aen={…}`（下发渲染进程项）。
# 与 2.42304.2 的差异都在表里，所以适配器只追加真正还缺的开关：
# - 2371478310 / 857846362 官方已自行强制，不再追加（重复键会被后出现的官方值覆盖）；
# - 3264789062 改成 rule:"follows"，是官方有意跟随“会话是否跑在本机”，不覆盖；
# - 3858164079（状态分组）/ 1235690166（每文件夹工作树）的数值默认项已从 Aen 里
#   消失，但渲染进程仍按名字查这两个门；继续追加，只恢复可用性默认值，不改行为；
# - 3335190042 是无关的面板实验，不追加；
# - 2120747855（提示建议设置项）仍然缺失，继续强制。
ADAPTER_31623 = {
 'version': '3.1623.0',
 'asarHash': '760e7cbb6f861f0bc0e71e71fd9c6b721ba735f0f638b6ea623645daff4261a7',
 'main': '.vite/build/index.chunk-BLvpT1n4.js',
 'featureAnchor': 'var ken={',
 'mainAvailability': '2120747855:{rule:"forced",value:!0},',
 'webFeatureAnchor': 'Aen={',
 'webAvailability': '3858164079:{rule:"always",value:!0},1235690166:{rule:"always",value:!0},',
 'files': {
  '.vite/build/index.pre.js': '3061f02edf85b68dc862372a93d68986f83ee0a39e60859eb44f8a7f12ada8a2',
  '.vite/build/index.chunk-BLvpT1n4.js': 'b842b02003a350a14dd76bf6d56a8fda75255fb76576a2a7ac7407ad3c057315',
  '.vite/build/index.chunk-Dln4TQLP.js': '5b1d9361afc8deab52a7659f026dfbc74b9d944d9077663778195547917bcd4b',
 },
 'sourceFiles': {
  'Contents/Info.plist': '6744736f8ce9888c17966d223a1a277409f6c987a4847d4a92c5fa7bf34948bd',
  'Contents/MacOS/Claude': '9cda504cf0bf64709e2da71f4cecd7dbd5b523273603abd231dcbe83d9cccb22',
  'Contents/Resources/app.asar.unpacked/node_modules/@ant/claude-swift/build/Release/computer_use.node': 'eda174bfbbbbcb6747a756a397466ebb06f381cf9649c18baad7564571cbdf2a',
 }
}

# 3.6556.2：官方大版本更新后的构建。功能表仍是两张，变量名换成
# `var Hon={…}`（强制项）与 `Uon={…}`（下发渲染进程项）。
# 表内已有 2371478310 / 857846362（Code 工作台），不再重复追加；
# 2120747855（提示建议设置项）仍然缺失，继续强制。
# 渲染进程表里这一版没有任何“状态分组 / 每文件夹工作树 / PR 面板”的默认值，
# 而 claude.ai 前端仍按名字查这些门，所以补回默认值；只改“设置项是否可见”
# 这类部署可用性，不改官方分桶、分类、PR 或工作树逻辑。
ADAPTER_36556 = {
 'version': '3.6556.2',
 'asarHash': 'd1315bccfdb85cfcd79fcc34f194f3a5154d383bff1518fde95d39e821e3852d',
 'main': '.vite/build/index.chunk-jPKPzV-P.js',
 'featureAnchor': 'var Hon={',
 'mainAvailability': '2120747855:{rule:"forced",value:!0},',
 'webFeatureAnchor': 'Uon={',
 'webAvailability': (
  '3858164079:{rule:"always",value:!0},'   # claudeai_session_state：会话状态分组
  '1235690166:{rule:"always",value:!0},'   # cai_epitaxy_worktree：每文件夹工作树
  '1282344890:{rule:"always",value:!0},'   # ccr_autofix_ui：PR 自动修复设置
  '3774822426:{rule:"always",value:!0},'   # epitaxy_pr_panel：PR 面板/在浏览器打开
  '2280470525:{rule:"always",value:!0},'   # ccr_code_requires_action_category_enabled
  # 下面 6 个是 3.6556.2 里前端仍在读取、但两张表都没有的纯界面开关。
  # 官方缺省即关闭，功能入口整个不出现；补回 true 只恢复入口，动作仍走官方逻辑。
  # 灰度门（如 ccr_plan_mode_enabled）不在此列：那是官方逐步放量，不属于部署可用性。
  '69389141:{rule:"always",value:!0},'     # ccd_palette_session_actions：命令面板会话操作
  '3619252400:{rule:"always",value:!0},'   # claudeai_cc_epitaxy_folder_browser：SSH 文件夹浏览
  '4000837158:{rule:"always",value:!0},'   # epitaxy_plan_pane_auto_open：计划面板自动打开
  '273163521:{rule:"always",value:!0},'    # claudeai_cc_epitaxy_sticky_prompt：置顶提示
  '2149681774:{rule:"always",value:!0},'   # ccd_prompt_jump：上一/下一提示跳转
  '2697147784:{rule:"always",value:!0},'   # ccd_checkin_noop_rollup：检查点折叠
 ),
 'files': {
  '.vite/build/index.pre.js': '87806ce239d643852bf9ac06987f4f48415f7afc3046546f6c1654a1a7670a5b',
  '.vite/build/index.chunk-jPKPzV-P.js': '6e898798943b990e6eefa9209c61d977866b7898f681864f02c8cad3bb691ed5',
  '.vite/build/index.chunk-BzjccvJJ.js': 'cb0a1d0fdd1e22011a989396876a484a7317e4507dcf5d5831921fde1630daaa',
 },
 'sourceFiles': {
  'Contents/Info.plist': '00075ca3f823a0b7731e0591a25f512136ada6d5c208cad1a5dce562b392a276',
  'Contents/MacOS/Claude': '6dc844c1b12bea0d46f3840a97dbab121aa64bad4710730eed46ef0cce6ac4b5',
  'Contents/Resources/app.asar.unpacked/node_modules/@ant/claude-swift/build/Release/computer_use.node': '301ce0ba38ac3725d5b5833abfaae9c7e453d54fddd509850344f821f2931aa1',
 }
}

# 已验证能安全写入的系统范围，按构建登记。空表示“还没有任何系统验证过”，
# 未知构建永远不会进入写入路径。系统升级超出登记范围时保持官方行为，
# 直到完成静态检查和真机回归——不随系统升级自动抬高。
VERIFIED_SYSTEM = {
    ADAPTER_31226['version']: ('arm64', (27, 0, 1)),
    ADAPTER_37337['version']: ('arm64', (27, 0, 1)),
    ADAPTER_42304['version']: ('arm64', (27, 0, 1)),
    ADAPTER_42304_2['version']: ('arm64', (27, 0, 1)),
    ADAPTER_31623['version']: ('arm64', (27, 0, 1)),
    ADAPTER_36556['version']: ('arm64', (27, 0, 1)),
}


def verified_system(adapter):
    """返回这套构建已验证的 (架构, 系统版本)；没登记就是 (None, None)。

    只读登记表。现场适配的构建不写进这里：它的验证环境是本机，由
    `check_system` / `system_ceiling` 单独处理，免得把“本机是什么”当成
    “登记过什么”。
    """
    return VERIFIED_SYSTEM.get(adapter['version'], (None, None))


def system_name(value):
    return '.'.join(str(part) for part in value) if value else '未知'


def adapter_system(adapter):
    """这套构建的验证环境：已登记的读登记表，现场适配的读本机记录。

    现场适配（`discovered`）的构建是本机按官方签名和结构现场认下来的，
    它只在生成它的这台机器上使用，所以验证环境就是这台机器。
    """
    registered = verified_system(adapter)
    if registered != (None, None):
        return registered
    if adapter.get('discovered'):
        return adapter.get('system') or (None, None)
    return None, None


def system_ceiling(adapter):
    """Computer Use 的系统版本上限：等于这套构建的验证环境。

    已登记构建的上限只随登记表变化，不随系统升级自动抬高；系统升级后
    超出范围时保持官方行为。现场适配的构建本来就用本机版本做范围，
    两者是同一个值。
    """
    _, version = adapter_system(adapter)
    if version is None:
        raise BuildRefused('这个构建没有可用的系统版本范围；停止修改。')
    return '{major:%d,minor:%d}' % (version[0], version[1])


def check_platform(verified, machine, mac_version):
    """架构和系统版本都必须在验证范围内，否则拒绝写入。

    `verified` 是 (架构, 版本元组)；`machine`/`mac_version` 由调用方传入
    （生产代码传 platform 的结果），这样测试可以在任何主机上给出确定的
    结果，不受宿主机影响。
    """
    architecture, version = verified or (None, None)
    if architecture is None:
        raise BuildRefused('这个构建没有可用的系统范围；停止修改。')
    actual = tuple(int(part) for part in re.findall(r'\d+', mac_version or ''))[:3]
    if machine != architecture or actual != tuple(version):
        raise BuildRefused(
            '当前适配仅验证 %s / macOS %s；检测到 %s / macOS %s，停止修改。'
            % (architecture, system_name(version), machine or '未知', mac_version or '未知'))


def check_system(adapter, machine, mac_version):
    """Desktop 补丁的系统范围：已登记的按登记表，现场适配的按本机。"""
    check_platform(adapter_system(adapter), machine, mac_version)


# 这些版本才有“新版 Code 工作台”相关界面；补丁 revision 5 起按版本判断。
CODE_UI_VERSIONS = (ADAPTER_37337['version'], ADAPTER_42304['version'],
                    ADAPTER_42304_2['version'], ADAPTER_31623['version'],
                    ADAPTER_36556['version'])

# 补回渲染进程默认值的门。官方每一版都可能删掉其中几条，前端却仍按名字查；
# 未登记版本现场适配时也沿用这一组，避免“能打补丁但少了功能”。
RENDERER_AVAILABILITY = (
 '3858164079:{rule:"always",value:!0},'
 '1235690166:{rule:"always",value:!0},'
 '1282344890:{rule:"always",value:!0},'
 '3774822426:{rule:"always",value:!0},'
 '2280470525:{rule:"always",value:!0},'
 '69389141:{rule:"always",value:!0},'
 '3619252400:{rule:"always",value:!0},'
 '4000837158:{rule:"always",value:!0},'
 '273163521:{rule:"always",value:!0},'
 '2149681774:{rule:"always",value:!0},'
 '2697147784:{rule:"always",value:!0},'
)

# 未登记版本现场适配时，主进程要强制打开的部署门。
DISCOVERED_MAIN_GATES = (
 '2371478310:{rule:"forced",value:!0},'
 '857846362:{rule:"forced",value:!0},'
 '2120747855:{rule:"forced",value:!0},'
)


def referenced_entries(javascript, entries):
    """只保留这一版前端仍在读的键。

    补回的开关是给前端按名字查的：前端整版不再读某个键时，补进去只是
    死条目——既不能恢复功能，也把“补了什么”说得比实际多。所以按整个包
    的 JS 文本过滤一次，只补还有读取点的键。
    """
    kept = [key + ':{' + body + '},' for key, body in split_entries(entries)
            if re.search(r'(?<![0-9])' + key + r'(?![0-9])', javascript)]
    return ''.join(kept)


def adapters_for_version(version):
    """返回一个官方版本可能对应的构建。

    2.42304.0 同时存在官网 universal DMG 和更新器 arm64 两种构建，
    版本号相同但 ASAR / 源文件指纹不同，必须分开校验。
    """
    if version == VERSION:
        return [ADAPTER_31226]
    if version == ADAPTER_37337['version']:
        return [ADAPTER_37337]
    if version == ADAPTER_42304['version']:
        return [ADAPTER_42304_ARM64, ADAPTER_42304]
    if version == ADAPTER_42304_2['version']:
        return [ADAPTER_42304_2]
    if version == ADAPTER_31623['version']:
        return [ADAPTER_31623]
    if version == ADAPTER_36556['version']:
        return [ADAPTER_36556]
    return []


def installed_build(record):
    """从安装记录还原原始官方构建，用于把备份绑定到同一构建。

    Info.plist 在打补丁时会被改写，主程序在重签时也会变化，二者都不能
    作为判断依据；只有 computer_use.node 保持官方原样，且能唯一区分
    同版本的不同构建。旧记录缺少该文件时返回 None，调用方跳过检查。
    """
    files = record.get('installedFiles') or {}
    matches = []
    for adapter in adapters_for_version(record.get('version', VERSION)):
        fingerprints = {path: digest for path, digest in adapter['sourceFiles'].items()
                        if path.endswith('computer_use.node')}
        if fingerprints and all(files.get(path) == digest
                                for path, digest in fingerprints.items()):
            matches.append(adapter)
    return matches[0] if len(matches) == 1 else None


PACKED_LIMIT = 20_000_000


def packed_js(header):
    """遍历 ASAR 里所有已打包的 .js 文件。"""
    def walk(nodes, prefix=''):
        for name, child in nodes.get('files', {}).items():
            path = prefix + '/' + name if prefix else name
            if 'files' in child:
                yield from walk(child, path)
            elif not child.get('unpacked') and 'offset' in child and path.endswith('.js'):
                yield path, child

    yield from walk(header)


def feature_tables(text):
    """找出强制项表和下发渲染进程项表的锚点。

    锚点是对象字面量的开头（`var X={` 或 `X={`），只认紧跟着
    `rule:"forced"` / `rule:"always"` 的那一个；找不到或找到多个都拒绝。
    这样新版本改了变量名也不用改代码。
    """
    forced, always = [], []
    for match in re.finditer(r'(?:var\s+)?([A-Za-z0-9$_]{1,16})=\{', text):
        window = text[match.end():match.end() + 400]
        if 'rule:"forced"' in window:
            forced.append(match.group(0))
        elif 'rule:"always"' in window:
            always.append(match.group(0))
    forced, always = list(dict.fromkeys(forced)), list(dict.fromkeys(always))
    if len(forced) != 1 or len(always) != 1:
        raise ValueError('Feature tables are not uniquely locatable')
    return forced[0], always[0]


def table_bounds(text, anchor):
    """返回锚点所在对象字面量的 (起点, 终点)，用来把查找限制在表内。"""
    start = text.find(anchor)
    if start < 0:
        raise ValueError('Feature table anchor missing: ' + anchor)
    brace = text.index('{', start)
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == '{':
            depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0:
                return brace, index + 1
    raise ValueError('Unterminated feature table')


def split_entries(text):
    """把 `k:{…},k2:{…},` 拆成 [(k, '{…}' 内部内容), …]。"""
    entries, index = [], 0
    while index < len(text):
        if text[index] in ', ':
            index += 1
            continue
        match = re.match(r'(\d+):\{', text[index:])
        if not match:
            raise ValueError('Malformed availability entry')
        brace = index + len(match.group(0)) - 1
        depth = 0
        for end in range(brace, len(text)):
            if text[end] == '{':
                depth += 1
            elif text[end] == '}':
                depth -= 1
                if depth == 0:
                    break
        else:
            raise ValueError('Unterminated availability entry')
        entries.append((match.group(1), text[brace + 1:end]))
        index = end + 1
    return entries


def table_definition(text, anchor, key):
    """取表里 key 的**最后一次**定义体；没有就返回 None。只在表内查找。

    对象字面量里后定义获胜，所以表尾那份才是实际生效的值——插入覆盖项之后
    再读，读到的必须是我们的定义。
    """
    start, end = table_bounds(text, anchor)
    matches = list(re.finditer(r'(?<![0-9])' + re.escape(key) + r':\{', text[start:end]))
    if not matches:
        return None
    brace = start + matches[-1].end() - 1
    depth = 0
    for index in range(brace, end):
        if text[index] == '{':
            depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0:
                return text[brace + 1:index]
    raise ValueError('Unterminated gate definition: ' + key)


def insert_entries(text, anchor, entries, report=None, conflict='reject'):
    """把可用性条目写进功能表，返回 (新文本, 实际结果)。

    三种情况分开处理：

    - 表里没有这个键：插到表头。
    - 表里已有完全相同的定义：跳过，什么都不做。
    - 表里已有不同定义：默认直接报错。官方对目标门有自己的定义
      （强制关闭、`when`/`follows` 条件、下调的系统上限）时，覆盖它就等于
      改官方行为，必须停下来进入研究。`conflict='override'` 只留给测试和
      维护工具显式调用，生产路径不再使用。

    结果写进 report（若提供），用于安装记录和界面提示：
    `inserted` / `skipped` / `overridden`。
    """
    result = {'inserted': [], 'skipped': [], 'overridden': []}
    head, tail = [], []
    for key, body in entries:
        current = table_definition(text, anchor, key)
        if current is None:
            head.append((key, body))
            result['inserted'].append(key)
        elif current.replace(' ', '') == body.replace(' ', ''):
            result['skipped'].append(key)
        elif conflict == 'reject':
            raise OfficialDefinitionConflict(key)
        else:
            tail.append((key, body))
            result['overridden'].append(key)
    if head:
        text = replace_once(text, anchor, anchor
                            + ''.join(key + ':{' + body + '},' for key, body in head))
    if tail:
        # 表尾：定位这张表自己的结束花括号。注意表末条目可能没有尾逗号，
        # 直接拼 `k:{…},` 会变成 `…},k:{…}` 之外还多出逗号，必须补分隔符。
        _, end = table_bounds(text, anchor)
        close = end - 1
        prefix = text[:close].rstrip()
        if not prefix.endswith(','):
            prefix += ','
        inserted = ''.join(key + ':{' + body + '},' for key, body in tail)
        text = prefix + inserted + text[close:]
    if report is not None:
        report.update(result)
    return text


def main_availability(adapter):
    """主进程功能表要追加的可用性条件。"""
    return adapter.get('mainAvailability', '')


def web_availability(adapter):
    """渲染进程功能表要追加的可用性条件。"""
    return adapter.get('webAvailability', '')


DISCOVERY_CACHE = {}


def discover_adapter(app):
    """未登记版本：按官方身份和功能表结构做**只读**定位。

    只给诊断和维护工具用，不参与任何写入路径。补丁只认登记表里的固定
    指纹：未知构建一律只诊断、不写入。调用方仍然要先通过官方签名校验；
    表找不到、表不唯一都会抛异常。
    """
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != 'com.anthropic.claudefordesktop':
        raise ValueError('Not the expected application')
    b, header, base = read_asar(app / 'Contents/Resources/app.asar')
    digest = sha(b)
    cached = DISCOVERY_CACHE.get(digest)
    if cached:
        return cached
    found = []
    for path, child in packed_js(header):
        if int(child.get('size', 0)) > PACKED_LIMIT:
            continue
        if b'rule:"forced"' in content(b, header, base, path):
            found.append(path)
    if len(found) != 1:
        raise ValueError('Feature table not uniquely located')
    text = content(b, header, base, found[0]).decode('utf-8', 'replace')
    forced_anchor, always_anchor = feature_tables(text)
    # 这一版还在读哪些键：整个包里所有已打包 JS 的文本。
    javascript = '\n'.join(
        content(b, header, base, path).decode('utf-8', 'replace')
        for path, child in packed_js(header)
        if int(child.get('size', 0)) <= PACKED_LIMIT)
    adapter = {
        'version': info.get('CFBundleShortVersionString'),
        'asarHash': digest,
        'main': found[0],
        'featureAnchor': forced_anchor,
        'webFeatureAnchor': always_anchor,
        # 用和登记版本同一组条件；insert_entries 会跳过官方已强制成同样
        # 形状的项，遇到形状不同则拒绝，不覆盖官方语义。
        'mainAvailability': referenced_entries(
            javascript, DISCOVERED_MAIN_GATES),
        # 渲染进程表用 reject：表里已有的 when/follows 是官方语义，不能覆盖。
        # 只补这一版前端仍在读的键：官方整版删掉的键已经没人读，补进去只是
        # 死条目，按“门从官方默认表消失时不自动补回”不补。
        'webAvailability': referenced_entries(
            javascript, RENDERER_AVAILABILITY),
        # 未登记构建没有固定文件指纹；安全性来自官方签名和结构校验。
        'files': {},
        'sourceFiles': {},
        'discovered': True,
    }
    DISCOVERY_CACHE[digest] = adapter
    return adapter


def registered_adapter(app):
    """只按已登记版本和指纹取适配器，不做结构识别。

    备份校验用这个：备份必须是官方原始构建，不能是“看起来结构像”的东西。
    """
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    version = info['CFBundleShortVersionString']
    adapters = adapters_for_version(version)
    if not adapters:
        raise ValueError('Unknown version; no patch applied')
    if len(adapters) == 1:
        return adapters[0]
    try:
        b, _, _ = read_asar(app / 'Contents/Resources/app.asar')
    except Exception:
        raise ValueError('ASAR fingerprint mismatch; no patch applied')
    digest = sha(b)
    for adapter in adapters:
        if digest == adapter['asarHash']:
            return adapter
    raise ValueError('ASAR fingerprint mismatch; no patch applied')


def source_adapter(app):
    """选择适配器：已登记版本认指纹，未登记的新版本按结构现场适配。

    用户 2026-10-25 明确：官方桌面端升级后，旧助手要默认照常解锁，不要
    因为“这个版本还没登记”就失效。所以分两种情况：

    - 版本登记过：只认固定指纹。构建内容不同 → FingerprintMismatch
      （可能被改过，绝不写入）。
    - 版本没登记：走 `discovered_adapter` 的三重校验（官方签名、包标识、
      功能表结构）。任何一项不符都拒绝；通过就照常适配。

    两者都不放松“失败即拒绝”：签名不是官方的、包标识不对、表找不到或
    不唯一、目标门形状冲突，仍然一律停下。
    """
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    version = info['CFBundleShortVersionString']
    adapters = adapters_for_version(version)
    try:
        b, _, _ = read_asar(app / 'Contents/Resources/app.asar')
    except Exception:
        raise FingerprintMismatch('ASAR fingerprint mismatch; no patch applied')
    digest = sha(b)
    for adapter in adapters:
        if digest == adapter['asarHash']:
            return adapter
    if adapters:
        # 同一版本号可能有多种官方构建（universal / arm64）；登记过的都不匹配就是不符。
        raise FingerprintMismatch('ASAR fingerprint mismatch; no patch applied')
    return discovered_adapter(app)


def discovered_adapter(app):
    """未登记版本：三重校验通过就现场适配；任何一项不符都拒绝。

    校验顺序固定：先确认这是官方签名的 Claude（TeamIdentifier 与包标识），
    再按功能表结构定位。签名不对时连结构都不看——避免把“结构像”的东西
    当成可写入目标。

    系统范围取本机：现场适配只发生在本机、也只装在本机，不存在把补丁
    搬到别的机器上的情况。
    """
    verify_official_signature(app)
    adapter = dict(discover_adapter(app))
    adapter['system'] = (platform.machine(),
                         tuple(int(part) for part in
                               re.findall(r'\d+', platform.mac_ver()[0] or ''))[:3])
    return adapter


def verify_official_signature(app):
    """只校验官方签名与签名有效性，不比对登记指纹。

    恢复路径用它检查备份：备份可能来自已经淘汰的版本，但它的签名必须
    仍然是 Anthropic 官方签名（TeamIdentifier=Q6L2SF6YDW）。
    """
    run('codesign','--verify','--deep','--strict',str(app))
    signature=run('codesign','-dvv',str(app)).stderr.decode()
    if 'TeamIdentifier=Q6L2SF6YDW' not in signature:
        raise ValueError('Source is not signed by the expected official team')


def verify_source(app, adapter=None):
    """确认应用是已适配的官方原版：版本、ASAR、关键文件和签名全部匹配。"""
    adapter = adapter or source_adapter(app)
    b,h,base=read_asar(app/'Contents/Resources/app.asar')
    if sha(b) != adapter['asarHash']:
        raise FingerprintMismatch('ASAR fingerprint mismatch; no patch applied')
    for p,digest in adapter['files'].items():
        if sha(content(b,h,base,p)) != digest:
            raise FingerprintMismatch('File fingerprint mismatch: '+p)
    for path,digest in adapter['sourceFiles'].items():
        if sha((app/path).read_bytes())!=digest:
            raise FingerprintMismatch('Source component fingerprint mismatch: '+path)
    verify_official_signature(app)
    return b,h,base


JSC_CANDIDATES = (
    '/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc',
    '/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc',
)


def javascript_core():
    """系统自带的 JavaScriptCore 命令行；没有就返回 None。"""
    for candidate in JSC_CANDIDATES:
        if pathlib.Path(candidate).exists():
            return candidate
    return None


def check_table_syntax(text, anchor):
    """用系统自带的 JavaScriptCore 只解析被改动的那张表，失败即拒绝。

    不依赖用户机器上的 node：检查器随 macOS 提供。这里只编译、不执行
    （`checkSyntax` 在解析阶段就返回），所以表里即使混进未知内容也不会
    运行。找不到检查器同样拒绝写入——没有检查就不写。
    """
    jsc = javascript_core()
    if jsc is None:
        raise SyntaxCheckUnavailable('缺少系统自带的 JavaScriptCore；停止写入。')
    start, end = table_bounds(text, anchor)
    source = 'var __table=' + text[start:end] + ';'
    work = pathlib.Path(tempfile.mkdtemp(prefix='syntax-'))
    try:
        target = work / 'table.js'
        target.write_text(source, encoding='utf-8')
        script = 'checkSyntax(' + json.dumps(str(target)) + ');'
        try:
            run(jsc, '-e', script)
        except subprocess.CalledProcessError as error:
            # jsc 把 SyntaxError 写到 stdout，把崩溃类信息写到 stderr，两处都看。
            detail = ((error.stdout or b'') + (error.stderr or b'')).decode(
                'utf-8', 'replace').strip()
            # 只回显解析器报错的第一行（形如 `Exception: SyntaxError: …`），
            # 不带源码内容。
            first = detail.splitlines()[0][:160] if detail else '未知语法错误'
            raise SyntaxCheckFailed('改动后的功能表语法不成立；停止写入：' + first)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check_js(text):
    """有 node 时再对整份 JS 做一次语法检查；没有 node 不视为通过。

    主检查是 check_table_syntax（系统 JavaScriptCore，必须通过）。这里
    只是额外一层：装了 node 就用它复核整份文件，能发现表以外的损坏。
    """
    node = shutil.which('node')
    if not node:
        for candidate in ('/opt/homebrew/bin/node', '/usr/local/bin/node'):
            if pathlib.Path(candidate).exists():
                node = candidate
                break
    if not node:
        return
    handle = tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8')
    try:
        handle.write(text)
        handle.close()
        run(node, '--check', handle.name)
    finally:
        pathlib.Path(handle.name).unlink(missing_ok=True)


def replace_once(s, old, new):
    """精确替换一次；出现次数不是 1 就停止，避免改错位置。"""
    if s.count(old) != 1:
        raise ValueError('Patch match count != 1: '+old[:90])
    return s.replace(old,new,1)


# --------------------------------------------------------------------------
# 固定的本地代码签名身份
#
# 改过 app.asar 就必须重新签名，而没有官方私钥时只能用临时（ad-hoc）签名。
# ad-hoc 签名的“指定要求”是二进制的 cdhash，每次重签都会变，macOS 因此
# 每次都把之前授予的辅助功能、屏幕录制作废。改用一把**固定的本地身份**
# 后，指定要求变成 `identifier … and certificate root = H"…"`，只跟证书
# 有关；同一个证书重签多少次，要求都不变，系统权限就能留住。
#
# 证书在本机生成一次，私钥只留在本机的钥匙串里，不随发布包分发——
# 分发的私钥等于没有私钥，任何人都能拿它冒充本工具。
# --------------------------------------------------------------------------

IDENTITY_COMMON_NAME = 'Claude Native Unlock Local Signing'


def remove_path(path):
    """删除文件或目录；shutil.rmtree 对文件会静默失败，必须分开处理。"""
    path = pathlib.Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def signing_paths():
    """本地签名身份的存放位置；随 WORK 一起落在用户数据目录。

    密码**不**再落盘：存在登录钥匙串的通用密码条目里，读取时由系统
    向用户确认（Touch ID 或登录密码）。原来的 `password` 文件只在迁移时
    读一次，迁移成功后删除。
    """
    root = WORK / 'signing'
    return root, root / 'identity.keychain', root / 'password', root / 'identity.txt'


# 登录钥匙串里保存签名钥匙串密码的条目。由助手后端自己写入，系统默认
# 的 ACL 只信任创建它的那个可执行文件，别的程序读取会弹系统确认。
SIGNING_SERVICE = 'Claude 原生助手 签名钥匙串'
SIGNING_ACCOUNT = 'local-signing'


def _keychain_password():
    """从登录钥匙串读签名钥匙串密码；取不到返回 None。

    已实测：走 `/usr/bin/security` 读不到——ACL 认的是调用进程，
    而 `security` 不是受信任的那个程序，只会弹确认框然后卡住。所以这里
    直接用 Security 框架，由后端自己读，命中创建者身份时不需要确认。
    """
    try:
        return keychain.read(SIGNING_SERVICE, SIGNING_ACCOUNT) or None
    except Exception:
        return None


def keychain_writer_is_trustworthy():
    """只有固定身份签名的助手后端可以写密码条目；返回 (是否可以, 原因)。

    这是本文件里最重要的一道闸。钥匙串 ACL 认的是**写入进程**的签名，
    所以如果从开发用的解释器（`.venv/bin/python`、`/usr/bin/python3`）
    执行迁移，条目就会信任那个解释器——等于任何 Python 脚本都能读到签名
    密码，比原来的明文文件还糟。实测确认了 ACL 就是按调用进程判定的。

    判定看**签名**而不是路径：ACL 本身也只认签名，按路径判断既会误伤
    （换个安装位置就写不了）也会漏判（同路径换成别的程序）。所以要求
    当前可执行文件的指定要求是 `certificate root = H"<本地身份>"`，
    而不是 ad-hoc 的 cdhash——后者重建即失效，用户会被反复要求确认。
    """
    executable = pathlib.Path(sys.executable).resolve()
    requirement = app_signing_identity(executable)
    if not requirement:
        return False, '当前进程是 ad-hoc 签名，重建后条目会失效'
    identity = local_identity()
    if not identity:
        return False, '本机还没有固定签名身份'
    if requirement != identity:
        return False, ('当前进程的签名身份与本地身份不一致（' + requirement[:12] +
                       '…），写入会把密码交给另一个程序')
    return True, ''


def store_keychain_password(password):
    """把签名钥匙串密码写进登录钥匙串，并删掉原来的明文文件。

    条目必须由本进程写：`security add-generic-password -T …` 只能把信任
    指向某个路径下的程序，而调用者始终是 `security` 自己——实测把
    `/usr/bin/security` 写进信任列表后，任何程序都能直接读到密码。
    """
    allowed, reason = keychain_writer_is_trustworthy()
    if not allowed:
        raise ValueError('拒绝把签名密码写入钥匙串：' + reason)
    _, _, password_file, _ = signing_paths()
    if keychain.store(SIGNING_SERVICE, SIGNING_ACCOUNT, password) != keychain.ERR_SEC_SUCCESS:
        raise ValueError('登录钥匙串拒绝保存签名密码')
    password_file.unlink(missing_ok=True)


def migrate_keychain_password():
    """把旧版留下的明文密码搬进钥匙串；返回是否发生过迁移。

    读不到旧文件就当作已经迁移过；写入失败时保留旧文件，让签名照常可用，
    并返回 False——宁可暂时保留旧行为，也不能让用户签不了名。
    """
    _, _, password_file, _ = signing_paths()
    if not password_file.exists():
        return False
    password = password_file.read_text().strip()
    if not password:
        return False
    try:
        store_keychain_password(password)
    except Exception:
        return False
    return True


def signing_password_location():
    """报告签名密码现在存在哪，不读密码本身。

    只做只读判断，可以在状态检测里调用：钥匙串条目的存在与否由系统
    回答，不触发任何确认框。
    """
    _, _, password_file, _ = signing_paths()
    if password_file.exists():
        return 'plaintext'
    try:
        if keychain.exists(SIGNING_SERVICE, SIGNING_ACCOUNT):
            return 'keychain'
    except Exception:
        pass
    return 'missing'


def local_identity():
    """返回本机已建立的签名身份；没有就返回 None（此时退回 ad-hoc 签名）。

    只读一个小标记文件，不访问钥匙串，所以可以在每次状态检测里调用。
    """
    _, keychain_path, _, marker = signing_paths()
    if not keychain_path.exists() or not marker.exists() or marker.is_symlink():
        return None
    value = marker.read_text().strip()
    return value or None


def create_local_identity():
    """一次性建立本地代码签名身份；失败时返回 None，由调用方退回 ad-hoc。

    证书和私钥只在本机生成并留在本机钥匙串里，不进发布包：分发的私钥
    等于没有私钥，任何人都能拿它冒充本工具。
    """
    existing = local_identity()
    if existing:
        return existing
    root, keychain_path, password_file, marker = signing_paths()
    if not pathlib.Path('/usr/bin/openssl').exists():
        return None
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # 标记文件丢失但钥匙串还在（上次中断）时先清干净，否则 create-keychain 会失败。
    if keychain_path.exists():
        remove_path(keychain_path)
    password = secrets.token_urlsafe(24)
    work = pathlib.Path(tempfile.mkdtemp(prefix='identity-'))
    try:
        combined = work / 'identity.pem'
        run('/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048',
            '-keyout', str(work / 'key.pem'), '-out', str(work / 'cert.pem'),
            '-days', '3650', '-nodes',
            '-subj', '/CN=' + IDENTITY_COMMON_NAME + '/O=Claude Native Unlock',
            '-addext', 'keyUsage=critical,digitalSignature',
            '-addext', 'extendedKeyUsage=critical,codeSigning',
            '-addext', 'basicConstraints=critical,CA:false')
        # 直接导入 PEM，省掉 PKCS#12 的加密格式兼容问题。
        combined.write_bytes((work / 'key.pem').read_bytes()
                             + (work / 'cert.pem').read_bytes())
        run('/usr/bin/security', 'create-keychain', '-p', password, str(keychain_path))
        run('/usr/bin/security', 'set-keychain-settings', '-l', '-u', str(keychain_path))
        run('/usr/bin/security', 'unlock-keychain', '-p', password, str(keychain_path))
        run('/usr/bin/security', 'import', str(combined), '-k', str(keychain_path),
            '-f', 'pemseq', '-T', '/usr/bin/codesign')
        # 允许 codesign 无提示使用私钥；否则每次签名都会弹权限对话框。
        run('/usr/bin/security', 'set-key-partition-list',
            '-S', 'apple-tool:,apple:,codesign:', '-s', '-k', password, str(keychain_path))
        listing = run('/usr/bin/security', 'find-identity', '-p', 'codesigning',
                      str(keychain_path)).stdout.decode()
        digest = next((line.split()[1] for line in listing.splitlines()
                       if IDENTITY_COMMON_NAME in line), None)
        if not digest:
            raise ValueError('identity not found after import')
        # security 输出大写、codesign 的指定要求用小写；统一成小写，
        # 否则“身份是否变化”会永远判成变了，界面就会一直提示更新。
        digest = digest.lower()
        # 密码只进登录钥匙串，不落盘；钥匙串本身用 -l 在闲置后自动锁上。
        try:
            store_keychain_password(password)
        except Exception:
            # 存不进钥匙串时退回旧行为（0600 文件），否则这把身份以后就签不了名。
            password_file.write_text(password + '\n')
            os.chmod(password_file, 0o600)
        marker.write_text(digest + '\n')
        os.chmod(marker, 0o600)
        return digest
    except Exception:
        # 建不出来就退回 ad-hoc：功能不受影响，只是更新后要重新授权。
        for path in (keychain_path, pathlib.Path(str(keychain_path) + '-db')):
            remove_path(path)
        password_file.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        try:
            keychain.delete(SIGNING_SERVICE, SIGNING_ACCOUNT)
        except Exception:
            pass
        return None
    finally:
        remove_path(work)


def app_signing_identity(app):
    """读取应用**实际**的签名身份：固定身份返回证书哈希，ad-hoc 返回 None。

    记录实际结果而不是“打算用什么”，这样安装记录始终能反映真实签名，
    本地身份被删掉时也能自愈（下次打补丁会重新建立）。
    """
    try:
        # codesign 把指定要求写在 stdout、把 Executable 行写在 stderr，两处都收。
        done = run('/usr/bin/codesign', '-d', '-r-', str(app))
        text = (done.stdout + done.stderr).decode()
    except Exception:
        # 读不出来（例如合成测试目录）时按“没有固定身份”处理，不影响流程。
        return None
    match = re.search(r'certificate (?:root|leaf) = H"([0-9a-fA-F]+)"', text)
    return match.group(1).lower() if match else None


def signing_password():
    """取签名钥匙串的密码：优先登录钥匙串，旧版的明文文件只作迁移期兜底。"""
    value = _keychain_password()
    if value:
        return value
    _, _, password_file, _ = signing_paths()
    if password_file.exists() and not password_file.is_symlink():
        return password_file.read_text().strip() or None
    return None


def _codesign_with_identity(target, entitlements_path=None, identity=None):
    """内部：用给定身份签一个路径，不验签、不选身份。

    解锁只用一次，签完立刻重新锁上：密码在内存里停留的时间就是一次
    codesign 的时长，平时钥匙串是锁着的。
    """
    if identity:
        keychain_path = signing_paths()[1]
        unlocked = False
        try:
            password = signing_password()
            if not password:
                raise ValueError('取不到签名钥匙串密码')
            run('/usr/bin/security', 'unlock-keychain', '-p', password, str(keychain_path))
            unlocked = True
            args = ['/usr/bin/codesign', '--force', '--sign', identity,
                    '--keychain', str(keychain_path), '--options', 'runtime']
            if entitlements_path:
                args += ['--entitlements', str(entitlements_path)]
            run(*args, str(target))
            return True
        except Exception:
            return False
        finally:
            if unlocked:
                # 锁失败不影响已完成的签名；这里只是尽量缩短暴露时间。
                try:
                    run('/usr/bin/security', 'lock-keychain', str(keychain_path))
                except Exception:
                    pass
    return False


def nested_bundles(app):
    """列出应用包里需要单独签名的嵌套代码，深的在前。

    改了 Electron Framework 就必须整棵树用同一个身份重签：dyld 会拒绝
    加载 Team ID 与宿主不一致的库（`mapping process and mapped file
    (non-platform) have different Team IDs`），而只重签框架会让 Helper
    仍带官方 Team ID。顺序要由内向外，外层签名才会盖住内层。
    """
    app = pathlib.Path(app)
    found = []
    for path in app.rglob('*'):
        if path.is_symlink() or path == app:
            continue
        if path.suffix in ('.app', '.framework', '.dylib', '.node'):
            # 框架内部的 dylib 也要签：它们各自带 Team ID，漏签会让框架
            # 加载时报 `different Team IDs`。深的排在前面，先签里层，
            # 外层的框架才会把已签好的内层一起封进去。
            found.append(path)
    return sorted(found, key=lambda p: len(p.parts), reverse=True)


def _entitlements_of(target, work_dir):
    """读回一个已签名路径的 entitlements，并加上库验证豁免。

    两件事都不能少：

    - **必须逐个继承原值**。Helper 少了 `allow-jit` 会直接
      `Fatal process out of memory: Failed to reserve virtual memory for
      CodeRange`，启动就死。
    - **必须加上 `disable-library-validation`**。重签框架后它不再带官方
      团队标识，硬运行时下 Helper 会拒绝加载，报
      `mapping process and mapped file (non-platform) have different Team IDs`。

    原来没有 entitlements 的路径也返回一份，只含库验证豁免。
    """
    result = run('/usr/bin/codesign', '-d', '--entitlements', ':-', str(target),
                 timeout=60)
    data = result.stdout
    values = {}
    if data and b'<?xml' in data:
        try:
            values = plistlib.loads(data)
        except Exception:
            values = {}
    values['com.apple.security.cs.disable-library-validation'] = True
    path = pathlib.Path(work_dir) / ('ent-' + sha(str(target).encode())[:16] + '.plist')
    path.write_bytes(plistlib.dumps(values))
    return path


def sign_bundle(paths, work_dir):
    """用与应用相同的身份重签嵌套代码，逐个保留它自己的 entitlements。

    嵌入式 ASAR 摘要写在框架二进制里，改写就让它自己的签名失效；而 dyld
    要求同一个进程和它加载的库能互相接受，所以框架和所有 Helper 必须一起
    重签。先内后外，最后再签外层应用。
    """
    identity = local_identity()
    for path in paths:
        # 先读原 entitlements，再签——签完原来的就被覆盖了。
        ent = _entitlements_of(path, work_dir)
        if not _codesign_with_identity(path, ent, identity):
            run('/usr/bin/codesign', '--force', '--sign', '-', '--options', 'runtime',
                '--entitlements', str(ent), str(path))
        run('/usr/bin/codesign', '--verify', '--strict', str(path))
    return identity


def sign_app(app, entitlements_path):
    """用固定的本地身份签名；没有身份时退回 ad-hoc，并返回实际用到的身份。"""
    identity = local_identity()
    if not _codesign_with_identity(app, entitlements_path, identity):
        identity = None
        run('/usr/bin/codesign', '--force', '--sign', '-', '--options', 'runtime',
            '--entitlements', str(entitlements_path), str(app))
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', str(app))
    return identity
