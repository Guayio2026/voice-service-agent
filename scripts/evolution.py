"""Operator CLI; run with the project's Python. Only propose/evaluate call cloud APIs."""
import argparse
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from evolution_store import Store
from evolution import propose, evaluate, publish, rollback
from qwen_client import load_config, QwenError
from text_client import text_client

def main():
    parser = argparse.ArgumentParser(description='智语外呼：受控RSI管理；cycle --activate可在门槛通过后自动发布')
    parser.add_argument('--db', type=Path, default=ROOT / 'runtime/mock.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='初始化表并查看当前版本，免费')
    history = sub.add_parser('history', help='查看最新20轮已保存对话'); history.add_argument('--limit', type=int, default=20)
    detail = sub.add_parser('turn', help='查看一轮对话及执行记录'); detail.add_argument('id', type=int)
    feedback = sub.add_parser('feedback', help='标注错误和依据，供分析器读取')
    feedback.add_argument('id', type=int)
    feedback.add_argument('--category', required=True, choices=['intent','asr','knowledge','tool','flow','expression','other'])
    feedback.add_argument('--note', required=True)
    proposal = sub.add_parser('propose', help='调用一次文字模型，分析已标注问题并生成候选；会计费')
    proposal.add_argument('--token-budget', type=int, default=30000)
    cycle = sub.add_parser('cycle', help='完整多轮RSI：采集、候选、验证、封存与可选自动发布；会计费')
    cycle.add_argument('--config', type=Path, default=ROOT / 'data/rsi-loop/config.json')
    cycle.add_argument('--output', type=Path, default=ROOT / 'runtime/rsi-loops')
    cycle.add_argument('--activate', action='store_true', help='门槛通过后自动启用；发布后检查失败则回滚')
    cycle.add_argument('--proposal-token-budget', type=int, default=30000)
    cycle.add_argument('--token-budget', type=int, default=None)
    cycle.add_argument('--max-calls', type=int, default=None)
    sub.add_parser('rsi-status', help='只读查看最新完整RSI闭环阶段和结果，不调用模型')
    sub.add_parser('versions', help='列出提示词版本')
    difference = sub.add_parser('diff', help='查看候选相对于父版本的指导差异'); difference.add_argument('version')
    test = sub.add_parser('evaluate', help='独立验证16次调用，通过后封存测试12次；会计费')
    test.add_argument('version'); test.add_argument('--max-calls', type=int, default=40)
    test.add_argument('--token-budget', type=int, default=250000)
    sub.add_parser('runs', help='列出分析/评测任务')
    report = sub.add_parser('report'); report.add_argument('run')
    release = sub.add_parser('publish', help='审核后发布已通过评测的版本')
    release.add_argument('run'); release.add_argument('--reviewed', action='store_true')
    undo = sub.add_parser('rollback'); undo.add_argument('version'); undo.add_argument('--reason', required=True)
    args = parser.parse_args()
    store = Store(args.db)
    if args.command == 'status':
        result = {'database':str(args.db.resolve()),'active_version':store.active(),
                  'turns':store.one('SELECT COUNT(*) AS n FROM conversation_turns')['n'],
                  'feedback':store.one('SELECT COUNT(*) AS n FROM evolution_feedback')['n'],
                  'notice':'旧内存对话不能补回；新会话使用启用版本。优化不自动运行、不自动发布。'}
    elif args.command == 'history':
        if not 1 <= args.limit <= 100: raise ValueError('limit须为1至100')
        result = store.rows('SELECT id,input,response,status,version_id,started_at FROM conversation_turns ORDER BY id DESC LIMIT ?', (args.limit,))
    elif args.command == 'turn':
        result = store.one('SELECT * FROM conversation_turns WHERE id=?', (args.id,))
        if result: result['trace'] = json.loads(result['trace'] or '{}')
    elif args.command == 'feedback':
        store.feedback(args.id, args.category, args.note); result = {'feedback_saved':args.id}
    elif args.command == 'cycle':
        from rsi_loop import run
        config = json.loads(args.config.read_text(encoding='utf-8'))
        for role in ('development', 'validation', 'holdout'):
            config[role] = str((args.config.parent / config[role]).resolve())
        for field in ('token_budget', 'max_calls', 'proposal_token_budget'):
            if getattr(args, field) is not None:
                config[field] = getattr(args, field)
        load_config(); client = text_client()
        result = run(store, client, config, args.output, args.activate)
        print(json.dumps({'run_id': result['run_id'], 'status': result['status'],
                          'published': result['published'], 'report_directory': str(args.output / result['run_id'])}, ensure_ascii=False, indent=2))
        if result['status'] in {'failed', 'rolled_back_after_error'}:
            raise SystemExit(1)
        return
    elif args.command == 'rsi-status':
        row = store.one("SELECT * FROM evolution_runs WHERE kind='rsi_cycle' ORDER BY created_at DESC,rowid DESC LIMIT 1")
        if not row:
            result = {'active_version': store.active(), 'status': 'never_run', 'notice': '没有完整闭环运行记录，不能宣称已实测'}
        else:
            report = json.loads(row['report'])
            result = {'active_version': store.active(), 'run_id': row['id'], 'database_status': row['status'],
                      'status': report.get('status', row['status']), 'candidate': report.get('candidate'),
                      'stages': report.get('stages', []), 'gates': report.get('gates', {}),
                      'published': report.get('published', False), 'error': report.get('error'),
                      'calls': store.one('SELECT COUNT(*) AS n FROM evolution_calls WHERE run_id=?', (row['id'],))['n']}
    elif args.command in {'propose','evaluate'}:
        load_config(); client = text_client()
        print('开始离线文字模型调用；不使用语音合成。每次调用计入预算，失败不自动重试。', flush=True)
        result = propose(store, client, token_budget=args.token_budget) if args.command == 'propose' else evaluate(
            store,args.version,client,args.max_calls,args.token_budget)
    elif args.command == 'versions':
        result = store.rows('SELECT id,parent,created_at FROM evolution_versions ORDER BY created_at DESC')
    elif args.command == 'diff':
        item = store.version(args.version)
        parent = store.version(item['parent']) if item['parent'] else {'guidance':'','id':'empty'}
        print(''.join(difflib.unified_diff(parent['guidance'].splitlines(True), item['guidance'].splitlines(True),
                                         fromfile=parent['id'], tofile=item['id'])))
        result = {'analysis':json.loads(item['analysis']), 'fixed_base_unchanged':True}
    elif args.command == 'runs':
        result = store.rows('SELECT id,kind,version_id,status,created_at FROM evolution_runs ORDER BY created_at DESC LIMIT 30')
    elif args.command == 'report':
        result = store.one('SELECT * FROM evolution_runs WHERE id=?', (args.run,))
        if result:
            result['metadata'] = json.loads(result['metadata']); result['report'] = json.loads(result['report'])
            result['calls'] = store.rows('SELECT purpose,reserved_tokens,usage,status,duration_ms FROM evolution_calls WHERE run_id=?', (args.run,))
    elif args.command == 'publish': result = publish(store,args.run,args.reviewed)
    elif args.command == 'rollback': result = rollback(store,args.version,args.reason)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__ == '__main__':
    try:
        main()
    except (ValueError, QwenError) as exc:
        print('操作未完成：' + str(exc), file=sys.stderr); sys.exit(1)
