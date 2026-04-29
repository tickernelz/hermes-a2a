import urllib.error

import pytest

from plugin import tools


def test_http_request_blocks_redirects(monkeypatch):
    class RedirectingOpener:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(req.full_url, 302, "Redirect blocked", {}, None)

    monkeypatch.setattr(tools.urllib.request, "build_opener", lambda *handlers: RedirectingOpener())

    with pytest.raises(RuntimeError, match="HTTP 302"):
        tools._http_request("POST", "http://agent.local", {"x": 1}, {"Authorization": "Bearer secret"})
