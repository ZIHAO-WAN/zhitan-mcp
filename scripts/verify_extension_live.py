"""Verify the current Boss search, detail reads and optional 30-job review without sending."""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]


def report(stage, **values):
    print(json.dumps({'stage': stage, **values}, ensure_ascii=True), flush=True)


async def verify(review_30: bool, keyword: str | None) -> bool:
    transport = StdioTransport(sys.executable, ['-m', 'boss_zp.server'], cwd=str(ROOT),
                               env={'PYTHONUTF8': '1'})
    async with Client(transport, timeout=60) as client:
        reconnect_deadline = time.monotonic() + 65
        while time.monotonic() < reconnect_deadline:
            response = (await client.call_tool('get_session_status')).structured_content
            status = response.get('data') or {}
            state = status.get('state')
            if state == 'connected':
                break
            if state != 'pairing':
                report('blocked', reason=(response.get('error') or {}).get('code', state))
                return False
            await asyncio.sleep(1)
        else:
            report('blocked', reason='BRIDGE_DISCONNECTED')
            return False

        context = status.get('search') or {}
        report('status', state=status.get('extension_state'), extension_version=status.get('extension_version'),
               keyword_recognized=bool(context.get('keyword')), city_recognized=bool(context.get('city')),
               job_cards=status.get('job_cards'))
        if status.get('send_contract') != 4:
            report('blocked', reason='EXTENSION_OUTDATED')
            return False
        keyword = keyword or context.get('keyword')
        if not keyword or not context.get('city'):
            report('blocked', reason='SEARCH_CONTEXT_REQUIRED')
            return False

        started = time.perf_counter()
        arguments = {'keyword': keyword, 'city': context['city'], 'limit': 60}
        result = (await client.call_tool('search_jobs', arguments)).structured_content
        data = result.get('data') or {}
        jobs = data.get('jobs') or []
        report('search', elapsed_ms=round((time.perf_counter() - started) * 1000), ok=result['ok'],
               count=len(jobs), stop_reason=data.get('stop_reason'),
               error_code=(result.get('error') or {}).get('code'))
        if not result['ok'] or not jobs:
            return False

        eligible = []
        detail_reads = 0
        recorded = data.get('recorded_outcomes', {})
        for job in jobs if review_30 else jobs[:1]:
            job_id = job['job_id']
            if review_30 and recorded.get(job_id) in {'sent', 'unknown', 'already_contacted'}:
                continue
            detail_started = time.perf_counter()
            detail = (await client.call_tool('get_job_detail', {'job_id': job_id})).structured_content
            detail_reads += 1
            code = (detail.get('error') or {}).get('code')
            report('detail', elapsed_ms=round((time.perf_counter() - detail_started) * 1000),
                   ok=detail['ok'], error_code=code)
            if code in {'AUTH_REQUIRED', 'VERIFICATION_REQUIRED', 'BRIDGE_DISCONNECTED'}:
                return False
            if not review_30:
                return detail['ok']
            if (detail.get('data') or {}).get('contact_status') == 'not_contacted':
                eligible.append(job_id)
            if len(eligible) == 30:
                break
        prepare_started = time.perf_counter()
        review = (await client.call_tool('prepare_greetings', {'job_ids': eligible})).structured_content if eligible else {}
        summary = review.get('data') or {}
        prepared = bool(summary.get('batch_id'))
        report('review', elapsed_ms=round((time.perf_counter() - prepare_started) * 1000),
               total_ms=round((time.perf_counter() - started) * 1000), detail_reads=detail_reads,
               eligible=len(eligible), missing=summary.get('missing_count', 30 - len(eligible)),
               prepared=prepared, error_code=(review.get('error') or {}).get('code'))
        return prepared


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-30', action='store_true', help='Read details until 30 eligible jobs are found and prepare a local review.')
    parser.add_argument('--keyword', help='Use an explicitly requested keyword with the city and filters already set in Boss.')
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(verify(args.review_30, args.keyword)) else 1)
