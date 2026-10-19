#!/usr/bin/env python3
"""서버 발행 결과를 최신 원격 상태에 안전하게 합친다 (맥북이 그 사이 새 글을 올려도 유실 없음).
사용: python3 server_merge.py save   -> 현재 queue/log를 /tmp/mine 에 보관
      python3 server_merge.py apply  -> (git reset --hard origin/main 이후) 보관본의 발행 결과를 반영
"""
import json, os, sys, shutil
FILES = ["queue.json", "posted_log.json"] + [f"accounts/{a}/{f}" for a in ("kitchen", "season") for f in ("queue.json", "posted_log.json")]
TMP = "/tmp/mine"

def load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return []

if sys.argv[1] == "save":
    os.makedirs(TMP, exist_ok=True)
    for f in FILES:
        if os.path.exists(f):
            os.makedirs(os.path.dirname(os.path.join(TMP, f)) or TMP, exist_ok=True)
            shutil.copy(f, os.path.join(TMP, f))
else:
    for f in FILES:
        mine = load(os.path.join(TMP, f))
        cur = load(f)
        if f.endswith("queue.json"):
            m = {x["id"]: x for x in mine}
            out = []
            seen = set()
            for x in cur:
                y = m.get(x["id"])
                out.append(y if (y and y.get("status") != "queued") else x)
                seen.add(x["id"])
            out += [y for y in mine if y["id"] not in seen]
        else:
            by = {x["post_id"]: x for x in cur}
            for x in mine:
                by.setdefault(x["post_id"], x)
            out = sorted(by.values(), key=lambda x: x["posted_at"])
        json.dump(out, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
