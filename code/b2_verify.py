#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B2 验收器：**逐位对齐**（精确）＋ **机械蕴含** ＋ **防伪造反查** ＋ **抽样**（人看）。

执行者交的判定表（全 ASCII，一行一处）：
    {"f":"b01.txt","ln":63,"occ":2,"cls":"R","lim":"100","unit":"pct"}
      f/ln/occ = 文件 / 行号 / **行内第几处**「加收」（地板按此精确枚举）
      cls      = R 规定（可加收/加收上限）｜T 项目名或字段名里的字样｜O 其他
      lim/unit = 仅 cls=R：该规定给出的**上限数值**（`lim` 纯数字，`unit` ∈ pct/yuan）；无数值则空

本脚本判的（机械可判）：
  ① **逐位对齐**：报来的 (文件,行号,第几处) 序列必须与地板序列**逐位相同**（一处不漏、不重、不乱序）。
  ② 结构：类/单位/数值形态合法；非 R 不许带 lim。
  ③ **机械蕴含**：紧窗口（±6 字）里有 `可加收`／`加收不超过` ⇒ 必须 R；有 `（加收）` ⇒ 必须 T。
     这是**规则不是等价**（分母必须看：564 里有 553 处被钉住，剩 11 处机械判不了）——
     被钉住的处不允许和规则打架；**钉不住的 11 处正是要人看的地方**。
  ④ **防伪造**：cls=R 且给了 lim ⇒ `lim` ＋（pct=`%` / yuan=`元`）必须**逐字**出现在该处的
     窗口（该行及前后 1 行）里 ⇒ 「编一个数值」过不去（⑫「只信带原文支撑的结论」的机器版）。
  ⑤ 抽样：每类各取若干条连片段印出来，**语义对不对机器判不了**，只能人看。

⚠ 尺子先落成文件再看结果；配对负对照 `b2_negctl.py` 断言它「报在哪、报几条」。
用法：python b2_verify.py [--rows <rows.jsonl>] [--floor <B2_floor.json>] [--out <报告.txt>] [--json <机械判据.json>]
退出码：0 = 机械层全绿；1 = 有硬伤；2 = 输入缺失/拒绝覆写
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b2_floor  # noqa: E402 —— 紧窗口/片段的唯一实现

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CLS = {"R", "T", "O"}
UNIT = {"pct", "yuan", ""}
FILES = lambda: {p.name for p in sorted((HERE / "b2run/src").glob("*.txt"))}  # noqa: E731


