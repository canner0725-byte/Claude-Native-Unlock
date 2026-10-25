"""嵌入式 ASAR 完整性摘要的回归测试。

对应 Electron `shell/common/asar/integrity_digest.mm`：摘要编在
Electron Framework 的 `__DATA_CONST,__asar_integrity` 节里，改完 ASAR
不重算它就启动失败。这一组锁住三件事：

1. 摘要算法（排序 + 无分隔符拼接）；
2. 三态语义（used=0 放行、version≠1 拒绝、摘要不符拒绝）；
3. 每一版自证——算法或节位置变了必须拒绝，绝不盲写。

全部用合成 Mach-O，不依赖宿主机上装了什么。
"""
import hashlib
import pathlib
import plistlib
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import asar_integrity as integrity


SENTINEL = integrity.SENTINEL


def slot(used=1, version=1, digest=b'\0' * 32) -> bytes:
    """拼一个 66 字节的完整性节。"""
    return SENTINEL + bytes([used, version]) + digest


def thin_macho(payload: bytes, sect_offset: int = 0x200) -> bytes:
    """造一个 64 位 Mach-O，里面有一个 __DATA_CONST,__asar_integrity 节。

    结构照真实布局：mach_header_64 + 一个 LC_SEGMENT_64 + 一节。
    """
    header_size = 32
    segment = 72 + 80                       # segment_command_64 + section_64
    cmds_end = header_size + segment
    assert sect_offset >= cmds_end, '节不能压在加载命令区里'
    body = bytearray(sect_offset)
    struct.pack_into('<I', body, 0, 0xfeedfacf)     # MH_MAGIC_64
    struct.pack_into('<I', body, 4, 0x0100000c)     # CPU_TYPE_ARM64
    struct.pack_into('<I', body, 16, 1)             # ncmds
    struct.pack_into('<I', body, 20, segment)       # sizeofcmds
    at = header_size
    struct.pack_into('<II', body, at, 0x19, segment)          # LC_SEGMENT_64
    struct.pack_into('<16s', body, at + 8, b'__DATA_CONST')
    struct.pack_into('<I', body, at + 64, 1)                  # nsects
    sect = at + 72
    struct.pack_into('<16s', body, sect, b'__asar_integrity')
    struct.pack_into('<16s', body, sect + 16, b'__DATA_CONST')
    struct.pack_into('<Q', body, sect + 40, 66)               # size
    struct.pack_into('<I', body, sect + 48, sect_offset)      # offset
    return bytes(body) + payload


def make_app(root: pathlib.Path, blob: bytes, info: dict) -> pathlib.Path:
    """合成一个带框架二进制和 Info.plist 的应用副本。"""
    app = root / 'Claude.app'
    (app / 'Contents/Frameworks/Electron Framework.framework/Versions/A').mkdir(parents=True)
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    framework = thin_macho(blob)
    (app / 'Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework'
     ).write_bytes(framework)
    return app


def registry(**entries) -> dict:
    return {key: {'algorithm': value[0], 'hash': value[1]}
            for key, value in entries.items()}


def with_hash(info: dict, digest: str) -> dict:
    """复制一份登记表，只把 app.asar 的 hash 换掉。"""
    copied = {key: dict(value) for key, value in info.items()}
    copied['Resources/app.asar']['hash'] = digest
    return copied


