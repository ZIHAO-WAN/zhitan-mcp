"""Review snapshots and sequential greeting batches."""
import asyncio
import re
import secrets
import sqlite3
from datetime import datetime, time, timedelta, timezone
from typing import Awaitable, Callable

from .models import JobDetail, JobSummary, SendResult
from .storage import BEIJING, BossError, GreetingLedger, beijing_today

BATCH_SIZE = 30
SEND_TIMEOUT = 1530
_JOB_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
_RETRYABLE = {'not_clicked', 'rejected'}
_BATCH_STOP_REASONS = {
    'VERIFICATION_REQUIRED', 'AUTH_REQUIRED', 'PAGE_TIMEOUT', 'NO_TAB',
    'BROWSER_ERROR', 'DAILY_LIMIT_REACHED', 'RESUME_INCOMPLETE', 'DEADLINE_EXCEEDED', 'EXTENSION_OUTDATED',
}


class BatchCoordinator:
    def __init__(self, ledger: GreetingLedger,
                 cached_detail: Callable[[str], JobDetail | None],
                 send_once: Callable[[JobSummary, datetime], Awaitable[SendResult]]):
        self.ledger = ledger
        self.cached_detail = cached_detail
        self.send_once = send_once
        self._batch_id: str | None = None
        self._jobs: tuple[JobSummary, ...] = ()
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def prepare(self, job_ids: list[str]) -> dict:
        if any(not isinstance(job_id, str) or not _JOB_ID_PATTERN.fullmatch(job_id) for job_id in job_ids):
            raise BossError('INVALID_JOB_ID', '请使用有效的官网 job_id。')
        if not 1 <= len(job_ids) <= BATCH_SIZE or len(set(job_ids)) != len(job_ids):
            raise BossError('INVALID_BATCH', '请提交最多 30 个不同的岗位；满 30 个合格岗位才能生成审核清单。')
        async with self._lock:
            self._batch_id, self._jobs, self._expires_at = None, (), None
            prior, quota = self.ledger.review_snapshot(job_ids, beijing_today())
            jobs, skipped = [], []
            for job_id in job_ids:
                recorded = prior.get(job_id)
                if recorded and recorded not in _RETRYABLE:
                    skipped.append({'job_id': job_id, 'stage': 'ledger', 'reason': recorded})
                    continue
                detail = self.cached_detail(job_id)
                if detail is None:
                    reason = 'DETAIL_REQUIRED'
                elif detail.contact_status == 'not_contacted':
                    jobs.append(detail.job)
                    continue
                else:
                    reason = detail.contact_status
                skipped.append({'job_id': job_id, 'stage': 'review', 'reason': reason})

            result = {'batch_id': None, 'jobs': [job.model_dump() for job in jobs], 'skipped': skipped,
                      'missing_count': BATCH_SIZE - len(jobs), 'quota': quota}
            if quota['remaining'] < BATCH_SIZE:
                result['blocked_reason'] = 'INSUFFICIENT_QUOTA'
            elif len(jobs) == BATCH_SIZE:
                self._batch_id = secrets.token_urlsafe(24)
                self._jobs = tuple(job.model_copy(deep=True) for job in jobs)
                self._expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
                result.update(batch_id=self._batch_id, expires_at=self._expires_at.isoformat())
            return result

    async def send(self, batch_id: str, progress=None) -> dict:
        async with self._lock:
            if (self._batch_id is None or batch_id != self._batch_id
                    or self._expires_at is None or datetime.now(timezone.utc) >= self._expires_at):
                raise BossError('INVALID_BATCH', '审核清单不存在、已过期或已经使用，请重新准备。')
            day = beijing_today()
            if self.ledger.quota(day)['remaining'] < BATCH_SIZE:
                raise BossError('INSUFFICIENT_QUOTA', '今日 MCP 剩余额度不足 30 次。')
            midnight = datetime.combine(day + timedelta(days=1), time.min, tzinfo=BEIJING)
            jobs = self._jobs
            self._batch_id, self._jobs, self._expires_at = None, (), None
            results = []
            stop_reason = None
            try:
                async with asyncio.timeout(SEND_TIMEOUT):
                    for job in jobs:
                        try:
                            reserved = self.ledger.reserve(job.job_id, day)
                        except BossError as exc:
                            stop_reason = exc.code
                            break
                        item = {'job_id': job.job_id, 'job': job.model_dump(),
                                'outcome': 'unknown' if reserved else 'already_recorded'}
                        results.append(item)
                        if reserved:
                            result = await self.send_once(job, midnight)
                            match result.outcome:
                                case 'sent':
                                    self.ledger.mark_sent(job.job_id)
                                case 'already_contacted':
                                    self.ledger.mark_already_contacted(job.job_id)
                                case 'not_clicked':
                                    self.ledger.mark_not_clicked(job.job_id, result.reason or 'precheck_failed')
                                case 'rejected':
                                    self.ledger.mark_rejected(job.job_id)

                            item['outcome'] = 'precheck_failed' if result.outcome == 'not_clicked' else result.outcome
                            if result.reason:
                                item['reason'] = result.reason
                            if result.outcome == 'unknown' or result.reason in _BATCH_STOP_REASONS:
                                stop_reason = result.reason or 'UNKNOWN_OUTCOME'
                                break
                        if progress:
                            await progress(len(results), len(jobs))
            except TimeoutError:
                stop_reason = 'TIMEOUT'
            except sqlite3.Error:
                stop_reason = 'LEDGER_ERROR'

            results.extend({'job_id': job.job_id, 'job': job.model_dump(), 'outcome': 'not_attempted'}
                           for job in jobs[len(results):])
            return {'results': results, 'stop_reason': stop_reason, 'quota': self.ledger.quota(beijing_today())}
