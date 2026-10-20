#!/usr/bin/env python3
"""맥북 <-> GitHub 저장소(salim-threads-publisher) 동기화.

  python3 sync.py pull   # 서버의 발행 결과(queue.json 상태, posted_log.json)를 맥북으로 합치기
  python3 sync.py push   # 맥북에서 새로 쓴 예약글을 서버로 올리기 (pull 후 병합해서 올림)
  python3 sync.py both   # pull 후 push (루틴 기본)
  python3 sync.py put <로컬파일> [저장소경로]   # 임의 파일 업로드 (코드 갱신용)

발행은 서버(GitHub Actions)가 전담한다. 맥북은 글 작성·답글·링크만 한다.
"""
import base64, json, os, sys, time, urllib.request, urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
ACC = os.environ.get("ACCOUNT", "salim")
PREFIX = "" if ACC == "salim" else f"accounts/{ACC}/"
FILES = [PREFIX + "queue.json", PREFIX + "posted_log.json"]


def secrets():
    d = {}
    for line in open(os.path.join(BASE, ".secrets.env"), encoding="utf-8"):
        if "=" in line:
            k, v = line.rstrip("\n").split("=", 1)
            d[k] = v
    return d


S = secrets()
REPO = S["GH_REPO"]
HDR = {"Authorization": "Bearer " + S["GH_PAT"], "Accept": "application/vnd.github+json",
       "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "salim-sync"}


def api(method, path, body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", method=method, headers=HDR,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def get_remote(name):
    st, j = api("GET", f"contents/{name}")
    if st == 404:
        return None, None
    if st != 200:
        raise RuntimeError(f"GET {name} {st} {j}")
    return base64.b64decode(j["content"]).decode("utf-8"), j["sha"]


def put_remote(name, text, sha, msg):
    body = {"message": msg, "content": base64.b64encode(text.encode("utf-8")).decode()}
    if sha:
        body["sha"] = sha
    return api("PUT", f"contents/{name}", body)


def load_local(name):
    p = os.path.join(BASE, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def save_local(name, data):
    p = os.path.join(BASE, name)
    tmp = p + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def merge_queue(local, remote):
    """id 기준 병합. 서버가 상태를 바꾼 항목(queued 아님)은 서버 우선, 그 외엔 맥북 우선."""
    by = {x["id"]: x for x in remote}
    for x in local:
        r = by.get(x["id"])
        if r is None:
            by[x["id"]] = x
        elif r.get("status") != "queued":
            pass  # 서버가 발행/보류한 항목은 항상 서버 우선 (지연 답글 상태 등 덮어쓰기 금지)
        else:
            by[x["id"]] = x
    return sorted(by.values(), key=lambda x: x["scheduled_at"])


def merge_log(local, remote):
    by = {x["post_id"]: x for x in remote}
    for x in local:
        by.setdefault(x["post_id"], x)
    return sorted(by.values(), key=lambda x: x["posted_at"])


def pull():
    for name in FILES:
        txt, _ = get_remote(name)
        if txt is None:
            continue
        remote = json.loads(txt)
        local = load_local(name)
        merged = merge_queue(local, remote) if name.endswith("queue.json") else merge_log(local, remote)
        save_local(name, merged)
    q = load_local(PREFIX + "queue.json")
    from collections import Counter
    print(f"[{ACC}] pull 완료:", dict(Counter(x.get("status") for x in q)), "/ 발행기록", len(load_local(PREFIX + "posted_log.json")))


def push():
    for name in FILES:
        for attempt in range(4):
            txt, sha = get_remote(name)
            remote = json.loads(txt) if txt else []
            local = load_local(name)
            merged = merge_queue(local, remote) if name.endswith("queue.json") else merge_log(local, remote)
            out = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
            save_local(name, merged)
            if txt is not None and json.loads(txt) == merged:
                break
            st, j = put_remote(name, out, sha, f"sync {name} from mac")
            if st in (200, 201):
                break
            if st in (409, 422):
                time.sleep(3)
                continue
            raise RuntimeError(f"PUT {name} {st} {j}")
    print("push 완료")


def put_file(local_path, repo_path=None):
    repo_path = repo_path or os.path.basename(local_path)
    txt = open(local_path, encoding="utf-8").read()
    _, sha = get_remote(repo_path)
    st, j = put_remote(repo_path, txt, sha, f"update {repo_path}")
    print(repo_path, st)


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "both"
    if c == "pull":
        pull()
    elif c == "push":
        push()
    elif c == "put":
        put_file(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        pull()
        push()
