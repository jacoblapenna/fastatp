"""What is actually in a repo: python blocks.py <state-dir>

Every block in blocks.sqlite, decoded. A repo holds three kinds of block and
nothing else: one signed commit, the tree nodes, and the records. All of it
is DAG-CBOR, a binary encoding of the same data model JSON uses. None of it
is encrypted; the only secret in the directory is signing.key.
"""

import sqlite3
import sys

from fastatp import dagcbor, datamodel
from fastatp.cid import CID

# Open read-only so a running peer is never disturbed. The whole store is one
# table: cid (the hash of the bytes) -> data (the bytes). The hash is the key,
# which is what "content-addressed" means.
db = sqlite3.connect(f"file:{sys.argv[1]}/blocks.sqlite?mode=ro", uri=True)
rows = db.execute("SELECT cid, data FROM blocks").fetchall()
print(f"{len(rows)} blocks")
raw = b""
for cid, data in rows:
    obj = dagcbor.decode(bytes(data))  # every block decodes to a plain dict
    # Three kinds of block, told apart by shape alone:
    if "sig" in obj:  # the commit: the only signed block; it names the tree root and a rev
        kind = f"commit     rev={obj['rev']} did={obj['did']}"
    elif "$type" in obj:  # a record: the same dict a Note or Reply validated, nothing added
        kind = f"record     {datamodel.to_json(obj)}"
        raw = bytes(data)  # keep one to show its stored form at the end
    else:  # a tree node: sorted entries pointing (by hash) at records and child nodes
        kind = f"tree node  {len(obj.get('e', []))} entries"
    print(f"  {CID.decode(bytes(cid)).encode()[:16]}...  {len(data):4d} bytes  {kind}")
if raw:
    print(f"one record, as stored: {raw[:56]!r}...")  # binary, but the field names are right there