def load_rows(p: Path, ok_files: set[str] | None = None):
    """→ {file: [(ln, occ, cls, lim, unit)]} ＋ 畸形行清单（**不静默跳过**）。

    `ok_files` 缺省从语料目录 glob；**优先由地板的 files 给**——「哪些文件合法」这件事
    只该有一个真源，地板就是那个（20260920：B2w 窗口单跑时，语料目录换了、地板没换，
    两处各写一份就会漏判）。
    """
    by: dict[str, list] = {}
    malformed: list[str] = []
    ok_files = ok_files or FILES()
    for i, raw in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        s = raw.strip()
        if not s:
            continue
        try:
            r = json.loads(s)
            f, ln, occ = str(r["f"]), int(r["ln"]), int(r["occ"])
            cls = str(r["cls"])
            lim = str(r.get("lim", "") or "")
            unit = str(r.get("unit", "") or "")
        except Exception as e:  # noqa: BLE001
            malformed.append(f"行{i}: JSON 解析失败（{e}）：{s[:70]}")
            continue
        why = []
        if f not in ok_files:
            why.append(f"文件不在语料内：{f}")
        if cls not in CLS:
            why.append(f"cls 不在三类内：{cls}")
        if unit not in UNIT:
            why.append(f"unit 非法：{unit}")
        if lim and not lim.isdigit():
            why.append(f"lim 不是纯数字：{lim}")
        if cls != "R" and (lim or unit):
            why.append(f"非 R 却带了 lim/unit：{cls} {lim}/{unit}")
        if (lim == "") != (unit == ""):
            why.append(f"lim/unit 不配套：{lim}/{unit}")
        if why:
            malformed.append(f"行{i}: " + "；".join(why))
            continue
        by.setdefault(f, []).append((ln, occ, cls, lim, unit))
    return by, malformed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default=str(HERE / "b2run/B2_rows.jsonl"))
    ap.add_argument("--floor", default=str(ROOT / "regression_20260916/ab_deleg/B2_floor.json"))
    ap.add_argument("--out", default=str(ROOT / "regression_20260916/ab_deleg/B2_verify.txt"))
    ap.add_argument("--json", default="", help="机械判据另存 JSON（负对照断言用）")
    a = ap.parse_args()
    rows_p, floor_p = Path(a.rows), Path(a.floor)
    for p, hint in ((rows_p, "执行者的判定表"), (floor_p, "先跑 b2_floor.py")):
        if not p.exists():
            print(f"[FATAL] 不存在：{p}（{hint}）", file=sys.stderr)
            return 2
    floor = json.loads(floor_p.read_text(encoding="utf-8"))
    by, malformed = load_rows(rows_p, {fd["ascii"] for fd in floor["files"]})
    meta = {(fd["ascii"], o["ln"], o["occ"]): o for fd in floor["files"] for o in fd["occs"]}

    L = [f"B2 验收  产出 {rows_p.name}", ""]
    hard, suspect, fabric = list(malformed), [], []
    align, align_bad = {}, []
    L.append("一、逐位对齐（地板 = **精确枚举**：语料里就这么多处「" + floor["keyword"] + "」）")
    for fd in floor["files"]:
        name = fd["ascii"]
        want = [(o["ln"], o["occ"]) for o in fd["occs"]]
        got = [(ln, occ) for ln, occ, *_ in by.get(name, [])]
        first = next((i for i, (x, y) in enumerate(zip(want, got)) if x != y), None)
        if first is None and len(want) != len(got):
            first = min(len(want), len(got))
        align[name] = {"floor": len(want), "got": len(got), "first_div": first}
        s = f"  {name}: 地板 {len(want)} 处 / 报来 {len(got)} 处"
        if first is None and len(got) == len(want):
            L.append(s + " ⇒ **逐位相同**")
        else:
            d = f"{s} ⇒ **不一致**，首个分歧位 #{first}"
            if first is not None and first < len(want):
                d += (f"（地板 {(want[first])} vs 报来 "
                      f"{got[first] if first < len(got) else '缺'}）")
            align[name]["mismatch"] = d
            align_bad.append(d)
            L.append(d)

    #: ⚠ 分母必须自证：`forced` 是**语料的属性**，只能从地板算——若从"报来的行"里数，
    #: 执行者少报的处会把分母一起缩小，读数看起来一样漂亮（20260918「读数分母必须自证」）。
    forced_total = sum(1 for o in meta.values() if o["force"])
    checked = 0
    L += ["", "二、机械蕴含（紧窗口 ±6 字的字面信号——**规则不是等价**，被钉住的处不许和它打架）"]
    for name, rs in sorted(by.items()):
        for ln, occ, cls, lim, unit in rs:
            o = meta.get((name, ln, occ))
            if not o:
                continue
            # 限值反查（防伪造）：数值＋单位必须逐字在该处窗口里
            if cls == "R" and lim:
                mark = lim + ("%" if unit == "pct" else "元")
                if mark not in o["win"]:
                    fabric.append(f"{name}:{ln} 第{occ}处 报 lim={mark}，但该处窗口里找不到这几个字")
            if o["force"]:
                checked += 1
                if cls != o["force"]:
                    suspect.append(f"{name}:{ln} 第{occ}处 紧窗口「{o['tight']}」⇒ 规则钉 {o['force']}，"
                                   f"报来 {cls}")
    L.append(f"  地板里被字面信号钉住的处：**{forced_total}/{floor['total_occ']}**"
             f"（剩 {floor['total_occ'] - forced_total} 处机械判不了 ⇒ 见第六栏抽样）；"
             f"其中**报来了的** {checked} 处已逐处对照过规则")
    L += ["", "三、结构硬伤（必须为 0）"]
    L += [f"  {x}" for x in hard] or ["  （无）"]
    L += ["", "四、与规则打架 / 限值反查不过（**须逐条看**）"]
    L += [f"  {x}" for x in (suspect + fabric)] or ["  （无）"]
    L += ["", "五、分布"]
    L.append("  类别：" + "  ".join(f"{k}={v}" for k, v in sorted(Counter(
        c for rs in by.values() for _, _, c, _, _ in rs).items())))
    L.append("  限值：" + "  ".join(f"{k}={v}" for k, v in sorted(Counter(
        (u or "无") for rs in by.values() for _, _, _, _, u in rs).items())))
    L.append("  文件：" + "  ".join(f"{k}={len(v)}" for k, v in sorted(by.items())))
    L += ["", "六、抽样（每类 2 条；**语义对不对机器判不了**，须人看）"]
    for want_cls in ("R", "T", "O"):
        n = 0
        for name, rs in sorted(by.items()):
            for ln, occ, cls, lim, unit in rs:
                if n >= 2:
                    break
                if cls != want_cls:
                    continue
                o = meta.get((name, ln, occ), {})
                L.append(f"  [{cls}] {name}:{ln} 第{occ}处 lim={lim or '—'}/{unit or '—'}"
                         f"  片段：{o.get('frag', '')}")
                n += 1
    txt = "\n".join(L) + "\n"
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(txt)
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"align": align, "malformed": malformed, "align_bad": align_bad,
             "rule_clash": suspect, "fabric": fabric,
             "forced_total": forced_total, "forced_checked": checked,
             "total": floor["total_occ"]},
            ensure_ascii=False, indent=1), encoding="utf-8")
    out = Path(a.out)
    if out.exists():
        print(f"[拒绝覆写] {out} 已存在（⑰）；本次未落盘。", file=sys.stderr)
        return 1 if hard else 0
    out.write_text(txt, encoding="utf-8")
    print(f"[落盘] {out}")
    # 退出码：枚举/结构硬伤 ＋ **编数值**（防伪造层）都算失败；「与规则打架」另列（那是语义，
    # 规则本身也只是规则，须人看 ⇒ 不能让它自动判死）
    return 1 if (malformed or align_bad or fabric) else 0


if __name__ == "__main__":
    sys.exit(main())
