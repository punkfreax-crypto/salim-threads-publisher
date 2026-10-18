#!/usr/bin/env python3
"""스레드 쿠파스 자동화 - 발행기 (@salim.ggultem.note)

명령:
  python3 threads_bot.py run        # 예약 시간이 지난 글을 발행 (하루 한도/워밍업 규칙 적용)
  python3 threads_bot.py refresh    # 장기 토큰 갱신 (50일 지나면 자동)
  python3 threads_bot.py stats      # 최근 글 조회수/좋아요/답글 수집 -> stats.json
  python3 threads_bot.py status     # 현재 단계, 오늘 발행 수, 대기열 요약
  python3 threads_bot.py test "본문"  # 즉시 1건 발행 (테스트용)

대기열(queue.json) 항목 예:
  {"id": "20261014-01", "type": "info" | "ad", "text": "본문",
   "image_url": "https://... (선택)", "reply_text": "링크+대가성 문구 (ad 필수)",
   "scheduled_at": "2026-10-14T08:30:00+09:00", "status": "queued"}
"""
import json, os, sys, time, urllib.parse, urllib.request, datetime as dt

BASE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(BASE, ".secrets.env")
QUEUE = os.path.join(BASE, "queue.json")
LOG = os.path.join(BASE, "posted_log.json")
STATS = os.path.join(BASE, "stats.json")
CONFIG = os.path.join(BASE, "config.json")
API = "https://graph.threads.net/v1.0"
KST = dt.timezone(dt.timedelta(hours=9))

DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."


def now():
    return dt.datetime.now(KST)


def load_json(p, default):
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(p, data):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def secrets():
    d = {}
    with open(SECRETS, encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1)
                d[k] = v
    return d


def set_secret(key, value):
    d = secrets()
    d[key] = value
    with open(SECRETS, "w", encoding="utf-8") as f:
        for k, v in d.items():
            f.write(f"{k}={v}\n")
    os.chmod(SECRETS, 0o600)


def http(method, url, params=None):
    params = params or {}
    data = None
    if method == "GET":
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}")


# ---------- 워밍업 / 하루 한도 ----------
def config():
    return load_json(CONFIG, {})


def phase_info():
    cfg = config()
    start = dt.date.fromisoformat(cfg["account_started"])
    day = (now().date() - start).days + 1
    for ph in cfg["phases"]:
        if day <= ph["until_day"]:
            return day, ph
    return day, cfg["phases"][-1]


def posted_today(log, kind=None):
    today = now().date().isoformat()
    return [x for x in log if x["posted_at"][:10] == today and (kind is None or x["type"] == kind)]


# ---------- 발행 ----------
def create_container(token, uid, text, image_url=None, reply_to=None):
    p = {"access_token": token, "text": text}
    if image_url:
        p.update(media_type="IMAGE", image_url=image_url)
    else:
        p.update(media_type="TEXT")
    if reply_to:
        p["reply_to_id"] = reply_to
    return http("POST", f"{API}/{uid}/threads", p)["id"]


def publish(token, uid, creation_id):
    # 이미지 컨테이너는 처리 시간이 필요할 수 있음
    for _ in range(10):
        st = http("GET", f"{API}/{creation_id}", {"fields": "status,error_message", "access_token": token})
        if st.get("status") in ("FINISHED", None):
            break
        if st.get("status") == "ERROR":
            raise RuntimeError(f"컨테이너 오류: {st.get('error_message')}")
        time.sleep(6)
    return http("POST", f"{API}/{uid}/threads_publish", {"creation_id": creation_id, "access_token": token})["id"]


def permalink(token, media_id):
    try:
        return http("GET", f"{API}/{media_id}", {"fields": "permalink", "access_token": token}).get("permalink")
    except Exception:
        return None


