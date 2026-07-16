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
    MAX_MEMORY_BACKFILL_MESSAGES,
    MemoryBackfillRequest,
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
from telefire.ai_attachments import attachment_metadata_only, message_has_attachment
from telefire.ai_memory import (
    MemoryClient,
    MemoryClientError,
    MemoryDocumentReceipt,
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

    async def fetch_after(
        self,
        chat_id: int,
        *,
        after_message_id: int,
        until: datetime,
        limit: int,
    ) -> tuple[ReplyTarget, ...]: ...


class TelegramHistorySource:
    def __init__(self, client: Any):
        self._client = client

    async def fetch_recent(
        self,
        trigger: ReplyTarget,
        *,
        limit: int,
    ) -> tuple[ReplyTarget, ...]:
        if trigger.chat_id is None:
            return ()
        kwargs: dict[str, Any] = {
            "limit": limit,
            "max_id": trigger.id,
        }
        reply_header = getattr(trigger, "reply_to", None)
        if bool(getattr(reply_header, "forum_topic", False)):
            topic_id = getattr(reply_header, "reply_to_top_id", None) or getattr(
                reply_header,
                "reply_to_msg_id",
                None,
            )
            if isinstance(topic_id, int) and topic_id > 0:
                kwargs["reply_to"] = topic_id
        messages = [
            message
            async for message in self._client.iter_messages(
                trigger.chat_id,
                **kwargs,
            )
        ]
        messages.reverse()
        return tuple(messages)

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

    async def fetch_after(
        self,
        chat_id: int,
        *,
        after_message_id: int,
        until: datetime,
        limit: int,
    ) -> tuple[ReplyTarget, ...]:
        messages: list[ReplyTarget] = []
        async for message in self._client.iter_messages(
            chat_id,
            min_id=after_message_id,
            reverse=True,
            limit=limit,
        ):
            if _message_datetime(message) > until:
                break
            messages.append(message)
        return tuple(messages)


@dataclass(frozen=True, slots=True)
class DreamSettings:
    lookback: timedelta = timedelta(hours=24)
    overlap: timedelta = timedelta(minutes=10)
    settlement_delay: timedelta = timedelta(seconds=30)
    max_messages: int = 500
    max_thread_messages: int = 100
    session_idle_gap: timedelta = timedelta(minutes=15)
    session_max_span: timedelta = timedelta(hours=1)
    session_max_events: int = 30
    session_max_chars: int = 4_000
    retain_concurrency: int = 4
    preprocess_concurrency: int = 12
    cycle_budget_seconds: float = 50
    scope_timeout_seconds: float = 300
    lease_seconds: float = 3_600
    retry_attempts: int = 3
    max_retry_delay: float = 30

    def __post_init__(self) -> None:
        if self.lookback <= timedelta(0):
            raise ValueError("Dream lookback must be positive")
        if self.overlap < timedelta(0) or self.settlement_delay < timedelta(0):
            raise ValueError("Dream overlap and settlement delay cannot be negative")
        if self.session_idle_gap <= timedelta(0) or self.session_max_span <= timedelta(
            0
        ):
            raise ValueError("Dream session time limits must be positive")
        if (
            self.max_messages < 1
            or self.max_thread_messages < 1
            or self.session_max_events < 1
            or self.session_max_chars < 1
            or self.retain_concurrency < 1
            or self.preprocess_concurrency < 1
        ):
            raise ValueError("Dream message limits must be positive")
        if self.cycle_budget_seconds <= 0:
            raise ValueError("Dream cycle budget must be positive")
        if self.scope_timeout_seconds <= 0:
            raise ValueError("Dream scope timeout must be positive")
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
            session_idle_gap=timedelta(
                seconds=float(
                    os.environ.get(
                        "TELEFIRE_MEMORY_DREAM_SESSION_IDLE_SECONDS",
                        "900",
                    )
                )
            ),
            session_max_span=timedelta(
                seconds=float(
                    os.environ.get(
                        "TELEFIRE_MEMORY_DREAM_SESSION_MAX_SPAN_SECONDS",
                        "3600",
                    )
                )
            ),
            session_max_events=int(
                os.environ.get("TELEFIRE_MEMORY_DREAM_SESSION_MAX_EVENTS", "30")
            ),
            session_max_chars=int(
                os.environ.get("TELEFIRE_MEMORY_DREAM_SESSION_MAX_CHARS", "4000")
            ),
            retain_concurrency=int(
                os.environ.get("TELEFIRE_MEMORY_DREAM_RETAIN_CONCURRENCY", "4")
            ),
            preprocess_concurrency=int(
                os.environ.get("TELEFIRE_MEMORY_DREAM_PREPROCESS_CONCURRENCY", "12")
            ),
            cycle_budget_seconds=float(
                os.environ.get("TELEFIRE_MEMORY_DREAM_CYCLE_BUDGET_SECONDS", "50")
            ),
            scope_timeout_seconds=float(
                os.environ.get("TELEFIRE_MEMORY_DREAM_SCOPE_TIMEOUT_SECONDS", "300")
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


@dataclass(frozen=True, slots=True)
class ContinuousMemoryResult:
    messages_seen: int
    messages_retained: int
    documents_created: int
    documents_unchanged: int
    caught_up: bool


@dataclass(frozen=True, slots=True)
class ContinuousMemorySchedulerSettings:
    poll_interval_seconds: float = 10
    concurrency: int = 2
    scope_batch_size: int = 20

    def __post_init__(self) -> None:
        if (
            self.poll_interval_seconds <= 0
            or self.concurrency < 1
            or self.scope_batch_size < 1
        ):
            raise ValueError("Continuous memory scheduler limits must be positive")

    @classmethod
    def from_env(cls) -> ContinuousMemorySchedulerSettings:
        return cls(
            poll_interval_seconds=float(
                os.environ.get("TELEFIRE_MEMORY_CONTINUOUS_POLL_SECONDS", "10")
            ),
            concurrency=int(
                os.environ.get("TELEFIRE_MEMORY_CONTINUOUS_CONCURRENCY", "2")
            ),
            scope_batch_size=int(
                os.environ.get("TELEFIRE_MEMORY_CONTINUOUS_SCOPE_BATCH_SIZE", "20")
            ),
        )


@dataclass(frozen=True, slots=True)
class ContinuousMemoryScheduleResult:
    scopes_seen: int
    scopes_succeeded: int
    scopes_failed: int
    scopes_busy: int
    scopes_pending: int
    messages_seen: int
    messages_retained: int


@dataclass(frozen=True, slots=True)
class _DreamDocument:
    episode: MemoryEpisode
    window_message_ids: frozenset[int]


class DreamCycleBusyError(RuntimeError):
    pass


class DreamCycleTimeoutError(TimeoutError):
    pass


class DreamBackfillLimitError(RuntimeError):
    pass


class DreamThreadLimitError(RuntimeError):
    pass


def _dream_session_document_id(
    chat_id: int,
    root_message_id: int,
    started_at: datetime,
) -> str:
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    stamp = started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"telegram:dream-session:{chat_id}:{stamp}:{root_message_id}"


def _channel_album_document_id(chat_id: int, message: ReplyTarget) -> str | None:
    if not bool(getattr(message, "post", False)):
        return None
    grouped_id = getattr(message, "grouped_id", None)
    if isinstance(grouped_id, int):
        return f"telegram:channel-album:{chat_id}:{grouped_id}"
    return None


def _session_accepts(
    current: tuple[HumanObservation, ...],
    candidate: tuple[HumanObservation, ...],
    settings: DreamSettings,
) -> bool:
    if not current:
        return True
    current_start = min(observation.occurred_at for observation in current)
    current_end = max(observation.occurred_at for observation in current)
    candidate_start = min(observation.occurred_at for observation in candidate)
    candidate_end = max(observation.occurred_at for observation in candidate)
    if candidate_start - current_end > settings.session_idle_gap:
        return False
    if (
        max(current_end, candidate_end)
        - min(
            current_start,
            candidate_start,
        )
        > settings.session_max_span
    ):
        return False
    if len(current) + len(candidate) > settings.session_max_events:
        return False
    return sum(len(observation.text) for observation in (*current, *candidate)) <= (
        settings.session_max_chars
    )


def _completed_message_prefix(
    messages: tuple[ReplyTarget, ...],
    documents: tuple[_DreamDocument, ...],
    completed_document_ids: set[str],
) -> tuple[ReplyTarget, ...]:
    owner_by_message_id = {
        message_id: document.episode.document_id
        for document in documents
        for message_id in document.window_message_ids
    }
    completed: list[ReplyTarget] = []
    for message in messages:
        document_id = owner_by_message_id.get(message.id)
        if document_id is not None and document_id not in completed_document_ids:
            break
        completed.append(message)
    return tuple(completed)


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
        monotonic: Callable[[], float] = time.monotonic,
        logger: Any | None = None,
    ):
        self._source = source
        self._store = store
        self._memory = memory
        self._prompt_builder = prompt_builder
        self._settings = settings
        self._clock = clock
        self._sleep = sleep
        self._monotonic = monotonic
        self._logger = logger
        self._locks: dict[int, asyncio.Lock] = {}
        self._lease_owner = uuid4().hex

    async def run_scope(self, chat_id: int) -> MemoryDreamResult:
        try:
            return await self._run_bounded(
                lambda: self._run_exclusive(
                    chat_id,
                    lambda: self._run_scope(chat_id),
                ),
                timeout_seconds=self._settings.scope_timeout_seconds,
            )
        except DreamCycleTimeoutError as exc:
            await self._store.record_memory_dream_failure(
                _telegram_scope_id(chat_id),
                failed_at=self._clock(),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    async def _run_bounded(
        self,
        operation: Callable[[], Awaitable[MemoryDreamResult]],
        *,
        timeout_seconds: float,
    ) -> MemoryDreamResult:
        work = asyncio.create_task(operation())
        timeout = asyncio.create_task(asyncio.sleep(timeout_seconds))
        tasks: set[asyncio.Task[Any]] = {work, timeout}
        try:
            done, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if work in done:
                return await work
            raise DreamCycleTimeoutError(
                f"Dream Cycle exceeded its {timeout_seconds:g}-second scope timeout"
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_backfill(
        self,
        chat_id: int,
        request: MemoryBackfillRequest,
    ) -> MemoryDreamResult:
        return await self._run_exclusive(
            chat_id,
            lambda: self._run_backfill(chat_id, request),
        )

    async def run_continuous_scope(self, chat_id: int) -> ContinuousMemoryResult:
        return await self._run_exclusive(
            chat_id,
            lambda: self._run_continuous_scope(chat_id),
        )

    async def _run_exclusive(
        self,
        chat_id: int,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
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
                    "Another Dream operation is already running for this chat"
                )
            work = asyncio.create_task(operation())
            heartbeat = asyncio.create_task(self._renew_lease(scope_id))
            tasks: set[asyncio.Task[Any]] = {work, heartbeat}
            try:
                done, _ = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if heartbeat in done:
                    await heartbeat
                    raise AssertionError("Dream lease heartbeat stopped unexpectedly")
                return await work
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await self._store.release_memory_dream_lease(
                    scope_id,
                    owner=self._lease_owner,
                )

    async def _run_continuous_scope(
        self,
        chat_id: int,
    ) -> ContinuousMemoryResult:
        scope_id = _telegram_scope_id(chat_id)
        scope = await self._store.get_memory_scope_state(scope_id)
        if not scope.continuous_enabled:
            raise ValueError("Continuous memory is disabled for this chat")
        if scope.continuous_cursor_message_id is None:
            raise ValueError("Continuous memory cursor is not initialized")
        attempted_at = self._clock()
        await self._store.record_continuous_memory_attempt(scope_id, attempted_at)
        until = (
            datetime.fromtimestamp(attempted_at, UTC)
            - self._settings.settlement_delay
        )

        async def checkpoint(
            cursor_message_id: int | None,
            _scanned_until_at: float,
        ) -> None:
            await self._store.record_continuous_memory_success(
                scope_id,
                cursor_message_id=cursor_message_id,
                succeeded_at=self._clock(),
            )

        try:
            messages = await self._retry_telegram(
                lambda: self._source.fetch_after(
                    chat_id,
                    after_message_id=scope.continuous_cursor_message_id,
                    until=until,
                    limit=self._settings.max_messages,
                )
            )
            result, _, complete = await self._retain_threads(
                chat_id,
                messages,
                checkpoint=checkpoint,
            )
            if not messages:
                await self._store.record_continuous_memory_success(
                    scope_id,
                    cursor_message_id=None,
                    succeeded_at=self._clock(),
                )
            return ContinuousMemoryResult(
                messages_seen=result.messages_seen,
                messages_retained=result.messages_retained,
                documents_created=result.documents_created,
                documents_unchanged=result.documents_unchanged,
                caught_up=complete and len(messages) < self._settings.max_messages,
            )
        except Exception as exc:
            await self._store.record_continuous_memory_failure(
                scope_id,
                failed_at=self._clock(),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

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

    async def _run_backfill(
        self,
        chat_id: int,
        request: MemoryBackfillRequest,
    ) -> MemoryDreamResult:
        until = (
            datetime.fromtimestamp(self._clock(), UTC) - self._settings.settlement_delay
        )
        if request.mode == "days":
            since = until - timedelta(days=request.value)
            limit = MAX_MEMORY_BACKFILL_MESSAGES + 1
        else:
            since = datetime.min.replace(tzinfo=UTC)
            limit = request.value
        messages = await self._retry_telegram(
            lambda: self._source.fetch_window(
                chat_id,
                since=since,
                until=until,
                limit=limit,
            )
        )
        if request.mode == "days" and len(messages) > MAX_MEMORY_BACKFILL_MESSAGES:
            raise DreamBackfillLimitError(
                "Memory backfill exceeds the 5,000-message limit; use message mode "
                "or request a shorter day range"
            )
        result, _, _ = await self._retain_threads(chat_id, messages)
        return result

    async def _run_scope(self, chat_id: int) -> MemoryDreamResult:
        deadline = self._monotonic() + self._settings.cycle_budget_seconds
        scope_id = _telegram_scope_id(chat_id)
        scope = await self._store.get_memory_scope_state(scope_id)
        if scope.continuous_enabled:
            raise ValueError("Continuous memory overrides Dream for this chat")
        if not scope.dream_enabled:
            raise ValueError("Dream is disabled for this chat")
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
        checkpoint_scanned_at = state.scanned_until_at

        async def checkpoint(
            cursor_message_id: int | None,
            scanned_until_at: float,
        ) -> None:
            nonlocal checkpoint_scanned_at
            if (
                checkpoint_scanned_at is not None
                and scanned_until_at <= checkpoint_scanned_at
            ):
                return
            succeeded_at = self._clock()
            await self._store.record_memory_dream_success(
                scope_id,
                cursor_message_id=cursor_message_id,
                scanned_until_at=scanned_until_at,
                succeeded_at=succeeded_at,
            )
            checkpoint_scanned_at = scanned_until_at

        try:
            messages = await self._retry_telegram(
                lambda: self._source.fetch_window(
                    chat_id,
                    since=since,
                    until=until,
                    limit=self._settings.max_messages,
                )
            )
            result, _, _ = await self._retain_threads(
                chat_id,
                messages,
                deadline=deadline,
                checkpoint=checkpoint,
            )
            await self._store.record_memory_dream_success(
                scope_id,
                cursor_message_id=messages[-1].id if messages else None,
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
        *,
        deadline: float | None = None,
        checkpoint: Callable[[int | None, float], Awaitable[None]] | None = None,
    ) -> tuple[MemoryDreamResult, int | None, bool]:
        started_at = self._monotonic()
        documents = await self._prepare_documents(chat_id, messages)
        prepared_at = self._monotonic()
        documents_created = 0
        documents_unchanged = 0
        retained_window_ids: set[int] = set()
        completed_document_ids: set[str] = set()
        complete = True
        cursor: int | None = None
        for start in range(0, len(documents), self._settings.retain_concurrency):
            batch = documents[start : start + self._settings.retain_concurrency]
            results = await asyncio.gather(
                *(
                    self._retry_memory(
                        lambda document=document: retain_episodes_once(
                            self._memory,
                            self._store,
                            (document.episode,),
                        )
                    )
                    for document in batch
                ),
                return_exceptions=True,
            )
            first_error: BaseException | None = None
            for document, created in zip(batch, results, strict=True):
                if isinstance(created, BaseException):
                    if first_error is None:
                        first_error = created
                    continue
                documents_created += int(created[0])
                documents_unchanged += int(not created[0])
                retained_window_ids.update(document.window_message_ids)
                completed_document_ids.add(document.episode.document_id)
            if checkpoint is not None:
                prefix = _completed_message_prefix(
                    messages,
                    documents,
                    completed_document_ids,
                )
                if prefix:
                    cursor = prefix[-1].id
                    await checkpoint(
                        cursor,
                        _message_datetime(prefix[-1]).timestamp(),
                    )
            if first_error is not None:
                raise first_error
            if (
                deadline is not None
                and start + len(batch) < len(documents)
                and self._monotonic() >= deadline
            ):
                complete = False
                break
        if complete and messages:
            cursor = messages[-1].id
            if not documents and checkpoint is not None:
                await checkpoint(
                    cursor,
                    _message_datetime(messages[-1]).timestamp(),
                )
        result = MemoryDreamResult(
            messages_seen=len(messages),
            messages_retained=len(retained_window_ids),
            documents_created=documents_created,
            documents_unchanged=documents_unchanged,
        )
        if self._logger is not None:
            finished_at = self._monotonic()
            self._logger.info(
                "Dream retention complete "
                "(chat_id=%s, messages=%s, documents=%s, created=%s, "
                "unchanged=%s, complete=%s, prepare_seconds=%.3f, "
                "retain_seconds=%.3f)",
                chat_id,
                result.messages_seen,
                len(documents),
                result.documents_created,
                result.documents_unchanged,
                complete,
                prepared_at - started_at,
                finished_at - prepared_at,
            )
        return result, cursor, complete

    async def _prepare_documents(
        self,
        chat_id: int,
        messages: tuple[ReplyTarget, ...],
    ) -> tuple[_DreamDocument, ...]:
        window_ids = {message.id for message in messages}
        window_positions = {message.id: index for index, message in enumerate(messages)}
        known = {message.id: message for message in messages}
        root_groups: dict[int, dict[int, ReplyTarget]] = {}
        fixed_document_ids: dict[int, str] = {}
        album_roots: dict[int, int] = {}
        for message in messages:
            chain = await self._load_chain(chat_id, message, known)
            if not chain:
                continue
            channel_document_id = (
                _channel_album_document_id(chat_id, message)
                if len(chain) == 1
                else None
            )
            grouped_id = getattr(message, "grouped_id", None)
            if channel_document_id is not None and isinstance(grouped_id, int):
                root_id = album_roots.setdefault(grouped_id, message.id)
            else:
                root_id = chain[0].id
            if channel_document_id is not None:
                fixed_document_ids[root_id] = channel_document_id
            group = root_groups.setdefault(root_id, {})
            for item in chain:
                group[item.id] = item

        scope_id = _telegram_scope_id(chat_id)
        source_ids = tuple(
            f"telegram:message:{chat_id}:{message_id}"
            for grouped in root_groups.values()
            for message_id in grouped
        )
        source_documents = await self._store.find_memory_document_ids_for_sources(
            scope_id,
            source_ids,
        )

        assigned_documents: dict[int, str] = {}
        for root_id, grouped in root_groups.items():
            root_source_id = f"telegram:message:{chat_id}:{root_id}"
            document_id = source_documents.get(root_source_id)
            if document_id is None:
                for message_id in grouped:
                    source_id = f"telegram:message:{chat_id}:{message_id}"
                    document_id = source_documents.get(source_id)
                    if document_id is not None:
                        break
            if document_id is None:
                document_id = fixed_document_ids.get(root_id)
            if document_id is not None:
                assigned_documents[root_id] = document_id

        assigned_document_ids = tuple(dict.fromkeys(assigned_documents.values()))
        receipts = await self._store.get_memory_document_receipts(
            scope_id,
            assigned_document_ids,
        )

        document_groups: dict[str, dict[int, ReplyTarget]] = {}
        for root_id, document_id in assigned_documents.items():
            grouped = root_groups[root_id]
            if len(grouped) > self._settings.max_thread_messages:
                raise DreamThreadLimitError(
                    f"Thread {root_id} exceeds the configured Dream thread bound"
                )
            document_group = document_groups.setdefault(document_id, {})
            document_group.update(grouped)

        unassigned_root_ids = tuple(
            root_id for root_id in root_groups if root_id not in assigned_documents
        )
        open_document_id: str | None = None
        candidate_document_id: str | None = None
        loaded_candidate_only = False
        if unassigned_root_ids:
            latest = await self._store.get_latest_memory_document_receipt(
                scope_id,
                f"telegram:dream-session:{chat_id}:",
            )
            if latest is not None:
                open_document_id, receipt = latest
                candidate_document_id = open_document_id
                receipts[open_document_id] = receipt
                loaded_candidate_only = open_document_id not in document_groups
                document_groups.setdefault(open_document_id, {})

        await self._hydrate_previous_events(
            chat_id,
            document_groups,
            receipts,
            known,
        )
        observation_by_id = await self._build_observations(
            chat_id,
            tuple(
                sorted(
                    {
                        message.id: message
                        for group in (
                            *document_groups.values(),
                            *(root_groups[root_id] for root_id in unassigned_root_ids),
                        )
                        for message in group.values()
                    }.values(),
                    key=lambda message: (_message_datetime(message), message.id),
                )
            ),
        )

        open_observations = (
            tuple(
                observation_by_id[message_id]
                for message_id in document_groups[open_document_id]
                if message_id in observation_by_id
            )
            if open_document_id is not None
            else ()
        )
        candidate_appended = False
        unassigned_roots: list[
            tuple[int, dict[int, ReplyTarget], tuple[HumanObservation, ...]]
        ] = []
        for root_id in unassigned_root_ids:
            grouped = root_groups[root_id]
            if len(grouped) > self._settings.max_thread_messages:
                raise DreamThreadLimitError(
                    f"Thread {root_id} exceeds the configured Dream thread bound"
                )
            observations = tuple(
                sorted(
                    (
                        observation_by_id[message_id]
                        for message_id in grouped
                        if message_id in observation_by_id
                    ),
                    key=lambda observation: (
                        observation.occurred_at,
                        observation.message_id,
                    ),
                )
            )
            if observations:
                unassigned_roots.append((root_id, grouped, observations))
        unassigned_roots.sort(key=lambda item: (item[2][0].occurred_at, item[0]))

        for root_id, grouped, observations in unassigned_roots:
            if open_document_id is not None and _session_accepts(
                open_observations,
                observations,
                self._settings,
            ):
                document_id = open_document_id
                candidate_appended = (
                    candidate_appended or document_id == candidate_document_id
                )
            else:
                document_id = _dream_session_document_id(
                    chat_id,
                    root_id,
                    observations[0].occurred_at,
                )
                open_document_id = document_id
                open_observations = ()
            document_groups.setdefault(document_id, {}).update(grouped)
            open_observations = tuple(
                {
                    observation.message_id: observation
                    for observation in (*open_observations, *observations)
                }.values()
            )

        if loaded_candidate_only and not candidate_appended:
            assert candidate_document_id is not None
            document_groups.pop(candidate_document_id, None)

        documents: list[_DreamDocument] = []
        for document_id, grouped in document_groups.items():
            ordered = sorted(
                grouped.values(),
                key=lambda message: (_message_datetime(message), message.id),
            )
            if (
                document_id.startswith("telegram:thread:")
                and len(ordered) > self._settings.max_thread_messages
            ):
                raise DreamThreadLimitError(
                    f"Document {document_id} exceeds the Dream thread bound"
                )
            observations = tuple(
                observation_by_id[message.id]
                for message in ordered
                if message.id in observation_by_id
            )
            if not observations:
                continue
            episode = _telegram_memory_episode(
                chat_id,
                observations,
                document_id=document_id,
            )
            await _record_episode_labels(self._store, episode)
            window_message_ids = frozenset(
                observation.message_id
                for observation in observations
                if observation.message_id in window_ids
            )
            if not window_message_ids:
                continue
            documents.append(
                _DreamDocument(
                    episode=episode,
                    window_message_ids=window_message_ids,
                )
            )
        documents.sort(
            key=lambda document: min(
                (
                    window_positions[message_id]
                    for message_id in document.window_message_ids
                ),
                default=len(messages),
            )
        )
        return tuple(documents)

    async def _hydrate_previous_events(
        self,
        chat_id: int,
        document_groups: dict[str, dict[int, ReplyTarget]],
        receipts: dict[str, MemoryDocumentReceipt],
        known: dict[int, ReplyTarget],
    ) -> None:
        prefix = f"telegram:message:{chat_id}:"
        previous_by_document: dict[str, tuple[int, ...]] = {}
        missing_ids: set[int] = set()
        for document_id, grouped in document_groups.items():
            receipt = receipts.get(document_id)
            if receipt is None:
                continue
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
                    if message_id not in known:
                        missing_ids.add(message_id)
            if document_id.startswith("telegram:thread:"):
                limit = self._settings.max_thread_messages
            elif document_id.startswith("telegram:dream-session:"):
                limit = max(
                    self._settings.max_thread_messages,
                    self._settings.session_max_events,
                )
            else:
                # Legacy ID-packed documents can be larger than new sessions.
                limit = max(
                    self._settings.max_messages,
                    self._settings.max_thread_messages,
                )
            if len(grouped) + len(previous_ids) > limit:
                raise DreamThreadLimitError(
                    f"Document {document_id} exceeds its configured Dream bound"
                )
            previous_by_document[document_id] = tuple(previous_ids)

        semaphore = asyncio.Semaphore(self._settings.preprocess_concurrency)

        async def fetch(message_id: int) -> ReplyTarget | None:
            async with semaphore:
                return await self._retry_telegram(
                    lambda: self._source.fetch_message(chat_id, message_id)
                )

        if missing_ids:
            fetched = await asyncio.gather(
                *(fetch(message_id) for message_id in sorted(missing_ids))
            )
            for message in fetched:
                if message is not None:
                    known[message.id] = message

        for document_id, previous_ids in previous_by_document.items():
            grouped = document_groups[document_id]
            for message_id in previous_ids:
                message = known.get(message_id)
                if message is not None:
                    grouped[message.id] = message

    async def _build_observations(
        self,
        chat_id: int,
        messages: tuple[ReplyTarget, ...],
    ) -> dict[int, HumanObservation]:
        message_ids = tuple(message.id for message in messages)
        excluded_ids, answer_ids = await asyncio.gather(
            self._store.get_memory_excluded_message_ids(chat_id, message_ids),
            self._store.get_ai_answer_message_ids(chat_id, message_ids),
        )
        semaphore = asyncio.Semaphore(self._settings.preprocess_concurrency)
        identity_semaphore = asyncio.Semaphore(self._settings.preprocess_concurrency)
        identity_tasks: dict[int, asyncio.Task[Any]] = {}
        album_attachment_representatives: dict[int, int] = {}
        for message in messages:
            grouped_id = getattr(message, "grouped_id", None)
            if (
                bool(getattr(message, "post", False))
                and isinstance(grouped_id, int)
                and message_has_attachment(message)
            ):
                album_attachment_representatives.setdefault(grouped_id, message.id)

        async def resolve_identity(message: ReplyTarget) -> Any:
            async with identity_semaphore:
                return await self._prompt_builder.resolve_identity(message)

        def identity_for(message: ReplyTarget) -> asyncio.Task[Any]:
            assert message.sender_id is not None
            task = identity_tasks.get(message.sender_id)
            if task is None:
                task = asyncio.create_task(resolve_identity(message))
                identity_tasks[message.sender_id] = task
            return task

        async def build(message: ReplyTarget) -> HumanObservation | None:
            if (
                message.sender_id is None
                or message.id in excluded_ids
                or message.id in answer_ids
            ):
                return None
            async with semaphore:
                text = _memory_message_text(message.raw_text or "")
                grouped_id = getattr(message, "grouped_id", None)
                representative_id = (
                    album_attachment_representatives.get(grouped_id)
                    if isinstance(grouped_id, int)
                    else None
                )
                if representative_id is not None and representative_id != message.id:
                    attachment = attachment_metadata_only(
                        message,
                        reason="another item in this media album was analyzed",
                    )
                else:
                    attachment = await self._prompt_builder.describe_attachment(message)
                observation_text = self._prompt_builder.build_observation_text(
                    text,
                    attachment,
                )
                if not observation_text:
                    return None
                identity = await identity_for(message)
                if not identity.is_memory_source:
                    return None
                mentioned_users = (
                    await self._prompt_builder.resolve_mentions(message)
                    if getattr(message, "entities", None)
                    else ()
                )
                return HumanObservation(
                    message_id=message.id,
                    sender_id=message.sender_id,
                    text=observation_text,
                    occurred_at=_message_datetime(message),
                    mentioned_at=_message_datetime(message),
                    identity=identity,
                    reply_to_message_id=message.reply_to_msg_id,
                    mentioned_users=mentioned_users,
                    metadata=_telegram_memory_event_metadata(message),
                )

        observations = await asyncio.gather(*(build(message) for message in messages))
        return {
            observation.message_id: observation
            for observation in observations
            if observation is not None
        }

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
        scopes = await self._store.list_memory_dream_scopes()
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


class ContinuousMemoryScheduler:
    def __init__(
        self,
        *,
        scanner: Any,
        store: AIStateRepository,
        settings: ContinuousMemorySchedulerSettings = (
            ContinuousMemorySchedulerSettings()
        ),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        logger: Any | None = None,
    ):
        self._scanner = scanner
        self._store = store
        self._settings = settings
        self._sleep = sleep
        self._logger = logger
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._run_forever(),
            name="telefire-continuous-memory-scheduler",
        )

    def notify(self) -> None:
        self._wake.set()

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_once(self) -> ContinuousMemoryScheduleResult:
        scopes = await self._store.list_continuous_memory_scopes()
        semaphore = asyncio.Semaphore(self._settings.concurrency)
        succeeded = 0
        failed = 0
        busy = 0
        pending = 0
        messages_seen = 0
        messages_retained = 0

        async def run(scope_id: str) -> tuple[str, Any | None]:
            prefix = "telegram:chat:"
            if not scope_id.startswith(prefix):
                return "failed", None
            try:
                chat_id = int(scope_id[len(prefix) :])
            except ValueError:
                return "failed", None
            async with semaphore:
                try:
                    result = await self._scanner.run_continuous_scope(chat_id)
                except DreamCycleBusyError:
                    return "busy", None
                except Exception as exc:
                    if self._logger is not None:
                        self._logger.warning(
                            "Continuous memory ingestion failed "
                            "(scope_id=%s, error=%s): %s",
                            scope_id,
                            type(exc).__name__,
                            exc,
                        )
                    return "failed", None
            return "succeeded", result

        for start in range(0, len(scopes), self._settings.scope_batch_size):
            results = await asyncio.gather(
                *(
                    run(scope_id)
                    for scope_id in scopes[
                        start : start + self._settings.scope_batch_size
                    ]
                )
            )
            for status, result in results:
                succeeded += status == "succeeded"
                failed += status == "failed"
                busy += status == "busy"
                if result is None:
                    continue
                messages_seen += result.messages_seen
                messages_retained += result.messages_retained
                pending += not result.caught_up

        schedule_result = ContinuousMemoryScheduleResult(
            scopes_seen=len(scopes),
            scopes_succeeded=succeeded,
            scopes_failed=failed,
            scopes_busy=busy,
            scopes_pending=pending,
            messages_seen=messages_seen,
            messages_retained=messages_retained,
        )
        if self._logger is not None:
            self._logger.info(
                "Continuous memory cycle complete "
                "(scopes=%s, succeeded=%s, failed=%s, busy=%s, pending=%s, "
                "messages=%s, retained=%s)",
                schedule_result.scopes_seen,
                schedule_result.scopes_succeeded,
                schedule_result.scopes_failed,
                schedule_result.scopes_busy,
                schedule_result.scopes_pending,
                schedule_result.messages_seen,
                schedule_result.messages_retained,
            )
        return schedule_result

    async def _run_forever(self) -> None:
        while True:
            self._wake.clear()
            try:
                result = await self.run_once()
            except Exception as exc:
                if self._logger is not None:
                    self._logger.exception(
                        "Continuous memory orchestration failed (error=%s): %s",
                        type(exc).__name__,
                        exc,
                    )
                result = None
            if result is not None and result.scopes_pending:
                await self._sleep(0)
                continue
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self._settings.poll_interval_seconds,
                )
            except TimeoutError:
                pass
