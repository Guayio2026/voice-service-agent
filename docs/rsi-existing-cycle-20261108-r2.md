# 新门槛下的沙箱复验（第二轮，2026-11-08）

- 运行 ID：`173b68a3befd499e9ebf89063fae4d48`
- 结论：**`rejected_no_stable_gain`**。候选**确实稳定修好了 2 个家族**（各 3/3），但同时让原本 3/3 通过的两个轮次各失败 1 次，`no_regression` 不成立 → 拒绝，**封存集未消费、未切换版本**。
- 源码：`src/rsi_loop.py` sha256 `6d6a8111…`（16:51 版），`engine_hash=b8e6d8a4…`，运行前后一致
- 数据：`data/rsi-existing-r2/`（开发/封存与第一轮同字节；验证集 4→3 例）
- 沙箱库：`runtime/rsi-existing-data-r2.sqlite3`（新建）；生产库 `runtime/mock.sqlite3` 未参与
- 第一轮记录（`90d551fa…`）原样保留，未追溯改判

## 一、新实现与文档核对

逐行读了新 `src/rsi_loop.py`，与 `docs/rsi-closed-loop.txt` 第 5 条一致：

| 文档要求 | 实现 | 判定 |
|---|---|---|
| 候选先重跑开发集 | `score(candidate, development, repeat)` | ✅ |
| 至少重复 3 次 | `if gates['aligned'] and repeat >= 3` | ✅（repeat<3 时永远拿不到修复证据，但只报笼统原因，属尖锐边角） |
| 至少 2 个家族各有一轮基线 ≥2/3 判错 | `sum(decision is False) >= ceil(repeat*2/3)` | ✅ |
| 候选所有重复均通过 | `all(passed and decision is True for new)` | ✅ |
| 开发断言不得退步、硬断言通过 | `no_regression` / `no_assertion_regression` / `hard_constraints` | ✅ |
| 未达标 = rejected_no_stable_gain，不消费封存集 | 提前 return，`rsi_holdout_uses` 0 行 | ✅ |
| 验证集满分允许持平 | `gates.pop('improved')` → `observed_improvement` 仅记录 | ✅（本轮未走到） |

两处小问题（不影响判定，建议改）：

1. `stable_repairs=true` 但 `no_regression=false` 时，状态仍叫 `rejected_no_stable_gain`；名字只说"没稳定增益"，实际是"有增益但伴随退步"。`reason` 文本覆盖了，状态名容易误读。
2. `repeat<3` 时 `repaired` 恒为空，只落到同一句 reason，没有"repeat 不足"的专门提示。

## 二、预算重算（旧 97 次不适用）

新链路开发集跑两遍，按上一轮实测的每遍调用数重算：

| 阶段 | 单遍 | 次数 | 小计 |
|---|---|---|---|
| 开发集（基线） | 12 | 3 | 36 |
| 开发集（候选复跑） | 12 | 3 | 36 |
| 候选生成 | — | 1 | 1 |
| 验证集（新旧各 3 次） | 6 | 6 | 36 |
| 封存集（新旧各 3 次） | 3 | 6 | 18 |
| 发布后（验证集 ×1） | 6 | 1 | 6 |
| **合计** | | | **133 > 128** |

**超限 5 次**。处理：验证集从 4 例收缩到 3 例（移除 `E-val-off-topic`，该例每遍占 2 次），降到 **119/128**。这是覆盖收缩，不是判分放松；`no_regression`、`hard_constraints`、`holdout_passed` 均未动。
代价明说：验证集不再覆盖"无关话题"边界。

实际消耗：闭环 **73** 次 + 候选生成 1 次 = **74** 次；token **98,098**（95,714 + 2,384），预留口径 503,734 / 1,000,000。开发集复跑完成后即被拒，验证/封存/发布后未执行，所以低于 119。

## 三、开发集：基线 vs 候选（repeat=3）

