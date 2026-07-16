from __future__ import annotations

import asyncio
import os
import signal
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from telefire.ai import (
    AIConversationHandler,
    AIRateLimiter,
    AIResponder,
    AISettings,
    AIStateRepository,
    PiAgentGateway,
    PromptBuilder,
)
from telefire.ai_attachments import ChatAttachmentDescriber
from telefire.ai_dream import (
    ChatDreamScanner,
    ContinuousMemoryScheduler,
    ContinuousMemorySchedulerSettings,
    DreamScheduler,
    DreamSchedulerSettings,
    DreamSettings,
)
from telefire.ai_memory import HindsightMemoryClient
from telefire.onebot.ai import (
    QQ_IDENTITY_CODEC,
    OneBotChatTransport,
    OneBotDirectory,
    OneBotHistorySource,
    OneBotMemoryScopeTargetResolver,
    OneBotMessageIdentityResolver,
    OneBotMessageMentionResolver,
    onebot_memory_event_metadata,
    onebot_source_retry_delay,
    onebot_system_prompt,
)
from telefire.onebot.client import OneBotReverseWebSocket
from telefire.onebot.message import OneBotMessage, OneBotMessageError
from telefire.plugins.base import PluginMount
from telefire.runtime import build_logger


@dataclass(frozen=True, slots=True)
class OneBotRuntimeSettings:
    host: str
    port: int
    token: str
    self_id: int

    @classmethod
    def from_env(cls) -> OneBotRuntimeSettings:
        token = os.environ.get("TELEFIRE_ONEBOT_TOKEN", "").strip()
        self_id = _positive_int(os.environ.get("TELEFIRE_ONEBOT_SELF_ID", ""))
        if not token or self_id is None:
            raise ValueError(
                "Missing OneBot configuration: TELEFIRE_ONEBOT_TOKEN and "
                "TELEFIRE_ONEBOT_SELF_ID are required"
            )
        port = _positive_int(os.environ.get("TELEFIRE_ONEBOT_PORT", "8766"))
        if port is None or port > 65_535:
            raise ValueError("TELEFIRE_ONEBOT_PORT must be between 1 and 65535")
        return cls(
            host=os.environ.get("TELEFIRE_ONEBOT_HOST", "0.0.0.0").strip()
            or "0.0.0.0",
            port=port,
            token=token,
            self_id=self_id,
        )


