from data_pipeline.http import PublicHTTPClient


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.content = b"{}"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("status %s" % self.status_code)


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = 0

    def request(self, method, url, timeout=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return FakeResponse(429, headers={"Retry-After": "0.25"})
        return FakeResponse(200, payload={"ok": True})


def test_public_http_client_respects_retry_after_on_429():
    sleeps = []
    session = FakeSession()
    client = PublicHTTPClient(session=session, retries=3, sleeper=sleeps.append)

    payload = client.get_json("https://example.test/public")

    assert payload == {"ok": True}
    assert session.calls == 2
    assert sleeps == [0.25]

