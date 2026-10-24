#!/bin/sh
# 固定后台作业的外层入口。现在**只做闸门和日志，不驱动任何维护动作**。
#
# 为什么是个空壳：方案第七节要求整个流程由外层脚本驱动、维护代理只是其中一步，
# 而维护代理本身还没被授权运行（AGENTS.md「维护代理的边界」）。在零弹窗验证
# 通过之前，这个脚本被调度起来也只应该留下一条日志然后退出，不应该下载官方包、
# 不应该跑测试、不应该签名、不应该推分支。
#
# 闸门是 `~/.claude-maintenance/verified` 这个文件：只有动态层验证真的通过之后
# 才会被创建。没有它，脚本直接跳过。这样即使有人提前登记了作业，也不会有一个
# 没验证过的流程在后台动东西。
#
# 退出码：0 表示“按设计没有事要做”，不是“维护成功”。

set -eu

CONFIG="${CLAUDE_CONFIG_DIR:-$HOME/.claude-maintenance}"
STATE="${CNU_MAINTENANCE_STATE:-$HOME/Library/Application Support/Claude 原生助手/maintenance}"
GATE="$CONFIG/verified"
LOCK="$STATE/run.lock"

mkdir -p "$STATE"
chmod 700 "$STATE" 2>/dev/null || true

stamp() { date '+%Y-%m-%d %H:%M:%S'; }

# 单实例：mkdir 是原子的。上一次还没结束就跳过这一轮，不排队、不重试。
# 锁里写进程号：被 SIGKILL 或断电打断时锁会留下，那种情况下必须能自己恢复，
# 否则“跳过”会变成永久的——作业看起来在跑，实际再也不会做任何事。
if ! mkdir "$LOCK" 2>/dev/null; then
    holder=$(cat "$LOCK/pid" 2>/dev/null || echo '')
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
        echo "$(stamp) 上一轮（pid $holder）还在跑，跳过"
        exit 0
    fi
    echo "$(stamp) 发现残留锁（pid ${holder:-未知} 已不在），接管"
    # 锁里只有 pid 一个文件；逐项删，不用 rm -rf。
    rm -f "$LOCK/pid"
    rmdir "$LOCK" 2>/dev/null || { echo "$(stamp) 接管失败，跳过"; exit 0; }
    mkdir "$LOCK" 2>/dev/null || { echo "$(stamp) 接管失败，跳过"; exit 0; }
fi
echo $$ > "$LOCK/pid"
trap 'rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null || true' EXIT INT TERM

if [ ! -f "$GATE" ]; then
    echo "$(stamp) 未通过零弹窗验证（缺 $GATE），跳过。见 docs/SETUP_CHECKLIST.md 第 4 步"
    exit 0
fi

# 走到这里说明验证已经通过。真正的维护流程还没有实现——明确记一笔，
# 而不是静默成功，免得以后有人看到“作业在跑”就以为维护在工作。
echo "$(stamp) 闸门已开，但外层维护流程尚未实现；本轮不做任何事"
echo "$(stamp) 见 docs/MAINTENANCE_AGENT.md「尚未完成」"
exit 0
