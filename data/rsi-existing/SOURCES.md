# 用已有数据做一次 RSI 闭环：数据来源与映射清单

- 生成时间：2026-11-08（本轮）
- 适用范围：仅本轮沙箱闭环；不改产品源码、不切生产版本、不覆盖任何原始结果文件
- 机器可读清单：`data/rsi-existing/inventory.csv`（100 行，逐用例）
- 生成脚本：`scripts/rsi_existing_inventory.py`（只读原始文件）、`scripts/rsi_existing_preflight.py`

## 1. 用了哪些原始证据

| 版本 | 用例文件 | 结果文件 | 归因/报告 | 状态 |
|---|---|---|---|---|
| v1 | `data/independent-holdout/holdout-20261107-v1.json` | `results-v1.json`（另存 `results-v1-prev*.json`、`baseline-v1-firstrun.json`） | `docs/independent-holdout-report-v1.md` | 已曝光 |
| v2 | `data/independent-holdout/holdout-20261108-v2.json` | `results-v2.json`（另存 `results-v2-prev*.json`） | `docs/independent-holdout-report-v2.md` | 已曝光 |
| v3 | `data/independent-v3/{real-model-v3,state-fault-v3,content-consistency-v3}.json` | `results-v3.json`（原始）、`results-v3-rejudged.json`（复判） | `attribution-v3.json`、`manifest.json`、`docs/independent-holdout-report-v3.md` | 已曝光 |
| 回归/复验 | — | `data/regression-20261108/`、`data/verify-20261108-r{3,4}/` | `docs/full-regression-20261108.md` | 已曝光 |
| 100 例 | `data/generated-100/scenarios.json` | `results.json`、`results-before-d3-d4.json` | `docs/generated-100-{report,defects,coverage}.md` | 已曝光 |
| 既有 RSI 实验 | `data/rsi-20261108/generalization-set.json`、`probe-fixes-1613.json`、`probe-confirm.json` | `baseline/gen-results.json` 等 | `cycle-attempt.log`、`trace-gap.json` | 已曝光 |
| 旧单轮评测集 | `data/evolution/{validation,holdout}.json` | 无独立结果文件 | — | 未用于本轮 |

`cycle-attempt.log` 记录的是一次**失败**的尝试：`没有已标注的问题对话`，未生成候选，也未调用模型。它不是"闭环已完成"的证据。

## 2. 分类规则（写进脚本，可复核）

- **C 排除**：`BLOCKED`、归因为 `adapter`、执行器/故障注入能力缺失、纯源码层状态机缺陷、业务口径未定、断言口径含混。
- **B 回归保护**：已通过或部分通过的用例、合法反例、安全约束（未核验不披露、零写入、幂等）。
- **A 可供提示词优化**：真实模型在**有分类上下文**的轮次上给出的 `decision` 与已写明预期不符，且其他硬断言成立。**"被门禁拒绝写入"不算意图错误**，一律不进 A。

判定"旧失败是否仍是当前失败"只看本轮开发集的实际执行结果；旧结果只用于选题。

## 3. 本轮实际采用的用例（10 条来源 → 13 个新用例）

| 新用例 | 来源 | 原判定 | 采用理由 | 断言 |
|---|---|---|---|---|
| `E-dev-deny-missing` | R018 T1（原句） | FAIL，2/3 被判"拒绝沟通" | 否认缺项属资料质疑，归 `correct`；提示词明文 | decision=correct |
| `E-dev-complaint` | R009 T2（原句） | FAIL，1/3 被判"拒绝沟通" | 抱怨/嘲讽不是拒绝；归因报告 action 明列 | decision=unclear |
| `E-dev-view-materials` | R011 T2（原句） | FAIL，1/3 被判"转人工" | 明确请求看材料，属业务问题 | decision=question/materials |
| `E-dev-third-party-callback` | R010 T3（原句） | 模型 3/3 判 handoff（正确） | 护栏：防止候选把 handoff 改坏 | decision=handoff |
| `E-dev-auth-number` | R003 T2（原句） | FAIL，2/3 判 "unclear" | 问句，非拒绝；主题无法归类 → other | decision=question/other |
| `E-dev-falsify-guard` | C008 T1（原句） | 整体 PASS，1/3 误判 | 口径存疑，**不断言 intent**，只断言零写入 | records/not_contains/forbid_tools |
| `E-val-fees-deadline` | 项目样例 V-combined + 业务口径 | 样例 | 正常流程多主题合并 | decision=question/fees+deadline |
| `E-val-off-topic` | 样例 V-scope + R007 | 样例 | 无关话题边界 | decision=off_topic |
| `E-val-late-confirm` | 样例 V-refusal + D3 | 缺陷已修 | 结束后的迟到确认不得写入 | step/records/forbid_tools |
| `E-val-no-disclosure` | R001/R005 的"未核验不披露"段 + G01a | FAIL（源码层） | 未核验不得披露/查询 | identity/records/forbid_tools |
| `E-hold-deadline` | 新编（业务口径） | — | 封存集，不取自旧题 | decision=question/deadline |
| `E-hold-thanks-handoff` | 新编 | — | 封存集 | decision=handoff |
| `E-hold-cancel-then-end` | 新编（D3 口径） | — | 封存集，零写入+结束 | step/records/forbid_tools |

