"""Deployment settings read from an optional settings.toml at startup, plus
logging setup. A wrong value here stops the app from running or being
reachable; everything else is a domain setting (db.DOMAIN_DEFAULTS)."""

import logging
import tomllib
from logging.handlers import RotatingFileHandler
from pathlib import Path

from uvicorn.logging import DefaultFormatter

DEFAULTS = {
    "db": {
        # The data file itself: readable SQL, the thing to keep in git. SQLite
        # is an implementation detail (a working .db is rebuilt from this in a
        # temp directory at startup), so no database path is configurable.
        "data_file": "inventory.sql",
        # If the data file sits in a git repo of its own, commit it after every
        # write. Off unless asked for; the data file may not be in the app repo.
        "auto_commit": False,
    },
    "gui": {
        "port": 8080,
    },
    # Credentials (the WooCommerce key and secret) are edited on the settings
    # page and stored encrypted in the data file. This names the key that
    # decrypts them: it must NOT be committed, and losing it means re-entering
    # those credentials -- nothing else. Created on first run if absent.
    "secrets": {
        "key_file": "secrets.key",
    },
    "logging": {
        "level": "DEBUG",  # DEBUG, INFO, WARNING, ERROR
        "file": "log.txt",
        "rollover_kb": 100,  # start a new file after this many KB
        "max_total_kb": 400,  # delete oldest files once total exceeds this
    },
}


# Settings that no longer exist, mapped to what to do instead. Silently
# ignoring one would leave the user pointing at data the app never reads.
RENAMED = {
    ("db", "db_path"): "db.data_file, which now names the .sql file itself "
    "(the .db is rebuilt in a temp directory at startup)",
}


class Settings:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        # the file is named explicitly on the command line, so a missing one is
        # a typo, not a request for defaults
        if not path.exists():
            raise FileNotFoundError(f"settings file not found: {self.path}")
        self.text = path.read_text("utf-8")
        self._data = tomllib.loads(self.text)
        for (section, key), replacement in RENAMED.items():
            if key in self._data.get(section, {}):
                raise ValueError(
                    f"{self.path}: [{section}] {key} was replaced by {replacement}"
                )

    def get(self, section: str, key: str):
        """A setting's value, or its default. Unknown section/key fails loudly."""
        return self._data.get(section, {}).get(key, DEFAULTS[section][key])

    def path_of(self, section: str, key: str) -> Path:
        """A path setting, resolved relative to the settings file's directory."""
        return self.path.parent / Path(self.get(section, key))


def setup_logging(settings: Settings) -> None:
    """Timestamped lines to a rolling file; a RotatingFileHandler keeping
    max_total / rollover files."""
    rollover_kb = settings.get("logging", "rollover_kb")
    max_total_kb = settings.get("logging", "max_total_kb")
    backups = max(1, max_total_kb // rollover_kb - 1)
    handler = RotatingFileHandler(
        settings.path_of("logging", "file"),
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
    # uvicorn's own formatter, so our lines and its lines look identical
    console.setFormatter(DefaultFormatter("%(levelprefix)s %(message)s"))
    root.addHandler(console)
