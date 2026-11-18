"""Shared timestamped logger. All modules in this app import `log` from here."""
from datetime import datetime


def log(msg: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)