class OneBotAI(metaclass=PluginMount):
    command_group = "onebot"
    command_name = "ai"

    def __init__(self, log_level: str = "info"):
        self._runtime = OneBotRuntimeSettings.from_env()
        self._settings = AISettings.from_env()
        self.logger = build_logger(__name__, log_level=log_level)
        self._gateway = PiAgentGateway(
            self._settings.agent_url,
            token=self._settings.agent_token,
            timeout=self._settings.request_timeout,
        )
        self._store = AIStateRepository(self._settings.state_path)
        self._memory = (
            HindsightMemoryClient(
                self._settings.hindsight_url,
                timeout=self._settings.hindsight_timeout,
            )
            if self._settings.hindsight_url
            else None
        )
        self._bridge = OneBotReverseWebSocket(
            token=self._runtime.token,
            self_id=self._runtime.self_id,
            logger=self.logger,
        )
        self._directory = OneBotDirectory()
        self._handler: AIConversationHandler | None = None
        self._dream_scheduler: DreamScheduler | None = None
        self._continuous_scheduler: ContinuousMemoryScheduler | None = None
        self._seen_messages: OrderedDict[tuple[int, int], None] = OrderedDict()

    def __call__(self) -> None:
        """Run the Pi-powered OneBot 11/NapCat userbot."""
        try:
            asyncio.run(self._run())
        except KeyboardInterrupt:
            pass

    async def _run(self) -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(stop_signal, stop.set)
            except NotImplementedError:
                pass
        try:
            await self._setup()
            await self._bridge.start(self._runtime.host, self._runtime.port)
            self.logger.info(
                "OneBot AI listening on %s:%s",
                self._runtime.host,
                self._runtime.port,
            )
            await self._bridge.wait_connected()
            await self._verify_account()
            await self._refresh_directory()
            if self._continuous_scheduler is not None:
                self._continuous_scheduler.start()
            if self._dream_scheduler is not None:
                self._dream_scheduler.start()
            self.logger.info(
                "OneBot AI connected (self_id=%s)",
                self._runtime.self_id,
            )
            await stop.wait()
        finally:
            if self._continuous_scheduler is not None:
                await self._continuous_scheduler.close()
            if self._dream_scheduler is not None:
                await self._dream_scheduler.close()
            await self._bridge.close()
            if self._memory is not None:
                await self._memory.close()
            await self._gateway.close()
            await self._store.close()

    async def _setup(self) -> None:
        transport = OneBotChatTransport(self._bridge, logger=self.logger)
        responder = AIResponder(
            self._gateway,
            max_output_chars=self._settings.max_output_chars,
            transport=transport,
            logger=self.logger,
        )
        await self._store.connect()
        history_source = OneBotHistorySource(
            self._bridge,
            directory=self._directory,
        )
        prompt_builder = PromptBuilder(
            system_prompt=onebot_system_prompt(self._settings.system_prompt),
            max_context_messages=self._settings.max_context_messages,
            max_context_chars=self._settings.max_context_chars,
            attachment_describer=ChatAttachmentDescriber(
                self._gateway,
                logger=self.logger,
            ),
            identity_resolver=OneBotMessageIdentityResolver(),
            mention_resolver=OneBotMessageMentionResolver(self._directory),
            history_source=history_source,
            transport=transport,
            identity_codec=QQ_IDENTITY_CODEC,
            metadata_resolver=onebot_memory_event_metadata,
        )
        dream_runner = (
            ChatDreamScanner(
                source=history_source,
                store=self._store,
                memory=self._memory,
                prompt_builder=prompt_builder,
                settings=DreamSettings.from_env(),
                identity_codec=QQ_IDENTITY_CODEC,
                source_retry_delay=onebot_source_retry_delay,
                logger=self.logger,
            )
            if self._memory is not None
            else None
        )
        if dream_runner is not None:
            self._dream_scheduler = DreamScheduler(
                scanner=dream_runner,
                store=self._store,
                identity_codec=QQ_IDENTITY_CODEC,
                settings=DreamSchedulerSettings.from_env(),
                logger=self.logger,
            )
            self._continuous_scheduler = ContinuousMemoryScheduler(
                scanner=dream_runner,
                store=self._store,
                identity_codec=QQ_IDENTITY_CODEC,
                settings=ContinuousMemorySchedulerSettings.from_env(),
                logger=self.logger,
            )
        self._handler = AIConversationHandler(
            owner_id=self._runtime.self_id,
            responder=responder,
            store=self._store,
            prompt_builder=prompt_builder,
            rate_limiter=AIRateLimiter(
                self._store,
                cooldown_seconds=self._settings.delegated_cooldown,
            ),
            memory=self._memory,
            dream_runner=dream_runner,
            memory_scope_resolver=OneBotMemoryScopeTargetResolver(self._bridge),
            memory_command_delete_delay=(
                self._settings.memory_command_delete_delay
            ),
            transport=transport,
            identity_codec=QQ_IDENTITY_CODEC,
            logger=self.logger,
        )
        self._bridge.set_event_handler(self._on_event)

    async def _verify_account(self) -> None:
        info = await self._bridge.call("get_login_info", {}, timeout=30)
        user_id = (
            _positive_int(info.get("user_id"))
            if isinstance(info, dict)
            else None
        )
        if user_id != self._runtime.self_id:
            raise RuntimeError("NapCat connected with an unexpected QQ account")

    async def _refresh_directory(self) -> None:
        try:
            await self._directory.refresh(self._bridge)
        except Exception as exc:
            self.logger.warning(
                "OneBot directory refresh failed (%s): %s",
                type(exc).__name__,
                exc,
            )

    async def _on_event(self, payload: dict[str, Any]) -> None:
        if payload.get("post_type") not in {"message", "message_sent"}:
            return
        try:
            message = OneBotMessage.from_payload(
                payload,
                action_client=self._bridge,
            )
        except OneBotMessageError as exc:
            self.logger.warning("Ignoring malformed OneBot message: %s", exc)
            return
        if message.scope_display_name is None:
            message.scope_display_name = self._directory.scope_name(message.chat_id)
        key = (message.chat_id, message.id)
        if key in self._seen_messages:
            return
        self._seen_messages[key] = None
        self._seen_messages.move_to_end(key)
        while len(self._seen_messages) > 4_096:
            self._seen_messages.popitem(last=False)
        if self._handler is None:
            return
        try:
            await self._handler.handle(message)
        except Exception:
            self.logger.exception(
                "OneBot AI message handling failed "
                "(chat_id=%s, message_id=%s)",
                message.chat_id,
                message.id,
            )


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None
