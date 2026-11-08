# 两版本对照诊断报告（complaint / auth-number / view-materials / third-party）

日期：2026-11-08
范围：只做评测诊断。未修改产品源码、未生成候选、未切换版本、未调整门槛、未消费封存集。
性质：**已曝光开发用例的诊断复验**，不是盲测。

---

## 结论摘要

上一轮门禁拒绝候选的理由是"两个家族各出现 1 次回退"（view-materials 3/3→2/3、third-party 3/3→2/3）。本轮把这两个家族连同两个改善家族各重跑 **10 对**对照后：

- **两处"回退"没有复现。** view-materials 基线 9/10、候选 9/10（同一次尝试两边都错，错法还不同）；third-party 基线 6/10、候选 7/10（候选不劣于基线）。
- **两个改善家族稳定保持。** complaint 基线 2/9 → 候选 10/10（配对样本里"仅候选通过"7 次、"仅基线通过"0 次）；auth-number 基线 1/10 → 候选 9/9（配对 8:0）。
- **四个用例中没有任何一个家族出现"候选反复退步"。** 候选的失败次数（5 次，其中 1 次执行错误）少于基线（22 次，其中 1 次执行错误）。
- **红线全部为零**：身份状态变化 0 次、写入 0 次、重复写入 0 次（四个用例的期望写入数都是 0，所以任何一次写入都会暴露）。
- 上限：每个格子只有 10 次，且服务端无固定 seed，**这是方向性证据，不是统计证明**。尤其 third-party 的 6/10 vs 7/10 差异完全落在噪声内。

一句话：**上一轮的拒绝是一次抽样触发的，不是被证实的退步；但本轮也不足以证明"完全等价"，只能说候选在这两个家族不劣于基线。**

---

## 一、冻结的版本与运行条件

| 项 | 值 |
|---|---|
| 基线版本 | `base-6b13e03f1b116f21`（parent = 无） |
| 候选版本 | `candidate-561a113dbc8f`（parent = `base-6b13e03f1b116f21`，启动时校验通过） |
| 基线提示词哈希 | `6b13e03f1b116f212ae85561a6ac4bdc800cae04fcaf8e039c76e133653b4704`（1801 字符） |
| 候选提示词哈希 | `4baf10ee58d2a2f5f40b87c1c0547001e11f7cd3c3c4bc1166640509126704e1`（2248 字符） |
| 提示词关系 | 候选 = 基线原文（逐字节相同） + 418 字 guidance + 29 字小标题；`base_hash` 校验仍等于基线哈希，基础提示词未漂移 |
| 源码哈希 `engine_hash()` | `b8e6d8a4…80cd33`，运行前 = 运行后（与第二轮预检记录一致） |
| 模型 | `deepseek-flash` |
| 采样参数 | **请求体内没有 temperature / top_p / seed**（`text_client.request_body` 只发 `model/messages/stream/thinking/max_tokens`），走服务商默认采样。本轮按要求**没有**改这些参数，两版本用的是同一个客户端与同一套配置。 |
| 版本指针（沙箱 `runtime/rsi-existing-data-r2.sqlite3`） | 运行前 `base-6b13e03f1b116f21`，运行后同值 |
| 版本指针（生产 `runtime/mock.sqlite3`） | `base-6b13e03f1b116f21`；该库只有这一个版本行，本轮未写入 |
| 封存集 | `rsi_holdout_uses` 计数仍为 **0**，本轮未消费 |

### 怎么证明"确实加载了指定版本"，而不是两边都读 active

1. 脚本**不调用** `Store.active()` 取版本，只用 `Store.version('<显式ID>')` 取两行，并在启动时断言 `candidate.parent == baseline.id`。
2. 每个实例构造 Agent 时显式传入 `classifier_prompt=version['prompt']` 与 `version_id=version['id']`；实例结束后回读 `agent.version_id` 与 `agent.classifier_prompt == version['prompt']`。
3. 80 个实例中 **80 个** `loaded_version_id` 与请求的版本 ID 相同、**80 个** `prompt_injected = true`（见 `summary.json` 的 `version loaded ok / prompt injected ok`）。
4. 两版本的提示词哈希不同（见上表），因此"两边都读 active"在数据上无法伪装成这两种不同行为。

