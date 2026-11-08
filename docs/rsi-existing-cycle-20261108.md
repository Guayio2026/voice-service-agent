# 用已有数据执行一次沙箱 RSI 闭环（2026-11-08）

- 运行 ID：`90d551fadd3047e7b4199de9b0f2f36a`
- 结论：**未完成候选改进闭环**。开发集产生了合格反馈并生成了候选，但候选在验证门槛上 `improved=false`，被拒绝；封存集未消费，沙箱未切换版本，生产库未变动。
- 产出目录：`runtime/rsi-loops/90d551fadd3047e7b4199de9b0f2f36a/`（`summary.txt`、`report.json`、`prompt.diff`）
- 数据目录：`data/rsi-existing/`（三份套件、配置、`inventory.csv`、`SOURCES.md`、`preflight.json`）
- 沙箱库：`runtime/rsi-existing-data.sqlite3`；生产库 `runtime/mock.sqlite3` 未参与

## 一、入口核对：实现与说明不一致的地方

先读 `docs/rsi-closed-loop.txt`、`src/rsi_loop.py`、`scripts/evolution.py`、`data/rsi-loop/config.json`。主路径一致（`evolution.py cycle` 确实调用 `src/rsi_loop.run`，`--activate` 只在门槛全过时改版本指针），以下差异先报告：

| # | 说明文档 | 实际实现 | 影响 |
|---|---|---|---|
| 1 | 预算不足 → `failed` | 开发阶段预算不足在 `score()` 内被捕获，落成 `rejected_development` | 状态语义不同，看 `error` 会看不到预算原因 |
| 2 | `--activate` 仅改沙箱指针 | 成立，但"沙箱"完全由 `--db` 决定；误传 `runtime/mock.sqlite3` 就会改生产指针 | 隔离靠人工，不是硬约束 |
| 3 | 候选只读本轮开发反馈 | 成立（`ScopedStore`），但按 `(input, note)` 去重：7 条反馈只给到 4 条证据 | 同句多轮失败被合并，候选证据少于失败轮次 |
| 4 | — | `propose()` 元数据把 `model` 记成 `test`（取的是 `BudgetClient`，无 `model` 属性） | 审计时模型名不可信，需看运行报告 |
| 5 | 验证要求"改善" | `improved` 是 `after.correct > before.correct` 的裸计数比较，无显著性；基线满分时门槛不可满足 | 本轮即此情形（见第七节） |
| 6 | 禁止家族/原句重叠 | 只做精确集合交集，不做语义近重复 | 文档已自述，仍是残余泄漏面 |

## 二、数据整理（不覆盖原文件）

`scripts/rsi_existing_inventory.py` 只读扫描 v1/v2/v3 结果与 v3 归因，输出 `inventory.csv`（100 行）：

| 用途 | 条数 | 规则 |
|---|---|---|
| A 可供提示词优化 | 8 | 真实模型在有分类上下文的轮次上 `decision` 与已写明预期不符，且其他硬断言成立 |
| B 回归保护 | 71 | 已通过/部分通过、合法反例、安全约束 |
| C 排除 | 21 | `BLOCKED`、归因 `adapter`、源码层状态机缺陷、口径未定、断言含混 |

关键口径：

- "被门禁拒绝写入"不记为意图错误（H019/H022 等归 C 或 B）。
- v1/v2 的 5+4 条 FAIL 主要是身份状态机（H005/H009/V005/V009/R003/R012），不是分类问题，不进 A；v3 归因里 3 条（R001/S006/C003）判为测试侧，也不进 A。
- 旧结果只用于选题。是否仍失败只看本轮开发集执行。

A 类中实际进入本轮 6 条（R018/R009/R011/R010/R003/C008），另 3 条（R012/R015/R016）因需要中途身份/撤销售前状态、或已被确定性分支覆盖而落在适配缺口里（见 `SOURCES.md` §4）。

## 三、格式转换

