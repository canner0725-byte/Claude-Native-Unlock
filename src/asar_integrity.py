"""Electron 嵌入式 ASAR 完整性摘要：定位、校验、重算与改写。

Claude 3.9683.0 起，Electron 把 `Info.plist` 里 `ElectronAsarIntegrity`
字典的摘要编进 **Electron Framework 二进制**的
`__DATA_CONST,__asar_integrity` 节，启动时用它校验那份登记值。
`Archive::Init()` 读不到或不匹配就直接 `LOG(FATAL)`：

    Failed to get integrity for validatable asar archive: Resources/app.asar

改完 `app.asar` 却不更新这个摘要，应用就起不来。3.6556.2 之所以一直好用，
是因为它那一版这个节还处在“未启用”状态。

节结构固定 66 字节：

    sentinel[32]  固定 ASCII 标记，用来确认位置没被换掉
    used          1 字节；0 = 未启用，校验直接放行
    version       1 字节；必须是 1，其它值 Electron 一律拒绝
    digest[32]    SHA-256

摘要算法（对 `ElectronAsarIntegrity` 字典）：

    按 key 用 NSLiteralSearch 排序，逐个拼接 key、algorithm、hash 的
    UTF-8 字节，**不加任何分隔符**，再整体取 SHA-256。

关键设计：**每一版都自证，不写死任何偏移或已知摘要。**

定位靠解析 Mach-O 找节名，不写死文件偏移；算法靠拿官方 Info.plist 现算
一遍、和二进制里原有的摘要比对来证明——一致才允许改写，不一致说明这一版
换了算法或换了节位置，直接拒绝，不盲写。这样 Electron 升级后要么自动
继续工作，要么明确停下，不会悄悄产出起不来的应用。
"""
import hashlib
import pathlib
import plistlib
import struct

# 官方构建里这个节的固定标记。出现在所有已知版本中，用来确认节没被换掉。
SENTINEL = b'AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A'
SECTION = b'__asar_integrity'
SUPPORTED_VERSION = 1
BLOB_SIZE = 32 + 1 + 1 + 32

# 只读节内容用到的字段在 66 字节内的位置。
USED_AT = 32
VERSION_AT = 33
DIGEST_AT = 34

# Mach-O 魔数：fat（大端表）与 thin（按小端解析）。
FAT_MAGICS = {0xcafebabe, 0xcafebabf}
THIN_MAGICS = {0xfeedfacf, 0xfeedface}
LC_SEGMENT_64 = 0x19


def digest_of(integrity):
    """按 Electron 的算法算出 `ElectronAsarIntegrity` 字典的摘要。

    排序必须跟 `NSLiteralSearch` 一致：那是 **UTF-16 码元序**，既不是
    Unicode 码点序也不是 UTF-8 字节序。三者只在基本多文种平面之外才会
    分叉（例如 U+10000 在 UTF-16 里是代理对 D800 DC00，排在 U+FFFF 之前），
    而 ASAR 的键是包内路径，几乎不会出现这种字符——但差一个字节摘要就
    全错，所以照原样实现，不图省事。

    拼接部分：逐项把 key、algorithm、hash 的 UTF-8 字节接上去，
    不加分隔符、不加长度前缀。
    """
    h = hashlib.sha256()
    for key in sorted(integrity, key=lambda k: k.encode('utf-16-be')):
        entry = integrity[key]
        h.update(key.encode('utf-8'))
        h.update(entry['algorithm'].encode('utf-8'))
        h.update(entry['hash'].encode('utf-8'))
    return h.digest()


def plist_integrity(app):
    """读出应用 Info.plist 里的 `ElectronAsarIntegrity`；没有就返回空字典。"""
    info = plistlib.loads((pathlib.Path(app) / 'Contents/Info.plist').read_bytes())
    return info.get('ElectronAsarIntegrity') or {}


def _macho_offset(handle):
    """判断文件是不是 Mach-O，是则返回 (各架构切片偏移, 是否 fat)。"""
    handle.seek(0)
    head = handle.read(8)
    if len(head) < 8:
        return [], False
    big = struct.unpack('>I', head[:4])[0]
    small = struct.unpack('<I', head[:4])[0]
    if big in FAT_MAGICS:
        wide = big == 0xcafebabf
        count = struct.unpack('>I', head[4:8])[0]
        if count > 16:
            return [], True
        size = 32 if wide else 20
        table = handle.read(count * size)
        offsets = []
        for i in range(count):
            at = i * size
            if wide:
                offsets.append(struct.unpack('>Q', table[at + 8:at + 16])[0])
            else:
                offsets.append(struct.unpack('>I', table[at + 8:at + 12])[0])
        return offsets, True
    if big in THIN_MAGICS or small in THIN_MAGICS:
        return [0], False
    return [], False


