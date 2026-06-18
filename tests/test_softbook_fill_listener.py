"""Real-fill capture chain — added 2026-06-11.

① recorder: per-bet High alerts store book/odds/message_id on high_alerted
   (legacy 6-col table auto-migrates) + ✂️ min-odds line in the alert text.
② listener: reply parsing (decimal/american odds, book alias, stake, skip),
   reply→bet resolution via message_id, upsert-on-correction, no-fill stats.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_ev_recorder as rec                             # noqa: E402
import softbook_fill_listener as fl                            # noqa: E402

NOW = "2026-06-11T15:00:00+00:00"

BET = {"event_id": "E1", "market": "total", "side": "Under", "point": 169.5,
       "player": "", "home": "Phoenix Mercury", "away": "Dallas Wings",
       "label": "Under 169.5", "book": "BetMGM", "odds": 2.06, "ev": 0.04,
       "n_ev": 4, "n_books": 5, "tier": "High",
       "sport": "basketball_wnba", "commence_time": "2026-06-12T01:10:00Z"}


def alerts_db(legacy=False):
    db = sqlite3.connect(":memory:")
    if legacy:                       # pre-migration 6-col table from production
        db.execute("""CREATE TABLE high_alerted(
            event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT,
            alerted_at TEXT, PRIMARY KEY (event_id, market, side, point, player))""")
    return db


# ---------- ① recorder side ----------

def test_alert_stores_book_odds_message_id(monkeypatch):
    db = alerts_db()
    sent = []
    monkeypatch.setattr(rec, "send_telegram", lambda t: sent.append(t) or 777)
    monkeypatch.setattr(rec.time, "sleep", lambda s: None)
    assert rec.alert_new_high(db, [BET], NOW) == 1
    row = db.execute("SELECT book, odds, message_id FROM high_alerted").fetchone()
    assert row == ("BetMGM", 2.06, 777)
    assert len(sent) == 1                          # one message per bet
    assert "✂️ 최소 +100" in sent[0]               # 1.01/fair=2.0006 -> +100 (american)
    assert "본전 -102" in sent[0]                    # 1/fair=1.9808 -> -102
    assert "답장" in sent[0]


def test_alert_migrates_legacy_table(monkeypatch):
    db = alerts_db(legacy=True)
    db.execute("INSERT INTO high_alerted VALUES ('E0','h2h','X',0,'','t0')")
    monkeypatch.setattr(rec, "send_telegram", lambda t: 5)
    monkeypatch.setattr(rec.time, "sleep", lambda s: None)
    assert rec.alert_new_high(db, [BET], NOW) == 1   # ALTER TABLE path, no crash
    assert db.execute("SELECT message_id FROM high_alerted WHERE event_id='E1'"
                      ).fetchone() == (5,)
    assert db.execute("SELECT message_id FROM high_alerted WHERE event_id='E0'"
                      ).fetchone() == (None,)


def test_alert_dedup_unchanged(monkeypatch):
    db = alerts_db()
    monkeypatch.setattr(rec, "send_telegram", lambda t: 1)
    monkeypatch.setattr(rec.time, "sleep", lambda s: None)
    assert rec.alert_new_high(db, [BET], NOW) == 1
    assert rec.alert_new_high(db, [BET], NOW) == 0   # exact line alerts once


def test_min_odds_line_math():
    # fair = 1.04/2.06 = 0.50485; min = 1.01/fair = 2.0006 -> +100; be = 1/fair = 1.9808 -> -102
    line = rec._min_odds_line(0.04, 2.06)
    assert "최소 +100" in line and "본전 -102" in line


def test_failed_send_unmarks_for_retry(monkeypatch):
    """Transient Telegram failure must NOT permanently silence the bet: the
    dedup row is deleted so the next 20-min scan re-alerts."""
    db = alerts_db()
    monkeypatch.setattr(rec, "send_telegram", lambda t: None)   # send fails
    monkeypatch.setattr(rec.time, "sleep", lambda s: None)
    assert rec.alert_new_high(db, [BET], NOW) == 1
    assert db.execute("SELECT count(*) FROM high_alerted").fetchone() == (0,)
    monkeypatch.setattr(rec, "send_telegram", lambda t: 42)     # next scan: ok
    assert rec.alert_new_high(db, [BET], NOW) == 1
    assert db.execute("SELECT message_id FROM high_alerted").fetchone() == (42,)


# ---------- ② listener: parsing ----------

def test_parse_decimal_with_book_and_stake():
    assert fl.parse_reply("1.98 fd 5") == {"odds": 1.98, "book": "FanDuel", "stake": 5.0}


def test_parse_bare_decimal():
    assert fl.parse_reply("2.1") == {"odds": 2.1, "book": None, "stake": None}


def test_parse_american():
    assert fl.parse_reply("+105 dk")["odds"] == 2.05
    assert abs(fl.parse_reply("-120")["odds"] - 1.8333) < 1e-3
    assert fl.parse_reply("150")["odds"] == 2.5      # unsigned american


def test_parse_book_first_and_at_sign():
    assert fl.parse_reply("fd @1.98") == {"odds": 1.98, "book": "FanDuel", "stake": None}


def test_parse_fullname_book_american():
    assert fl.parse_reply("Fanduel +106") == {"odds": 2.06, "book": "FanDuel", "stake": None}


def test_parse_skip_words():
    for w in ("skip", "SKIP", "스킵", "패스", "x"):
        assert fl.parse_reply(w) == {"skip": True}


def test_parse_garbage_is_none():
    assert fl.parse_reply("good game") is None
    assert fl.parse_reply("") is None


def test_parse_cashout_variants():
    for t in ("cashout 1.50", "cash out 1.50", "cashed out $1.50",
              "CASHOUT 1.50", "캐시아웃 1.50", "co 1.50"):
        assert fl.parse_reply(t) == {"cashout": True, "amount": 1.5}, t


def test_parse_cashout_without_amount():
    assert fl.parse_reply("cashed out") == {"cashout": True, "amount": None}
    assert fl.parse_reply("cashout") == {"cashout": True, "amount": None}


def test_parse_cashout_number_not_mistaken_for_odds():
    # the 6/12 bug this feature closes: "cashed out 1.50" must never be
    # recorded as a fill @ 1.50
    p = fl.parse_reply("cashed out 1.50")
    assert "odds" not in p


# ---------- ② listener: reply handling ----------

def fill_db():
    db = sqlite3.connect(":memory:")
    db.execute(fl.FILL_SCHEMA)
    db.execute(fl.STATE_SCHEMA)
    return db


def seeded_alerts():
    db = alerts_db()
    rec._ensure_alert_cols(db)
    db.execute("INSERT INTO high_alerted(event_id,market,side,point,player,"
               "alerted_at,book,odds,message_id) VALUES "
               "('E1','total','Under',169.5,'',?, 'BetMGM',2.06,777)", (NOW,))
    return db


def _upd(text, reply_to=777):
    m = {"chat": {"id": 1}, "message_id": 9000, "text": text}
    if reply_to:
        m["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": 1, "message": m}


def test_fill_reply_records_and_acks_slippage():
    adb, fdb = seeded_alerts(), fill_db()
    ack = fl.handle_update(_upd("1.98 fd"), adb, fdb, NOW)
    assert "FanDuel @ 1.98" in ack and "-3.9%" in ack
    row = fdb.execute("SELECT fill_book, fill_odds, skipped, alert_odds"
                      " FROM real_fill").fetchone()
    assert row == ("FanDuel", 1.98, 0, 2.06)


def test_fill_default_book_is_alert_book():
    adb, fdb = seeded_alerts(), fill_db()
    fl.handle_update(_upd("2.02"), adb, fdb, NOW)
    assert fdb.execute("SELECT fill_book FROM real_fill").fetchone() == ("BetMGM",)


def test_reply_again_overwrites_typo():
    adb, fdb = seeded_alerts(), fill_db()
    fl.handle_update(_upd("1.89"), adb, fdb, NOW)
    fl.handle_update(_upd("1.98"), adb, fdb, NOW)
    rows = fdb.execute("SELECT fill_odds FROM real_fill").fetchall()
    assert rows == [(1.98,)]                        # upsert, not duplicate


def test_skip_reply_counts_no_fill():
    adb, fdb = seeded_alerts(), fill_db()
    ack = fl.handle_update(_upd("skip"), adb, fdb, NOW)
    assert "skip 기록" in ack
    assert fdb.execute("SELECT skipped FROM real_fill").fetchone() == (1,)


def test_reply_to_unknown_message_hints():
    adb, fdb = seeded_alerts(), fill_db()
    ack = fl.handle_update(_upd("1.98", reply_to=123), adb, fdb, NOW)
    assert "추적 대상이 아니" in ack
    assert fdb.execute("SELECT count(*) FROM real_fill").fetchone() == (0,)


def test_unparseable_reply_gets_help():
    adb, fdb = seeded_alerts(), fill_db()
    ack = fl.handle_update(_upd("dunno"), adb, fdb, NOW)
    assert "형식" in ack


def test_plain_chat_message_ignored():
    adb, fdb = seeded_alerts(), fill_db()
    assert fl.handle_update(_upd("hello", reply_to=None), adb, fdb, NOW) is None


def test_plain_fill_message_gets_reply_guidance():
    """Fill-looking message WITHOUT replying — guide, don't silently drop."""
    adb, fdb = seeded_alerts(), fill_db()
    ack = fl.handle_update(_upd("Fanduel +106", reply_to=None), adb, fdb, NOW)
    assert ack and "답장" in ack
    assert fdb.execute("SELECT count(*) FROM real_fill").fetchone() == (0,)


