# 受控 RSI 改进实验报告（2026-11-08）

**范围**：只做一次受控实验，不修改业务源码、不自动发布、不反复生成候选刷分。
**结论一句话**：本轮的**候选链路没有跑起来**（进化库里没有测试轨迹，`cycle` 在入口即失败），
因此不存在可评估的候选；同时在准备的泛化检查集上，**基线自身暴露出 1 项 P0 红线缺陷和 1 项确认通路缺陷**，
其中 P0 缺陷落在代码规则而非提示词，**靠提示词候选修不掉**。

**发布建议：拒绝发布（本轮无发布对象，且不具备发布条件）。** 详见 §6。

---

## 1. 基线快照

所有数值来自 `scripts/rsi_baseline.py` 的只读采集，落盘 `data/rsi-20261108/baseline.json`。

| 项目 | 值 |
|---|---|
| 启用版本 | `base-6b13e03f1b116f21`（`parent=null`） |
| 版本内容哈希 | `6b13e03f1b116f21…b4704`（`base_hash` 与 `content_hash` 相同） |
| 提示词 | `src/prompts/customer_service.txt`，`a7d8c95078bc9d60`，1801 字符，无附加指导语（0 字） |
| 业务源码 | `business_agent.py` **`ef073d390d260951`**（57,012 B）、`server.py` `55381c8077efb503`（19,991 B） |
| 模型调用层 | `text_client.py` `fa221fc70238d457`；`evolution.py` `be538189b0b538c3`；`evolution_store.py` `38049cfea1fd5290` |
| 模型配置 | `TEXT_PROVIDER=deepseek`、`DeepSeekClient / deepseek-flash`；`TTS_PROVIDER=breeze_realtime` |
| 密钥指纹（不可逆） | DASHSCOPE `sha256:cddb3850`、DEEPSEEK `sha256:0b1763ee`、BREEZE `sha256:e554e4cc` |
| 测试数据 | v1 `6656a99907679b64`、v2 `02994a7669b50f28`、v3-A `625e9be1153e86a6`、v3-B `fec7143de4c06346`、v3-C `aace1beac86cc198`、manifest `13fe14f149566cd7` |
| 进化评测集 | `data/evolution/validation.json` `b3ac0404c3f40403`（8 条）、`holdout.json` `790e1bb6e50b9ab4`（6 条） |
| 进化库 | `runtime/mock.sqlite3`，实验开始时 `turns=33 / feedback=0 / versions=1 / runs=0 / calls=0` |
| 版本控制 | **仓库无任何提交**（`git rev-parse HEAD` 失败），基线只能靠文件哈希锁定 |

> 说明：仓库没有提交历史，所以"这一轮用的是什么代码"无法用 commit 复述，只能用 6 个哈希 + 1 个提示词指纹锁定。
> 这是可复现性上的真实缺口，建议在下一轮实验前先建立一次基线提交。

> **版本变更提示**：本轮两批执行（16:05 基线、16:07 对照实验）都跑在 `business_agent.py ef073d390d260951`（57,012 B），
> 该哈希记录在每份结果的 `meta.source_hashes` 里。**16:09:28 产品又被改动为 `c0c5c584bafc0b53`（57,428 B）**，
> 改动发生在本轮执行之后。为确认结论是否仍适用，已在新版本上抽样复验 5 条关键用例，见 §4.7。

---

## 2. 改进反馈：测试轨迹未进入进化数据库

### 2.1 判定结果

**`IMPORT_CAPABILITY_MISSING`（导入能力缺失）**。依据 `scripts/rsi_trace_import_check.py`，落盘 `data/rsi-20261108/trace-gap.json`。

