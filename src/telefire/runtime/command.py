import asyncio
import inspect


class ServiceCommand:
    def __init__(self, service, logger):
        self.service = service
        self.logger = logger

    @property
    def client(self):
        return self.service.client

    async def _invoke_async(self, action):
        result = action() if callable(action) else action
        if inspect.isawaitable(result):
            return await result
        return result

    async def _run_once_async(self, action):
        await self.service.connect()
        try:
            return await self._invoke_async(action)
        finally:
            await self.service.close()

    async def _run_forever_async(self, setup=None, runner=None):
        if runner is None:
            raise ValueError("A long-running command requires a runner")

        await self.service.connect()
        try:
            if setup is not None:
                await self._invoke_async(setup)
            await self._invoke_async(runner)
        finally:
            await self.service.close()

    def run_once(self, action):
        return asyncio.run(self._run_once_async(action))

    def run_forever(self, setup=None, runner=None):
        return asyncio.run(self._run_forever_async(setup=setup, runner=runner))
