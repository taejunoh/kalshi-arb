#!/usr/bin/env python3
"""+EV-betting (soft-book vs sharp Pinnacle) forward CLV recorder — FULL markets.

Records, several times a day, the strategy's actual +EV bets across MONEYLINE,
SPREAD and TOTAL (best soft-book bet per game+market, vs Pinnacle's same-line
sharp price — see softbook_ev_core.py) so softbook_ev_measure.py can later verify
CLOSING-LINE VALUE (did these +EV bets beat the Pinnacle close?). NO BETTING —
record only. Run via kalshi-softbook-ev.timer.
"""
import json, os, sqlite3, time, urllib.request, urllib.parse
from datetime import datetime, timezone
import softbook_ev_core as core

OUT = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")
QUIET_UTC = core.QUIET_UTC         # 22:00-05:00 NYC: no pushes while user sleeps
# Daily wake-summary fires at the first scan >= WAKE_UTC. Default 08:30 UTC =
# 04:30 America/New_York (EDT). DST: set to 09:30 in EST (Nov-Mar). Env-overridable.
WAKE_UTC = os.getenv("SOFTBOOK_WAKE_UTC", "08:30")
# Flat per-bet stake ($) shown on BET picks — mirrors the app's unit (build#11
# eb7fcac: DEFAULT_UNIT_STAKE=20, "bet small / NO size-up, SAME unit on every
# pick", NOT EV-scaled). Env-overridable so this channel tracks the app default.
# SKIP picks (props / late-crossers) show NO amount — the app hides it too; the
# BET/SKIP tag (_first_entry_note) is the single source of which picks we stake.
# Since 2026-07-22 SKIP picks are ALSO silent on Telegram (alert_new_high skips the
# send) — only game first-entry BETs (incl. WATCH advisories) message; SKIPs stay
# recorded for dedup/context/ledger. A transient note miss ("") still sends.
UNIT_STAKE = float(os.getenv("SOFTBOOK_UNIT_STAKE", "20"))
BET_TAG = "🟣 BET · first-entry game — 베팅 (bet small)"
PROP_SKIP_TAG = "⚪ SKIP · prop (게임 first-entry만 베팅)"
LATE_CROSSER_SKIP_TAG = "⚪ SKIP · late-crosser game (라인 이동 후)"
SKIP_TAGS = (PROP_SKIP_TAG, LATE_CROSSER_SKIP_TAG)


def _is_bet(db, event_id, market, side, player, tier):
    """True iff (game first-entry) — the only picks we stake. Mirrors core.is_bet
    over market_first_entry_sides; missing snapshot / lookup error → False (never
    claim a stake on an unverified pick)."""
    try:
        seen, sides = core.market_first_entry_sides(db, event_id, market, player or "")
    except sqlite3.Error:
        return False
    return bool(seen) and core.is_bet(side in sides, market, tier)

SCHEMA = """CREATE TABLE IF NOT EXISTS softbook_ev(
    recorded_at TEXT, sport TEXT, event_id TEXT, commence_time TEXT,
    home TEXT, away TEXT, market TEXT, side TEXT, point REAL, player TEXT,
    pinnacle_fair_prob REAL, pinnacle_price REAL,
    best_soft_book TEXT, best_soft_price REAL, snapshot_ev REAL,
    n_books INTEGER, n_ev INTEGER, conf REAL, tier TEXT,
    UNIQUE(recorded_at, event_id, market, side, point, player))"""

ALERT_SCHEMA = """CREATE TABLE IF NOT EXISTS high_alerted(
    event_id TEXT, market TEXT, side TEXT, point REAL, player TEXT, alerted_at TEXT,
    PRIMARY KEY (event_id, market, side, point, player))"""

WAKEUP_SCHEMA = "CREATE TABLE IF NOT EXISTS wakeup_sent(day TEXT PRIMARY KEY)"


def send_telegram(text):
    """Send; returns the Telegram message_id (int) or None. The fill listener
    (softbook_fill_listener.py) maps user replies back to bets via this id."""
    tok = cho = None
    try:
        for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
            if line.startswith("TELEGRAM_BOT_TOKEN="): tok = line.split("=", 1)[1].strip().strip('"')
            if line.startswith("TELEGRAM_CHAT_ID="): cho = line.split("=", 1)[1].strip().strip('"')
    except Exception:
        return None
    if not (tok and cho):
        return None
    try:
        data = urllib.parse.urlencode({"chat_id": cho, "text": text}).encode()
        resp = urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10).read()
        return json.loads(resp)["result"]["message_id"]
    except Exception:
        return None


