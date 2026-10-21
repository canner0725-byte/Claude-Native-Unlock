#!/usr/bin/env python3
"""版本固定的官方应用补丁基础工具。

这里只负责三件事：
1. 识别 Claude 的版本和文件指纹；
2. 读写 Electron 的 app.asar；
3. 提供统一的校验、哈希和替换工具。

不在这里实现任何 Computer Use、浏览器或模型功能，也不修改 Claude 的
逐应用授权、系统权限、截图过滤、拒绝、中断或动作审查。
"""
import hashlib, json, os, pathlib, plistlib, re, secrets, shutil, struct, subprocess, tempfile

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


def run(*args):
    """运行系统命令；失败时抛出异常，不吞掉错误。"""
    return subprocess.run(args, check=True, capture_output=True)


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
# - 1235690166 / 3335190042 / 3858164079 已从整个包里消失，不再追加；
# - 2120747855（提示建议设置项）仍然缺失，继续强制。
ADAPTER_31623 = {
 'version': '3.1623.0',
 'asarHash': '760e7cbb6f861f0bc0e71e71fd9c6b721ba735f0f638b6ea623645daff4261a7',
 'main': '.vite/build/index.chunk-BLvpT1n4.js',
 'featureAnchor': 'var ken={',
 'mainAvailability': '2120747855:{rule:"forced",value:!0},',
 'webFeatureAnchor': 'Aen={',
 'webAvailability': '',
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

# 这些版本才有“新版 Code 工作台”相关界面；补丁 revision 5 起按版本判断。
CODE_UI_VERSIONS = (ADAPTER_37337['version'], ADAPTER_42304['version'],
                    ADAPTER_42304_2['version'], ADAPTER_31623['version'])


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


def insert_entries(text, anchor, entries, report=None):
    """把可用性条目写进功能表，返回 (新文本, 实际结果)。

    三种情况分开处理：

    - 表里没有这个键：插到表头。
    - 表里已有完全相同的定义：跳过，什么都不做。
    - 表里已有不同定义：追加到**表尾**。对象字面量里后出现的定义获胜，
      所以追加在末尾能覆盖官方值；如果放在表头反而会被官方值覆盖。

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
    """未登记版本：按官方身份和功能表结构现场生成适配器。

    这是给“官方又升级了”准备的兜底：只读结构，不猜语义。调用方仍然要先
    通过官方签名校验；表找不到、表不唯一、或目标开关的现有形状和我们要写
    的不一致，都会抛异常，不会修改应用。
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
    adapter = {
        'version': info.get('CFBundleShortVersionString'),
        'asarHash': digest,
        'main': found[0],
        'featureAnchor': forced_anchor,
        'webFeatureAnchor': always_anchor,
        # 用和登记版本同一组条件；insert_entries 会跳过官方已强制成同样
        # 形状的项，遇到形状不同则拒绝，不覆盖官方语义。
        'mainAvailability': ('2371478310:{rule:"forced",value:!0},'
                             '857846362:{rule:"forced",value:!0},'
                             '2120747855:{rule:"forced",value:!0},'),
        'webAvailability': '',
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
    """按版本和 ASAR 指纹选择适配器。

    已登记版本走固定指纹；未登记的新版本按官方身份和功能表结构现场识别，
    这样官方再升级也不用等人改代码。识别不出来一律拒绝。
    """
    info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
    version=info['CFBundleShortVersionString']
    adapters=adapters_for_version(version)
    if not adapters:
        try:
            return discover_adapter(app)
        except ValueError as error:
            if 'Not the expected application' in str(error):
                raise ValueError('Unknown version; no patch applied')
            raise
    if len(adapters) == 1:
        # 只有一种已登记构建：仍要核对整包指纹。官方可能不升版本号就换构建，
        # 此时按结构重新识别，而不是套用旧锚点。ASAR 读不出来就直接拒绝。
        try:
            b,_,_=read_asar(app/'Contents/Resources/app.asar')
        except Exception:
            raise ValueError('ASAR fingerprint mismatch; no patch applied')
        if sha(b) == adapters[0]['asarHash']:
            return adapters[0]
        return _rediscover_or_refuse(app, 'ASAR fingerprint mismatch; no patch applied')
    # 同一个版本号可能对应两种官方构建：官网 universal DMG 和更新器 arm64。
    # 版本号相同，只能靠 ASAR 指纹区分，绝不能混用整包指纹。
    try:
        b,_,_=read_asar(app/'Contents/Resources/app.asar')
    except Exception:
        raise ValueError('ASAR fingerprint mismatch; no patch applied')
    digest=sha(b)
    for adapter in adapters:
        if digest == adapter['asarHash']:
            return adapter
    return _rediscover_or_refuse(app, 'ASAR fingerprint mismatch; no patch applied')


def _rediscover_or_refuse(app, message):
    """指纹对不上时尝试按结构重新识别；任何失败都回落到原来的拒绝。"""
    try:
        return discover_adapter(app)
    except Exception:
        raise ValueError(message)


def verify_source(app, adapter=None):
    """确认应用是已适配的官方原版：版本、ASAR、关键文件和签名全部匹配。"""
    adapter = adapter or source_adapter(app)
    b,h,base=read_asar(app/'Contents/Resources/app.asar')
    if sha(b) != adapter['asarHash']:
        raise ValueError('ASAR fingerprint mismatch; no patch applied')
    for p,digest in adapter['files'].items():
        if sha(content(b,h,base,p)) != digest:
            raise ValueError('File fingerprint mismatch: '+p)
    for path,digest in adapter['sourceFiles'].items():
        if sha((app/path).read_bytes())!=digest:
            raise ValueError('Source component fingerprint mismatch: '+path)
    run('codesign','--verify','--deep','--strict',str(app))
    signature=run('codesign','-dvv',str(app)).stderr.decode()
    if 'TeamIdentifier=Q6L2SF6YDW' not in signature:
        raise ValueError('Source is not signed by the expected official team')
    return b,h,base


def check_js(text):
    """本机有 node 时对改过的 JS 做一次语法检查，没有就跳过。

    这是给“未登记版本自动适配”加的一道保险：语法不成立就绝不安裝。
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
    """本地签名身份的存放位置；随 WORK 一起落在用户数据目录。"""
    root = WORK / 'signing'
    return root, root / 'identity.keychain', root / 'password', root / 'identity.txt'


def local_identity():
    """返回本机已建立的签名身份；没有就返回 None（此时退回 ad-hoc 签名）。

    只读一个小标记文件，不访问钥匙串，所以可以在每次状态检测里调用。
    """
    _, keychain, _, marker = signing_paths()
    if not keychain.exists() or not marker.exists() or marker.is_symlink():
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
    root, keychain, password_file, marker = signing_paths()
    if not pathlib.Path('/usr/bin/openssl').exists():
        return None
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # 标记文件丢失但钥匙串还在（上次中断）时先清干净，否则 create-keychain 会失败。
    if keychain.exists():
        remove_path(keychain)
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
        run('/usr/bin/security', 'create-keychain', '-p', password, str(keychain))
        run('/usr/bin/security', 'set-keychain-settings', '-l', '-u', str(keychain))
        run('/usr/bin/security', 'unlock-keychain', '-p', password, str(keychain))
        run('/usr/bin/security', 'import', str(combined), '-k', str(keychain),
            '-f', 'pemseq', '-T', '/usr/bin/codesign')
        # 允许 codesign 无提示使用私钥；否则每次签名都会弹权限对话框。
        run('/usr/bin/security', 'set-key-partition-list',
            '-S', 'apple-tool:,apple:,codesign:', '-s', '-k', password, str(keychain))
        listing = run('/usr/bin/security', 'find-identity', '-p', 'codesigning',
                      str(keychain)).stdout.decode()
        digest = next((line.split()[1] for line in listing.splitlines()
                       if IDENTITY_COMMON_NAME in line), None)
        if not digest:
            raise ValueError('identity not found after import')
        # security 输出大写、codesign 的指定要求用小写；统一成小写，
        # 否则“身份是否变化”会永远判成变了，界面就会一直提示更新。
        digest = digest.lower()
        password_file.write_text(password + '\n')
        os.chmod(password_file, 0o600)
        marker.write_text(digest + '\n')
        os.chmod(marker, 0o600)
        return digest
    except Exception:
        # 建不出来就退回 ad-hoc：功能不受影响，只是更新后要重新授权。
        for path in (keychain, pathlib.Path(str(keychain) + '-db')):
            remove_path(path)
        password_file.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
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


def sign_app(app, entitlements_path):
    """用固定的本地身份签名；没有身份时退回 ad-hoc，并返回实际用到的身份。"""
    identity = local_identity()
    if identity:
        try:
            _, keychain, password_file, _ = signing_paths()
            run('/usr/bin/security', 'unlock-keychain', '-p',
                password_file.read_text().strip(), str(keychain))
            run('/usr/bin/codesign', '--force', '--sign', identity,
                '--keychain', str(keychain), '--options', 'runtime',
                '--entitlements', str(entitlements_path), str(app))
        except Exception:
            identity = None
    if not identity:
        run('/usr/bin/codesign', '--force', '--sign', '-', '--options', 'runtime',
            '--entitlements', str(entitlements_path), str(app))
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', str(app))
    return identity
