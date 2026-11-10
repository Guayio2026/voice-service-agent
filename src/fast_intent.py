"""Exact whole-utterance classification, never business authorization."""
import re

def fast_decision(text):
    phrase = re.sub(r'[\s，。！？、,.!?]', '', text)
    for intent, phrases in {
        'decline': {'不需要了', '不要再联系我', '我拒绝沟通', '结束对话', '结束通话', '挂断',
                    '先这样吧', '就这样吧', '不聊了', '先到这里'},
        'defer': {'现在不方便', '稍后联系', '晚点联系'},
        'handoff': {'转人工', '我要转人工', '我需要人工协助'},
    }.items():
        if phrase in phrases:
            return {'intent': intent, 'topics': [], 'issue_numbers': []}
    for topic, phrases in {
        'materials': {'需要补什么材料', '我需要补充什么材料', '要补哪些材料', '怎么补材料',
                      # "补正事项" is the same request without the word 材料; the
                      # classifier otherwise filed it under other and the answer
                      # degraded to the "资料不足" fallback.
                      '是什么补正事项', '补正事项是什么', '补正事项有哪些', '补正事项是哪些',
                      '什么补正事项', '补正事项是啥', '要补什么', '需要补什么', '缺什么', '缺少什么',
                      # "补正具体指什么" asks for the same content without naming
                      # 事项/材料; unmatched it fell to the model, which returned
                      # other and the answer degraded to "资料不足".
                      '补正具体指什么', '补正指什么', '补正是什么意思', '补正具体是什么意思',
                      '补正是啥', '补正具体是啥', '补正都包括什么', '补正包括什么'},
        'reason': {'为什么要补材料', '补正原因是什么'},
        'channel': {'补充材料在哪里提交', '在哪里提交材料'},
        'deadline': {'补正截止时间是什么', '什么时候截止'},
    }.items():
        if phrase in phrases:
            return {'intent': 'question', 'topics': [topic], 'issue_numbers': []}
    return readonly_question(phrase) or material_question(phrase) or correction_subject(phrase)


def correction_subject(phrase):
    """'补正 的问题/事项/内容/要求' + '是什么/指什么/包括什么' asks what to fix.

    A whole-utterance grammar instead of another phrase list: the same request
    keeps arriving with a new noun, and each new wording used to fall through to
    the model, get filed under unclear/other and hit a "please rephrase" reply.
    Negations, third-party subjects and action requests do not match the grammar.
    """
    intro = r'(?:(?:我)?(?:想(?:知道|了解一下|问一下|了解)|问一下|请问|咨询一下|请说明))?'
    subject = r'(?:(?:这次|本次|当前)(?:的)?)?(?:资质)?补正(?:的)?'
    noun = r'(?:问题|事项|内容|要求|材料|范围|情况|东西|地方)?'
    ask = (r'(?:(?:具体|到底|究竟)?(?:是(?:什么|啥|哪些|什么问题|什么内容)|'
           r'指(?:的)?是?(?:什么|啥)|包括(?:什么|哪些)|包含(?:什么|哪些)|'
           r'有(?:哪些|什么)|是什么意思))')
    if re.fullmatch(intro + subject + noun + ask, phrase):
        return {'intent': 'question', 'topics': ['materials'], 'issue_numbers': []}
    # "我现在需要补正什么" names the action instead of the subject.
    need = r'(?:我)?(?:(?:现在|目前|这次|本次))?(?:到底|具体)?(?:需要|要|应该|得)(?:补正|补充|补)(?:些)?(?:什么|哪些)'
    if re.fullmatch(intro + need, phrase):
        return {'intent': 'question', 'topics': ['materials'], 'issue_numbers': []}
    return None


def material_question(phrase):
    """Parse complete, compositional material questions without keyword guessing.

    The first clause must explicitly identify materials. Later clauses may
    inherit that subject. Every character must belong to the grammar; mixed
    intents, negations and corrections remain model/controller work.
    """
    intro = r'(?:(?:我)?想(?:知道|了解)|请问|请解释一下|请说明)?'
    scope = r'(?:(?:这次|本次|当前)(?:申请|补正)(?:里|中|的)?)?'
    subject = scope + r'(?:(?:每一项|全部|所有)|第(?P<number>[1-9]|10|[一二三四五六七八九十])项)?材料'
    questions = {
        'reason': r'(?:为什么(?:需要|要)?补[正证]|为什么(?:需要|要)补充|补正原因是什么)',
        'action': r'(?:(?:我)?(?:应该|需要)?(?:怎样|怎么|如何)(?:处理|补正|补充|修改))',
        'basis': r'(?:补正依据是什么|依据是什么)',
    }
    clauses = re.split(r'以及|还有|并且|和', phrase)
    if not 1 <= len(clauses) <= 3:
        return None
    topics, numbers = [], []
    for index, clause in enumerate(clauses):
        found = False
        for topic, question in questions.items():
            pattern = intro + (subject if index == 0 else '') + question
            match = re.fullmatch(pattern, clause)
            if not match:
                continue
            if index == 0 and match['number']:
                value = match['number']
                numbers = [int(value) if value.isascii() else '一二三四五六七八九十'.index(value)+1]
            topics.append(topic)
            found = True
            break
        if not found:
            return None
    return {'intent':'question', 'topics':list(dict.fromkeys(topics)), 'issue_numbers':numbers}


def readonly_question(phrase):
    """Whole-utterance grammar for explicit information requests, never actions.

    Unknown words, conflicting requests and unscoped follow-ups go to the model.
    A retracted topic is accepted only with an explicit new question introducer.
    """
    nouns = {
        'deadline': r'(?:补[正证]截止时间|截止时间|补[正证]期限|截止日期)',
        'channel': r'(?:提交入口|提交渠道|办理渠道|办理入口)',
        'fees': r'(?:补正费用|办理费用|收费信息)',
        'materials': r'(?:补正材料|所需材料)',
    }
    noun = '(?:' + '|'.join(nouns.values()) + ')'
    # Retraction is grammatical, not deletion of arbitrary negations.
    retraction = rf'(?:我?(?:先|暂时)?不问(?:材料|{noun})了?)?'
    intro = r'(?:我想知道|我想了解|想知道|想了解|请问|咨询一下)'
    tail = r'(?:是什么|有哪些|是多少|能否确定|是否明确|是否已确定|吗)?'
    match = re.fullmatch(rf'{retraction}{intro}(?P<topics>{noun}(?:(?:和|以及|还有|及){noun})*){tail}', phrase)
    if not match:
        return None
    parts = re.split(r'以及|还有|和|及', match['topics'])
    topics = list(dict.fromkeys(next(k for k,v in nouns.items() if re.fullmatch(v,p)) for p in parts))
    return {'intent': 'question', 'topics': topics, 'issue_numbers': []}