def test_edited_reply_corrects_fill():
    """User edits a typo'd reply in place — edited_message rides the same upsert."""
    adb, fdb = seeded_alerts(), fill_db()
    fl.handle_update(_upd("1.89"), adb, fdb, NOW)
    edited = {"update_id": 2, "edited_message": {
        "chat": {"id": 1}, "message_id": 9000, "text": "1.98",
        "reply_to_message": {"message_id": 777}}}
    ack = fl.handle_update(edited, adb, fdb, NOW)
    assert "1.98" in ack
    assert fdb.execute("SELECT fill_odds FROM real_fill").fetchall() == [(1.98,)]


def test_cashout_after_fill_keeps_fill_data():
    """Bet first ("2.05 5"), opposite side alerts later, user cashes out —
    fill odds/stake must survive and the row stays a fill (skipped=0)."""
    adb, fdb = seeded_alerts(), fill_db()
    fl.handle_update(_upd("2.05 5"), adb, fdb, NOW)
    ack = fl.handle_update(_upd("cashout 4.20"), adb, fdb, NOW)
    assert "캐시아웃" in ack and "$4.20" in ack and "-0.80" in ack  # 4.20-5.00
    row = fdb.execute("SELECT fill_odds, fill_stake, skipped, cashed_out,"
                      " cashout_amount FROM real_fill").fetchone()
    assert row == (2.05, 5.0, 0, 1, 4.2)


