"""Read-only live test of MCP transport and the signed-in Claude CLI."""
import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def data(result):
    if result.isError:
        raise RuntimeError('MCP tool returned an error')
    if result.structuredContent:
        return result.structuredContent
    return json.loads(next(block.text for block in result.content if block.type == 'text'))


async def main():
    script = Path(__file__).with_name('implementer_bridge.py').resolve()
    server = StdioServerParameters(command=sys.executable, args=['-B', str(script)], env=dict(os.environ))
    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            required = {'implement_changes', 'implementation_status', 'review_changes'}
            assert required <= names, names
            print('MCP initialized; implementation and read-only review tools discovered.', flush=True)
            job = data(await session.call_tool('review_changes', {
                'task': 'Read-only bridge connection check. Read README.md and return only its first heading. Do not edit any file or run commands.',
                'scope': 'Read README.md only; no file modifications.',
                'acceptance_criteria': 'Return the actual README heading, no tests or commands needed.'},
                read_timeout_seconds=timedelta(seconds=60)))
            print('Claude job dispatched.', flush=True)
            for _ in range(150):
                status = data(await session.call_tool('implementation_status', {'job_id': job['job_id']}))
                if status['status'] != 'running':
                    print(json.dumps(status, ensure_ascii=False, indent=2))
                    assert status['status'] == 'completed', status['status']
                    assert status['repository'] == status['repository_after'], 'Checkout changed during read-only smoke test'
                    assert status['review_verification']['unchanged'], 'Source changed during review'
                    assert status.get('session_id'), 'No Claude session returned'
                    return
                if _ % 15 == 0:
                    print(f"Claude running: {status['elapsed_seconds']} seconds", flush=True)
                await asyncio.sleep(2)
            raise RuntimeError('Smoke test did not finish within five minutes')


if __name__ == '__main__':
    asyncio.run(main())