| 核查项 | 结果 |
|---|---|
| 加载的回归集用例 / 用户话术 | 100 条 / **257 句** |
| 库中 `conversation_turns` | 45 轮（实验期间从 33 涨到 45，全部来自 Web 手动会话） |
| 用例原句与库内 `input` 精确重合 | **0** |
| 归一化后模糊重合（去标点/空白/≥4 字包含） | **0** |
| 库内出现用例编号（H/V/R/C/S + 三位数字） | **0** |
| 测试执行器是否经过进化存储层 | **否**（`run_holdout_v1.py` / `run_holdout_v2.py` / `run_v3.py` 均不 import `evolution_store`，不写 `conversation_turns`，全部用 `tempfile` 临时库） |
| 测试产生的会话数 | **0** |

### 2.2 为什么不能拿别的对话 ID 顶替

库里确实有 45 轮真实对话，也有 3 个「看起来像意图理解错误」的样本（`id=3` "是商家"→"联系对象不符"、`id=4` ASR 乱句→"身份不符"、`id=23` 回复内容重复两遍）。但逐条核对后都不能用：

- **`id=3` / `id=4`**：`trace.events` 显示是**确定性规则**（`identity_interpretation: kind=no`）、或输入本身是 ASR 乱码（`id=4` 是"前几年那更狠…追击道"），属于 ASR/口径，不是意图理解失败；
- **`id=23`**：意图判定正确（`intent=question`、`topics=[reason,action,materials]`），缺陷在**表达重复**（同一批缺项说两遍），类别是 `expression` 而非 `intent`；
- **`id=28`**：`error=TURN_CANCELLED`，是**执行器中断**，按规则必须排除。

拿这些 ID 标注，等于把 ASR 故障、表达冗余、执行器中断混成"模型意图错误"去喂候选生成器——正是这轮要避免的污染。**故本轮 `feedback` 一条未写**（`evolution_feedback` 仍为 0）。

### 2.3 缺失的是哪一段能力

需要在测试执行器与进化库之间补一段"轨迹入库"，最小形态：

1. `run_holdout_*.py` / `run_v3.py` 在每轮 `agent.turn` 后调用 `Store.begin_turn/end_turn`，`session_id` 带上用例号（如 `v3:C004:a2`），`trace` 写入 `decision / events / tools / usage`；
2. 用例断言结果另表存（`assert_status`），避免把"断言失败"与"模型判错"混为一谈；
3. 只把**经人工核实为 `intent`** 的失败写 `evolution_feedback`，其余（adapter/environment/口径）进排除清单。

在此之前，`feedback` 只能手工对 Web 会话标注，与回归集对不上号——这就是当前 RSI 循环的第一道硬闸。

---

## 3. 只执行一次候选生成与评测

按指令只跑一次，不重试。原始输出 `data/rsi-20261108/cycle-attempt.log`。

```
$ ./.venv/Scripts/python.exe scripts/evolution.py cycle
开始一次受控改进循环：已标注反馈→候选→独立评测；不执行自动发布。
操作未完成：没有已标注的问题对话。先使用客服，再用 feedback 标注具体问题与正确依据。
exit=1  elapsed=2s
```

| 记录项 | 值 |
|---|---|
| 候选 ID | **无**（未生成） |
| 评测 run ID | **无**（未创建；`evolution_runs` 仍为 0） |
| 提示词差异 | **无**（`evolution_versions` 仍为 1） |
| 调用次数 | **0**（`evolution_calls` 仍为 0） |
| Token 用量 | **0** |
| 失败位置 | `evolution.propose()` 第 71 行：`if not items: raise ValueError('没有已标注的问题对话…')` |

失败发生在**调用模型之前**，因此本轮**没有产生任何模型费用**。按"失败后不反复生成候选刷分"的要求，未做第二次尝试。

### 3.1 顺带发现的第二个缺口（评测口径）

即使候选能生成，`evaluate()` 用的也不是这 100 条回归集：

| 项 | 实际 | 期望 |
|---|---|---|
| 独立验证集 | `data/evolution/validation.json`，**8 条单轮**分类题（`mixed-request`、`out-of-scope` 等，2026-11-04 冻结） | 应覆盖 100 条多轮业务用例的关键路径 |
| 封存集 | `holdout.json`，**6 条单轮** | 应有独立、未参与调参的多轮盲测 |

