"""独立助手的内置命令入口。

Swift 界面只调用这里，再分发到三个后端模块。所有运行数据都放在
用户数据目录，不写进应用包，所以更新或移动助手不会丢备份。
"""
import json
import os
from pathlib import Path
import shutil
import sys


ROOT_NAME = 'Claude 原生助手'
LEGACY_ROOT_NAME = 'Claude Native Unlock'


def rewrite_legacy_paths(root, old, new):
    """把记录文件里写死的旧目录路径改成新路径（备份路径是绝对路径）。"""
    before, after = str(old), str(new)
    for path in root.rglob('*.json'):
        if path.is_symlink() or not path.is_file():
            continue
        text = path.read_text()
        if before in text:
            path.write_text(text.replace(before, after))


def state_entries(root):
    """目录里已有的真实状态条目（安装记录、备份都在 work/ 下面）。"""
    work = root / 'work'
    if not work.is_dir() or work.is_symlink():
        return []
    return list(work.iterdir())


def disposable_shell(root):
    """判断目录是不是“界面启动时建出来的空壳”。

    界面为了写 last-operation.json 会先建好目录；此时 work/ 里还没有任何
    安装记录或备份。只有这种情况才允许清掉重建，避免误删真实状态。
    """
    if not root.is_dir() or root.is_symlink():
        return False
    if state_entries(root):
        return False
    for entry in root.iterdir():
        if entry.name == 'work' or entry.name.startswith('last-'):
            continue
        return False
    return True


def storage_root():
    """恢复记录和备份的固定位置。

    0.3.16 起目录名随产品改名。旧目录还在、新目录还没拿到真实状态时整体
    改名一次，并把记录里的绝对路径一起改掉；任何一步失败都退回旧目录，
    绝不丢官方备份。符号链接一律不动，避免被重定向到别处。
    """
    support = Path.home() / 'Library/Application Support'
    current, legacy = support / ROOT_NAME, support / LEGACY_ROOT_NAME
    if current.is_symlink() or legacy.is_symlink() or not legacy.is_dir():
        return current
    if state_entries(current):
        return current
    if current.exists() and not disposable_shell(current):
        return current
    try:
        if current.exists():
            shutil.rmtree(current)
        legacy.rename(current)
        rewrite_legacy_paths(current, legacy, current)
    except Exception:
        try:
            if current.exists() and not legacy.exists():
                current.rename(legacy)
        except OSError:
            pass
        return legacy
    return current


def configure(root=None):
    """把三个模块的状态目录统一指到用户数据目录。

    拒绝符号链接，避免备份或恢复记录被重定向到别处。
    """
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


def configure_module(name, root=None):
    """单模块命令行入口用：让 `__main__` 和 backend 眼里的模块是同一份。

    `python3 src/official_unlock.py status` 时本模块以 `__main__` 身份运行；
    configure() 再 `import official_unlock` 拿到的是另一份副本，STATE 设在
    副本上，真正执行 main() 的这份仍指向仓库里的 work/——读不到安装记录，
    于是把已打补丁的应用误报成「尚未适配」，还在仓库里留下空目录。
    先按模块名把 `__main__` 登记回 sys.modules，两边才是同一个对象。
    """
    sys.modules.setdefault(name, sys.modules['__main__'])
    configure(root)
    return sys.modules[name]


def status_all():
    """一次进程内完成三项只读检测，减少启动等待和界面闪烁。

    每个部分单独捕获错误：某一项失败时，其余状态仍然返回。
    """
    import official_unlock
    import native_features
    import native_cli_unlock
    result = {}
    application = None
    try:
        application = official_unlock.status()
        result['official'] = application
    except Exception:
        result['official'] = {'error': '正式版检测未完成；请重新检测。'}
    try:
        result['cli'] = native_cli_unlock.status('official')
    except Exception:
        result['cli'] = {'error': 'Code 自动整理检测未完成；请重新检测。'}
    try:
        result['features'] = native_features.report(application)
    except Exception:
        result['features'] = {'error': '原生配置检测未完成；请重新检测。'}
    return result


def main():
    """按第一个参数选择后端，例如 official_unlock status。"""
    try:
        configure()
        # 固定系统工具路径；合并检测和单模块命令都使用它。
        os.environ['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
        import official_unlock
        import native_features
        import native_cli_unlock
        if sys.argv[1:2] == ['status-all']:
            print(json.dumps(status_all(), ensure_ascii=False))
            return
        routes = {'official_unlock': official_unlock.main,
                  'native_features': native_features.main,
                  'native_cli_unlock': native_cli_unlock.main}
        if len(sys.argv) < 3 or sys.argv[1] not in routes:
            raise ValueError('请选择有效的补丁操作。')
        route = routes[sys.argv[1]]
        # 把剩余参数原样交给对应模块。
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        route()
    except ValueError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        raise SystemExit(1)
    except Exception:
        print(json.dumps({'error': '操作未完成；已保留备份，请重新检测。'}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