def _telegram_call(method, payload):
    tok = _env_read("TELEGRAM_BOT_TOKEN")
    cho = _env_read("TELEGRAM_CHAT_ID")
    if not (tok and cho):
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": cho, **payload}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/{method}", data=data, timeout=10).read()
        return True
    except Exception:
        return False


def _env_read(key):
    try:
        for line in open(os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        return None
    return None


def mark_bet_done(db, event_id, market, side, point, player, note):
    """Stamp the original Telegram alert as already-bet.

    2026-08-06: the user bet a pick at 1:30 PM (Caesars, marked bet in the app)
    and again at 2:20 PM at DraftKings, at a worse price. Nothing was sent at
    2:20 — the second bet came from the 1:30 Telegram message, which sits in
    chat history looking actionable forever. The app hides a bet-marked pick;
    the message that pitched it never changes.

    So on bet-marking, the original message gets "✅ 베팅 완료" prepended via
    editMessageText (alert_text holds the sent body — an edit replaces the whole
    text, so without it we could only annotate). Rows from before alert_text
    existed fall back to a reply anchored under the message. The REPLY_HINT is
    kept in the edited body on purpose: replying with the fill odds is exactly
    what the fill listener still wants.

    Best-effort by contract: returns False rather than raising — this runs
    inside the app's POST /bets, and a Telegram hiccup must never fail the
    user's bet recording."""
    try:
        row = db.execute(
            "SELECT message_id, alert_text FROM high_alerted WHERE event_id=?"
            " AND market=? AND side=? AND COALESCE(point,0)=?"
            " AND COALESCE(player,'')=?",
            (event_id, market, side, float(point or 0), player or "")).fetchone()
    except sqlite3.Error:
        return False
    if not row or row[0] is None:
        return False
    mid, text = row
    banner = f"✅ 베팅 완료 — {note}"
    if text:
        return _telegram_call("editMessageText",
                              {"message_id": mid, "text": f"{banner}\n\n{text}"})
    return _telegram_call("sendMessage",
                          {"text": banner, "reply_to_message_id": mid,
                           "allow_sending_without_reply": "true"})


def _ensure_alert_cols(db):
    """One-time migrate: book/odds at alert time (slippage baseline) + the
    Telegram message_id so the fill listener can resolve replies to exact bets.
    'duplicate column' is swallowed: refresh + digest units both run this and
    can race the first post-deploy ALTER."""
    db.execute(ALERT_SCHEMA)
    cols = {r[1] for r in db.execute("PRAGMA table_info(high_alerted)")}
    for col, typ in (("book", "TEXT"), ("odds", "REAL"), ("message_id", "INTEGER"),
                     ("alert_text", "TEXT")):
        if col not in cols:
            try:
                db.execute(f"ALTER TABLE high_alerted ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e):
                    raise


def _american(dec):
    """Decimal odds -> the American number US books actually display on screen
    (+underdog / -favorite), so the alert matches the sportsbook 1:1 for instant
    eyeballing. The reply parser already accepts American back (no decimal needed):
    softbook_fill_listener._to_decimal handles '+290'/'-110'/bare ints."""
    if not dec or dec <= 1:
        return "-"
    a = round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))
    return f"{a:+d}"


def _min_odds_line(ev, odds):
    """Floor odds for taking this bet at ANY book: EV scales ~1:1 with the odds,
    so below min the user's ROI>=1% bar is gone even though the alert said +EV.
    Bars come from core.odds_bars so the alert and the app card can't drift."""
    bars = core.odds_bars(ev, odds)
    if bars is None:
        return ""
    mn, be = bars
    return (f"   ✂️ 최소 {_american(mn)} · 본전 {_american(be)}"
            " (어느 북이든 최소 위면 OK)")


REPLY_HINT = '↩️ 답장: 체결배당 [북] (예: -110 fd) · 안 했으면 "skip" · 보너스벳이면 "bonus"'


