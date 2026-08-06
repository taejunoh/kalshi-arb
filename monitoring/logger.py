import csv
import logging
import os
from datetime import datetime, timezone
import gzip
import shutil
from logging.handlers import TimedRotatingFileHandler


def _gzip_rotator(source, dest):
    r"""Compress on rotation instead of keeping 30 days of plain text.

    The bot writes 100-200 MB of log a day. backupCount=30 was pruning
    correctly, but 30 days uncompressed is ~4 GB against a 29 GB disk that also
    holds a 12 GB bot.db — and on 2026-08-06 it filled: 4 MB free, SQLite
    returning "disk I/O error" on every write, strategies down. gzip -6 takes
    these logs to roughly a tenth, so the same 30-day window costs ~400 MB.

    Pruning still works on the compressed files: the handler matches rotated
    names with (?<!\d)\d{4}-\d{2}-\d{2}(?!\d), which a trailing .gz does not
    break (verified on the deployed Python 3.12).

    Best-effort: a failure here leaves the plain rotated file in place, which is
    the pre-existing behaviour. Losing compression is a disk-space problem;
    raising out of a logging handler at midnight is an outage.
    """
    try:
        with open(source, "rb") as src, gzip.open(dest, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, length=1 << 20)
        os.remove(source)
    except Exception:
        try:
            if os.path.exists(dest):
                os.remove(dest)          # no half-written .gz beside the original
            if os.path.exists(source):
                os.replace(source, dest[:-3] if dest.endswith(".gz") else dest)
        except Exception:
            pass


def setup_logger(name: str, log_dir: str = "logs") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)

    # Configure root logger so all module loggers (market_scanner, sum_arb, etc.) are captured
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.DEBUG)
        # Quiet noisy third-party loggers
        for noisy in ("aiosqlite", "httpcore", "httpx", "hpack"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(console)

        file_handler = TimedRotatingFileHandler(
            os.path.join(log_dir, "bot.log"), when="midnight", interval=1, backupCount=30, utc=True,
        )
        file_handler.namer = lambda name: name + ".gz"
        file_handler.rotator = _gzip_rotator
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root.addHandler(file_handler)

    return logging.getLogger(name)


class TradeLogger:
    FIELDS = ["timestamp", "strategy", "market_id", "direction", "price", "size", "fee", "pnl", "status"]

    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        self.csv_path = os.path.join(log_dir, "trades.csv")
        self._ensure_header()

    def _ensure_header(self):
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.FIELDS)
                writer.writeheader()

    def log_trade(self, strategy, market_id, direction, price, size, fee, pnl, status):
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy": strategy, "market_id": market_id, "direction": direction,
            "price": price, "size": size, "fee": fee, "pnl": pnl, "status": status,
        }
        with open(self.csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDS)
            writer.writerow(row)
