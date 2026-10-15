"""Dev tool (not used by the app). Local CPU run of the engine on a segment of the test clip, with a detection cache so reruns only redo tracking/analytics.
usage: python run_local.py START DUR [--mode balanced] [--tag name] [--repo /home/user/trivela-labs] [--frames 1,2.5]
"""
import argparse, hashlib, json, os, gzip, subprocess, sys, time, collections
from pathlib import Path

W = Path(os.environ.get("TRIVELA_WORK", "/tmp/trivela-work"))   # clip_h264.mp4 + the detector weights live here
ap = argparse.ArgumentParser()
ap.add_argument("start", type=float); ap.add_argument("dur", type=float)
ap.add_argument("--mode", default="balanced"); ap.add_argument("--res", default="1080p")
ap.add_argument("--tag", default="run"); ap.add_argument("--repo", default="/home/user/trivela-labs")
ap.add_argument("--frames", default=""); ap.add_argument("--noburn", action="store_true")
a = ap.parse_args()

seg = W / f"seg_{a.start:g}_{a.dur:g}.mp4"
if not seg.exists():
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(a.start), "-i", str(W / "clip_h264.mp4"), "-t", str(a.dur),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", str(seg)], check=True)
out = W / "out" / a.tag
os.environ["TRIVELA_WORKSPACE"] = str(out)
os.chdir(W)                                    # the weights are here (MODEL_PATH is relative)
sys.path.insert(0, str(Path(a.repo) / "CV"))
import numpy as np, torch
torch.set_num_threads(4)
import app as engine
from ultralytics import YOLO as _YOLO
from ultralytics.engine.results import Boxes

CACHE = W / "detcache"; CACHE.mkdir(exist_ok=True)

class _R:
    def __init__(self, data, shape): self.boxes = Boxes(data, shape)

class CachedYOLO:
    """Same predict() interface; results cached per (frame content, imgsz, conf)."""
    def __init__(self, path): self.m = _YOLO(path); self.names = self.m.names; self.hits = 0; self.miss = 0
    def predict(self, frames, imgsz=640, conf=0.25, iou=0.7, **kw):
        out = [None] * len(frames); todo = []
        keys = []
        for i, fr in enumerate(frames):
            k = hashlib.md5(np.ascontiguousarray(fr[::7, ::7]).tobytes()).hexdigest() + f"_{imgsz}_{conf}_{iou}"
            keys.append(k); p = CACHE / (k + ".npy")
            if p.exists(): out[i] = _R(torch.from_numpy(np.load(p)), fr.shape[:2]); self.hits += 1
            else: todo.append(i)
        if todo:
            kw2 = {k_: v for k_, v in kw.items() if k_ not in ("half", "device")}
            res = self.m.predict([frames[i] for i in todo], imgsz=imgsz, conf=conf, iou=iou, device="cpu", **kw2)
            for i, r in zip(todo, res):
                d = r.boxes.data.cpu().numpy(); np.save(CACHE / (keys[i] + ".npy"), d)
                out[i] = _R(torch.from_numpy(d), frames[i].shape[:2]); self.miss += 1
        return out

engine.YOLO = CachedYOLO
t0 = time.time()
engine.process_football_match(str(seg), a.mode, a.res, not a.noburn, True)
J = engine.JOB_STATE
print(f"status={J['status']} err={J.get('error_msg')} time={time.time() - t0:.0f}s")
proc = out / "processed"
s = json.load(open(proc / "summary.json"))
m = json.load(gzip.open(proc / "tactical_metadata.json.gz"))["frames"]
fps = s["fps"]; st = s["stats"]
print("perf", s.get("perf"))
print("pitch", s.get("pitch"))
print({k: v for k, v in st.items() if k not in ("players", "top_speeds")})
ev = s["events"]; print("events", collections.Counter(e[1] for e in ev))
for e in ev:
    if e[1] in ("strike", "shot", "goal", "save"): print("  ", round(e[0] / fps + a.start, 1), e)
print("shots map", (s.get("maps") or {}).get("shots"))
ti = s["team_info"]; print("labels", collections.Counter(ti.values()))
# per-second: people tracked (by label) and ball state
codes = collections.defaultdict(collections.Counter); ballc = collections.Counter(); n = collections.Counter()
for k, f in m.items():
    sec = int(int(k) / fps); n[sec] += 1
    for p in f["p"]: codes[sec][p[5]] += 1
    b = f.get("b"); ballc[sec] += (0 if b is None else 1 if b[2] == 0 else 0.0)
line = []
for sec in range(int(a.dur) + 1):
    if not n[sec]: line.append(f"{sec + a.start:5.0f}s  -"); continue
    c = codes[sec]
    line.append(f"{sec + a.start:5.0f}s A{c[0] / n[sec]:4.1f} B{c[1] / n[sec]:4.1f} GK{c[2] / n[sec]:3.1f} OFF{c[3] / n[sec]:3.1f} ball{100 * ballc[sec] / n[sec]:3.0f}%")
print("\n".join(line))
for t in [float(x) for x in a.frames.split(",") if x.strip()]:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t - a.start:.2f}", "-i", str(proc / "tactical_output.mp4"),
                    "-frames:v", "1", "-q:v", "3", str(out / f"frame_{t:07.2f}.jpg")])
print("out:", out)