def _other_books_line(b):
    """Top other-book quotes by odds. The ★ book is best BETTABLE (EV<=cap), so a
    book can beat it on raw price yet be hidden by the stale-line EV cap — the
    user shops books live, so show those prices instead of burying them."""
    others = sorted((q for q in b.get("books_all", []) if q["book"] != b["book"]),
                    key=lambda q: -q["odds"])[:3]
    if not others:
        return ""
    return "\n   📚 " + " · ".join(f"{q['book']} {_american(q['odds'])}" for q in others)


def _first_entry_note(db, b):
    """Tag the alert BET vs SKIP. Rule = FIRST-ENTRY GAME ONLY (user, 2026-06-16).

    A pick is 'first-entry' if THIS SIDE was already High at the (event, market, player)'s
    earliest recorded snapshot (a standing mispricing on this side); a 'late-crosser' only
    drifted into High after the line moved — INCLUDING the opposite side that crosses after
    the total/spread steams (side-aware since 2026-06-17; see core.market_first_entry_sides).
    BET iff (game [ML/spread/total] AND first-entry); SKIP else (all props, all late-crossers).

    This is a CONTRACTION of the first-entry-only discipline, not an edge claim. The
    6/15 re-measure (independent + adversarial) found NO confirmed pick edge: every
    cut ≈ breakeven, Pikkit realized −4.6% governs, and the seductive cells were
    artifacts (late-crosser-props was ~90% one 6/14 slate; first-entry's only + cell
    was one chalk spread). Props are dropped because first-entry props ran negative
    and the late-crosser-prop "edge" was a single-day cluster — carving them out is
    safe (it removes volume, it does not chase a phantom). Real +EV lives in signup
    BONUSES, not the picks; these ≈BE game bets are the low-variance vehicle to clear
    them. No size increase until forward CLV confirms an edge.

    Looks up the High sides at the (event, market, player)'s earliest snapshot, then
    matches THIS side; record() persists this scan's snapshot before alert_new_high.
    Non-critical: any lookup error / missing snapshot → "" (the alert still fires untagged
    — never mislabel a pick on a transient miss)."""
    try:
        seen, sides = core.market_first_entry_sides(
            db, b["event_id"], b["market"], b.get("player") or "")
    except sqlite3.Error:
        return ""
    if not seen:
        return ""
    first_entry = b["side"] in sides                      # side-aware (single source)
    # .get: a bet dict missing its tier degrades to SKIP rather than raising —
    # same policy as a missing snapshot, never mislabel on a transient miss.
    if core.is_bet(first_entry, b["market"], b.get("tier")):   # single source
        return BET_TAG
    if b["market"] in core.PROP_SHORT:
        return PROP_SKIP_TAG
    return LATE_CROSSER_SKIP_TAG


def _send_bet_alert(db, b, prefix, extra="", batch=(), fe=None):
    """One Telegram message per bet (NOT batched: a reply must identify exactly
    one bet) and stamp book/odds/message_id onto its high_alerted row.
    On a failed send the dedup row is DELETED so the next 20-min scan retries —
    otherwise one transient Telegram error would silence that bet forever AND
    leave message_id NULL (untrackable fill). Commits per bet: the write txn
    must not span the next send's HTTP round-trip (refresh + digest units can
    overlap on this root-owned db at digest hours).
    fe: precomputed BET/SKIP tag (alert_new_high passes it after gating so it is
    not looked up twice); recomputed here when the caller omits it."""
    pk = (b["event_id"], b["market"], b["side"], b["point"] or 0, b.get("player") or "")
    if fe is None:
        fe = _first_entry_note(db, b)
    stake_seg = f" · 💵 ${UNIT_STAKE:.0f}" if fe == BET_TAG else ""   # bet picks only (app parity)
    text = (f"{prefix}\n{fe}\n\n" if fe else f"{prefix}\n\n") + (
            f"• {b['away'][:16]} @ {b['home'][:16]}\n"
            f"   {b['label']}\n"
            f"   📍 {b['book']} @ {_american(b['odds'])}{stake_seg}\n"
            f"   (EV {b['ev']*100:+.1f}%, {b['n_ev']}/{b['n_books']}bk)"
            + _other_books_line(b) + "\n"
            + _min_odds_line(b["ev"], b["odds"])
            + extra + _dup_note(db, b) + _opposite_note(db, b, batch)
            + f"\n\n{REPLY_HINT}")
    mid = send_telegram(text)
    if mid is None:
        db.execute("DELETE FROM high_alerted WHERE event_id=? AND market=?"
                   " AND side=? AND point=? AND player=?", pk)
    else:
        # alert_text is kept so mark_bet_done can EDIT this message later —
        # Telegram's editMessageText replaces the whole body, so without the
        # original we could only reply, not stamp.
        db.execute("UPDATE high_alerted SET book=?, odds=?, message_id=?, alert_text=?"
                   " WHERE event_id=? AND market=? AND side=? AND point=? AND player=?",
                   (b["book"], b["odds"], mid, text, *pk))
    db.commit()
    time.sleep(1.05)                     # Telegram per-chat rate: ~1 msg/s
    return mid


