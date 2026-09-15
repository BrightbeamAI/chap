from __future__ import annotations

import pytest

from chap_analytics.load import from_url


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://internal/host",
    "gopher://169.254.169.254/",
    "//no-scheme/path",
])
def test_from_url_rejects_non_http_schemes(url):
    with pytest.raises(ValueError, match="http and https"):
        from_url(url, "w")


def test_from_url_rejects_before_any_request(monkeypatch):
    called = False

    def boom(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network should not be reached")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(ValueError, match="http and https"):
        from_url("file:///etc/passwd", "w")
    assert called is False