## 4. 选了但没用上的 A 类（及原因）

| 原用例 | 原因 |
|---|---|
| R012（撤回后"你重新核一下"） | 需要"已撤回身份"前置状态，`score()` 只能设初始 verified/unverified，无法注入中途状态 |
| R015（"刚才那条不算，别存"） | 现由 `graph_gate` 的确定性撤销分支拦截，不再进入模型分类，产生不了 decision 反馈 |
| R016（"先别存，我把话说清楚再记"） | 同上（确定性撤销分支），原 3/3 的 `defer` 误判已不可复现 |
| R001（"按已通知完成记一条"） | 现由 `graph_classify` 的"记一条"确定性分支覆盖；且原断言要求"建立待确认项"，属口径/适配混合 |

## 5. 适配缺口（不静默删除、不当通过）

`rsi_loop` 只支持 `identity / records / step / decision / contains_any / not_contains / forbid_tools` 七类断言，`score()` 只能设置**初始身份**和 `application_id`。因此以下旧证据**无法表达**，本轮明确记为缺口：

1. 前置 `pending`（待确认项）、已有记录数、`operation` 待核对状态 —— 无法注入，S 组 12 条与 G02 全部不可表达。
2. `identity_in ["unverified","wrong_contact"]` 这类**集合断言** —— 只支持精确值。
3. 时序/并发/故障注入（延迟投递、并发确认、response_override、timeout）—— 无对应能力，7 条 `BLOCKED` 无法解除。
4. 工具返回值断言（`get_operation_status` 返回非 confirmed）—— 执行器只记调用名。
5. 语义类断言（"不得宣称审核通过""五段复述一致"）—— 只能退化为 `not_contains` 关键词，属弱化表达，本轮未用于 A 类反馈。

## 6. 封存集状态

- 旧封存集：v1/v2/v3 全部已执行并出报告 → 只能作开发或已知回归，**不得改名当盲测**。
- `runtime/mock.sqlite3` 中 `rsi_holdout_uses` 表不存在，`data/rsi-loop/holdout.json` 从未被消费；但它是仓库内已公开的样例，不满足"未曝光"。
- 本轮因此**新编** `data/rsi-existing/holdout.json`（3 例 6 轮，新话术），哈希在运行前冻结并登记；由本轮执行者自编自查，**不是外部独立评审**，报告中不称其为盲测。

## 7. 预算与调用量（预检实测）

| 阶段 | 单遍调用 | 次数 | 小计 |
|---|---|---|---|
| 开发集 6 例 ×2 轮 | 12 | repeat 3 | 36 |
| 候选生成 | — | 1 | 1 |
| 验证集 4 例 ×2 轮 | 6 | 新旧 ×3 | 36 |
| 封存集 3 例 ×2 轮 | 3 | 新旧 ×3 | 18 |
| 发布后检查（验证集 ×1） | 6 | 1 | 6 |
| **合计** | | | **97 / 128** |

单次调用预留约 5.0k–6.6k token（实测 `classification_input` 字节数），97 次预计 < 700k，低于 1,000,000 预算。`repeat` 保持文档默认 3；不通过重复运行补样本。
