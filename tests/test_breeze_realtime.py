import asyncio,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import aiohttp
from breeze_realtime import BreezeRealtime

def msg(kind,data): return SimpleNamespace(type=kind,data=data)
class Socket:
    def __init__(self): self.closed=False; self.sent=[]; self.events=[]
    async def receive(self): return msg(aiohttp.WSMsgType.TEXT,json.dumps({'type':'session.ready'}))
    async def send_json(self,data):
        self.sent.append(data)
        if data['type']=='turn.start': self.turn_id=data['turn_id']
        if data['type']=='turn.cancel':
            self.events.extend([msg(aiohttp.WSMsgType.BINARY,b'old'),msg(aiohttp.WSMsgType.TEXT,json.dumps({'type':'turn.cancelled','turn_id':self.turn_id}))])
        if data['type']=='turn.end':
            self.events.extend([msg(aiohttp.WSMsgType.BINARY,b'\0\0'),msg(aiohttp.WSMsgType.TEXT,'{"type":"turn.done"}')])
    def __aiter__(self): return self
    async def __anext__(self):
        while not self.events:
            if self.closed: raise StopAsyncIteration
            await asyncio.sleep(.001)
        return self.events.pop(0)
    async def close(self): self.closed=True
class Client:
    def __init__(self): self.sockets=[]
    async def ws_connect(self,*a,**kw):
        ws=Socket(); self.sockets.append(ws); return ws
class Tests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_completed_turn_only(self):
        client=Client(); rt=BreezeRealtime()
        with patch.dict('os.environ',{'BREEZE_API_KEY':'test'}):
            for _ in range(2):
                self.assertEqual([v async for v in rt.stream(client,'voice','hello')],[b'\0\0'])
        self.assertEqual(len(client.sockets),1)
        self.assertEqual([e['type'] for e in client.sockets[0].sent].count('text.flush'),2)
        await rt.close()
    async def test_cancel_discards_old_audio_and_reuses_connection(self):
        client=Client(); rt=BreezeRealtime()
        with patch.dict('os.environ',{'BREEZE_API_KEY':'test'}):
            stream=rt.stream(client,'voice','hello');await anext(stream);await stream.aclose()
            self.assertFalse(client.sockets[0].closed);self.assertFalse(rt.lock.locked())
            self.assertEqual([v async for v in rt.stream(client,'voice','next')],[b'\0\0'])
        self.assertEqual(len(client.sockets),1);await rt.close()
    async def test_wrong_cancel_ack_closes_socket(self):
        rt=BreezeRealtime(); ws=Socket();rt.ws=ws
        ws.turn_id='old';ws.events=[]
        self.assertFalse(await rt.cancel_turn('different'))
        self.assertTrue(ws.closed);self.assertIsNone(rt.ws)

    async def test_missing_ack_times_out_and_closes(self):
        class Silent(Socket):
            async def __anext__(self):
                await asyncio.Event().wait()
        rt=BreezeRealtime();ws=Silent();ws.turn_id='old';rt.ws=ws
        self.assertFalse(await asyncio.wait_for(rt.cancel_turn('old'),1.5))
        self.assertTrue(ws.closed)

    async def test_duplex_returns_audio_before_text_input_ends(self):
        class Duplex(Socket):
            def __init__(self): super().__init__();self.queue=asyncio.Queue()
            async def send_json(self,data):
                self.sent.append(data)
                if data['type']=='turn.start':self.turn_id=data['turn_id']
                if data['type']=='text.flush':await self.queue.put(msg(aiohttp.WSMsgType.BINARY,b'\0\0'))
                if data['type']=='turn.end':await self.queue.put(msg(aiohttp.WSMsgType.TEXT,'{"type":"turn.done"}'))
            async def __anext__(self):return await self.queue.get()
        class DuplexClient(Client):
            async def ws_connect(self,*a,**kw):
                ws=Duplex();self.sockets.append(ws);return ws
        gate=asyncio.Event()
        async def text():
            yield 'first';await gate.wait();yield 'last'
        rt=BreezeRealtime();client=DuplexClient()
        with patch.dict('os.environ',{'BREEZE_API_KEY':'test'}):
            audio=rt.stream(client,'voice',text())
            self.assertEqual(await asyncio.wait_for(anext(audio),1),b'\0\0')
            self.assertNotIn('turn.end',[e['type'] for e in client.sockets[0].sent])
            gate.set();self.assertEqual([x async for x in audio],[b'\0\0'])
        await rt.close()

    async def test_idle_peer_close_is_detected_before_next_turn(self):
        rt=BreezeRealtime();client=Client()
        with patch.dict('os.environ',{'BREEZE_API_KEY':'test'}):
            [x async for x in rt.stream(client,'voice','hello')]
            client.sockets[0].events.append(msg(aiohttp.WSMsgType.CLOSE,None))
            await asyncio.sleep(.02)
            self.assertIsNone(rt.ws)
            [x async for x in rt.stream(client,'voice','next')]
            self.assertEqual(len(client.sockets),2)
        await rt.close()

    async def test_voice_change_reconnects(self):
        client=Client();rt=BreezeRealtime()
        with patch.dict('os.environ',{'BREEZE_API_KEY':'test'}):
            for voice in ['one','two']:
                [v async for v in rt.stream(client,voice,'hello')]
        self.assertTrue(client.sockets[0].closed);self.assertEqual(len(client.sockets),2)
        await rt.close()
if __name__=='__main__':unittest.main()