def post_item(item):
    s = secrets()
    token, uid = s["THREADS_ACCESS_TOKEN"], s["THREADS_USER_ID"]
    text = item["text"].strip()
    if len(text) > 500:
        raise RuntimeError("본문 500자 초과")
    cid = create_container(token, uid, text, item.get("image_url"))
    time.sleep(3)
    mid = publish(token, uid, cid)
    result = {"post_id": mid, "permalink": permalink(token, mid)}
    if item.get("reply_text"):
        reply = item["reply_text"].strip()
        if item["type"] == "ad" and DISCLOSURE not in reply:
            reply = reply + "\n\n" + DISCLOSURE
        time.sleep(20)  # 본글 직후 바로 답글 달지 않기
        rcid = create_container(token, uid, reply, reply_to=mid)
        time.sleep(3)
        result["reply_id"] = publish(token, uid, rcid)
    return result


def validate(item):
    if item["type"] == "ad":
        if not item.get("reply_text") or "link.coupang.com" not in item["reply_text"]:
            return "광고글인데 답글에 쿠팡 파트너스 링크가 없음"
    if "coupang.com" in item["text"]:
        return "본문에 링크 금지 (링크는 답글로)"
    return None


def cmd_run():
    if config().get("server_publish") and not os.environ.get("GITHUB_ACTIONS"):
        print("발행은 GitHub 서버가 전담 (맥북 발행 꺼짐). 맥북에서는 `python3 sync.py both`로 동기화")
        return
    q = load_json(QUEUE, [])
    log = load_json(LOG, [])
    day, ph = phase_info()
    done = 0
    for item in sorted(q, key=lambda x: x["scheduled_at"]):
        if item.get("status") != "queued":
            continue
        sched = dt.datetime.fromisoformat(item["scheduled_at"])
        if sched > now():
            continue
        # 맥북이 잠들었다 깨서 밀린 글: 6시간 넘게 지났으면 발행하지 않고 보류 (몰아 올리기 방지)
        if now() - sched > dt.timedelta(hours=6):
            item["status"] = "stale"
            item["note"] = "예약 6시간 초과로 보류 (다음 비축 때 재배정 가능)"
            continue
        # 한 번 실행에 1건만, 직전 발행 후 60분 간격 유지
        if done >= 1:
            break
        last = max((dt.datetime.fromisoformat(x["posted_at"]) for x in log), default=None)
        if last and now() - last < dt.timedelta(minutes=60):
            break
        if len(posted_today(log)) >= ph["max_posts"]:
            print(f"오늘 한도 도달 ({ph['max_posts']}건, {day}일차)")
            break
        if item["type"] == "ad" and len(posted_today(log, "ad")) >= ph["max_ads"]:
            item["status"] = "deferred"
            item["note"] = f"{day}일차 광고 한도({ph['max_ads']}) 초과로 보류"
            continue
        err = validate(item)
        if err:
            item["status"] = "rejected"
            item["note"] = err
            continue
        try:
            res = post_item(item)
            item.update(status="posted", posted_at=now().isoformat(), **res)
            log.append({"id": item["id"], "type": item["type"], "posted_at": item["posted_at"],
                        "post_id": res["post_id"], "permalink": res.get("permalink"),
                        "product": item.get("product"), "text": item["text"][:200],
                        "format": item.get("format")})
            done += 1
            print(f"발행: {item['id']} ({item['type']}) {res.get('permalink')}")
        except Exception as e:
            if "timed out" in str(e) and item.get("retries", 0) < 2:
                item["retries"] = item.get("retries", 0) + 1
                item["note"] = f"타임아웃 재시도 대기 {item['retries']}/2"
                print(f"타임아웃, 다음 실행에 재시도: {item['id']}")
                save_json(QUEUE, q)
                break
            item["status"] = "error"
            item["note"] = str(e)[:300]
            print(f"실패: {item['id']} {e}")
        save_json(QUEUE, q)
        save_json(LOG, log)
        time.sleep(5)
    save_json(QUEUE, q)
    print(f"{day}일차 / 단계 '{ph['name']}' / 이번 실행 발행 {done}건 / 오늘 누적 {len(posted_today(log))}건")