def test_cashout_without_prior_fill_counts_as_filled():
    adb, fdb = seeded_alerts(), fill_db()
    ack = fl.handle_update(_upd("cash out"), adb, fdb, NOW)
    assert "캐시아웃" in ack and "회수 금액" in ack       # nudges for the amount
    row = fdb.execute("SELECT skipped, cashed_out, cashout_amount, fill_odds"
                      " FROM real_fill").fetchone()
    assert row == (0, 1, None, None)


def test_cashout_amount_added_by_second_reply():
    adb, fdb = seeded_alerts(), fill_db()
    fl.handle_update(_upd("cashout"), adb, fdb, NOW)
    fl.handle_update(_upd("cashout 1.50"), adb, fdb, NOW)
    rows = fdb.execute("SELECT cashed_out, cashout_amount FROM real_fill").fetchall()
    assert rows == [(1, 1.5)]


def test_odds_correction_after_cashout_keeps_cashout():
    adb, fdb = seeded_alerts(), fill_db()
    fl.handle_update(_upd("1.89"), adb, fdb, NOW)
    fl.handle_update(_upd("cashout 1.50"), adb, fdb, NOW)
    fl.handle_update(_upd("1.98"), adb, fdb, NOW)       # typo fix on the fill
    row = fdb.execute("SELECT fill_odds, cashed_out, cashout_amount"
                      " FROM real_fill").fetchone()
    assert row == (1.98, 1, 1.5)


