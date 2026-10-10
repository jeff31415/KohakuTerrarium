import ctypes
from types import SimpleNamespace

import pytest

from kohakuterrarium.llm import antigravity_store as store


class StorageFailure(Exception):
    pass


class MissingItem(StorageFailure):
    pass


class LockedItem(StorageFailure):
    pass


@pytest.fixture
def secret_service(monkeypatch):
    state = {
        "raw": b"consumer-secret",
        "items": 1,
        "collection_locked": False,
        "item_locked": False,
        "login_missing": False,
        "default_missing": False,
        "failure": None,
        "queries": [],
        "paths": [],
        "aliases": [],
        "reads": 0,
        "closes": 0,
    }

    class Connection:
        def close(self):
            state["closes"] += 1

    class Item:
        def is_locked(self):
            return state["item_locked"]

        def get_secret(self):
            state["reads"] += 1
            return state["raw"]

    class Collection:
        def is_locked(self):
            return state["collection_locked"]

        def search_items(self, attributes):
            state["queries"].append(attributes)
            return [Item() for _ in range(state["items"])]

    def collection(connection, path):
        state["paths"].append(path)
        if state["failure"]:
            raise state["failure"]
        if state["login_missing"]:
            raise MissingItem("not found")
        return Collection()

    def by_alias(connection, alias):
        state["aliases"].append(alias)
        if state["default_missing"]:
            raise MissingItem("not found")
        return Collection()

    monkeypatch.setattr(store, "_connect_linux", Connection, raising=False)
    monkeypatch.setattr(
        store,
        "secretstorage",
        SimpleNamespace(
            Collection=collection,
            get_collection_by_alias=by_alias,
            ItemNotFoundException=MissingItem,
            LockedException=LockedItem,
            SecretStorageException=StorageFailure,
            dbus_init=Connection,
        ),
    )
    monkeypatch.setattr(store, "DBusErrorResponse", StorageFailure, raising=False)
    monkeypatch.setattr(store.sys, "platform", "linux")
    return state


def test_linux_reads_only_agy_item_and_closes_connection(secret_service):
    assert store.read_keyring() == ("consumer-secret", "linux_secret_service")
    assert secret_service["paths"] == ["/org/freedesktop/secrets/collection/login"]
    assert secret_service["queries"] == [
        {"service": "gemini", "username": "antigravity"}
    ]
    assert secret_service["reads"] == 1
    assert secret_service["closes"] == 1
    assert secret_service["aliases"] == []


def test_linux_default_alias_without_creating_a_collection(secret_service):
    secret_service["login_missing"] = True
    assert store.read_keyring()[0] == "consumer-secret"
    assert secret_service["aliases"] == ["default"]
    secret_service["default_missing"] = True
    assert store.read_keyring() == (None, "linux_secret_service")
    assert secret_service["closes"] == 2


def test_linux_missing_item_is_not_an_unavailable_store(secret_service):
    secret_service["items"] = 0
    assert store.read_keyring() == (None, "linux_secret_service")
    assert secret_service["reads"] == 0
    assert secret_service["closes"] == 1


@pytest.mark.parametrize("target", ["collection_locked", "item_locked"])
def test_linux_never_unlocks_a_locked_store(secret_service, target):
    secret_service[target] = True
    with pytest.raises(store.AntigravityError, match="credential_store_locked"):
        store.read_keyring()
    assert secret_service["reads"] == 0
    assert secret_service["closes"] == 1


def test_linux_duplicate_items_are_rejected_before_reading_secrets(secret_service):
    secret_service["items"] = 2
    with pytest.raises(store.AntigravityError, match="ambiguous_sources"):
        store.read_keyring()
    assert secret_service["reads"] == 0
    assert secret_service["closes"] == 1


@pytest.mark.parametrize("raw", [b"\xff", b"x" * 65537], ids=["utf8", "oversize"])
def test_linux_invalid_secrets_are_redacted(secret_service, raw):
    secret_service["raw"] = raw
    with pytest.raises(store.AntigravityError, match="malformed_credential"):
        store.read_keyring()
    assert secret_service["closes"] == 1