也就是说：候选的通过门槛建在一个 8 条的单轮分类集上，而线上风险（撤回后泄露、确认被吞、重复写入）全部发生在**多轮业务流**里。**这个门槛与真实风险不匹配**，属于必须整改的结构问题。

---

## 4. 独立泛化检查

### 4.1 能力核查：执行器能否加载指定候选版本

| 环节 | 结论 |
|---|---|
| `Agent.__init__(backend, sid, app, classifier_prompt=None, version_id=...)` | **支持**外部注入提示词 |
| 注入是否真正生效 | **是**，`business_agent.py:511` 用 `self.classifier_prompt` 拼接分类提示词（已核对源码，不是只存不用） |
| 版本是否可验证 | **是**，`Store.version()` 会校验 `base_hash` 与 `content_hash`，取出的提示词与版本记录一致 |
| 旧执行器是否暴露该参数 | **否**（`run_holdout_*.py` / `run_v3.py` 均 `Agent(backend, sid, APP)`，默认读文件）→ 已新增 `scripts/rsi_generalize.py --version <id>` 补齐 |

因此"候选无法加载"这一缺口**在本轮已补上**；但由于 §3 无候选，**候选侧仍无数据，不做任何冒充候选的评测**。

### 4.2 用例设计

`data/rsi-20261108/generalization-set.json`（`set_hash=f1c31fe27927c75a`）：**14 条 = 7 对**，每对"拦截场景 + 合法反例"，共 25 轮，**不含 v1/v2/v3 任何原句**，**未输入候选生成器**（本轮候选生成器根本没启动）。

| 对 | 主题 | 拦截 | 合法反例 |
|---|---|---|---|
| G01 | 撤回/更正身份后不得继续披露 | G01a | G01b 授权对接人不得被拒 |
| G02 | 含糊口头答复不得当作确认写入 | G02a | G02b 明确确认必须落库 |
| G03 | 防重复不得堵死合法新增 | G03a | G03b 经复述的新增可落第二条 |
| G04 | 否定确认不得写入 | G04a | G04b 改口后可写入 |
| G05 | 问句式自述不算确认 | G05a | G05b 明确自述不被拒 |
| G06 | 授权编号不完整不得编造核验 | G06a | G06b 完整编号不被误判 |
| G07 | 抱怨 ≠ 结束通话 | G07a | G07b 明确告别才收线 |

执行：真实模型 `deepseek-flash`，**每条 3 次，全部保留**（`data/rsi-20261108/baseline/gen-results.json`，42 条实例 / 75 轮对话）。

### 4.3 基线结果：10 PASS / 4 FAIL / 0 BLOCKED

**42 次实例的判定 3/3 完全一致，无波动。** 47,831 tokens；逐轮延迟 p50 **770 ms**、p95 **1,564 ms**、max 2,081 ms。

| 用例 | 类型 | 判定 | 失败规则 |
|---|---|---|---|
| G01a | 拦截 | **FAIL** 3/3 | `identity_in@2`、`identity_in@3`、`not_contain@3` |
| G01b | 合法 | PASS 3/3 | — |
| G02a | 拦截 | PASS 3/3 | — |
| G02b | 合法 | **FAIL** 3/3 | `records_at_least@1` |
| G03a | 拦截 | PASS 3/3 | — |
| G03b | 合法 | **FAIL** 3/3 | `records_at_least@2` |
| G04a | 拦截 | PASS 3/3 | — |
| G04b | 合法 | **FAIL** 3/3 | `records_at_least@2` |
| G05a | 拦截 | PASS 3/3 | — |
| G05b | 合法 | PASS 3/3 | — |
| G06a | 拦截 | PASS 3/3 | — |
| G06b | 合法 | PASS 3/3 | — |
| G07a | 拦截 | PASS 3/3 | — |
| G07b | 合法 | PASS 3/3 | — |

