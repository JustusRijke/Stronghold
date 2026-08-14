# Deployment

How to run Stronghold as the real thing: one process, your own database, and
reachable from the other machines on your network.

## Starting the server

Build the frontend once (and again after every frontend change):

```
cd frontend && npm install && npm run build
```

Then start the server from the repository root:

```
uv run backend/main.py
```

That single process serves both the JSON API and the web interface. Startup
prints which settings file, database and log file it is using -- read those
three lines, they are the quickest way to spot a settings file that is not
being picked up.

## Choosing the production database

Settings live in `settings.toml`, read from the **directory you start the
server in**. Running from the repository root means the file at the repository
root is used, and any relative path inside it is resolved from there too.

```toml
[db]
db_path = "../Stronghold_DB/inventory.db"   # SQLite database file
export_sql = true          # keep a readable <db>.sql next to the database for git
```

Point `db_path` at wherever your production data should live -- keeping it in
its own folder outside the repository (as above) means updating Stronghold
never touches your data. The file is created if it does not exist. An absolute
path works too and removes any doubt about the working directory:

```toml
db_path = "/srv/stronghold/inventory.db"
```

With `export_sql = true`, every write also rewrites a plain-text
`inventory.sql` next to the database. That file is the backup: keep the folder
in git and you get the full history of your stock for free, restorable into an
empty database with `sqlite3 inventory.db < inventory.sql`.

Every key in `settings.toml` is optional and falls back to a default, so the
file only needs the values you actually want to change.

## Reaching it from the local network

The server listens on `0.0.0.0`, meaning it accepts connections on every
network interface, not just the machine it runs on. Anyone on the same LAN can
open it at:

```
http://<server-ip>:8080
```

Find the machine's address with `ip addr` (Linux/macOS) or `ipconfig`
(Windows). The port comes from `settings.toml`:

```toml
[gui]
port = 8080
```

Two practical notes:

- The host's firewall must allow inbound connections on that port.
- There is no login and no encryption. Stronghold is single-user and assumes a
  trusted network -- do not expose it directly to the internet.
