import asyncio
import getpass
import json
import os
from contextlib import suppress
from datetime import datetime, timezone

from mautrix.errors import DecryptionError
from mautrix.types import EncryptedEvent, EventType, MessageEvent, PaginationDirection

from telefire.matrix import MatrixCommand
from telefire.plugins.base import PluginMount


class CryptoMatrixCommand(MatrixCommand):
    def __init__(self, account: str = "default", log_level: str = "info"):
        super().__init__(
            account=account,
            log_level=log_level,
            enable_crypto=True,
        )

    async def _status(self) -> dict:
        crypto = self.service.crypto
        own_identity = crypto.own_identity
        own_cross_signing = await crypto.get_own_cross_signing_public_keys()
        try:
            own_trust = await crypto.resolve_trust(own_identity)
        except Exception as exc:
            own_trust = f"error: {exc}"
        return {
            "account": self.service.config.account,
            "user_id": self.service.user_id,
            "device_id": self.client.device_id,
            "device_keys_shared": bool(crypto.account and crypto.account.shared),
            "curve25519_identity_key": str(crypto.account.identity_key),
            "ed25519_signing_key": str(crypto.account.signing_key),
            "own_device_trust": str(own_trust),
            "has_cross_signing_keys": own_cross_signing is not None,
            "crypto_store": str(self.service.config.crypto_store_path),
        }


class MatrixCryptoStatus(CryptoMatrixCommand, metaclass=PluginMount):
    command_name = "matrix_crypto_status"

    def __call__(self):
        """Print Matrix E2EE device, cross-signing, and crypto-store status."""

        async def _inner():
            print(json.dumps(await self._status(), indent=2, sort_keys=True))

        self.run_once(_inner)


class MatrixVerifyRecoveryKey(CryptoMatrixCommand, metaclass=PluginMount):
    command_name = "matrix_verify_recovery_key"

    def __call__(
        self,
        recovery_key: str | None = None,
        recovery_key_env: str = "MATRIX_RECOVERY_KEY",
        sync_seconds: int = 10,
    ):
        """Verify this headless Matrix device using the account recovery/security key."""

        async def _inner():
            key = recovery_key or os.environ.get(recovery_key_env)
            if not key:
                key = getpass.getpass("Matrix recovery/security key: ")
            key = key.strip()
            if not key:
                raise ValueError("Recovery key is required")

            crypto = self.service.crypto
            await crypto.verify_with_recovery_key(key)
            self.logger.info(
                "Telefire device has been signed with account cross-signing keys."
            )
            if sync_seconds > 0:
                self.logger.info(
                    f"Syncing for {sync_seconds}s to receive to-device keys."
                )
                await self.service.sync_for(sync_seconds)
            print(json.dumps(await self._status(), indent=2, sort_keys=True))

        self.run_once(_inner)


class MatrixCryptoSync(CryptoMatrixCommand, metaclass=PluginMount):
    command_name = "matrix_crypto_sync"

    def __call__(self, seconds: int = 30, full_state: bool = False):
        """Run Matrix sync long enough to receive to-device room-key traffic."""

        async def _inner():
            count = await self.service.sync_for(seconds, full_state=full_state)
            self.logger.info(f"Completed {count} Matrix sync request(s).")

        self.run_once(_inner)


