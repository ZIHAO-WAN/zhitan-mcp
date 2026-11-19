import asyncio
import hashlib
import hmac
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from boss_zp.bridge import BrowserBridge
from boss_zp.storage import BossError


def test_bridge_authenticates_and_restricts_commands():
    async def scenario():
        bridge = BrowserBridge('private-token', port=0)
        await bridge.start()
        address = f'ws://127.0.0.1:{bridge.port}/'
        try:
            async with connect(address, origin='https://example.test') as wrong:
                with pytest.raises(ConnectionClosed):
                    await wrong.recv()
            async with connect(address, origin='chrome-extension://' + 'a' * 32) as wrong:
                challenge = json.loads(await wrong.recv())
                proof = hmac.new(b'wrong', ('client:' + challenge['nonce']).encode(), hashlib.sha256).hexdigest()
                await wrong.send(json.dumps({'type': 'authenticate', 'proof': proof}))
                with pytest.raises(ConnectionClosed):
                    await wrong.recv()
            async with connect(address, origin='chrome-extension://' + 'a' * 32) as client:
                challenge = json.loads(await client.recv())
                assert challenge['proof'] == hmac.new(b'private-token', ('server:' + challenge['nonce']).encode(), hashlib.sha256).hexdigest()
                proof = hmac.new(b'private-token', ('client:' + challenge['nonce']).encode(), hashlib.sha256).hexdigest()
                await client.send(json.dumps({'type': 'authenticate', 'proof': proof}))
                assert json.loads(await client.recv())['type'] == 'connected'
                task = asyncio.create_task(bridge.request('status'))
                command = json.loads(await client.recv())
                assert command['command'] == 'status'
                await client.send(json.dumps({'id': command['id'], 'ok': True, 'data': {'ready': True}}))
                assert await task == {'ready': True}
                task = asyncio.create_task(bridge.request('search_jobs'))
                command = json.loads(await client.recv())
                await client.send(json.dumps({'id': command['id'], 'ok': True, 'data': {'jobs': [{'job_id': 'a'}]}}))
                assert (await task)['jobs'][0]['job_id'] == 'a'
                with pytest.raises(BossError):
                    await bridge.request('send_greeting')
        finally:
            await bridge.close()
    asyncio.run(scenario())
