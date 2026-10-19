#!/usr/bin/env python3
"""版本固定的官方应用补丁基础工具。

这里只负责三件事：
1. 识别 Claude 的版本和文件指纹；
2. 读写 Electron 的 app.asar；
3. 提供统一的校验、哈希和替换工具。

不在这里实现任何 Computer Use、浏览器或模型功能，也不修改 Claude 的
逐应用授权、系统权限、截图过滤、拒绝、中断或动作审查。
"""
import hashlib, json, pathlib, plistlib, struct, subprocess

# 源码仓库根目录。打包成独立助手后，backend.py 会把 WORK 指到用户数据目录，
# 避免把备份写进只读或会被替换的应用包。
ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = ROOT / 'work'
APP = WORK / 'Claude Native Unlock Test.app'
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

# 这些版本才有“新版 Code 工作台”相关界面；补丁 revision 5 起按版本判断。
CODE_UI_VERSIONS = (ADAPTER_37337['version'], ADAPTER_42304['version'],
                    ADAPTER_42304_2['version'])


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


def main_availability(adapter):
    """主进程功能表要追加的可用性条件。"""
    return adapter.get('mainAvailability', '')


def web_availability(adapter):
    """渲染进程功能表要追加的可用性条件。"""
    return adapter.get('webAvailability', '')


def source_adapter(app):
    """按版本和 ASAR 指纹选择适配器；未知版本直接拒绝。"""
    info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
    version=info['CFBundleShortVersionString']
    adapters=adapters_for_version(version)
    if not adapters:
        raise ValueError('Unknown version; no patch applied')
    if len(adapters) == 1:
        return adapters[0]
    # 同一个版本号可能对应两种官方构建：官网 universal DMG 和更新器 arm64。
    # 版本号相同，只能靠 ASAR 指纹区分，绝不能混用整包指纹。
    b,_,_=read_asar(app/'Contents/Resources/app.asar')
    digest=sha(b)
    for adapter in adapters:
        if digest == adapter['asarHash']:
            return adapter
    raise ValueError('ASAR fingerprint mismatch; no patch applied')


def verify_source(app):
    """确认应用是已适配的官方原版：版本、ASAR、关键文件和签名全部匹配。"""
    adapter=source_adapter(app)
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


def replace_once(s, old, new):
    """精确替换一次；出现次数不是 1 就停止，避免改错位置。"""
    if s.count(old) != 1:
        raise ValueError('Patch match count != 1: '+old[:90])
    return s.replace(old,new,1)