def _dup_note(db, b):
    """If this game+market(+player) was already High-alerted at other side/points,
    return a warning line: it's the SAME bet exposure (1/game/market), with the
    FULL chain of previously alerted lines in order (so line movement is visible
    at a glance) plus whether the new line beats the FIRST alert (the one most
    likely bet). A moved line re-alerts as a new bet, so without this the alert
    looks like a fresh opportunity when it's really a re-quote of one you may hold."""
    prior = db.execute(
        "SELECT side, point FROM high_alerted WHERE event_id=? AND market=? AND player=?"
        " AND NOT (side=? AND point=?) ORDER BY alerted_at",
        (b["event_id"], b["market"], b.get("player") or "",
         b["side"], b["point"] or 0)).fetchall()
    if not prior:
        return ""
    first_side, first_point = prior[0]
    same_side = all(s == b["side"] for s, _ in prior)
    if same_side and b["market"] != "h2h":     # compact chain: side once, points only
        chain = (f"{core.label(b['market'], b['side'], prior[0][1], b.get('player'))} → "
                 + " → ".join(f"{p:+g}" if b["market"] == "spread" else f"{p:g}"
                              for _, p in prior[1:] + [(None, b["point"] or 0)]))
    else:                                       # mixed sides (or ML): full labels
        chain = " → ".join(core.label(b["market"], s, p or None, b.get("player"))
                           for s, p in prior + [(b["side"], b["point"] or 0)])
    if first_side != b["side"]:
        # Opposite side: it crossed into High only after the line moved = a LATE-CROSSER
        # (is_first_entry is side-aware → this alert is already tagged SKIP). The first-entry
        # side was the standing edge; do NOT bet the opposite side and do NOT hedge (reverse
        # middle — a close game kills both). Pre-2026-06-17 this recommended "take the higher-EV
        # side"; the data refuted it (higher-EV opposite won 3/8; the first-entry side won 6/8).
        return (f"\n   ⚠️ 중복 {len(prior)+1}번째: {chain} — 처음과 방향 반대"
                f" (동시적중 불가, 사이 구간이면 둘 다 패). 라인 이동 후 넘어온 late-crosser →"
                f" 스킵; 베팅은 처음 사이드(first-entry)만, 헤지 금지")
    new_point = b["point"] or 0
    if b["side"] == "Under":
        better = new_point > first_point
    elif b["side"] == "Over":
        better = new_point < first_point
    else:                                       # spread: more cushion = better
        better = new_point > first_point
    verdict = "더 좋은 라인" if better else "더 나쁜 라인"
    return (f"\n   ⚠️ 중복 {len(prior)+1}번째: {chain} (처음보다 {verdict})"
            f" — 이미 베팅했다면 스킵 (같은 경기·마켓 1베팅)")


def _axis_dir(b):
    """Bet -> (axis, dir, thr): wins iff stat > thr (dir=+1) / stat < thr (-1).
    Margin axis: stat = home score − away score (h2h = spread at 0); O/U axis:
    stat = the total, keyed per market+player so props don't cross-match."""
    m, s = b["market"], b["side"]
    p = b.get("point") or 0.0
    if s in ("Over", "Under"):
        return ("ou", m, b.get("player") or ""), (1 if s == "Over" else -1), p
    if s == b["home"]:
        return ("margin",), 1, -p if m == "spread" else 0.0
    if s == b["away"]:
        return ("margin",), -1, p if m == "spread" else 0.0
    return None                                    # 3-way Draw etc — no axis