def test_migrate_adds_cashout_cols_to_legacy_db():
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE real_fill(
        message_id INTEGER PRIMARY KEY,
        event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT,
        alert_book TEXT, alert_odds REAL,
        fill_book TEXT, fill_odds REAL, fill_stake REAL,
        skipped INTEGER DEFAULT 0, raw_reply TEXT, replied_at TEXT)""")
    db.execute("INSERT INTO real_fill(message_id, skipped) VALUES (1, 0)")
    fl._migrate(db)
    fl._migrate(db)                                     # idempotent
    assert db.execute("SELECT cashed_out, cashout_amount FROM real_fill"
                      ).fetchone() == (0, None)


def test_stats_include_cashouts():
    adb, fdb = seeded_alerts(), fill_db()
    fl.handle_update(_upd("2.05 5"), adb, fdb, NOW)
    fl.handle_update(_upd("cashout 4.20"), adb, fdb, NOW)
    out = fl.handle_update(_upd("fills", reply_to=None), adb, fdb, NOW)
    assert "캐시아웃 1건" in out and "$-0.80" in out


def test_stats_command():
    adb, fdb = seeded_alerts(), fill_db()
    fl.handle_update(_upd("1.98"), adb, fdb, NOW)
    out = fl.handle_update(_upd("fills", reply_to=None), adb, fdb, NOW)
    assert "실체결 1" in out and "슬리피지" in out


# ---------- ② listener: bonus-bet tagging (2026-06-16) ----------

def test_parse_bonus_token():
    p = fl.parse_reply("3.8 ts bonus")
    assert p["odds"] == 3.8 and p["book"] == "theScore Bet" and p.get("bonus") is True
    assert fl.parse_reply("보너스 +400 bally")["bonus"] is True
    # a normal reply carries NO bonus key (backward-compatible shape)
    assert "bonus" not in fl.parse_reply("1.98 fd")


def test_bonus_fill_tagged_and_acked():
    adb, fdb = seeded_alerts(), fill_db()
    ack = fl.handle_update(_upd("3.8 ts bonus"), adb, fdb, NOW)
    assert "보너스" in ack and "엣지 측정 제외" in ack
    assert "슬리피지" not in ack                      # bonus bets don't report slippage
    assert fdb.execute("SELECT bonus FROM real_fill").fetchone() == (1,)


def test_bonus_excluded_from_edge_stats():
    adb, fdb = seeded_alerts(), fill_db()
    fl.handle_update(_upd("1.98 fd"), adb, fdb, NOW)            # edge fill
    # a bonus fill on a different alert (distinct message_id)
    adb.execute("INSERT INTO high_alerted(event_id,market,side,point,player,"
                "alerted_at,book,odds,message_id) VALUES "
                "('E2','h2h','X',0,'',?, 'FanDuel',3.50,778)", (NOW,))
    fl.handle_update(_upd("3.5 fd bonus", reply_to=778), adb, fdb, NOW)
    out = fl.handle_update(_upd("fills", reply_to=None), adb, fdb, NOW)
    assert "실체결 1" in out                                    # only the edge fill counted
    assert "보너스 베팅 1건" in out
    # slippage averages ONLY the edge fill (1.98 vs 2.06 = -3.88%); if the bonus
    # (3.5 vs 3.50 = 0%) leaked in, the mean would be -1.94%.
    assert "-3.88%" in out


def test_bonus_tag_persists_on_odds_correction():
    adb, fdb = seeded_alerts(), fill_db()
    fl.handle_update(_upd("3.8 ts bonus"), adb, fdb, NOW)
    fl.handle_update(_upd("3.85 ts"), adb, fdb, NOW)           # plain correction, no token
    row = fdb.execute("SELECT fill_odds, bonus FROM real_fill").fetchone()
    assert row == (3.85, 1)                                    # tag survives


def test_migrate_adds_bonus_col_to_legacy_db():
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE real_fill(
        message_id INTEGER PRIMARY KEY,
        event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT,
        alert_book TEXT, alert_odds REAL,
        fill_book TEXT, fill_odds REAL, fill_stake REAL,
        skipped INTEGER DEFAULT 0, raw_reply TEXT, replied_at TEXT)""")
    db.execute("INSERT INTO real_fill(message_id, skipped) VALUES (1, 0)")
    fl._migrate(db)
    fl._migrate(db)                                            # idempotent
    assert db.execute("SELECT bonus FROM real_fill").fetchone() == (0,)