@pytest.mark.parametrize(
    "failure",
    [StorageFailure("secret-canary"), OSError("secret-canary"), TimeoutError()],
)
def test_linux_store_failures_are_redacted(secret_service, failure):
    secret_service["failure"] = failure
    with pytest.raises(
        store.AntigravityError, match="credential_store_unavailable"
    ) as exc:
        store.read_keyring()
    assert "secret-canary" not in str(exc.value)
    assert secret_service["closes"] == 1


def test_linux_missing_dependency_and_other_platforms(monkeypatch):
    monkeypatch.setattr(store, "secretstorage", None)
    monkeypatch.setattr(store.sys, "platform", "linux")
    with pytest.raises(store.AntigravityError, match="credential_store_unavailable"):
        store.read_keyring()
    for platform in ("darwin", "freebsd", "android"):
        monkeypatch.setattr(store.sys, "platform", platform)
        with pytest.raises(store.AntigravityError, match="unsupported_platform"):
            store.read_keyring()


def test_linux_connection_closes_socket_on_failed_handshake(monkeypatch):
    closed = []
    socket = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(store, "get_bus", lambda bus: "test-bus", raising=False)

    def prep(address, *, timeout):
        assert address == "test-bus"
        assert timeout == 5
        return socket

    def reject(socket, deadline):
        raise TimeoutError

    monkeypatch.setattr(store, "prep_socket", prep, raising=False)
    monkeypatch.setattr(store, "_DeadlineConnection", reject)
    with pytest.raises(TimeoutError):
        store._connect_linux()
    assert closed == [True]


def test_linux_connection_bounds_every_reply_and_expires(monkeypatch):
    pytest.importorskip("secretstorage")
    blocking = pytest.importorskip("jeepney.io.blocking")
    limits, replies = [], []
    socket = SimpleNamespace(settimeout=limits.append)
    clock = [100.0]
    monkeypatch.setattr(store.time, "monotonic", lambda: clock[0])

    def initialize(self, sock):
        self.sock = sock
        self.send_and_get_reply("Hello")

    def send(self, message, *, timeout=None):
        replies.append((message, timeout))
        return "reply"

    monkeypatch.setattr(blocking.DBusConnection, "__init__", initialize)
    monkeypatch.setattr(blocking.DBusConnection, "send_and_get_reply", send)
    connection = store._DeadlineConnection(socket, 105.0)
    clock[0] = 102.0
    assert connection.send_and_get_reply("SearchItems", timeout=1) == "reply"
    assert replies == [("Hello", 5), ("SearchItems", 1)]
    assert limits == [5, 3]
    clock[0] = 105.0
    with pytest.raises(TimeoutError):
        connection.send_and_get_reply("GetSecret")
    assert len(replies) == 2


@pytest.mark.parametrize(
    "raw,error,expected",
    [
        (b"windows-secret", None, "windows-secret"),
        (b"\xff", None, "malformed_credential"),
        (b"x" * 65537, None, "malformed_credential"),
        (b"", 1168, None),
        (b"", 5, "credential_store_unavailable"),
    ],
    ids=["ready", "utf8", "oversize", "missing", "denied"],
)
def test_windows_reader_preserves_target_and_frees_blob(
    monkeypatch, raw, error, expected
):
    blob = ctypes.create_string_buffer(raw)
    credential = store._WindowsCredential()
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte))
    credential.CredentialBlobSize = len(raw)
    calls, freed = [], []

    class Read:
        def __call__(self, target, kind, flags, output):
            calls.append((target, kind, flags))
            ctypes.cast(
                output, ctypes.POINTER(ctypes.POINTER(store._WindowsCredential))
            )[0] = ctypes.pointer(credential)
            return error is None

    class Free:
        def __call__(self, pointer):
            freed.append(True)

    library = SimpleNamespace(CredReadW=Read(), CredFree=Free())
    monkeypatch.setattr(store.sys, "platform", "win32")
    monkeypatch.setattr(store.ctypes, "WinDLL", lambda *a, **k: library, raising=False)
    monkeypatch.setattr(store.ctypes, "get_last_error", lambda: error, raising=False)
    if expected in ("malformed_credential", "credential_store_unavailable"):
        with pytest.raises(store.AntigravityError, match=expected):
            store.read_keyring()
    else:
        assert store.read_keyring() == (expected, "windows_keyring")
    assert calls == [("gemini:antigravity", 1, 0)]
    assert freed == ([True] if error is None else [])
