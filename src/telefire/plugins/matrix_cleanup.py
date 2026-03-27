from datetime import datetime, timedelta, timezone

from telefire.matrix import MatrixCommand
from telefire.plugins.base import PluginMount
from mautrix.types import PaginationDirection


class MatrixCleanup(MatrixCommand, metaclass=PluginMount):
    command_name = 'matrix_cleanup'

    def __call__(self, days=90, confirm=False):
        """Leave Matrix rooms with no messages in the past N days.

        Args:
            days: Number of days of inactivity (default 90)
            confirm: Set to True to actually leave rooms. Default is dry-run.
        """

        async def _cleanup():
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            rooms = await self.matrix.client.get_joined_rooms()
            self._logger.info(f"Checking {len(rooms)} rooms for inactivity (>{days} days)...\n")

            stale = []
            errors = []

            for i, room_id in enumerate(rooms):
                name = await self.rooms.get_display_name(room_id)
                try:
                    msgs = await self.matrix.client.get_messages(
                        room_id=room_id,
                        direction=PaginationDirection.BACKWARD,
                        limit=1,
                    )
                    if msgs.events:
                        last_ts = msgs.events[0].timestamp / 1000
                        last_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)
                        if last_dt < cutoff:
                            days_ago = (datetime.now(timezone.utc) - last_dt).days
                            stale.append((room_id, name, days_ago))
                    else:
                        # No messages at all
                        stale.append((room_id, name, -1))
                except Exception as e:
                    errors.append((room_id, name, str(e)))

                if (i + 1) % 20 == 0:
                    self._logger.info(f"  checked {i + 1}/{len(rooms)}...")

            self._logger.info(f"\n{'=' * 60}")
            self._logger.info(f"Stale rooms (no messages in {days}+ days): {len(stale)}")
            self._logger.info(f"Errors (could not check): {len(errors)}")
            self._logger.info(f"{'=' * 60}\n")

            for room_id, name, days_ago in sorted(stale, key=lambda x: x[2], reverse=True):
                age = f"{days_ago} days ago" if days_ago >= 0 else "no messages"
                self._logger.info(f"  {'[LEAVE]' if confirm else '[DRY-RUN]'} {name} ({room_id}) — last activity: {age}")

            if errors:
                self._logger.info(f"\nCould not check ({len(errors)} rooms):")
                for room_id, name, err in errors:
                    self._logger.info(f"  [SKIP] {name} ({room_id}) — {err}")

            if not confirm:
                self._logger.info(f"\nDry-run complete. To actually leave these {len(stale)} rooms, run:")
                self._logger.info(f"  uv run telefire matrix_cleanup --days={days} --confirm=True")
                return

            self._logger.info(f"\nLeaving {len(stale)} rooms...")
            left = 0
            for room_id, name, days_ago in stale:
                try:
                    await self.matrix.client.leave_room(room_id)
                    self._logger.info(f"  Left: {name}")
                    left += 1
                except Exception as e:
                    self._logger.info(f"  Failed to leave {name}: {e}")

            self._logger.info(f"\nDone. Left {left}/{len(stale)} rooms.")

        self.run_matrix(_cleanup)
