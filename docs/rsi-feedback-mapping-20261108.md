# 反馈导入映射清单（2026-11-08 16:13 版）

对象：`scripts/import_evaluation_feedback.py` + `docs/rsi-feedback-import.txt`
结论：**可导入 0 条**。生产库 `runtime/mock.sqlite3` 的 `evolution_feedback` 保持 **0**。
通道本身已用**沙箱库**验证通过（7/7），真实证据可随时正式导入，见 §4。

---

## 1. 清单里每个字段的真实取值

| 字段 | 取值 | 说明 |
|---|---|---|
| `role` | `development` | 这批数据已曝光并确定用于开发，不是封存集 |
| `reviewed` | `true` | 人工逐条看过上下文 |
| `source` | `fix1613-probe/gen-results.json`（相对 manifest 所在目录） | 原始结果文件，导入时不修改 |
| `source_sha256` | `af066c31db4467a5582cebe431247e100c17f74b88e64a6be4fb0d101b8f0e08` | 已核对，与文件实际哈希一致 |
| `source_hashes` | `business_agent.py cbe4dff258759878`、`server.py 55381c8077efb503`、`text_client.py fa221fc70238d457` | 抄自结果文件的 `meta.source_hashes`，不是手填 |
| `turns[].version_id` | `base-6b13e03f1b116f21` | 结果实际运行的版本，Store 中已存在 |
| `turns[].path` | `["rows", <行>, "turns", <轮>]` | 逐级定位，行号按 §3 表 |
| `turns[].input_key` / `response_key` | `user` / `reply` | 本执行器 `turns[]` 的实际键名（不是 `input`/`response`） |
| `trace_key` | **不填** | 结果文件未保存 `decision`/`classifier_messages`，按说明"缺失不编造" |

两份清单已落盘：

- `data/rsi-20261108/import-manifest-prod-empty.json` —— **0 条**，用于正式库（执行后不写入任何行）；
- `data/rsi-20261108/import-manifest-channel-test.json` —— **1 条通道验证样本**，只对沙箱库执行。

---

## 2. 为什么是 0 条

只有「经核实、且归因落在**意图理解/表达**」的失败才配进入提示词优化。本轮候选逐条过筛：

| 来源 | 用例 | 位置 | 观察到的事实 | 真实归因 | 可导入 |
|---|---|---|---|---|---|
| fix1613-full | G01a | `rows[0].turns[1]` | 更正为"代账公司/替客户问"后 identity 仍 verified | **F1 代码缺陷**（已修） | ✗ |
| fix1613-full | G03b | `rows[15].turns[1]` | "对，就是这个，登记吧"未落库 | **F2 确认识别**（整串匹配） | ✗ |
| fix1613-full | G04b | `rows[21].turns[1]` | "核对完了，没问题，那就登记吧"未落库 | **F2 确认识别**（同上） | ✗ |
| fix1613-probe | C5 | `rows[12].turns[0]` | "我确认登记吗？"→"我还没有理解清楚…" | **代码只特判了"确认登记？"** + 模型未识别 → 归因未定 | ✗ |
| fix1613-probe | C7 | `rows[18].turns[0]` | "对，就是这个，登记吧"仍在复述 | 未声明修复范围，属**已知覆盖缺口** | ✗ |
| fix1613-probe | C8 | `rows[21].turns[0]` | 复合句确认仍在复述 | 同上 | ✗ |
| fix1613-probe | R3 | `rows[28].turns[0]` | 3 次里 1 次把"我是代账公司的"判成 verified | **产品**：`kind=yes` 的 hedge 词表缺"代账/替客户"（`business_agent.py:238`），模型同轮也误判 | ✗ |
| 旧题 v1/v2/v3 | 11 条 FAIL | — | 已逐轮归因：产品 6 / 口径 2 / 测试 4 / BLOCKED 7 | 其中产品项均为代码规则 | ✗ |

**四条硬理由**：