def _no_both_win(a, b):
    """True iff two bets on the SAME game can never both win — a reverse middle:
    win zones disjoint, with a both-lose gap between them (e.g. home -5.5 +
    away ML both lose on a 1-5pt home win)."""
    ax_a, ax_b = _axis_dir(a), _axis_dir(b)
    if not ax_a or not ax_b or ax_a[0] != ax_b[0] or ax_a[1] == ax_b[1]:
        return False
    gt_thr = ax_a[2] if ax_a[1] > 0 else ax_b[2]   # the "stat >" bet's threshold
    lt_thr = ax_b[2] if ax_a[1] > 0 else ax_a[2]   # the "stat <" bet's threshold
    return gt_thr >= lt_thr


def _opposite_note(db, b, batch=()):
    """Same-game opposite-direction High at a DIFFERENT market (h2h vs spread —
    best_per_market already keeps one side per market, and same-market opposites
    are _dup_note's job): both are individually +EV vs Pinnacle so this is no
    contradiction, but they can never both win and a close game kills both.
    Flag the pair and recommend keeping only the higher-EV side (user's rule,
    2026-06-12). Sources: this scan's batch (has live EV) + previously alerted
    lines for the event (EV looked up from their latest High snapshot)."""
    others = {}
    for m, s, p, pl, bk_, od in db.execute(
            "SELECT market, side, COALESCE(point,0), player, book, odds"
            " FROM high_alerted WHERE event_id=? AND market != ?",
            (b["event_id"], b["market"])):
        tiers = sorted(core.bet_tiers(m))            # the tier THAT market alerts at
        ev_row = db.execute(
            "SELECT snapshot_ev FROM softbook_ev WHERE event_id=? AND market=?"
            " AND side=? AND COALESCE(point,0)=? AND COALESCE(player,'')=?"
            f" AND tier IN ({','.join('?' * len(tiers))})"
            " ORDER BY recorded_at DESC LIMIT 1",
            (b["event_id"], m, s, p, pl, *tiers)).fetchone()
        others[(m, s, p, pl)] = (bk_, od, ev_row[0] if ev_row else None)
    for o in batch:
        if o["event_id"] == b["event_id"] and o["market"] != b["market"]:
            others[(o["market"], o["side"], o["point"] or 0,
                    o.get("player") or "")] = (o["book"], o["odds"], o["ev"])
    notes = []
    for (m, s, p, pl), (bk_, od, ev_o) in others.items():
        other = {"market": m, "side": s, "point": p, "player": pl,
                 "home": b["home"], "away": b["away"]}
        if not _no_both_win(b, other):
            continue
        lbl = core.label(m, s, None if m == "h2h" else p, pl or None)
        quote = f" {bk_} {_american(od)}" if od else ""
        if ev_o is None:
            notes.append(f"\n   ⚖️ 같은 경기 반대방향 +EV: {lbl}{quote}"
                         " — 동시적중 불가(접전이면 둘 다 패), 둘 중 하나만 권장")
        else:
            pick = "이쪽" if b["ev"] >= ev_o else "반대쪽"
            notes.append(f"\n   ⚖️ 같은 경기 반대방향 +EV: {lbl}{quote}"
                         f" (EV {ev_o*100:+.1f}%) — 동시적중 불가(접전이면 둘 다 패),"
                         f" EV 높은 쪽만: {pick}")
    return "".join(notes[:2])


def actioned_picks():
    """{(event_id, market, side, point, player)} the user has already marked bet or
    dismissed in the app. READ-ONLY on the app db, like the push-token read below.

    Telegram and push know what the bet RULE says but not what the USER did, and
    on 2026-08-05 that gap surfaced: a Med ML line the user marked bet at 20:37
    alerted at 21:50 telling them to bet it. Not a dedup failure — it was that
    line's first alert (Med only started qualifying at ~19:00, so nothing earlier
    was ever recorded). Two channels each correct on their own rules, one wrong
    outcome. Moving ML to Med quadrupled alert volume, which is why it showed up
    now; the gap predates it.

    Any failure returns an empty set: an alert that should have been suppressed
    is a nuisance, a swallowed alert is a missed bet.
    """
    paper = os.getenv("SOFTBOOK_PAPER_DB",
                      "/home/ubuntu/kalshi-arb/data/softbook_paper.db")
    try:
        pdb = sqlite3.connect(f"file:{paper}?mode=ro", uri=True, timeout=15)
    except sqlite3.Error:
        return frozenset()
    try:
        return frozenset(
            (e, m, sd, float(pt or 0), pl or "") for e, m, sd, pt, pl in pdb.execute(
                "SELECT event_id, market, side, point, player FROM pick_state"
                " WHERE state IN ('bet','dismissed')"))
    except sqlite3.Error:
        return frozenset()
    finally:
        pdb.close()


