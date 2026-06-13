# scripts/softbook_push.py
#!/usr/bin/env python3
"""Expo Push API sender for the +EV companion app (replaces the Telegram send
path). POST messages to exp.host, pull receipts, prune dead tokens.

Docs shape: POST https://exp.host/--/api/v2/push/send with one message or an
array (<=100). Each returns a ticket {status, id}. Later POST the ticket ids to
/getReceipts; a DeviceNotRegistered receipt means stop sending to that token.
"""
SEND_URL = "https://exp.host/--/api/v2/push/send"
RECEIPT_URL = "https://exp.host/--/api/v2/push/getReceipts"


def build_message(token, title, body, data=None, category=None):
    msg = {"to": token, "title": title, "body": body, "sound": "default"}
    if data:
        msg["data"] = data
    if category:
        msg["categoryId"] = category
    return msg


def _http_post(url, json):
    import httpx
    return httpx.post(url, json=json, timeout=20,
                      headers={"Content-Type": "application/json"}).json()


def send(messages, post=_http_post):
    """Send in chunks of 100; return the flat list of tickets."""
    tickets = []
    for i in range(0, len(messages), 100):
        resp = post(SEND_URL, messages[i:i + 100])
        tickets.extend(resp.get("data", []))
    return tickets


def get_receipts(ticket_ids, post=_http_post):
    if not ticket_ids:
        return {}
    out = {}
    for i in range(0, len(ticket_ids), 1000):
        resp = post(RECEIPT_URL, {"ids": ticket_ids[i:i + 1000]})
        out.update(resp.get("data", {}))
    return out


def prune_tokens(db, ticket_to_token, receipts):
    """Mark a token inactive when its receipt is DeviceNotRegistered."""
    for ticket_id, rec in receipts.items():
        if rec.get("status") == "error" and \
                rec.get("details", {}).get("error") == "DeviceNotRegistered":
            token = ticket_to_token.get(ticket_id)
            if token:
                db.execute("UPDATE push_token SET active=0 WHERE token=?", (token,))
    db.commit()


def active_tokens(db):
    return [r[0] for r in db.execute(
        "SELECT token FROM push_token WHERE active=1")]
