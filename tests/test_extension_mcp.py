import asyncio
import hashlib
import hmac
import json

from fastmcp import Client
from websockets.asyncio.client import connect

from boss_zp.server import create_server
from boss_zp.browser import ExtensionSession
from boss_zp.storage import GreetingLedger


async def authenticate(browser, token):
    challenge = json.loads(await browser.recv())
    expected = hmac.new(token.encode(), ('server:' + challenge['nonce']).encode(), hashlib.sha256).hexdigest()
    assert challenge['proof'] == expected
    proof = hmac.new(token.encode(), ('client:' + challenge['nonce']).encode(), hashlib.sha256).hexdigest()
    await browser.send(json.dumps({'type': 'authenticate', 'proof': proof}))
    await browser.recv()


def test_extension_mcp_rejects_wrong_search_and_never_exposes_send(tmp_path):
    async def scenario():
        session = ExtensionSession(port=0, launch_window=False, token='a' * 43)
        server = create_server(session, ledger=GreetingLedger(tmp_path))
        async with Client(server) as client:
            assert {t.name for t in await client.list_tools()} == {'get_session_status', 'search_jobs', 'get_job_detail', 'prepare_greetings', 'send_greetings'}
            invalid = await client.call_tool('search_jobs', {'keyword': '   ', 'city': '北京'}, raise_on_error=False)
            assert invalid.is_error
            disabled = await client.call_tool('send_greetings', {'batch_id': 'anything'})
            assert disabled.structured_content['error']['code'] == 'SENDING_UNAVAILABLE'
            assert (await client.call_tool('get_session_status')).structured_content['data']['state'] == 'pairing'
            async with connect(f'ws://127.0.0.1:{session.bridge.port}/', origin='chrome-extension://' + 'a' * 32) as browser:
                await authenticate(browser, session.token)
                async def serve():
                    async for raw in browser:
                        request = json.loads(raw)
                        data = {'jobs': [{'job_id': 'a', 'url': 'https://www.zhipin.com/job_detail/a.html', 'title': '岗位', 'company': None}],
                                'search': {'keyword': 'Python', 'city': '北京', 'stop_reason': 'site_end', 'filters': [],
                                           'filter_query': {'degree': ['203']}, }}
                        if request['command'] == 'read_detail':
                            data = {'job_id': 'a', 'url': 'https://www.zhipin.com/job_detail/a.html', 'title': '岗位',
                                    'company': '甲公司', 'description': '职责', 'contact_status': 'not_contacted'}
                        await browser.send(json.dumps({'id': request['id'], 'ok': True, 'data': data}, ensure_ascii=False))
                worker = asyncio.create_task(serve())
                accepted = await client.call_tool('search_jobs', {'keyword': 'Python', 'city': '北京'})
                assert accepted.structured_content['data']['jobs'][0]['job_id'] == 'a'
                rejected = await client.call_tool('search_jobs', {'keyword': 'Java', 'city': '北京'})
                assert rejected.structured_content['error']['code'] == 'FILTER_NOT_APPLIED'
                invalid_limit = await client.call_tool('search_jobs', {'keyword': 'Python', 'city': '北京', 'limit': 151}, raise_on_error=False)
                assert invalid_limit.is_error
                detail = await client.call_tool('get_job_detail', {'job_id': 'a'})
                assert detail.structured_content['data']['job']['company'] == '甲公司'
                prepared = await client.call_tool('prepare_greetings', {'job_ids': ['a']})
                assert prepared.structured_content['data']['jobs'][0]['company'] == '甲公司'
                assert prepared.structured_content['data']['batch_id'] is None
                assert prepared.structured_content['data']['missing_count'] == 29
                mismatch = await client.call_tool('get_job_detail', {'job_id': 'b'})
                assert mismatch.structured_content['error']['code'] == 'JOB_MISMATCH'
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(scenario())