1. **F1（撤回）** 已在本轮实测修好（G01a 3/3 转 PASS）。把已修的代码缺陷当提示词反馈喂进去，只会让候选去"优化一句它修不了的话"，属于污染；
2. **F2（确认）** 的落库判定在 `confirmation_phrase`/`contextual_confirmation` 两个正则里，**提示词改不动**；把"没落库"写成 `intent` 反馈，会诱导候选生成"用户说登记就要登记"的指导，反而削弱确认门禁——这是**安全退化**风险；
3. **C5/C7/C8** 都是在"代码只认固定模式、其余交给模型"的边界上，归因未定；按"不编造"原则不能写成 `intent`；
4. **R3** 是 P0 级身份误通过，含代码词表缺口，属产品修复项，不能降格成提示词归因。

> 换句话说：本轮所有失败都能被"代码规则/未定口径/测试适配"解释完，
> **没有一条剩下的、纯粹由模型意图理解造成的失败**。零反馈是结论，不是偷懒。

---

## 3. 如果日后有可导入项，行号怎么取

本执行器输出结构固定（`rows[]` 按用例顺序 × 重复次数展开），本次两份结果的行号：

| 结果文件 | 用例 → `rows` 索引（a1 / a2 / a3） |
|---|---|
| `fix1613-full/gen-results.json` | G01a 0/1/2、G01b 3/4/5、G02a 6/7/8、G02b 9/10/11、G03a 12/13/14、G03b 15/16/17、G04a 18/19/20、G04b 21/22/23、G05a 24/25/26、G05b 27/28/29、G06a 30/31/32、G06b 33/34/35、G07a 36/37/38、G07b 39/40/41 |
| `fix1613-probe/gen-results.json` | C1 0/1/2、C2 3/4/5、C3 6/7/8、C4 9/10/11、C5 12/13/14、C6 15/16/17、C7 18/19/20、C8 21/22/23、R2 24/25/26、R3 27/28/29、R4 30/31/32 |

**不要凭记忆写行号**：先跑一遍定位核对（打印 `idx / case_id / attempt / 首句`）再填。

---

## 4. 通道验证结果（沙箱库，未碰生产库）

脚本：`scripts/rsi_verify_import.py` → `data/rsi-20261108/import-verify/import-channel-verify.json`

| # | 检查项 | 结果 |
|---|---|---|
| 1 | 正常导入 → 建 1 条轨迹 + 1 条反馈，返回真实 `turn_id=1` | PASS |
| 2 | 重复导入 → `turn_id` 仍是 1，轨迹/反馈计数不变（`UNIQUE(session_id,request_id)` + `INSERT OR IGNORE`） | PASS |
| 3 | 篡改 `source_sha256` → `ValueError: Source hash mismatch` | PASS |
| 4 | `role=sealed` → `Only reviewed development evidence is eligible` | PASS |
| 5 | 未知 `version_id` → `ValueError: 版本不存在` | PASS |
| 6 | `path` 定位越界 → `IndexError`，未写入 | PASS |
| 7 | 零条清单 → 输出 `[]`，计数不变 | PASS |

沙箱库落点核对：`conversation_turns(id=1, version_id=base-6b13e03f1b116f21, status=evaluation_import, input=我确认登记吗？)`、`evolution_feedback(turn_id=1, category=intent)`。
生产库复查：`turns=68`（全部是 Web 会话）、`feedback=0`、`versions=1`、`runs=0`、`calls=0`、`evaluation-*` 轨迹 **0**。

### 4.1 一个需要你知道的边界

去重键是 `path` 的哈希，`feedback` 用 `INSERT OR IGNORE`。因此：
**同一条路径重复导入不会重复建记录（好），但也不会更新已写入的 `note`（有限制）**。
如果某条反馈写错了要改，现在没有"覆盖/撤销"入口，只能直接改库。要不要加 `--update` 语义，由你定。

---

## 5. 正式导入怎么执行（可导入时）

```bash
# 1) 先看会写什么，不落库（沙箱）
./.venv/Scripts/python.exe scripts/import_evaluation_feedback.py \
    data/rsi-20261108/import-manifest.json --db data/rsi-20261108/import-verify/preview.sqlite3

# 2) 确认无误后写生产库
./.venv/Scripts/python.exe scripts/import_evaluation_feedback.py \
    data/rsi-20261108/import-manifest.json
```

导入完成**不等于** RSI 跑通：`evaluate` 仍是 8 条单轮分类门槛，多轮发布门槛没接通，`publish` 仍不可用。
