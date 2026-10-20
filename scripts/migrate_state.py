"""把旧项目目录里的恢复记录迁移到独立助手的数据目录。

迁移前逐个校验恢复记录、备份路径、官方签名和补丁文件；只复制，
不修改 Claude，也不删除源文件。任何一项校验失败就停止，不写目标目录。
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import native_unlock as core
import official_unlock as official
import native_cli_unlock as cli


def migrate(source_root, destination):
    """校验并复制三类恢复记录；返回复制数量和“源文件保留”标记。"""
    source = Path(source_root).resolve() / 'work'
    destination = Path(destination).absolute()
    if destination.resolve() != destination or source == destination / 'work':
        raise ValueError('迁移路径异常。')
    target = destination / 'work'
    if target.exists() and any(target.iterdir()):
        raise ValueError('目标已有恢复记录；不覆盖。')
    # 正式补丁、官方配置和 Code 自动整理各自的恢复材料。
    groups = [source/'official-install', source/'native-features',
              source/'native-code-memory/official']
    records = []
    bundles = []
    for folder in groups:
        if not folder.exists():
            continue
        if folder.is_symlink():
            raise ValueError('源目录不可为符号链接。')
        paths = list(folder.glob('*.json')) if folder.name == 'native-features' else [folder/'manifest.json']
        for path in paths:
            if not path.exists():
                continue
            if path.is_symlink():
                raise ValueError('恢复记录不可为符号链接。')
            value = json.loads(path.read_text())
            # 先确认备份和当前安装仍然匹配，避免把损坏或错版本的记录搬过去。
            if path.name == 'manifest.json':
                backup = Path(value['backup'])
                if backup.parent != folder or backup.is_symlink() or backup.resolve() != backup:
                    raise ValueError('备份路径异常。')
                if folder.name == 'official-install':
                    core.verify_source(backup)
                    official.verify_patched(official.TARGET, value)
                else:
                    target, _, _, adapter = cli.locations('official', version=value.get('version'))
                    cli.verify_source(backup, adapter)
                    cli.verify_installed(target, value)
                bundles.append(backup)
                retired = Path(value.get('retired', ''))
                if value.get('upgradeFromFiles') and retired.exists():
                    if retired.parent != folder or retired.resolve() != retired:
                        raise ValueError('撤回备份路径异常。')
                    official.verify_patched(retired, dict(value, installedFiles=value['upgradeFromFiles']))
                    bundles.append(retired)
            records.append((path, value))
    if not records:
        raise ValueError('没有可迁移的恢复记录。')
    # 所有源记录都校验通过后才开始复制；失败时保留已复制的部分，便于排查。
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.mkdir(exist_ok=True, mode=0o700)
    for bundle in bundles:
        out = target / bundle.relative_to(source)
        out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.run(['/bin/cp', '-cR', str(bundle), str(out)], check=True)
    def rewrite(value):
        """把记录里的旧路径改到新数据目录；其他内容保持不变。"""
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, str) and value.startswith(str(source) + '/'):
            return str(target) + value[len(str(source)):]
        return value
    for path, value in records:
        out = target / path.relative_to(source)
        out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        out.write_text(json.dumps(rewrite(value), ensure_ascii=False, indent=2) + '\n')
        out.chmod(0o600)
    return {'migratedRecords': len(records), 'copiedRecoveryBundles': len(bundles),
            'originalsPreserved': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_root', type=Path)
    parser.add_argument('--destination', type=Path, default=Path.home()/'Library/Application Support/Claude 原生助手')
    args = parser.parse_args()
    print(json.dumps(migrate(args.source_root, args.destination)))