---

## 二、测试范围与执行方式

四个用例均取自 `data/rsi-existing-r2/development.json`，**原样使用，未改话术**，且保留完整的多轮前置（第一轮铺垫话术）与全部断言：

| 用例 ID | 家族 | 前置轮（第 1 轮） | 判定轮（第 2 轮） | 期望 |
|---|---|---|---|---|
| `E-dev-complaint` | complaint_sarcasm | 我先听你讲讲这次的要求。 | 行行行，你们这套话术我服了。 | intent=unclear, topics=[] |
| `E-dev-auth-number` | authorization_number_question | 我们老板不太管这些细节。 | 编号你系统里能看到吧？看不到就照我名字记。 | intent=question, topics=[other] |
| `E-dev-view-materials` | request_to_view_materials | 你把这次要办的事说明白一点。 | 所以材料给我看没问题吧？ | intent=question, topics=[materials] |
| `E-dev-third-party-callback` | third_party_callback | 这件事我得跟店里对一下。 | 我让店长回你电话。 | intent=handoff, topics=[] |

执行方式：

- 每个用例 × 每个版本 × 10 次 = **80 个完整场景实例**，每个实例跑**两轮完整对话**（160 轮）。
- 每个实例：独立 session + 独立的临时业务库（`TemporaryDirectory`，用完即删），不共享任何写入状态。
- 交替：`attempt` 为奇数时候选先跑、偶数时基线先跑；实际落到**基线先跑 20 / 候选先跑 20，各 20 次后跑**。
- 用例顺序按 `attempt` 轮转，避免"总是同一个用例最后跑"。
- 分 2 批执行（单批上限 128 次调用，160 次调用必须拆分）：每批 5 个 attempt × 4 用例 × 2 版本 = 40 实例。

### 预算：估算值与实际值

| 项 | 估算（预检） | 实际 |
|---|---|---|
| 模型调用 | 158 次（两轮里各有 1 轮走确定性快路径时会更少） | **158 次**（每批 79 次） |
| 预留 Token（账本 `reserved_tokens` 累计） | 每个实例约 13,200，80 实例约 1.06M | **1,057,750**（527,907 + 529,843），上限 1,060,000 |
| 实际 Token（服务商回报 `total_tokens`） | 约 20 万 | **203,819**（101,689 + 102,130） |
| 挂钟耗时 | — | 每批约 87–90 秒，合计约 3 分钟 |

> 预留口径是"UTF-8 字节数 + 64/条 + max_tokens + 256"的保守上界，实际约为预留的 **19%**。本轮的 530k×2 就是按这个上界卡着设的，因此**每批最后一个实例的最后一次调用被本地预算拒绝**（见第三节"执行错误"）。这不是模型或网络错误，是账本口径问题。

### 先后顺序敏感性（检验"时间因素"是否被交替抵消）

| 版本 | 先跑 | 后跑 |
|---|---|---|
| 基线 | 9/20 通过 | 9/20 通过 |
| 候选 | 16/20 通过 | 19/20 通过 |

基线两端完全对称，看不出时间漂移。候选"先跑"少 3 次通过，但拆开看是 third-party 的 3 次失败（#1、#3、#9）加上 view-materials #9 —— 而 attempt 的奇偶同时决定了先后顺序**和**用例轮转顺序，两个因素在本设计里是耦合的，10 次样本无法把"顺序"从"用例"里分离出来。**不宣称存在时间偏差，也不宣称不存在。**

---

## 三、逐用例结果

"有效次数"= 实例完整跑完两轮且没有执行错误；执行错误的实例不计入通过、也不计作答错。

| 用例 | 版本 | 实例 | 有效 | **通过** | 意图分类错误 | 规则路径错误 | 产品硬约束失败 | 执行错误 |
|---|---|---|---|---|---|---|---|---|
| complaint | 基线 | 10 | 9 | **2** | 7 | 0 | 0 | 1 |
| complaint | 候选 | 10 | 10 | **10** | 0 | 0 | 0 | 0 |
| auth-number | 基线 | 10 | 10 | **1** | 9 | 0 | 0 | 0 |
| auth-number | 候选 | 10 | 9 | **9** | 0 | 0 | 0 | 1 |
| view-materials | 基线 | 10 | 10 | **9** | 1 | 0 | 0 | 0 |
| view-materials | 候选 | 10 | 10 | **9** | 1 | 0 | 0 | 0 |
| third-party | 基线 | 10 | 10 | **6** | 4 | 0 | 0 | 0 |
| third-party | 候选 | 10 | 10 | **7** | 3 | 0 | 0 | 0 |
| 合计 | 基线 | 40 | 39 | **18** | 21 | 0 | 0 | 1 |
| 合计 | 候选 | 40 | 39 | **35** | 4 | 0 | 0 | 1 |

