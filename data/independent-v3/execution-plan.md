# v3 盲测集执行方案

> 本文件只描述**怎么执行**。本批用例尚未执行，任何"通过/失败"结论都还不存在。

## 1. 产物位置

目录：`data/independent-v3/`

| 文件 | 条数 | 说明 |
|---|---|---|
| `real-model-v3.json` | 20 | A 组：真实模型对话（R001–R020），禁止关键词替身 |
| `state-fault-v3.json` | 12 | B 组：状态与故障（S001–S012），真故障注入 |
| `content-consistency-v3.json` | 8 | C 组：登记内容一致性（C001–C008） |
| `manifest.json` | — | 条数、来源、SHA256、版本、状态统计 |
| `execution-plan.md` | — | 本文件 |

v1 / v2 的冻结用例、基线、结果文件一律未改动。

## 2. 接口映射（均取自当前源码，未虚构）

### 2.1 对话入口

| 项 | 位置 | 说明 |
|---|---|---|
| 单轮对话 | `src/business_agent.py:151` `async def turn(self, text, model, verified)` | 一行用户输入 → 一次回复；本批全部用例按此逐轮推进 |
| 工具调用 | `src/business_agent.py:117` `tool()` / `:129` `write()` | 只走白名单工具，写入需 `Backend.authorize_write` 预授权 |
| 身份状态 | `src/server.py:90` `identity_status(sid)` | 取值 `unverified` / `verified` / `wrong_contact` |
| 会话状态 | `agent.step` | 观测值 `introduction` / `identity` / `availability` / `confirmation` / `saved` / `declined` / `closing` / `questions` |

### 2.2 模型（A 组硬要求）

| 项 | 值 |
|---|---|
| 选择器 | `src/text_client.py` `text_client()`；`TEXT_PROVIDER=qwen` |
| 实现 | `src/qwen_client.py` `QwenClient`，`base_url` 取 `DASHSCOPE_BASE_URL`，模型取 `QWEN_MODEL` |
| 备选 | `TEXT_PROVIDER=deepseek` → `DeepSeekClient`（`DEEPSEEK_MODEL`） |
| 提示词 | 身份理解提示词内联在 `src/business_agent.py:171-175`；客服主提示 `src/prompts/customer_service.txt` |

A 组执行时必须记录**实际生效的模型名**与**提示词哈希**；用关键词替身跑的 A 组结果**判为无效**，不计入任何统计。

### 2.3 工具面与参数约束（`src/server.py:118`）

| 工具 | 必填参数 | 关键约束 |
|---|---|---|
| `verify_identity` | `session_id`, `is_responsible`(bool) | 只在 `unverified` 时可改变身份 |
| `get_application` | `session_id`, `application_id` | 需 `verified`；`application_id` 必须等于会话所属申请 |
| `get_results` | `session_id` | 未核验时按 `visible_record` 裁剪字段 |
| `get_operation_status` | `session_id`, `idempotency_key` | 无记录返回 `status=unknown`，不得编造标识 |
| `save_call_result` | `session_id`, `idempotency_key`, `result_revision`, `result` | 需 `authorize_write` 预授权；`result_revision` 必须为上一版 +1 |
| `create_handoff` | `session_id`, `idempotency_key`, `consent`(true), `reason` | 只建模拟工单，未接通真人 |

### 2.4 数据库观测点（`runtime/*.sqlite3`）

| 表 | 用途 |
|---|---|
| `sessions` | `identity` 字段 |
| `records` | `revision`、`payload`（含 `candidate_outcome` / `issues_explained` / `confirmation_turn_id` / `unresolved`） |
| `handoffs` | 工单条数 |
| `operations` | 幂等台账（`key`、`request`、`response`），判断是否重复写入 |
| `call_progress` | 阶段流水，用于"结束事件""撤回事件"计数 |

## 3. 执行方式

### 3.1 A 组（真实模型）

1. 逐例新建临时库 + `Backend(path, faults=False)` + `Agent(backend, sid, APP)`。
2. 按 `preconditions` 构造前置状态：**身份必须走 `verify_identity` 真实调用**；已落库记录必须走 `authorize_write` + `save_call_result`，禁止直插 `records`（会造成"有记录无台账"的假状态）。
3. 每轮：`await agent.turn(text, real_model, False)`，实时模型实例**每例新建**，避免上下文串味。
4. 每轮后采样：`identity_status`、`agent.step`、`agent.pending`、`records/handoffs` 行数、本轮工具调用序列。
5. 全例跑完后按 `final_assertions` 逐条判定，`【需适配】`记 BLOCKED 并写原因。
6. **重复 3 次**，逐次落盘为 `runs/attempt-1..3.json`，报告必须展示三次全部结果，不允许只报最好的一次。

### 3.2 B 组（状态与故障）

| 用例 | 执行路径 | 状态 |
|---|---|---|
| S004 | 进程内，真实打断时序 | 可直接执行 |
| S006 | **必须走 HTTP**：`POST /save_call_result` 带 `X-Mock-Fault: response_lost` | 可直接执行（需 HTTP 驱动） |
| S008 / S009 | 进程内 `Backend.call(name, params, fault='query_fail'/'write_fail')` | 可直接执行 |
| S010 | 进程内真实重复输入 | 可直接执行 |
| S001 / S002 / S003 / S005 / S007 / S011 / S012 | 见 §4 BLOCKED 清单 | **BLOCKED** |