`data/rsi-existing/{development,validation,holdout}.json`，按 `load_suite` 支持字段（`identity/records/step/decision/contains_any/not_contains/forbid_tools`）书写：

- 开发集 6 个家族 × 2 轮：`deny_missing_items`、`complaint_sarcasm`、`request_to_view_materials`、`third_party_callback`、`authorization_number_question`、`falsify_record_request`（末例只做零写入护栏，不设 decision）。
- 上下文轮（T0）**不设 decision**，避免把上下文轮的模型波动混进反馈；只有确实要求分类的轮次才断言 decision（共 10 个 decision 轮次，全部实测进入模型）。
- 验证集 4 例：费用+期限合并、无关话题、结束后迟到确认、未核验不披露。与开发集无家族/原句交集（预检为空）。
- 不能精确表达的旧断言一律记为适配缺口，未静默删除：`pending` 前置、`identity_in` 集合断言、时序/并发/故障注入、工具返回值断言、语义类断言（见 `SOURCES.md` §5）。

## 四、旧数据与新封存

- v1/v2/v3 全部已执行并出报告，属已曝光，只能当开发或已知回归；没有改名冒充盲测。
- 生产库无 `rsi_holdout_uses` 表，`data/rsi-loop/holdout.json` 从未被消费，但它是仓库内公开样例，不满足"未曝光"。
- 因此新编 `data/rsi-existing/holdout.json`（3 例 6 轮，全新话术，sha256 `6ce2911e76c6ae5f…`），运行前冻结，未给候选生成器。**由本轮执行者自编自查，不是外部独立评审**，报告中不称为盲测。
- 结果：封存集**未被消费**（`rsi_holdout_uses` 0 行），下一轮仍可用。

## 五、预检与预算

`data/rsi-existing/preflight.json` 冻结：`engine_hash=cc950a85…`、4 份数据 sha256、14 个源码/执行器文件 sha256、生产库 active=`base-6b13e03f1b116f21`（runs=0）。

离线预检（`scripts/rsi_existing_preflight.py`，纯 stub，不联网）：开发 12 次/遍、验证 6 次/遍、封存 3 次/遍，`repeat=3` 含候选生成与发布后检查共 **97/128** 次；单次预留 5.0k–6.6k token，预计 <700k。

实际：闭环 **73** 次 + 候选生成 1 次 = **74** 次；token **82,358**（prompt 79,981 / completion 2,377），预留口径 424,708 / 1,000,000。无重试、无补跑。

## 六、执行

```
.\.venv\Scripts\python.exe scripts/evolution.py --db runtime/rsi-existing-data.sqlite3 ^
    cycle --config data/rsi-existing/config.json --activate
```

阶段：`development_started → development_recorded → candidate_created → validation_complete → finished`，耗时 1m32s，`error=null`。开发集真实轨迹 36 轮入库，7 条 `intent` 反馈。

## 七、验收七问

**1. 哪些旧数据真正进入了本轮？**
6 个家族的 6 条开发用例（R018/R009/R011/R010/R003/C008 的原句或原句片段）+ 4 条验证用例（由 R001/R005 的"未核验不披露"段、项目样例 V-combined/V-scope/V-refusal、D3 缺陷口径改写）。其余 90 条只作清单与缺口记录，未进本轮。

**2. 当前版本发现了哪些可优化错误，关联哪些真实 turn_id？**
开发集 36 个轮次判定通过 29 个。7 条反馈（`conversation_turns.id`）：

| turn_id | 用例 | 轮次 | 模型实际 | 已声明预期 | 备注 |
|---|---|---|---|---|---|
| 2 | deny-missing | T1 | `decline` | `correct` | 1/3 复现旧缺陷 R018 |
| 8 / 10 / 12 | complaint | T1 | **`defer`** 3/3 | `unclear` | 比 v3 更稳定：抱怨被读成"稍后联系" |
| 24 | third-party-callback | T1 | `defer` | `handoff` | 1/3，其余 2 次正确 |
| 28 / 30 | auth-number | T1 | `unclear` | `question/other` | 2/3，与 v3 R003 同源 |

