#!/usr/bin/env python3
"""Build a self-contained arm64 macOS application and distributable archives."""
import hashlib
import importlib.metadata
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.3.36'
BUILD_NUMBER = '49'
APP_NAME = 'Claude 原生助手'
# 文件名保持 ASCII：非 ASCII 的发布文件名会被 GitHub 换成“.”，下载链接很难看。
ARCHIVE_NAME = 'Claude-Native-Assistant'


def run(*args):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


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
    # 使用说明里的版本号由构建时替换，避免每次发版都要手动改、改漏了还随包发出去。
    guide = (ROOT/'使用说明.html').read_text()
    (resources/'使用说明.html').write_text(guide.replace('{{VERSION}}', VERSION))
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
    # Nested Python binaries are already ad-hoc signed by PyInstaller.
    run('/usr/bin/codesign', '--force', '--sign', '-', app)
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
