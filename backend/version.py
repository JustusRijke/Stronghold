"""What version of Stronghold this is, and what shape of data it speaks.

Two separate numbers, on purpose:

- APP_VERSION comes from the git tag (hatch-vcs). Informational: it answers
  "which Stronghold do I install to open this data file?".
- SCHEMA_VERSION is a hand-maintained integer, bumped only when the shape of
  the data changes. It is what decides whether a data file needs migrating,
  so it must not move just because a release was cut.
"""

import re
from importlib.metadata import PackageNotFoundError, version

# Bump when a change makes an older data file wrong, and add the matching entry
# to db._MIGRATIONS. 1 is the 1.0 baseline: the shape the app shipped 1.0 with.
SCHEMA_VERSION = 1

try:
    APP_VERSION = version("stronghold")
except PackageNotFoundError:
    # running from a source tree that was never `uv sync`ed
    APP_VERSION = "0.0.0+unknown"

# Untagged builds get a "1.0.1.dev3+gabc1234" suffix that changes every commit.
# Stamping that into the data file would rewrite the stamp (and, with
# auto_commit, raise a commit) on every developer build, so only X.Y.Z is used.
_RELEASE = re.match(r"\d+\.\d+\.\d+", APP_VERSION)
RELEASE_VERSION = _RELEASE.group(0) if _RELEASE else APP_VERSION
