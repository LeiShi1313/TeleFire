from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


MAX_MEMORY_BACKFILL_DAYS = 30
MAX_MEMORY_BACKFILL_MESSAGES = 5_000


@dataclass(frozen=True, slots=True)
class AIAskCommand:
    prompt: str
    recent_messages: int | None = None


@dataclass(frozen=True, slots=True)
class AICancelCommand:
    pass


@dataclass(frozen=True, slots=True)
class AccessCommand:
    allowed: bool

    @property
    def name(self) -> str:
        return "/ai_allow" if self.allowed else "/ai_deny"


@dataclass(frozen=True, slots=True)
class MemoryRememberCommand:
    instruction: str


@dataclass(frozen=True, slots=True)
class MemoryBackfillCommand:
    mode: Literal["days", "messages"]
    value: int

    def __post_init__(self) -> None:
        maximum = (
            MAX_MEMORY_BACKFILL_DAYS
            if self.mode == "days"
            else MAX_MEMORY_BACKFILL_MESSAGES
        )
        if self.mode not in {"days", "messages"} or not 1 <= self.value <= maximum:
            raise ValueError("Memory backfill request is outside supported bounds")


@dataclass(frozen=True, slots=True)
class MemoryModeCommand:
    mode: Literal["continuous", "dream"]
    enabled: bool
    target: str | None = None

    @property
    def name(self) -> str:
        prefix = "/ai_memory" if self.mode == "continuous" else "/ai_dream"
        suffix = "enable" if self.enabled else "disable"
        return f"{prefix}_{suffix}"


@dataclass(frozen=True, slots=True)
class MemoryStatusCommand:
    pass


@dataclass(frozen=True, slots=True)
class MemoryListCommand:
    pass


@dataclass(frozen=True, slots=True)
class MemoryDreamCommand:
    pass


@dataclass(frozen=True, slots=True)
class InvalidCommand:
    name: str


ChatCommand: TypeAlias = (
    AIAskCommand
    | AICancelCommand
    | AccessCommand
    | MemoryRememberCommand
    | MemoryBackfillCommand
    | MemoryModeCommand
    | MemoryStatusCommand
    | MemoryListCommand
    | MemoryDreamCommand
    | InvalidCommand
)


def parse_chat_command(text: str | None) -> ChatCommand | None:
    if text is None or not text.startswith("/"):
        return None

    ai = _parse_ai(text)
    if ai is not None:
        return ai

    memory_revision = _parse_memory_revision(text)
    if memory_revision is not None:
        return memory_revision

    control = text.strip()
    if control == "/ai_cancel":
        return AICancelCommand()
    if control == "/ai_allow":
        return AccessCommand(allowed=True)
    if control == "/ai_deny":
        return AccessCommand(allowed=False)

    memory = _parse_memory_control(control)
    if memory is not None:
        return memory
    return None


def _parse_ai(text: str) -> AIAskCommand | None:
    if not text.casefold().startswith("/ai"):
        return None
    cursor = 3
    digit_start = cursor
    while cursor < len(text) and text[cursor].isascii() and text[cursor].isdigit():
        cursor += 1
    recent_messages = (
        int(text[digit_start:cursor]) if cursor > digit_start else None
    )
    if cursor < len(text) and text[cursor] == "@":
        cursor += 1
        mention_start = cursor
        while cursor < len(text) and text[cursor] not in " \n\t\r":
            cursor += 1
        if cursor == mention_start:
            return None
    if cursor == len(text):
        return AIAskCommand(prompt="", recent_messages=recent_messages)
    if text[cursor] not in " \n\t\r":
        return None
    return AIAskCommand(
        prompt=text[cursor:].strip(),
        recent_messages=recent_messages,
    )


def _parse_memory_revision(text: str) -> MemoryRememberCommand | None:
    if text == "/ai_memory":
        return MemoryRememberCommand(instruction="")
    if text.startswith(("/ai_memory ", "/ai_memory\n", "/ai_memory\t")):
        return MemoryRememberCommand(
            instruction=text[len("/ai_memory") :].strip()
        )
    return None


def _parse_memory_control(text: str) -> ChatCommand | None:
    parts = text.split()
    if not parts:
        return None
    name = parts[0]
    if name == "/ai_memory_backfill":
        if len(parts) != 3 or parts[1] not in {"days", "messages"}:
            return InvalidCommand(name=name)
        try:
            value = int(parts[2])
        except ValueError:
            return InvalidCommand(name=name)
        maximum = (
            MAX_MEMORY_BACKFILL_DAYS
            if parts[1] == "days"
            else MAX_MEMORY_BACKFILL_MESSAGES
        )
        if not 1 <= value <= maximum:
            return InvalidCommand(name=name)
        return MemoryBackfillCommand(mode=parts[1], value=value)

    mode_commands = {
        "/ai_memory_enable": ("continuous", True),
        "/ai_memory_disable": ("continuous", False),
        "/ai_dream_enable": ("dream", True),
        "/ai_dream_disable": ("dream", False),
    }
    if name in mode_commands:
        if len(parts) > 2:
            return InvalidCommand(name=name)
        mode, enabled = mode_commands[name]
        return MemoryModeCommand(
            mode=mode,
            enabled=enabled,
            target=parts[1] if len(parts) == 2 else None,
        )
    if text == "/ai_memory_status":
        return MemoryStatusCommand()
    if text == "/ai_memory_list":
        return MemoryListCommand()
    if text == "/ai_memory_dream":
        return MemoryDreamCommand()
    return None
