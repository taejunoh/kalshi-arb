import json
import softbook_scan as scan


class _Blk:
    def __init__(self, t, text=None):
        self.type = t
        self.text = text


class _Resp:
    def __init__(self, content):
        self.content = content


class _Msgs:
    def __init__(self, payload):
        self._payload = payload
        self.kw = None

    def create(self, **kw):
        self.kw = kw
        return _Resp([_Blk("thinking"), _Blk("text", json.dumps(self._payload))])


class FakeClient:
    def __init__(self, payload):
        self.messages = _Msgs(payload)


def test_parse_bet_slip_returns_parsed_dict_and_sends_image():
    payload = {
        "sport": "baseball_mlb", "home": "COL", "away": "CHC",
        "market": "batter_total_bases", "side": "Over", "point": 1.5,
        "player": "Nico Hoerner", "book": "FanDuel", "fill_odds": 2.0,
        "stake": 2.0, "label": "Nico Hoerner TB Over 1.5", "notes": "single leg",
    }
    fake = FakeClient(payload)
    out = scan.parse_bet_slip("QkFTRTY0", media_type="image/png", client=fake)
    assert out == payload
    assert fake.messages.kw["model"] == "claude-opus-4-8"
    img = fake.messages.kw["messages"][0]["content"][0]
    assert img["type"] == "image"
    assert img["source"] == {"type": "base64", "media_type": "image/png", "data": "QkFTRTY0"}
    assert fake.messages.kw["output_config"]["format"]["type"] == "json_schema"