def alert_new_high(db, bets, now):
    """Push NEW High-tier bets to Telegram as they appear (the 4x/day digest's
    fixed times miss them — High clusters near game start). Dedup per exact bet
    (event+market+side+point+player) so each line alerts once; a moved line is a
    new bet and re-alerts. Fires 24/7 — NO sleep window: the user would rather
    be woken than miss a bet (2026-06-11, his call; ledger B books the trigger
    price, so the alert must fire at the trigger too)."""
    _ensure_alert_cols(db)
    new = []
    for b in bets:
        if b["tier"] not in core.bet_tiers(b["market"]):
            continue    # tier this market is bet at (single source; ML=Med, rest=High)
        cur = db.execute(
            "INSERT OR IGNORE INTO high_alerted(event_id,market,side,point,player,alerted_at)"
            " VALUES (?,?,?,?,?,?)",
            (b["event_id"], b["market"], b["side"], b["point"] or 0,
             b.get("player") or "", now))
        if cur.rowcount:
            new.append(b)
    db.commit()
    if not new:
        return 0
    new.sort(key=lambda b: -b["ev"])
    # Telegram only the BET picks (first-entry game; WATCH is a BET advisory and still
    # sends). SKIP picks (props, late-crossers) stay recorded above — dedup, dup/opposite
    # context, and the CLV/ledger trail — but no longer message (user, 2026-07-22). A
    # transient note miss ("") is NOT a SKIP tag, so it still sends: never silence a
    # possible bet on a lookup blip.
    # Nothing the user has already acted on: they marked it bet (telling them to
    # bet it again invites a double stake) or dismissed it (they said no once).
    done = actioned_picks()
    # ...and nothing only ONE book quotes: those markets routinely exist in the
    # API but not in the book's app, so the alert points at nothing (core
    # .is_confirmable — recording above is untouched).
    to_send = [(b, fe) for b, fe in ((b, _first_entry_note(db, b)) for b in new)
               if fe not in SKIP_TAGS
               and core.is_confirmable(b.get("n_books"))
               and (b["event_id"], b["market"], b["side"], float(b["point"] or 0),
                    b.get("player") or "") not in done]
    for b, fe in to_send[:8]:
        # Header names the pick's OWN tier — ML alerts at Med since 2026-08-05,
        # and a Med pick announcing itself as "High" is simply false.
        _send_bet_alert(db, b, f"🟣 {b['tier']} +EV 즉시 알림 (pre-game)", batch=new, fe=fe)
    if len(to_send) > 8:
        send_telegram(f"…+{len(to_send)-8} more new +EV — 대쉬보드 +EV 탭 확인")
    db.commit()
    return len(to_send)


def _et(iso):
    """HH:MM New-York time for display (falls back to UTC if tzdata missing)."""
    try:
        from zoneinfo import ZoneInfo
        return (datetime.fromisoformat(iso.replace("Z", "+00:00"))
                .astimezone(ZoneInfo("America/New_York")).strftime("%H:%M ET"))
    except Exception:
        return iso[11:16] + " UTC"