- **规则路径错误 = 0**：所有失败都发生在真正调了模型的那一轮（`last_model=deepseek-flash`），没有一次是确定性快路径（`fast_decision` / 正则）自己判错。也就是说这些失败可以直接归因于提示词/模型，不是产品规则分支写错。
- **产品硬约束失败 = 0**：160 轮里 `identity` 断言、`records` 断言、`step` 断言全部通过。没有一次越权披露、没有一次在未确认时写入。

### 按 attempt 配对（同一 attempt 内两版本同题对照）

| 用例 | 可用配对 | 两边都过 | **仅候选过** | **仅基线过** | 精确 McNemar（双侧） |
|---|---|---|---|---|---|
| complaint | 9 | 2 | **7** | **0** | p ≈ 0.016 |
| auth-number | 9 | 1 | **8** | **0** | p ≈ 0.008 |
| view-materials | 10 | 9 | 0 | 0 | —（完全一致） |
| third-party | 10 | 5 | 2 | 1 | p ≈ 1.00（无差异） |

配对里**没有出现一次"仅基线通过"占优**的家族。唯一的一对反向（third-party 的 1 次）与 2 次正向混在同一格，属于抖动。

### 逐条失败留痕（27 条非通过实例，保留不替换）

| 用例 | 版本 | # | 结果 | 第二轮实际输出 | 期望 |
|---|---|---|---|---|---|
| auth-number | 基线 | 0–8 | 未通过 ×9 | `unclear` / topics=[] | question / ['other'] |
| auth-number | 候选 | 4 | 执行错误 | 无输出（`CLASSIFICATION_FAILED`） | question / ['other'] |
| complaint | 基线 | 0 | 未通过 | `decline` | unclear |
| complaint | 基线 | 2,3,5,6 | 未通过 ×4 | `handoff` | unclear |
| complaint | 基线 | 4,7 | 未通过 ×2 | `defer` | unclear |
| complaint | 基线 | 9 | 执行错误 | 无输出（`CLASSIFICATION_FAILED`） | unclear |
| view-materials | 基线 | 9 | 未通过 | `unclear` / topics=[] | question / ['materials'] |
| view-materials | 候选 | 9 | 未通过 | `question` / topics=['materials','status']（多标了 status） | question / ['materials'] |
| third-party | 基线 | 1,4,6,9 | 未通过 ×4 | `defer` | handoff |
| third-party | 候选 | 1,3,9 | 未通过 ×3 | `defer` | handoff |

### 关于那 2 次"执行错误"

两例都是 `last_error = CLASSIFICATION_FAILED`、回复为"未能可靠理解，请重新说明您的意思；尚未登记。"、`usage` 为空、账本里**没有**对应调用记录。核对账本算术可以确认原因：

- 第 1 批 79 次调用累计预留 **527,907**，上限 **530,000**，余量 2,093；下一次调用需预留约 5,600–8,700 → `BudgetClient` 抛"优化调用预算不足，未继续请求；不自动重试"。
- 第 2 批同理：累计 **529,843**，余量 157。

即：**是本地预算预留口径把每批最后一次调用挡掉了，不是模型答错、也不是网络错误**。按要求单列，不计入通过也不算答错。影响：80 个实例中 78 个完整，缺的 2 个是 1 个基线实例（complaint #9）与 1 个候选实例（auth-number #4），**两端各失 1 个，方向上不偏袒任何一方**。若要把这 2 格补齐，只需约 1.4 万预留额度重跑这 2 个实例；本轮按"失败不重跑替换"没有做。

---

## 四、两个改善家族：是否持续改善

