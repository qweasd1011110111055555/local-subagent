# -*- coding: utf-8 -*-
"""DGX 本地模型·L3 逐工具调用循环执行器（纯 stdlib，零依赖）。

与通道 A（``ds4f_step.py``：纯文本进、文本出，模型无工具）的区别：这里模型可以
**自己发起工具调用**，由本脚本执行、把结果回填进对话、循环到它给出不带工具调用
的最终回执——这就是「V4.1 输出『调用 xxx 工具、参数 yyy』，执行方执行并回传」
的那个循环，只是检查者从指挥换成了本脚本的白名单 + 事后审计。

用法::

    python ds4f_loop.py <任务增量.md> --workdir <仓库绝对路径>
                        [--brief <项目说明书.md>] [--max-turns 12]
                        [--deadline-min 20] [--out <目录>] [--out-file <路径>]
                        [--lock]

本脚本与项目无关：仓库锚点由 ``--workdir`` 显式给（**必填**——默认值指向某个具体
仓库是给别的项目埋雷）；项目专属规则走 ``--brief``（不传则系统提示自带通用规则
与回报格式，照样能干活）。

安全模型（为什么这样就算「下限有保障」）：
- 工具白名单只有两个：``run_command``（黑名单片段+超时+输出截断）与 ``read_file``
  （按字符分页）。**没有写文件工具**——执行者的唯一产出是最终回执文本。
- 每一次工具调用与回执都逐条落进 ``trace.jsonl``，指挥事后审计整条 trace。
- 命令黑名单只挡仓库级写操作（commit/push/reset/checkout/clean、rm -rf 等），
  挡不住命令自己的重定向——那部分靠 trace 审计兜底，不假装这是沙箱。
- R3/R4 护栏（20260916 反馈）：写文件命令自动回显目标大小（空文件当场暴露）；
  空产出/与上轮重复/连续多轮工具调用时注入「换方法或先写产出」提示；剩余 ≤2 轮
  注入收尾指令（产出文件取 --out-file 或任务书「输出写到：」）；异常退出时
  reply.md 在 [未完成] 外附最后三轮在做什么——指挥不必翻 trace 才知道卡在哪。
- **产出判据是两层**（20260920 缺陷⑳/㉙ 修法，真模型实测撞出）：㉔ 把「磨循环」判据
  改成「连续 N 轮没有产出」是对的，但实现是**纯子串** `out_name in args_text`
  ⇒ `read_file`/`cat`/`ls` 产出文件都算「产出」并清零 streak，护栏被静默绕过
  （真模型六轮一声不响，其中两轮在读自己刚写坏的产出文件）。现在①**语义层**：看产出
  文件的 `(size, mtime)` 这一轮变没变——不问命令文本长什么样，`>>`/`tee`/heredoc/
  `python -c open(...)` 一视同仁；②**静态层** `is_emit_call` 只认**写入位**。
  **两层取或**是刻意的：只收窄静态层会把假阴换成假阳，而 ㉔ 正是花 8 次假阳才换来收窄。
  同批修 ㉙：`/`、`~` 起的写目标在 Windows 上只有 bash 说得清落在哪
  （`Path("/tmp/x")` 会按 Windows 规则落到**盘符根下的 tmp**，而 MSYS 的 `/tmp` 实为
  %TEMP%）⇒ 交给**跑这条命令的
  同一个 bash** 判；真判不出来时说「**无法判定**」，**不许说不存在的那个「失败」**
  ——㉔ 自己的口径：误导性提示比无效提示更坏。
- **无产出锚点必须喊出来**（20260918 缺陷⑭）：多轮跑而任务书没有**可解析的**
  「输出写到：<路径>」（近义词不算）时，收尾硬闸不会注入、产出缺失检测恒为假
  ——两条护栏**一起静默失效**。旧版对此一声不吭，回执照样写「完成」。现在
  trace 记 `no_out_anchor`，reply.md 首行加 [框架附注] 明说「完成」不可核验。
  **这一层不能只靠 hook**：hook 只管分发时，而 settings.json 被宿主整体抹掉过
  4 次（R5）；分发闸与框架喊话是**两侧同尺**，不是一道闸。
- **空回执不算完成**（20260917 缺陷⑯；成因由通道 A 实测，两侧同源）：长任务书诱导长
  思维链吃满 ``max_tokens`` 时，线上回包是 ``content: null`` + ``finish_reason=length``；
  旧写法 ``content or "（空回复）"`` 把它**记成 final 且 rc=0**，与「正常完成」在
  trace 与退出码上完全无法区分，任何以 rc 判成功的自动化会被骗。现在 ``chat()``
  保留 finish_reason 与 usage，空回执记 ``EMPTY_REPLY`` 并走 finish_incomplete（rc=5）
  ——回执被截断这件事必须**喊出来**，不能靠指挥碰巧去读 reply.md 才发现。
  **别抬 ``--max_tokens`` 解空回执**（20260918 修订，实证作废旧建议）：思维链会随
  预算同步膨胀（18k→35k→57k 字三度耗尽），抬预算=更久的失败。处置见退出码 ``5``。
- **回执被截断也必须喊出来**（20260918 缺陷⑮，⑯ 的非空孪生）：非空但 `finish_reason=length`
  的回执旧版记 `final` + rc0、**无任何标注**（T2-2 实证：要 300 句、回 240 句、看不出来），
  而回报格式要求总结行放最后 ⇒ **截断处往往正好切掉总结行**。现在记 `RECEIPT_TRUNCATED`
  （**rc=6**），reply.md 首行 `[回执截断]` 并原样附上已收到的正文。
  **行为面另修**：回执上限从「回报格式的一条」升为**铁律 5**（与只读边界同级）——
  T2-6/T2-2 跨单对照显示，执行者对**安全类**规则会停下报冲突、对**格式类**规则照做不报，
  所以格式类里最要命的那一条必须提到与安全类同级，它才会被当成冲突而非偏好。

退出码（**别只看「非零=失败」就完事**——`4`/`5` 是协议/额度层的问题，
`3` 是预算层的问题，处置方式不同）：

    0  正常：拿到最终回执（reply.md = 执行者原文；**无产出锚点的多轮跑例外**——
       首行会多一段 [框架附注] 的「无产出锚点」喊话，见上「护栏」第 5 条）
    3  TIMEOUT / NOTURN：超时或用满轮次，没拿到回执（预算不够，拆小或加轮次）
    4  BADJSON：执行者两次发出非 JSON 的 arguments（协议错，多半是模型侧）
    5  EMPTY_REPLY：拿到的是空回执（**多半是思维链吃满 max_tokens**，处置按实证有效序：
       ①砍指令到纯判断+判据点名+输出字数上限（300 字案例成功）；②拆步走通道 C；
       ③别抬 --max-tokens——思维链随预算同步膨胀，抬了=更久的失败，见上）
    6  RECEIPT_TRUNCATED：拿到回执但**被输出额度截断**（finish_reason=length，正文非空）。
       与 5 分开：两种「回执不可信」要能互相区分，也要与干净回执可区分——否则
       「rc==0 ⇒ 成功」的自动化照样被骗（⑯ 同一条理由）。reply.md 首行为 `[回执截断]`，
       正文原样附在后面（已挣到的信息不丢）。**别抬 --max-tokens**，理由同 5。

坑位备忘（与 ds4f_step.py 同款）：显式禁代理；结果写文件、stdout 只打单行状态；
连接被拒 = SSH 隧道不在；HTTP 4xx 大概率是服务端模板不支持 tools（先怀疑这个）。
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

#: 与 ds4f_sparky.md / ds4f_step.py 一致；换端口只改这里。
URL = "http://127.0.0.1:8888/v1/chat/completions"
MODEL = "deepseek-v4-flash-0731"

TOOL_RESULT_CAP = 4000     # 单次工具回执进上下文的上限（128k 窗口，省着用）
READ_WINDOW = 6000         # read_file 单次窗口
MAX_TIMEOUT = 300          # run_command 超时上限（秒）
REQUEST_TIMEOUT = 300      # 单次模型请求超时（秒）
MAX_TOKENS = 8192          # 单次模型输出上限（**思维链也算在里面**，见 --max-tokens）

#: trace 里工具调用文本的**裁剪宽度**——不是进上下文的上限（那是 TOOL_RESULT_CAP），
#: 只是日志省地方。提成常量是因为 OTel 探针要判「这条内容是不是被裁过」
#: （`probes/otel/genai_spans.py`）：同一个数字两处写，迟早一边改了另一边不知道
#: （设计 §3.8 通则①）。
TRACE_ARGS_CLIP = 300
TRACE_RESULT_CLIP = 500

#: trace 的字段 schema 版本，**加字段就 +1**。
#: 理由（㉘，B2c 事故 20260920）：㉓ 埋点在那一单起跑后 3 分钟才落地 ⇒ 该单整份缺新字段，
#: 而下游读数器只会看到「空值」——与「真的是 0」症状完全一样（§3.12 通则二）。
#: 有版本号，读数器就能**拒绝**给低版本 trace 出数，而不是静默给 0。
#: v1 = 只有 turn/content/tool_calls/finish_reason/completion_tokens（B2c 及更早）
#: v2 = ㉓ 的 args_chars/result_chars/reasoning_*
#: v3 = 加整份 `usage`（OTel GenAI semconv 的 input/output tokens 要它）
TRACE_SCHEMA = 3

#: 挡的是「改变仓库状态」的命令片段，不是完备沙箱；漏网之鱼靠 trace 审计。
BLOCKLIST = ("git commit", "git push", "git reset", "git checkout", "git clean",
             "git revert", "git merge", "git rebase", "git restore", "git stash",
             "rm -rf", "rd /s", "rmdir /s", "del /f", "format ", "shutdown",
             "taskkill", "reg add", "reg delete")

def system_prompt(workdir: str, brief: str) -> str:
    """系统提示与项目解耦：仓库锚点来自 --workdir，项目规则来自 --brief。

    回报格式直接内置（自包含）：不传 --brief 时执行者照样能按同一格式交回执，
    这是对「别的项目忘带说明书」的兜底——通用契约不依赖某个项目的文件。
    """
    cdto = workdir.replace("\\", "/")
    lines = [
        "你是「执行者」，在 Windows + Git Bash 环境里为主会话（指挥）完成一个只交代一次的任务。铁律：",
        "",
        "1. 只做任务增量交代的事。任务增量与任何规则冲突时停下来，在回执里报告冲突，不要自行取舍。",
        "2. **不修改仓库文件**：禁止 git commit/push/reset/checkout/clean、删除、覆盖写。"
        "唯一例外：任务增量「输出写到：」点名的产出文件允许重定向写入——那是交付物，"
        "不是仓库改动；除此之外一律只读。与规则冲突时停下来报告，不要自行取舍。",
        f"3. 环境事实：每条命令的 cwd 都会重置，命令必须以 `cd {cdto} && ` 开头；"
        "Python 一律 `encoding=\"utf-8\"`，跑 CLI 前加 `PYTHONIOENCODING=utf-8`；"
        "不要动任务增量未点名的路径；不打印任何密钥。",
        f"4. 工具回执超过 {TOOL_RESULT_CAP} 字会被截断：宁可把命令收窄（tail、指定文件、"
        "单目录）也不要一次全量；`read_file` 按字符分页，用 offset 往后翻。",
        "5. **回执有硬上限**（与只读边界同级，不是格式偏好）：逐句/逐条/全文/大段产出"
        "**不许进回执**——写进「输出写到：」点名的产出文件，回执只放命令摘要、计数与断言结论。"
        "最终回执因超长被截断时，被切掉的往往正是最后那行总结（全文句数、未完成项），"
        "等于整份回执作废。**任务增量要求「回执里逐句列出全部…」时，这是与铁律的冲突**："
        "停下、在回执里报告冲突并说明「大段产出已写入产出文件」，不要照做。",
    ]
    if brief:
        lines.append(
            f"6. 本项目专属说明书在 `{brief}` ——**第一步先 read_file 读它**，"
            "环境事实与禁区以它为准。")
    lines += [
        "",
        "回报格式（最终回执，即不带工具调用的纯文本，必须逐项满足）：",
        "- 逐条列出跑过的命令：命令原文、退出码、关键输出（每条 ≤20 行，保留原文行）；",
        "- 结论必须带 `file:line` 或原文行支撑；禁止「应该」「大概」；**只引用你真实"
        "看到的行**，原文里没有的不要编；",
        "- 回执有输出上限、超长会被截断（铁律 5）：逐句清单等大段产出**写进「输出写到：」"
        "点名**的文件，回执只放命令摘要、计数与断言结论；",
        "- 失败时原样贴最后 30 行报错；最多自行重试一次，再败就停；",
        "- 最后一行总结：`完成 N/M 条；未完成的是第几条、原因一句话`。",
    ]
    return "\n".join(lines)

TOOLS = [
    {"type": "function", "function": {
        "name": "run_command",
        "description": "跑一条 shell 命令（Git Bash）。只读命令随便用；会改仓库状态的命令会被拒绝。cwd 每次重置，需要先 cd。",
        "parameters": {"type": "object", "properties": {
            "cmd": {"type": "string", "description": "整条命令，含必要的 cd"},
            "timeout_sec": {"type": "integer", "description": f"默认 120，上限 {MAX_TIMEOUT}"},
        }, "required": ["cmd"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": f"读一个文本文件（utf-8），按字符窗口分页，单次最多 {READ_WINDOW} 字。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "字符偏移，默认 0"},
        }, "required": ["path"]}}},
]

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 禁代理


def chat(messages: list[dict], max_tokens: int = MAX_TOKENS) -> tuple[dict, dict]:
    """返回 ``(message, meta)``；meta 保留 ``finish_reason``/``usage``/思维链字数。

    **为什么不能只返回 message**（20260917 缺陷⑯）：``finish_reason`` 是「回执有没有被
    截断」的唯一权威信号，旧写法把它丢在这里，主循环就只剩下 ``content`` 可看——而
    ``content: null``（思维链吃满额度）与「模型真没话说」在 ``content`` 上长得一模一样。
    丢一个字段，等于把两种处境抹成一种。
    """
    body = json.dumps({"model": MODEL, "messages": messages, "tools": TOOLS,
                       "tool_choice": "auto", "max_tokens": max_tokens,
                       "temperature": 0, "stream": False},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer local"})
    try:
        with OPENER.open(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"!! HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:400]}\n"
                         "   服务端不支持 tools 的话 L3 这条路就不通，先把这个原文给指挥。")
    except urllib.error.URLError as e:
        raise SystemExit(f"!! 连不上 {URL}（{e.reason}）——SSH 隧道不在了。")
    try:
        choice = data["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError):
        raise SystemExit("!! 响应没有 choices[0].message："
                         + json.dumps(data, ensure_ascii=False)[:400])
    meta = {
        "finish_reason": choice.get("finish_reason"),
        "usage": data.get("usage") or {},
        # 思维链的键名随服务端模板变（vLLM 用 reasoning_content，别家给 reasoning）
        "reasoning_chars": len(msg.get("reasoning_content")
                               or msg.get("reasoning") or ""),
    }
    return msg, meta


def clip(text: str, cap: int = TOOL_RESULT_CAP) -> str:
    """截到 cap 以内——**含截断标记本身**（T0.5-S6 实测：旧写法 2000+标记+2000 = 4026，
    超了 26 字，即 `cap` 声明了上限却管不住自己，与「8192 静默截断」同一物种：
    契约上写着有闸、闸却没关严）。标记长度随位数浮动，故先按预留切、再按实际长度削尾片。
    """
    if len(text) <= cap:
        return text
    half = max(0, (cap - 64) // 2)          # 64 = 截断标记的预留位
    head, tail = text[:half], text[-half:]
    note = f"\n…[中段截断 {len(text) - len(head) - len(tail)} 字；把命令收窄重跑]…\n"
    over = len(head) + len(note) + len(tail) - cap
    if over > 0:                            # 位数变大时再削尾片，保住标记完整
        tail = tail[over:]
    return head + note + tail


#: ── R3/R4 护栏辅助（20260916 反馈：空产出暴露、重复轮提示、收尾硬闸）──────────
WRITE_TARGET_RE = re.compile(r"(?:>>|2>|&>|>)\s*([^\s;|&\"']+)"
                             r"|\btee\s+(?:-a\s+)?([^\s;|&\"']+)")


def write_targets(cmd: str) -> list[str]:
    """启发式抠出命令里的「写文件」目标（> / >> / 2> / &> / tee），供写后回显大小。

    先剥引号段再扫：python -c "…" 的正文里常有 '>' 起头的领域词（如「>60天」），
    不剥会把正文当重定向目标。含 CJK/全角的目标也跳过——本环境命令路径一律 ASCII，
    引号外出现中文基本是正文残段而非路径。
    空设备按**小写**比对：Windows 上 `> NUL` 大小写等价，漏了会误报「写入失败」
    （T0-5 实测：`echo x > NUL` 曾回显 `NUL 不存在 [!! 写入失败]`）。
    """
    stripped = re.sub(r"\"[^\"\n]*\"|'[^'\n]*'", " ", cmd)
    out: list[str] = []
    for m in WRITE_TARGET_RE.finditer(stripped):
        t = (m.group(1) or m.group(2)).strip("\"'")
        if (t and t.lower() not in ("/dev/null", "nul") and not t.startswith("$")
                and not re.search(r"[一-鿿：（），。；／]", t)
                and t not in out):
            out.append(t)
    return out


#: Windows 上「只有 bash 说得清落在哪」的写目标形态（㉙）：
#:   `/tmp/x`       —— MSYS 根，映射到 %TEMP%，**不是** `C:\tmp`
#:   `/c/Users/...` —— MSYS 盘符形式
#:   `~/x`          —— bash 展开家目录（任何平台上 `Path` 都不会展开 `~`）
#: 共同点 = 拿 Windows 的 `Path` 规则既判不出它们是绝对的、也解不对它们指向哪。
BASH_PATH_RE = re.compile(r"^(?:/|~)")


def is_bash_path(tgt: str) -> bool:
    """这个写目标是不是「由 bash 解释、Windows 侧 stat 不到同一个文件」的形态。"""
    if tgt.startswith("~"):
        return True          # `~` 哪个平台上都只有 shell 会展开
    return os.name == "nt" and tgt.startswith("/")


def bash_stat(targets: list[str], workdir: str) -> dict[str, tuple[bool, int | None]]:
    """用**跑这条命令的同一个 bash** 判这些目标存不存在、多大。

    ㉙ 修法（20260920，真模型通道 C 的 L4 t3 实证）：旧写法把 `write_targets` 抠出来的
    目标**一律**交给 `Path`，而 Windows 上 `Path(workdir) / "/tmp/x"` 得 `C:\\tmp\\x`
    （MSYS 的 `/tmp` 其实在 %TEMP%）⇒ 回显打 `[!! 写入失败]`，**而那一次真落盘了 8476 字节**。
    按本项目自己的口径（㉔ 注释：**误导性提示比无效提示更坏**）这条比沉默更坏：
    它告诉执行者「写失败」而其实成功，真模型于是又磨了一轮。

    命令是 bash 跑的，那段路径语义就只有 bash 说了算 ⇒ 这里**不重写一份** bash 的路径
    规则，直接问它（§3.8 通则① 同一条：同一件事不两处写）。一次往返问全部目标。

    返回 `{目标: (存在, 字节数)}`；**没回来的目标不在字典里** ⇒ 调用方按「无法判定」
    处理，**不许当成失败**。目录返回 `(True, None)`（`wc -c` 对目录没意义）。
    """
    if not targets:
        return {}
    bash = shutil.which("bash")
    if not bash:
        return {}
    script = ('for t in "$@"; do '
              'if [ -d "$t" ]; then printf "%s\\t1\\t-\\n" "$t"; '
              'elif [ -e "$t" ]; then '
              'printf "%s\\t1\\t%s\\n" "$t" "$(wc -c < "$t" 2>/dev/null || echo -)"; '
              'else printf "%s\\t0\\t0\\n" "$t"; fi; done')
    try:
        p = subprocess.run([bash, "-lc", script, "_", *targets], cwd=workdir,
                           capture_output=True, timeout=20,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    except (OSError, subprocess.TimeoutExpired):
        return {}
    res: dict[str, tuple[bool, int | None]] = {}
    for line in smart_decode(p.stdout or b"").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        try:
            size: int | None = int(parts[2])
        except ValueError:
            size = None                      # 目录 / `wc` 没吐数：存在，但大小不可知
        res[parts[0]] = (parts[1] == "1", size)
    return res


def _write_echo(tgt: str, exists: bool, size: int | None) -> str:
    """写后回显的统一措辞（R3a）。四种状态**必须可区分**：

    不存在（失败）／存在但 0 字节（十有八九失败）／存在且有大小／**大小读不到**。
    最后一档不许并进「失败」——㉙ 的教训正是一条**误导性的失败**。
    """
    if not exists:
        return f"\n[写文件回显] {tgt} 不存在 [!! 写入失败]"
    if size is None:
        return f"\n[写文件回显] {tgt} 存在（是目录或大小读不到）"
    mark = "" if size else "  [!! 空文件：写入很可能失败]"
    return f"\n[写文件回显] {tgt} = {size} bytes{mark}"


def result_is_empty(result: str) -> bool:
    """工具结果是否「实质为空」：run_command 只剩 exit= 行、read_file 只剩表头。"""
    body = re.sub(r"^exit=\d+\s*", "", result.strip())
    body = re.sub(r"^\[[^\]]*\]\s*$", "", body, flags=re.M)
    return not body.replace("（已到文件末尾）", "").strip()


def norm_cmd(cmd: str) -> str:
    """命令规范化（压空白）：供「与上轮相同」判定，宽匹配而非精确去重。"""
    return " ".join(cmd.split())


def is_emit_call(name: str, args_text: str, out_name: str) -> bool:
    """这一次工具调用是不是**在产出**：它把声明的产出文件当成了**写入目标**。

    ㉔ 修法（20260920，B2c 实证）：guard 的「磨循环」判据原先是**连续带工具调用的轮数**
    （`streak += 1`，只有「纯文本最终回执」那一支清零），**完全不看这些轮里有没有产出**
    ⇒ 对一个每轮都在 append 产出文件的**正常长任务** 100% 误报：B2c 26 轮里响了 **8 次**，
    第 24 轮那次 564/564 已经一条不差地写完，它还在劝执行者「把已有结论写入输出文件」。
    而 B1b（同族真成功那次）也吃了 2 次假阳。**误导性提示比无效提示更坏**：
    若 B2c 服从第 3 轮那次，产出就停在 58/564。

    缺陷⑳ 修法（20260920，真模型通道 C 实证）：㉔ 把判据换成了「这一轮有没有产出调用」，
    但**实现的判据是纯子串** `out_name in args_text` ⇒ `read_file(path=out.md)`、
    `cat out.md`、`ls out.md` 参数里**一定含这个名字**，全都算「产出调用」并清零 streak。
    真模型 L1 六轮一声不响，其中 t4/t5 **连着两轮读自己刚写坏的 6 字节产出文件**
    ——那正是护栏该压的形态。**这是 ⑬ 家族第四例**（护栏全都浅＝模式匹配而非语义判定），
    也**是 ㉔ 那次修的代价**（假阳清零的同时把「读产出」误记成「产出」）。

    ⇒ 判据从「文本里出现过这个名字」收窄为「这个名字出现在**写入位**」：
      · `run_command`：`write_targets(cmd)` 里有同名目标（`>`/`>>`/`2>`/`&>`/`tee`）；
      · 其余工具（`read_file` 等）：**一律不算**——读不是产出。

    名字粒度**保持 basename 匹配**（㉔ 已标定，本次不动）：执行者常写相对路径
    （`>> B2_rows.jsonl`）而锚点是绝对路径，整路径匹配会漏成假阳。

    **代价要说清**：这里只认重定向/tee 这一种写法，`python -c "open('out.md','w')"`
    这类静态认不出 ⇒ **本函数不是 guard 的唯一判据**，另有语义判据 `out_stamp`
    （问文件动没动，覆盖一切写法）。本函数仍是**离线分类器**的唯一定义
    （`b2_run_audit.py` 直接 import，§3.8 通则①）。

    ⚠ 签名 20260920 变更：新增首参 `name`（旧签名只有 args_text/out_name，因为旧判据
    根本不看是哪个工具）。离线相位审计的调用点同批更新。
    """
    if not out_name or name != "run_command":
        return False
    try:
        a = json.loads(args_text) if isinstance(args_text, str) else args_text
    except ValueError:
        return False
    if not isinstance(a, dict):
        return False
    return out_name in [Path(t).name for t in write_targets(str(a.get("cmd") or ""))]


def out_stamp(path: str) -> tuple[int, int] | None:
    """产出文件的 `(字节数, mtime_ns)`；不存在/读不到 → `None`。

    「这一轮有没有产出」的**语义判据**（缺陷⑳ 修法）。不问命令文本长什么样，只问**文件
    动没动**——于是 `>>`、`tee`、`python -c "open(...)"`、heredoc 全都覆盖，静态分类器
    认不出的写法在这里一视同仁。size 与 mtime_ns 一起取：覆写成同样长度也要能看出来。

    没有产出锚点时 `path` 为空 ⇒ 恒 `None` ⇒ 判据自动退化成「静态分类器说了算」，
    与 ㉔ 标定的旧口径一致（S2 断言 gt==[3,6] 正是这一支）。
    """
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


#: 「输出写到：」的**规范形态**——短语 + 冒号 + 一个可解析的路径 token。
#: 缺陷⑭ 修法（20260918）：**豁免判定（hook G3）与完成判定（本函数）必须同一把尺**。
#: 只认这一种形态；近义词（「结果放：」等）一律不认，且两侧**同时在**两个时点暴露
#: ——分发时（G3 硬拒并明说要哪种写法）与收尾时（无锚点喊话，见 no_out_anchor）。
#: 不同义词表：那是 ⑬ 家族的无底洞（护栏全都浅 = 模式匹配而非语义判定）。
#: hook 侧 route_gate.task_out_path() 直接 import 本函数，从结构上消灭漂移。
#:
#: 缺口13（20260921）：**提到≠声明**。旧正则对短语在文中任何位置都命中，两个实测
#: 后果：①探针任务书里句中**提及**（哪怕否定句「不要输出写到：」）被判成声明，
#: 闸放行、真跑起来白烧一次调用（20260920 夜实证）；②真实任务书大量句中引用
#: （「只许写「输出写到：」点名的 out.md」）全靠「真锚点恰好排在前面」的运气才没
#: 解析出 `」点名的` 这种垃圾路径。修法＝**行首独行**才算声明；判据收紧不许补
#: 近义词（⑬ 那口井），两个时点（G3 硬拒/收尾喊话）的暴露面不变。
OUT_FILE_RE = re.compile(r"^[ \t]*输出写到[：:]\s*(\S+)", re.M)

#: 无锚点回执附注（⑭）。措辞直说后果，不说「可能有问题」——静默失效正是缺陷本体。
NO_ANCHOR_NOTE = (
    "[无产出锚点] 任务书没有可解析的「输出写到：<路径>」声明（近义词如「结果放：」不算）。"
    "本次运行**没有完成判定锚点**：收尾硬闸未注入、产出缺失检测恒为假——"
    "两条护栏一起失效，且「完成」二字无从核验。"
    "本该有产出文件时，请补规范写法后重发；纯调查单请用 --max-turns 1 显式声明单轮。")


def parse_out_file(goal_text: str) -> str:
    """从任务书解析声明的产出文件（「输出写到：」行）；没有就返回空串。"""
    m = OUT_FILE_RE.search(goal_text or "")
    return m.group(1).strip("\"'") if m else ""


def smart_decode(raw: bytes) -> str:
    """utf-8 为主，替换符超 1% 再试 gbk（cmd 原生工具会吐 gbk）。"""
    text = raw.decode("utf-8", "replace")
    if text.count("\ufffd") > max(4, len(text) // 100):
        try:
            text = raw.decode("gbk", "replace")
        except UnicodeDecodeError:
            pass
    return text


def run_command(cmd: str, timeout_sec: int, workdir: str) -> str:
    for bad in BLOCKLIST:
        if bad in cmd:
            return (f"[拒绝] 命令含被禁片段「{bad}」：执行者无权改变仓库状态。"
                    "只读命令（log/show/diff/ls/grep/测试）照常可用。")
    timeout_sec = max(5, min(int(timeout_sec or 120), MAX_TIMEOUT))
    bash = shutil.which("bash")
    argv = [bash, "-lc", cmd] if bash else ["cmd", "/c", cmd]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run(argv, cwd=workdir, capture_output=True,
                           timeout=timeout_sec, env=env)
    except subprocess.TimeoutExpired:
        return f"[超时] {timeout_sec}s 未结束，已终止。"
    text = smart_decode((p.stdout or b"") + (p.stderr or b""))
    # R3a：写文件后自动回显目标大小——空文件当场暴露（20260916-02 的 T4 教训）
    # ㉙：**分两路判**。bash 形态的目标（`/tmp/x`、`/c/...`、`~/x`）交给跑这条命令的
    # 同一个 bash（`bash_stat`，一次往返问全部），其余仍走 Windows 的 `Path`——不为一条
    # 边角给每次带重定向的命令都加一次子进程。**两路都可能判不出来**，那时说「无法判定」，
    # 不说不存在的那个「失败」。
    targets = write_targets(cmd)
    bstats = bash_stat([t for t in targets if is_bash_path(t)], workdir) if bash else {}
    for tgt in targets:
        if is_bash_path(tgt):
            if tgt in bstats:
                text += _write_echo(tgt, *bstats[tgt])
            else:
                text += (f"\n[写文件回显] {tgt} ：**无法判定**——这是 Git Bash 形态的路径，"
                         "本机 bash 不可用或没回话，框架不知道自己 stat 到的会不会是另一个文件。")
            continue
        fp = Path(tgt)
        if not fp.is_absolute():
            fp = Path(workdir) / tgt
        try:
            text += _write_echo(tgt, True, fp.stat().st_size)
        except OSError:
            text += _write_echo(tgt, False, None)
    return clip(f"exit={p.returncode}\n{text}")


def read_file(path: str, offset: int, workdir: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = Path(workdir) / p
    if not p.exists():
        return f"[错误] 文件不存在：{p}"
    if p.stat().st_size > 20 * 1024 * 1024:
        return "[错误] 文件超过 20MB：用 run_command 分段看（tail/sed -n）。"
    data = p.read_bytes().decode("utf-8", "replace")
    offset = max(0, int(offset or 0))
    window = data[offset:offset + READ_WINDOW]
    head = f"[{p.name} 共 {len(data)} 字，本段 {offset}–{min(offset + READ_WINDOW, len(data))}]"
    return head + "\n" + (window or "（已到文件末尾）")


def dispatch(name: str, args: dict, workdir: str) -> str:
    if name == "run_command":
        return run_command(str(args.get("cmd", "")),
                           args.get("timeout_sec") or 120, workdir)
    if name == "read_file":
        return read_file(str(args.get("path", "")), args.get("offset") or 0,
                         workdir)
    return f"[错误] 未知名为 {name} 的工具，可用：run_command / read_file。"


def main() -> None:
    ap = argparse.ArgumentParser(description="DGX 逐工具调用循环（L3）")
    ap.add_argument("task", help="任务增量 .md")
    ap.add_argument("--workdir", required=True,
                    help="仓库绝对路径（执行者命令的工作锚点，必填）")
    ap.add_argument("--brief", default="",
                    help="可选：项目专属说明书；不传则用内置通用规则与回报格式")
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS,
                    help=f"单次输出上限（思维链也算在里面；默认 {MAX_TOKENS}）")
    ap.add_argument("--deadline-min", type=int, default=20)
    ap.add_argument("--out", default="")
    ap.add_argument("--out-file", default="",
                    help="声明的最终产出文件（R4 收尾硬闸用）；缺省=从任务书「输出写到：」解析")
    ap.add_argument("--lock", nargs="?", const="", default=None,
                    help="单飞锁（DGX 并发=1）：给值=指定锁文件，不给值=默认共享锁")
    ap.add_argument("--wait", type=int, default=0,
                    help="锁被占时排队等待的秒数（默认 0=立即报忙）")
    args = ap.parse_args()

    out = Path(args.out) if args.out else (
        Path(os.environ.get("TEMP", os.environ.get("TMP", "/tmp")))
        / f"dgx_loop_{datetime.now().strftime('%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)

    gate = None
    if args.lock is not None:        # P1：单飞锁（DGX 并发=1），见 ds4f_gate.py
        try:
            import ds4f_gate
        except ImportError:
            raise SystemExit("!! --lock 需要同目录有 ds4f_gate.py。")
        lock_path = args.lock or ds4f_gate.DEFAULT_LOCK
        gate, holder = ds4f_gate.acquire(lock_path, args.wait)
        if gate is None:
            raise SystemExit(f"!! DGX 忙：锁 {lock_path} 被进程 PID {holder} 持有"
                             "（--wait N 排队，或稍后再发）")
        atexit.register(gate.close)  # 循环里多处 sys.exit，交给 atexit 统一释放

    def log(ev: str, **kw) -> None:
        rec = {"t": datetime.now().isoformat(timespec="seconds"), "event": ev, **kw}
        with open(out / "trace.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    goal = open(args.task, encoding="utf-8").read()
    # R4：产出文件 = 显式 --out-file 或任务书「输出写到：」；相对路径锚在 workdir
    out_file = args.out_file or parse_out_file(goal)
    if out_file:
        ofp = Path(out_file)
        if not ofp.is_absolute():
            ofp = Path(args.workdir) / ofp
        out_file = str(ofp)
    messages: list[dict] = [{"role": "system",
                             "content": system_prompt(args.workdir, args.brief)},
                            {"role": "user", "content": goal}]
    end_at = datetime.now().timestamp() + args.deadline_min * 60
    n_tools = bad_json = 0
    prev_norm = ""           # R3b：上一轮 run_command 的规范化串
    streak = 0               # R3b：连续发起工具调用的轮数
    ending_injected = False  # R4：收尾硬闸只注入一次
    recent: list[str] = []   # R4：最近三轮各在干什么（异常退出摘要用）
    # ⑭ 框架侧独立防御（20260918）：多轮跑而**没有可解析的产出一锚点**时，
    # 收尾硬闸不注入、产出缺失检测恒为假——两条护栏一起静默失效。这**不能靠 hook
    # 兜**：hook 只拦「分发时」，而 settings.json 已被宿主整体抹掉过 4 次（R5）；
    # 且 --max-turns 1 是合法豁免路径。所以框架自己也要喊（单轮豁免，不算异常）。
    no_anchor = (not out_file) and args.max_turns > 1
    #: 产出文件**文件名**：guard 靠它判「这一轮有没有在产出」（㉔，见 is_emit_call）。
    #: 用文件名而不是整条路径：执行者常写相对路径（`>> B2_rows.jsonl`），
    #: 整路径匹配会漏 ⇒ 又变成「假阳」。文件名足够，且与相位审计同口径。
    out_name = Path(out_file).name if out_file else ""
    # trace_schema：让读数器能区分「老 trace 没这个字段」与「这个字段真的是 0」（㉘ 修法）。
    log("start", out_file=out_file or "（任务书未声明）", no_out_anchor=no_anchor,
        trace_schema=TRACE_SCHEMA)
    if no_anchor:
        log("no_out_anchor", max_turns=args.max_turns)

    def finish_incomplete(reason: str, code: int, tag: str, label: str = "[未完成]",
                          partial: str = "", **extra) -> None:
        """R4：异常收尾回执；声明的产出文件缺失时附最后三轮摘要，省得指挥翻 trace。

        第 1 轮就中止时 recent 还是空的（例：空回执）：那时只报缺失、不甩一个空的
        「最后三轮：」标题——空标题看着像信息，其实只是噪声。

        `partial`（⑮）：被截断的回执**有正文**，正文必须随回执交回（那是执行者已经
        挣到的信息），但要用分隔线标明「到此为止不完整」，免得下半段被当成正常结尾。
        """
        missing = bool(out_file) and not Path(out_file).exists()
        lines = [f"{label} {reason}"]
        if no_anchor:
            lines.append(NO_ANCHOR_NOTE)
        if missing:
            lines.append(f"[输出缺失] 任务书声明的产出文件 {out_file} 不存在。")
            if recent:
                lines.append("最后三轮：")
                lines += [f"- {r}" for r in recent[-3:]]
        if partial:
            lines += ["", "--- 以下为执行者原文（**在输出额度处被截断，末尾不完整**）---",
                      partial]
        (out / "reply.md").write_text("\n".join(lines), encoding="utf-8")
        log(tag, out_file_missing=missing, **extra)
        print(f"{tag} out={out}")
        sys.exit(code)

    for turn in range(1, args.max_turns + 1):
        if datetime.now().timestamp() > end_at:
            finish_incomplete(f"超过时限 {args.deadline_min} 分钟，在第 {turn} 轮前中止。",
                              3, "TIMEOUT")
        # R4：收尾硬闸——剩余 ≤2 轮（或时限 <3 分钟）时强制转向「先写产出」
        remaining = args.max_turns - turn + 1
        time_left = end_at - datetime.now().timestamp()
        if out_file and not ending_injected and (remaining <= 2 or time_left < 180):
            messages.append({"role": "user", "content":
                f"[收尾硬闸] 剩余 {remaining} 轮 / 约 {max(0, int(time_left))} 秒："
                f"立即把已有结论写入 {out_file}，然后给出最终回执。不要再发起新的调查。"})
            log("ending_gate", turn=turn, remaining=remaining)
            ending_injected = True
        msg, meta = chat(messages, args.max_tokens)
        calls = msg.get("tool_calls") or []
        content = msg.get("content") or ""
        usage = meta.get("usage") or {}
        log("assistant", turn=turn, content=content,
            tool_calls=[c.get("function") for c in calls],
            finish_reason=meta.get("finish_reason"),
            completion_tokens=usage.get("completion_tokens"),
            # ㉓（20260920 B2 事故）：思维链**一个字都不落 trace** 时，事后只能看到
            # 「第 8/9/10 轮命令一模一样」，**为什么**却不可考（推理不在回执里）。
            # 全存会撑爆 trace（实测单轮 15k+ 字），故存**首尾定长摘录**：
            # 收尾处是它下决心的地方，开头是它怎么理解任务的地方——失败的动机在这两头。
            reasoning_chars=meta.get("reasoning_chars"),
            # OTel GenAI semconv 映射（20260920 探针，trace_schema v3）：semconv 要
            # `gen_ai.usage.input_tokens` / `output_tokens`，而旧 trace 只留 completion_tokens
            # ⇒ input 侧**永远填不上**（进上下文多少 = 我们最贵的那个量，恰好丢了）。
            # 纯附加字段，不改任何判定。
            usage=usage,
            reasoning_head=(msg.get("reasoning_content") or msg.get("reasoning") or "")[:200],
            reasoning_tail=(msg.get("reasoning_content") or msg.get("reasoning") or "")[-300:])
        if not calls:                                   # 纯文本 = 最终回执
            streak = 0
            if not content.strip():
                # 缺陷⑯：空回执**不是**完成。线上这一档是 content=null + finish_reason=length
                # （思维链吃满 max_tokens）。旧写法 `content or "（空回复）"` 会记 final + rc0，
                # 把「被截断」伪装成「已完成」，以 rc 判成功的自动化全被骗。
                reason = meta.get("finish_reason")
                rchars = meta.get("reasoning_chars") or 0
                why = ("多半是思维链吃满了输出额度" if reason == "length"
                       else "回包里既没有工具调用也没有正文")
                finish_incomplete(
                    f"第 {turn} 轮返回空回执（finish_reason={reason}，思维链 {rchars} 字，"
                    f"max_tokens={args.max_tokens}）：{why}——处置按实证有效序："
                    "①砍指令到纯判断+判据点名+输出字数上限（20260918 盈亏会话 300 字案例成功）；"
                    "②拆步走通道 C（工具循环天然分步，思维链不再一杆撑满）；"
                    "③别抬 --max-tokens——思维链会随预算同步膨胀"
                    "（18k→35k→57k 字三度耗尽实证），抬了=更久的失败。",
                    5, "EMPTY_REPLY",
                    finish_reason=reason, reasoning_chars=rchars)
            if meta.get("finish_reason") == "length":
                # 缺陷⑮（20260918）：回执**非空但被输出额度截断** = 静默截断。T2-2 实证
                # 「300 句只回 240 句、无任何标注」，而回报格式要求总结行放最后 ⇒
                # **截断处往往正好切掉总结行**，即最要紧的那部分。
                # 与空回执分开记：rc=5（啥也没有）/ rc=6（有一半）是两种不同的不可信，
                # 但都必须与「干净回执」可区分——否则「rc==0 ⇒ 成功」的自动化照样被骗
                # （⑯ 的同一条理由）。正文随回执交回，不放丢掉已挣到的信息。
                finish_incomplete(
                    f"第 {turn} 轮回执被输出额度截断（finish_reason=length，正文 "
                    f"{len(content)} 字，max_tokens={args.max_tokens}）：**回执不完整，"
                    "总结行多半已被切掉**，别把它当成执行者自己写完了。处置按实证有效序："
                    "①大段产出改写入「输出写到：」点名的产出文件，回执只留结论+数字"
                    "（这也是回执格式本来就要求的）；②砍指令到纯判断+判据点名+输出字数上限；"
                    "③别抬 --max-tokens——思维链会随预算同步膨胀，抬了只是在更靠后的位置再断一次。",
                    6, "RECEIPT_TRUNCATED", label="[回执截断]",
                    finish_reason=meta.get("finish_reason"), content_chars=len(content),
                    partial=content)
            # ⑭：框架附注置于回执**最前**（指挥从头读起），并明确标成框架加的，
            # 免得被当成执行者原文；无锚点时「完成」不可核验，这件事必须随回执走。
            note = f"[框架附注] {NO_ANCHOR_NOTE}\n\n---\n\n" if no_anchor else ""
            (out / "reply.md").write_text(note + content, encoding="utf-8")
            log("final", turns=turn, tool_calls=n_tools, no_out_anchor=no_anchor)
            print(f"FINAL turn={turn} tools={n_tools} out={out}")
            sys.exit(0)
        messages.append(msg)
        round_cmds = [str((tc.get("function") or {}).get("arguments") or "")
                      for tc in calls
                      if (tc.get("function") or {}).get("name") == "run_command"]
        dup = bool(round_cmds) and norm_cmd(round_cmds[-1]) == prev_norm
        if round_cmds:
            prev_norm = norm_cmd(round_cmds[-1])
        # ㉔ + ⑳：产出判据的**语义层**要在工具真的跑之前取样（见下面 emit_this_turn）。
        stamp_before = out_stamp(out_file)
        last_result = ""
        for tc in calls:
            fn = tc.get("function", {})
            raw = fn.get("arguments") or "{}"
            try:
                fn_args = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(fn_args, dict):
                    raise ValueError
                bad_json = 0
            except ValueError:
                bad_json += 1
                if bad_json >= 2:
                    finish_incomplete("arguments 连续两次不是 JSON 对象（协议错）",
                                      4, "BADJSON")
                result = "[错误] arguments 不是合法 JSON 对象，请重发该调用。"
            else:
                result = dispatch(str(fn.get("name")), fn_args, args.workdir)
                n_tools += 1
            last_result = result
            # ㉓（20260920 B2 事故）：`result=result[:500]` 只有前 500 字，而**长度**
            # 恰恰是「搬进上下文多少原文」的唯一度量（B 杠杆的自变量）⇒ 旧 trace 只能给
            # 「run_command N 次 ⇒ 上界 N×4000」这种区间，量不出真值，报告的结论就没底气。
            # `dispatch` 返回的**已经是** clip 过的字符串（run_command 4000 / read_file 6000
            # 每段），所以 `len(result)` 就是执行者实际收到的字数——精确值，不是上界。
            log("tool_result", turn=turn, name=fn.get("name"),
                args=raw[:TRACE_ARGS_CLIP], result=result[:TRACE_RESULT_CLIP],
                args_chars=len(raw), result_chars=len(result))
            messages.append({"role": "tool",
                             "tool_call_id": tc.get("id") or f"c{n_tools}",
                             "content": result})
        # ㉔ + ⑳：streak = **连续「没有产出」的轮数**（旧名「连续带工具调用的轮数」）。
        # 有产出就清零——否则正常长任务（每轮 append 产出文件）会被当成磨循环，
        # 且建议恰好是错的（B2c 8 次假阳实证，见 is_emit_call 的注释）。
        # 判据**两层取或**（缺陷⑳ 修法，20260920）：
        #   ①**语义层**（权威）：产出文件的 (size, mtime_ns) 这一轮变没变——不问命令文本
        #     长什么样，只问文件动没动 ⇒ 覆盖一切写法（`>>`/`tee`/heredoc/python -c）；
        #   ②**静态层**：is_emit_call 认「写目标位」；这一条同时是 b2_run_audit 离线复用
        #     的唯一定义，且在没有产出锚点（out_file 为空 ⇒ out_stamp 恒 None）时是唯一判据。
        # 旧口径是纯子串 `out_name in args_text` ⇒ read_file/cat/ls 产出文件都算产出并清零
        # streak：真模型 L1 六轮一声不响、t4/t5 连着两轮读自己刚写坏的 6 字节文件——⑬ 家族
        # 第四例，也是 ㉔ 那次修的代价。**两层取或**是为了不让收窄造出新的假阳：只认重定向
        # 会让 `python -c "open('out.md','w')"` 这类写法被判成「没产出」，那正是 ㉔ 要消灭的。
        emit_this_turn = (out_stamp(out_file) != stamp_before) or any(
            is_emit_call(str((tc.get("function") or {}).get("name") or ""),
                         str((tc.get("function") or {}).get("arguments") or ""), out_name)
            for tc in calls)
        streak = 0 if emit_this_turn else streak + 1
        # R3b：空产出 / 与上轮重复 / 磨循环 → 提示附在最后一个工具结果后，下一轮可见
        if result_is_empty(last_result):
            hint = "[护栏提示] 上一步无实质产出：换方法，或直接把已有结论写入输出文件。"
        elif dup:
            hint = "[护栏提示] 本轮命令与上轮相同：换方法，或直接把已有结论写入输出文件。"
        elif streak >= 3 and streak % 3 == 0:
            # ㉔：3/6/9…提醒一次。旧注释写「正常长任务不受噪」是**错的**——旧判据下
            # 每个长任务都会响；现在只在**连续 3 轮没有任何产出调用**时才响。
            hint = (f"[护栏提示] 已连续 {streak} 轮没有写入产出文件：确认不是在磨循环，"
                    "把已有结论写入输出文件或给出最终回执。")
        else:
            hint = ""
        if hint:
            messages[-1]["content"] += "\n" + hint
            log("guard", turn=turn, hint=hint)
        recent.append(f"轮{turn}：" + "；".join(
            f"{(tc.get('function') or {}).get('name')}:"
            f"{str((tc.get('function') or {}).get('arguments') or '')[:60]}"
            for tc in calls))
        recent[:] = recent[-3:]

    finish_incomplete(f"用满 {args.max_turns} 轮仍没有给出最终回执，trace.jsonl 里有全过程。",
                      3, "NOTURN")


if __name__ == "__main__":
    main()
