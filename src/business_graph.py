"""Per-turn business graph. Persistent authority stays with the trusted backend."""
from typing import Any, TypedDict
from langgraph.graph import END, START, StateGraph


class BusinessState(TypedDict, total=False):
    agent: Any
    model: Any
    text: str
    token: int
    turn: int
    pending: Any
    decision: dict
    application: dict
    reply: dict
    next_node: str


def convert(state, result):
    if '_next' not in result:
        return {'reply': result, 'next_node': 'end'}
    return {'next_node': result['_next'], **{k:v for k,v in result.items() if k != '_next'}}


def mark(state, name):
    state['agent'].business_graph_nodes.append(name)


async def identity_gate(state):
    mark(state, 'identity_gate')
    from server import require
    require(state['token'] == state['agent'].generation, 'TURN_CANCELLED', 409)
    result = state['agent'].graph_gate(state['text'])
    if result.get('_identity_understanding'):
        result = await state['agent'].conversational_identity(state['text'], state['model'], state['token'])
    return convert(state, result)


async def confirmation(state):
    mark(state, 'confirmation')
    state['agent'].check_graph_authority(state['token'])
    return convert(state, state['agent'].graph_confirmation(state['text'], state['turn']))


async def intent(state):
    mark(state, 'intent')
    state['agent'].check_graph_authority(state['token'])
    result = await state['agent'].graph_classify(state['text'], state['model'], state['token'])
    state['agent'].check_graph_authority(state['token'])
    return convert(state, result)


async def route(state):
    mark(state, 'business_route')
    state['agent'].check_graph_authority(state['token'])
    return convert(state, state['agent'].graph_route(state['decision']))


async def social(state):
    mark(state, 'social')
    state['agent'].check_graph_authority(state['token'])
    return convert(state, state['agent'].graph_social(state['decision'], state.get('pending')))


async def disposition(state):
    mark(state, 'disposition')
    state['agent'].check_graph_authority(state['token'])
    return convert(state, state['agent'].graph_disposition(state['decision'], state['turn']))


async def off_topic(state):
    mark(state, 'off_topic')
    state['agent'].check_graph_authority(state['token'])
    return convert(state, state['agent'].graph_off_topic())


async def clarify(state):
    mark(state, 'clarify')
    state['agent'].check_graph_authority(state['token'])
    return convert(state, state['agent'].graph_clarify())


async def correction(state):
    mark(state, 'correction')
    state['agent'].check_graph_authority(state['token'])
    return convert(state, state['agent'].graph_correction())


async def lookup(state):
    mark(state, 'query_application')
    state['agent'].check_graph_authority(state['token'])
    return convert(state, state['agent'].graph_lookup())


def answer_route(state):
    if state['next_node'] == 'end':
        return 'end'
    return 'question' if state['decision']['intent'] == 'question' else 'continue'


async def answer_question(state):
    mark(state, 'answer_question')
    state['agent'].check_graph_authority(state['token'])
    return convert(state, state['agent'].graph_business_question(state['decision'], state['application']))


async def prepare_confirmation(state):
    mark(state, 'prepare_confirmation')
    state['agent'].check_graph_authority(state['token'])
    return convert(state, state['agent'].graph_prepare_confirmation(state['text'], state['application'], state['turn']))


def next_node(state):
    return state['next_node']


builder = StateGraph(BusinessState)
builder.add_node('identity_gate', identity_gate)
builder.add_node('confirmation', confirmation)
builder.add_node('intent', intent)
builder.add_node('business_route', route)
builder.add_node('query_application', lookup)
builder.add_node('social', social)
builder.add_node('disposition', disposition)
builder.add_node('off_topic', off_topic)
builder.add_node('clarify', clarify)
builder.add_node('correction', correction)
builder.add_node('answer_question', answer_question)
builder.add_node('prepare_confirmation', prepare_confirmation)
builder.add_edge(START, 'identity_gate')
builder.add_conditional_edges('identity_gate', next_node, {'confirmation':'confirmation', 'end':END})
builder.add_conditional_edges('confirmation', next_node, {'classify':'intent', 'end':END})
builder.add_conditional_edges('intent', next_node, {'route':'business_route', 'end':END})
builder.add_conditional_edges('business_route', next_node, {'lookup':'query_application', 'social':'social',
    'disposition':'disposition', 'off_topic':'off_topic', 'clarify':'clarify', 'correction':'correction'})
builder.add_conditional_edges('query_application', answer_route,
    {'question':'answer_question', 'continue':'prepare_confirmation', 'end':END})
for node in ['social', 'disposition', 'off_topic', 'clarify', 'correction', 'answer_question', 'prepare_confirmation']:
    builder.add_edge(node, END)
BUSINESS_GRAPH = builder.compile()