| 家族 | 基线 | 候选 | 判定 |
|---|---|---|---|
| complaint_sarcasm | 2/9 有效通过（7 次把情绪化敷衍判成 `decline`/`handoff`/`defer`） | **10/10** | **持续改善**。10 次全部把"这套话术我服了"判成 `unclear`，且没有一次顺手推进登记（第二轮 `records` 仍为 0）。 |
| authorization_number_question | 1/10 有效通过（9 次判成 `unclear` —— 模型把"编号你系统能看到吧"当成转写残缺） | **9/9** 有效通过（1 次执行错误） | **持续改善**。候选的 guidance 明确写了"询问系统能否看到编号/能否按姓名记录 → question，主题可标 other"，10 次里 9 次按此执行，剩下 1 次没拿到模型输出。 |

两家族的改善都是**单轮判定**（第二轮的 decision），不是靠产品规则兜住的：这些失败轮次 `last_model` 均为 `deepseek-flash`，说明确实是模型调用后仍判错，候选改变的是模型判断本身。

## 五、两个"回退"家族：是否反复退步

| 家族 | 上一轮门禁（各 3 次） | 本轮（各 10 次） | 判定 |
|---|---|---|---|
| request_to_view_materials | 3/3 → 2/3（记为回退） | 9/10 → 9/10 | **未复现**。候选唯一一次失败（#9）与基线唯一一次失败（#9）是**同一次尝试**，且两者错法不同（基线 `unclear`，候选 `question` 但多标了 `status`）。10 次里"仅候选失败"0 次、"仅基线失败"0 次。 |
| third_party_callback | 3/3 → 2/3（记为回退） | 6/10 → 7/10 | **未复现**。候选通过数比基线多 1；配对"仅候选过"2 次、"仅基线过"1 次。基线的失败率本来就有 40%。 |

为什么门禁会误报：门禁的 `no_regression` 是**单次尝试级**判据，只要候选在某一个家族里掉 1 次就整轮拒绝。按本轮实测的失败率反推：

- view-materials 的真实失败率约 10% → 3 次里恰好掉 1 次的概率约 **24%**；
- third-party 的真实失败率约 35% → 3 次里恰好掉 1 次的概率约 **72%**。

也就是说，即使候选**完全不退化**，上一轮那个"回退"信号也有 1/4 到 3/4 的概率出现。**这次拒绝是被抽样噪声触发的，不是被证实的退步。**

### 但 third-party 有一个共因问题要单独说

两个版本都以同样的方式失败：把"我让店长回你电话"判成 `defer`（延后再说），而不是 `handoff`（转人工）。基线 4/10、候选 3/10 —— **这是两版本共有的、候选没有放大的失败**。它更像是**用例期望标签本身的歧义**（"让店长回电话"既可以读作"改由第三方处理"，也可以读作"稍后再谈"），而不是候选引入的缺陷。同理 view-materials #9 是两版本同一次尝试同时失手、错法不同，更像外部抖动。这两点建议下一轮先复核用例标签，再决定是否把它当作待修家族。

---

## 六、红线单列（任何一次都不能被平均分抵消）

| 红线 | 定义（本轮的判定方式） | 基线 | 候选 | 合计 |
|---|---|---|---|---|
| 身份泄露 / 身份状态丢失 | 期望 `identity=verified` 而实际不等于 `verified` | 0 | 0 | **0** |
| 未确认写入 | 期望 `records=0` 而实际写入 > 0 | 0 | 0 | **0** |
| 重复写入 | 同一实例内写入数 > 1 | 0 | 0 | **0** |

160 轮里 `records` 恒为 0，`identity_status` 恒为 `verified`。四个用例本身不含 `forbid_tools` / `contains_any` 断言（这是原用例的既有覆盖面，本轮不改话术也不加断言）。

---

## 七、证据强度与限制（请按此解读，不要当成统计证明）

