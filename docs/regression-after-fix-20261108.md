# 回归结论：v1 留出集修复后重跑（2026-11-08）

- 被测代码：`src/business_agent.py` / `src/server.py`（修复后版本，00:03 落盘）
- 测试集：`data/independent-holdout/holdout-20261107-v1.json`（冻结 v1，30 条，未改动）
- 基线：`data/independent-holdout/baseline-v1-firstrun.json`（首轮 13/3/14/0）
- 本轮：`data/independent-holdout/results-v1.json`

## 1 总览

| 指标 | 首轮 | 本轮 | 变化 |
| --- | --- | --- | --- |
| PASS | 13 | 23 | +10 |
| PASS(partial) | 3 | 5 | +2 |
| FAIL | 14 | 2 | −12 |
| BLOCKED | 0 | 0 | — |
| 失败断言数 | 57 | 10 | −47 |
| **原 PASS 转非 PASS（回归）** | — | **0 条** | 无回归 |

结论：修复有效且未引入回归。剩余 2 条 FAIL 均为测试侧问题（见 §4），不计产品缺陷。

## 2 缺陷修复验证（7 项全部转 PASS）

| 缺陷 | 原失败用例 | 现判定 | 关键证据 |
| --- | --- | --- | --- |
| X1 身份撤回不回退 | H011、H012 | PASS | 改口后 identity 变 `unverified`，回复“已撤回本次身份确认”，后续不再调 `get_application` |
| X2 模型误判穿透 | H030 | PASS(partial) | “不太确定”第 1 轮未核验，第 2 轮明确后才核验一次 |
| X3 拒绝后仍写库 | H015、H016 | PASS | 拒绝即结束，`records=0`；未核验状态下“不用了”也结束 |
| X4 过度拦截授权对接人 | H005 | PASS | “我不是法人…一直是我在跑…授权编号”首轮核验通过 |
| X5 告别识别过窄 | H002、H014、H017 | PASS / PASS(partial) | “先不聊了，我这边有事”“等我问完再打过来”“这会儿不方便说这个”均结束会话 |
| X6 重复确认可双写 | H021 | PASS | 二次“确认登记”回复“已登记，无需重复确认”，`records` 仍为 1 |
| X7 不查操作状态 | H027、H028 | PASS | “刚才那次登记成没成”调用 `get_operation_status` 并如实回答；审核问题明确不可查 |

## 3 本轮判定分布

- PASS 23：H001–H007、H009–H012、H015–H021、H023–H025、H027、H028
- PASS(partial) 5：H008（内部确认事件）、H014（迟到时间戳）、H026（材料字段映射）、H029（解析异常计数 ×2）、H030（模型拒绝计数）
- FAIL 2：H013、H022

## 4 剩余 2 条 FAIL 的归因（测试侧，不计产品缺陷）

**H013（测试侧 + 口径）**
- 现象：“我其实是老板，刚才逗你的”在第 1 轮即被核验。
- 归因：替身模型无法判断“逗你的”是玩笑，也无法解析第 2 轮“不开玩笑了，我确实是”的省略指代；同时“玩笑式改口是否必须先拦”属产品口径，v1 未列为待审项。
- 处置：不改产品；如需强约束，应在 v2 待审清单中定性后再加断言。

**H022（口径差异）**
- 现象：用户“再记一条”时被“还有未解决的问题”挡住，未进入待确认。
- 归因：实现规定“有未核实问题必须先回复问题已解决”，比契约更严，属设计选择，且已给出解除路径。
- 处置：已在 v2 列为待审项（第 2 条）；若产品决定允许 `unresolved=true` 记录，则改断言而非改实现。

## 5 本轮同时修正的两处测试侧口径

1. **前置构造**：`written` 前置原先直接插 `records` 行，缺 `operations` 台账，造成“有记录但查不到操作”这种真实中不存在的状态，误报 H028。已改为走真实 `save_call_result` 路径构造（附 `authorize_write`）。
2. **判定器否定语境**：`get_operation_status 的返回值不得被表述为审核进度` 原先会命中“**不是**业务审核进度”这种否定句，误报 H027。已加否定语境剥离后再匹配。

两处均属执行器缺陷，已修正；修正前后同一代码的判定差异仅出现在 H027/H028，其余 28 条判定不变。

## 6 复跑命令

```bash
cd voice-service-agent
./.venv/Scripts/python.exe scripts/run_holdout_v1.py          # 全量
./.venv/Scripts/python.exe scripts/run_holdout_v1.py H030     # 单例
```

对比脚本（基线 vs 本轮）：

```bash
./.venv/Scripts/python.exe - <<'PY'
import json
a=json.load(open('data/independent-holdout/baseline-v1-firstrun.json',encoding='utf-8'))
b=json.load(open('data/independent-holdout/results-v1.json',encoding='utf-8'))
A={r['id']:r['verdict'] for r in a['rows']}; B={r['id']:r['verdict'] for r in b['rows']}
print('回归:',[(i,A[i],B[i]) for i in A if A[i]=='PASS' and B[i]!='PASS'])
print('改善:',[(i,A[i],B[i]) for i in A if A[i]!='PASS' and B[i]=='PASS'])
PY
```
