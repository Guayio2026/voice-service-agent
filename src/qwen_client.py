"""Bailian text API adapter; no ASR/TTS or automatic business side effects."""
import argparse
import json
import os
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]

class QwenError(Exception):
    pass

def load_config(path=ROOT / '.env'):
    """Read literal KEY=value only, no execution/interpolation. Environment wins."""
    if not path.exists():
        return
    allowed = {'DASHSCOPE_API_KEY', 'DASHSCOPE_BASE_URL', 'QWEN_MODEL',
               'BREEZE_API_KEY', 'BREEZE_VOICE_ID', 'VOICE_PORT',
               'ASR_VOCABULARY_ID', 'TEXT_PROVIDER', 'DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL', 'TTS_PROVIDER',
               'QWEN_TTS_MODEL', 'QWEN_TTS_VOICE', 'QWEN_TTS_VOICE_NAME'}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key.strip() in allowed:
            os.environ.setdefault(key.strip(), value.strip().strip('\"\''))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class QwenClient:
    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key if api_key is not None else os.environ.get('DASHSCOPE_API_KEY', '')
        self.base_url = (base_url if base_url is not None else os.environ.get('DASHSCOPE_BASE_URL', '')).rstrip('/')
        self.model = model or os.environ.get('QWEN_MODEL', 'qwen-plus')
        if not self.api_key or self.api_key.startswith('YOUR_'):
            raise QwenError('MISSING_API_KEY: 请在本地.env配置百炼密钥')
        parsed = urlsplit(self.base_url)
        host = parsed.hostname or ''
        legacy = {'dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'dashscope-us.aliyuncs.com'}
        workspace = re.fullmatch(r'[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+\.maas\.aliyuncs\.com', host)
        if (parsed.scheme != 'https' or not (host in legacy or workspace)
            or parsed.path != '/compatible-mode/v1' or parsed.query or parsed.fragment
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or '{' in self.base_url):
            raise QwenError('INVALID_BASE_URL: 从百炼控制台复制匹配地域和业务空间的兼容接口Base URL')

    def request_body(self, messages, max_tokens):
        return {'model':self.model,'messages':messages,'stream':False,
                'enable_thinking':False,'max_tokens':max_tokens}

    def chat(self, messages, max_tokens=256):
        if not isinstance(messages, list) or not messages or len(messages) > 32:
            raise QwenError('INVALID_MESSAGES')
        for message in messages:
            if (not isinstance(message, dict) or set(message) != {'role','content'}
                or message['role'] not in {'system','user','assistant'}
                or not isinstance(message['content'], str)):
                raise QwenError('INVALID_MESSAGES')
        if sum(len(m['content']) for m in messages) > 16000 or type(max_tokens) is not int or not 1 <= max_tokens <= 1024:
            raise QwenError('INPUT_LIMIT')
        body = json.dumps(self.request_body(messages, max_tokens), ensure_ascii=False).encode()
        request = urllib.request.Request(self.base_url+'/chat/completions', data=body,
            headers={'Authorization':'Bearer '+self.api_key,'Content-Type':'application/json'})
        try:
            # Never forward credentials to a redirect target. No automatic billed retries.
            transport = getattr(self, 'http_transport', None)
            if transport is not None:
                raw = transport(self.base_url+'/chat/completions', body, self.api_key)
            else:
                with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
                    raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise QwenError('RESPONSE_TOO_LARGE')
            data = json.loads(raw)
            choice = data['choices'][0]
            text = choice['message']['content']
            if not isinstance(text, str) or not text.strip():
                raise QwenError('EMPTY_RESPONSE')
            return {'text':text,'model':data.get('model',self.model),
                    'usage':data.get('usage',{}),'finish_reason':choice.get('finish_reason')}
        except urllib.error.HTTPError as error:
            # Do not surface vendor response bodies: these can contain private input.
            raise QwenError(f'PROVIDER_HTTP_{error.code}: 检查地域、模型权限、余额或限流；未自动重试') from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise QwenError('PROVIDER_NETWORK_ERROR: 结果未知，请检查网络；未自动重试') from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise QwenError('INVALID_PROVIDER_RESPONSE') from None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Send one small request; may incur API fees')
    args = parser.parse_args()
    load_config()
    try:
        client = QwenClient()
        if not args.live:
            print('本地配置检查通过（未连接云端、未验证密钥权限、未产生调用费用）')
            return 0
        result = client.chat([{'role':'user','content':'请仅回复：连接成功。'}],max_tokens=32)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except QwenError as error:
        print(str(error))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