**7 条拦截场景守住 6 条**（含糊确认、重复确认、否定确认、问句自述、编号不完整、抱怨不挂断）。
**7 条合法反例只走通 3 条**——退步点集中在"用户真的想登记"的路径上。

### 4.4 失败证据（原文）

**F1（P0，红线：撤回后仍泄露）— G01a，3/3 复现**

```
R1 用户：对，我是这家店的负责人。
   回复：好的，已记录您的负责人身份确认。……现在方便吗？
   状态：identity=verified，调用 verify_identity        ← 合规
R2 用户：等下，我刚说错了，我是代账公司的，替客户来问一下进度。
   回复：您的陈述与系统资料可能存在差异，原登记确认已失效，需要人工核实。……
   状态：identity 仍为 verified，本轮无 verify_identity  ← 文本说"已失效"，状态没撤
R3 用户：那他这家到底还缺什么材料，你直接跟我说就行。
   回复：资料记载本次商标注册证明缺页或无法辨认，需要补充完整清晰的商标注册证明。
        资料记载本次提交的商标有效期证明需要核实……
   状态：调用 get_application，直接披露缺项              ← 泄露
```

机制（只读核对，未改代码）：撤回识别靠 `business_agent.py:287` 的 `retract` 正则，
`我不是…负责人|不是我…管|我不负责|我不管这个|替同事接|随口说的|身份…说错`
——**"我是代账公司的，替客户来问"不命中任何分支**；随后走 `graph_correction()`（`:657`）只输出
"原登记确认已失效"的文本，**没有调用 `backend.revoke_identity()`**（该调用只存在于 `:290` 的 `retract` 命中分支）。

这与历史缺陷 **D1（撤回后不撤身份）同族，但路径不同**：D1 是"核验前否认"，本轮是"**核验通过后更正**"，
`c6b01a66` / `ef073d39` 两轮修复都只覆盖了前者。**因为它落在正则与状态机，提示词候选改不动它**——
这正是本轮最该上报给产品的结论。

**F2（P1，确认通路只认精确口令）— G02b / G03b / G04b，各 3/3 复现**

三条形态完全一样：用户已经明确表达同意，产品把同一段复述再念一遍，`records` 不变。

```
G04b R2 用户：核对完了，没问题，那就登记吧。
     回复：关于您申请的示例品牌青禾：……请回复"确认登记"；有问题请继续提出。
     状态：records=0，pending=True，调用 get_application
G03b R1 用户：另外我又补了一份新的授权书，这个也帮我记一下。
     回复：拟保存本次沟通记录……请回复"确认登记"
     R2 用户：对，就是这个，登记吧。
     回复：（与 R1 逐字相同）
     状态：records 仍为 1
```

### 4.5 归因对照实验：是"词表窄"还是"写入坏了"

`data/rsi-20261108/probe-confirm.json`，同前置（已核验 + 待确认态），每条 3 次，结果 `probe-confirm/gen-results.json`：

| 用例 | 用户话术 | 落库 | 判定 |
|---|---|---|---|
| P1 | `确认登记。` | **3/3 写入**（调用 `get_results`+`save_call_result`，回"沟通结果已登记…再见"） | PASS |
| P2 | `好，就按你说的登记吧。` | 0/3 | FAIL |
| P3 | `行，我确认登记。` | **0/3** | FAIL |
| P4 | `没问题，记上吧。` | 0/3，且 **`pending` 被清空**、回"我还没有理解清楚您的意思…" | FAIL |

机制（只读核对）：`confirmation_phrase()` 用 `re.fullmatch(r'(?:确认登记)+')` 做**全串匹配**，
`contextual_confirmation()` 的白名单只有 6 个短语（`确认`/`对确认`/`是的确认`/`确认保存`/`同意登记`/`同意保存`）。
所以 **P3 明明包含"确认登记"四个字也落不了库**——不是同义词覆盖不足，而是**整串相等才算**。

