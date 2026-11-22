"""Authenticated loopback channel between the local MCP and browser extension."""
import asyncio
import hashlib
import hmac
import json
import re
import secrets

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .storage import BossError


class BrowserBridge:
    def __init__(self, token: str, port: int = 43286, *, allow_send: bool = False):
        self._token = token
        self.port = port
        self._server = None
        self._start_lock = asyncio.Lock()
        self._client = None
        self._pending = {}
        self.allow_send = allow_send

    @property
    def connected(self):
        return self._client is not None

    async def start(self):
        async with self._start_lock:
            if self._server is None:
                self._server = await serve(self._handle, '127.0.0.1', self.port,
                                           max_size=1_000_000, open_timeout=5, ping_interval=20)
                self.port = self._server.sockets[0].getsockname()[1]

    async def _handle(self, socket):
        origin = socket.request.headers.get('Origin', '')
        if not re.fullmatch(r'chrome-extension://[a-p]{32}', origin):
            await socket.close(code=1008)
            return
        try:
            nonce = secrets.token_urlsafe(24)
            signature = lambda role: hmac.new(self._token.encode('ascii'), f'{role}:{nonce}'.encode('ascii'), hashlib.sha256).hexdigest()
            await socket.send(json.dumps({'type': 'challenge', 'nonce': nonce, 'proof': signature('server')}))
            hello = json.loads(await asyncio.wait_for(socket.recv(), 5))
            proof = hello.get('proof') if isinstance(hello, dict) and hello.get('type') == 'authenticate' else None
            if not isinstance(proof, str) or not re.fullmatch(r'[0-9a-f]{64}', proof) or not secrets.compare_digest(proof, signature('client')) or self._client:
                await socket.close(code=1008)
                return
            self._client = socket
            await socket.send(json.dumps({'type': 'connected'}))
            async for message in socket:
                try:
                    payload = json.loads(message)
                    ident = payload.get('id')
                    future = self._pending.pop(ident, None)
                    if future and not future.done():
                        future.set_result(payload)
                except (ValueError, AttributeError, TypeError):
                    continue
        except (TimeoutError, ValueError, ConnectionClosed):
            pass
        finally:
            if self._client is socket:
                self._client = None
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(BossError('BRIDGE_DISCONNECTED', '浏览器连接已断开。'))
                self._pending.clear()

    async def request(self, command: str, payload: dict | None = None, timeout: float = 20):
        if self._client is None:
            raise BossError('BRIDGE_DISCONNECTED', '请先连接 Boss 浏览器扩展。')
        if command not in {'status', 'search_jobs', 'read_detail'} and not (self.allow_send and command == 'send_once'):
            raise BossError('UNSUPPORTED_OPERATION', '该操作尚未通过验证。')
        ident = secrets.token_urlsafe(18)
        future = asyncio.get_running_loop().create_future()
        self._pending[ident] = future
        try:
            await self._client.send(json.dumps({'id': ident, 'command': command, 'payload': payload or {}}, ensure_ascii=False))
            result = await asyncio.wait_for(future, timeout)
            if result.get('ok') is not True:
                errors = {
                    'NO_TAB': ('请切换到已登录的 Boss 官网标签页。', None),
                    'PAGE_TIMEOUT': ('官网页面加载超时。', None),
                    'JOB_NOT_IN_SEARCH': ('岗位不在当前已读取的搜索结果中。', '请先重新读取搜索结果。'),
                    'VERIFICATION_REQUIRED': ('官网需要安全验证。', '请在日常浏览器中完成验证。'),
                    'AUTH_REQUIRED': ('无法确认官网已登录。', '请在日常浏览器中登录。'),
                    'SEARCH_NOT_RECOGNIZED': ('无法识别当前搜索结果。', None),
                    'CITY_NOT_MATCHED': ('无法确认当前搜索城市与请求一致。', '请先在官网搜索页选择指定城市。'),
                    'FILTER_NOT_APPLIED': ('无法确认搜索条件已生效。', None),
                    'DETAIL_NOT_RECOGNIZED': ('无法确认职位详情和沟通状态。', None),
                    'EXTENSION_OUTDATED': ('请重新加载当前版“ZhiTan · Boss 连接器”扩展。', None),
                    'JOB_MISMATCH': ('实际页面与指定岗位不一致。', None),
                    'JOB_CLOSED': ('该岗位已关闭或停止招聘。', None),
                }
                code = result.get('code')
                message, action = errors.get(code, ('浏览器未能确认操作结果。', None))
                raise BossError(code if code in errors else 'BROWSER_OPERATION_FAILED', message, action)
            return result.get('data')
        except ConnectionClosed:
            raise BossError('BRIDGE_DISCONNECTED', '浏览器连接已断开。') from None
        finally:
            self._pending.pop(ident, None)

    async def close(self):
        if self._client:
            await self._client.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