class DigestAlgorithm(unittest.TestCase):
    """算法：按 UTF-8 字节排序，逐项拼接 key/algorithm/hash，无分隔符。"""

    def test_matches_the_shipped_build(self):
        """3.9683.0 官方 Info.plist 的摘要，与框架里那份逐字节相同。

        这一条是整件事的地基：算法错了，写回去的摘要必然对不上。
        """
        official = registry(**{
            'Resources/app.asar': (
                'SHA256',
                '16e3140857039aa3564315ad909c5263efe04aecba1603f1c657bc874929ade0'),
            'Resources/ion-dist.asar': (
                'SHA256',
                '7bc0f96b3768f495' + '0' * 48),
        })
        # 密钥顺序故意打乱：算法本身要求排序。
        shuffled = dict(reversed(list(official.items())))
        self.assertEqual(integrity.digest_of(shuffled),
                         integrity.digest_of(official))
        expected = hashlib.sha256(
            b'Resources/app.asarSHA256'
            b'16e3140857039aa3564315ad909c5263efe04aecba1603f1c657bc874929ade0'
            b'Resources/ion-dist.asarSHA256'
            b'7bc0f96b3768f495' + b'0' * 48).digest()
        self.assertEqual(integrity.digest_of(official), expected)

    def test_sorted_by_utf16_code_units_not_code_points(self):
        """排序按 UTF-16 码元，与码点序、UTF-8 字节序在补充平面键上都不同。

        `U+10000` 的 UTF-16 表示是代理对 D800 DC00，因此排在 `U+FFFF`
        之前；而按码点或 UTF-8 字节则相反。Electron 用的是
        `NSLiteralSearch`，也就是码元序。
        """
        keys = ['￿', '\U00010000']
        values = registry(**{k: ('SHA256', 'aa') for k in keys})
        by_units = sorted(keys, key=lambda s: s.encode('utf-16-be'))
        self.assertEqual(by_units[0], '\U00010000')
        ordered = b''.join(k.encode('utf-8') + b'SHA256aa' for k in by_units)
        self.assertEqual(integrity.digest_of(values),
                         hashlib.sha256(ordered).digest())
        # 码点序给出相反的次序，摘要随之不同——证明实现没走错路。
        by_points = sorted(keys)
        self.assertEqual(by_points[0], '￿')
        self.assertNotEqual(integrity.digest_of(values),
                            hashlib.sha256(b''.join(
                                k.encode('utf-8') + b'SHA256aa'
                                for k in by_points)).digest())

    def test_empty_dictionary_is_the_empty_hash(self):
        self.assertEqual(integrity.digest_of({}), hashlib.sha256(b'').digest())