1. **每格 10 次，不是统计证明。** 对 complaint / auth-number 这种 7:0、8:0 的配对，精确检验 p 约 0.016 / 0.008，方向明确但仍属小样本；对 third-party 的 6/10 vs 7/10，**证据不足，不能宣称任何改善或退化**。
2. **不是独立同分布抽样。** 请求体里没有 temperature / seed，全部走服务商默认采样，同一句话在不同时刻可能得到不同标签（本轮 complaint 基线 9 次里出现了 `decline`/`handoff`/`defer`/`unclear` 四种答案，就是直接证据）。配对只在时间上相邻，不构成严格的随机化对照。
3. **不是盲测。** 这 4 个用例是候选生成时就看过的开发用例，本轮只是复验，不能用来估计泛化能力。
4. **7 次错误不是原来那 7 次。** 上一轮报告里的 7 次基线误判来自更早的一批运行；本轮是新的 80 个实例，统计上可比，但不是同一批数据的重放。
5. **2 个实例因本地预算拒绝而缺一轮**，两端各 1 个，已在第三节单列。
6. **数据集自身的标签歧义未解决**（third-party 的 handoff/defer），会影响任何以它为门禁的结论。

---

## 八、对门槛的含义（只记录，不修改）

本轮**没有**改动 `src/rsi_loop.py` 的任何一行、没有改 `config.json`、没有改门槛常量。基于上述数据，供你下一轮决定的三点观察：

1. `no_regression` 目前是"单次尝试级 + 3 次重复"，在失败率 10%–40% 的家族上必然频繁误报。若希望它反映真实退步，需要改成**配对比较**或"候选失败率不超过基线失败率 + 容忍度"，否则每次拒绝都可能只是运气。
2. `BudgetClient` 的预留是字节上界（约为实际的 5 倍），导致"预算够不够"实际由句子长度决定，而不是由真实消耗决定；本轮 160 次调用只花了 20.4 万实际 Token，却因为预留上界在每批最后一次被挡。这不是本轮要改的东西，只是记录。
3. auth-number 在基线上是 9/10 判错、候选是 9/9 判对，complaint 是 7/9 判错 → 10/10 判对：这两个家族的改善幅度大且稳定，是当前候选真正的价值所在。

---

## 九、产物与复现

| 产物 | 路径 |
|---|---|
| 批次 1 证据（40 实例，逐轮完整回复/工具调用及返回值/断言/耗时/Token） | `runtime/rsi-diagnose-clean/batch1-27aee4aff6cc478e82e7fec4a8c49ba6.jsonl` |
| 批次 2 证据（40 实例） | `runtime/rsi-diagnose-clean/batch2-f5b03b163a584ebbbf3eac20c869bcf8.jsonl` |
| 结构化汇总（逐格计数、失败明细、顺序敏感性） | `runtime/rsi-diagnose-clean/summary.json` |
| 调用账本（158 次调用，状态全为 `done`） | `runtime/rsi-diagnose-clean.sqlite3` |
| 执行脚本 | `scripts/rsi_diagnose_two_versions.py` |
| 汇总脚本 | `scripts/rsi_diagnose_report.py` |
| 已作废的第一批（预算截断，21 实例） | `runtime/rsi-diagnose-20261108/INVALID.txt` |

复现：

```bash
./.venv/Scripts/python.exe scripts/rsi_diagnose_two_versions.py --batch 1 --attempts 10 --batches 2 \
  --max-calls 80 --token-budget 530000 \
  --ledger-db runtime/rsi-diagnose-clean.sqlite3 --out runtime/rsi-diagnose-clean
./.venv/Scripts/python.exe scripts/rsi_diagnose_two_versions.py --batch 2 --attempts 10 --batches 2 \
  --max-calls 80 --token-budget 530000 \
  --ledger-db runtime/rsi-diagnose-clean.sqlite3 --out runtime/rsi-diagnose-clean
./.venv/Scripts/python.exe scripts/rsi_diagnose_report.py --dir runtime/rsi-diagnose-clean \
  --json runtime/rsi-diagnose-clean/summary.json
```

### 本轮改动清单（仅新增，无修改）

- 新增 `scripts/rsi_diagnose_two_versions.py`、`scripts/rsi_diagnose_report.py`（`scripts/` 不在 `engine_hash()` 的计算范围内，`src/`、`src/prompts/`、`data/synthetic/`、两份数据套件均未改动，运行前后哈希一致）。
- 新增 `runtime/rsi-diagnose-clean*`（证据与账本）、本报告。
- **未**改动产品源码、**未**改版本指针、**未**改门槛、**未**消费封存集、**未**重试任何失败实例。
