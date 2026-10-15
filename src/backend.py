"""Bundled command bridge. Runtime data never lives inside the application."""
import json
import os
from pathlib import Path
import sys


def storage_root():
    return Path.home() / 'Library/Application Support/Claude Native Unlock'


def configure(root=None):
    import native_unlock as core
    root = storage_root() if root is None else Path(root)
    if root.resolve() != root.absolute():
        raise ValueError('备份路径包含符号链接；停止操作。')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    core.ROOT = root
    core.WORK = root / 'work'
    core.WORK.mkdir(exist_ok=True, mode=0o700)
    import official_unlock as official
    import native_features as features
    official.STATE = core.WORK / 'official-install'
    features.STATE = core.WORK / 'native-features'
    return core


def main():
    try:
        configure()
        import official_unlock
        import native_features
        import native_cli_unlock
        routes = {'official_unlock': official_unlock.main,
                  'native_features': native_features.main,
                  'native_cli_unlock': native_cli_unlock.main}
        if len(sys.argv) < 3 or sys.argv[1] not in routes:
            raise ValueError('请选择有效的补丁操作。')
        route = routes[sys.argv[1]]
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        os.environ['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
        route()
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        raise SystemExit(1)
    except Exception:
        print(json.dumps({'error': '操作未完成；已保留备份，请重新检测。'}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
