"""Reusable Breeze realtime transport; a cancelled turn is never replayed."""
import asyncio
import json
import os
import uuid
import time
import re
from urllib.parse import urlencode, quote
import aiohttp
import anyio

class BreezeRealtime:
    def __init__(self):
        self.ws = None
        self.voice = None
        self.lock = asyncio.Lock()
        self.last_used = 0
        self.idle_task = None

    async def stop_idle_watch(self):
        task, self.idle_task = self.idle_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def watch_idle(self):
        ws = self.ws
        async def monitor():
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT and json.loads(msg.data).get('type') == 'pong':
                        continue
                    # No unconsumed audio or turn events may leak into a new turn.
                    break
                if self.ws is ws:
                    await self.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                if self.ws is ws: await self.close()
        self.idle_task = asyncio.create_task(monitor())

    async def close(self):
        await self.stop_idle_watch()
        ws, self.ws = self.ws, None
        self.voice = None
        if ws is not None:
            await ws.close()

    async def connect(self, client, voice):
        await self.stop_idle_watch()
        if self.ws is not None and not self.ws.closed and self.voice == voice and time.monotonic() - self.last_used < 150:
            return
        await self.close()
        query = urlencode({'output_format':'pcm', 'language_code':'zh',
                           'inactivity_timeout_seconds':180})
        self.ws = await client.ws_connect(
            'wss://api.breeze.blue/v1/text-to-speech/' + quote(voice, safe='') + '/stream-input?' + query,
            headers={'xi-api-key':os.environ['BREEZE_API_KEY']})
        try:
            async with asyncio.timeout(15):
                msg = await self.ws.receive()
                if msg.type != aiohttp.WSMsgType.TEXT or json.loads(msg.data).get('type') != 'session.ready':
                    raise RuntimeError('BREEZE_REALTIME_NOT_READY')
            self.voice = voice
            self.last_used = time.monotonic()
        except BaseException:
            await self.close()
            raise

    async def cancel_turn(self, turn_id):
        """Drain old binary frames before permitting reuse; fail closed on ambiguity."""
        ws = self.ws
        if ws is None or ws.closed:
            return False
        try:
            async with asyncio.timeout(0.8):
                await ws.send_json({'type':'turn.cancel'})
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        continue  # Old audio must never reach the next turn.
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        break
                    event = json.loads(msg.data)
                    if event.get('type') == 'turn.cancelled' and event.get('turn_id') == turn_id:
                        self.last_used = time.monotonic()
                        self.watch_idle()
                        return True
                    if event.get('type') in {'error','session.closed'}:
                        break
        except (TimeoutError, aiohttp.ClientError, ValueError):
            pass
        await self.close()
        return False

    async def stream(self, client, voice, text):
        async with self.lock:
            complete = False
            interrupted = False
            turn_id = uuid.uuid4().hex
            started = False
            sender = None
            try:
                async with asyncio.timeout(45):
                    await self.connect(client, voice)
                    await self.ws.send_json({'type':'turn.start','turn_id':turn_id})
                    started = True
                    async def send_text():
                        try:
                            count = 0
                            async def parts():
                                if isinstance(text, str): yield text
                                else:
                                    async for part in text: yield part
                            async for part in parts():
                                count += len(part)
                                if not part or len(part.encode('utf8')) > 2048 or count > 1000:
                                    raise RuntimeError('BREEZE_TEXT_LIMIT')
                                await self.ws.send_json({'type':'text.append','text':part})
                                await self.ws.send_json({'type':'text.flush'})
                            await self.ws.send_json({'type':'turn.end'})
                        except Exception:
                            await self.ws.close()
                            raise
                    if isinstance(text, str):
                        await send_text()
                    else:
                        sender = asyncio.create_task(send_text())
                    async for msg in self.ws:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            yield msg.data
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            event = json.loads(msg.data)
                            if event.get('type') == 'turn.done':
                                if event.get('turn_id', turn_id) != turn_id:
                                    raise RuntimeError('BREEZE_WRONG_TURN')
                                if sender is not None:
                                    await sender
                                complete = True
                                self.last_used = time.monotonic()
                                self.watch_idle()
                                return
                            if event.get('type') in {'error','turn.cancelled','session.closed'}:
                                detail = event.get('error') or {}
                                code = event.get('code') or (detail.get('code') if isinstance(detail,dict) else '')
                                code = re.sub(r'[^A-Za-z0-9_.-]', '', str(code))[:80]
                                raise RuntimeError('BREEZE_REALTIME_TURN_FAILED:' + event['type'] + ':' + code)
                        else:
                            raise RuntimeError('BREEZE_REALTIME_DISCONNECTED')
                    raise RuntimeError('BREEZE_REALTIME_INCOMPLETE')
            except (asyncio.CancelledError, GeneratorExit):
                interrupted = True
                raise
            finally:
                if sender is not None:
                    sender.cancel()
                    with anyio.CancelScope(shield=True):
                        await asyncio.gather(sender, return_exceptions=True)
                if not complete:
                    # Starlette cancels the response scope on disconnect. Shield only
                    # this bounded cleanup, keeping the transport lock until it ends.
                    with anyio.CancelScope(shield=True):
                        if interrupted and started:
                            await self.cancel_turn(turn_id)
                        else:
                            await self.close()
