from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol
from uuid import uuid4

import aiohttp
from croniter import croniter
from telethon.errors import FloodWaitError

from telefire.ai import (
    AIStateRepository,
    HumanObservation,
    MemoryDreamResult,
    PromptBuilder,
    ReplyTarget,
    _memory_message_text,
    _message_datetime,
    _record_episode_labels,
    _telegram_memory_event_metadata,
    _telegram_memory_episode,
    _telegram_scope_id,
)
from telefire.ai_memory import (
    MemoryClient,
    MemoryClientError,
    MemoryEpisode,
    retain_episodes_once,
)


class DreamMessageSource(Protocol):
    async def fetch_window(
        self,
        chat_id: int,
        *,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> tuple[ReplyTarget, ...]: ...

    async def fetch_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> ReplyTarget | None: ...


class TelegramHistorySource:
    def __init__(self, client: Any):
        self._client = client

    async def fetch_window(
        self,
        chat_id: int,
        *,
        since: datetime,
        until: datetime,
        limit: int,
    ) -> tuple[ReplyTarget, ...]:
        messages: list[ReplyTarget] = []
        async for message in self._client.iter_messages(
            chat_id,
            offset_date=until,
            limit=limit,
        ):
            occurred_at = _message_datetime(message)
            if occurred_at < since:
                break
            if occurred_at <= until:
                messages.append(message)
        messages.reverse()
        return tuple(messages)

    async def fetch_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> ReplyTarget | None:
        message = await self._client.get_messages(chat_id, ids=message_id)
        if isinstance(message, list):
            return message[0] if message else None
        return message


@dataclass(frozen=True, slots=True)
class DreamSettings:
    lookback: timedelta = timedelta(hours=24)
    overlap: timedelta = timedelta(minutes=10)
    settlement_delay: timedelta = timedelta(seconds=30)
    max_messages: int = 500
    max_thread_messages: int = 100
    retain_batch_size: int = 10
    lease_seconds: float = 3_600
    retry_attempts: int = 3
    max_retry_delay: float = 30

    def __post_init__(self) -> None:
        if self.lookback <= timedelta(0):
            raise ValueError("Dream lookback must be positive")
        if self.overlap < timedelta(0) or self.settlement_delay < timedelta(0):
            raise ValueError("Dream overlap and settlement delay cannot be negative")
        if (
            self.max_messages < 1
            or self.max_thread_messages < 1
            or self.retain_batch_size < 1
        ):
            raise ValueError("Dream message limits must be positive")
        if self.lease_seconds <= 0:
            raise ValueError("Dream lease duration must be positive")
        if self.retry_attempts < 1 or self.max_retry_delay < 0:
            raise ValueError("Dream retry settings are invalid")

    @classmethod
    def from_env(cls) -> DreamSettings:
        return cls(
            lookback=timedelta(
                hours=float(
                    os.environ.get("TELEFIRE_MEMORY_DREAM_LOOKBACK_HOURS", "24")
                )
            ),
            overlap=timedelta(
                seconds=float(
                    os.environ.get("TELEFIRE_MEMORY_DREAM_OVERLAP_SECONDS", "600")
                )
            ),
            settlement_delay=timedelta(
                seconds=float(
                    os.environ.get(
                        "TELEFIRE_MEMORY_DREAM_SETTLEMENT_SECONDS",
                        "30",
                    )
                )
            ),
            max_messages=int(
                os.environ.get("TELEFIRE_MEMORY_DREAM_MAX_MESSAGES", "500")
            ),
            max_thread_messages=int(
                os.environ.get(
                    "TELEFIRE_MEMORY_DREAM_MAX_THREAD_MESSAGES",
                    "100",
                )
            ),
            retain_batch_size=int(
                os.environ.get("TELEFIRE_MEMORY_DREAM_RETAIN_BATCH_SIZE", "10")
            ),
            lease_seconds=float(
                os.environ.get("TELEFIRE_MEMORY_DREAM_LEASE_SECONDS", "3600")
            ),
            retry_attempts=int(
                os.environ.get("TELEFIRE_MEMORY_DREAM_RETRY_ATTEMPTS", "3")
            ),
            max_retry_delay=float(
                os.environ.get("TELEFIRE_MEMORY_DREAM_MAX_RETRY_DELAY", "30")
            ),
        )


@dataclass(frozen=True, slots=True)
class DreamSchedulerSettings:
    cron: str | None = "0 * * * *"
    concurrency: int = 2
    scope_batch_size: int = 20

    def __post_init__(self) -> None:
        if self.cron is not None:
            if len(self.cron.split()) != 5 or not croniter.is_valid(self.cron):
                raise ValueError("Dream schedule must be a valid five-field cron")
        if self.concurrency < 1 or self.scope_batch_size < 1:
            raise ValueError("Dream scheduler limits must be positive")

    @classmethod
    def from_env(cls) -> DreamSchedulerSettings:
        raw_cron = os.environ.get("TELEFIRE_MEMORY_DREAM_CRON", "0 * * * *").strip()
        cron = None if raw_cron.casefold() in {"", "off", "disabled"} else raw_cron
        return cls(
            cron=cron,
            concurrency=int(os.environ.get("TELEFIRE_MEMORY_DREAM_CONCURRENCY", "2")),
            scope_batch_size=int(
                os.environ.get("TELEFIRE_MEMORY_DREAM_SCOPE_BATCH_SIZE", "20")
            ),
        )


@dataclass(frozen=True, slots=True)
class DreamScheduleResult:
    scopes_seen: int
    scopes_succeeded: int
    scopes_failed: int
    scopes_busy: int


class DreamCycleBusyError(RuntimeError):
    pass


class DreamWindowLimitError(RuntimeError):
    pass


class DreamThreadLimitError(RuntimeError):
    pass


class TelegramDreamScanner:
    def __init__(
        self,
        *,
        source: DreamMessageSource,
        store: AIStateRepository,
        memory: MemoryClient,
        prompt_builder: PromptBuilder,
        settings: DreamSettings = DreamSettings(),
        clock: Any = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        logger: Any | None = None,
    ):
        self._source = source
        self._store = store
        self._memory = memory
        self._prompt_builder = prompt_builder
        self._settings = settings
        self._clock = clock
        self._sleep = sleep
        self._logger = logger
        self._locks: dict[int, asyncio.Lock] = {}
        self._lease_owner = uuid4().hex

    async def run_scope(self, chat_id: int) -> MemoryDreamResult:
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            scope_id = _telegram_scope_id(chat_id)
            acquired_at = self._clock()
            acquired = await self._store.acquire_memory_dream_lease(
                scope_id,
                owner=self._lease_owner,
                acquired_at=acquired_at,
                lease_seconds=self._settings.lease_seconds,
            )
            if not acquired:
                raise DreamCycleBusyError(
                    "Another Dream Cycle is already running for this chat"
                )
            try:
                work = asyncio.create_task(self._run_scope(chat_id))
                heartbeat = asyncio.create_task(self._renew_lease(scope_id))
                done, _ = await asyncio.wait(
                    {work, heartbeat},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if heartbeat in done:
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                    await heartbeat
                    raise AssertionError("Dream lease heartbeat stopped unexpectedly")
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                return await work
            finally:
                await self._store.release_memory_dream_lease(
                    scope_id,
                    owner=self._lease_owner,
                )

    async def _renew_lease(self, scope_id: str) -> None:
        interval = max(0.05, self._settings.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            renewed_at = self._clock()
            if not await self._store.renew_memory_dream_lease(
                scope_id,
                owner=self._lease_owner,
                renewed_at=renewed_at,
                lease_seconds=self._settings.lease_seconds,
            ):
                raise DreamCycleBusyError("Dream Cycle lease was lost")

    async def _run_scope(self, chat_id: int) -> MemoryDreamResult:
        scope_id = _telegram_scope_id(chat_id)
        if not await self._store.is_memory_enabled(scope_id):
            raise ValueError("Automatic memory is disabled for this chat")
        attempted_at = self._clock()
        await self._store.record_memory_dream_attempt(scope_id, attempted_at)
        state = await self._store.get_memory_dream_state(scope_id)
        until = (
            datetime.fromtimestamp(attempted_at, UTC) - self._settings.settlement_delay
        )
        since = (
            datetime.fromtimestamp(state.scanned_until_at, UTC) - self._settings.overlap
            if state.scanned_until_at is not None
            else until - self._settings.lookback
        )
        try:
            messages = await self._retry_telegram(
                lambda: self._source.fetch_window(
                    chat_id,
                    since=since,
                    until=until,
                    limit=self._settings.max_messages + 1,
                )
            )
            if len(messages) > self._settings.max_messages:
                raise DreamWindowLimitError(
                    "Dream window exceeds TELEFIRE_MEMORY_DREAM_MAX_MESSAGES; "
                    "increase the bound or reduce the lookback"
                )
            result, cursor = await self._retain_threads(chat_id, messages)
            await self._store.record_memory_dream_success(
                scope_id,
                cursor_message_id=cursor,
                scanned_until_at=until.timestamp(),
                succeeded_at=self._clock(),
            )
            return result
        except Exception as exc:
            await self._store.record_memory_dream_failure(
                scope_id,
                failed_at=self._clock(),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    async def _retain_threads(
        self,
        chat_id: int,
        messages: tuple[ReplyTarget, ...],
    ) -> tuple[MemoryDreamResult, int | None]:
        window_ids = {message.id for message in messages}
        known = {message.id: message for message in messages}
        groups: dict[int, dict[int, ReplyTarget]] = {}
        for message in messages:
            chain = await self._load_chain(chat_id, message, known)
            if not chain:
                continue
            root_id = chain[0].id
            group = groups.setdefault(root_id, {})
            for item in chain:
                group[item.id] = item

        episodes: list[MemoryEpisode] = []
        retained_window_ids: set[int] = set()
        for root_id, grouped in sorted(groups.items()):
            await self._hydrate_previous_events(chat_id, root_id, grouped, known)
            ordered = sorted(
                grouped.values(),
                key=lambda message: (_message_datetime(message), message.id),
            )
            if len(ordered) > self._settings.max_thread_messages:
                raise DreamThreadLimitError(
                    f"Thread {root_id} exceeds the configured Dream thread bound"
                )
            observations = await self._observations(chat_id, ordered)
            if not observations:
                continue
            episode = _telegram_memory_episode(
                chat_id,
                observations,
                root_message_id=root_id,
            )
            episodes.append(episode)
            await _record_episode_labels(self._store, episode)
            retained_window_ids.update(
                observation.message_id
                for observation in observations
                if observation.message_id in window_ids
            )

        documents_created = 0
        documents_unchanged = 0
        for start in range(0, len(episodes), self._settings.retain_batch_size):
            batch = tuple(episodes[start : start + self._settings.retain_batch_size])
            created = await self._retry_memory(
                lambda: retain_episodes_once(
                    self._memory,
                    self._store,
                    batch,
                )
            )
            documents_created += sum(created)
            documents_unchanged += len(created) - sum(created)
        return (
            MemoryDreamResult(
                messages_seen=len(messages),
                messages_retained=len(retained_window_ids),
                documents_created=documents_created,
                documents_unchanged=documents_unchanged,
            ),
            max(retained_window_ids, default=None),
        )

    async def _hydrate_previous_events(
        self,
        chat_id: int,
        root_id: int,
        grouped: dict[int, ReplyTarget],
        known: dict[int, ReplyTarget],
    ) -> None:
        scope_id = _telegram_scope_id(chat_id)
        document_id = f"telegram:thread:{chat_id}:{root_id}"
        receipt = await self._store.get_memory_document_receipt(scope_id, document_id)
        if receipt is None:
            return
        prefix = f"telegram:message:{chat_id}:"
        previous_ids: list[int] = []
        for source_id, _ in receipt.event_versions:
            if not source_id.startswith(prefix):
                continue
            try:
                message_id = int(source_id.removeprefix(prefix))
            except ValueError:
                continue
            if message_id > 0 and message_id not in grouped:
                previous_ids.append(message_id)
        if len(grouped) + len(previous_ids) > self._settings.max_thread_messages:
            raise DreamThreadLimitError(
                f"Thread {root_id} exceeds the configured Dream thread bound"
            )
        for message_id in previous_ids:
            message = known.get(message_id)
            if message is None:
                message = await self._retry_telegram(
                    lambda message_id=message_id: self._source.fetch_message(
                        chat_id,
                        message_id,
                    )
                )
                if message is not None:
                    known[message.id] = message
            if message is not None:
                grouped[message.id] = message

    async def _load_chain(
        self,
        chat_id: int,
        message: ReplyTarget,
        known: dict[int, ReplyTarget],
    ) -> tuple[ReplyTarget, ...]:
        newest_first: list[ReplyTarget] = []
        seen: set[int] = set()
        current: ReplyTarget | None = message
        while current is not None:
            if current.id in seen:
                break
            if len(newest_first) >= self._settings.max_thread_messages:
                raise DreamThreadLimitError(
                    f"Reply chain at message {message.id} exceeds the configured bound"
                )
            seen.add(current.id)
            newest_first.append(current)
            parent_id = current.reply_to_msg_id
            if parent_id is None:
                break
            parent = known.get(parent_id)
            if parent is None:
                parent = await self._retry_telegram(
                    lambda: self._source.fetch_message(chat_id, parent_id)
                )
                if parent is not None:
                    known[parent.id] = parent
            current = parent
        return tuple(reversed(newest_first))

    async def _observations(
        self,
        chat_id: int,
        messages: list[ReplyTarget],
    ) -> tuple[HumanObservation, ...]:
        observations: list[HumanObservation] = []
        for message in messages:
            if message.sender_id is None:
                continue
            if await self._store.is_memory_excluded_message(chat_id, message.id):
                continue
            if await self._store.get_answer(chat_id, message.id) is not None:
                continue
            text = _memory_message_text(message.raw_text or "")
            attachment = await self._prompt_builder.describe_attachment(message)
            observation_text = self._prompt_builder.build_observation_text(
                text,
                attachment,
            )
            if not observation_text:
                continue
            identity = await self._prompt_builder.resolve_identity(message)
            if not identity.is_human:
                continue
            observations.append(
                HumanObservation(
                    message_id=message.id,
                    sender_id=message.sender_id,
                    text=observation_text,
                    occurred_at=_message_datetime(message),
                    mentioned_at=_message_datetime(message),
                    identity=identity,
                    reply_to_message_id=message.reply_to_msg_id,
                    mentioned_users=await self._prompt_builder.resolve_mentions(
                        message
                    ),
                    metadata=_telegram_memory_event_metadata(message),
                )
            )
        return tuple(observations)

    async def _retry_telegram(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        for attempt in range(1, self._settings.retry_attempts + 1):
            try:
                return await operation()
            except FloodWaitError as exc:
                if attempt >= self._settings.retry_attempts:
                    raise
                delay = min(
                    max(0.0, float(exc.seconds)),
                    self._settings.max_retry_delay,
                )
                if self._logger is not None:
                    self._logger.warning(
                        "Dream Telegram request rate limited; retrying in %.1fs",
                        delay,
                    )
                await self._sleep(delay)
        raise AssertionError("unreachable")

    async def _retry_memory(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        for attempt in range(1, self._settings.retry_attempts + 1):
            try:
                return await operation()
            except MemoryClientError as exc:
                if exc.status not in {429, 502, 503, 504}:
                    raise
                error = exc
                retry_after = exc.retry_after
            except (TimeoutError, aiohttp.ClientConnectionError) as exc:
                error = exc
                retry_after = None
            if attempt >= self._settings.retry_attempts:
                raise error
            delay = min(
                retry_after if retry_after is not None else 2 ** (attempt - 1),
                self._settings.max_retry_delay,
            )
            if self._logger is not None:
                self._logger.warning(
                    "Dream memory retain backpressured; retrying in %.1fs",
                    delay,
                )
            await self._sleep(delay)
        raise AssertionError("unreachable")


class DreamScheduler:
    def __init__(
        self,
        *,
        scanner: TelegramDreamScanner,
        store: AIStateRepository,
        settings: DreamSchedulerSettings = DreamSchedulerSettings(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        logger: Any | None = None,
    ):
        self._scanner = scanner
        self._store = store
        self._settings = settings
        self._clock = clock
        self._sleep = sleep
        self._logger = logger
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._settings.cron is None or self._task is not None:
            return
        self._task = asyncio.create_task(
            self._run_forever(),
            name="telefire-memory-dream-scheduler",
        )

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_once(self) -> DreamScheduleResult:
        scopes = await self._store.list_memory_enabled_scopes()
        semaphore = asyncio.Semaphore(self._settings.concurrency)
        succeeded = 0
        failed = 0
        busy = 0

        async def run(scope_id: str) -> str:
            prefix = "telegram:chat:"
            if not scope_id.startswith(prefix):
                return "failed"
            try:
                chat_id = int(scope_id[len(prefix) :])
            except ValueError:
                return "failed"
            async with semaphore:
                try:
                    await self._scanner.run_scope(chat_id)
                except DreamCycleBusyError:
                    return "busy"
                except Exception as exc:
                    if self._logger is not None:
                        self._logger.warning(
                            "Scheduled Dream Cycle failed (scope_id=%s, error=%s): %s",
                            scope_id,
                            type(exc).__name__,
                            exc,
                        )
                    return "failed"
            return "succeeded"

        for start in range(0, len(scopes), self._settings.scope_batch_size):
            results = await asyncio.gather(
                *(
                    run(scope_id)
                    for scope_id in scopes[
                        start : start + self._settings.scope_batch_size
                    ]
                )
            )
            succeeded += results.count("succeeded")
            failed += results.count("failed")
            busy += results.count("busy")

        result = DreamScheduleResult(
            scopes_seen=len(scopes),
            scopes_succeeded=succeeded,
            scopes_failed=failed,
            scopes_busy=busy,
        )
        if self._logger is not None:
            self._logger.info(
                "Scheduled Dream Cycle complete "
                "(scopes=%s, succeeded=%s, failed=%s, busy=%s)",
                result.scopes_seen,
                result.scopes_succeeded,
                result.scopes_failed,
                result.scopes_busy,
            )
        return result

    async def _run_forever(self) -> None:
        assert self._settings.cron is not None
        while True:
            now = self._clock()
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            next_run = croniter(self._settings.cron, now).get_next(datetime)
            await self._sleep(max(0.0, (next_run - now).total_seconds()))
            try:
                await self.run_once()
            except Exception as exc:
                if self._logger is not None:
                    self._logger.exception(
                        "Scheduled Dream Cycle orchestration failed (error=%s): %s",
                        type(exc).__name__,
                        exc,
                    )
