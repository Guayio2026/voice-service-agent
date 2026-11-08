"""Deterministic pre-delivery rules for this synthetic qualification workflow.

This is a bounded rule set, not a claim of universal semantic moderation.
Never retain rejected text in user-facing reports.
"""
import re
import unicodedata
from evolution_store import redact
from grounding import citations_current

VERSION = 'qualification-output-v2'
IDENTITY_REPLY = '您好，我是智语外呼的AI客服。本次为合成业务演示，请先通过页面模拟确认负责人身份。'
SAFE_REPLY = '补充材料后仍需审核，最终结果以实际审核通知为准。我可以解释当前资料中有依据的补正要求。'
RULES = {
    'approval_promise': r'(?:一定|肯定|保证|确保|百分之百|100%|必定|包过|稳过).{0,12}(?:通过|批准|过审)|(?:包过|稳过|保过)|(?:通过|批准).{0,6}(?:没问题|有保证)',
    'unsupported_approval': r'(?:已经|已|现已).{0,8}(?:审核通过|通过审核|批准|审批通过)|审核(?:结果|状态)(?:是|为|：|:)?通过',
    'false_human_connection': r'(?:已经|已|正在).{0,6}(?:接通|转接到).{0,4}(?:真人|人工)',
    'sensitive_data_request': r'(?:请|麻烦|需要您).{0,12}(?:提供|发送|告诉|报出|上传).{0,8}(?:验证码|密码|银行卡号|身份证号)',
}
# Remove only explicit safe phrases, not whole sentences containing a negation.
SAFE_PHRASES = ('不一定通过', '不一定审核通过', '无法保证通过', '不能保证通过', '不保证通过', '无法保证审核通过', '不能保证审核通过', '不保证审核通过',
                '不等于审核通过', '不代表审核通过', '未通过审核', '没有通过审核',
                '尚未通过审核', '尚未批准', '未批准', '未接通真人', '未接通人工')


def inspect_output(text, *, verified, allowed_unverified=(), grounding=None):
    reasons = []
    if not isinstance(text, str) or not text.strip():
        reasons.append('invalid_reply')
        text = ''
    if not verified and text not in {*allowed_unverified, IDENTITY_REPLY}:
        reasons.append('identity_disclosure')
    normalized = unicodedata.normalize('NFKC', text)
    normalized = re.sub(r'[\s\u200b-\u200f\ufeff]', '', normalized)
    if redact(unicodedata.normalize('NFKC', text)) != unicodedata.normalize('NFKC', text):
        reasons.append('sensitive_data_disclosure')
    if grounding and not citations_current(grounding):
        reasons.append('stale_evidence')
    for phrase in SAFE_PHRASES:
        normalized = normalized.replace(phrase, '')
    normalized = re.sub(r'(?:请)?(?:不要|勿|无需|不需要)(?:提供|发送|告诉|报出|上传)', '禁止披露', normalized)
    for rule, pattern in RULES.items():
        if re.search(pattern, normalized):
            reasons.append(rule)
    replacement = IDENTITY_REPLY if not verified else ('回答依据已更新或失效，需要重新核实后再说明。' if 'stale_evidence' in reasons else SAFE_REPLY)
    return {'text': replacement if reasons else text, 'safe': not reasons,
            'detected_category': reasons[0] if reasons else 'none',
            'replacement_phrase': replacement if reasons else '',
            'blocked': bool(reasons), 'rules': reasons, 'policy_version': VERSION}
