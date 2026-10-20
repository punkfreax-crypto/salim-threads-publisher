#!/usr/bin/env python3
"""shorts/queue.json의 queued 작업을 mp4로 렌더링 (서버 전용: ffmpeg, edge-tts 필요).
결과 mp4는 out/<id>.mp4 로 저장하고 status=rendered."""
import asyncio, json, os, subprocess, urllib.request
import edge_tts
Q = "shorts/queue.json"
VOICE = {"salim": "ko-KR-SunHiNeural", "kitchen": "ko-KR-SunHiNeural", "season": "ko-KR-InJoonNeural"}

def dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", p]).decode().strip())

async def tts(text, voice, out):
    await edge_tts.Communicate(text, voice, rate="+12%").save(out)

def render(job):
    wd = f"/tmp/{job['id']}"; os.makedirs(wd, exist_ok=True)
    parts = []
    for i, (img, nar) in enumerate(zip(job["images"], job["narrations"]), 1):
        ip, ap, vp = f"{wd}/{i}.png", f"{wd}/{i}.mp3", f"{wd}/{i}.mp4"
        urllib.request.urlretrieve(img, ip)
        asyncio.run(tts(nar or " ", VOICE.get(job.get("account"), "ko-KR-SunHiNeural"), ap))
        d = dur(ap) + 0.35
        subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", ip, "-i", ap,
            "-filter_complex", f"[0:v]scale=1080:1920,zoompan=z='min(zoom+0.0006,1.06)':d={int(d*30)}:s=1080x1920:fps=30,format=yuv420p[v];[1:a]apad[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{d:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", vp])
        parts.append(vp)
    lst = f"{wd}/list.txt"
    open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
    os.makedirs("out", exist_ok=True)
    out = f"out/{job['id']}.mp4"
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out])
    return out, dur(out)

q = json.load(open(Q, encoding="utf-8"))
for job in q:
    if job.get("status") == "queued":
        try:
            out, d = render(job)
            job.update(status="rendered", video_path=out, duration=round(d, 1))
            print("렌더 완료", job["id"], round(d, 1), "초")
        except Exception as e:
            job.update(status="render_error", note=str(e)[:300]); print("렌더 실패", job["id"], e)
json.dump(q, open(Q, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
