import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from boss_zp.batch import BatchCoordinator
from boss_zp.browser import ExtensionSession
from boss_zp.storage import GreetingLedger


def test_fixed_candidates_reuse_review_cache_and_send_has_no_backend_reads(tmp_path):
    async def scenario():
        session = ExtensionSession(port=0, token='a'*43, launch_window=False)
        try:
            calls = []
            async def request(command, payload=None, **kwargs):
                calls.append(command)
                job_id = payload['job_id']
                if command == 'send_once': return {'outcome': 'sent'}
                return {'job_id': job_id, 'url': f'https://www.zhipin.com/job_detail/{job_id}.html',
                        'title': 'Job', 'company': 'Company', 'description': 'Description', 'contact_status': 'not_contacted'}
            session.bridge.request = request
            ids = [f'job{i}' for i in range(30)]
            for job_id in ids: await session.get_job_detail(job_id)
            coordinator = BatchCoordinator(GreetingLedger(tmp_path), session.cached_detail, session.send_once)
            review = await coordinator.prepare(ids)
            assert calls == ['read_detail'] * 30
            await coordinator.send(review['batch_id'])
            assert calls == ['read_detail'] * 30 + ['send_once'] * 30
            await session.get_job_detail(ids[0]) # Public read remains fresh.
            assert calls[-1] == 'read_detail'
            assert session.cached_detail(ids[0]) is not None
            assert session.cached_detail('unread') is None
            # Known ledger rows must be skipped before any browser read.
            await coordinator.prepare(ids)
            assert calls.count('read_detail') == 31
        finally:
            await session.shutdown()
    asyncio.run(scenario())


def test_new_approval_reserves_not_clicked_once_and_preserves_history(tmp_path):
    ledger = GreetingLedger(tmp_path)
    assert ledger.reserve('a')
    ledger.mark_not_clicked('a', 'JOB_MISMATCH')
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: GreetingLedger(tmp_path).reserve('a'), range(12)))
    assert results.count(True) == 1
    assert ledger.get('a') == 'unknown'
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute('SELECT reason FROM not_clicked_attempts').fetchall() == [('JOB_MISMATCH',)]
    ledger.mark_sent('a')
    assert not ledger.reserve('a')


def test_legacy_failure_and_sent_records_are_preserved(tmp_path):
    path = tmp_path / 'greetings.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE greetings (job_id TEXT PRIMARY KEY, outcome TEXT NOT NULL, updated_at TEXT NOT NULL)')
        conn.executemany('INSERT INTO greetings VALUES (?, ?, ?)', [('a', 'not_clicked', 'old'), ('b', 'sent', 'old')])
    ledger = GreetingLedger(tmp_path)
    assert ledger.reserve('a')
    assert not ledger.reserve('b')
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT job_id, recorded_at FROM not_clicked_attempts').fetchall() == [('a', 'old')]
    assert ledger.get('b') == 'sent'