class MatrixDecryptHistory(CryptoMatrixCommand, metaclass=PluginMount):
    command_name = "matrix_decrypt_history"

    def __call__(
        self,
        room_id: str,
        limit: int = 20,
        request_keys: bool = False,
        key_request_timeout: int = 15,
        request_from_user: str | None = None,
    ):
        """Print recent room messages, decrypting Megolm events when keys are available.

        Args:
            room_id: Matrix room ID to inspect.
            limit: Number of recent events to fetch.
            request_keys: Ask other devices for missing Megolm sessions.
            key_request_timeout: Seconds to wait for each missing session.
            request_from_user: User ID whose devices should receive key requests.
        """

        async def _inner():
            sync_task = None
            stop_sync = None
            key_request_handler = None
            if request_keys:
                key_request_handler = self.client.crypto.handle_room_key_request
                self.client.remove_event_handler(
                    EventType.ROOM_KEY_REQUEST,
                    key_request_handler,
                )
                stop_sync = asyncio.Event()
                sync_task = asyncio.create_task(self._sync_key_requests(stop_sync))
                await asyncio.sleep(0)

            messages = await self.client.get_messages(
                room_id=room_id,
                direction=PaginationDirection.BACKWARD,
                limit=limit,
            )
            decrypted = 0
            encrypted = 0
            failed = 0
            try:
                for event in reversed(messages.events):
                    event_to_print = event
                    if isinstance(event, EncryptedEvent):
                        encrypted += 1
                        try:
                            event_to_print = (
                                await self.client.crypto.decrypt_megolm_event(event)
                            )
                            decrypted += 1
                        except DecryptionError as exc:
                            if request_keys:
                                requested = await self._request_room_key(
                                    event,
                                    request_from_user=request_from_user,
                                    timeout=key_request_timeout,
                                )
                                if requested:
                                    try:
                                        event_to_print = await self.client.crypto.decrypt_megolm_event(
                                            event
                                        )
                                        decrypted += 1
                                    except DecryptionError as retry_exc:
                                        failed += 1
                                        self._print_event(
                                            event,
                                            f"[unable to decrypt after key request: {retry_exc}]",
                                        )
                                        continue
                                else:
                                    failed += 1
                                    self._print_event(
                                        event, f"[unable to request key: {exc}]"
                                    )
                                    continue
                            else:
                                failed += 1
                                self._print_event(event, f"[unable to decrypt: {exc}]")
                                continue
                    if isinstance(event_to_print, MessageEvent):
                        body = getattr(event_to_print.content, "body", None)
                        if body:
                            self._print_event(event_to_print, body)
                self.logger.info(
                    f"Encrypted events: {encrypted}; decrypted: {decrypted}; failed: {failed}"
                )
            finally:
                if stop_sync and sync_task:
                    stop_sync.set()
                    try:
                        await asyncio.wait_for(sync_task, timeout=5)
                    except asyncio.TimeoutError:
                        sync_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await sync_task
                if key_request_handler:
                    self.client.add_event_handler(
                        EventType.ROOM_KEY_REQUEST,
                        key_request_handler,
                    )

        self.run_once(_inner)

    def _print_event(self, event, body: str) -> None:
        timestamp = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
        self.logger.info(f"{timestamp.isoformat()} {event.sender}: {body}")

    async def _request_room_key(
        self,
        event: EncryptedEvent,
        request_from_user: str | None,
        timeout: int,
    ) -> bool:
        sender_key = getattr(event.content, "_sender_key", None)
        if not sender_key:
            self.logger.info(
                f"{event.event_id}: cannot request key, event has no sender_key"
            )
            return False

        user_id = request_from_user or self.service.user_id
        devices_by_user = await self.client.crypto._fetch_keys(
            [user_id], include_untracked=True
        )
        device_ids = [
            device_id
            for device_id in devices_by_user.get(user_id, {})
            if device_id != self.client.device_id
        ]
        if not device_ids:
            self.logger.info(
                f"{event.event_id}: no candidate devices found for {user_id}"
            )
            return False

        self.logger.info(
            f"{event.event_id}: requesting room key from {len(device_ids)} device(s) of {user_id}"
        )
        return await self.client.crypto.request_room_key(
            room_id=event.room_id,
            sender_key=sender_key,
            session_id=event.content.session_id,
            from_devices={user_id: {device_id: None for device_id in device_ids}},
            timeout=timeout,
        )

    async def _sync_key_requests(self, stop_sync: asyncio.Event) -> None:
        sync_filter = json.dumps(
            {
                "account_data": {"types": []},
                "presence": {"types": []},
                "room": {
                    "account_data": {"types": []},
                    "ephemeral": {"types": []},
                    "state": {"types": []},
                    "timeline": {"limit": 0},
                },
            },
            separators=(",", ":"),
        )
        while not stop_sync.is_set():
            try:
                await self.service.sync_once(filter_data=sync_filter, timeout_ms=1000)
            except Exception as exc:
                if stop_sync.is_set():
                    return
                self.logger.info(
                    f"Key-request sync failed: {type(exc).__name__}: {exc}"
                )
                await asyncio.sleep(1)
