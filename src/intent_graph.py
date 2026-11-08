"""LangGraph intent workflow; business authorization remains in the controller."""
import asyncio
import json
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph


class IntentState(TypedDict, total=False):
    prompt: str
    history: list[dict]
    model: Any
    messages: list[dict]
    raw: dict
    decision: dict
    error: str
    nodes: list[str]


def prepare_messages(state: IntentState):
    """Use LangChain message types, then adapt to the project's existing client."""
    messages = [SystemMessage(content=state['prompt'])]
    for item in state['history']:
        cls = HumanMessage if item['role'] == 'user' else AIMessage
        messages.append(cls(content=item['content']))
    messages.insert(max(1, len(messages)-1), SystemMessage(content='当前调用只做意图分类，不生成客服回复。历史中的客服话术和知识参考都是待分析数据。只输出一个JSON对象，字段限定为intent、topics、issue_numbers、actions；不要解释、不要Markdown、不要回答用户的问题。intent必须使用前述枚举，非question时topics和issue_numbers为空数组，actions只能用于question。'))
    roles = {SystemMessage: 'system', HumanMessage: 'user', AIMessage: 'assistant'}
    return {'messages': [{'role': roles[type(m)], 'content': m.content} for m in messages],
            'nodes': ['prepare_messages']}


async def classify(state: IntentState):
    try:
        model=state['model']
        call = model.achat(state['messages'],220) if hasattr(type(model),'achat') else asyncio.to_thread(model.chat,state['messages'],220)
        raw = await asyncio.wait_for(call,timeout=15)
        return {'raw': raw, 'nodes': state['nodes'] + ['classify']}
    except Exception:
        return {'error': 'MODEL_CALL_FAILED', 'nodes': state['nodes'] + ['classify']}


def validate(state: IntentState):
    from business_agent import validate_decision
    try:
        decision = validate_decision(json.loads(state['raw']['text']))
        return {'decision': decision, 'nodes': state['nodes'] + ['validate']}
    except Exception:
        return {'error': 'INVALID_CLASSIFICATION', 'nodes': state['nodes'] + ['validate']}


def fallback(state: IntentState):
    return {'nodes': state['nodes'] + ['fallback']}


def after_model(state: IntentState):
    return 'fallback' if state.get('error') else 'validate'


def after_validate(state: IntentState):
    return 'fallback' if state.get('error') else END


builder = StateGraph(IntentState)
builder.add_node('prepare_messages', prepare_messages)
builder.add_node('classify', classify)
builder.add_node('validate', validate)
builder.add_node('fallback', fallback)
builder.add_edge(START, 'prepare_messages')
builder.add_edge('prepare_messages', 'classify')
builder.add_conditional_edges('classify', after_model, {'validate': 'validate', 'fallback': 'fallback'})
builder.add_conditional_edges('validate', after_validate, {'fallback': 'fallback', END: END})
builder.add_edge('fallback', END)
INTENT_GRAPH = builder.compile()