def cmd_refresh(force=False):
    s = secrets()
    issued = dt.datetime.fromisoformat(s.get("THREADS_TOKEN_ISSUED", "2000-01-01T00:00:00+00:00").replace("Z", "+00:00"))
    age = (dt.datetime.now(dt.timezone.utc) - issued).days
    if age < 50 and not force:
        print(f"토큰 사용 {age}일째, 갱신 불필요")
        return
    r = http("GET", "https://graph.threads.net/refresh_access_token",
             {"grant_type": "th_refresh_token", "access_token": s["THREADS_ACCESS_TOKEN"]})
    set_secret("THREADS_ACCESS_TOKEN", r["access_token"])
    set_secret("THREADS_TOKEN_ISSUED", dt.datetime.now(dt.timezone.utc).isoformat())
    print(f"토큰 갱신 완료 (유효 {int(r.get('expires_in', 0)) // 86400}일)")


def cmd_stats():
    s = secrets()
    token = s["THREADS_ACCESS_TOKEN"]
    log = load_json(LOG, [])
    out = load_json(STATS, {})
    for x in log[-60:]:
        try:
            r = http("GET", f"{API}/{x['post_id']}/insights",
                     {"metric": "views,likes,replies,reposts,quotes,shares", "access_token": token})
            m = {d["name"]: d["values"][0]["value"] for d in r.get("data", [])}
            out[x["post_id"]] = {"id": x["id"], "type": x["type"], "posted_at": x["posted_at"],
                                 "product": x.get("product"), **m, "checked_at": now().isoformat()}
        except Exception as e:
            out.setdefault(x["post_id"], {})["error"] = str(e)[:200]
    # 형식(format) 정보 붙이기
    fmt = {x.get("post_id"): x.get("format") for x in load_json(QUEUE, []) + load_json(LOG, []) if x.get("post_id") and x.get("format")}
    for pid, v in out.items():
        if fmt.get(pid):
            v["format"] = fmt[pid]
    save_json(STATS, out)
    # 계정 지표 (팔로워, 일별 조회수)
    try:
        r = http("GET", f"{API}/{s['THREADS_USER_ID']}/threads_insights",
                 {"metric": "views,followers_count,likes,replies,reposts", "access_token": token})
        acc = {}
        for d in r.get("data", []):
            acc[d["name"]] = d["total_value"]["value"] if "total_value" in d else d["values"][-1]["value"]
        hist = load_json(os.path.join(BASE, "account_stats.json"), {})
        hist[now().date().isoformat()] = {**acc, "checked_at": now().isoformat()}
        save_json(os.path.join(BASE, "account_stats.json"), hist)
        print("계정:", acc)
    except Exception as e:
        print("계정 지표 실패", str(e)[:150])
    # 형식별 평균 조회수
    from collections import defaultdict
    agg = defaultdict(list)
    for v in out.values():
        agg[v.get("format") or "미분류"].append(v.get("views", 0))
    print("형식별 평균 조회:", {k: round(sum(x) / len(x)) for k, x in agg.items()})
    rows = sorted(out.values(), key=lambda v: v.get("views", 0), reverse=True)
    for v in rows[:10]:
        print(f"{v.get('views', 0):>7} 조회 | {v.get('type')} | {v.get('id')}")


def cmd_status():
    day, ph = phase_info()
    q = load_json(QUEUE, [])
    log = load_json(LOG, [])
    from collections import Counter
    print(f"{day}일차, 단계: {ph['name']} (하루 최대 {ph['max_posts']}건, 광고 {ph['max_ads']}건)")
    print("오늘 발행:", len(posted_today(log)), "건 (광고", len(posted_today(log, "ad")), ")")
    print("대기열:", dict(Counter(x.get("status") for x in q)))


# ---------- 답글 관리 / 성과 기반 링크 부착 ----------
PENDING = os.path.join(BASE, "pending_replies.json")
REPLIED = os.path.join(BASE, "replied.json")
CANDS = os.path.join(BASE, "link_candidates.json")
ATTACHED = os.path.join(BASE, "attached_links.json")
MY_NAME = "salim.ggultem.note"


