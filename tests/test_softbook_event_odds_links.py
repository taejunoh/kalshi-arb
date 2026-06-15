# tests/test_softbook_event_odds_links.py
"""event_odds(links=True) must append includeLinks/includeSids to the Odds API URL
(needed so the deep-link builder gets provider bet-slip links/SIDs); links=False
(default — bulk scan / price-move polling) must NOT, to avoid any extra cost."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_ev_core as core  # noqa: E402


class _Resp:
    def __init__(self, body):
        self._b = body
        self.headers = {"x-requests-last": "1"}

    def read(self):
        return self._b


def test_event_odds_links_param_toggles_includelinks(monkeypatch):
    captured = {}

    def fake_urlopen(url, timeout=30):
        captured["url"] = url
        return _Resp(json.dumps({"bookmakers": []}).encode())

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)

    core.event_odds("baseball_mlb", "EID", "KEY", "h2h", links=True)
    assert "includeLinks=true" in captured["url"]
    assert "includeSids=true" in captured["url"]

    core.event_odds("baseball_mlb", "EID", "KEY", "h2h")  # default links=False
    assert "includeLinks" not in captured["url"]
    assert "includeSids" not in captured["url"]
