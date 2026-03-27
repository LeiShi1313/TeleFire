from mautrix.api import HTTPAPI
from mautrix.client import Client
from mautrix.client.state_store import FileStateStore
from mautrix.errors import MatrixConnectionError, MatrixError, MatrixInvalidToken
from mautrix.types import Filter, FilterID

from telefire.matrix.config import MatrixRuntimeConfig
from telefire.matrix.store import FileSyncStore, MatrixSession, MatrixSessionStore
from telefire.runtime import build_logger


class MatrixService:
    def __init__(self, config: MatrixRuntimeConfig, log_level: str = "info"):
        self.config = config
        self.logger = build_logger(__name__, log_level=log_level)
        self._client: Client | None = None
        self._session_store = MatrixSessionStore(config.session_path)
        self._sync_store = FileSyncStore(config.sync_store_path)
        self._state_store = FileStateStore(config.state_store_path)
        self._stores_open = False
        self._whoami_user_id = config.user_id

    @property
    def client(self) -> Client:
        if self._client is None:
            raise RuntimeError("Matrix client is not connected")
        return self._client

    @property
    def user_id(self) -> str:
        return self._whoami_user_id

    async def connect(self) -> Client:
        if self._client is not None:
            return self._client

        await self._open_stores()

        stored_session = self._load_stored_session()
        candidate_session = self._load_env_session() or stored_session

        if candidate_session is not None:
            client = self._build_client(
                access_token=candidate_session.access_token,
                device_id=candidate_session.device_id,
            )
            try:
                await self._validate_client(client)
            except MatrixInvalidToken:
                self.logger.info("Matrix session token is invalid, bootstrapping a new session.")
                self._session_store.clear()
            except MatrixConnectionError:
                raise
            except MatrixError as exc:
                self.logger.debug(f"Stored Matrix session validation failed: {exc}")
                self._session_store.clear()
            else:
                self._client = client
                self._persist_session()
                self.logger.info(f"Connected to Matrix as {self.user_id}")
                return self._client

        if not self.config.password:
            raise ValueError(
                "No valid Matrix session found. Set MATRIX_PASSWORD or run: telefire init"
            )

        bootstrap_device_id = self.config.device_id or (
            stored_session.device_id if stored_session is not None else None
        )
        client = self._build_client(device_id=bootstrap_device_id)
        login_response = await client.login(
            identifier=self.config.user_id,
            password=self.config.password,
            device_name=self.config.device_name,
            device_id=bootstrap_device_id,
        )
        self._client = client
        await self._validate_client(self._client)
        self._persist_session(
            MatrixSession(
                base_url=self.config.base_url,
                user_id=login_response.user_id,
                device_id=login_response.device_id,
                access_token=login_response.access_token,
            )
        )
        self.logger.info(f"Connected to Matrix as {self.user_id}")
        return self._client

    async def close(self) -> None:
        client = self._client
        if client is not None:
            client.stop()
            if not client.api.session.closed:
                await client.api.session.close()

        await self._state_store.flush()
        await self._sync_store.flush()
        self._client = None

    async def start_sync(self, filter_data: FilterID | Filter | None = None) -> None:
        await self.client.start(filter_data=filter_data)

    def _build_client(
        self,
        access_token: str | None = None,
        device_id: str | None = None,
    ) -> Client:
        api = HTTPAPI(self.config.base_url, token=access_token or "")
        return Client(
            mxid=self.config.user_id,
            device_id=device_id or "",
            api=api,
            sync_store=self._sync_store,
            state_store=self._state_store,
        )

    async def _open_stores(self) -> None:
        if self._stores_open:
            return

        self.config.store_dir.mkdir(parents=True, exist_ok=True)
        await self._state_store.open()
        await self._sync_store.open()
        self._stores_open = True

    def _load_env_session(self) -> MatrixSession | None:
        if not self.config.access_token:
            return None
        return MatrixSession(
            base_url=self.config.base_url,
            user_id=self.config.user_id,
            device_id=self.config.device_id or "",
            access_token=self.config.access_token,
        )

    def _load_stored_session(self) -> MatrixSession | None:
        session = self._session_store.load()
        if session is None:
            return None
        if session.base_url != self.config.base_url or session.user_id != self.config.user_id:
            return None
        return session

    async def _validate_client(self, client: Client) -> None:
        whoami = await client.whoami()
        if whoami.user_id != self.config.user_id:
            raise RuntimeError(
                f"Matrix session belongs to {whoami.user_id}, expected {self.config.user_id}"
            )
        client.mxid = whoami.user_id
        if whoami.device_id:
            client.device_id = whoami.device_id
        self._whoami_user_id = whoami.user_id

    def _persist_session(self, session: MatrixSession | None = None) -> None:
        client = self.client
        persisted = session or MatrixSession(
            base_url=self.config.base_url,
            user_id=self.user_id,
            device_id=client.device_id or "",
            access_token=client.api.token,
        )
        if not persisted.device_id or not persisted.access_token:
            raise RuntimeError("Matrix session is missing device_id or access_token")
        self._session_store.save(persisted)
