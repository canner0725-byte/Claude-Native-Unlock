"""Copy verified legacy recovery records; never change Claude or delete originals."""
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
    source = Path(source_root).resolve() / 'work'
    destination = Path(destination).absolute()
    if destination.resolve() != destination or source == destination / 'work':
        raise ValueError('迁移路径异常。')
    target = destination / 'work'
    if target.exists() and any(target.iterdir()):
        raise ValueError('目标已有恢复记录；不覆盖。')
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
            if path.name == 'manifest.json':
                backup = Path(value['backup'])
                if backup.parent != folder or backup.is_symlink() or backup.resolve() != backup:
                    raise ValueError('备份路径异常。')
                if folder.name == 'official-install':
                    core.verify_source(backup)
                    official.verify_patched(official.TARGET, value)
                else:
                    cli.verify_source(backup)
                    cli.verify_installed(cli.locations('official')[0], value)
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
    # All sources are checked before copying. A partial destination is retained on failure.
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.mkdir(exist_ok=True, mode=0o700)
    for bundle in bundles:
        out = target / bundle.relative_to(source)
        out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.run(['/bin/cp', '-cR', str(bundle), str(out)], check=True)
    def rewrite(value):
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
    parser.add_argument('--destination', type=Path, default=Path.home()/'Library/Application Support/Claude Native Unlock')
    args = parser.parse_args()
    print(json.dumps(migrate(args.source_root, args.destination)))
