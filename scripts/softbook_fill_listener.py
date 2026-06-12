#!/usr/bin/env python3
"""Telegram reply listener — logs the user's REAL fills against High alerts.

Why: the +EV verdict rides on ROI>=1%, and the gap between alerted odds and the
odds actually obtained (line moved / different book / line gone) can eat that
whole margin. Trackers (Pikkit etc.) only see bets that were PLACED — they can
never see the alerts the user had to skip, which is exactly the survivorship
that overstates capturable edge. So the user replies to each per-bet High alert:

    1.98          -> filled at the alerted book @ 1.98
    1.98 fd       -> filled at FanDuel @ 1.98
    +105 dk 5     -> filled at DraftKings @ +105 (american), $5 stake
    skip          -> could not / chose not to bet (no-fill)
    cashout 1.50  -> cashed the bet out early for $1.50 back (e.g. the opposite
                     side later alerted as +EV). Marks the fill so settle-at-close
                     PnL is known to diverge from realized; never touches the
                     ledger row. Sent on top of an earlier odds reply it keeps
                     that fill data (UPDATE, not replace).

A reply maps to its bet via reply_to_message_id == high_alerted.message_id
(stamped by softbook_ev_recorder._send_bet_alert). Re-replying to the same
alert overwrites (typo fix). Sending "fills" (or "슬리피지") returns a summary.

Runs as a systemd service (kalshi-fill-listener.service, User=ubuntu): this is
the ONLY getUpdates consumer for the bot token — never add another, Telegram
allows one. Writes real_fill into softbook_paper.db (ubuntu-owned); reads
high_alerted from softbook_ev.db (root-owned, read-only here).
"""
import json, os, sqlite3, sys, time, urllib.parse, urllib.request

ALERTS_DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")
FILLS_DB = os.getenv("SOFTBOOK_PAPER_DB", "/home/ubuntu/kalshi-arb/data/softbook_paper.db")
ENV_PATH = os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")

FILL_SCHEMA = """CREATE TABLE IF NOT EXISTS real_fill(
    message_id INTEGER PRIMARY KEY,
    event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT,
    alert_book TEXT, alert_odds REAL,
    fill_book TEXT, fill_odds REAL, fill_stake REAL,
    skipped INTEGER DEFAULT 0, raw_reply TEXT, replied_at TEXT,
    cashed_out INTEGER DEFAULT 0, cashout_amount REAL)"""
STATE_SCHEMA = "CREATE TABLE IF NOT EXISTS tg_state(k TEXT PRIMARY KEY, v TEXT)"

FILL_COLS = ("message_id, event_id, market, side, point, player, alert_book,"
             " alert_odds, fill_book, fill_odds, fill_stake, skipped, raw_reply,"
             " replied_at")

SKIP_WORDS = {"skip", "s", "pass", "x", "스킵", "패스", "안함", "노"}
CASHOUT_WORDS = {"cashout", "cashedout", "co", "캐시아웃", "캐쉬아웃"}
BOOK_ALIASES = {
    "fd": "FanDuel", "fanduel": "FanDuel",
    "dk": "DraftKings", "draftkings": "DraftKings",
    "mgm": "BetMGM", "betmgm": "BetMGM",
    "cz": "Caesars", "czr": "Caesars", "caesars": "Caesars",
    "br": "BetRivers", "rivers": "BetRivers", "betrivers": "BetRivers",
    "fan": "Fanatics", "fanatics": "Fanatics",
    "ts": "theScore Bet", "thescore": "theScore Bet", "score": "theScore Bet",
    "bally": "Bally Bet", "ballybet": "Bally Bet",
}


def _to_decimal(tok):
    """'1.98'->1.98 · '+105'/'-120'/'150'->american->decimal · None if not odds.
    Bare ints >=100 are american (decimal odds here live in 1.50-4.00)."""
    try:
        if tok.startswith(("+", "-")) and tok[1:].isdigit():
            am = int(tok)
            if abs(am) < 100:
                return None
            return 1 + am / 100 if am > 0 else 1 + 100 / -am
        v = float(tok)
    except ValueError:
        return None
    if v >= 100 and v == int(v):                    # unsigned american, e.g. "150"
        return 1 + v / 100
    return v if 1.0 < v < 100 else None


