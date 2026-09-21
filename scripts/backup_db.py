"""Take a consistent SQLite backup, including committed WAL data."""
import os
import sqlite3
import sys

os.umask(0o077)
with sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True) as source:
    with sqlite3.connect(sys.argv[2]) as target:
        source.backup(target)
