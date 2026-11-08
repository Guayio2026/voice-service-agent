"""Render identifiers for Chinese TTS without changing stored business values."""
import re
DIGITS = dict(zip('0123456789', '零一二三四五六七八九'))
def speech_text(text):
    def identifier(match):
        return '，'.join(DIGITS.get(c, c) for c in match.group() if c != '-')
    return re.sub(r'(?<![A-Za-z0-9])(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*[0-9])[A-Z][A-Z0-9-]*(?![A-Za-z0-9])', identifier, text)
