from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from aiohttp import WSMsgType, web


OneBotEventHandler = Callable[[dict[str, Any]], Awaitable[None]]
OneBotHttpHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class OneBotActionError(RuntimeError):
    def __init__(self, action: str, retcode: int, message: str):
        self.action = action
        self.retcode = retcode
        super().__init__(f"{action} failed ({retcode}): {message}")


class OneBotReverseWebSocket:
    def __init__(
        self,
        *,
        token: str,
        self_id: int,
        event_handler: OneBotEventHandler | None = None,
        logger: Any | None = None,
        max_queue_size: int = 2_048,
        event_concurrency: int = 8,
    ):
        if not token:
            raise ValueError("OneBot token cannot be empty")
        if self_id <= 0:
            raise ValueError("OneBot self ID must be positive")
        if max_queue_size < 1:
            raise ValueError("OneBot event queue size must be positive")
        if event_concurrency < 1:
            raise ValueError("OneBot event concurrency must be positive")
        self._token = token
        self._self_id = self_id
        self._event_handler = event_handler
        self._logger = logger
        self._connection: web.WebSocketResponse | None = None
        self._connection_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, tuple[asyncio.Future[Any], str]] = {}
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._event_concurrency = event_concurrency
        self._event_workers: list[asyncio.Task[None]] = []
        self._connected = asyncio.Event()
        self._closed = asyncio.Event()
        self._runner: web.AppRunner | None = None
        self.application = web.Application(client_max_size=1 * 1024 * 1024)
        self.application.router.add_get("/healthz", self._health)
        self.application.router.add_get("/onebot", self._handle_websocket)

    @property
    def connected(self) -> bool:
        connection = self._connection
        return connection is not None and not connection.closed

    def set_event_handler(self, handler: OneBotEventHandler) -> None:
        self._event_handler = handler

    def add_authenticated_route(
        self,
        method: str,
        path: str,
        handler: OneBotHttpHandler,
    ) -> None:
        async def authenticated(request: web.Request) -> web.StreamResponse:
            if not self._is_authenticated(request):
                raise web.HTTPUnauthorized()
            return await handler(request)

        self.application.router.add_route(method, path, authenticated)

    async def start(self, host: str, port: int) -> None:
        if self._runner is not None:
            raise RuntimeError("OneBot server is already running")
        self._runner = web.AppRunner(self.application, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host=host, port=port)
        await site.start()

    async def wait_connected(self, timeout: float | None = None) -> None:
        if timeout is None:
            await self._connected.wait()
            return
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        self._closed.set()
        connection = self._connection
        self._connection = None
        self._connected.clear()
        if connection is not None and not connection.closed:
            await connection.close(code=1001, message=b"service stopping")
        self._fail_pending(ConnectionError("OneBot connection closed"))
        for worker in self._event_workers:
            worker.cancel()
        if self._event_workers:
            await asyncio.gather(*self._event_workers, return_exceptions=True)
            self._event_workers.clear()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def call(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = 30,
    ) -> Any:
        if not action or any(character.isspace() for character in action):
            raise ValueError("OneBot action must be a non-empty token")
        connection = self._connection
        if connection is None or connection.closed:
            raise ConnectionError("NapCat is not connected")
        echo = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[echo] = (future, action)
        try:
            async with self._send_lock:
                await connection.send_json(
                    {
                        "action": action,
                        "params": params or {},
                        "echo": echo,
                    }
                )
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(echo, None)

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "connected": self.connected,
                "selfId": str(self._self_id),
            }
        )

    async def _handle_websocket(self, request: web.Request) -> web.StreamResponse:
        if not self._is_authenticated(request):
            raise web.HTTPUnauthorized()

        websocket = web.WebSocketResponse(
            heartbeat=45,
            max_msg_size=8 * 1024 * 1024,
            compress=False,
        )
        await websocket.prepare(request)
        self._ensure_event_workers()
        async with self._connection_lock:
            previous = self._connection
            self._connection = websocket
            self._connected.set()
            if previous is not None and previous is not websocket:
                await previous.close(code=1000, message=b"replaced")
        self._log("info", "NapCat OneBot connection established")

        try:
            async for incoming in websocket:
                if incoming.type == WSMsgType.TEXT:
                    self._handle_payload(incoming.data)
                elif incoming.type == WSMsgType.ERROR:
                    break
        finally:
            async with self._connection_lock:
                if self._connection is websocket:
                    self._connection = None
                    self._connected.clear()
                    self._fail_pending(ConnectionError("NapCat disconnected"))
            self._log("warning", "NapCat OneBot connection closed")
        return websocket

    def _is_authenticated(self, request: web.Request) -> bool:
        authorization = request.headers.get("Authorization", "")
        supplied_token = authorization.removeprefix("Bearer ")
        supplied_self_id = request.headers.get("X-Self-ID", "")
        return hmac.compare_digest(
            supplied_token,
            self._token,
        ) and hmac.compare_digest(supplied_self_id, str(self._self_id))

    def _handle_payload(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._log("warning", "Ignoring malformed OneBot JSON payload")
            return
        if not isinstance(payload, dict):
            return
        echo = payload.get("echo")
        if isinstance(echo, str) and echo in self._pending:
            self._resolve_action(echo, payload)
            return
        if not isinstance(payload.get("post_type"), str):
            return
        try:
            self._events.put_nowait(payload)
        except asyncio.QueueFull:
            self._log("error", "OneBot event queue is full; dropping event")

    def _resolve_action(self, echo: str, payload: dict[str, Any]) -> None:
        future, action = self._pending[echo]
        if future.done():
            return
        retcode = payload.get("retcode")
        if payload.get("status") == "ok" and retcode in {None, 0}:
            future.set_result(payload.get("data"))
            return
        message = payload.get("message") or payload.get("wording") or "unknown error"
        bounded = " ".join(str(message).split())[:500] or "unknown error"
        future.set_exception(
            OneBotActionError(
                action,
                int(retcode) if isinstance(retcode, int) else -1,
                bounded,
            )
        )

    def _ensure_event_workers(self) -> None:
        if not self._event_workers:
            self._event_workers = [
                asyncio.create_task(
                    self._consume_events(),
                    name=f"telefire-onebot-events-{index + 1}",
                )
                for index in range(self._event_concurrency)
            ]

    async def _consume_events(self) -> None:
        while True:
            payload = await self._events.get()
            try:
                if self._event_handler is not None:
                    await self._event_handler(payload)
            except Exception as exc:
                self._log(
                    "exception",
                    "OneBot event handling failed (%s): %s",
                    type(exc).__name__,
                    exc,
                )
            finally:
                self._events.task_done()

    def _fail_pending(self, error: Exception) -> None:
        for future, _ in self._pending.values():
            if not future.done():
                future.set_exception(error)

    def _log(self, level: str, message: str, *args: Any) -> None:
        operation = getattr(self._logger, level, None)
        if callable(operation):
            operation(message, *args)
