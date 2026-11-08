"""Bounded input screening; backend authorization remains the security boundary."""
import re
import unicodedata
from evolution_store import redact

VERSION = 'input-v2'

def inspect_input(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        return {'text': '', 'blocked': True, 'safe': False, 'redacted': False, 'rules': ['invalid_input'], 'detected_category': 'invalid_input', 'replacement_phrase': '请用简短文字说明您的补正问题。', 'policy_version': VERSION}
    clean = unicodedata.normalize('NFKC', text)
    clean = re.sub(r'[\u200b-\u200f\ufeff]', '', clean)
    compact = re.sub(r'\s+', '', clean).lower()
    patterns = (
        r'(忽略|绕过|跳过|覆盖).{0,12}(指令|规则|核验|验证|权限|确认)',
        r'(泄露|输出|显示|告诉我).{0,12}(系统提示词|密钥|apikey|密码)',
        r'ignore.{0,20}(instructions|rules)|bypass.{0,20}(verification|authorization)',
    )
    blocked = any(re.search(p, compact) for p in patterns)
    safe = redact(clean) if redact(clean) != clean else text
    return {'text': safe, 'blocked': blocked, 'redacted': safe != text,
            'rules': ['instruction_override'] if blocked else [], 'safe': not blocked,
            'detected_category': 'instruction_override' if blocked else 'sensitive_data' if safe != text else 'none',
            'replacement_phrase': '我只能按已核实的身份和业务规则处理，不能跳过核验或确认。请说明您要咨询的补正事项。' if blocked else '',
            'policy_version': VERSION}