def _cashout_toks(toks):
    """Strip a leading cashout marker ('cashout' / 'cash out' / 'cashed out' /
    'co' / 캐시아웃); -> remaining tokens, or None if not a cashout reply."""
    first = toks[0].lower().rstrip(".,")
    if first in CASHOUT_WORDS:
        return toks[1:]
    if first in ("cash", "cashed") and len(toks) > 1 and \
            toks[1].lower().rstrip(".,") == "out":
        return toks[2:]
    return None


def parse_reply(text):
    """-> {'skip': True} | {'cashout': True, 'amount': float|None}
    | {'odds': float, 'book': str|None, 'stake': float|None}
    | None when unparseable. First number = odds, second = stake, word = book."""
    toks = text.replace("$", " ").replace("@", " ").split()
    if not toks:
        return None
    if toks[0].lower() in SKIP_WORDS:
        return {"skip": True}
    rest = _cashout_toks(toks)
    if rest is not None:
        amount = None
        for t in rest:
            try:
                amount = float(t.rstrip(".,"))
                break
            except ValueError:
                pass
        return {"cashout": True, "amount": amount}
    odds = stake = book = None
    for t in toks:
        low = t.lower().rstrip(".,")
        if low in BOOK_ALIASES:
            book = BOOK_ALIASES[low]
            continue
        d = _to_decimal(low)
        if d is not None and odds is None:
            odds = d
        elif odds is not None and stake is None:
            try:
                stake = float(low)
            except ValueError:
                pass
    if odds is None:
        return None
    return {"odds": odds, "book": book, "stake": stake}


def _migrate(fdb):
    """Pre-2026-06-12 real_fill lacks the cashout columns."""
    for col in ("cashed_out INTEGER DEFAULT 0", "cashout_amount REAL"):
        try:
            fdb.execute(f"ALTER TABLE real_fill ADD COLUMN {col}")
        except sqlite3.OperationalError:        # already there
            pass


def lookup_alert(adb, message_id):
    try:
        return adb.execute(
            "SELECT event_id, market, side, point, player, book, odds FROM high_alerted"
            " WHERE message_id=?", (message_id,)).fetchone()
    except sqlite3.OperationalError:    # pre-migration table (no message_id col yet)
        return None


def record_fill(fdb, message_id, alert, parsed, raw, now):
    """Upsert one reply; returns the ack text sent back to the user."""
    eid, market, side, point, player, abook, aodds = alert
    if parsed.get("skip"):
        fdb.execute(f"INSERT OR REPLACE INTO real_fill({FILL_COLS})"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (message_id, eid, market, side, point, player, abook, aodds,
                     None, None, None, 1, raw, now))
        fdb.commit()
        skips, fills = fdb.execute(
            "SELECT sum(skipped=1), sum(skipped=0) FROM real_fill").fetchone()
        return (f"✅ skip 기록 (노필) · 누적 skip {skips or 0} / 체결 {fills or 0}"
                f" → 노필률 {100 * (skips or 0) / ((skips or 0) + (fills or 0)):.0f}%")
    if parsed.get("cashout"):
        amount = parsed.get("amount")
        # UPDATE first so an earlier odds reply's fill data survives; a bet had
        # to exist to be cashed out, so a fresh row counts as filled (skipped=0)
        cur = fdb.execute(
            "UPDATE real_fill SET cashed_out=1, cashout_amount=?, skipped=0,"
            " raw_reply=?, replied_at=? WHERE message_id=?",
            (amount, raw, now, message_id))
        if cur.rowcount == 0:
            fdb.execute(f"INSERT INTO real_fill({FILL_COLS}, cashed_out, cashout_amount)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                        (message_id, eid, market, side, point, player, abook, aodds,
                         None, None, None, 0, raw, now, amount))
        fdb.commit()
        stake = fdb.execute("SELECT fill_stake FROM real_fill WHERE message_id=?",
                            (message_id,)).fetchone()[0]
        ack = "✅ 캐시아웃 기록"
        if amount is not None:
            ack += f": ${amount:.2f} 회수"
            if stake:
                ack += f" (스테이크 ${stake:.2f} → 손익 ${amount - stake:+.2f})"
        else:
            ack += ' — 회수 금액도 알려주면 좋아요. 예) "cashout 1.50"'
        n = fdb.execute("SELECT count(*) FROM real_fill WHERE cashed_out=1").fetchone()[0]
        return ack + f" · 누적 캐시아웃 {n}"
    book = parsed["book"] or abook
    # UPDATE-first keeps cashed_out/cashout_amount when correcting odds later
    cur = fdb.execute(
        "UPDATE real_fill SET fill_book=?, fill_odds=?, fill_stake=?, skipped=0,"
        " raw_reply=?, replied_at=? WHERE message_id=?",
        (book, parsed["odds"], parsed.get("stake"), raw, now, message_id))
    if cur.rowcount == 0:
        fdb.execute(f"INSERT INTO real_fill({FILL_COLS})"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (message_id, eid, market, side, point, player, abook, aodds,
                     book, parsed["odds"], parsed.get("stake"), 0, raw, now))
    fdb.commit()
    ack = f"✅ 기록: {book} @ {parsed['odds']:.2f}"
    if aodds:
        slip = (parsed["odds"] / aodds - 1) * 100
        ack += f" (알림 {abook} {aodds:.2f} → 슬리피지 {slip:+.1f}%)"
    n = fdb.execute("SELECT count(*) FROM real_fill WHERE skipped=0").fetchone()[0]
    return ack + f" · 누적 체결 {n}"


