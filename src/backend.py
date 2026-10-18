"""独立助手的内置命令入口。

Swift 界面只调用这里，再分发到三个后端模块。所有运行数据都放在
用户数据目录，不写进应用包，所以更新或移动助手不会丢备份。
"""
import json
import os
from pathlib import Path
import sys


def storage_root():
    """恢复记录和备份的固定位置。"""
    return Path.home() / 'Library/Application Support/Claude Native Unlock'


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
