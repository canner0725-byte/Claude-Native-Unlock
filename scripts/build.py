#!/usr/bin/env python3
"""Build a self-contained arm64 macOS application and distributable archives."""
import hashlib
import importlib.metadata
import os
import pathlib
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.0.0'
BUILD_NUMBER = '62'
APP_NAME = 'Claude 原生助手'
# 文件名保持 ASCII：非 ASCII 的发布文件名会被 GitHub 换成“.”，下载链接很难看。
ARCHIVE_NAME = 'Claude-Native-Assistant'


def run(*args):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def sign(app):
    """用固定的本地身份签助手；没有身份就退回 ad-hoc。

    这不只是省一次系统授权：登录钥匙串里的签名密码条目，ACL 认的是
    **创建条目那个二进制**的指定要求。ad-hoc 的要求是 cdhash，重新构建
    就变，用户每次更新助手都会被要求重新确认；固定身份的要求只跟证书
    有关，重建多少次都不变。

    私钥只在本机钥匙串里，构建脚本从不出售或导出它。
    """
    sys.path.insert(0, str(ROOT / 'src'))
    # 必须走真实入口：直接 import 拿到的是仓库内的 work/，而签名身份在
    # 用户数据目录里。漏了这一步会静默退回 ad-hoc，而且构建看起来是成功的。
    import backend
    backend.configure()
    import native_unlock as core
    identity = core.local_identity()
    if identity:
        # entitlements 是中间产物，写临时目录：仓库里不能留 work/
        # （有测试守着这一点，因为那里以前泄漏过真实的签名身份）。
        import tempfile
        with tempfile.TemporaryDirectory(prefix='cnu-sign-') as work:
            entitlements = pathlib.Path(work) / 'assistant-entitlements.plist'
            entitlements.write_bytes(plistlib.dumps({
                'com.apple.security.cs.allow-jit': True,
                'com.apple.security.cs.disable-library-validation': True,
            }))
            try:
                used = core.sign_app(app, entitlements)
            except Exception:
                used = None
        if used:
            print(f'已用本地身份签名：{used}')
            return used
        print('本地身份签名失败，退回 ad-hoc', file=sys.stderr)
    else:
        print('未建立本地签名身份，助手使用 ad-hoc 签名', file=sys.stderr)
    run('/usr/bin/codesign', '--force', '--sign', '-', app)
    return None


def prune_artifacts():
    """发布后只保留当前版本的安装包和应用，构建中间产物全部清理。

    安装包按“文件名里没有当前版本号”判断，应用按“不是刚构建的那个”判断，
    这样换产品名、换版本号都不会把旧文件留在发布目录里。
    """
    dist = ROOT / 'dist'
    keep = dist / f'{APP_NAME}.app'
    for path in dist.iterdir():
        if (path.is_file() and path.suffix in ('.dmg', '.zip')
                and (f'-{VERSION}-' not in path.name
                     or not path.name.startswith(ARCHIVE_NAME))):
            path.unlink()
        elif path.is_dir() and path.suffix == '.app' and path != keep:
            shutil.rmtree(path)
    build_dir = ROOT / 'build'
    for path in build_dir.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def build():
    if sys.platform != 'darwin' or os.uname().machine != 'arm64':
        raise SystemExit('当前仅构建已验证的 Apple Silicon 版本。')
    import PyInstaller  # Build-time dependency only.
    work = ROOT / 'build' / str(time.time_ns())
    work.mkdir(parents=True)
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    app = dist / f'{APP_NAME}.app'
    if app.exists():
        app.rename(work / 'previous.app')
    run(sys.executable, '-m', 'PyInstaller', '--onedir', '--name', 'NativeUnlockBackend',
        '--noupx', '--target-arch', 'arm64', '--paths', ROOT/'src',
        '--distpath', work/'frozen', '--workpath', work/'pyinstaller',
        '--specpath', work, ROOT/'src/backend.py')
    contents = app / 'Contents'
    macos = contents / 'MacOS'
    resources = contents / 'Resources'
    macos.mkdir(parents=True)
    resources.mkdir()
    shutil.copytree(work/'frozen/NativeUnlockBackend', resources/'Backend', symlinks=True)
    run('/usr/bin/swiftc', '-O', '-target', 'arm64-apple-macos15.0', ROOT/'app/NativeUnlock.swift', '-o', macos/'ClaudeNativeUnlock')
    run('/usr/bin/swiftc', ROOT/'app/GenerateIcon.swift', '-o', work/'GenerateIcon')
    run(work/'GenerateIcon', work/'AppIcon.iconset')
    run('/usr/bin/iconutil', '-c', 'icns', work/'AppIcon.iconset', '-o', resources/'AppIcon.icns')
    shutil.copy2(ROOT/'THIRD_PARTY_NOTICES.md', resources/'THIRD_PARTY_NOTICES.md')
    shutil.copy2(ROOT/'LICENSE', resources/'LICENSE')
    # Keep the redistributable runtime's license with the product.
    licenses = resources/'ThirdPartyLicenses'
    licenses.mkdir()
    distribution = importlib.metadata.distribution('PyInstaller')
    for entry in distribution.files or []:
        if str(entry).endswith(('COPYING.txt', 'COPYING', 'LICENSE.txt')):
            candidate = distribution.locate_file(entry)
            if candidate.is_file():
                shutil.copy2(candidate, licenses/('PyInstaller-'+candidate.name))
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if not python_license.exists():
        python_license = Path(sys.base_prefix)/'LICENSE'
    if not python_license.exists():
        python_license = ROOT/'licenses/Python-LICENSE.txt'
    shutil.copy2(python_license, licenses/'Python-LICENSE.txt')
    (contents/'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'local.claude.native-unlock',
        'CFBundleName': APP_NAME,
        'CFBundleDisplayName': 'Claude 原生助手',
        'CFBundleExecutable': 'ClaudeNativeUnlock',
        'CFBundlePackageType': 'APPL', 'CFBundleVersion': BUILD_NUMBER,
        'CFBundleShortVersionString': VERSION, 'CFBundleIconFile': 'AppIcon',
        'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '15.0',
        'NSHumanReadableCopyright': 'Copyright © 2026 Claude Native Unlock contributors',
    }))
    # 后端可执行文件要单独用固定身份签：登录钥匙串条目的 ACL 认的是
    # **直接调用 Security 框架的那个进程**，也就是这个后端，而不是外层
    # .app。只签外层时，后端每次重建都会换 cdhash，密码条目就又要重新确认。
    sign(resources / 'Backend' / 'NativeUnlockBackend')
    # 其余嵌套的 Python 二进制已由 PyInstaller 做过 ad-hoc 签名。
    sign(app)
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
    archive = dist/f'{ARCHIVE_NAME}-{VERSION}-arm64.zip'
    if archive.exists():
        archive.rename(work/archive.name)
    run('/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, archive)
    image_root = work/'dmg'
    image_root.mkdir()
    run('/bin/cp', '-cR', app, image_root/app.name)
    (image_root/'Applications').symlink_to('/Applications')
    image = dist/f'{ARCHIVE_NAME}-{VERSION}-arm64.dmg'
    if image.exists():
        image.rename(work/image.name)
    run('/usr/bin/hdiutil', 'create', '-volname', 'Claude 原生助手', '-srcfolder', image_root,
        '-format', 'UDZO', image)
    artifacts = [archive, image]
    (dist/'SHA256SUMS.txt').write_text(''.join(
        hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in artifacts))
    prune_artifacts()
    print(app)


if __name__ == '__main__':
    build()