def cmd_replies():
    """최근 14일 내 글에 달린, 아직 답하지 않은 다른 사람 답글을 pending_replies.json으로 수집"""
    s = secrets()
    token = s["THREADS_ACCESS_TOKEN"]
    log = load_json(LOG, [])
    replied = set(load_json(REPLIED, {}).keys())
    cutoff = now() - dt.timedelta(days=14)
    pending = []
    for x in log:
        if dt.datetime.fromisoformat(x["posted_at"]) < cutoff:
            continue
        try:
            r = http("GET", f"{API}/{x['post_id']}/conversation",
                     {"fields": "id,text,username,timestamp,replied_to,root_post", "access_token": token})
        except Exception as e:
            print("조회 실패", x["id"], str(e)[:120])
            continue
        items = r.get("data", [])
        mine_parent = {i.get("replied_to", {}).get("id") for i in items if i.get("username") == MY_NAME}
        for i in items:
            if i.get("username") == MY_NAME or i["id"] in replied or i["id"] in mine_parent:
                continue
            pending.append({"comment_id": i["id"], "username": i.get("username"), "text": i.get("text", ""),
                            "timestamp": i.get("timestamp"), "post_local_id": x["id"], "post_id": x["post_id"],
                            "post_type": x["type"], "product": x.get("product")})
    save_json(PENDING, pending)
    print(f"답할 답글 {len(pending)}개 -> pending_replies.json")
    for p in pending[:20]:
        print(f"- [{p['comment_id']}] @{p['username']}: {p['text'][:80]}")


import fcntl, contextlib


@contextlib.contextmanager
def file_lock(name, wait=60):
    """프로세스 간 잠금. 다른 실행이 잡고 있으면 최대 wait초 기다린 뒤 실패"""
    f = open(os.path.join(BASE, name), "w")
    t0 = time.time()
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() - t0 > wait:
                f.close()
                raise RuntimeError(f"다른 실행이 {name} 사용 중")
            time.sleep(2)
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def cmd_reply(comment_id, text):
    with file_lock(".reply.lock"):
        _cmd_reply(comment_id, text)


def _cmd_reply(comment_id, text):
    if comment_id in load_json(REPLIED, {}):
        print("이미 답한 답글, 건너뜀", comment_id)
        return
    # 오래된 목록으로 답하지 않기 (맥 잠자기 후 깨어난 실행 방지)
    if os.path.exists(PENDING) and time.time() - os.path.getmtime(PENDING) > 1800:
        print("답글 목록이 30분 넘게 지남. replies 명령으로 새로 받은 뒤 다시 시도")
        return
    # 스레드 서버에서 직접 확인: 이 답글에 내 답이 이미 있으면 건너뜀
    s0 = secrets()
    try:
        r = http("GET", f"{API}/{comment_id}/replies", {"fields": "id,username", "access_token": s0["THREADS_ACCESS_TOKEN"]})
        if any(i.get("username") == MY_NAME for i in r.get("data", [])):
            rep = load_json(REPLIED, {})
            rep[comment_id] = {"reply_id": None, "text": "(서버에 이미 답 있음)", "at": now().isoformat()}
            save_json(REPLIED, rep)
            print("스레드에 이미 내 답이 있음, 건너뜀", comment_id)
            return
    except Exception as e:
        print("서버 확인 실패, 안전을 위해 건너뜀", str(e)[:100])
        return
    if "coupang.com" in text and DISCLOSURE not in text:
        text = text.strip() + "\n\n" + DISCLOSURE
    if len(text) > 500:
        raise RuntimeError("답글 500자 초과")
    s = secrets()
    token, uid = s["THREADS_ACCESS_TOKEN"], s["THREADS_USER_ID"]
    cid = create_container(token, uid, text, reply_to=comment_id)
    time.sleep(3)
    rid = publish(token, uid, cid)
    rep = load_json(REPLIED, {})
    rep[comment_id] = {"reply_id": rid, "text": text, "at": now().isoformat()}
    save_json(REPLIED, rep)
    print("답글 완료", comment_id, rid)


