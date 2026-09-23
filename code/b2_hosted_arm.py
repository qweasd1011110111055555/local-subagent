#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""托管臂启动器（B2「办法自便」× 托管端点）——测试侧注入，不改生产代码。

背景：本地 DGX 通道三次「零产出」（B1/B2b/B2w，20260920），根因假设收窄到
「模型×脚手架」复合性质；用户的托管证据（同款模型在官方 harness 下完成过整批
项目）把「脚手架差异」变成活假设。本件把同一份任务书、同一个 ds4f_loop 客户端
打到 DeepSeek 托管 API，分离 harness（本地 vs 托管 flash）与代际（flash vs pro）。

为什么走本地反向代理：ds4f_loop.chat() 的 ``Authorization`` 写死 ``Bearer local``
（:203-204），托管端点要真 key。key 只从凭据文件读、只进代理进程——
不进环境变量、不进生产件、不进本文件正文（照 settings.json token 同一纪律）。
ds4f_loop 侧照 loop_under_stub 哲学：只换 ``URL``/``MODEL`` 两个常量，
main() 以下（锁/轮次/护栏/trace/回执）全是真代码，一行未动。
托管臂**不取 --lock**（共享 DGX 并发=1 的锁只属于本地通道，托管不打那台机器）。

用法::

    python b2_hosted_arm.py --selftest
    python b2_hosted_arm.py <model-id> -- <ds4f_loop 参数...>

自证（成对接入验收）：正例 = 经代理 GET /models 必须 200 且列出托管 id；
负对照 = 经代理请求不存在的模型，上游 4xx 必须原样透传（证明代理不掩错误）。
代理每请求往 stderr 打一行：状态/耗时/上游回包 model 字段/usage tokens
——不打 key、不打对话内容。
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

KEY_FILE = Path(__file__).resolve().parent.parent / "hosted_deepseek.key"
UPSTREAM = "https://api.deepseek.com"

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 显式禁代理


def _forward(method, path, body, auth):
    req = urllib.request.Request(UPSTREAM + path, data=body, method=method, headers={
        "Content-Type": "application/json", "Authorization": auth})
    try:
        with _OPENER.open(req, timeout=280) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class _Handler(BaseHTTPRequestHandler):
    def _do(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        t0 = time.monotonic()
        status, resp = _forward(method, self.path, body,
                                "Bearer " + KEY_FILE.read_text(encoding="utf-8").strip())
        ms = int((time.monotonic() - t0) * 1000)
        note = ""
        if resp[:1] == b"{":
            try:
                j = json.loads(resp.decode("utf-8"))
                u = j.get("usage") or {}
                note = (f"model={j.get('model')} "
                        f"ptok={u.get('prompt_tokens')} ctok={u.get('completion_tokens')}")
            except Exception:
                pass
        print(f"[proxy] {method} {self.path} -> {status} {ms}ms {note}",
              flush=True, file=sys.stderr)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def do_GET(self):
        self._do("GET")

    def do_POST(self):
        self._do("POST")

    def log_message(self, *a):  # 静默默认访问日志（上面已打单行）
        pass


def start_proxy():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)  # 0=临时端口
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def selftest():
    port = start_proxy()
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # 正例
    with op.open(f"http://127.0.0.1:{port}/models", timeout=30) as r:
        ids = [m.get("id") for m in json.loads(r.read().decode())["data"]]
        assert r.status == 200 and ids, ids
    print(f"selftest positive OK: {ids}")
    # 负对照：不存在的模型，上游 4xx 必须透传
    body = json.dumps({"model": "nonexistent-model-xyz",
                       "messages": [{"role": "user", "content": "ping"}],
                       "max_tokens": 4}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/chat/completions",
                                 data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        op.open(req, timeout=60)
        print("selftest NEGATIVE FAILED: expected upstream 4xx")
        return 1
    except urllib.error.HTTPError as e:
        print(f"selftest negative OK: upstream {e.code} propagated")
    return 0


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        raise SystemExit(selftest())
    if len(sys.argv) < 3 or "--" not in sys.argv:
        raise SystemExit("用法：python b2_hosted_arm.py <model-id> -- <ds4f_loop 参数...>")
    model = sys.argv[1]
    argv = sys.argv[sys.argv.index("--") + 1:]

    ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(ROOT))
    import ds4f_loop                                          # noqa: E402
    port = start_proxy()
    ds4f_loop.URL = f"http://127.0.0.1:{port}/v1/chat/completions"
    ds4f_loop.MODEL = model
    sys.argv = ["ds4f_loop.py"] + argv
    ds4f_loop.main()


if __name__ == "__main__":
    main()
