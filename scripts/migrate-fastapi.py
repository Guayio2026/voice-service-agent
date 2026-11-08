"""One-time source migration. Original retained under runtime."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
p = root/'src/voice_server.py'
s = p.read_text(encoding='utf-8')
assert 'from aiohttp import web' in s
(root/'runtime/voice_server.before-fastapi.py').write_text(s, encoding='utf-8')
s = s.replace('from contextlib import closing', 'from contextlib import closing, asynccontextmanager')
s = s.replace('from aiohttp import web', '''from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, FileResponse, RedirectResponse, StreamingResponse
from starlette.websockets import WebSocketState
import uvicorn''')
s = s.replace('web.json_response', 'JSONResponse').replace('status=status', 'status_code=status')
s = s.replace('@web.middleware\n', '')
s = s.replace('request.host', "request.headers.get('host', '')").replace('request.path', 'request.url.path')
# Avoid nested quote issues in f strings on older Python interpreters.
s = s.replace("expected = f'http://{request.headers.get('host', '')}'", "expected = 'http://' + request.headers.get('host', '')")
s = s.replace("request.content_type == 'application/json'", "request.headers.get('content-type', '').split(';')[0].strip() == 'application/json'")
s = s.replace('    p = await request.json()\n', '''    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        require(len(raw) <= 32768, 'REQUEST_TOO_LARGE', 413)
    p = json.loads(raw)
''')
s = s.replace("request.app['service']", 'request.app.state.service').replace('request.query.get', 'request.query_params.get').replace('request.match_info', 'request.path_params')
import re
s = re.sub(r'async def (\w+)\(request\):', r'async def \1(request: Request):', s)
s = s.replace("raise web.HTTPFound('/workbench.html')", "return RedirectResponse('/workbench.html', status_code=302)")
s = s.replace('web.FileResponse', 'FileResponse').replace('web.Response(text=', 'Response(content=').replace("content_type='text/javascript'", "media_type='text/javascript'")
start = s.index('    async with s.tts_lock:', s.index('async def speech'))
end = s.index('\n\nasync def asr', start)
s = s[:start] + '''    await s.tts_lock.acquire()
    turn['tts_used'] = True
    upstream = None
    try:
        upstream = await s.http.post(BREEZE + '/text-to-speech/' + vid + '/stream',
            params={'output_format': 'pcm'}, headers={'xi-api-key': key},
            json={'text': turn['result']['text'], 'language_code': 'zh',
                  'instructions': '语气自然、清晰、亲切，像专业客服一样交流。'}, allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=90, sock_read=25))
        require(upstream.status == 200, f'BREEZE_HTTP_{upstream.status}', 502)
    except BaseException:
        if upstream is not None:
            upstream.close()
        s.tts_lock.release()
        raise

    async def chunks():
        total = 0
        try:
            async for chunk in upstream.content.iter_chunked(8192):
                total += len(chunk)
                require(total <= 8_640_000, 'AUDIO_LIMIT')
                yield chunk
        finally:
            upstream.close()
            s.tts_lock.release()
    return StreamingResponse(chunks(), media_type='application/octet-stream',
        headers={'X-Audio-Sample-Rate': '24000', 'X-Audio-Format': 'pcm_s16le', 'Cache-Control': 'no-store'})
''' + s[end:]
s = s.replace('async def asr(request: Request):', 'async def asr(request: WebSocket):')
s = s.replace('    ws = web.WebSocketResponse(max_msg_size=65536, heartbeat=15)\n    await ws.prepare(request)', '''    ws = request
    await ws.accept()''')
s = s.replace('hello = await ws.receive_json(timeout=10)', 'hello = await asyncio.wait_for(ws.receive_json(), 10)')
s = s.replace('                    async for msg in ws:\n                        if msg.type == aiohttp.WSMsgType.BINARY:', '''                    while True:
                        msg = await ws.receive()
                        if msg['type'] == 'websocket.disconnect':
                            return 'disconnected'
                        if msg.get('bytes') is not None:''')
# Only transform client messages; upstream remains an aiohttp client.
a = s.index('                async def send_audio():'); b = s.index('                async def receive_text():', a)
part = s[a:b].replace('msg.data', "msg['bytes']")
part = part.replace('elif msg.type == aiohttp.WSMsgType.TEXT:', "elif msg.get('text') is not None:")
part = part.replace("json.loads(msg['bytes'])", "json.loads(msg['text'])")
part = part.replace("total += len(msg['bytes'])", "require(len(msg['bytes']) <= 65536, 'AUDIO_LIMIT')\n                            total += len(msg['bytes'])")
s = s[:a] + part + s[b:]
s = s.replace('return ws', 'return').replace('if not ws.closed:', 'if ws.client_state == WebSocketState.CONNECTED:')
s = s.replace('    except (Error, QwenError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as e:', '    except WebSocketDisconnect:\n        capture["status"] = "interrupted_or_disconnected"\n    except (Error, QwenError, aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as e:')
s = s.replace('            await ws.close()', '            if ws.client_state == WebSocketState.CONNECTED:\n                await ws.close()')
s = s[:s.index('def create_app(db=None):')] + '''def create_app(db=None):
    @asynccontextmanager
    async def lifecycle(app):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                headers={'User-Agent': 'VoiceServiceAgent/0.1'}, trust_env=True) as http:
            app.state.service.http = http
            yield

    app = FastAPI(title='智语外呼 API', lifespan=lifecycle)
    app.state.service = VoiceService(db or ROOT / 'runtime/mock.sqlite3')
    app.middleware('http')(local_only)
    for path, endpoint, methods in [
        ('/api/status', status, ['GET']), ('/api/voices', voices, ['GET']),
        ('/api/session', session_create, ['POST']), ('/api/identity', identity_update, ['POST']),
        ('/api/interrupt', agent_interrupt, ['POST']), ('/api/report', agent_report, ['GET']),
        ('/api/agent/chat', chat, ['POST']), ('/api/chat', chat, ['POST']),
        ('/api/speech', speech, ['POST']), ('/mock-config.js', mock_config, ['GET']),
        ('/business/{operation}', business, ['GET', 'POST'])]:
        app.add_api_route(path, endpoint, methods=methods)

    @app.websocket('/api/asr')
    async def websocket_endpoint(ws: WebSocket):
        host = ws.headers.get('host', '')
        if host not in {f'127.0.0.1:{ws.url.port}', f'localhost:{ws.url.port}'} or ws.headers.get('origin') != 'http://' + host:
            await ws.close(code=1008)
            return
        try:
            await asr(ws)
        except (Error, QwenError) as exc:
            if ws.application_state == WebSocketState.CONNECTING:
                await ws.close(code=1008)
            elif ws.application_state == WebSocketState.CONNECTED:
                await ws.send_json({'type': 'error', 'code': getattr(exc, 'code', 'ASR_CONNECTION_FAILED')})
                await ws.close()

    app.add_api_route('/{path:path}', static, methods=['GET'], include_in_schema=False)
    return app


if __name__ == '__main__':
    load_config()
    uvicorn.run(create_app(), host='127.0.0.1', port=int(os.getenv('VOICE_PORT', '8766')),
                access_log=False, proxy_headers=False, ws_max_size=65536)
'''
p.write_text(s, encoding='utf-8')
