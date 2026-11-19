import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pytest

from boss_zp import batch, storage
from boss_zp.batch import BATCH_SIZE, BatchCoordinator
from boss_zp.models import JobDetail, JobSummary, SendResult
from boss_zp.storage import BEIJING, DAILY_LIMIT, BossError, GreetingLedger


IDS = [f'job{i}' for i in range(BATCH_SIZE)]


def detail(job_id):
    return JobDetail(job=JobSummary(job_id=job_id, url=f'https://www.zhipin.com/job_detail/{job_id}.html',
                                    title='同名岗位', company='同一公司'),
                     description='职责', contact_status='not_contacted')


async def sent(_job, _deadline):
    return SendResult(outcome='sent')


def test_review_bounds_and_cached_details(tmp_path):
    async def scenario():
        opened = set(IDS[:-1])
        coordinator = BatchCoordinator(GreetingLedger(tmp_path), lambda key: detail(key) if key in opened else None, sent)
        short = await coordinator.prepare(IDS[:-1])
        assert short['missing_count'] == 1 and short['batch_id'] is None
        unread = await coordinator.prepare(IDS)
        assert unread['skipped'] == [{'job_id': IDS[-1], 'stage': 'review', 'reason': 'DETAIL_REQUIRED'}]
        assert unread['batch_id'] is None
        opened.add(IDS[-1])
        ready = await coordinator.prepare(IDS)
        assert ready['batch_id']
        assert [job['job_id'] for job in ready['jobs']] == IDS
        with pytest.raises(BossError, match='30'):
            await coordinator.prepare([*IDS, 'extra'])
        with pytest.raises(BossError, match='30'):
            await coordinator.prepare([*IDS[:-1], IDS[0]])
        with pytest.raises(BossError, match='job_id'):
            await coordinator.prepare(['https://www.zhipin.com/job_detail/a.html'])
    asyncio.run(scenario())


def test_batch_is_single_use_and_persistent(tmp_path):
    async def scenario():
        coordinator = BatchCoordinator(GreetingLedger(tmp_path), detail, sent)
        review = await coordinator.prepare(IDS)
        with pytest.raises(BossError, match='审核清单'):
            await coordinator.send('错误的清单编号')
        result = await coordinator.send(review['batch_id'])
        assert [item['outcome'] for item in result['results']] == ['sent'] * BATCH_SIZE
        assert result['quota']['used'] == BATCH_SIZE
        with pytest.raises(BossError, match='审核清单'):
            await coordinator.send(review['batch_id'])
        replacement = BatchCoordinator(GreetingLedger(tmp_path), detail, sent)
        assert (await replacement.prepare(IDS))['batch_id'] is None
    asyncio.run(scenario())


def test_expired_batch_cannot_send(tmp_path):
    async def scenario():
        coordinator = BatchCoordinator(GreetingLedger(tmp_path), detail, sent)
        review = await coordinator.prepare(IDS)
        coordinator._expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        with pytest.raises(BossError, match='审核清单'):
            await coordinator.send(review['batch_id'])
        assert coordinator.ledger.get(IDS[0]) is None
    asyncio.run(scenario())


def test_known_outcomes_continue_until_unknown(tmp_path):
    async def scenario():
        calls = []
        answers = iter([
            SendResult(outcome='not_clicked', reason='JOB_MISMATCH'),
            SendResult(outcome='already_contacted'),
            SendResult(outcome='rejected', reason='JOB_CLOSED'),
            SendResult(outcome='sent'),
            SendResult(outcome='unknown', reason='BRIDGE_DISCONNECTED'),
        ])
        async def send(job, _deadline):
            calls.append(job.job_id)
            return next(answers)
        ledger = GreetingLedger(tmp_path)
        coordinator = BatchCoordinator(ledger, detail, send)
        review = await coordinator.prepare(IDS)
        result = await coordinator.send(review['batch_id'])
        assert calls == IDS[:5]
        assert [item['outcome'] for item in result['results'][:5]] == [
            'precheck_failed', 'already_contacted', 'rejected', 'sent', 'unknown',
        ]
        assert {item['outcome'] for item in result['results'][5:]} == {'not_attempted'}
        assert result['stop_reason'] == 'BRIDGE_DISCONNECTED'
        assert result['quota']['used'] == 2
        assert ledger.get(IDS[2]) == 'rejected'
        replacement = await coordinator.prepare(IDS)
        assert replacement['missing_count'] == 3
        assert {item['reason'] for item in replacement['skipped']} == {'sent', 'unknown', 'already_contacted'}
    asyncio.run(scenario())


@pytest.mark.parametrize('outcome, reason', [('not_clicked', 'VERIFICATION_REQUIRED'), ('rejected', 'DAILY_LIMIT_REACHED')])
def test_known_global_failure_stops_and_releases_quota(tmp_path, outcome, reason):
    async def scenario():
        async def send(_job, _deadline):
            return SendResult(outcome=outcome, reason=reason)
        coordinator = BatchCoordinator(GreetingLedger(tmp_path), detail, send)
        review = await coordinator.prepare(IDS)
        result = await coordinator.send(review['batch_id'])
        assert result['stop_reason'] == reason
        assert result['quota']['used'] == 0
        assert result['results'][1]['outcome'] == 'not_attempted'
        assert coordinator.ledger.get(IDS[0]) == outcome
    asyncio.run(scenario())