| 用例 | 基线通过 | 候选通过 | 基线意图 | 候选意图 |
|---|---|---|---|---|
| deny-missing T1 | 2/3 | 3/3 | correct, **decline**, correct | correct ×3 |
| complaint T1 | **0/3** | **3/3** | continue, defer, handoff | unclear ×3 |
| view-materials T1 | 3/3 | **2/3** | question ×3 | question, question, **unclear** |
| third-party T1 | 3/3 | **2/3** | handoff ×3 | **defer**, handoff, handoff |
| auth-number T1 | **0/3** | **3/3** | unclear, off_topic, unclear | question ×3 |
| falsify-guard T1 | 3/3 | 3/3 | decline ×3 | correct, decline, unclear |

- 汇总：基线 29/36 → 候选 34/36。
- 稳定修复家族：`complaint_sarcasm`、`authorization_number_question`（均基线 0/3、候选 3/3）→ `stable_repairs=true`。
- 未达"2/3 判错"门槛所以不算修复证据的：deny-missing 基线只有 1/3 判错（未达 2/3）；third-party 基线 0/3 判错（方向相反）。
- 两处退步（各 1 次）：`view-materials` 3/3→2/3、`third-party` 3/3→2/3。

这就是拒绝的原因：`no_regression=false`、`no_assertion_regression=false`，其余四项（`aligned`、`hard_constraints`、`improved`、`stable_repairs`）都是 true。

## 四、候选与提示词

`candidate-561a113dbc8f`，guidance 418 字（base 1801 → 2219 字）。要点：材料异议归 `correct` 不归 `decline`；只有不满/敷衍且无业务内容归 `unclear`、不默认 `continue`；问编号/记录/渠道归 `question/other`；混合情绪以业务为主。未改意图集合、未改登记确认与工具权限。

值得注意：候选自己写了"明确要求人工或情绪激烈升级可归 handoff"，但 `我让店长回你电话` 仍有一次被判 `defer`——不是提示词没写，是单次采样波动。

## 五、判定与状态

| 项 | 值 |
|---|---|
| 状态 | `rejected_no_stable_gain` |
| 候选 | `candidate-561a113dbc8f`（保留在沙箱库，未启用） |
| 沙箱 active | `base-6b13e03f1b116f21`（未变） |
| `evolution_releases` | 0 行 |
| `rsi_holdout_uses` | 0 行（封存集仍未消费） |
| 开发轨迹 | 36 轮、7 条 intent 反馈 |
| 生产库 | active `base-6b13e03f1b116f21`，runs=0，未打开写入 |

## 六、这轮真正学到的

1. **新门槛能工作，而且拦得有道理**：它不是"没涨分就拒"，而是先承认定向修复，再看有没有把别处弄坏。这次候选把两个 0/3 的家族做成 3/3，如果只看总分（29→34）就该发布；门槛拦下的是总分掩盖的两处回退。
2. **但 `no_regression` 是"单次尝试级"的，模型又没有固定随机性**：`qwen_client.request_body` 不传 `temperature`/`seed`，走服务端默认采样。所以"某轮 3/3 → 2/3"既可能是真退步，也可能是一次采样抖动。本轮两处退步都只有 1 次，且同一轮另外 2 次仍对。
3. 建议在判定口径上二选一，并在文档里写明：
   - (a) 判定按**家族多数票**（如 3 次中 2 次通过不算退步，1 次算），或
   - (b) 评测请求固定 `temperature=0` / `seed`，让 `no_regression` 的"单次"有确定性含义。
   否则每一轮都可能因为一次抖动否掉一个真实有效的候选，而这恰好是"宁可漏发"方向，代价是闭环长期产不出发布。
4. 适配缺口没变：`score()` 仍不能注入 `pending`、已有记录、故障位，v3 的 S 组与 R012 仍进不来。

## 七、下一步（未执行，等确认）

- 若按 (b)：给分类请求加 `temperature=0`（或可配 seed），重跑同一套 r2 数据，才有可比性；这会改产品请求体，属源码改动，需你点头。
- 若按 (a)：改 `compare()` 的 `no_regression` 粒度，同样要改源码。
- 两者都不做的话，可以换一个更"聚焦"的开发集（只保留基线 0/3 且候选稳定的家族），但那属于按结果挑样本，我不建议。