`response_lost` 只在 HTTP 层生效（`src/server.py:246-247` 直接关连接、不回包），进程内调用 `Backend.call` 不会触发——这一点必须在执行器里显式区分，否则会得到"故障没发生"的假通过。

### 3.3 C 组（内容一致性）

按"意向 → 复述 → 确认 → 工具参数 → 库内记录"五段逐段留证：

| 段 | 取证方式 |
|---|---|
| 用户意向 | 原始 `user_input` |
| Agent 复述 | 该轮 `reply` 全文 |
| 用户确认 | 确认轮 `user_input` + `agent.turn_number` |
| 工具参数 | 该轮 `save_call_result` 的实参（`result.candidate_outcome` / `issues_explained` / `confirmation_turn_id` / `unresolved`） |
| 库内记录 | `records.payload` 全字段 + `revision` |

结论判定必须写明**在哪一段发生了扩大**（例如"复述正确，落库时把 `unresolved` 从 true 写成 false"），不接受只说"不一致"。

## 4. 缺失能力与 BLOCKED 清单（7 条）

| 用例 | 缺什么 |
|---|---|
| S001 | 延迟投递：无法让某一轮的模型分类结果延后到后续轮次返回 |
| S002 | 延迟投递：无法让工具回执延后到达 |
| S003 | 延迟投递：无法让 `get_application` 响应延后到身份变更之后到达 |
| S005 | 异步队列：无法在取消之后让排队中的写入继续执行 |
| S007 | 超时注入：现有故障只有 `query_fail` / `write_fail` / `response_lost`，无法制造"客户端超时且服务端结果未知" |
| S011 | 并发驱动：执行器单线程逐轮推进，无法制造真正的并发确认 |
| S012 | 响应模板替换：无法返回 200 但结构非法/字段缺失的响应 |

补齐建议（按性价比排序）：

1. **延迟投递**：在适配层给每轮结果加 `deliver_at_turn`，一行钩子即可覆盖 S001–S003。
2. **超时注入**：`Backend.call` 增加 `fault='timeout'`，在写入后 sleep 超过客户端超时阈值。
3. **响应模板替换**：`Backend.call` 增加 `response_override`，直接替换返回体。
4. **异步队列 + 并发**：成本最高，建议只在上三项补齐后再考虑。

## 5. 执行记录字段（每次运行必须落盘）

```json
{
  "suite": "v3-20261108",
  "case_id": "R001",
  "attempt": 1,
  "model": {"provider": "qwen", "model_id": "<实际生效模型名>", "params": {"temperature": null, "max_tokens": null}},
  "prompt_sha256": "<身份理解提示词 + customer_service.txt 的合并哈希>",
  "source_sha256": {"business_agent.py": "...", "server.py": "..."},
  "adapter_version": "v3-runner/0.1",
  "started_at": "ISO8601",
  "duration_ms": 0,
  "usage": {"prompt_tokens": 0, "completion_tokens": 0},
  "turn_evidence": [{"turn": 1, "user": "...", "reply": "...", "calls": [], "state": {}}],
  "assertions": [{"assert": "...", "status": "ok|fail|blocked", "detail": "..."}],
  "verdict": "PASS|PASS_PARTIAL|FAIL|BLOCKED",
  "attribution": "product|adapter|environment|model_variance"
}
```

`attribution` 四选一，取值含义：

- `product`：产品行为违反业务约束。
- `adapter`：执行器/判定器的问题，例如故障没注入成功、断言写错。
- `environment`：网络、模型服务 5xx、依赖缺失。
- `model_variance`：真实模型分类与人类语义不一致，且无法归为产品缺陷（需附原话与模型输出）。

## 6. 判定与归因纪律

- 四级判定 PASS / PASS(partial) / FAIL / BLOCKED，BLOCKED 不计通过。
- `【需适配】`断言一律 BLOCKED，不得因"没匹配到"判 PASS。
- A 组三次执行结果需并列呈现；三次不一致时**按最差一次定级**，并单独列出波动用例。
- 不根据当前实现推断"正确答案"：本批断言只引用本轮需求里的业务约束与通用语义，不引用旧报告里的修复结论。
- 产品歧义单列"待审"，见 §7，不擅自放宽门禁。

## 7. 待审项（执行前需要口径确认）

| # | 待审问题 | 涉及用例 |
|---|---|---|
| 1 | "登记沟通结果"是否允许在存在未解决事项时以 `unresolved=true` 落库，还是必须先解除问题 | C001、C004 |
| 2 | 用户自述"已收到/已了解"时，允许记录的最小语义边界是什么 | C001、C002、C007 |
| 3 | 撤回身份后由同一人来电并再次自述，是否必须二次核验（不接受"静默恢复"） | R012 |
| 4 | 授权编号能否作为本人负责的辅助证据（只作说明，不作认证） | R003 |
| 5 | 复述与用户原话"语义一致"的判定粒度：允许信息压缩，不允许增加承诺/结论 | C007、C008 |
| 6 | 结束后迟到输入的处置：丢弃 or 回一句固定结束语（两者都算合规，但必须唯一） | S001、S002、R013 |

## 8. 本轮明确不做

- 不执行任何用例（本文件不含任何结果）。
- 不修改产品代码。
- 不覆盖 v1/v2 的冻结用例、基线与结果。
- 不生成声学用例、不放音频占位。
