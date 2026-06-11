#!/usr/bin/env python3
"""One-time ledger-B honesty migration (2026-06-11) — quiet-hour entries.

Ledger B (paper_bet_high) measures "bet each new High alert as it fires", but
alerts are suppressed during QUIET_UTC (02-09 UTC = NYC sleep): entries whose
trigger snapshot fell in that window booked prices the user could never act on
(e.g. 6/11 Wings -5.5 booked BetRivers 1.91 at 04:20 UTC; best available at
wake-up was 1.82). Re-align existing rows with the new candidates_high rule:

  - line re-hit High at a later NON-quiet pre-game scan -> RE-PRICE the entry to
    that snapshot (the scan where the alert actually fires); settled rows get
    pnl recomputed from the new odds/stake (same result — same exact line).
  - line never High outside the window -> DELETE (the alert never fired, so
    strategy B never bets it; the signal-level record stays in ledger A and
    softbook_ev). If it goes High again later, place_high re-enters it fresh.

Backs up the paper db next to it first. Dry-run by default; --apply to write.

  python scripts/softbook_ev_quiet_requote_migration.py            # preview
  python scripts/softbook_ev_quiet_requote_migration.py --apply    # write
"""
import shutil, sqlite3, sys

import softbook_ev_core as core
import softbook_ev_paper_pnl as pnl


def migrate(paper, src, apply=False):
    new = {(c[0], c[4], c[5], c[6], c[7]): c for c in pnl.candidates_high(src)}
    qmin, qmax = min(core.QUIET_UTC), max(core.QUIET_UTC)
    rows = paper.execute(
        """SELECT event_id, market, side, COALESCE(point,0), COALESCE(player,''),
                  placed_at, book, odds, stake, completed, result, pnl, label
           FROM paper_bet_high
           WHERE CAST(strftime('%H', placed_at) AS INTEGER) BETWEEN ? AND ?""",
        (qmin, qmax)).fetchall()
    total = paper.execute("SELECT COUNT(*) FROM paper_bet_high").fetchone()[0]
    print(f"{len(rows)} quiet-hour entries ({qmin:02d}-{qmax+1:02d} UTC trigger) "
          f"of {total} total\n")
    nrep = ndel = 0
    for (eid, market, side, point, player, placed, book, odds, stake,
         comp, result, old_pnl, lbl) in rows:
        k = (eid, market, side, point, player)
        c = new.get(k)
        if c:
            nbook, nodds, nev, nstake, nrec, nconf = c[10], c[11], c[12], c[13], c[14], c[16]
            npnl = old_pnl
            if comp and result in ("win", "lose", "push"):
                npnl = (round(nstake * (nodds - 1), 2) if result == "win"
                        else -nstake if result == "lose" else 0)
            print(f"REPRICE {lbl:<28} {placed[:16]} {book} {odds:.2f} ${stake:.2f}"
                  f" -> {nrec[:16]} {nbook} {nodds:.2f} ${nstake:.2f}"
                  + (f"  pnl {old_pnl:+.2f} -> {npnl:+.2f}" if comp and result else ""))
            nrep += 1
            if apply:
                paper.execute(
                    """UPDATE paper_bet_high SET placed_at=?, book=?, odds=?, ev=?,
                           stake=?, conf=?, pnl=?
                       WHERE event_id=? AND market=? AND side=?
                         AND COALESCE(point,0)=? AND COALESCE(player,'')=?""",
                    (nrec, nbook, nodds, nev, nstake, nconf, npnl, *k))
        else:
            print(f"DELETE  {lbl:<28} {placed[:16]} {book} {odds:.2f} ${stake:.2f} "
                  f"[{result or 'pending'}] — High 재진입 없음 (알림 미발동)")
            ndel += 1
            if apply:
                paper.execute(
                    """DELETE FROM paper_bet_high
                       WHERE event_id=? AND market=? AND side=?
                         AND COALESCE(point,0)=? AND COALESCE(player,'')=?""", k)
    if apply:
        paper.commit()
    print(f"\n{'APPLIED' if apply else 'DRY-RUN (use --apply to write)'}: "
          f"repriced {nrep}, deleted {ndel}")
    return nrep, ndel


def main():
    apply = "--apply" in sys.argv
    if apply:
        bak = pnl.PAPER_DB + ".bak-quietfix"
        shutil.copy2(pnl.PAPER_DB, bak)
        print(f"backup -> {bak}")
    src = sqlite3.connect(f"file:{pnl.SRC_DB}?mode=ro", uri=True)
    paper = sqlite3.connect(pnl.PAPER_DB)
    migrate(paper, src, apply)
    if apply:
        pnl.report_high(paper)


if __name__ == "__main__":
    main()