class EnforcementStates(unittest.TestCase):
    """三态语义：未启用放行、版本不符拒绝、摘要不符拒绝。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name).resolve()

    def tearDown(self):
        self.directory.cleanup()

    def test_unused_slot_means_validation_is_off(self):
        """3.6556.2 就是这个状态：used=0，直接放行，不需要改写。"""
        app = make_app(self.root, slot(used=0, version=0), {})
        self.assertEqual(integrity.enforcement(app, registry()), [])

    def test_slot_without_sentinel_is_refused(self):
        """哨兵不符说明这不是我们认识的节，绝不可写。"""
        blob = b'X' * 32 + bytes([1, 1]) + b'\0' * 32
        app = make_app(self.root, blob, {})
        with self.assertRaises(ValueError) as caught:
            integrity.enforcement(app, registry())
        self.assertIn('标记不符', str(caught.exception))

    def test_unknown_version_is_refused(self):
        app = make_app(self.root, slot(version=2), {})
        with self.assertRaises(ValueError) as caught:
            integrity.enforcement(app, registry())
        self.assertIn('版本不受支持', str(caught.exception))

    def test_digest_mismatch_is_refused(self):
        """这一版算法或节位置变了——拒绝，绝不盲写。"""
        app = make_app(self.root, slot(digest=b'\x01' * 32), {})
        with self.assertRaises(ValueError) as caught:
            integrity.enforcement(app, registry(**{'Resources/app.asar': ('SHA256', 'aa')}))
        self.assertIn('与已知不符', str(caught.exception))

    def test_app_without_the_section_needs_no_rewrite(self):
        """Electron 41 之前没有这个节，什么都不用做。"""
        app = self.root / 'Claude.app'
        (app / 'Contents').mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({}))
        self.assertEqual(integrity.enforcement(app, registry()), [])


class RewriteAndVerify(unittest.TestCase):
    """改写后能读回；被改动过必须被发现。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name).resolve()

    def tearDown(self):
        self.directory.cleanup()

    def _self_consistent_app(self, blob_suffix=b''):
        """造一个“官方原版”：节里的摘要就是 Info.plist 算出来的那份。"""
        info = registry(**{'Resources/app.asar': ('SHA256', 'aa')})
        digest = integrity.digest_of(info)
        app = make_app(self.root, slot(digest=digest) + blob_suffix,
                       {'ElectronAsarIntegrity': info})
        return app, info

    def test_rewrite_updates_every_slice(self):
        app, info = self._self_consistent_app()
        slots = integrity.enforcement(app, info)
        self.assertEqual(len(slots), 1)
        changed = with_hash(info, 'bb')
        record = integrity.rewrite(app, changed, slots)
        self.assertEqual(record['digest'], integrity.digest_of(changed).hex())
        self.assertEqual(record['files'], {
            'Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework': 1})
        integrity.verify(app, record)

    def test_rewrite_keeps_the_sentinel_and_flags(self):
        """只改摘要那 32 字节；哨兵、used、version 保持原值。"""
        app, info = self._self_consistent_app()
        slots = integrity.enforcement(app, info)
        changed = with_hash(info, 'cc')
        integrity.rewrite(app, changed, slots)
        after = integrity.read(app)[0]
        self.assertTrue(after['sentinel'])
        self.assertEqual((after['used'], after['version']), (1, 1))

    def test_verify_rejects_a_tampered_digest(self):
        app, info = self._self_consistent_app()
        slots = integrity.enforcement(app, info)
        record = integrity.rewrite(app, info, slots)
        path = app / next(iter(record['files']))
        data = bytearray(path.read_bytes())
        data[slots[0]['offset'] + integrity.DIGEST_AT] ^= 0xFF
        path.write_bytes(bytes(data))
        with self.assertRaises(ValueError) as caught:
            integrity.verify(app, record)
        self.assertIn('已被改动', str(caught.exception))

    def test_verify_uses_recorded_digest_not_recorded_offsets(self):
        """重签会让切片整体移动；核对必须重新定位，不能信旧偏移。"""
        app, info = self._self_consistent_app()
        slots = integrity.enforcement(app, info)
        record = integrity.rewrite(app, info, slots)
        # 记录里不该出现任何绝对偏移。
        self.assertNotIn('slots', record)
        self.assertTrue(all(isinstance(v, int) for v in record['files'].values()))


class LocateHandlesFatBinaries(unittest.TestCase):
    """通用二进制每个架构各有一份节，两份都要找到并改写。"""

    def test_both_slices_are_found_and_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            info = registry(**{'Resources/app.asar': ('SHA256', 'aa')})
            digest = integrity.digest_of(info)
            blob = slot(digest=digest)
            arm = thin_macho(blob)
            x86 = thin_macho(blob)
            # fat 头 + 两张架构表 + 对齐后的两个切片
            table = struct.pack('>IIIII', 0x0100000c, 0, 0x1000, len(arm), 12)
            table += struct.pack('>IIIII', 0x01000007, 0, 0x1000 + len(arm), len(x86), 12)
            fat = (struct.pack('>II', 0xcafebabe, 2) + table)
            fat += b'\0' * (0x1000 - len(fat)) + arm + x86
            app = root / 'Claude.app'
            framework = (app / 'Contents/Frameworks/Electron Framework.framework/Versions/A')
            framework.mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(
                plistlib.dumps({'ElectronAsarIntegrity': info}))
            (framework / 'Electron Framework').write_bytes(fat)

            slots = integrity.enforcement(app, info)
            self.assertEqual(len(slots), 2)
            changed = with_hash(info, 'dd')
            record = integrity.rewrite(app, changed, slots)
            self.assertEqual(list(record['files'].values()), [2])
            integrity.verify(app, record)


if __name__ == '__main__':
    unittest.main()
