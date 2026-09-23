#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B2 机械地板：把 2 份语料里**每一处**「加收」枚举成 (行号, 行内第几处) ＋ 该处的逐字窗口。

与 B1 地板的**结构差别（这是 B2 设计的关键）**：B1 的地板是**关键字命中的超集**（含表头假阳性），
覆盖率 <100% 还需逐条分辨「执行者判假」还是「漏判」；B2 的地板是**精确枚举**——
语料里就这么多处「加收」，一处不多一处不少 ⇒ 覆盖可以做到「**逐位对齐**」，没有解释空间。

⚠ 本文件同时是**唯一实现**：片段窗口 `frag()`、行内出现位置 `occ_positions()` 都由这里出，
装配脚本（b2_terms.py）与验收器（b2_verify.py）**直接 import**，不各写一份（设计 §3.8 通则①：
同一件事写两份实现，迟早口径打架——⑭ 的教训）。

只读语料、只写本项目的 evidence 目录；已存在拒绝覆写（⑰）。
用法：python b2_floor.py
退出码：0 = 落盘成功；2 = 输入缺失/拒绝覆写
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = HERE / "b2run/src"
KEYWORD = "加收"
WIN = 20  #: 窗口半宽（字符）——判「这一处是不是一条规范性规定」够用，且与 B1 的 40 字上限同量级


def tight(line: str, occ: int, win: int = 6) -> str:
    """±win 的**紧窗口**——用来判「这一处是名字里的、还是规定里的」。

    为什么必须紧：语料是硬换行的表格转文本，一行里往往**同时**含
    `…儿童（加收） 次 可加收不超过…` 两处；±20 的宽窗口会让两者互相污染，
    紧窗口（±6）才能把「（加收）」与「可加收」分开。
    """
    return frag(line, occ, win)


def force_from(t: str) -> str:
    """紧窗口给出的**机械强制类别**（空串=机械判不了，须读上下文）。

    这是「验收比生产弱」的那一半：它只能钉住有明确字面信号的处，
    钉不住的地方正是要人抽样看的地方（分母必须报出来）。
    """
    #: 「加收项」**不**强制——它是总则里的**术语定义**（"所称加收项，指…"），语义上是 O 还是 T
    #: 正好是必须人看的判断，机器不许替它拿主意（第一版把它当 R，属于拿规则冒充语义）
    if "可加收" in t or "加收不超过" in t:
        return "R"
    if "（加收）" in t or "(加收)" in t:
        return "T"
    return ""


def occ_positions(line: str) -> list[int]:
    """该行内每一处 KEYWORD 的**字符偏移**（0 基），按出现顺序。"""
    out, i = [], line.find(KEYWORD)
    while i >= 0:
        out.append(i)
        i = line.find(KEYWORD, i + 1)
    return out


def frag(line: str, occ: int, win: int = WIN) -> str:
    """第 occ 处（1 基）为中心的逐字窗口，**逐字由构造保证**（不做任何改写/去空白）。

    这是「执行者一个字都不用打中文」的实现：它只报 (行号, 第几处)，中文由这里取。
    """
    pos = occ_positions(line)
    if not (1 <= occ <= len(pos)):
        raise IndexError(f"occ={occ} 越界（该行共 {len(pos)} 处）")
    i = pos[occ - 1]
    return line[max(0, i - win): i + len(KEYWORD) + win]


def scan(path: Path) -> dict:
    #: ⚠ 口径（20260920 补）：语料是 **CRLF**，于是「这文件多大」有四个都对的答案——
    #:   框架 `read_file`：不翻译换行、数 `\r`（w01.txt = **5,681**）
    #:   本函数原先的 `read_text()`：通用换行翻译掉 `\r`（= 5,281）
    #:   `wc -c`：字节（= 11,807）；`wc -l`：行（= 400）
    #: 行号与处数三者一致（不受影响），但**「必须进上下文的字符量」必须以框架口径为准**——
    #: 执行者看到的就是 5,681。所以两个都出，别再让读者猜是哪个。
    raw = path.read_bytes().decode("utf-8", "replace")
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    occs = []
    for ln, line in enumerate(lines, 1):
        for k in range(1, len(occ_positions(line)) + 1):
            t = tight(line, k)
            #: 限值反查用的窗口：该处**行及其后 2 行**（硬换行会把 `100%。` 甩到下一行）
            win = "".join(lines[max(0, ln - 2): ln + 2])
            occs.append({"ln": ln, "occ": k, "frag": frag(line, k),
                         "tight": t, "force": force_from(t), "win": win})
    return {"ascii": path.name, "chars": len(text), "chars_raw": len(raw),
            "crlf": raw.count("\r\n"), "lines": len(lines),
            "count": len(occs), "occs": occs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--out", default=str(ROOT / "regression_20260916/ab_deleg/B2_floor.json"))
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    files = sorted(src.glob("*.txt"))
    if not files:
        print(f"[FATAL] 没有语料：{src}（先跑 stage_b2.py）", file=sys.stderr)
        return 2
    if out.exists():
        print(f"[拒绝覆写] {out} 已存在（⑰）；本次未落盘。", file=sys.stderr)
        return 2
    doc = {"keyword": KEYWORD, "win": WIN, "src": str(src), "files": []}
    for p in files:
        d = scan(p)
        doc["files"].append(d)
        print(f"{d['ascii']}: {d['lines']} 行 / **{d['chars_raw']} 字符（框架口径，含 \\r）**"
              f" / {d['chars']} 字符（翻译换行后）/ CRLF {d['crlf']} / **{d['count']} 处**「{KEYWORD}」")
    doc["total_occ"] = sum(f["count"] for f in doc["files"])
    doc["total_chars"] = sum(f["chars"] for f in doc["files"])
    doc["total_chars_raw"] = sum(f["chars_raw"] for f in doc["files"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[落盘] {out}")
    print(f"合计 {doc['total_chars_raw']} 字符（框架口径）/ {doc['total_chars']} 字符（翻译后）"
          f" / {doc['total_occ']} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
