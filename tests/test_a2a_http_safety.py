import socket

import pytest

from plugin import tools


def test_http_request_blocks_redirects(monkeypatch):
    class RedirectingResponse:
        status = 302

        def read(self, _size=-1):
            return b""

    class RedirectingConnection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return RedirectingResponse()

        def close(self):
            pass

    monkeypatch.setattr(
        tools.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
    )
    monkeypatch.setattr(tools.http.client, "HTTPConnection", RedirectingConnection)

    with pytest.raises(RuntimeError, match="HTTP 302"):
        tools._http_request("POST", "http://agent.local", {"x": 1}, {"Authorization": "Bearer secret"})
