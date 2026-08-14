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
db_path = "../Stronghold_DB/inventory.db"
```

Point `db_path` at wherever your production data should live -- keeping it in
its own folder outside the repository (as above) means updating Stronghold
never touches your data. The files are created if they do not exist. An
absolute path works too and removes any doubt about the working directory:

```toml
db_path = "/srv/stronghold/inventory.db"
```

Every key in `settings.toml` is optional and falls back to a default, so the
file only needs the values you actually want to change.

## Your data lives in the .sql file

Two files sit side by side: `inventory.db` (the SQLite database the app reads
and writes) and `inventory.sql` (the same data as plain, readable text).

**The `.sql` file is the real one.** Every write rewrites it, and on every
startup the app throws the `.db` away and rebuilds it from the `.sql`. The
database is a working copy; the text file is your data.

That is what makes the backup story work: put the data folder in git, commit
after a day's work, and you have the complete history of your stock in a
format you can read, diff and restore anywhere.

```
cd ../Stronghold_DB
git init
git add inventory.sql && git commit -m "stock as of today"
```

Add `inventory.db` to that folder's `.gitignore` -- it is rebuilt anyway, and
being binary it bloats the repository without adding anything.

### Rolling back

Because the `.sql` is the truth, undoing a mistake is a checkout and a
restart:

```
cd ../Stronghold_DB
git log --oneline           # find the commit you want
git checkout <commit> -- inventory.sql
```

Restart the server and it comes up on exactly that state. No import step and
no way for the two files to disagree.

One safety rule follows from this: if the app finds a `.db` with no `.sql`
next to it, it refuses to start. A missing `.sql` means your source of truth
went astray, and quietly carrying on from the working copy would bless a stale
copy as the real thing. Restore the `.sql` (or move the `.db` aside if you
really do want to start fresh).

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
