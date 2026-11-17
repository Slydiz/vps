import sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest


class FakeResp:
    def __init__(self, status=200, data=None, text="", headers=None):
        self.status_code, self._d, self.text, self.headers = status, data, text, headers or {}

    def json(self):
        return self._d


class FakeHttp:
    """Records calls; routes by substring of the URL. routes: {substr: FakeResp | callable(call)->FakeResp | list}"""
    def __init__(self, routes=None):
        self.routes, self.calls = routes or {}, []

    def _do(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for sub, r in self.routes.items():
            if sub in url:
                if isinstance(r, list):
                    return r.pop(0) if len(r) > 1 else r[0]
                return r(method, url, kw) if callable(r) else r
        return FakeResp(404, {})

    def get(self, url, **kw): return self._do("GET", url, **kw)
    def post(self, url, **kw): return self._do("POST", url, **kw)
    def put(self, url, **kw): return self._do("PUT", url, **kw)


@pytest.fixture
def http():
    return FakeHttp()
