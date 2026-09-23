# local-subagent：DGX 子模型「空回执」现象调查（托管 A/B，20260923）

给同事的因析包：本地 DGX（:8888，deepseek-v4-flash-0731 @ vLLM）上跑委托通道时，执行模型
三次「零产出」（B1/B2b rc=5 思维链吃满预算、B2w rc=3 轮次退化循环）。本批把**同一份任务书、
同一个客户端（code/ds4f_loop.py，零改动，仅换端点/模型两常量）**打到 DeepSeek 托管 API，
分离 serving 栈 / 模型代际 / 客户端包络三个变量。

## 一句话结论

**两堵墙都在 HTTP 请求里，不在机架上**：托管 flash 别名（believed v4.1）在 DeepSeek 自家
serving 上逐字复刻本地 rc=5（思维链 23,853 字撞死客户端发的 `max_tokens=8192` ⇒ content 空）；
托管 v4-pro 形式 rc=3（12 回合用尽）但交付物已写完 = **564/564 逐位全中**（data/ 对地板验收 rc=0）。

## 臂读数

| 臂 | 端点/模型 | rc | 回合 | ctok | 交付 |
|---|---|---|---|---|---|
| HF | 托管 deepseek-flash（believed v4.1） | 5 | 5 | 13,159 | 无（思维链 23,853 字撞 8192） |
| HP | 托管 deepseek-v4-pro | 3 | 12 | 14,118 | 564/564 逐位全中 |
| 本地参照 | DGX v4-flash-0731（0920 批） | 5/3 | — | — | B1/B2b rc=5（思维链 21k/15.5k 字）；B2w rc=3 退化循环 |

同样任务书：flash「暴食」（单回合全量枚举 → 思维链爆炸 → 死）vs pro「分餐」（枚举摊多回合 → 活）。
完成 token 几乎相同（13.2k vs 14.1k）——差别是每回合节奏，不是预算总量。

## 文件导览

- `report/托管臂空回执AB报告_20260923-1427.txt` — 主报告（判读、五假设终裁、自白）
- `report/托管AB_口径预登记_20260923.md` — 跑前冻结口径 + 跑后补记（含用户手动臂登记）
- `code/ds4f_loop.py` — 被测客户端本体（system_prompt 在 :129，`MAX_TOKENS=8192` 在 :105，
  Authorization 写死 "Bearer local" 在 :203）
- `code/b2_hosted_arm.py` — 托管臂启动器（本地反代注入真 Bearer；只 patch URL/MODEL）
- `code/b2_verify.py` + `code/b2_floor.py` + `data/B2_floor.json` — 验收尺与地板
- `task/B2_task.md` — 任务书（带「读一段追加一段」节奏指令，仍未能救 flash 臂）
- `runs/run_b2b/` — 本地 v4.0 rc=5 原始轨迹；`runs/run_hosted_flash/` — HF rc=5；
  `runs/run_hosted_pro/` — HP rc=3；`runs/hosted_ab_console.log` — 代理逐请求行（含 usage）
- `data/B2_rows_hosted_pro.jsonl` — HP 的 564 行交付物；`data/corpus/` — 语料副本

## 复现

```
python code/b2_hosted_arm.py <model-id> -- task/B2_task.md路径改写版 --workdir <干净目录> \
    --out <轨迹目录> --max-turns 12
```
自证成对：`python code/b2_hosted_arm.py --selftest`（/models 200 正例 + 坏模型 4xx 透传负对照）。
验收：`python code/b2_verify.py --rows <rows> --floor data/B2_floor.json --out <新报告>`（拒绝覆写）。

## 未包含 / 纪律

- **API key 不在本仓库**（托管 key 仅存测试机本地文件，且已建议轮换）；代理日志不打 key。
- trace 含任务书全文与语料片段（模型所见即所得），语料另存 `data/corpus/`。
- 待做：用户开 agent 窗口直连 :8888 的手动臂（同任务书 × 用户 harness，判读已冻结在预登记文件）；
  若成功 ⇒ 本地栈+模型快照双重实测无罪，定罪收敛到 ds4f_loop 包络（8192/12回合/快停策略）。
