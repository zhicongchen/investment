from __future__ import annotations

import os
import socket

import pytest

from shared import ibkr
from shared.env import load_dotenv
from shared.moomoo import MoomooClient, MoomooError, is_reachable


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    first = tmp_path / "a.env"
    second = tmp_path / "b.env"
    first.write_text("# comment\nSHARED_TEST_A='one'\nSHARED_TEST_B=keep\n")
    second.write_text('SHARED_TEST_A="two"\nSHARED_TEST_C=three\n')
    monkeypatch.setenv("SHARED_TEST_B", "already set")
    monkeypatch.delenv("SHARED_TEST_A", raising=False)
    monkeypatch.delenv("SHARED_TEST_C", raising=False)

    load_dotenv(first, second, tmp_path / "missing.env")

    assert os.environ["SHARED_TEST_A"] == "one"
    assert os.environ["SHARED_TEST_B"] == "already set"
    assert os.environ["SHARED_TEST_C"] == "three"
    for key in ("SHARED_TEST_A", "SHARED_TEST_C"):
        monkeypatch.delenv(key)


def test_moomoo_client_fails_fast_when_opend_is_down():
    port = _closed_port()
    assert not is_reachable("127.0.0.1", port, timeout=1)
    with pytest.raises(MoomooError, match="not reachable"):
        MoomooClient("127.0.0.1", port).connect()


def test_ibkr_session_raises_connection_error():
    with pytest.raises(ibkr.IbkrConnectionError, match="TWS not reachable"):
        with ibkr.session("127.0.0.1", _closed_port(), client_id=99):
            pass
