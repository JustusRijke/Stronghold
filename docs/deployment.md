# Deployment

How to run Stronghold as the real thing: one process, your own database, and
reachable from the other machines on your network.

## Starting the server

Build the frontend once (and again after every frontend change):

```
cd frontend && npm install && npm run build
```

Then start the server, naming the settings file to use:

```
uv run backend/main.py settings.toml
```

That single process serves both the JSON API and the web interface. Startup
prints which settings file, database and log file it is using -- read those
three lines, they are the quickest way to spot a settings file that is not
being picked up.

## Pointing at your production data

The settings file is the one you name on the command line, and every relative
path inside it is resolved **relative to that file**, not to the directory you
started the server in. So the settings file can live next to your data:

```
uv run backend/main.py ../Stronghold_DB/settings.toml
```

```toml
[db]
data_file = "inventory.sql"
```

Point `data_file` at wherever your data should live -- keeping it (and the
settings file) in its own folder outside the repository, as above, means
updating Stronghold never touches your data. The file is created if it does
not exist. An absolute path works too:

```toml
data_file = "/srv/stronghold/inventory.sql"
```

Every key in `settings.toml` is optional and falls back to a default, so the
file only needs the values you actually want to change.

## Connecting to WooCommerce

Sales orders are imported from a WooCommerce shop. The connection goes in
`settings.toml` and nowhere else -- these are credentials, and the data file is
meant to be kept in git:

```toml
[woocommerce]
url = "https://shop.example.com"   # the site root, nothing after it
key = "ck_..."
secret = "cs_..."
```

Generate the pair in WooCommerce under **Settings -> Advanced -> REST API**, with
**Read** permission. Stronghold only ever reads from WooCommerce, so a read-only
key is all it needs and all it should be given.

Leave the section out (or blank) and the import button simply reports that
WooCommerce is not configured; the rest of the app is unaffected.

## Your data is one readable file

`inventory.sql` is your entire inventory as plain, readable text: one line per
row, no binary format to decode. It is the only file you need to back up, copy
to another machine, or keep in version control.

Stronghold uses SQLite internally to query that data, but the database is not
your data -- it is a scratch copy, rebuilt from the `.sql` in your system's
temp directory every time the app starts. You never have to name it, back it
up, or clean it up. Deleting it costs nothing.

That is what makes the backup story simple: put the data folder in git, commit
after a day's work, and you have the complete history of your stock in a
format you can read, diff and restore anywhere.

```
cd ../Stronghold_DB
git init
git add inventory.sql && git commit -m "stock as of today"
```

### Committing automatically

You can let Stronghold do that commit for you, after every single change:

```toml
[db]
auto_commit = true
```

Each write commits the data file with the activity-log entry it produced as
the commit message ("Created part M3 bolt"), so `git log` reads as your
inventory's history. A change that produced no activity entry is still
committed, with a message saying so -- that is a gap in the app, worth
reporting.

Nothing happens if the data folder is not a git repository (or has no commit
identity configured); the write still succeeds. The one thing that is refused
is pointing `auto_commit` at a data file *inside* the Stronghold application
folder -- the server stops at startup with an error, rather than filling the
app's own history with your stock. Keep your data in a repository of its own.

Startup also commits anything it finds uncommitted in the data file before
loading it -- an edit you made by hand, say. Loading ends by rewriting the
file, so without that commit those changes would be gone for good; with it,
they are one `git checkout` away. Stopping the server writes the file out one
last time too.

When starting the app itself changes the file -- recording the version the
first time, or migrating older data -- the commit says so
("Recorded the Stronghold version in the data file (Stronghold 1.0.0, data
schema 1)"), rather than borrowing the message of whatever change came before
it. A start that changes nothing commits nothing.

Default is `false`.

### Rolling back

Because the `.sql` is the truth, undoing a mistake is a checkout and a
restart:

```
cd ../Stronghold_DB
git log --oneline           # find the commit you want
git checkout <commit> -- inventory.sql
```

Restart the server and it comes up on exactly that state. There is no import
step and nothing else to clean up: the scratch database is rebuilt from
whatever the file now says.

Rolling back far enough can land on a file written by an older Stronghold.
That is handled for you: the app upgrades the data to the current shape on
startup and writes it back (with `auto_commit` on, as its own commit), so
there is nothing extra to run.

### "Refusing to start: it would drop the data this version does not know about"

The opposite case is refused. If the data file was written by a *newer*
Stronghold than the one you are running, the app reports which version wrote
it and exits without touching the file.

This is deliberate, and it protects your data. An older Stronghold exports
only the columns it knows about, so a single write would drop everything the
newer version added -- and with `auto_commit` on it would commit that loss
over the only copy. Install the version named in the message (or newer) and
start again.

Every start prints the version in use, and the settings page shows it
alongside the schema version of your data:

```
INFO:     Stronghold 1.0.0 (data schema version 1)
```

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
