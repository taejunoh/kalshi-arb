import csv
import logging
import os
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler


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
