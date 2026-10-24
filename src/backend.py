"""独立助手的内置命令入口。

Swift 界面只调用这里，再分发到三个后端模块。所有运行数据都放在
用户数据目录，不写进应用包，所以更新或移动助手不会丢备份。
"""
import json
import os
from pathlib import Path
import shutil
import sys
import traceback

# 协议版本和输出格式在引擎里定义，所有后端共用一套。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_unlock import PROTOCOL_VERSION, emit, envelope  # noqa: E402


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
    正式版校验（codesign 校验官方备份）和 Code 自动整理校验（校验 CLI
    副本）互不依赖，各自要跑几个子进程；两路并发能把每次检测缩短一秒左右。
    原生配置检测要用正式版结果，仍然排在它后面。
    """
    from concurrent.futures import ThreadPoolExecutor
    import official_unlock
    import official_update
    import native_features
    import native_cli_unlock
    result = {}
    application = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        official_future = pool.submit(official_unlock.status)
        cli_future = pool.submit(native_cli_unlock.status, 'official')
        try:
            application = official_future.result()
            result['official'] = application
        except Exception:
            result['official'] = {'error': '正式版检测未完成；请重新检测。'}
        try:
            result['cli'] = cli_future.result()
        except Exception:
            result['cli'] = {'error': 'Code 自动整理检测未完成；请重新检测。'}
    try:
        result['features'] = native_features.report(application)
    except Exception:
        result['features'] = {'error': '原生配置检测未完成；请重新检测。'}
    # 官方新版提醒只读上次成功检查的缓存，不联网：合并检测要保持快。
    # 联网检查由界面单独发 `official_update check`，只在缓存过期时自动发。
    try:
        result['officialUpdate'] = official_update.cached_status(application)
    except Exception:
        result['officialUpdate'] = {'state': 'unknown', 'installed': '', 'latest': '',
                                    'adapted': False, 'checkedAt': '', 'stale': False,
                                    'channel': 'stable',
                                    'message': '官方新版提醒读取失败；重新检测。'}
    return envelope(result)


def switch_all(turn_on):
    """一键全开 / 全关：正式版补丁、原生配置和 Code 自动整理一起切换。

    每一步调用的都是原来的命令本身，各自的指纹校验、备份和恢复记录都不变；
    这里只负责顺序、跳过“此版本没有的设置”和“没有恢复记录的项”，并如实
    汇总已经完成和失败的部分，绝不把部分成功说成全部成功。
    """
    import official_unlock
    import native_features
    import native_cli_unlock
    steps = []

    def record(identifier, title, state, message=''):
        steps.append({'id': identifier, 'title': title, 'state': state, 'message': message})

    def run(identifier, title, action):
        try:
            action()
            record(identifier, title, 'done')
            return True
        except Exception as error:
            record(identifier, title, 'failed', str(error) or '操作未完成')
            return False

    if turn_on:
        # 先装补丁：原生配置的写入要求正式应用校验通过。
        if not run('official', '正式版补丁', official_unlock.enable):
            reason = steps[-1]['message']
            return envelope({'on': False, 'steps': steps,
                             'error': '补丁没有装好（' + reason + '）；已停止，未修改原生配置。'})
        try:
            application = official_unlock.status()
        except Exception:
            application = None
        try:
            report = native_features.report(application)
        except Exception:
            report = {}
        available = {item.get('id'): item.get('available')
                     for item in report.get('features', [])}
        for identifier, spec in native_features.FEATURES.items():
            if available.get(identifier) is False:
                record(identifier, spec['title'], 'skipped', '此版本没有这个设置')
                continue
            run(identifier, spec['title'],
                lambda name=identifier: native_features.change(name))
        run('code-auto-dream', 'Code 自动整理',
            lambda: native_cli_unlock.enable('official'))
    else:
        # 先还原配置，再卸补丁：配置写入要求正式应用仍然校验通过。
        try:
            report = native_features.report()
        except Exception:
            report = {}
        restorable = {item.get('id'): item.get('canRestore')
                      for item in report.get('features', [])}
        for identifier, spec in native_features.FEATURES.items():
            if not restorable.get(identifier):
                record(identifier, spec['title'], 'skipped', '没有本工具的恢复记录')
                continue
            run(identifier, spec['title'],
                lambda name=identifier: native_features.change(name, restore=True))
        run('code-auto-dream', 'Code 自动整理',
            lambda: native_cli_unlock.restore('official'))
        run('official', '正式版补丁', official_unlock.disable)

    failed = [step for step in steps if step['state'] == 'failed']
    result = {'on': turn_on, 'steps': steps,
              'completed': len([step for step in steps if step['state'] == 'done'])}
    if failed:
        titles = '、'.join(step['title'] for step in failed)
        result['error'] = (titles + ' 没有开启；其余已生效。' if turn_on
                           else titles + ' 没有恢复；其余已恢复。')
    return envelope(result)


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
            emit(status_all())
            return
        # 总开关：一条命令完成全部可用性切换，界面只发一次调用。
        if sys.argv[1:2] in (['enable-all'], ['restore-all']):
            emit(switch_all(sys.argv[1] == 'enable-all'))
            return
        # 官方新版提醒：联网检查一次并写缓存，失败保留旧结论。
        if sys.argv[1:2] == ['official_update']:
            if sys.argv[2:3] != ['check']:
                raise ValueError('请选择有效的检测操作。')
            import official_update
            emit(official_update.check())
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
        emit({'error': str(error)})
        raise SystemExit(1)
    except Exception:
        # 真正的 traceback 走 stderr；stdout 只留给界面能解析的 JSON。
        traceback.print_exc()
        emit({'error': '操作未完成；已保留备份，请重新检测。'})
        raise SystemExit(1)


if __name__ == '__main__':
    main()
