"""Deployment settings read from an optional settings.toml at startup, plus
logging setup. A wrong value here stops the app from running or being
reachable; everything else is a domain setting (db.DOMAIN_DEFAULTS)."""

import logging
import tomllib
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULTS = {
    "db": {
        "db_path": "inventory.db",
        "export_sql": True,  # keep a readable <db>.sql next to the database
    },
    "gui": {
        "port": 8080,
    },
    "logging": {
        "level": "DEBUG",  # DEBUG, INFO, WARNING, ERROR
        "file": "log.txt",
        "rollover_kb": 100,  # start a new file after this many KB
        "max_total_kb": 400,  # delete oldest files once total exceeds this
    },
}


class Settings:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.found = path.exists()
        self.text = path.read_text("utf-8") if self.found else ""
        self._data = tomllib.loads(self.text) if self.found else {}

    def get(self, section: str, key: str):
        """A setting's value, or its default. Unknown section/key fails loudly."""
        return self._data.get(section, {}).get(key, DEFAULTS[section][key])


def setup_logging(settings: Settings) -> None:
    """Timestamped lines to a rolling file; a RotatingFileHandler keeping
    max_total / rollover files."""
    rollover_kb = settings.get("logging", "rollover_kb")
    max_total_kb = settings.get("logging", "max_total_kb")
    backups = max(1, max_total_kb // rollover_kb - 1)
    handler = RotatingFileHandler(
        Path(settings.get("logging", "file")),
        maxBytes=rollover_kb * 1000,
        backupCount=backups,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(settings.get("logging", "level"))
    root.addHandler(handler)
    # INFO+ also goes to the console (bare message), so startup says which
    # settings file and database are in use. Writes log at DEBUG: file only.
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console)