def stats_text(fdb):
    fills, skips, slip, couts, conet = fdb.execute(
        """SELECT sum(skipped=0), sum(skipped=1),
           avg(CASE WHEN skipped=0 AND alert_odds>0
               THEN 100.0*(fill_odds/alert_odds-1) END),
           sum(cashed_out=1),
           sum(CASE WHEN cashed_out=1 AND cashout_amount IS NOT NULL
               AND fill_stake IS NOT NULL THEN cashout_amount-fill_stake END)
           FROM real_fill""").fetchone()
    fills, skips = fills or 0, skips or 0
    if not fills and not skips:
        return "기록된 체결/스킵 없음 — High 알림에 답장하면 쌓입니다."
    out = f"🎯 실체결 {fills} · skip {skips} (노필률 {100*skips/(fills+skips):.0f}%)"
    if slip is not None:
        out += f"\n평균 슬리피지 {slip:+.2f}% (알림 배당 대비)"
    if couts:
        out += f"\n캐시아웃 {couts}건"
        if conet is not None:
            out += f" (확정 손익 ${conet:+.2f})"
    return out


def _api(tok, method, **params):
    data = urllib.parse.urlencode(params).encode()
    resp = urllib.request.urlopen(
        f"https://api.telegram.org/bot{tok}/{method}", data=data, timeout=70).read()
    return json.loads(resp)


def _read_env():
    tok = cho = None
    for line in open(ENV_PATH):
        if line.startswith("TELEGRAM_BOT_TOKEN="): tok = line.split("=", 1)[1].strip().strip('"')
        if line.startswith("TELEGRAM_CHAT_ID="): cho = line.split("=", 1)[1].strip().strip('"')
    return tok, cho


def handle_update(upd, adb, fdb, now):
    """-> ack text or None (ignore). Pure-ish for tests (no Telegram I/O).
    edited_message rides the same path: record_fill upserts on the alert's
    message_id, so editing a typo'd reply corrects the stored fill."""
    msg = upd.get("message") or upd.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    if not text:
        return None
    if text.lower() in ("fills", "slippage", "슬리피지", "체결"):
        return stats_text(fdb)
    reply = msg.get("reply_to_message") or {}
    if not reply.get("message_id"):
        # a fill-looking PLAIN message must not be silently ignored (real user
        # confusion 6/11: sent "Fanduel +106" without replying — unmappable)
        if parse_reply(text):
            return ("↩️ 어느 베팅인지 알 수 없어 기록 못 했어요 — 해당 High 알림"
                    " 메시지에 답장(Reply)으로 보내주세요"
                    " (메시지를 스와이프하거나 길게 눌러 Reply).")
        return None                                     # plain chat — not ours
    alert = lookup_alert(adb, reply["message_id"])
    if not alert:
        return "↩️ 이 메시지는 체결 추적 대상이 아니에요 — 베팅별 High 알림에 답장해주세요."
    parsed = parse_reply(text)
    if not parsed:
        return ('형식을 못 읽었어요. 예) "1.98" · "1.98 fd" · "+105 dk 5" · "skip"'
                ' · "cashout 1.50"')
    return record_fill(fdb, reply["message_id"], alert, parsed, text, now)


