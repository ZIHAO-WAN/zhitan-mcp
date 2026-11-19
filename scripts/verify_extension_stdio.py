"""Check the real stdio tool catalog without executing actions."""
import asyncio
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]


async def main():
    transport = StdioTransport(sys.executable, ['-m', 'boss_zp.server'], cwd=str(ROOT),
                                     env={'PYTHONUTF8': '1'})
    async with Client(transport) as client:
        names = sorted(tool.name for tool in await client.list_tools())
        assert names == ['get_job_detail', 'get_session_status', 'prepare_greetings', 'search_jobs', 'send_greetings'], names
        print('Stdio connected:', ', '.join(names))


if __name__ == '__main__':
    asyncio.run(main())