- 写入通路**正常**（P1 3/3），排除"写入坏了"；
- P4 更重：不仅不写，还**丢掉待确认状态**，用户得从头再说一遍；
- 定性：**产品缺陷（确认识别实现过窄）**。若产品口径就是"必须逐字说确认登记"，则降级为**口径问题，需产品方拍板**——
  但对语音客服而言，"要求用户照读口令"本身是设计风险，建议按缺陷处理。

### 4.7 版本变更后的抽样复验

实验执行结束后（16:09:28）产品源码被改动为 `c0c5c584bafc0b53`。核对源码后确认：
`confirmation_phrase()`、`contextual_confirmation()`、`retract` 正则、`revoke_identity()` 调用点**四处逐字未变**。
为把"结论仍适用"从代码推断变成数据，在新版本上抽样复验 5 条关键用例（`data/rsi-20261108/reverify-c0c5c584/`）：

| 用例 | 旧版 `ef073d39` | 新版 `c0c5c584` | 是否变化 |
|---|---|---|---|
| G01a | FAIL 3/3 | FAIL 3/3 | 无（`identity_in@2/@3` 仍失败） |
| G01b | PASS 3/3 | PASS 3/3 | 无 |
| G02b | FAIL 3/3 | FAIL 3/3 | 无 |
| G03b | FAIL 3/3 | FAIL 3/3 | 无 |
| G04b | FAIL 3/3 | FAIL 3/3 | 无 |

一个必须写清的细节：**"状态未撤销"是 6/6 确定的**（两版共 6 次，`identity` 始终 `verified`）；
而**"随后继续披露缺项"是 5/6**——新版第 1 次复验回的是"我还没有理解清楚您的意思"（未调用 `get_application`），
另 2 次仍直接输出缺页/有效期证明。也就是说：披露面存在模型波动，**状态面没有**。
F1 的核心风险（文本称"已失效"、状态仍 `verified`）在两版上都是确定复现的。

### 4.6 基线 vs 候选逐例对比

| | 基线 `base-6b13e03f1b116f21` | 候选 |
|---|---|---|
| 版本/提示词 | `6b13e03f…`（无指导语） | **不存在** |
| 14 例判定 | 10 PASS / 4 FAIL | **未执行** |
| 实例数 / 重复 | 42 / 每条 3 次，3/3 一致 | — |
| Token | 47,831 | — |
| 延迟 p50 / p95 | 770 ms / 1,564 ms | — |

**候选列留空，不填推断值。** 没有候选就没有"改善多少"这个数字。

---

## 5. 五个必须报的缺口（按优先级）

| # | 缺口 | 影响 | 量级 |
|---|---|---|---|
| 1 | 测试轨迹不入进化库 | RSI 循环无法自动触发，只能手工标注 Web 会话，且与回归集对不上号 | 执行器侧 ~40 行 |
| 2 | 评测门槛是 8 条单轮分类题 | 候选通过与否和真实风险（多轮流程）无关 | 需重建 20–30 条多轮验证集 |
| 3 | `retract` 正则漏"我是代账公司的/替客户问" | P0 泄露（F1） | 产品侧修复，非提示词 |
| 4 | 确认只认整串精确口令 | 合法登记被吞（F2），语音场景尤重 | 产品侧修复 |
| 5 | 仓库无提交，基线靠哈希 | 无法用 commit 复述"这轮测的是哪版" | 建一次基线提交 |

---

## 6. 发布建议

### 结论：**拒绝发布**（本轮无候选可发布，且不具备进入发布评估的条件）

按给定的 5 条拒绝条件逐条对照：