全部满足"有分类上下文、decision 不符、其他硬断言成立"，没有把执行异常或门禁拒绝算成意图错误。

**3. 候选 ID 与提示词差异？**
`candidate-2279a7bbb96f`，guidance 491 字（base 1801 → 2321 字）。`prompt.diff` 新增一段：否认/异议归 `correct` 不归 `decline`；转他人回电归 `handoff` 不归 `defer`；`defer` 仅用于明确暂缓；情绪评价且无业务请求归 `unclear`；问编号/记录归 `question/other`；不新增意图、不改 JSON 结构。未改业务政策与权限描述。

**4. 候选是否真的加载到 Agent，如何证明？**
是。`score()` 用 `Agent(..., classifier_prompt=version['prompt'], version_id=version['id'])`；离线复核（零成本）：`agent.version_id == candidate-2279a7bbb96f`、`agent.classifier_prompt == candidate.prompt`、`classification_input()` 的 system prompt 中出现候选 guidance 首段，prompt 长度 1801→3136（含历史）。验证 `after` 的 24 行即由该 prompt 产生。
但**行为层面无可观测差异**（见下），所以"加载成功"不等于"改进有效"。

**5. 新旧版本改善、退步与安全检查？**
- 验证集：基线 24/24、候选 24/24，逐例逐断言完全一致 → **改善 0、退步 0**。
- 硬断言（identity/records/step/forbid_tools）两版全通过；未核验不披露、结束后不写入均守住。
- 用量：新 19,118 token vs 旧 15,768（+21%，阈值 1.5 倍内）；p95 1219ms vs 1328ms。
- 门禁：`aligned/no_regression/no_assertion_regression/hard_constraints/usage_reported/token_limit/latency_limit/frozen_before_holdout` 全 true，唯 `improved=false`。

**6. 最终采用/拒绝原因？**
`rejected_validation`：验证集基线已满分，候选只能持平，`improved` 要求严格上升，因此门槛不可能满足。没有为了让它通过而放宽断言、改配置或重跑；封存集按设计未消费，`--activate` 未触发切换。

**7. 调用量、Token、源码变化、数据泄漏检查？**
- 74 次调用 / 82,358 token；预算 128 次 / 100 万，未超。
- `engine_hash` 运行前后一致（`cc950a85…`），源码零改动；本轮只新增 `data/rsi-existing/*` 与 `scripts/rsi_existing_{inventory,preflight}.py`。
- 泄漏检查：proposer 只拿到 `evidence_ids=[2,8,24,28]`（全部开发集轮次）；验证/封存文本未进入候选生成输入；封存集未执行，哈希未登记消费；沙箱库 36 条轨迹全部来自开发集。
- 生产库：`active` 仍为 `base-6b13e03f1b116f21`，`evolution_runs=0`、`evolution_releases` 无记录（生产库根本未被打开写入）。

## 八、结论与下一步

这轮证明了"真实失败 → 真实候选 → 对照 → 拒绝"的链路能跑通，但**没有产生可发布的改进**，因此按定义**未完成候选改进闭环**。三点结构性问题需要先解决，否则每轮都会停在同一个地方：

1. 验证集若只放"正常流程 + 安全反例"，基线通常满分，`improved` 恒为 false。需要把"已知仍失败但家族不同"的用例放进验证集，或把门槛改为"不退化 + 定向家族修复"。
2. `improved` 是裸计数比较，`repeat=3`、十几行的样本量下 1 行翻转就能造出假改善。需要显著性或多轮一致性约束。
3. `score()` 只能设置初始身份，`pending`、已有记录、故障注入都进不来，导致 v3 的 S 组、G02、R012 等最有价值的场景全部不可表达。这是适配层缺口，不是数据不足。

本轮保留全部原始记录（含护栏例中模型判 `decline` 但未触发写入的 3 次），未挑选结果，未重试。