def cmd_candidates(min_views=None):
    """조회수가 잘 나온 링크 없는 info 글 -> 나중에 상품 답글 붙일 후보"""
    stats = load_json(STATS, {})
    attached = load_json(ATTACHED, {})
    views = sorted([v.get("views", 0) for v in stats.values() if v.get("type") == "info"])
    if min_views is None:
        # 상위 25% 또는 최소 300회
        q = views[int(len(views) * 0.75)] if views else 0
        min_views = max(300, q)
    cutoff = now() - dt.timedelta(hours=72)
    cands = []
    for pid, v in stats.items():
        if v.get("type") != "info" or pid in attached:
            continue
        if not v.get("posted_at") or dt.datetime.fromisoformat(v["posted_at"]) < cutoff:
            continue
        if v.get("views", 0) >= min_views:
            cands.append({"post_id": pid, "id": v.get("id"), "views": v.get("views"), "posted_at": v["posted_at"]})
    q = load_json(QUEUE, []) + load_json(LOG, [])
    texts = {x.get("post_id"): x.get("text") for x in q if x.get("post_id")}
    for c in cands:
        c["text"] = texts.get(c["post_id"])
    cands.sort(key=lambda c: -c["views"])
    save_json(CANDS, cands)
    print(f"기준 조회수 {min_views}+ / 링크 부착 후보 {len(cands)}개 -> link_candidates.json")
    for c in cands[:10]:
        print(f"- {c['post_id']} {c['views']}회: {(c.get('text') or '')[:50]}")


def cmd_attach(post_id, text):
    """이미 터진 info 글에 상품 링크 답글 부착 (하루 링크 한도에 포함)"""
    if "link.coupang.com" not in text:
        raise RuntimeError("쿠팡 파트너스 링크 없음")
    if post_id in load_json(ATTACHED, {}):
        print("이미 링크 붙인 글, 건너뜀", post_id)
        return
    if DISCLOSURE not in text:
        text = text.strip() + "\n\n" + DISCLOSURE
    day, ph = phase_info()
    log = load_json(LOG, [])
    attached = load_json(ATTACHED, {})
    today = now().date().isoformat()
    used = len(posted_today(log, "ad")) + sum(1 for a in attached.values() if a["at"][:10] == today)
    limit = ph["max_ads"] + 1  # 성과 기반 부착은 하루 1건 추가 허용
    if day < config().get("attach_from_day", 4) or used >= limit:
        raise RuntimeError(f"오늘 링크 한도 초과 ({used}/{limit}, {day}일차)")
    s = secrets()
    token, uid = s["THREADS_ACCESS_TOKEN"], s["THREADS_USER_ID"]
    cid = create_container(token, uid, text, reply_to=post_id)
    time.sleep(3)
    rid = publish(token, uid, cid)
    attached[post_id] = {"reply_id": rid, "text": text, "at": now().isoformat()}
    save_json(ATTACHED, attached)
    print("링크 부착 완료", post_id, rid)


def cmd_test(text):
    item = {"id": "test-" + now().strftime("%H%M%S"), "type": "info", "text": text}
    res = post_item(item)
    log = load_json(LOG, [])
    log.append({"id": item["id"], "type": "info", "posted_at": now().isoformat(),
                "post_id": res["post_id"], "permalink": res.get("permalink")})
    save_json(LOG, log)
    print(res)


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "status"
    if c == "run":
        try:
            with file_lock(".run.lock", wait=5):
                cmd_refresh()
                cmd_run()
        except RuntimeError as e:
            print("건너뜀:", e)
    elif c == "refresh":
        cmd_refresh(force="--force" in sys.argv)
    elif c == "stats":
        cmd_stats()
    elif c == "test":
        cmd_test(sys.argv[2])
    elif c == "replies":
        cmd_replies()
    elif c == "reply":
        cmd_reply(sys.argv[2], sys.argv[3])
    elif c == "candidates":
        cmd_candidates(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif c == "attach":
        cmd_attach(sys.argv[2], sys.argv[3])
    else:
        cmd_status()