def _wake_app_push(bet_survivors, tokens=None, send=None):
    """Wake-time companion-app push: ONE Expo notification summarizing the
    still-live should_bet survivors, deep-linking to the top pick via the app's
    EXISTING high_pick handler (data={'pick_id': ...}) — no app-side change.
    push_token is read READ-ONLY from the app db (never write it as the recorder
    user). Best-effort: any failure must never break the Telegram re-quote.
    bet_survivors: EV-sorted list of is_bet dicts (event_id/market/side/point/
    player/label/book/odds/ev)."""
    if not bet_survivors:
        return 0
    try:
        import softbook_push as push, softbook_app_feed as feed
        if tokens is None:
            paper = os.getenv("SOFTBOOK_PAPER_DB",
                              "/home/ubuntu/kalshi-arb/data/softbook_paper.db")
            pdb = sqlite3.connect(f"file:{paper}?mode=ro", uri=True, timeout=15)
            try:
                tokens = [r[0] for r in pdb.execute(
                    "SELECT token FROM push_token WHERE active=1")]
            finally:
                pdb.close()
        if not tokens:
            return 0
        top = bet_survivors[0]
        pid = feed._encode_id((top["event_id"], top["market"], top["side"],
                               top["point"] or 0, top["player"] or ""))
        n = len(bet_survivors)
        title = f"🌅 밤사이 +EV — 아직 유효 {n}건"
        body = (f"지금 베팅 가능: {top['label']}" + (f" 외 {n-1}건" if n > 1 else "")
                + f" · {top['book']} {_american(top['odds'])} (EV {top['ev']*100:+.1f}%)")
        msgs = [push.build_message(t, title, body, data={"pick_id": pid},
                                   category="high_pick") for t in tokens]
        (send or push.send)(msgs)
        return len(msgs)
    except Exception as e:
        print("wake app-push err:", e)
        return 0


def wakeup_requote(db, key, now, *, app_push=None):
    """Once per day, just after the user's wake time (WAKE_UTC, default 04:30 ET),
    summarize the OVERNIGHT +EV picks that are STILL LIVE so the user, asleep when
    they first fired, can act at a fresh price. 'Still live' = the pick is High in
    the LATEST scan (odds already refreshed every 10 min — no re-fetch), pre-game,
    and its FIRST-ever High fell in the overnight window. NOT filtered by
    high_alerted: the 24/7 3am alert already marks every overnight High, so the old
    'not-yet-alerted' filter made this a permanent no-op. Sends ONE Telegram summary
    (should_bet picks to bet + props/late-crossers as context) and one companion-app
    push of the should_bet set. Touches only wakeup_sent (once/day guard) — no
    high_alerted / message_id side effects (alert_new_high owns that)."""
    if now[11:16] < WAKE_UTC:                    # before wake time (HH:MM UTC) → not yet
        return 0
    db.execute(WAKEUP_SCHEMA)
    day = now[:10]
    if db.execute("SELECT 1 FROM wakeup_sent WHERE day=?", (day,)).fetchone():
        return 0
    now_dt = datetime.fromisoformat(now)
    w0, w1 = f"{day}T{min(QUIET_UTC):02d}:00:00", f"{day}T{WAKE_UTC}:00"   # overnight window
    firsts = {}                      # exact line -> its FIRST-ever High timestamp (when it appeared)
    tier_sql, tier_params = core.alertable_tiers_sql()
    for eid, market, side, pt, pl, first_at in db.execute(
            "SELECT event_id, market, side, COALESCE(point,0), COALESCE(player,''),"
            f" MIN(recorded_at) FROM softbook_ev WHERE {tier_sql}"
            " GROUP BY event_id, market, side, COALESCE(point,0), COALESCE(player,'')",
            tier_params):
        firsts[(eid, market, side, pt, pl)] = first_at
    latest = db.execute("SELECT MAX(recorded_at) FROM softbook_ev").fetchone()[0]
    bet, other = [], []              # still-live overnight picks, split by the bet rule
    for eid, home, away, market, side, pt, pl, ct, book, odds, ev, tier, nb in db.execute(
            "SELECT event_id, home, away, market, side, COALESCE(point,0),"
            " COALESCE(player,''), commence_time, best_soft_book, best_soft_price,"
            f" snapshot_ev, tier, n_books FROM softbook_ev WHERE recorded_at=? AND {tier_sql}",
            (latest, *tier_params)):
        if tier not in core.bet_tiers(market):
            continue                 # not the tier this market is bet at
        if not core.is_confirmable(nb):
            continue                 # single-book market — likely not purchasable
        first_at = firsts.get((eid, market, side, pt, pl))
        if not first_at or not (w0 <= first_at < w1):
            continue                 # not an overnight pick (user was awake, or older day)
        try:
            if datetime.fromisoformat(ct.replace("Z", "+00:00")) <= now_dt:
                continue             # game already started — unbettable
        except Exception:
            continue
        lbl = core.label(market, side, None if market == "h2h" else pt, pl or None)
        entry = {"event_id": eid, "market": market, "side": side, "point": pt,
                 "player": pl, "label": lbl, "book": book, "odds": odds, "ev": ev,
                 "home": home, "away": away}
        (bet if _is_bet(db, eid, market, side, pl, tier) else other).append(entry)
    bet.sort(key=lambda b: -b["ev"])
    other.sort(key=lambda b: -b["ev"])
    if bet or other:
        msg = "🌅 밤사이 +EV — 아직 유효 (04:30 ET 기준)"
        if bet:
            msg += f"\n\n💵 지금 베팅 ({len(bet)}건):\n\n" + "\n\n".join(
                f"• {b['away'][:16]} @ {b['home'][:16]}\n   {b['label']}\n"
                f"   {b['book']} {_american(b['odds'])} (EV {b['ev']*100:+.1f}%) · 💵 ${UNIT_STAKE:.0f}"
                for b in bet[:12])
            if len(bet) > 12:
                msg += f"\n\n…+{len(bet)-12}건 더 (대쉬보드 +EV 탭)"
        if other:
            msg += f"\n\n⚪ 참고 {len(other)}건 (프랍/late-cross — 베팅 X)"
        msg += "\n\npre-game only · bet small · 앱 열어서 원탭"
        send_telegram(msg)
        (app_push or _wake_app_push)(bet)
    db.execute("INSERT OR IGNORE INTO wakeup_sent VALUES (?)", (day,))
    db.commit()
    return len(bet) + len(other)