def _persist_offset(fdb, offset):
    """Best-effort: the :05/:25/:45 place timer can hold softbook_paper.db's
    write lock for minutes (settle() spans network calls) — never die on it;
    an unpersisted offset just re-delivers updates (record_fill is an upsert)."""
    try:
        fdb.execute("INSERT OR REPLACE INTO tg_state VALUES ('offset', ?)", (str(offset),))
        fdb.commit()
        return True
    except sqlite3.OperationalError as e:
        print("offset persist deferred:", e)
        return False


def main():
    from datetime import datetime, timezone
    tok, cho = _read_env()
    if not (tok and cho):
        print("no TELEGRAM_BOT_TOKEN/CHAT_ID"); sys.exit(1)
    fdb = sqlite3.connect(FILLS_DB, timeout=60)
    fdb.execute(FILL_SCHEMA); fdb.execute(STATE_SCHEMA)
    _migrate(fdb); fdb.commit()
    row = fdb.execute("SELECT v FROM tg_state WHERE k='offset'").fetchone()
    offset = int(row[0]) if row else None
    while offset is None:               # first boot: drain FULL backlog (paged
        try:                            # 100/call), process none of it
            skipped, off = 0, 0
            while True:
                upds = _api(tok, "getUpdates", offset=off, timeout=0).get("result", [])
                if not upds:
                    break
                skipped += len(upds)
                off = upds[-1]["update_id"] + 1
            offset = off
            print(f"first boot: skipped {skipped} backlog updates")
        except Exception as e:
            print("first-boot drain err:", e); time.sleep(5)
    _persist_offset(fdb, offset)
    print(f"listening (offset {offset})")
    retries = {}                        # update_id -> attempts (transient-failure replay)
    while True:
        try:
            upds = _api(tok, "getUpdates", offset=offset, timeout=50,
                        allowed_updates='["message","edited_message"]').get("result", [])
        except urllib.error.HTTPError as e:
            if e.code == 409:           # second getUpdates consumer is running!
                print("CONFLICT 409: another getUpdates consumer holds this bot"
                      " — fix that; backing off 30s")
                time.sleep(30); continue
            print("getUpdates err:", e); time.sleep(5); continue
        except Exception as e:
            print("getUpdates err:", e); time.sleep(5); continue
        for upd in upds:
            uid = upd["update_id"]
            try:
                msg = upd.get("message") or upd.get("edited_message") or {}
                if str(msg.get("chat", {}).get("id")) == str(cho):
                    adb = sqlite3.connect(f"file:{ALERTS_DB}?mode=ro", uri=True, timeout=15)
                    try:
                        ack = handle_update(upd, adb, fdb,
                                            datetime.now(timezone.utc).isoformat())
                    finally:
                        adb.close()
                    if ack:
                        _api(tok, "sendMessage", chat_id=cho, text=ack,
                             reply_to_message_id=msg.get("message_id"),
                             allow_sending_without_reply="true")
            except Exception as e:
                # transient (locked db / network)? replay this update next poll
                # instead of silently dropping the user's reply — up to 3 tries.
                retries[uid] = retries.get(uid, 0) + 1
                print(f"handle err (try {retries[uid]}):", e)
                if retries[uid] < 3:
                    time.sleep(2)
                    break               # don't advance past uid; getUpdates re-delivers
                retries.pop(uid, None)
            offset = uid + 1
        _persist_offset(fdb, offset)


if __name__ == "__main__":
    main()