def test_reviewed_batch_sends_at_most_once_through_mcp(tmp_path):
    async def scenario():
        session = ExtensionSession(port=0, launch_window=False, token='a' * 43, allow_send=True)
        server = create_server(session, ledger=GreetingLedger(tmp_path), allow_send=True)
        sends = []
        extension_version = '1.3.0'
        async with Client(server) as client:
            await client.call_tool('get_session_status')
            async with connect(f'ws://127.0.0.1:{session.bridge.port}/', origin='chrome-extension://' + 'a' * 32) as browser:
                await authenticate(browser, session.token)
                async def serve():
                    async for raw in browser:
                        request = json.loads(raw)
                        if request['command'] == 'send_once':
                            sends.append(request['payload']['job_id'])
                            data = {'outcome': 'sent'}
                        elif request['command'] == 'status':
                            data = {'state': 'authenticated', 'extension_version': extension_version,
                                    'bridge_protocol': 1, 'send_contract': 4 if extension_version == '1.3.0' else None}
                        else:
                            job_id = request['payload']['job_id']
                            data = {'job_id': job_id, 'url': f'https://www.zhipin.com/job_detail/{job_id}.html', 'title': '岗位',
                                    'company': '甲公司', 'description': '职责', 'contact_status': 'not_contacted'}
                        await browser.send(json.dumps({'id': request['id'], 'ok': True, 'data': data}, ensure_ascii=False))
                worker = asyncio.create_task(serve())
                status = (await client.call_tool('get_session_status')).structured_content
                assert status['data']['send_contract'] == 4
                ids = ['a', *[f'job{i}' for i in range(29)]]
                for job_id in ids:
                    await client.call_tool('get_job_detail', {'job_id': job_id})
                prepared = (await client.call_tool('prepare_greetings', {'job_ids': ids})).structured_content
                batch_id = prepared['data']['batch_id']
                assert 'selection_checks' not in prepared['data']
                assert sends == []
                extension_version = '0.3.0'
                outdated = (await client.call_tool('send_greetings', {'batch_id': batch_id})).structured_content
                assert outdated['error']['code'] == 'EXTENSION_OUTDATED'
                assert GreetingLedger(tmp_path).get('a') is None
                extension_version = '1.3.0'
                sent = (await client.call_tool('send_greetings', {'batch_id': batch_id})).structured_content
                assert sent['data']['results'][0]['job_id'] == 'a'
                assert sent['data']['results'][0]['outcome'] == 'sent'
                assert sent['data']['results'][0]['job']['company'] == '甲公司'
                repeated = (await client.call_tool('send_greetings', {'batch_id': batch_id})).structured_content
                assert repeated['error']['code'] == 'INVALID_BATCH'
                assert (await client.call_tool('prepare_greetings', {'job_ids': ['a']})).structured_content['data']['batch_id'] is None
                assert sends == ids
                assert sent['data']['quota']['used'] == 30
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(scenario())


def test_bridge_autostarts_search_annotations_and_detail_validation(tmp_path):
    async def scenario():
        ledger = GreetingLedger(tmp_path)
        assert ledger.reserve('a')
        ledger.mark_sent('a')
        session = ExtensionSession(port=0, launch_window=False, token='a' * 43)
        server = create_server(session, ledger=ledger)
        async with Client(server) as client:
            disabled = (await client.call_tool('send_greetings', {'batch_id': 'x'})).structured_content
            assert disabled['error']['code'] == 'SENDING_UNAVAILABLE'
            assert 'BOSS_ZP_ENABLE_SEND' in (disabled['error']['next_action'] or '')
            bad_prepare = await client.call_tool('prepare_greetings', {'job_ids': ['https://www.zhipin.com/job_detail/a.html']}, raise_on_error=False)
            assert bad_prepare.is_error
            # Bridge started automatically in lifespan without calling get_session_status first.
            async with connect(f'ws://127.0.0.1:{session.bridge.port}/', origin='chrome-extension://' + 'a' * 32) as browser:
                await authenticate(browser, session.token)
                async def serve():
                    async for raw in browser:
                        request = json.loads(raw)
                        if request['command'] == 'search_jobs':
                            data = {'jobs': [{'job_id': 'a', 'url': 'https://www.zhipin.com/job_detail/a.html', 'title': '岗位A', 'company': '甲公司'}],
                                    'search': {'keyword': 'Python', 'city': '北京', 'stop_reason': 'site_end', 'filters': [],
                                               'filter_query': {}, }}
                        else:
                            data = {'job_id': 'a', 'url': 'https://www.zhipin.com/job_detail/a.html', 'title': '岗位A',
                                    'company': '甲公司', 'description': '', 'contact_status': 'not_contacted'}
                        await browser.send(json.dumps({'id': request['id'], 'ok': True, 'data': data}, ensure_ascii=False))
                worker = asyncio.create_task(serve())
                searched = (await client.call_tool('search_jobs', {'keyword': 'Python', 'city': '北京市'})).structured_content
                assert searched['ok'] is True
                assert searched['data']['recorded_outcomes'] == {'a': 'sent'}
                missing_desc = (await client.call_tool('get_job_detail', {'job_id': 'a'})).structured_content
                assert missing_desc['error']['code'] == 'DETAIL_NOT_RECOGNIZED'
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(scenario())
