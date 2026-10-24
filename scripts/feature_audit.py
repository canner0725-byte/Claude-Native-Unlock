#!/usr/bin/env python3
"""功能可用性检测：从已安装的 Claude.app 里读出官方功能表并逐条分类。

这个脚本只读 ASAR 里的官方 JS 源码，不启动、不控制 Claude，也不写任何配置。
它回答两个问题：

1. 某个版本里，主进程预载表 `JU()` 有多少项其实是写死的字面量（不构成证据）；
2. 每个有真实判定的功能，走的是哪条谓词链，能不能靠本地配置解锁。

用法：
    python3 scripts/feature_audit.py                 # 摘要
    python3 scripts/feature_audit.py --json          # 机器可读
    python3 scripts/feature_audit.py --app /path/to/Claude.app

退出码：0 正常；2 找不到 ASAR 或主 chunk。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

DEFAULT_APP = Path("/Applications/Claude.app")


def read_asar_file(asar_path: Path, rel: str) -> str:
    """从 ASAR 里取一个文件。头部格式：4B pickle 长度、4B header 长度、JSON。"""
    with asar_path.open("rb") as fh:
        header_prefix = fh.read(16)
        if len(header_prefix) < 16:
            raise ValueError("ASAR 头部不完整")
        # bytes 4:8 = pickle 大小（含自身 4 字节），12:16 = JSON 长度
        pickle_size = struct.unpack("<I", header_prefix[4:8])[0]
        json_len = struct.unpack("<I", header_prefix[12:16])[0]
        header = json.loads(fh.read(json_len).decode("utf-8"))
        base = 8 + pickle_size
        node = header
        for segment in rel.split("/"):
            if not segment:
                continue
            node = node["files"][segment]
        offset = base + int(node["offset"])
        size = int(node["size"])
        fh.seek(offset)
        return fh.read(size).decode("utf-8")


def find_main_chunk(app: Path) -> str:
    """主 chunk 文件名带内容哈希，每个版本都不一样。

    目录里有几十个 index.chunk-*.js，光看名字挑不出来——直接按内容找同时含
    `JU()` 和 `Hon` 表的那个，两个都在才说明是我们要审计的主进程块。
    """
    asar = app / "Contents/Resources/app.asar"
    if not asar.exists():
        raise FileNotFoundError(f"找不到 {asar}")
    with asar.open("rb") as fh:
        prefix = fh.read(16)
        json_len = struct.unpack("<I", prefix[12:16])[0]
        header = json.loads(fh.read(json_len).decode("utf-8"))
    build = header["files"][".vite"]["files"]["build"]["files"]
    candidates = [
        name for name in build
        if name.startswith("index.chunk-") and name.endswith(".js")
    ]
    fallback = None
    for name in candidates:
        # 大文件才可能是主块；先跳过明显的小模块，省掉几十次解压。
        if int(build[name].get("size", 0)) < 1_000_000:
            continue
        rel = f".vite/build/{name}"
        try:
            src = read_asar_file(asar, rel)
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
        if "function JU(" in src and "var Hon=" in src:
            return rel
        if fallback is None and "function JU(" in src:
            fallback = rel
    if fallback:
        return fallback
    raise FileNotFoundError("ASAR 里没有可用的主 chunk（既无 JU() 也无 Hon）")


def balanced(src: str, start: int, open_ch: str = "{", close_ch: str = "}") -> int:
    """返回与 src[start] 处开括号配对的闭括号下标，跳过字符串字面量。"""
    depth = 0
    i = start
    quote: str | None = None
    escaped = False
    while i < len(src):
        ch = src[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("括号不配对")


def split_top_level(body: str) -> list[str]:
    """按顶层逗号切分对象字面量，忽略嵌套结构里的逗号。"""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    current = ""
    for ch in body:
        if quote:
            current += ch
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'`":
            quote = ch
            current += ch
            continue
        if ch in "([{":
            depth += 1
        if ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def classify(expr: str) -> str:
    """把 JU() 里一项的表达式归类，用来区分「真判定」和「写死的字面量」。

    三元要排在函数调用前面判断：`Sir()?RU:{...}` 既是「以函数调用开头」，
    又是三元——按三元归类才反映它的实际形状（先判条件再二选一）。
    """
    expr = expr.strip()
    if expr == "RU":
        return "literal_supported"
    if re.fullmatch(r'\{status:"unavailable"\}', expr):
        return "literal_unavailable"
    if expr.startswith('{status:"unsupported"'):
        return "literal_unsupported"
    # 只有顶层（不在括号里）的问号才算三元；`?.` 是可选链，`??` 是空值合并，都不算
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(expr):
        ch = expr[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "?":
            nxt = expr[i + 1] if i + 1 < len(expr) else ""
            if nxt == "?":
                i += 1          # `??` 整体吞掉，别让第二个问号再判一次
            elif nxt != "." and depth == 0:
                return "ternary"
        i += 1
    if expr.startswith("Pb(") or expr.startswith("Fb("):
        return "gate_lookup"
    if re.match(r"^[A-Za-z_$][\w$]*\(", expr):
        return "predicate_call"
    return "other"


def extract_ju_entries(main: str) -> list[tuple[str, str]]:
    """拆出 JU() 返回对象里的全部 [名称, 表达式]。"""
    at = main.find("function JU(){")
    if at < 0:
        raise ValueError("找不到 JU()")
    ret = main.find("return{", at)
    brace = main.find("{", ret)
    end = balanced(main, brace)
    body = main[brace + 1:end]
    entries: list[tuple[str, str]] = []
    for part in split_top_level(body):
        match = re.match(r"^([A-Za-z_$][\w$]*):(.*)$", part, re.S)
        if match:
            entries.append((match.group(1), match.group(2).strip()))
    return entries


def extract_table(main: str, name: str) -> dict:
    """求值一个纯字面量对象表（Hon / 类似结构）。

    表里既有不带引号的数字键、`!0`/`!1` 这类 JS 布尔，又有嵌套对象，
    硬转 JSON 很脆弱。改交给 Node 求值：表里只有字面量，不执行任何函数。
    """
    at = main.find(f"var {name}={{")
    if at < 0:
        raise ValueError(f"找不到表 {name}")
    brace = main.find("{", at)
    end = balanced(main, brace)
    literal = main[brace:end + 1]
    if not shutil.which("node"):
        raise ValueError("需要 node 来解析 Hon 表")
    proc = subprocess.run(
        ["node", "-e", "let d='';process.stdin.on('data',c=>d+=c).on('end',"
                      "()=>console.log(JSON.stringify(eval('('+d+')'))))"],
        input=literal.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(f"Hon 表求值失败：{proc.stderr.decode('utf-8', 'replace')[:200]}")
    return json.loads(proc.stdout.decode("utf-8"))


def audit(app: Path) -> dict:
    asar = app / "Contents/Resources/app.asar"
    chunk_rel = find_main_chunk(app)
    main = read_asar_file(asar, chunk_rel)
    entries = extract_ju_entries(main)

    try:
        hon = extract_table(main, "Hon")
    except (ValueError, json.JSONDecodeError) as exc:
        hon = {}
        hon_error = str(exc)
    else:
        hon_error = None

    counts: dict[str, int] = {}
    rows = []
    for name, expr in entries:
        kind = classify(expr)
        counts[kind] = counts.get(kind, 0) + 1
        key_hits = re.findall(r'Pb\("(\d+)"\)|Fb\("(\d+)"', expr)
        keys = [a or b for a, b in key_hits]
        rows.append({
            "feature": name,
            "kind": kind,
            "expr": expr[:160],
            "keys": keys,
            "hon": {k: hon.get(k) for k in keys} if hon else None,
        })

    literal = counts.get("literal_supported", 0) + counts.get("literal_unavailable", 0) \
        + counts.get("literal_unsupported", 0)
    return {
        "app": str(app),
        "chunk": chunk_rel,
        "entries": len(entries),
        "counts": counts,
        "literal_entries": literal,
        "predicate_entries": len(entries) - literal,
        "hon_entries": len(hon),
        "hon_error": hon_error,
        "rows": rows,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="审计 Claude 官方功能表")
    parser.add_argument("--app", default=str(DEFAULT_APP), help="Claude.app 路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--rows", action="store_true", help="列出逐条判定")
    args = parser.parse_args(argv)

    try:
        result = audit(Path(args.app))
    except (FileNotFoundError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"App        : {result['app']}")
    print(f"主 chunk   : {result['chunk']}")
    print(f"功能条目   : {result['entries']}（预载表 JU()）")
    print(f"  写死字面量: {result['literal_entries']}  <- 不能当运行时证据")
    print(f"  有真实判定: {result['predicate_entries']}")
    print(f"Hon 表     : {result['hon_entries']} 条" + (f"（读取失败：{result['hon_error']}）" if result['hon_error'] else ""))
    print()
    print("判定类型分布：")
    for kind, n in sorted(result["counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {kind:22} {n}")

    if args.rows:
        print()
        for row in result["rows"]:
            if row["kind"].startswith("literal"):
                continue
            keys = ",".join(row["keys"]) or "-"
            hon = ""
            if row["hon"]:
                hon = " Hon[" + "; ".join(
                    f"{k}={v['rule']}:{json.dumps(v.get('value'))}" if v else f"{k}=ABSENT"
                    for k, v in row["hon"].items()
                ) + "]"
            print(f"  {row['feature']:34} {row['kind']:16} keys={keys}{hon}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
