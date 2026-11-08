"""Text provider selection; speech providers remain independent."""
import os
from qwen_client import QwenClient, QwenError

class DeepSeekClient(QwenClient):
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key if api_key is not None else os.getenv('DEEPSEEK_API_KEY', '')
        self.base_url = 'https://api.deepseek.com'
        self.model = model or os.getenv('DEEPSEEK_MODEL', 'deepseek-flash')
        if not self.api_key:
            raise QwenError('DEEPSEEK_KEY_MISSING')
        if self.model not in {'deepseek-flash', 'deepseek-v4-pro'}:
            raise QwenError('INVALID_DEEPSEEK_MODEL')

    def request_body(self, messages, max_tokens):
        return {'model': self.model, 'messages': messages, 'stream': False,
                'thinking': {'type': 'disabled'}, 'max_tokens': max_tokens}

def text_client():
    provider = os.getenv('TEXT_PROVIDER', 'qwen')
    if provider == 'deepseek':
        return DeepSeekClient()
    if provider == 'qwen':
        return QwenClient()
    raise QwenError('INVALID_TEXT_PROVIDER')


async def async_chat(http, client, messages, max_tokens=220):
    """Cancelable provider call for speculative intent work; no automatic retries."""
    import asyncio
    import json
    import aiohttp
    # Preserve injected synchronous models used by offline evaluations.
    if not isinstance(client, QwenClient):
        return await asyncio.to_thread(client.chat, messages, max_tokens)
    if (not isinstance(messages,list) or not 1 <= len(messages) <= 32
            or sum(len(m.get('content','')) for m in messages) > 16000):
        raise QwenError('INVALID_MESSAGES')
    async with asyncio.timeout(15):
        async with http.post(client.base_url+'/chat/completions',
                json=client.request_body(messages,max_tokens),
                headers={'Authorization':'Bearer '+client.api_key},allow_redirects=False) as response:
            if response.status != 200: raise QwenError('CLASSIFIER_HTTP_'+str(response.status))
            raw=bytearray()
            async for chunk in response.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw)>1048576:raise QwenError('RESPONSE_TOO_LARGE')
            data=json.loads(raw);choice=data['choices'][0];text=choice['message']['content']
            if not isinstance(text,str) or not text.strip():raise QwenError('EMPTY_RESPONSE')
            return {'text':text,'model':data.get('model',client.model),'usage':data.get('usage',{}),
                    'finish_reason':choice.get('finish_reason')}

class AsyncClassifier:
    def __init__(self,http,client):self.http=http;self.client=client
    async def achat(self,messages,max_tokens=220):
        return await async_chat(self.http,self.client,messages,max_tokens)
    def chat(self,*args):return self.client.chat(*args)
