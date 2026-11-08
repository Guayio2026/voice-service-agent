"""Real Uvicorn transport exercised by the existing asynchronous contract tests."""
import asyncio
import socket
import aiohttp
import uvicorn
from yarl import URL

class TestServer:
    def __init__(self, app): self.app = app

class TestClient:
    def __init__(self, server): self.app = server.app
    async def start_server(self):
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.base = 'http://127.0.0.1:' + str(self.socket.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='critical', access_log=False, proxy_headers=False, ws_max_size=65536))
        self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
        for _ in range(500):
            if self.server.started: break
            if self.task.done(): await self.task
            await asyncio.sleep(.01)
        else: raise RuntimeError('Uvicorn did not start')
        self.http = aiohttp.ClientSession()
    def make_url(self, path): return URL(self.base + path)
    def get(self, path, **kwargs): return self.http.get(self.base + path, **kwargs)
    def post(self, path, **kwargs): return self.http.post(self.base + path, **kwargs)
    def ws_connect(self, path, **kwargs): return self.http.ws_connect(self.base + path, **kwargs)
    async def close(self):
        await self.http.close()
        self.server.should_exit = True
        await asyncio.wait_for(self.task, 10)
        self.socket.close()