def test_timeout_reports_current_job_and_preserves_reservation(tmp_path):
    async def scenario():
        async def send(job, _deadline):
            if job.job_id == IDS[0]:
                return SendResult(outcome='sent')
            raise TimeoutError()
        coordinator = BatchCoordinator(GreetingLedger(tmp_path), detail, send)
        review = await coordinator.prepare(IDS)
        result = await coordinator.send(review['batch_id'])
        assert result['stop_reason'] == 'TIMEOUT'
        assert [item['outcome'] for item in result['results'][:3]] == ['sent', 'unknown', 'not_attempted']
        assert GreetingLedger(tmp_path).get(IDS[1]) == 'unknown'
    asyncio.run(scenario())


def test_cancellation_preserves_reservation(tmp_path):
    async def scenario():
        entered = asyncio.Event()

        async def send(_job, _deadline):
            entered.set()
            await asyncio.Event().wait()

        one = BatchCoordinator(GreetingLedger(tmp_path), detail, send)
        two = BatchCoordinator(GreetingLedger(tmp_path), detail, sent)
        first, second = await one.prepare(IDS), await two.prepare(IDS)
        task = asyncio.create_task(one.send(first['batch_id']))
        await entered.wait()
        assert (await two.send(second['batch_id']))['results'][0]['outcome'] == 'already_recorded'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert GreetingLedger(tmp_path).get(IDS[0]) == 'unknown'
    asyncio.run(scenario())


def test_quota_migrates_at_beijing_midnight_and_is_atomic_after_restart(tmp_path, monkeypatch):
    day = date(2026, 11, 18)
    monkeypatch.setattr(storage, 'beijing_today', lambda: day)
    with sqlite3.connect(tmp_path / 'greetings.sqlite3') as conn:
        conn.execute('CREATE TABLE greetings (job_id TEXT PRIMARY KEY, outcome TEXT, updated_at TEXT)')
        conn.executemany('INSERT INTO greetings VALUES (?, ?, ?)', [
            ('yesterday', 'sent', '2026-11-17T15:59:59+00:00'),
            ('today', 'sent', '2026-11-17T16:00:00+00:00'),
            ('pending', 'unknown', '2026-11-18T01:00:00+00:00'),
            ('manual', 'already_contacted', '2026-11-18T01:00:00+00:00'),
        ])
    ledger = GreetingLedger(tmp_path)
    assert ledger.quota(day)['used'] == 2
    assert ledger.quota(day - timedelta(days=1))['used'] == 1
    for index in range(DAILY_LIMIT - 3):
        assert ledger.reserve(f'old{index}')

    def reserve(index):
        try:
            return GreetingLedger(tmp_path).reserve(f'new{index}')
        except BossError as exc:
            assert exc.code == 'LOCAL_DAILY_LIMIT'
            return False

    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(reserve, range(12))) == 1
    assert GreetingLedger(tmp_path).quota(day)['remaining'] == 0
    day += timedelta(days=1)
    assert GreetingLedger(tmp_path).reserve('tomorrow')
    assert GreetingLedger(tmp_path).quota(day)['used'] == 1
    assert ledger.get('pending') == 'unknown'


def test_insufficient_quota_blocks_full_batch_before_send(tmp_path):
    async def scenario():
        ledger = GreetingLedger(tmp_path)
        coordinator = BatchCoordinator(ledger, detail, sent)
        for index in range(DAILY_LIMIT - BATCH_SIZE):
            assert ledger.reserve(f'old{index}')
        review = await coordinator.prepare(IDS)
        assert review['batch_id']
        assert ledger.reserve('race')
        with pytest.raises(BossError) as caught:
            await coordinator.send(review['batch_id'])
        assert caught.value.code == 'INSUFFICIENT_QUOTA'
        assert (await coordinator.prepare(IDS))['blocked_reason'] == 'INSUFFICIENT_QUOTA'
        assert ledger.get(IDS[0]) is None
    asyncio.run(scenario())


def test_midnight_stops_next_job_and_preserves_previous_quota(tmp_path, monkeypatch):
    day = date(2026, 11, 18)
    monkeypatch.setattr(storage, 'beijing_today', lambda: day)
    monkeypatch.setattr(batch, 'beijing_today', lambda: day)

    async def scenario():
        async def send(job, deadline):
            nonlocal day
            assert job.job_id == IDS[0]
            assert deadline == datetime(2026, 11, 19, tzinfo=BEIJING)
            day += timedelta(days=1)
            return SendResult(outcome='sent')

        ledger = GreetingLedger(tmp_path)
        coordinator = BatchCoordinator(ledger, detail, send)
        review = await coordinator.prepare(IDS)
        result = await coordinator.send(review['batch_id'])
        assert result['stop_reason'] == 'DAY_CHANGED'
        assert result['results'][0]['outcome'] == 'sent'
        assert result['results'][1]['outcome'] == 'not_attempted'
        assert result['quota']['used'] == 0
        assert ledger.quota(day - timedelta(days=1))['used'] == 1
    asyncio.run(scenario())
