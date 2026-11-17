#!/usr/bin/env python3
"""Split counts.json into database-sized pieces (db/counts-1.json, db/counts-2.json, ...).

Each database record holds at most 256 KB, so the per-system counts are packed
into pieces under ~230 KB. The database page joins every "counts-N" record.
"""
import json
import os

LIMIT = 230_000
src = json.load(open("counts.json", encoding="utf-8"))
os.makedirs("db", exist_ok=True)
for f in os.listdir("db"):
    if f.startswith("counts-") and f.endswith(".json"):
        os.remove(os.path.join("db", f))

head = {k: v for k, v in src.items() if k != "systems"}
pieces, cur, size = [], {}, 0
for sid, rec in sorted(src.get("systems", {}).items()):
    n = len(json.dumps({sid: rec}, ensure_ascii=False, separators=(",", ":")).encode())
    if cur and size + n > LIMIT:
        pieces.append(cur)
        cur, size = {}, 0
    cur[sid], size = rec, size + n
if cur:
    pieces.append(cur)

for i, part in enumerate(pieces, 1):
    with open(f"db/counts-{i}.json", "w", encoding="utf-8") as f:
        json.dump({**head, "part": i, "of": len(pieces), "systems": part}, f, ensure_ascii=False, separators=(",", ":"))
print(f"{len(src.get('systems', {}))} systems in {len(pieces)} piece(s)")