def _sections(handle, base):
    """在一个切片里找出 `__asar_integrity` 节的内容偏移。

    只读文件头和加载命令区，不读整个二进制——Electron Framework 有
    400 多 MB，全读会拖慢状态检测。
    """
    handle.seek(base)
    header = handle.read(32)
    if len(header) < 32:
        return []
    thin = struct.unpack('<I', header[:4])[0]
    if thin not in THIN_MAGICS:
        return []
    start = 32 if thin == 0xfeedfacf else 28
    count = struct.unpack('<I', header[16:20])[0]
    sizeofcmds = struct.unpack('<I', header[20:24])[0]
    if sizeofcmds > 1 << 24:
        return []
    handle.seek(base + start)
    cmds = handle.read(sizeofcmds)
    found = []
    at = 0
    for _ in range(count):
        if at + 8 > len(cmds):
            break
        cmd, size = struct.unpack('<II', cmds[at:at + 8])
        if size < 8:
            break
        if cmd == LC_SEGMENT_64 and at + 72 <= len(cmds):
            sections = struct.unpack('<I', cmds[at + 64:at + 68])[0]
            walk = at + 72
            for _ in range(sections):
                if walk + 80 > len(cmds):
                    break
                if cmds[walk:walk + 16].rstrip(b'\0') == SECTION:
                    offset = struct.unpack('<I', cmds[walk + 48:walk + 52])[0]
                    found.append(base + offset)
                walk += 80
        at += size
    return found


def locate(app):
    """在应用包里找出所有 `__asar_integrity` 节。

    返回 [(相对路径, 节在文件内的偏移)]。同一份二进制通常有 arm64 与
    x86_64 两个切片，各有一份、内容相同。
    """
    app = pathlib.Path(app)
    hits = []
    for path in sorted((app / 'Contents').rglob('*')):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open('rb') as handle:
                offsets, _ = _macho_offset(handle)
                if not offsets:
                    continue
                for base in offsets:
                    for offset in _sections(handle, base):
                        hits.append((str(path.relative_to(app)), offset))
        except OSError:
            continue
    return hits


def read(app, hits=None):
    """读出所有节的当前内容，供改写前判断与安装后核对。

    返回 [{'path','offset','sentinel','used','version','digest'}]。
    `sentinel` 表示标记匹配——不符说明这不是我们认识的节，绝不可写。
    """
    app = pathlib.Path(app)
    slots = []
    for path, offset in (hits if hits is not None else locate(app)):
        try:
            with (app / path).open('rb') as handle:
                handle.seek(offset)
                blob = handle.read(BLOB_SIZE)
        except OSError:
            continue
        if len(blob) < BLOB_SIZE:
            continue
        slots.append({
            'path': path,
            'offset': offset,
            'sentinel': blob[:32] == SENTINEL,
            'used': blob[USED_AT],
            'version': blob[VERSION_AT],
            'digest': blob[DIGEST_AT:],
        })
    return slots


def enforcement(app, integrity):
    """判断这一版的完整性校验是否启用，并在启用时自证算法。

    `integrity` 必须是**官方原版** Info.plist 里的 `ElectronAsarIntegrity`。
    做法：拿它现算一遍摘要，和二进制里已写的那份比。

    - 一致 → 算法确认，返回需要改写的节。
    - `used == 0` → 未启用，返回空列表（3.6556.2 就是这种）。
    - 不一致 → 这一版算法或节位置变了，抛错拒绝，绝不盲写。
    """
    slots = read(app)
    active = [s for s in slots if s['used'] != 0]
    if not active:
        # 没有这个节，或所有节都是“未启用”：这一版不做嵌入式校验。
        return []
    for slot in active:
        if not slot['sentinel']:
            raise ValueError('完整性节的标记不符；停止修改：' + slot['path'])
        if slot['version'] != SUPPORTED_VERSION:
            raise ValueError(
                '完整性摘要版本不受支持（' + str(slot['version'])
                + '）；停止修改：' + slot['path'])
        if digest_of(integrity) != slot['digest']:
            raise ValueError(
                '这一版的完整性摘要算法与已知不符，无法安全重算；'
                '停止修改，不会写入：' + slot['path'])
    return active


def rewrite(app, integrity, slots):
    """把重算后的摘要写回节里。返回写入记录，供安装后核对。

    只改 digest 那 32 字节，哨兵、used、version 保持官方原值。
    写完必须重签 Electron Framework，再重签外层应用，否则签名失效。
    """
    app = pathlib.Path(app)
    digest = digest_of(integrity)
    written = {}
    for slot in slots:
        target = app / slot['path']
        with target.open('r+b') as handle:
            handle.seek(slot['offset'])
            if handle.read(32) != SENTINEL:
                raise ValueError('完整性节在写入前发生变化；停止修改：' + slot['path'])
            handle.seek(slot['offset'] + DIGEST_AT)
            handle.write(digest)
        written[slot['path']] = written.get(slot['path'], 0) + 1
    return {'digest': digest.hex(), 'files': written}


def verify(app, record):
    """核对节里确实是我们写的那份摘要。

    记录里只存摘要和文件，**不存偏移**：重签之后代码签名改写会让切片
    整体前后移动（实测 x86_64 切片移了 16384 字节），存下来的绝对值就
    失效了。这里每次重新解析 Mach-O 定位，代价只有几十毫秒。
    """
    if not record:
        return
    app = pathlib.Path(app)
    expected = bytes.fromhex(record['digest'])
    wanted = record.get('files') or {}
    found = {}
    for slot in read(app):
        if not slot['sentinel']:
            raise ValueError('完整性节的标记不符；停止操作：' + slot['path'])
        found[slot['path']] = found.get(slot['path'], 0) + 1
        if slot['digest'] != expected:
            raise ValueError('完整性摘要已被改动；停止操作：' + slot['path'])
    if found != wanted:
        raise ValueError('完整性节的分布与安装记录不一致；停止操作。')