def init_db():
    c = sqlite3.connect(OUT, timeout=15)
    cols = [r[1] for r in c.execute("PRAGMA table_info(softbook_ev)")]
    if cols and "tier" not in cols:          # one-time migrate (adds consensus/confidence)
        c.execute("DROP TABLE softbook_ev")
    c.execute(SCHEMA)
    # The table's only other index is the autoindex behind UNIQUE(recorded_at, ...),
    # which leads with recorded_at — useless to the app API, whose hot queries are
    # keyed on (event_id, market): market_first_entry_sides and the pick card's
    # alert-vs-now lookup both run ONCE PER PICK. Without this they scanned all
    # 310k rows each time, and /picks took 26s for 203 picks — the app's "fetch
    # failed". Measured 2026-08-05: 26s -> 0.18s. It got bad enough to notice only
    # after ML moved to Med and roughly doubled the candidate set, but the scan was
    # always there.
    c.execute("CREATE INDEX IF NOT EXISTS idx_sbev_event_market"
              " ON softbook_ev(event_id, market, recorded_at)")
    c.commit()
    return c


def record(db, bets, now):
    """Persist one scan's +EV bets as CLV snapshots. Idempotent per (now, bet)."""
    n = 0
    for b in bets:
        try:
            db.execute("INSERT OR IGNORE INTO softbook_ev VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, b["sport"], b["event_id"], b["commence_time"], b["home"], b["away"],
                 b["market"], b["side"], b["point"], b.get("player") or "",
                 b["fair"], b["pin_price"], b["book"], b["odds"], b["ev"],
                 b["n_books"], b["n_ev"], b["conf"], b["tier"]))
            n += 1
        except Exception:
            pass
    db.commit()
    return n


def main():
    key = core.read_key()
    if not key:
        print("no ODDS_API_KEY"); return
    now = datetime.now(timezone.utc).isoformat()
    db = init_db()
    n = credits = 0
    all_bets = []
    for sport in core.active_sports(key):
        try:
            bets, c, _ = core.scan_sport(sport, key)
        except Exception as e:
            print(f"{sport}: {e}"); continue
        credits += c
        all_bets += bets
        n += record(db, bets, now)
    requoted = 0
    try:
        requoted = wakeup_requote(db, key, now)
    except Exception as e:
        print("wakeup err:", e)
    alerted = alert_new_high(db, all_bets, now)
    print(f"[softbook-ev] {now}: recorded {n} +EV bets (ML/spread/total), "
          f"alerted {alerted} new High, {requoted} wake-up requotes, "
          f"{credits} credits -> {OUT}")


if __name__ == "__main__":
    main()
