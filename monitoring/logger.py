import csv
import logging
import os
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler


def setup_logger(name: str, log_dir: str = "logs") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(console)

    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, "bot.log"), when="midnight", interval=1, backupCount=30, utc=True,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(file_handler)

    return logger


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
