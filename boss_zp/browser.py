"""Session backed by the user's signed-in browser extension."""
import asyncio
from collections import OrderedDict
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from .bridge import BrowserBridge
from .models import JobDetail, JobSummary, SearchResult, SendResult
from .storage import BossError, bridge_pairing_token, default_data_dir

MAX_CACHED_JOBS = 2000


def normalize_city(city: str) -> str:
    return city.strip().removesuffix('市').strip()


def _lru_put(cache: OrderedDict, key, value, max_size: int) -> None:
    if key in cache:
        cache.move_to_end(key)
    cache[key] = value
    while len(cache) > max_size:
        cache.popitem(last=False)


def canonical_job_url(value: str) -> tuple[str, str]:
    if re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        return value, f'https://www.zhipin.com/job_detail/{value}.html'
    parsed = urlparse(value)
    match = re.fullmatch(r'/job_detail/([A-Za-z0-9_-]{1,128})\.html', parsed.path)
    if parsed.scheme != 'https' or parsed.netloc != 'www.zhipin.com' or not match:
        raise BossError('INVALID_JOB_ID', '请使用有效的官网 job_id 或职位链接。')
    return match.group(1), f'https://www.zhipin.com/job_detail/{match.group(1)}.html'


class ExtensionSession:
    def __init__(self, *, port=43286, launch_window=True, token=None, allow_send=False):
        self.token = token if token is not None else bridge_pairing_token(default_data_dir())
        self.bridge = BrowserBridge(self.token, port, allow_send=allow_send)
        self.launch_window = launch_window
        self._directory = None
        self._window = None
        self._jobs: OrderedDict[str, JobSummary] = OrderedDict()
        self._details: OrderedDict[str, JobDetail] = OrderedDict()

    async def ensure_bridge_started(self) -> None:
        try:
            await self.bridge.start()
        except OSError:
            raise BossError('SESSION_BUSY', '本地扩展连接端口已被占用。', '关闭其他 Boss MCP 会话或配对验证窗口。') from None

    async def _close_pairing_window(self) -> None:
        if self._window and self._window.poll() is None:
            self._window.terminate()
            await asyncio.to_thread(self._window.wait, timeout=5)
            self._window = None

    async def start_pairing(self):
        await self.ensure_bridge_started()
        if self.bridge.connected or not self.launch_window or (self._window and self._window.poll() is None):
            return
        self._directory = tempfile.TemporaryDirectory(prefix='boss-pair-')
        status = Path(self._directory.name) / 'status.txt'
        status.write_text('已配对请等待自动连接；首次使用请复制密钥到扩展。', encoding='utf-8')
        env = os.environ.copy()
        env['BOSS_BRIDGE_STATUS_PATH'] = str(status)
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'pair_window.ps1'
        self._window = subprocess.Popen(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self._window.stdin.write(self.token + '\n')
        self._window.stdin.close()

    async def get_session_status(self):
        await self.start_pairing()
        try:
            result = await self.bridge.request('status', timeout=10)
            await self._close_pairing_window()
            site_state = result.get('state', 'unrecognized')
            return {'state': 'connected', 'extension_state': site_state,
                    'message': '浏览器扩展已连接。' if site_state == 'authenticated' else '扩展已连接；请在 Boss 页面完成登录或安全验证。',
                    'bridge_protocol': result.get('bridge_protocol'), 'send_contract': result.get('send_contract'),
                    'extension_version': result.get('extension_version'),
                    'job_cards': result.get('job_cards'), 'search': result.get('search')}
        except BossError as exc:
            if exc.code != 'BRIDGE_DISCONNECTED':
                raise
            return {'state': 'pairing', 'message': '请在本地配对窗口复制密钥并连接浏览器扩展。'}

    async def search_jobs(self, keyword, city, limit=60):
        await self.ensure_bridge_started()
        response = await self.bridge.request('search_jobs', {'keyword': keyword, 'city': city, 'limit': limit}, timeout=50)
        await self._close_pairing_window()
        search = response.get('search') or {}
        if 'stop_reason' not in search:
            raise BossError('EXTENSION_OUTDATED', '请重新加载当前版“ZhiTan · Boss 连接器”扩展后再搜索。')
        if (search.get('keyword') != keyword or not search.get('city')
                or normalize_city(search['city']) != normalize_city(city)):
            raise BossError('FILTER_NOT_APPLIED', '无法确认当前页面与指定关键词和城市一致。', '请在官网打开匹配的搜索结果页。')
        jobs, seen = [], set()
        for row in response.get('jobs', []):
            job = JobSummary(**row)
            job_id, url = canonical_job_url(job.url)
            if job_id != job.job_id or not job.title:
                raise BossError('JOB_MISMATCH', '岗位链接与身份不一致。')
            if job_id not in seen:
                job.url = url
                jobs.append(job)
                seen.add(job_id)
        for job in jobs:
            _lru_put(self._jobs, job.job_id, job, MAX_CACHED_JOBS)
        filters = search.get('filters') or []
        filter_query = search.get('filter_query') or {}
        return SearchResult(keyword=keyword, city=city,
                            applied_filters={'关键词': keyword, '城市': city,
                                             **{f'官网筛选{index + 1}': value for index, value in enumerate(filters)},
                                             **({'官网筛选参数': str(filter_query)} if filter_query else {})},
                            returned_count=len(jobs), stop_reason=search['stop_reason'], jobs=jobs)

    async def get_job_detail(self, job_id):
        job_id, canonical_url = canonical_job_url(job_id)
        await self.ensure_bridge_started()
        result = await self.bridge.request('read_detail', {'job_id': job_id})
        await self._close_pairing_window()
        if result.get('job_id') != job_id or result.get('url') != canonical_url:
            raise BossError('JOB_MISMATCH', '无法确认当前详情页与岗位一致。')
        if not result.get('title') or not result.get('company') or not result.get('description'):
            raise BossError('DETAIL_NOT_RECOGNIZED', '无法确认职位详情、公司或正文内容。')
        cached = self._jobs.get(job_id)
        fields = cached.model_dump() if cached else {'job_id': job_id, 'url': result['url']}
        fields.update({'url': result['url'], 'title': result['title'], 'company': result['company'],
                       'location': result.get('location') or fields.get('location'),
                       'salary_text': result.get('salary_text') or fields.get('salary_text'),
                       'hr_active': result.get('hr_active') or fields.get('hr_active'),
                       'company_info': result.get('company_info') or fields.get('company_info') or []})
        job = JobSummary(**fields)
        detail = JobDetail(job=job, description=result['description'], contact_status=result['contact_status'])
        _lru_put(self._details, job_id, detail, MAX_CACHED_JOBS)
        return detail

    def cached_detail(self, job_id):
        return self._details.get(job_id)

    async def send_once(self, job, midnight):
        deadline = min(midnight, datetime.now(timezone.utc) + timedelta(seconds=45))
        try:
            response = await self.bridge.request('send_once', {
                'job_id': job.job_id, 'title': job.title, 'company': job.company,
                'send_contract': 4, 'deadline': int(deadline.timestamp() * 1000),
            }, timeout=50)
            return SendResult.model_validate(response)
        except BossError as exc:
            return SendResult(outcome='unknown', reason=exc.code)
        except (TimeoutError, ValueError):
            return SendResult(outcome='unknown', reason='CONFIRMATION_UNAVAILABLE')

    async def ensure_send_ready(self):
        await self.ensure_bridge_started()
        status = await self.bridge.request('status', timeout=10)
        await self._close_pairing_window()
        if status.get('bridge_protocol') != 1 or status.get('send_contract') != 4:
            raise BossError('EXTENSION_OUTDATED', '请重新加载当前版“ZhiTan · Boss 连接器”扩展后再发送。')

    async def shutdown(self):
        await self.bridge.close()
        await self._close_pairing_window()
        if self._directory:
            self._directory.cleanup()
            self._directory = None
