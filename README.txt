最新补充（2026-11-04）：已加入本地降噪、SQLite对话留存和受控提示词优化。
对话表 conversation_turns；离线管理入口 scripts/evolution.py；操作见 docs/evolution-operations.txt。
新表已在原数据库建立；启用新留存逻辑需要重启原语音后端。不会补回旧内存记录或自动发布候选。
自主进化第一版只优化分类指导，尚无网页管理中心；官方业务接口和比赛最终验收仍待完成。

语音联调记录（2026-11-04）：已接通Fun-ASR、千问和BreezeBlue，支持个人音色选择。
启动：powershell -ExecutionPolicy Bypass -File scripts/start-voice.ps1
打开：http://127.0.0.1:8766 （蓝色首页→开始语音对话）；可双击web/打开网页.cmd自动启动
逐步操作、配置、测试及边界见 docs/voice-setup.txt。业务仍为合成Mock，已接入经明确确认的受控结果登记。
语音服务使用 requirements-voice.txt 的依赖；原Mock服务仍为标准库实现。

以下为基础阶段历史记录，当前状态以本段及docs/voice-setup.txt为准。

智语外呼参赛项目
当前阶段：1—7基础准备与Mock后台完成，验收发现的F01/F02已修复；尚未接入模型、语音或官方业务接口。
启动：powershell -ExecutionPolicy Bypass -File scripts/start.ps1
检查：http://127.0.0.1:8765/health
服务仅绑定本机。Ctrl+C 停止。
Python：使用项目 .venv（3.12），当前仅用标准库，无第三方运行依赖。
目录：src 后端；web 前端页面（业务尚待联调）；data/synthetic 合成数据；tests 自动化测试；
docs 需求与环境；compliance 授权记录；scripts 启动工具。
先读 docs/requirements.txt、docs/progress.txt。
此骨架不是比赛成品；health 成功不代表实时语音或任务闭环验收通过。


第6—7步更新：可持久化内部Mock后台已实现（纯Python标准库+SQLite）。
接口和示例见docs/backend-api.txt；测试：.venv\Scripts\python.exe -m unittest discover -s tests -v。
默认数据库runtime/mock.sqlite3，重启保留。合成案例见data/synthetic。
9项测试通过，包含真实HTTP冒烟、身份变化后的缓存脱敏、跨修订确认校验；尚无Agent、语音或官方接口。网页适配器尚待联调。
配置通过进程环境变量传入；start.ps1不自动读取.env，.env.example仅为示例。