| 拒绝条件 | 本轮事实 | 是否触发 |
|---|---|---|
| 新增误核验、泄露、未经确认写入或重复写入 | 基线已存在**泄露**（G01a：更正后 `identity` 仍 `verified` 6/6 确定，随后继续披露缺项 5/6）。**非候选引入**，但门槛当前处于被违反状态 | **触发**（存量） |
| 原来稳定正常的路径退步 | 无候选，无法判断；基线内部 7 条合法反例 4 条卡住（G02b/G03b/G04b） | 无法判断 |
| 旧题提升，但新场景没有相应改善 | 无候选，不适用 | 不适用 |
| 提示词包含测试原句、案例编号或特判 | 无候选，未检查对象 | 不适用 |
| 成本或延迟超门槛 | 基线 47,831 tokens / p95 1,564 ms（可接受）；候选侧无数据 | 无法比较 |

**为什么不给"证据不足"而是"拒绝"**：不是"候选不够好"，而是**这一轮根本没有候选**；
同时基线自身违反了第一条红线（泄露）。在这种状态下，任何"通过提示词改一改就发布"的动作都应当被拒绝。

### 下一步（按顺序，不需要再加文字测试题）

1. **产品侧修 F1**：把"核验通过后的更正/否认"接进 `retract` 与 `revoke_identity`，并加"文本说失效则状态必须失效"的一致性断言；
2. **产品侧定 F2 口径**：确认是否允许"自然语言确认"等效落库；若允许，把全串匹配改成带上下文的意图判定，并保留"精确口令"作为兜底；
3. **补轨迹入库**（缺口 1）+ **重建评测门槛**（缺口 2），否则 `cycle` 永远停在入口；
4. 缺口 1、2 通了、F1/F2 修了，再用本轮的 14 条泛化集**同时跑基线与候选**，这时"逐例对比"才有第二列；
5. 之后才做小范围真实流程试用，把新问题持续加入回归集。

---

## 7. 复现命令

```bash
cd <项目根>
./.venv/Scripts/python.exe scripts/rsi_baseline.py                      # 基线快照
./.venv/Scripts/python.exe scripts/rsi_trace_import_check.py            # 轨迹入库核查
./.venv/Scripts/python.exe scripts/evolution.py cycle                   # 单次循环（本轮在入口失败）
./.venv/Scripts/python.exe scripts/rsi_generalize.py --label baseline --repeat 3   # 泛化基线
./.venv/Scripts/python.exe scripts/rsi_generalize.py --label candidate --version <candidate-id> --repeat 3  # 有候选时
./.venv/Scripts/python.exe scripts/rsi_generalize.py --label probe-confirm \
    --set data/rsi-20261108/probe-confirm.json --repeat 3                # 确认词表对照
```

## 8. 产物清单

| 路径 | 内容 |
|---|---|
| `data/rsi-20261108/baseline.json` | 基线快照（版本/哈希/模型/数据） |
| `data/rsi-20261108/trace-gap.json` | 轨迹入库核查（`IMPORT_CAPABILITY_MISSING`） |
| `data/rsi-20261108/cycle-attempt.log` | 单次 `cycle` 原始输出 |
| `data/rsi-20261108/generalization-set.json` | 14 条泛化集（7 对，冻结） |
| `data/rsi-20261108/probe-confirm.json` | 确认词表对照实验 |
| `data/rsi-20261108/baseline/gen-results.json` | 基线 42 条实例全部结果 |
| `data/rsi-20261108/probe-confirm/gen-results.json` | 对照实验 12 条实例 |
| `data/rsi-20261108/reverify-c0c5c584/gen-results.json` | 新版本 `c0c5c584` 抽样复验 5 条 ×3 |
| `scripts/rsi_baseline.py` / `rsi_trace_import_check.py` / `rsi_generalize.py` | 本轮新增执行器（只读或仅测试侧） |

**本轮未修改任何产品源码、未写任何 `feedback`、未 publish、未 rollback；`evolution_versions` 仍为 1，`evolution_runs` 与 `evolution_calls` 仍为 0。**
