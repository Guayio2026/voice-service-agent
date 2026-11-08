"""Service-owned connection pool; no redirects or automatic request retries."""
import httpx
from qwen_client import QwenError

class ModelTransport:
    def __init__(self, client=None):
        self.client = client or httpx.Client(timeout=30, follow_redirects=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4, keepalive_expiry=60))

    def __call__(self, url, body, key):
        try:
            with self.client.stream('POST', url, content=body,
                    headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'}) as response:
                if response.status_code != 200:
                    raise QwenError(f'PROVIDER_HTTP_{response.status_code}: 未自动重试')
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 1_048_576:
                        raise QwenError('RESPONSE_TOO_LARGE')
                return bytes(data)
        except httpx.HTTPError:
            raise QwenError('PROVIDER_NETWORK_ERROR: 结果未知；未自动重试') from None

    def close(self):
        self.client.close()
