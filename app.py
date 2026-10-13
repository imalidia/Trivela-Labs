import os, cv2, time, json, math, gzip, shutil, threading, queue, subprocess, asyncio
import numpy as np
import torch
import yaml
from collections import deque, defaultdict, Counter
from pathlib import Path
from typing import Dict, Any
from fastapi import FastAPI, Request, Form, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from ultralytics import YOLO
from ultralytics.utils import IterableSimpleNamespace

app = FastAPI(title="Tactical Vision AI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Everything is relative to the project folder, so the app works from any working directory.
ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = Path(os.environ.get("TRIVELA_WORKSPACE", ROOT / "tactical_workspace"))
UPLOAD_DIR = BASE_DIR / "uploads"
PARTS_DIR = UPLOAD_DIR / "parts"
PROCESSED_DIR = BASE_DIR / "processed"
# Videos you already have on disk go in VIDEO_DIR (the "Local videos" tab in the app).
VIDEO_DIR = Path(os.environ.get("TRIVELA_VIDEO_DIR", ROOT / "videos"))
for d in [UPLOAD_DIR, PROCESSED_DIR, VIDEO_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# The football detection model is downloaded automatically the first time an analysis starts.
MODEL_REPO = "martinjolif/yolo-football-player-detection"
MODEL_FILE = "yolo-football-player-detection.pt"
MODEL_DIR = Path(os.environ.get("TRIVELA_MODEL_DIR", ROOT / "models"))
MODEL_PATH = str(MODEL_DIR / MODEL_FILE)

def ensure_model() -> str:
    """Return the path to the model file, downloading it from Hugging Face if it is not there yet."""
    if not Path(MODEL_PATH).exists():
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        print("Downloading the football model (first run only)...", flush=True)
        from huggingface_hub import hf_hub_download
        hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE, local_dir=str(MODEL_DIR))
    return MODEL_PATH
HAS_GPU = torch.cuda.is_available()
DEVICE_TARGET = 0 if HAS_GPU else "cpu"
DEVICE_NAME = torch.cuda.get_device_name(0) if HAS_GPU else "CPU"

MODES = {
    "lightning": dict(imgsz=960,  stride=4, person_conf=0.30, batch=24),   # fastest: detect every 4th frame
    "fast":      dict(imgsz=960,  stride=2, person_conf=0.30, batch=16),   # default: detect every 2nd frame
    "balanced":  dict(imgsz=1280, stride=2, person_conf=0.28, batch=12),
    "ultra":     dict(imgsz=1600, stride=1, person_conf=0.25, batch=8),    # most accurate: every frame
}
RES_H = {"720p": 720, "1080p": 1080, "1440p": 1440, "source": 0}
BALL_MIN_CONF = 0.10
DET_CONF = 0.10
SC = 0.5            # scale of the grass-mask working image
WIDE_MIN = 0.12     # minimum grass ratio to treat a shot as a wide tactical view
MIN_TRACK_LEN = 25  # frames a track must live to be listed as a real player
CAM_SC = 0.25        # scale used for camera-motion estimation
PLAYER_H_M = 1.78    # average player height (metres) used as a pixel->metre ruler
SPRINT_MS = 7.0      # m/s (~25 km/h) counts as a sprint
STRIKE_KMH = 75.0    # smoothed ball speed that counts as a strike

TEAM_CODE = {"team_a": 0, "team_b": 1, "goalkeeper": 2, "official": 3}
DEFAULT_BGR = {"team_a": (60, 60, 225), "team_b": (245, 205, 60), "goalkeeper": (220, 70, 217), "official": (30, 210, 250)}

JOB_LOCK = threading.Lock()
JOB_CANCEL = threading.Event()
JOB_STATE: Dict[str, Any] = {
    "status": "idle", "phase": "", "filename": "", "total_frames": 0, "current_frame": 0,
    "fps": 0.0, "progress": 0.0, "eta": 0, "video_url": "", "meta_url": "", "error_msg": ""
}
BUSY = ("scanning", "processing", "finalizing")

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        return super().default(obj)

def even(v):
    return max(2, int(round(v / 2.0)) * 2)

def probe_duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out)
    except Exception:
        return 0.0

# ----------------------------------------------------------------------------
# PITCH / SCENE UNDERSTANDING
# ----------------------------------------------------------------------------
class GrassModel:
    """Adaptive turf colour model: finds the dominant green hue of the pitch instead of a fixed range."""
    def __init__(self):
        self.lo = np.array([28, 30, 25], np.uint8)
        self.hi = np.array([92, 255, 255], np.uint8)

    def calibrate(self, frame):
        small = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        cand = (h >= 22) & (h <= 105) & (s >= 40) & (v >= 30)
        if cand.sum() < 0.04 * cand.size:
            return
        hist = np.bincount(h[cand].ravel(), minlength=180).astype(np.float32)
        hist = np.convolve(hist, np.ones(5, np.float32), mode="same")
        peak = int(np.argmax(hist))
        self.lo = np.array([max(15, peak - 16), 30, 25], np.uint8)
        self.hi = np.array([min(115, peak + 16), 255, 255], np.uint8)

    def mask(self, bgr):
        return cv2.inRange(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV), self.lo, self.hi)

class CutDetector:
    def __init__(self): self.prev = None
    def reset(self): self.prev = None
    def check(self, frame):
        thumb = cv2.cvtColor(cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2HSV)
        h = cv2.calcHist([thumb], [0, 1], None, [16, 8], [0, 180, 0, 256])
        cv2.normalize(h, h)
        cut = False
        if self.prev is not None:
            cut = cv2.compareHist(self.prev, h, cv2.HISTCMP_CORREL) < 0.5
        self.prev = h
        return cut

def valid_geometry(b, W, H):
    w = b[2] - b[0]; h = b[3] - b[1]
    if h < max(18, int(H * 0.022)) or h > int(H * 0.40): return False
    if w < 6 or w > int(W * 0.16): return False
    return 1.15 <= h / max(1, w) <= 5.5

def on_turf(gm, b):
    x1, y1, x2, y2 = [int(v * SC) for v in b]
    h = max(1, y2 - y1); w = max(1, x2 - x1)
    fy1 = min(max(0, int(y2 - 0.18 * h)), gm.shape[0] - 1)
    fy2 = min(gm.shape[0], y2 + 4)
    fx1 = max(0, int(x1 - 0.1 * w)); fx2 = min(gm.shape[1], x2 + int(0.1 * w))
    p = gm[fy1:fy2, fx1:fx2]
    return p.size > 0 and (np.count_nonzero(p) / p.size) >= 0.20

def grass_top(gm):
    # first image row that is mostly turf: roughly the far touchline in a wide shot
    rows = np.flatnonzero(gm.mean(axis=1) > 0.45 * 255)
    return int(rows[0] / SC) if rows.size else 0

# ----------------------------------------------------------------------------
# TEAM CLASSIFICATION (K-means kit clustering + per-track temporal voting)
# ----------------------------------------------------------------------------
LABW = np.array([0.7, 1.3, 1.3], np.float32)

class TeamModel:
    def __init__(self, grass):
        self.grass = grass
        self.samples = []
        self.centroids = None
        self.votes = defaultdict(Counter)

    @property
    def ready(self): return self.centroids is not None

    def feature(self, frame, b):
        x1, y1, x2, y2 = [int(v) for v in b]
        w = max(1, x2 - x1); h = max(1, y2 - y1)
        ty1 = max(0, int(y1 + 0.18 * h)); ty2 = min(frame.shape[0], int(y1 + 0.52 * h))
        tx1 = max(0, int(x1 + 0.22 * w)); tx2 = min(frame.shape[1], int(x2 - 0.22 * w))
        if ty2 - ty1 < 3 or tx2 - tx1 < 3: return None
        torso = frame[ty1:ty2, tx1:tx2]
        g = cv2.inRange(cv2.cvtColor(torso, cv2.COLOR_BGR2HSV), self.grass.lo, self.grass.hi)
        keep = torso[g == 0]
        if len(keep) < 12: return None
        lab = cv2.cvtColor(keep.reshape(-1, 1, 3), cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
        return np.median(lab, axis=0)

    def fit(self):
        X = np.array(self.samples, np.float32) * LABW
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 60, 0.5)
        _, _, c = cv2.kmeans(X, 2, None, crit, 8, cv2.KMEANS_PP_CENTERS)
        d = np.min(np.linalg.norm(X[:, None, :] - c[None, :, :], axis=2), axis=1)
        Xt = X[d <= np.percentile(d, 85)]                 # trim outliers (refs, staff, shadows) and refit
        if len(Xt) >= 40:
            _, _, c = cv2.kmeans(Xt, 2, None, crit, 8, cv2.KMEANS_PP_CENTERS)
        c = c / LABW
        self.centroids = c[np.argsort(c[:, 0])]            # darker kit = Team A, lighter = Team B

    def kit_bgr(self):
        out = dict(DEFAULT_BGR)
        if self.ready:
            bgr = cv2.cvtColor(self.centroids.reshape(-1, 1, 3).clip(0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR).reshape(-1, 3)
            out["team_a"] = tuple(int(v) for v in bgr[0]); out["team_b"] = tuple(int(v) for v in bgr[1])
        return out

    def nearest(self, f):
        return "team_a" if np.argmin(np.linalg.norm((self.centroids - f) * LABW, axis=1)) == 0 else "team_b"

    def quick(self, frame, box):
        if not self.ready: return None
        f = self.feature(frame, np.asarray(box).astype(int))
        return None if f is None else self.nearest(f)

    def label_for(self, cid, frame, box, idx):
        c = self.votes[cid]
        if sum(c.values()) < 12 or (idx + cid) % 20 == 0:
            f = self.feature(frame, box)
            if f is not None:
                if self.ready:
                    c[self.nearest(f)] += 1
                else:
                    self.samples.append(f)
                    if len(self.samples) >= 150: self.fit()
        return c.most_common(1)[0][0] if c else "team_a"

# ----------------------------------------------------------------------------
# TRACKING HELPERS
# ----------------------------------------------------------------------------
TRACKER_YAML = BASE_DIR / "botsort_football.yaml"
TRACKER_YAML.write_text("""tracker_type: botsort
track_high_thresh: 0.35
track_low_thresh: 0.10
new_track_thresh: 0.45
track_buffer: 150
match_thresh: 0.80
fuse_score: True
gmc_method: sparseOptFlow
proximity_thresh: 0.6
appearance_thresh: 0.25
with_reid: False
model: auto
""")

class SimpleTracker:
    """Last-resort greedy IoU tracker, only used if the Ultralytics trackers fail to initialise."""
    def __init__(self): self.t = []; self.next = 1
    def reset(self): self.t = []
    @staticmethod
    def iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        i = ix * iy; u = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - i
        return i / u if u > 0 else 0
    def update(self, det, img=None):
        xy = det.xyxy; cf = det.conf; cl = det.cls
        pairs = []
        for di in range(len(xy)):
            for ti, t in enumerate(self.t):
                pb = t["box"] + np.tile(t["vel"], 2)
                v = self.iou(xy[di], pb)
                if v > 0.25: pairs.append((v, di, ti))
        pairs.sort(reverse=True)
        md, mt, rows = set(), set(), []
        for v, di, ti in pairs:
            if di in md or ti in mt: continue
            md.add(di); mt.add(ti); t = self.t[ti]
            nb = xy[di].astype(np.float32)
            t["vel"] = ((nb[:2] + nb[2:]) - (t["box"][:2] + t["box"][2:])) / 2.0
            t["box"] = nb; t["miss"] = 0
            rows.append([*nb, t["id"], cf[di], cl[di], di])
        for di in range(len(xy)):
            if di in md: continue
            self.t.append({"id": self.next, "box": xy[di].astype(np.float32), "vel": np.zeros(2, np.float32), "miss": 0})
            rows.append([*xy[di], self.next, cf[di], cl[di], di]); self.next += 1
        for ti, t in enumerate(self.t):
            if ti not in mt and t["miss"] is not None: t["miss"] += 1
        self.t = [t for t in self.t if t["miss"] <= 40]
        return np.array(rows, np.float32) if rows else np.zeros((0, 8), np.float32)

def make_tracker(fps):
    args = IterableSimpleNamespace(**yaml.safe_load(TRACKER_YAML.read_text()))
    fr = max(1, int(round(fps)))
    try:
        from ultralytics.trackers.bot_sort import BOTSORT
        return BOTSORT(args, frame_rate=fr), "BoT-SORT + camera-motion compensation"
    except Exception as e:
        print("BoT-SORT unavailable:", e)
    try:
        from ultralytics.trackers.byte_tracker import BYTETracker
        return BYTETracker(args, frame_rate=fr), "ByteTrack"
    except Exception as e:
        print("ByteTrack unavailable:", e)
    return SimpleTracker(), "Built-in IoU tracker"

def box_iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy; u = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - i
    return i / u if u > 0 else 0.0

class CameraMotion:
    # global pan estimate via phase correlation -> world coordinates that survive broadcast camera pans
    def __init__(self):
        self.prev = None; self.win = None
        self.cum = np.zeros(2, np.float32); self.shift = np.zeros(2, np.float32)
    def reset(self):
        self.prev = None; self.cum[:] = 0; self.shift[:] = 0
    def update(self, frame):
        g = cv2.cvtColor(cv2.resize(frame, None, fx=CAM_SC, fy=CAM_SC, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if self.win is None or self.win.shape != g.shape:
            self.win = cv2.createHanningWindow((g.shape[1], g.shape[0]), cv2.CV_32F)
        self.shift[:] = 0
        if self.prev is not None and self.prev.shape == g.shape:
            (dx, dy), resp = cv2.phaseCorrelate(self.prev, g, self.win)
            if resp > 0.05 and abs(dx) < g.shape[1] * 0.2 and abs(dy) < g.shape[0] * 0.2:
                self.shift[:] = (dx / CAM_SC, dy / CAM_SC)
        self.cum += self.shift; self.prev = g

def appearance(frame, b, grass):
    # kit/skin/shorts colour signature (HSV+V histogram of torso and legs, grass removed)
    x1, y1, x2, y2 = [int(v) for v in b]; w = max(1, x2 - x1); h = max(1, y2 - y1)
    out = []
    for a0, a1 in ((0.15, 0.48), (0.52, 0.85)):
        ya = max(0, int(y1 + a0 * h)); yb = min(frame.shape[0], int(y1 + a1 * h))
        xa = max(0, int(x1 + 0.2 * w)); xb = min(frame.shape[1], int(x2 - 0.2 * w))
        if yb - ya < 3 or xb - xa < 3: return None
        hsv = cv2.cvtColor(frame[ya:yb, xa:xb], cv2.COLOR_BGR2HSV)
        m = (cv2.inRange(hsv, grass.lo, grass.hi) == 0).astype(np.uint8)
        if m.sum() < 8: m = np.ones_like(m)
        hist = cv2.calcHist([hsv], [0, 1, 2], m, [8, 3, 3], [0, 180, 0, 256, 0, 256]).flatten()
        t = hist.sum(); out.append(hist / t if t > 0 else hist)
    return np.concatenate(out).astype(np.float32) / 2.0

def app_dist(a, b):
    return float(np.clip(1.0 - np.sum(np.sqrt(a * b)), 0.0, 1.0))

class IdentityManager:
    # Globally unique IDs. Tracker IDs restart after every reset, so a base offset keeps identities from colliding.
    # Lost tracks are re-linked with Hungarian matching on WORLD-space motion + appearance + team (never across teams),
    # and IDs that may have been swapped after an occlusion are verified against an appearance gallery.
    def __init__(self, fps):
        self.fps = fps; self.max_age = int(fps * 3.0)
        self.alias = {}; self.info = {}; self.raw_of = {}; self.overlap = {}; self.last_ov = {}
        self.base = 0; self.next_id = 1
    def new_shot(self):
        self.alias.clear(); self.raw_of.clear(); self.overlap.clear(); self.last_ov.clear(); self.info.clear()
        self.base = self.next_id
    def _mint(self, raw):
        cid = raw + self.base; self.next_id = max(self.next_id, cid + 1); return cid
    def assign_new(self, f, fresh, active):
        if not fresh: return
        lost = [(cid, i) for cid, i in self.info.items() if cid not in active and 1 <= f - i["last"] <= self.max_age]
        matched = {}
        if lost:
            C = np.full((len(fresh), len(lost)), 9.0, np.float32)
            for a, n in enumerate(fresh):
                for b, (cid, i) in enumerate(lost):
                    if i["cls"] != n["cls"] or (n["lab"] and i["lab"] and n["lab"] != i["lab"]): continue
                    if not 0.65 < n["h"] / max(1.0, i["h"]) < 1.55: continue
                    age = f - i["last"]
                    pred = i["wpos"] + i["wvel"] * min(age, self.fps * 0.6)
                    d = float(np.hypot(*(n["wpos"] - pred))) / max(n["h"], i["h"])
                    lim = 1.0 + 3.0 * age / self.fps
                    if d > lim: continue
                    ad = app_dist(n["app"], i["app"]) if (n["app"] is not None and i["app"] is not None) else 0.25
                    if ad > 0.55: continue
                    C[a, b] = 0.55 * d / lim + 0.45 * min(1.0, ad / 0.5)
            try:
                from scipy.optimize import linear_sum_assignment
                rr, cc = linear_sum_assignment(C)
            except Exception:
                order = np.dstack(np.unravel_index(np.argsort(C, axis=None), C.shape))[0]
                ra, ca, rr, cc = set(), set(), [], []
                for a, b in order:
                    if a in ra or b in ca: continue
                    ra.add(a); ca.add(b); rr.append(a); cc.append(b)
            for a, b in zip(rr, cc):
                if C[a, b] < 0.75: matched[fresh[a]["raw"]] = lost[b][0]
        for n in fresh:
            self.alias[n["raw"]] = matched.get(n["raw"]) or self._mint(n["raw"])
    def update(self, cid, raw, f, box, cls, lab, wpos, h, app, clean):
        i = self.info.get(cid)
        if i is None:
            self.info[cid] = dict(last=f, wpos=wpos.copy(), wvel=np.zeros(2, np.float32), h=h, cls=cls, lab=lab, app=app, box=box.copy())
        else:
            dt = max(1, f - i["last"])
            i["wvel"] = 0.7 * i["wvel"] + 0.3 * ((wpos - i["wpos"]) / dt)
            i["wpos"] = wpos.copy(); i["h"] = 0.9 * i["h"] + 0.1 * h; i["last"] = f; i["lab"] = lab; i["box"] = box.copy()
            if app is not None and (clean or i["app"] is None):
                i["app"] = app if i["app"] is None else 0.92 * i["app"] + 0.08 * app
        self.raw_of[cid] = raw
    def note_overlaps(self, f, boxes):
        ids = list(boxes)
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                a, b = ids[x], ids[y]
                if box_iou(boxes[a], boxes[b]) > 0.12:
                    self.overlap[(min(a, b), max(a, b))] = f; self.last_ov[a] = f; self.last_ov[b] = f
    def check_swaps(self, f, boxes, frame, grass):
        swaps = []
        for (a, b), lf in list(self.overlap.items()):
            if f - lf > 60:
                del self.overlap[(a, b)]; continue
            if f - lf < 6 or a not in boxes or b not in boxes or box_iou(boxes[a], boxes[b]) > 0.03: continue
            ia, ib = self.info.get(a), self.info.get(b)
            if ia is None or ib is None or ia["app"] is None or ib["app"] is None or ia["cls"] != ib["cls"]:
                del self.overlap[(a, b)]; continue
            ca = appearance(frame, boxes[a], grass); cb = appearance(frame, boxes[b], grass)
            if ca is None or cb is None: continue
            direct = app_dist(ca, ia["app"]) + app_dist(cb, ib["app"])
            swapped = app_dist(ca, ib["app"]) + app_dist(cb, ia["app"])
            if swapped + 0.10 < direct:
                ra, rb = self.raw_of.get(a), self.raw_of.get(b)
                if ra is not None and rb is not None:
                    self.alias[ra] = b; self.alias[rb] = a
                    self.raw_of[a], self.raw_of[b] = rb, ra
                    for key in ("wpos", "wvel", "h", "box", "last"): ia[key], ib[key] = ib[key], ia[key]
                    swaps.append((a, b))
            del self.overlap[(a, b)]
        return swaps

# ----------------------------------------------------------------------------
# CROSS-SHOT RE-IDENTIFICATION: every tracklet (one player inside one shot) is clustered into a persistent player.
# Signal = head/hair-skin, shirt, shorts and socks colour signature. Hard constraints: same team/role only, and two
# tracklets that overlap in time can never be the same person. The squad-size prior (10 outfield per team) stops the
# engine from inventing hundreds of players.
# ----------------------------------------------------------------------------
PW = np.array([0.35, 0.15, 0.20, 0.30], np.float32)      # head, torso, shorts, socks weights
REID_T_LO = 0.16
REID_T_HI = 0.50
REID_TARGET = {"team_a": 10, "team_b": 10, "goalkeeper": 2, "official": 3}

def signature(frame, b, grass):
    x1, y1, x2, y2 = [int(v) for v in b]; w = max(1, x2 - x1); h = max(1, y2 - y1)
    out = []
    for a0, a1, cx in ((0.0, 0.15, 0.28), (0.17, 0.48, 0.18), (0.50, 0.68, 0.18), (0.72, 0.96, 0.22)):
        ya = max(0, int(y1 + a0 * h)); yb = min(frame.shape[0], int(y1 + a1 * h))
        xa = max(0, int(x1 + cx * w)); xb = min(frame.shape[1], int(x2 - cx * w))
        if yb - ya < 2 or xb - xa < 2: return None
        hsv = cv2.cvtColor(frame[ya:yb, xa:xb], cv2.COLOR_BGR2HSV)
        m = (cv2.inRange(hsv, grass.lo, grass.hi) == 0).astype(np.uint8)
        if m.sum() < 4: m = np.ones_like(m)
        hist = cv2.calcHist([hsv], [0, 1, 2], m, [8, 3, 3], [0, 180, 0, 256, 0, 256]).flatten()
        t = hist.sum(); out.append(hist / t if t > 0 else hist)
    return np.stack(out).astype(np.float32)

def sig_dists(C, t):
    return ((1.0 - np.sqrt(C * t[None]).sum(axis=2)) * PW[None]).sum(axis=1)

def reid_cluster(items, group):
    target = REID_TARGET[group]
    items = sorted(items, key=lambda it: -it["w"])
    byid = {it["cid"]: it for it in items}
    cents, wsum, mem, assign = [], [], [], {}
    def conflict(k, it): return any(a < it["t1"] and it["t0"] < b for a, b, _ in mem[k])
    def pick(it):
        cand = [k for k in range(len(cents)) if not conflict(k, it)]
        if not cand: return None, 9.0
        ds = sig_dists(np.stack([cents[k] for k in cand]), it["sig"]); j = int(np.argmin(ds))
        return cand[j], float(ds[j])
    def put(k, it):
        if k == len(cents):
            cents.append(it["sig"].copy()); wsum.append(it["w"]); mem.append([])
        else:
            cents[k] = (cents[k] * wsum[k] + it["sig"] * it["w"]) / (wsum[k] + it["w"]); wsum[k] += it["w"]
        mem[k].append((it["t0"], it["t1"], it["cid"])); assign[it["cid"]] = k
    # seeding: longest / best-quality tracklets first, so the first full-squad shot creates the identities
    for it in items:
        k, d = pick(it)
        if it["short"]:
            if k is not None and d <= REID_T_HI: put(k, it)
            continue
        new = k is None or (len(cents) < target + 3 and (d > REID_T_HI or (len(cents) < target and d > REID_T_LO)))
        put(len(cents) if new else k, it)
    # refinement: k-means style re-assignment under the same constraints
    for _ in range(2):
        for k in range(len(cents)):
            ms = [byid[c] for c in assign if assign[c] == k]
            if ms:
                W_ = sum(m["w"] for m in ms); cents[k] = sum(m["sig"] * m["w"] for m in ms) / W_
        for it in items:
            c = it["cid"]
            if c in assign:
                k0 = assign[c]; mem[k0] = [m for m in mem[k0] if m[2] != c]
            else:
                k0 = None
            k, d = pick(it)
            if k is None or d > REID_T_HI:
                k = k0
            if k is None:
                assign.pop(c, None); continue
            mem[k].append((it["t0"], it["t1"], c)); assign[c] = k
    confs = []
    for k in range(len(cents)):
        ms = [byid[c] for c in assign if assign[c] == k]
        if not ms: confs.append(0); continue
        W_ = sum(m["w"] for m in ms); cents[k] = sum(m["sig"] * m["w"] for m in ms) / W_
        dbar = sum(float(sig_dists(cents[k][None], m["sig"])[0]) * m["w"] for m in ms) / W_
        c_ = 100.0 * max(0.0, 1.0 - dbar / 0.25)
        if len(ms) < 3: c_ = min(c_, 80.0)
        confs.append(int(round(c_)))
    return assign, cents, confs

class BallTracker:
    """Kalman-filtered ball with gating, re-acquisition and gap interpolation."""
    def __init__(self):
        k = cv2.KalmanFilter(4, 2)
        k.transitionMatrix = np.array([[1,0,1,0],[0,1,0,1],[0,0,1,0],[0,0,0,1]], np.float32)
        k.measurementMatrix = np.array([[1,0,0,0],[0,1,0,0]], np.float32)
        k.processNoiseCov = np.eye(4, dtype=np.float32) * 0.5
        k.measurementNoiseCov = np.eye(2, dtype=np.float32) * 3
        self.kf = k; self.reset()
    def reset(self):
        self.init = False; self.lost = 99; self.trail = deque(maxlen=26)
    def update(self, cands, W):
        pred = None
        if self.init:
            p = self.kf.predict(); pred = (float(p[0, 0]), float(p[1, 0]))
        pick = None
        if cands:
            if pred is None:
                c = max(cands, key=lambda t: t[2])
                if c[2] >= 0.25: pick = c
            else:
                gate = min(0.30 * W, max(45.0, 0.05 * W * (1 + 0.6 * self.lost)))
                bs = -1e9
                for c in cands:
                    d = math.hypot(c[0] - pred[0], c[1] - pred[1])
                    if d <= gate or c[2] >= 0.6:
                        s = c[2] - 0.8 * d / gate
                        if s > bs: bs, pick = s, c
        if pick is not None:
            if not self.init:
                self.kf.statePost = np.array([[pick[0]], [pick[1]], [0], [0]], np.float32)
                self.kf.errorCovPost = np.eye(4, dtype=np.float32) * 10
                self.init = True
            else:
                self.kf.correct(np.array([[pick[0]], [pick[1]]], np.float32))
            st = self.kf.statePost; self.lost = 0
            out = (float(st[0, 0]), float(st[1, 0]), float(pick[2]), False)
        elif self.init:
            self.lost += 1
            if self.lost > 14:
                self.reset(); return None
            self.kf.statePost = self.kf.statePre.copy(); self.kf.errorCovPost = self.kf.errorCovPre.copy()
            out = (pred[0], pred[1], 0.0, True)
        else:
            return None
        self.trail.append((out[0], out[1]))
        return out

class PossessionEngine:
    """Possession with hysteresis: a player must win the ball for N frames and the ball must stay free for a while to release."""
    def __init__(self, fps):
        self.enter = 3
        self.release = max(6, int(fps * 0.4))
        self.pass_gap = int(fps * 3)
        self.owner = None; self.owner_team = None
        self.cand = None; self.cand_n = 0; self.free = 0
        self.team_frames = Counter(); self.on_ball = Counter(); self.touches = Counter()
        self.passes = Counter(); self.gains = Counter()
        self.events = []; self.evlog = []; self.links = Counter()
        self.last_owner = None; self.last_team = None; self.last_owned = -10**9

    def drop(self, f):
        if self.owner is not None:
            self.owner = None; self.owner_team = None; self.events.append([f, -1, -1])
        self.cand = None; self.cand_n = 0; self.free = 0

    def step(self, f, ball, ball_lost, people):
        cand = None
        if ball is not None and ball_lost <= 4:
            bx, by = ball[0], ball[1]; best = 0.55
            for cid, b, lab in people:
                if lab == "official": continue
                h = max(1, b[3] - b[1]); fx = (b[0] + b[2]) / 2.0
                d = math.hypot(bx - fx, by - (b[3] - 0.12 * h)) / h
                if d < best: best, cand = d, cid
        if cand is not None and cand == self.owner:
            self.free = 0; self.cand = None; self.cand_n = 0
        elif cand is not None:
            if cand == self.cand: self.cand_n += 1
            else: self.cand, self.cand_n = cand, 1
            if self.cand_n >= self.enter:
                lab = next((l for c, b, l in people if c == cand), "team_a")
                team = 0 if lab == "team_a" else 1 if lab == "team_b" else self.last_team
                if self.last_owner is not None and self.last_owner != cand and team is not None and self.last_team is not None \
                        and f - self.last_owned <= self.pass_gap:
                    if team == self.last_team:
                        self.passes[team] += 1; self.links[(self.last_owner, cand)] += 1
                        self.evlog.append([f, "pass", cand, team, self.last_owner])
                    else:
                        self.gains[team] += 1; self.evlog.append([f, "won", cand, team, self.last_owner])
                self.touches[cand] += 1
                self.owner = cand; self.owner_team = team; self.last_owner = cand
                if team is not None: self.last_team = team
                self.events.append([f, cand, team if team is not None else -1])
                self.cand = None; self.cand_n = 0; self.free = 0
        else:
            self.cand = None; self.cand_n = 0
            if self.owner is not None:
                self.free += 1
                if self.free >= self.release: self.drop(f)
        if self.owner is not None:
            self.on_ball[self.owner] += 1; self.last_owned = f
            if self.owner_team is not None: self.team_frames[self.owner_team] += 1

# ----------------------------------------------------------------------------
# MATCH ANALYTICS: approximate pitch mapping + event stats, run once after tracking.
# Pure Python over the per-frame log, so it never slows the tracking loop down.
# Pitch positions are ESTIMATES: broadcast footage has no calibration, so x comes from the camera pan
# (player height as the ruler) anchored on the goalkeepers, and y from the image row.
# ----------------------------------------------------------------------------
PITCH_L, PITCH_W = 105.0, 68.0
GOAL_Y0, GOAL_Y1 = 30.34, 37.66
BOX_D, BOX_Y0, BOX_Y1 = 16.5, 13.84, 54.16
THIRD = PITCH_L / 3.0
GK_DEPTH_M = 9.0        # typical goalkeeper distance from his goal line: the pitch anchor
DEPTH_GAMMA = 0.8       # the far half of the pitch is squeezed into fewer image rows
GK_MIN_PLAYERS = 4      # outfield players needed in view to tell which goal a keeper stands in
REF_ROW = 0.6           # image row (fraction of the height) at which the camera pan is measured in metres
HEAT_NX, HEAT_NY = 21, 14
PHEAT_NX, PHEAT_NY = 12, 8
KICK_KMH = 40.0         # ball speed that marks a kick
REARM_KMH = 18.0        # the ball must slow below this before another kick counts
KICK_WIN_S = 0.3        # window used to measure ball speed
KICK_TRACK_S = 1.2      # how long a kick's flight is followed
TACKLE_M, TACKLE_S = 3.0, 0.7
LONG_BALL_M = 30.0
SHOT_RANGE_M = 32.0
DRIBBLE_M, DRIBBLE_PRESS_M = 5.0, 2.0
DEAD_BALL_S, DEAD_BALL_M = 1.0, 0.8
SAVE_WINDOW_S, SAVE_REACH_M = 2.5, 2.5
MAX_MAP_PASSES = 1500
COUNT_STATS = ("tackles", "interceptions", "clearances", "dispossessed", "dribbles", "crosses", "crosses_ok",
               "box_touches", "shots", "shots_on", "saves", "corners", "free_kicks", "long_balls", "long_balls_att")

def _frames(path):
    with open(path) as fin:
        for line in fin:
            fi, _, body = line.rstrip("\n").partition("\t")
            if body: yield int(fi), json.loads(body)

def _median(v):
    s = sorted(v); n = len(s)
    if not n: return None
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])

def _quantile(v, q):
    s = sorted(v)
    return s[int(round(q * (len(s) - 1)))] if s else None

def _fit_height(ps):
    # player height (px) as a straight line of the foot row: h = a + b*y
    n = len(ps)
    if n == 0: return None
    ys = [p[4] for p in ps]; hs = [p[4] - p[2] for p in ps]
    my = sum(ys) / n; mh = sum(hs) / n
    sxx = sum((y - my) ** 2 for y in ys)
    if n < 4 or sxx < 1e-6: return (mh, 0.0)
    b = sum((y - my) * (h - mh) for y, h in zip(ys, hs)) / sxx
    b = min(0.5, max(0.0, b))                    # nearer players (lower in the image) look taller
    return (mh - b * my, b)

def att(x, y, team):
    # normalised pitch -> the attacking frame of `team` (it always attacks towards x = 105)
    return (x, y) if team == 0 else (PITCH_L - x, PITCH_W - y)

def in_box(ax, ay):
    return ax >= PITCH_L - BOX_D and BOX_Y0 <= ay <= BOX_Y1

def shot_xg(ax, ay):
    # logistic model on distance and the angle the goal mouth subtends (~0.30 from the penalty spot)
    dx = max(0.5, PITCH_L - ax)
    ang = abs(math.atan2(GOAL_Y1 - ay, dx) - math.atan2(GOAL_Y0 - ay, dx))
    dist = math.hypot(dx, PITCH_W / 2 - ay)
    return min(0.95, max(0.01, 1.0 / (1.0 + math.exp(-(0.37 + 0.668 * ang - 0.1497 * dist)))))

def _norm_grid(g):
    m = max(g) if g else 0
    return [int(round(99.0 * v / m)) for v in g] if m else list(g)

class PitchMapper:
    """Image pixels -> approximate pitch metres (x 0..105 along the touchline, y 0..68 from the far side).
    Built in one pass over the frame log, then rotated so team A always attacks towards x = 105."""
    def __init__(self, W, H):
        self.W, self.H = W, H
        self.frame = {}; self.off = {}; self.flip = False
        self.anchored_pct = 0.0; self.dir_votes = 0

    def scale(self, fit, y):
        return PLAYER_H_M / max(6.0, fit[0] + fit[1] * y)

    def rel_x(self, C, fit, u, y):
        # a panning broadcast camera rotates: every depth shifts by the same pixels, so the pan (C, metres at the
        # reference row) is converted back to pixels and rescaled to the depth of row y
        return (C / self.scale(fit, self.H * REF_ROW) + u - self.W / 2.0) * self.scale(fit, y)

    def to_pitch(self, f, u, y):
        seg, C, fit, gtop = self.frame[f]
        x = min(PITCH_L, max(0.0, self.off.get(seg, PITCH_L / 2) + self.rel_x(C, fit, u, y)))
        t = min(1.0, max(0.0, (y - gtop) / max(1.0, self.H - gtop)))
        yy = PITCH_W * t ** DEPTH_GAMMA
        return (PITCH_L - x, PITCH_W - yy) if self.flip else (x, yy)

    def build(self, path):
        segs = {}; prev = None          # prev = (segment, camera x, camera centre in metres, height fit)
        for f, r in _frames(path):
            ps = r["p"]; seg = r.get("s", 0); camx = r.get("k", 0.0)
            same = prev is not None and prev[0] == seg
            fit = _fit_height(ps) or (prev[3] if same else (0.08 * self.H, 0.0))
            C = 0.0
            if same:
                C = prev[2] - (camx - prev[1]) * self.scale(fit, self.H * REF_ROW)
            self.frame[f] = (seg, C, fit, r.get("g", 0))
            sd = segs.get(seg)
            if sd is None:
                sd = segs[seg] = {"f0": f, "n": 0, "xs": [], "gk": [], "tx": [[0.0, 0], [0.0, 0]], "votes": 0}
            sd["f1"] = f; sd["C1"] = C; sd["n"] += 1
            self._observe(sd, ps, C, fit, f)
            prev = (seg, camx, C, fit)
        self._solve(segs)

    def _observe(self, sd, ps, C, fit, f):
        side = {0: [], 1: []}; gks = []
        for p in ps:
            X = self.rel_x(C, fit, (p[1] + p[3]) / 2.0, p[4])
            if p[5] in side:
                side[p[5]].append(X); t = sd["tx"][p[5]]; t[0] += X; t[1] += 1
            elif p[5] == 2:
                gks.append(X)
        out = side[0] + side[1]
        if f % 3 == 0: sd["xs"].extend(out)
        if len(out) < GK_MIN_PLAYERS: return
        mean = sum(out) / len(out)
        for X in gks:
            left = X < mean
            sd["gk"].append((GK_DEPTH_M if left else PITCH_L - GK_DEPTH_M) - X)
            if len(side[0]) >= 3 and len(side[1]) >= 3:
                # the side whose last outfield player is nearer this goal is the one defending it
                a = min(side[0]) if left else -max(side[0]); b = min(side[1]) if left else -max(side[1])
                if a != b: sd["votes"] += 1 if (a < b) == left else -1

    def _solve(self, segs):
        order = sorted(segs, key=lambda s: segs[s]["f0"])
        for s in order:
            sd = segs[s]; xs = sd["xs"]; lo, hi = -1e9, 1e9
            if len(xs) >= 10:            # players can't stand off the pitch: bounds on the segment's offset
                lo = -3.0 - _quantile(xs, 0.02); hi = PITCH_L + 3.0 - _quantile(xs, 0.98)
                if lo > hi: lo = hi = 0.5 * (lo + hi)
            sd["lo"], sd["hi"] = lo, hi
        clamp = lambda s, v: min(segs[s]["hi"], max(segs[s]["lo"], v))
        off = {s: clamp(s, _median(segs[s]["gk"])) for s in order if len(segs[s]["gk"]) >= 3}
        anchored = sum(segs[s]["n"] for s in off)
        if not off and order:            # no goalkeeper anywhere: start from the bounds, else assume midfield
            s0 = order[0]; lo, hi = segs[s0]["lo"], segs[s0]["hi"]
            off[s0] = 0.5 * (lo + hi) if hi - lo < 60 else clamp(s0, PITCH_L / 2 - segs[s0]["C1"] / 2)
        for i in range(1, len(order)):   # camera continuity: a segment starts where the previous one ended
            s, p = order[i], order[i - 1]
            if s not in off and p in off: off[s] = clamp(s, off[p] + segs[p]["C1"])
        for i in range(len(order) - 2, -1, -1):
            s, n = order[i], order[i + 1]
            if s not in off and n in off: off[s] = clamp(s, off[n] - segs[s]["C1"])
        self.off = off
        self.anchored_pct = round(100.0 * anchored / max(1, sum(sd["n"] for sd in segs.values())), 1)
        votes = sum(sd["votes"] for sd in segs.values())
        if votes == 0:                   # no goalkeeper evidence: the deeper side on average defends the left goal
            m = [sum(sd["tx"][t][0] + off.get(s, 0.0) * sd["tx"][t][1] for s, sd in segs.items())
                 / max(1, sum(sd["tx"][t][1] for sd in segs.values())) for t in (0, 1)]
            votes = 1 if m[0] <= m[1] else -1
        self.dir_votes = votes
        self.flip = votes < 0            # team A defends the right goal: rotate so A attacks towards x = 105

class MatchAnalytics:
    """Streams the frame log once (after PitchMapper) and turns positions + possession events into match stats.
    Coordinates are normalised so team A attacks towards x = 105 and team B towards x = 0."""
    def __init__(self, pm, fps, G, events, gains, team_of):
        self.pm, self.fps, self.G, self.team_of = pm, float(fps), G, team_of
        self.events = sorted(events, key=lambda e: e[0]); self.gains = sorted(gains, key=lambda e: e[0])
        self.ei = self.gi = 0
        self.t = {k: [0, 0] for k in COUNT_STATS}
        self.final_third = [0, 0]; self.xg = [0.0, 0.0]; self.fastest = [0.0, 0.0]
        self.heat = {k: [0] * (HEAT_NX * HEAT_NY) for k in ("a_on", "a_off", "b_on", "b_off")}
        self.pl = defaultdict(lambda: defaultdict(float)); self.pheat = {}
        self.passes = []; self.shots = []; self.saves = []
        self.pos = {}; self.last = {}; self.carry = {}     # gid -> (x, y, team) now / (x, y, f) last seen / last with the ball
        self.seg = None; self.ball = deque(maxlen=16); self.win = max(3, int(round(KICK_WIN_S * self.fps)))
        self.kick = None; self.armed = True; self.still = None; self.last_still = None
        self.spell = None; self.dribble = None; self.last_carrier = None

    def bump(self, k, team, g=-1, n=1):
        if team not in (0, 1): return
        self.t[k][team] += n
        if g is not None and g >= 0: self.pl[g][k] += n

    def side_of(self, g):
        # team code of a player; goalkeepers belong to the side whose goal they stand in front of
        code = self.team_of.get(g, -1)
        if code in (0, 1): return code
        p = self.pos.get(g) or self.last.get(g)
        if code == 2 and p is not None: return 0 if p[0] < PITCH_L / 2 else 1
        return -1

    def frame(self, f, r):
        seg = self.pm.frame[f][0]
        if seg != self.seg:
            self._flush_kick(); self.seg = seg; self.ball.clear(); self.armed = True; self.still = self.last_still = None
        c = self.G(r["c"]) if r["c"] >= 0 else -1
        pos = {}
        for p in r["p"]:
            g = self.G(p[0]); x, y = self.pm.to_pitch(f, (p[1] + p[3]) / 2.0, p[4])
            pos[g] = (x, y, p[5]); self.last[g] = (x, y, f)
            self._heat(g, x, y, p[5], g == c)
        self.pos = pos
        if c >= 0 and c in pos:
            self.carry[c] = (pos[c][0], pos[c][1], f); self.last_carrier = (c, self.side_of(c), f)
        self._spell_step(f, c)
        while self.gi < len(self.gains) and self.gains[self.gi][0] <= f:
            self._gain(self.gains[self.gi]); self.gi += 1
        while self.ei < len(self.events) and self.events[self.ei][0] <= f:
            self._event(self.events[self.ei]); self.ei += 1
        self._ball(f, r.get("b"))

    def _heat(self, g, x, y, team, on):
        if team in (0, 1):
            i = min(HEAT_NX - 1, int(x / PITCH_L * HEAT_NX)) + HEAT_NX * min(HEAT_NY - 1, int(y / PITCH_W * HEAT_NY))
            self.heat[("a_" if team == 0 else "b_") + ("on" if on else "off")][i] += 1
            if (x >= PITCH_L - THIRD) if team == 0 else (x <= THIRD): self.pl[g]["ft_f"] += 1
        ph = self.pheat.get(g)
        if ph is None: ph = self.pheat[g] = [0] * (PHEAT_NX * PHEAT_NY)
        ph[min(PHEAT_NX - 1, int(x / PITCH_L * PHEAT_NX)) + PHEAT_NX * min(PHEAT_NY - 1, int(y / PITCH_W * PHEAT_NY))] += 1

    # ---- possession: touches in the box, passes, tackles, interceptions, crosses, long balls ----
    def _gain(self, e):
        f, g, team = e[0], e[1], e[2]
        if g < 0 or team not in (0, 1) or self.team_of.get(g) != team: return
        p = self.pos.get(g) or self.last.get(g)
        if p is not None and in_box(*att(p[0], p[1], team)): self.bump("box_touches", team, g)

    def _event(self, e):
        f, kind, g, team, other = e[0], e[1], e[2], e[3], e[4]
        if kind not in ("pass", "won") or team not in (0, 1): return
        start = self.carry.get(other); end = self.pos.get(g) or self.last.get(g)
        if start is None or end is None: return
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        frm = team if kind == "pass" else 1 - team
        sx, sy = att(start[0], start[1], frm); ex, ey = att(end[0], end[1], frm)
        long_ = length >= LONG_BALL_M
        cross = (sx >= PITCH_L - THIRD and not BOX_Y0 <= sy <= BOX_Y1 and in_box(ex, ey)
                 and abs(ey - PITCH_W / 2) < abs(sy - PITCH_W / 2) - 6.0)
        if long_: self.bump("long_balls_att", frm, other)
        if cross: self.bump("crosses", frm, other)
        if kind == "pass":
            if long_: self.bump("long_balls", team, other)
            if cross: self.bump("crosses_ok", team, other)
            if len(self.passes) < MAX_MAP_PASSES:
                self.passes.append([round(start[0], 1), round(start[1], 1), round(end[0], 1), round(end[1], 1), team])
        elif f - start[2] <= TACKLE_S * self.fps and length <= TACKLE_M:
            self.bump("tackles", team, g); self.bump("dispossessed", frm, other)
        else:
            self.bump("interceptions", team, g)

    # ---- dribbles: a carry of 5 m+ that beats a defender who got within 2 m, keeping the ball ----
    def _spell_step(self, f, c):
        sp = self.spell
        if sp is not None and c != sp["g"]:
            if c < 0 and f - sp["f1"] <= 0.3 * self.fps: return       # carrier missed for a moment: same carry
            self._close_spell(); sp = None
        if c < 0 or c not in self.pos: return
        x, y, _ = self.pos[c]
        if sp is None:
            team = self.side_of(c)
            self._resolve_dribble(f, team)
            sp = self.spell = {"g": c, "team": team, "x0": x, "y0": y, "press": 99.0, "px": x, "py": y}
        sp["f1"] = f; sp["x1"], sp["y1"] = x, y
        opp = [q for q in self.pos.values() if q[2] in (0, 1) and q[2] != sp["team"]]
        if opp:
            d = min(math.hypot(q[0] - x, q[1] - y) for q in opp)
            if d < sp["press"]: sp["press"], sp["px"], sp["py"] = d, x, y

    def _close_spell(self):
        sp = self.spell; self.spell = None
        if sp is None or sp["team"] not in (0, 1): return
        moved = math.hypot(sp["x1"] - sp["x0"], sp["y1"] - sp["y0"])
        past = math.hypot(sp["x1"] - sp["px"], sp["y1"] - sp["py"])
        if moved >= DRIBBLE_M and sp["press"] <= DRIBBLE_PRESS_M and past >= 2.0:
            self.dribble = (sp["g"], sp["team"], sp["f1"])

    def _resolve_dribble(self, f, team):
        d = self.dribble; self.dribble = None
        if d is None: return
        if team != d[1] and f - d[2] <= TACKLE_S * self.fps: return     # lost it straight away: not a dribble
        self.bump("dribbles", d[1], d[0])

    # ---- the ball: final-third time, kicks -> shots / clearances / set pieces, saves ----
    def _ball(self, f, b):
        if b is not None:
            bx, _ = self.pm.to_pitch(f, b[0], b[1])
            if bx >= PITCH_L - THIRD: self.final_third[0] += 1
            elif bx <= THIRD: self.final_third[1] += 1
        if b is None or b[2]:            # lost or only predicted: no reliable speed
            if self.kick is not None and f - self.kick["f0"] > KICK_TRACK_S * self.fps: self._flush_kick()
            return
        x, y = self.pm.to_pitch(f, b[0], b[1])
        self.ball.append((f, x, y))
        self._check_saves(f, x, y)
        st = self.still
        if st is None or math.hypot(x - st[1], y - st[2]) > DEAD_BALL_M:
            if st is not None and st[3] - st[0] >= DEAD_BALL_S * self.fps: self.last_still = st
            self.still = (f, x, y, f)
        else:
            self.still = (st[0], st[1], st[2], f)
        v = self._speed()
        k = self.kick
        if k is not None:
            k["pts"].append((f, x, y)); k["v"] = max(k["v"], v or 0.0)
            if self._near_gk(x, y, k["team"]): k["gk"] = True
            if f - k["f0"] > KICK_TRACK_S * self.fps: self._flush_kick()
            return
        if v is None: return
        if v < REARM_KMH: self.armed = True
        elif self.armed and v >= KICK_KMH: self._start_kick(f, v)

    def _speed(self):
        if len(self.ball) < 2: return None
        f1, x1, y1 = self.ball[-1]
        for f0, x0, y0 in self.ball:
            if f1 - f0 <= self.win:
                return None if f1 == f0 else math.hypot(x1 - x0, y1 - y0) / ((f1 - f0) / self.fps) * 3.6
        return None

    def _start_kick(self, f, v):
        self.armed = False
        f0, x0, y0 = next(((pf, px, py) for pf, px, py in self.ball if f - pf <= self.win), self.ball[-1])
        ls = self.last_still
        dead = ls is not None and f - ls[3] <= 0.6 * self.fps
        sx, sy = (ls[1], ls[2]) if dead else (x0, y0)
        g, team = self._kicker(f, sx, sy, dead)
        self.kick = {"f0": f, "x": sx, "y": sy, "g": g, "team": team, "v": v, "pts": [], "dead": dead, "gk": False}

    def _kicker(self, f, x, y, dead):
        lc = self.last_carrier
        if not dead and lc is not None and f - lc[2] <= self.fps: return lc[0], lc[1]
        best, bg = 4.0, -1                # a dead ball is taken by whoever stands over it
        for g, q in self.pos.items():
            d = math.hypot(q[0] - x, q[1] - y)
            if q[2] != 3 and d < best: best, bg = d, g
        return bg, (self.side_of(bg) if bg >= 0 else -1)

    def _near_gk(self, x, y, team):
        # the defending goalkeeper (in front of the goal `team` attacks) within reach of the ball
        for q in self.pos.values():
            if q[2] == 2 and att(q[0], q[1], team)[0] >= PITCH_L - 20 and math.hypot(q[0] - x, q[1] - y) <= SAVE_REACH_M:
                return True
        return False

    def _check_saves(self, f, x, y):
        keep = []
        for f0, team in self.saves:
            if self._near_gk(x, y, team): self.bump("saves", 1 - team)
            elif f - f0 <= SAVE_WINDOW_S * self.fps: keep.append((f0, team))
        self.saves = keep

    def _flush_kick(self):
        k = self.kick; self.kick = None
        if k is None or k["team"] not in (0, 1): return
        team, g = k["team"], k["g"]
        v = min(150.0, k["v"])
        if v > self.fastest[team]: self.fastest[team] = round(v, 1)
        if k["dead"]: self._restart(k)
        early = [p for p in k["pts"] if p[0] - k["f0"] <= 0.5 * self.fps] or k["pts"][:1]
        if not early: return
        ox, oy = att(k["x"], k["y"], team); tx, ty = att(early[-1][1], early[-1][2], team)
        dx, dy = tx - ox, ty - oy
        if math.hypot(PITCH_L - ox, PITCH_W / 2 - oy) <= SHOT_RANGE_M and dx > 2.0:
            yg = oy + dy * (PITCH_L - ox) / dx                     # where the flight crosses the goal line
            if abs(yg - PITCH_W / 2) <= 11.0:
                self._shot(k, team, g, ox, oy, GOAL_Y0 - 0.8 <= yg <= GOAL_Y1 + 0.8, v); return
        if ox <= THIRD: self.bump("clearances", team, g)

    def _shot(self, k, team, g, ox, oy, on, v):
        xg = shot_xg(ox, oy)
        self.bump("shots", team, g); self.xg[team] += xg
        if g >= 0: self.pl[g]["xg"] += xg
        if on:
            self.bump("shots_on", team, g)
            if k["gk"]: self.bump("saves", 1 - team)
            else: self.saves.append((k["f0"], team))
        self.shots.append([round(k["x"], 1), round(k["y"], 1), team, 1 if on else 0, round(xg, 2), round(v)])

    def _restart(self, k):
        x, y = k["x"], k["y"]
        if abs(x - PITCH_L / 2) < 4 and abs(y - PITCH_W / 2) < 4: return                 # kick-off
        if (x < 6 or x > PITCH_L - 6) and (y < 8 or y > PITCH_W - 8):
            side = 0 if x > PITCH_L / 2 else 1                                        # the attacking side of that end
            self.bump("corners", side, k["g"] if k["team"] == side else -1); return
        if y < 3 or y > PITCH_W - 3: return                                               # throw-in
        if (x < 7 or x > PITCH_L - 7) and 22 < y < 46: return                             # goal kick
        self.bump("free_kicks", k["team"], k["g"])

    def finish(self):
        self._close_spell(); self._resolve_dribble(10 ** 12, -1); self._flush_kick()

    def result(self, keep):
        t, fps = self.t, self.fps
        team = {}
        for k in COUNT_STATS: team[k + "_a"], team[k + "_b"] = t[k]
        for i, s in enumerate("ab"):
            team["xg_" + s] = round(self.xg[i], 2)
            team["final_third_s_" + s] = round(self.final_third[i] / fps, 1)
            team["def_actions_" + s] = t["tackles"][i] + t["interceptions"][i] + t["clearances"][i]
            team["fastest_strike_" + s] = self.fastest[i]
        players = {}
        for g in keep:
            d = self.pl.get(g, {})
            row = {k: int(d.get(k, 0)) for k in COUNT_STATS if k not in ("saves", "corners", "crosses_ok")}
            row.update(xg=round(d.get("xg", 0.0), 2), final_third_s=round(d.get("ft_f", 0) / fps, 1),
                       heat=_norm_grid(self.pheat.get(g, [])))
            players[g] = row
        heat = dict(self.heat)
        for s in "ab": heat[s + "_all"] = [a + b for a, b in zip(heat[s + "_on"], heat[s + "_off"])]
        maps = {"heat": {k: _norm_grid(v) for k, v in heat.items()}, "heat_size": [HEAT_NX, HEAT_NY],
                "player_heat_size": [PHEAT_NX, PHEAT_NY], "passes": self.passes, "shots": self.shots}
        return {"team": team, "players": players, "maps": maps}

def match_analytics(path, G, fps, W, H, events, gains, team_of, keep):
    """Two passes over the per-frame log: map pixels to the pitch, then count. keep = player ids to report."""
    pm = PitchMapper(W, H); pm.build(path)
    ma = MatchAnalytics(pm, fps, G, events, gains, team_of)
    for f, r in _frames(path): ma.frame(f, r)
    ma.finish()
    out = ma.result(keep)
    out["pitch"] = {"estimated": True, "anchored_pct": pm.anchored_pct, "flipped": pm.flip}
    return out

# ----------------------------------------------------------------------------
# I/O: threaded decoding + single-pass ffmpeg encoder (CRF, audio kept)
# ----------------------------------------------------------------------------
class FrameReader:
    def __init__(self, cap, size, maxq=24):
        self.q = queue.Queue(maxq); self.stop = False; self.size = size
        self.t = threading.Thread(target=self._run, args=(cap,), daemon=True); self.t.start()
    def _put(self, item):
        while not self.stop:
            try: self.q.put(item, timeout=0.5); return
            except queue.Full: pass
    def _run(self, cap):
        W, H = self.size
        while not self.stop:
            ok, fr = cap.read()
            if not ok or fr is None: break
            if fr.shape[1] != W or fr.shape[0] != H:
                fr = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA if W < fr.shape[1] else cv2.INTER_LINEAR)
            self._put(fr)
        self._put(None)
    def get(self):
        while True:
            try: return self.q.get(timeout=0.5)
            except queue.Empty:
                if self.stop: return None

class DetectorThread:
    # Stage 2 of the pipeline: scene analysis (grass / cut / wide-shot) + BATCHED GPU detection, running in parallel with
    # decoding (stage 1) and with tracking / drawing / encoding on the main thread (stage 3).
    def __init__(self, reader, run_batch, grass, small, stride, batch):
        self.q = queue.Queue(48); self.stop = False
        self.reader, self.run_batch, self.grass, self.small = reader, run_batch, grass, small
        self.stride, self.batch = stride, batch; self.cap = 96
        self.t = threading.Thread(target=self._run, daemon=True); self.t.start()
    def _put(self, item):
        while not self.stop:
            try: self.q.put(item, timeout=0.5); return
            except queue.Full: pass
    def get(self):
        while True:
            try: return self.q.get(timeout=0.5)
            except queue.Empty:
                if self.stop and not self.t.is_alive(): return None
    def _run(self):
        try:
            cuts = CutDetector(); idx = -1; last_det = -10**9; prev_wide = False; pend = []; n_need = 0
            def flush():
                todo = [p for p in pend if p["need"]]
                if todo:
                    res = self.run_batch([p["frame"] for p in todo])
                    for p, r in zip(todo, res): p["bn"] = r.boxes.cpu().numpy()
                for p in pend: self._put(p)
                pend.clear()
            while not self.stop:
                fr = self.reader.get()
                if fr is None: break
                idx += 1
                if idx % 240 == 0: self.grass.calibrate(fr)
                gm = self.grass.mask(self.small(fr)); wide = (gm.mean() / 255.0) >= WIDE_MIN
                cut = cuts.check(fr)
                need = wide and (cut or not prev_wide or idx - last_det >= self.stride)
                if need: last_det = idx
                prev_wide = wide
                pend.append({"idx": idx, "frame": fr, "gm": gm, "wide": wide, "cut": cut, "need": need, "bn": None})
                n_need += 1 if need else 0
                if n_need >= self.batch or len(pend) >= self.cap:
                    flush(); n_need = 0
            flush()
        except Exception as e:
            import traceback; traceback.print_exc()
            self._put({"error": e})
        self._put(None)

_ENC = {}
def pick_encoder():
    # hardware NVENC when the GPU exposes it, otherwise a much faster x264 preset than 'veryfast'
    if _ENC: return _ENC["args"], _ENC["name"]
    args = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-threads", "0", "-pix_fmt", "yuv420p"]; name = "x264 ultrafast"
    if HAS_GPU:
        for nv in (["-c:v", "h264_nvenc", "-preset", "p3", "-rc", "vbr", "-cq", "25", "-b:v", "0", "-pix_fmt", "yuv420p"],
                   ["-c:v", "h264_nvenc", "-preset", "fast", "-cq", "25", "-pix_fmt", "yuv420p"]):
            try:
                r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25:d=0.3"]
                                   + nv + ["-f", "null", "-"], capture_output=True, timeout=40)
                if r.returncode == 0: args, name = nv, "NVENC (GPU)"; break
            except Exception:
                pass
    _ENC["args"], _ENC["name"] = args, name
    return args, name

class Encoder:
    # frames are handed to a writer thread, so encoding overlaps with detection and tracking
    def __init__(self, path, w, h, fps, audio_src):
        self.path = path; self.p = None; self.cv = None; self.raw = path; self.err = None; self.name = "OpenCV"; self.blocked = 0.0
        try:
            vargs, self.name = pick_encoder()
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                   "-r", f"{fps:.4f}", "-i", "-", "-i", audio_src, "-map", "0:v:0", "-map", "1:a:0?"] + vargs + \
                  ["-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", path]
            self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.q = queue.Queue(32)
            self.t = threading.Thread(target=self._pump, daemon=True); self.t.start()
        except Exception as e:
            print("ffmpeg pipe unavailable, falling back to OpenCV writer:", e)
            self.p = None; self.raw = path + ".raw.mp4"
            self.cv = cv2.VideoWriter(self.raw, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    def _pump(self):
        while True:
            fr = self.q.get()
            if fr is None: break
            try: self.p.stdin.write(fr.tobytes())
            except Exception as e:
                self.err = e; break
    def write(self, frame):
        if self.p is None: self.cv.write(frame); return
        t0 = time.time()
        while self.err is None:
            try: self.q.put(frame, timeout=0.5); self.blocked += time.time() - t0; return
            except queue.Full: pass
        raise RuntimeError(f"video encoder failed: {self.err}")
    def close(self):
        if self.p is not None:
            if self.err is None:
                try: self.q.put(None, timeout=30)
                except Exception: pass
            self.t.join(timeout=300)
            try: self.p.stdin.close()
            except Exception: pass
            return self.p.wait() == 0
        self.cv.release()
        vargs, _ = pick_encoder()
        r = subprocess.run(["ffmpeg", "-y", "-i", self.raw] + vargs + ["-movflags", "+faststart", self.path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0 or not os.path.exists(self.path): shutil.copyfile(self.raw, self.path)
        try: os.remove(self.raw)
        except Exception: pass
        return True

# ----------------------------------------------------------------------------
# BURNED-IN BROADCAST GRAPHICS
# ----------------------------------------------------------------------------
def draw_marker(frame, b, color, carrier=False):
    x1, y1, x2, y2 = b; cx = (x1 + x2) // 2; w = max(1, x2 - x1)
    ax = max(8, int(w * 0.62)); ay = max(3, int(ax * 0.32))
    cv2.ellipse(frame, (cx, y2), (ax, ay), 0, -30, 210, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.ellipse(frame, (cx, y2), (ax, ay), 0, -30, 210, color, 2, cv2.LINE_AA)
    if carrier:
        gold = (0, 215, 255)
        cv2.ellipse(frame, (cx, y2), (int(ax * 1.3), int(ay * 1.35)), 0, 0, 360, gold, 3, cv2.LINE_AA)
        cv2.fillConvexPoly(frame, np.array([[cx - 8, y1 - 20], [cx + 8, y1 - 20], [cx, y1 - 6]], np.int32), gold, cv2.LINE_AA)

def draw_ball(frame, ball, trail):
    c = (int(ball[0]), int(ball[1]))
    cv2.circle(frame, c, 8, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.circle(frame, c, 6, (255, 255, 255) if not ball[3] else (170, 170, 170), -1, cv2.LINE_AA)
    cv2.circle(frame, c, 10, (0, 215, 255), 1, cv2.LINE_AA)

def draw_possession_bar(frame, poss, kit):
    H, W = frame.shape[:2]
    a, b = poss.team_frames[0], poss.team_frames[1]; tot = a + b
    if tot < 30: return
    pa = a / tot; bw = int(W * 0.26); bh = max(8, int(H * 0.011))
    x0 = (W - bw) // 2; y0 = H - int(H * 0.04)
    cv2.rectangle(frame, (x0 - 2, y0 - 2), (x0 + bw + 2, y0 + bh + 2), (0, 0, 0), -1)
    cv2.rectangle(frame, (x0, y0), (x0 + int(bw * pa), y0 + bh), kit["team_a"], -1)
    cv2.rectangle(frame, (x0 + int(bw * pa), y0), (x0 + bw, y0 + bh), kit["team_b"], -1)
    fs = max(0.4, H / 1800.0)
    cv2.putText(frame, f"A {pa*100:.0f}%", (x0, y0 - 6), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 1, cv2.LINE_AA)
    t = f"B {(1-pa)*100:.0f}%"
    tw = cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)[0][0]
    cv2.putText(frame, t, (x0 + bw - tw, y0 - 6), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 1, cv2.LINE_AA)

# ----------------------------------------------------------------------------
# MAIN PIPELINE
# ----------------------------------------------------------------------------
def process_football_match(input_path: str, mode_key: str, res_key: str, burn: bool = True, reid: bool = True):
    J = JOB_STATE
    cap = reader = enc = gz = dthread = None
    t_job = time.time()
    mode = MODES.get(mode_key, MODES["balanced"])
    out_path = str(PROCESSED_DIR / "tactical_output.mp4")
    meta_path = str(PROCESSED_DIR / "tactical_metadata.json.gz")
    fixed = str(UPLOAD_DIR / "fixed_input.mp4")
    summ_path = str(PROCESSED_DIR / "summary.json")
    for p in [out_path, meta_path, fixed, summ_path]:
        if os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
    cancelled = False
    try:
        src = input_path
        cap = cv2.VideoCapture(src)
        if not cap.isOpened() or int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) <= 0:
            cap.release()
            subprocess.run(["ffmpeg", "-y", "-i", input_path, "-c", "copy", "-movflags", "+faststart", fixed],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            src = fixed; cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            raise RuntimeError("Could not open the video file.")

        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0 or np.isnan(fps): fps = 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            total = int(probe_duration(src) * fps)
        th = RES_H.get(res_key, 1080)
        scale = 1.0 if th == 0 else min(1.0, th / float(src_h))     # aspect ratio preserved, never upscaled
        W, H = even(src_w * scale), even(src_h * scale)

        model = YOLO(ensure_model())
        names = {int(k): str(v).lower() for k, v in model.names.items()}
        ball_ids = {k for k, v in names.items() if "ball" in v}
        ref_ids = {k for k, v in names.items() if "ref" in v}
        gk_ids = {k for k, v in names.items() if "keeper" in v or v == "gk"}

        grass = GrassModel(); team = TeamModel(grass)
        small = lambda fr: cv2.resize(fr, None, fx=SC, fy=SC, interpolation=cv2.INTER_AREA)

        bsz = mode["batch"] if HAS_GPU else 4

        tm = {"gpu": 0.0, "wait": 0.0, "pre": 0.0, "batches": 0}

        def run_batch(frames):
            # ALWAYS the same batch shape (padded with a repeat of the last frame) so the GPU never re-tunes or re-allocates
            n = len(frames); t_ = time.time()
            if n < bsz: frames = list(frames) + [frames[-1]] * (bsz - n)
            out = model.predict(frames, imgsz=mode["imgsz"], conf=DET_CONF, iou=0.55, half=HAS_GPU,
                                device=DEVICE_TARGET, verbose=False, max_det=80)[:n]
            tm["gpu"] += time.time() - t_; tm["batches"] += 1
            return out

        # ---- PASS 1: calibrate pitch colour and learn both kits from sampled frames ----
        J.update(status="scanning", phase="Pass 1/2: calibrating pitch & learning kit colours", total_frames=total,
                 current_frame=0, progress=0.0)
        t_pre0 = time.time()
        J.update(phase="Warming up the detector")
        run_batch([np.zeros((H, W, 3), np.uint8)])
        J.update(phase="Pass 1/2: calibrating pitch & learning kit colours")
        if total > 60:
            cap2 = cv2.VideoCapture(src); samples = []
            for pos in np.linspace(0, total - 2, 24).astype(int):
                if JOB_CANCEL.is_set(): break
                cap2.set(cv2.CAP_PROP_POS_FRAMES, int(pos)); ok, fr = cap2.read()
                if not ok or fr is None: continue
                fr = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA)
                grass.calibrate(fr); gm = grass.mask(small(fr))
                if gm.mean() / 255.0 >= WIDE_MIN: samples.append((fr, gm))
            cap2.release()
            for i0 in range(0, len(samples), bsz):
                chunk = samples[i0:i0 + bsz]
                for (fr, gm), r in zip(chunk, run_batch([c_[0] for c_ in chunk])):
                    bn = r.boxes.cpu().numpy()
                    for xy, c, k in zip(bn.xyxy, bn.conf, bn.cls.astype(int)):
                        if k in ball_ids or k in ref_ids or k in gk_ids or c < 0.5: continue
                        bx = xy.astype(int)
                        if valid_geometry(bx, W, H) and on_turf(gm, bx):
                            f = team.feature(fr, bx)
                            if f is not None: team.samples.append(f)
            if len(team.samples) >= 60: team.fit()
        kit = team.kit_bgr(); tm["pre"] = time.time() - t_pre0; tm["gpu"] = 0.0; tm["batches"] = 0

        tracker, tracker_name = make_tracker(fps)
        ident = IdentityManager(fps); cam = CameraMotion()
        ball = BallTracker(); poss = PossessionEngine(fps); cuts = CutDetector()

        reader = FrameReader(cap, (W, H), maxq=32)
        enc = Encoder(out_path, W, H, fps, src)
        dthread = DetectorThread(reader, run_batch, grass, small, mode["stride"], bsz)
        tmp_path = str(PROCESSED_DIR / "frames_tmp.jsonl")
        gz = open(tmp_path, "w")

        J.update(status="processing", phase=f"Pass 2/2: pipelined tracking ({tracker_name}) • {enc.name} • batch {bsz} • detect every {mode['stride']} frame(s)")
        tracks = {}; first_seen = {}; appear = Counter(); team_info = {}
        idx = -1; wide_frames = 0; ball_seen = 0; wrote_any = False; in_wide = False
        tracker_failed = False; start = time.time()
        phys = {}; xev = []; last_sprint = {}; shots = []; shot_start = None
        bw = {"p": None, "f": -10, "v": 0.0}; ball_max = 0.0; ball_sum = 0.0; ball_n = 0
        last_strike = -10**9; strikes = 0; swaps_n = 0
        sigacc = {}; last_seen = {}
        seg_n = [0]          # camera segment id for the analytics log: bumped whenever the camera state resets

        def reset_state(f):
            tracks.clear(); ball.reset(); ident.new_shot(); cam.reset(); poss.drop(f); bw["p"] = None; seg_n[0] += 1
            for p_ in phys.values(): p_["wp"] = None
            try: tracker.reset()
            except Exception: pass

        while True:
            if JOB_CANCEL.is_set():
                cancelled = True; break
            t_w = time.time(); item = dthread.get(); tm["wait"] += time.time() - t_w
            if item is None: break
            if "error" in item: raise item["error"]
            frame = item["frame"]; idx = item["idx"]

            if idx % 10 == 0:
                el = time.time() - start; pf = (idx + 1) / (el + 1e-5)
                J.update(current_frame=idx, fps=round(pf, 1),
                         progress=round(min(99.9, idx / max(1, total) * 100), 1) if total else 0.0,
                         eta=int((total - idx) / pf) if total and pf > 0 else 0)

            gm = item["gm"]; wide = item["wide"]
            if item["cut"]:
                if in_wide and shot_start is not None and shot_start <= idx - 1: shots.append([shot_start, idx - 1])
                shot_start = idx; reset_state(idx)

            if not wide:
                if in_wide:
                    if shot_start is not None and shot_start <= idx - 1: shots.append([shot_start, idx - 1])
                    shot_start = None; reset_state(idx); in_wide = False
                enc.write(frame); continue
            if not in_wide or shot_start is None: shot_start = idx
            in_wide = True; wide_frames += 1
            cam.update(frame)

            do_det = item["bn"] is not None
            cands = []
            if do_det:
                bn = item["bn"]
                xyxy = bn.xyxy; conf = bn.conf; cls = bn.cls.astype(int)
                keep = []
                for i in range(len(cls)):
                    k = int(cls[i]); c = float(conf[i]); x1, y1, x2, y2 = xyxy[i]
                    if k in ball_ids:
                        if c >= BALL_MIN_CONF and (x2 - x1) <= 0.03 * W and (y2 - y1) <= 0.05 * H:
                            cands.append(((x1 + x2) / 2, (y1 + y2) / 2, c))
                        continue
                    if c < mode["person_conf"]: continue
                    bx = xyxy[i].astype(int)
                    if valid_geometry(bx, W, H) and on_turf(gm, bx): keep.append(i)
                det = bn[np.array(keep, dtype=int)]
                try:
                    trk = tracker.update(det, frame)
                except Exception as e:
                    if tracker_failed: raise
                    print("Tracker failure, switching to built-in tracker:", e)
                    tracker = SimpleTracker(); tracker_failed = True; tracker_name = "Built-in IoU tracker"
                    trk = tracker.update(det, frame)
                rows = []; fresh = []
                for row in trk:
                    box = np.array(row[:4], np.float32); raw = int(row[4]); k = int(row[6])
                    plab = "official" if k in ref_ids else "goalkeeper" if k in gk_ids else None
                    rows.append((raw, box, k, plab))
                    if raw not in ident.alias:
                        fresh.append({"raw": raw, "box": box, "cls": k, "h": float(max(1.0, box[3] - box[1])),
                                      "lab": plab or team.quick(frame, box), "app": appearance(frame, box, grass),
                                      "wpos": np.array([(box[0] + box[2]) / 2 - cam.cum[0], box[3] - cam.cum[1]], np.float32)})
                ident.assign_new(idx, fresh, {ident.alias[r_[0]] for r_ in rows if r_[0] in ident.alias})
                used = set(); boxes_now = {}
                for raw, box, k, plab in rows:
                    cid = ident.alias.get(raw)
                    if cid is None or cid in used: continue
                    used.add(cid)
                    lab = plab or team.label_for(cid, frame, box.astype(int), idx)
                    h = float(max(1.0, box[3] - box[1]))
                    wpos = np.array([(box[0] + box[2]) / 2 - cam.cum[0], box[3] - cam.cum[1]], np.float32)
                    clean = idx - ident.last_ov.get(cid, -999) > 12
                    known = ident.info.get(cid)
                    app = appearance(frame, box, grass) if (known is None or known["app"] is None or (clean and (idx + cid) % 6 == 0)) else None
                    ident.update(cid, raw, idx, box, k, lab, wpos, h, app, clean)
                    last_seen[cid] = idx
                    if reid and clean and (idx + cid) % 5 == 0 and h >= 0.04 * H:
                        sg = signature(frame, box, grass)
                        if sg is not None:
                            wt = min(2.0, h / (0.08 * H)); acc_ = sigacc.get(cid)
                            if acc_ is None: sigacc[cid] = [sg * wt, wt]
                            else: acc_[0] += sg * wt; acc_[1] += wt
                    tr = tracks.get(cid)
                    if tr is None:
                        tracks[cid] = {"box": box, "vel": np.zeros(2, np.float32), "lab": lab, "last": idx}
                        first_seen.setdefault(cid, idx)
                    else:
                        nb = 0.62 * box + 0.38 * tr["box"]
                        v = ((nb[:2] + nb[2:]) / 2 - (tr["box"][:2] + tr["box"][2:]) / 2) / max(1, idx - tr["last"])
                        tr["vel"] = 0.5 * tr["vel"] + 0.5 * v; tr["box"] = nb; tr["lab"] = lab; tr["last"] = idx
                    team_info[cid] = lab; boxes_now[cid] = box
                    # camera-compensated speed / distance (metres estimated with player height as a ruler)
                    ph = phys.setdefault(cid, {"wp": None, "wf": idx, "dist": 0.0, "top": 0.0, "top_f": idx, "spr": 0, "on": False,
                                               "pk": 0.0, "pk_sum": 0.0, "pk_n": 0})   # pk: peak speed of the current sprint
                    hh = ident.info[cid]["h"]
                    if ph["wp"] is None:
                        ph["wp"] = wpos.copy(); ph["wf"] = idx
                    elif idx - ph["wf"] >= 5:
                        m = float(np.hypot(*(wpos - ph["wp"]))) * PLAYER_H_M / max(1.0, hh)
                        sp = m / ((idx - ph["wf"]) / fps)
                        if sp <= 11.0:
                            if sp >= 0.5: ph["dist"] += m
                            if sp > ph["top"]: ph["top"] = sp; ph["top_f"] = idx
                            if ph["on"] and sp > ph["pk"]: ph["pk"] = sp
                            if sp >= SPRINT_MS and not ph["on"]:
                                ph["on"] = True; ph["spr"] += 1; ph["pk"] = sp
                                if lab != "official" and idx - last_sprint.get(cid, -10**9) > fps * 3:
                                    last_sprint[cid] = idx; xev.append([idx, "sprint", cid, TEAM_CODE[lab], round(sp * 3.6, 1)])
                            elif sp < 5.0 and ph["on"]:
                                ph["on"] = False; ph["pk_sum"] += ph["pk"]; ph["pk_n"] += 1
                        ph["wp"] = wpos.copy(); ph["wf"] = idx
                ident.note_overlaps(idx, boxes_now)
                for a_, b_ in ident.check_swaps(idx, boxes_now, frame, grass):
                    if a_ in tracks and b_ in tracks:
                        tracks[a_], tracks[b_] = tracks[b_], tracks[a_]
                        tracks[a_]["lab"], tracks[b_]["lab"] = tracks[b_]["lab"], tracks[a_]["lab"]
                    if a_ in phys and b_ in phys: phys[a_]["wp"], phys[b_]["wp"] = phys[b_]["wp"], phys[a_]["wp"]
                    swaps_n += 1
                for cid in [c for c, t in tracks.items() if idx - t["last"] > max(10, int(fps))]:
                    del tracks[cid]

            ball_pt = ball.update(cands, W)

            cur = []
            for cid, t in tracks.items():
                dt = idx - t["last"]
                if dt > mode["stride"]: continue
                b = t["box"] + np.array([t["vel"][0], t["vel"][1], t["vel"][0], t["vel"][1]], np.float32) * dt
                b = np.array([max(0, b[0]), max(0, b[1]), min(W - 1, b[2]), min(H - 1, b[3])]).astype(int)
                cur.append((cid, b, t["lab"])); appear[cid] += 1

            poss.step(idx, ball_pt, ball.lost, cur)
            carrier = poss.owner if any(c == poss.owner for c, _, _ in cur) else None
            if ball_pt is not None and not ball_pt[3]:
                ball_seen += 1
                bwp = np.array([ball_pt[0] - cam.cum[0], ball_pt[1] - cam.cum[1]], np.float32)
                if bw["p"] is not None and 1 <= idx - bw["f"] <= 3 and cur:
                    med_h = float(np.median([b_[3] - b_[1] for _, b_, _ in cur]))
                    v = float(np.hypot(*(bwp - bw["p"]))) / (idx - bw["f"]) * fps * PLAYER_H_M / max(1.0, med_h) * 3.6
                    if v < 160:
                        bw["v"] = 0.5 * bw["v"] + 0.5 * v
                        ball_max = max(ball_max, bw["v"]); ball_sum += bw["v"]; ball_n += 1
                        if bw["v"] > STRIKE_KMH and idx - last_strike > fps * 1.5:
                            last_strike = idx; strikes += 1
                            xev.append([idx, "strike", -1, poss.last_team if poss.last_team is not None else -1, round(bw["v"], 1)])
                bw["p"] = bwp; bw["f"] = idx

            # ---- burned-in graphics ----
            if burn:
                for cid, b, lab in cur:
                    if cid != carrier: draw_marker(frame, b, kit[lab])
                for cid, b, lab in cur:
                    if cid == carrier: draw_marker(frame, b, kit[lab], True)
                if ball_pt is not None: draw_ball(frame, ball_pt, ball.trail)
                draw_possession_bar(frame, poss, kit)
            enc.write(frame)

            rec = {"p": [[int(c), int(b[0]), int(b[1]), int(b[2]), int(b[3]), TEAM_CODE[l]] for c, b, l in cur],
                   "c": int(carrier) if carrier is not None else -1,
                   "b": [int(ball_pt[0]), int(ball_pt[1]), 1 if ball_pt[3] else 0] if ball_pt is not None else None,
                   "k": round(float(cam.cum[0]), 1), "g": grass_top(gm), "s": seg_n[0]}   # k/g/s: for the pitch mapper only
            gz.write(str(idx) + "\t" + json.dumps(rec, separators=(",", ":")) + "\n")
            wrote_any = True

        # ---- finalize ----
        reader.stop = True; dthread.stop = True
        J.update(status="finalizing", phase="Encoding final video & writing analytics", progress=99.9)
        poss.drop(idx)
        enc_name = enc.name; enc_blk = enc.blocked; enc.close(); enc = None
        cap.release(); cap = None

        if cancelled:
            gz.close(); gz = None
            J.update(status="cancelled", phase="Cancelled", video_url="", meta_url="")
            return

        if in_wide and shot_start is not None and shot_start <= idx: shots.append([shot_start, idx])
        gz.close(); gz = None
        J.update(phase="Re-identifying players across camera cuts" if reid else "Writing analytics")
        gmap = {}; id_info = {}
        if reid:
            groups = ["team_a", "team_b", "goalkeeper", "official"]
            items = {g_: [] for g_ in groups}
            for c, acc_ in sigacc.items():
                grp = team_info.get(c)
                if grp not in items or acc_[1] <= 0: continue
                items[grp].append({"cid": c, "sig": acc_[0] / acc_[1], "w": float(acc_[1]), "t0": first_seen.get(c, 0),
                                   "t1": last_seen.get(c, 0) + 1, "short": appear[c] < MIN_TRACK_LEN})
            pref = {"team_a": "A", "team_b": "B", "goalkeeper": "GK", "official": "REF"}
            for gi, grp in enumerate(groups):
                if not items[grp]: continue
                assign, cents, confs = reid_cluster(items[grp], grp)
                secs = Counter()
                for c, k in assign.items(): secs[k] += appear[c]
                for rank, (k, _) in enumerate(secs.most_common()):
                    gid = 10000000 * (gi + 1) + rank + 1
                    members = [c for c, k_ in assign.items() if k_ == k]
                    id_info[gid] = {"label": f"{pref[grp]}{rank + 1}", "conf": confs[k], "tracklets": len(members), "group": grp}
                    for c in members: gmap[c] = gid
        G = lambda c: gmap.get(c, c)
        for p_ in phys.values():           # a sprint still running when the clip ends counts with its peak so far
            if p_.get("on"): p_["pk_sum"] += p_["pk"]; p_["pk_n"] += 1; p_["on"] = False
        if gmap:
            a2 = Counter(); fs2 = {}; ti2 = {}; ph2 = {}
            for c, n_ in appear.items(): a2[G(c)] += n_
            for c, f_ in first_seen.items():
                g_ = G(c); fs2[g_] = min(fs2.get(g_, f_), f_)
            for c, l_ in team_info.items(): ti2[G(c)] = l_
            for g_, inf in id_info.items(): ti2[g_] = inf["group"]
            for c, p_ in phys.items():
                q = ph2.setdefault(G(c), {"dist": 0.0, "top": 0.0, "top_f": 0, "spr": 0, "pk_sum": 0.0, "pk_n": 0})
                q["dist"] += p_["dist"]; q["spr"] += p_["spr"]; q["pk_sum"] += p_["pk_sum"]; q["pk_n"] += p_["pk_n"]
                if p_["top"] > q["top"]: q["top"] = p_["top"]; q["top_f"] = p_["top_f"]
            appear, first_seen, team_info, phys = a2, fs2, ti2, ph2
            t2 = Counter(); o2 = Counter()
            for c, n_ in poss.touches.items(): t2[G(c)] += n_
            for c, n_ in poss.on_ball.items(): o2[G(c)] += n_
            poss.touches, poss.on_ball = t2, o2
            l2 = Counter()
            for (a_, b_), n_ in poss.links.items():
                if G(a_) != G(b_): l2[(G(a_), G(b_))] += n_
            poss.links = l2
            ev2 = []
            for e_ in poss.evlog:
                if e_[1] == "pass" and G(e_[4]) == G(e_[2]):
                    poss.passes[e_[3]] -= 1; continue          # same player seen as two fragments, not a real pass
                ev2.append([e_[0], e_[1], G(e_[2]), e_[3], G(e_[4])])
            poss.evlog = ev2
            xev = [[e_[0], e_[1], G(e_[2]) if e_[2] >= 0 else e_[2], e_[3], e_[4]] for e_ in xev]
            poss.events = [[e_[0], G(e_[1]) if e_[1] >= 0 else e_[1], e_[2]] for e_ in poss.events]
        tot_p = poss.team_frames[0] + poss.team_frames[1]
        real = sorted(c for c, n in appear.items() if n >= MIN_TRACK_LEN)
        pl = {}
        for c in real:
            ph = phys.get(c, {})
            pl[str(c)] = {"touches": poss.touches[c], "on_ball_s": round(poss.on_ball[c] / fps, 1),
                          "dist_m": round(ph.get("dist", 0.0), 1), "top_kmh": round(ph.get("top", 0.0) * 3.6, 1),
                          "sprints": ph.get("spr", 0), "secs": round(appear[c] / fps, 1),
                          "sprint_kmh": round(ph["pk_sum"] / ph["pk_n"] * 3.6, 1) if ph.get("pk_n") else 0.0}
        J.update(phase="Mapping the pitch & counting match events")
        ma = None
        try:
            ma = match_analytics(tmp_path, G, fps, W, H, poss.evlog, poss.events,
                                 {c: TEAM_CODE[l_] for c, l_ in team_info.items()}, real)
            for c, row in ma["players"].items(): pl[str(c)].update(row)
        except Exception:
            import traceback; traceback.print_exc()
            print("Match analytics failed: shots, tackles, xG and the pitch maps are left out of this report.")
        spk = [[0.0, 0], [0.0, 0]]          # [sum of sprint peaks, sprints] per team -> average sprint speed
        for c in real:
            code = TEAM_CODE[team_info.get(c, "team_a")]
            if code < 2 and c in phys: spk[code][0] += phys[c].get("pk_sum", 0.0); spk[code][1] += phys[c].get("pk_n", 0)
        top_speeds = sorted(([c, pl[str(c)]["top_kmh"], phys[c].get("top_f", 0), TEAM_CODE[team_info.get(c, "team_a")]]
                             for c in real if c in phys and team_info.get(c) != "official"), key=lambda r_: -r_[1])[:12]
        acc = lambda p_, g_: round(100.0 * p_ / (p_ + g_), 1) if (p_ + g_) else 0.0
        stats = {
            "possession_a": round(100.0 * poss.team_frames[0] / tot_p, 1) if tot_p else 0.0,
            "possession_b": round(100.0 * poss.team_frames[1] / tot_p, 1) if tot_p else 0.0,
            "passes_a": poss.passes[0], "passes_b": poss.passes[1],
            "gains_a": poss.gains[0], "gains_b": poss.gains[1],
            "pass_acc_a": acc(poss.passes[0], poss.gains[1]), "pass_acc_b": acc(poss.passes[1], poss.gains[0]),
            "ball_visible_pct": round(100.0 * ball_seen / max(1, wide_frames), 1),
            "ball_max_kmh": round(ball_max, 1), "ball_avg_kmh": round(ball_sum / max(1, ball_n), 1),
            "strikes": strikes, "sprints": sum(1 for e_ in xev if e_[1] == "sprint"), "swaps_fixed": swaps_n,
            "wide_frames": wide_frames, "wide_pct": round(100.0 * wide_frames / max(1, idx + 1), 1),
            "players": pl, "top_speeds": top_speeds,
            "sprint_kmh_a": round(spk[0][0] / spk[0][1] * 3.6, 1) if spk[0][1] else 0.0,
            "sprint_kmh_b": round(spk[1][0] / spk[1][1] * 3.6, 1) if spk[1][1] else 0.0,
        }
        if ma: stats.update(ma["team"])
        events = sorted(poss.evlog + xev, key=lambda e_: e_[0])
        links = [[a_, b_, n_] for (a_, b_), n_ in poss.links.most_common(40)]
        kit_hex = {k: "#%02x%02x%02x" % (v[2], v[1], v[0]) for k, v in kit.items()}
        rest = {"fps": float(fps), "width": int(W), "height": int(H), "total_frames": int(idx + 1),
                "tracker": tracker_name, "mode": mode_key, "imgsz": mode["imgsz"], "team_colors": kit_hex,
                "kit_learned": bool(team.ready), "burned": bool(burn),
                "unique_ids": real, "team_info": {str(c): TEAM_CODE[team_info.get(c, "team_a")] for c in real},
                "track_len": {str(c): int(appear[c]) for c in real},
                "first_seen": {str(c): int(first_seen[c]) for c in real if c in first_seen},
                "poss_events": poss.events, "events": events, "links": links, "shots": shots, "stats": stats,
                "maps": ma["maps"] if ma else None, "pitch": ma["pitch"] if ma else None}
        el_ = max(1e-3, time.time() - t_job)
        rest["perf"] = {"seconds": round(el_, 1), "fps": round((idx + 1) / el_, 1), "realtime": round(((idx + 1) / fps) / el_, 2),
                        "encoder": enc_name, "stride": mode["stride"], "batch": bsz, "imgsz": mode["imgsz"],
                        "prepass_s": round(tm["pre"], 1), "gpu_s": round(tm["gpu"], 1), "batches": tm["batches"],
                        "wait_s": round(tm["wait"], 1), "enc_blocked_s": round(enc_blk, 1)}
        loop_s = max(1e-3, time.time() - start)
        rest["perf"]["bottleneck"] = ("detector / video decoding" if tm["wait"] / loop_s > 0.35 else
                                      "video encoder" if enc_blk / loop_s > 0.25 else "CPU tracking / drawing")
        print(f"[perf] total {el_:.1f}s | warm-up+prepass {tm['pre']:.1f}s | detector GPU busy {tm['gpu']:.1f}s in {tm['batches']} batches"
              f" | main waited on detector {tm['wait']:.1f}s | encoder back-pressure {enc_blk:.1f}s | {rest['perf']['fps']} fps"
              f" | bottleneck: {rest['perf']['bottleneck']}")
        rest["reid"] = bool(reid and gmap)
        rest["id_info"] = {str(g_): v_ for g_, v_ in id_info.items()}
        stats["reid_identities"] = len(id_info)
        with open(summ_path, "w") as sf:
            json.dump(rest, sf, cls=NumpyEncoder, separators=(",", ":"))
        gz = gzip.open(meta_path, "wt", compresslevel=3)
        gz.write('{"frames":{'); first_w = True
        with open(tmp_path) as fin:
            for line in fin:
                fi, _, body = line.rstrip("\n").partition("\t")
                r_ = json.loads(body)
                for k_ in ("k", "g", "s"): r_.pop(k_, None)      # pitch-mapper inputs, not needed by the viewers
                if gmap:
                    for p_ in r_["p"]: p_[0] = G(p_[0])
                    if r_["c"] >= 0: r_["c"] = G(r_["c"])
                body = json.dumps(r_, separators=(",", ":"))
                gz.write(("" if first_w else ",") + json.dumps(fi) + ":" + body); first_w = False
        gz.write("}," + json.dumps(rest, cls=NumpyEncoder, separators=(",", ":"))[1:])
        gz.close(); gz = None
        try: os.remove(tmp_path)
        except Exception: pass

        t = int(time.time())
        J.update(status="completed", phase="Done", progress=100.0, eta=0,
                 video_url=f"/api/video-stream/tactical_output.mp4?t={t}",
                 meta_url=f"/api/tracking-data/tactical_metadata.json.gz?t={t}")
    except Exception as e:
        import traceback; traceback.print_exc()
        J.update(status="error", error_msg=str(e))
        print(f"PIPELINE CRITICAL ERROR: {e}")
    finally:
        try:
            if reader: reader.stop = True
            if dthread: dthread.stop = True
            if cap: cap.release()
            if enc: enc.close()
            if gz: gz.close()
        except Exception:
            pass

# ----------------------------------------------------------------------------
# API
# ----------------------------------------------------------------------------
def range_streamer(path: Path, start: int, end: int, chunk: int = 512 * 1024):
    with open(path, "rb") as f:
        f.seek(start); remaining = end - start + 1
        while remaining > 0:
            data = f.read(min(chunk, remaining))
            if not data: break
            remaining -= len(data); yield data

@app.get("/api/video-stream/{file_name}")
def stream_video(file_name: str, request: Request):
    path = PROCESSED_DIR / Path(file_name.split("?")[0]).name
    if not path.exists(): raise HTTPException(status_code=404, detail="Video not found")
    size = path.stat().st_size
    rng = request.headers.get("range")
    if rng and rng.startswith("bytes="):
        try:
            a, _, b = rng[6:].split(",")[0].strip().partition("-")
            if a == "": start, end = max(0, size - int(b)), size - 1
            else: start, end = int(a), (int(b) if b else size - 1)
            end = min(end, size - 1)
            if start > end or start >= size: raise ValueError
        except Exception:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        return StreamingResponse(range_streamer(path, start, end), status_code=206, headers={
            "Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1), "Content-Type": "video/mp4"})
    return StreamingResponse(range_streamer(path, 0, size - 1), headers={
        "Accept-Ranges": "bytes", "Content-Length": str(size), "Content-Type": "video/mp4"})

@app.get("/api/tracking-data/{file_name}")
def get_tracking_data(file_name: str):
    path = PROCESSED_DIR / "tactical_metadata.json.gz"
    if not path.exists(): raise HTTPException(status_code=404, detail="Metadata not found")
    return Response(content=path.read_bytes(), media_type="application/json", headers={"Content-Encoding": "gzip"})

@app.get("/api/status")
def get_status():
    return JSONResponse(JOB_STATE)

@app.get("/api/summary")
def get_summary():
    # everything except the per-frame boxes: stats, team info, events, ID labels, perf. Used by the Trivela app.
    path = PROCESSED_DIR / "summary.json"
    if not path.exists(): raise HTTPException(status_code=404, detail="No finished analysis yet.")
    return Response(content=path.read_bytes(), media_type="application/json")

@app.get("/api/info")
def get_info():
    return {"device": DEVICE_NAME, "gpu": HAS_GPU}

@app.post("/api/cancel")
def cancel():
    JOB_CANCEL.set()
    return {"status": "cancelling"}

UPLOAD_LOCK = threading.Lock(); UPLOAD_RECV = set()

def _write_chunk(dest, data, offset, idx, total):
    # chunks are written straight to their final byte offset: no re-assembly copy, retries are idempotent
    with UPLOAD_LOCK:
        if idx == 0:
            UPLOAD_RECV.clear(); open(dest, "wb").close()
            JOB_STATE.update(status="uploading", progress=0.0, video_url="", meta_url="", error_msg="")
        if not dest.exists(): return False
        if offset < 0: offset = idx * 4 * 1024 * 1024   # Trivela's chunk size
        with open(dest, "r+b") as f:
            f.seek(offset); f.write(data)
        UPLOAD_RECV.add(idx)
        if len(UPLOAD_RECV) == total: JOB_STATE["status"] = "uploaded"
    return True

@app.post("/api/upload-chunk")
async def upload_chunk(chunk: UploadFile = File(...), fileName: str = Form(...), chunkIndex: int = Form(...),
                       totalChunks: int = Form(...), offset: int = Form(-1)):
    if JOB_STATE["status"] in BUSY:
        return JSONResponse({"error": "An analysis is already running."}, status_code=409)
    data = await chunk.read()
    ok = await asyncio.to_thread(_write_chunk, UPLOAD_DIR / "source_match.mp4", data, offset, chunkIndex, totalChunks)
    if not ok: return JSONResponse({"error": "Upload must start with chunk 0."}, status_code=400)
    return {"status": "ok", "index": chunkIndex}

def safe_local_path(name: str):
    """A video inside VIDEO_DIR (a file name, or a path inside that folder). Nothing outside it."""
    base = VIDEO_DIR.resolve()
    p = Path(name.strip())
    cands = [p] if p.is_absolute() else [base / p]
    for c in cands:
        try:
            c = c.resolve()
            if c.is_file() and c.is_relative_to(base): return c
        except Exception:
            continue
    return None

@app.post("/api/start-analysis")
def start_analysis(background_tasks: BackgroundTasks, fileName: str = Form(...), targetRes: str = Form("1080p"),
                   mode: str = Form("balanced"), source: str = Form("upload"), burn: str = Form("1"), reid: str = Form("1")):
    with JOB_LOCK:
        if JOB_STATE["status"] in BUSY:
            return JSONResponse({"error": "An analysis is already running."}, status_code=409)
        if source == "local":
            video = safe_local_path(fileName)
            if video is None:
                return JSONResponse({"error": "File not found. Put the video in the videos folder and type its file name."}, status_code=400)
        else:
            video = UPLOAD_DIR / "source_match.mp4"
            if not video.exists():
                return JSONResponse({"error": "Video file not found on server."}, status_code=400)
        JOB_CANCEL.clear()
        JOB_STATE.update(status="scanning", phase="Starting...", filename=video.name, total_frames=0, current_frame=0,
                         fps=0.0, progress=0.0, eta=0, video_url="", meta_url="", error_msg="")
    background_tasks.add_task(process_football_match, str(video), mode, targetRes, burn != "0", reid != "0")
    return {"status": "started"}

# ----------------------------------------------------------------------------
# CYBER HUD UI
# ----------------------------------------------------------------------------
HTML = r"""
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Football Tactical Engine Pro | Cyber HUD 6.1</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700;800&family=Orbitron:wght@500;700;900&display=swap" rel="stylesheet">
<style>
:root{--c-primary:#00ff87;--c-secondary:#60efff;--primary-rgb:0,255,135;--secondary-rgb:96,239,255}
body{background:#030712;color:#f8fafc;font-family:'Plus Jakarta Sans',sans-serif;overflow-x:hidden}
.font-orbitron{font-family:'Orbitron',monospace}.font-mono{font-family:'JetBrains Mono',monospace}
.cyber-canvas-bg{position:fixed;inset:0;pointer-events:none;z-index:0;background:radial-gradient(circle at 10% 15%,rgba(var(--primary-rgb),.12) 0%,transparent 45%),radial-gradient(circle at 90% 85%,rgba(var(--secondary-rgb),.12) 0%,transparent 45%),radial-gradient(circle at 50% 50%,rgba(15,23,42,.75) 0%,transparent 75%)}
.tactical-grid{position:fixed;inset:0;pointer-events:none;z-index:1;background-size:40px 40px;background-image:linear-gradient(to right,rgba(255,255,255,.02) 1px,transparent 1px),linear-gradient(to bottom,rgba(255,255,255,.02) 1px,transparent 1px);-webkit-mask-image:radial-gradient(ellipse at center,black 40%,transparent 80%);mask-image:radial-gradient(ellipse at center,black 40%,transparent 80%)}
.glass-panel{background:rgba(10,16,30,.72);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.08);box-shadow:0 20px 45px -10px rgba(0,0,0,.75);position:relative}
.glass-panel-glow{border-color:rgba(var(--primary-rgb),.35);box-shadow:0 0 35px -5px rgba(var(--primary-rgb),.2)}
.btn-cyber-primary,.tab.active{background:linear-gradient(135deg,var(--c-primary),var(--c-secondary));color:#030712;font-weight:800;letter-spacing:.08em;box-shadow:0 0 25px -4px rgba(var(--primary-rgb),.45);transition:all .3s cubic-bezier(.4,0,.2,1)}
.btn-cyber-primary:hover:not(:disabled){transform:translateY(-2px) scale(1.01);box-shadow:0 0 35px -2px rgba(var(--secondary-rgb),.65)}
.tab{color:#94a3b8;font-weight:700;transition:all .2s}.tab:not(.active):hover{color:#fff;background:rgba(255,255,255,.05)}
.opt{border:1px solid rgba(255,255,255,.1);background:rgba(15,23,42,.6);transition:all .2s;text-align:left}
.opt:hover{border-color:#64748b}
.opt.active{border-color:var(--c-primary);background:rgba(var(--primary-rgb),.1);box-shadow:0 0 15px rgba(var(--primary-rgb),.25)}
.tint{background:rgba(var(--primary-rgb),.12);border:1px solid rgba(var(--primary-rgb),.35);color:var(--c-primary)}
.corner-reticle::before,.corner-reticle::after{content:'';position:absolute;width:10px;height:10px;pointer-events:none}
.corner-reticle::before{top:-1px;left:-1px;border-top:2px solid var(--c-primary);border-left:2px solid var(--c-primary)}
.corner-reticle::after{bottom:-1px;right:-1px;border-bottom:2px solid var(--c-secondary);border-right:2px solid var(--c-secondary)}
@keyframes scanline{0%{transform:translateY(-100%)}100%{transform:translateY(1000%)}}
.scanline-beam{position:absolute;left:0;right:0;top:0;height:3px;background:linear-gradient(90deg,transparent,var(--c-primary),var(--c-secondary),transparent);animation:scanline 8s linear infinite;opacity:.35;pointer-events:none}
@keyframes dual-shimmer{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}
.dual-gradient-bar{background:linear-gradient(270deg,var(--c-primary),var(--c-secondary),var(--c-primary));background-size:250% 250%;animation:dual-shimmer 3s ease infinite}
.custom-scrollbar::-webkit-scrollbar{width:5px}.custom-scrollbar::-webkit-scrollbar-track{background:rgba(5,8,16,.5)}
.custom-scrollbar::-webkit-scrollbar-thumb{background:linear-gradient(to bottom,var(--c-primary),var(--c-secondary));border-radius:9999px}
.stat{background:rgba(2,6,23,.7);border:1px solid rgba(255,255,255,.08)}
.carrier-row{border-color:#f59e0b!important;background:rgba(120,53,15,.35)!important}
.pinned-row{box-shadow:0 0 0 1px var(--c-primary)}
.off-row{opacity:.5}
.chip{padding:.25rem .6rem;border-radius:.6rem;font-size:11px}
.ev-row{display:flex;gap:.5rem;align-items:center;width:100%;text-align:left;padding:.35rem .5rem;border-radius:.6rem;font-size:11px}
.ev-row:hover{background:rgba(255,255,255,.06)}
</style>
</head>
<body class="min-h-screen relative flex flex-col items-center p-3 sm:p-6 md:p-8">
<div class="cyber-canvas-bg"></div><div class="tactical-grid"></div>

<div class="max-w-6xl w-full relative z-10 space-y-6">
  <header class="glass-panel corner-reticle rounded-2xl p-4 sm:p-5 flex flex-wrap justify-between items-center gap-4">
    <div class="flex items-center gap-3.5">
      <div class="relative w-12 h-12 rounded-xl flex items-center justify-center p-[1px] bg-gradient-to-br from-[var(--c-primary)] to-[var(--c-secondary)]">
        <div class="w-full h-full bg-[#070b14] rounded-xl flex items-center justify-center">
          <svg class="w-6 h-6 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/></svg>
        </div>
      </div>
      <div>
        <div class="flex items-center gap-2">
          <h1 class="font-orbitron text-lg sm:text-xl font-black tracking-wider text-white">TACTICAL VISION</h1>
          <span class="font-mono text-[10px] px-2 py-0.5 rounded font-black bg-gradient-to-r from-[var(--c-primary)] to-[var(--c-secondary)] text-black">PRO v7.0</span>
        </div>
        <p class="font-mono text-[11px] text-slate-400 flex items-center gap-2 mt-0.5">
          <span class="w-1.5 h-1.5 rounded-full bg-[var(--c-primary)] animate-ping"></span>
          <span>PRECISION SCAN</span><span>•</span><span class="text-slate-300">DEEP MATCH ANALYTICS</span>
        </p>
      </div>
    </div>
    <div class="flex items-center gap-3">
      <div class="hidden sm:flex flex-col text-right font-mono pr-3 border-r border-white/10">
        <span class="text-[10px] text-slate-400 uppercase tracking-widest">NEURAL ACCELERATOR</span>
        <span class="text-xs font-bold text-white flex items-center justify-end gap-1.5"><span class="w-2 h-2 rounded-full bg-[var(--c-primary)] animate-pulse"></span><span id="deviceName">DETECTING...</span></span>
      </div>
      <button id="btnOpenSettings" class="p-2.5 rounded-xl glass-panel hover:border-white/30 text-slate-300 hover:text-white transition flex items-center gap-2 font-mono text-xs">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 3v2m0 14v2M5.6 5.6l1.4 1.4m10 10l1.4 1.4M3 12h2m14 0h2M5.6 18.4L7 17m10-10l1.4-1.4M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
        <span class="hidden md:inline font-bold">THEME HUD</span>
      </button>
    </div>
  </header>

  <div class="grid grid-cols-2 gap-2 glass-panel p-1.5 rounded-2xl border border-white/5 font-mono text-xs">
    <button id="tabUpload" class="tab active py-3 rounded-xl flex items-center justify-center gap-2">UPLOAD MATCH FOOTAGE</button>
    <button id="tabLocal" class="tab py-3 rounded-xl flex items-center justify-center gap-2">LOCAL VIDEOS (0s)</button>
  </div>

  <!-- ENGINE SETTINGS -->
  <div class="glass-panel p-5 rounded-2xl space-y-4">
    <div>
      <div class="flex justify-between items-center mb-2.5">
        <span class="text-xs uppercase font-extrabold text-slate-300 font-mono tracking-wider">OUTPUT RESOLUTION</span>
        <span class="text-[11px] text-slate-400 font-mono">Aspect ratio kept • never upscaled</span>
      </div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <button type="button" data-res="720p" class="res-btn opt active p-3 rounded-xl"><div class="text-xs font-black text-white">⚡ 720p</div><div class="text-[11px] text-[var(--c-primary)] font-mono font-bold">Fastest • default</div></button>
        <button type="button" data-res="1080p" class="res-btn opt p-3 rounded-xl"><div class="text-xs font-black text-white">🎯 1080p</div><div class="text-[11px] text-[var(--c-primary)] font-mono font-bold">Broadcast</div></button>
        <button type="button" data-res="1440p" class="res-btn opt p-3 rounded-xl"><div class="text-xs font-black text-white">💎 1440p</div><div class="text-[11px] text-[var(--c-secondary)] font-mono font-bold">Crisp pro</div></button>
        <button type="button" data-res="source" class="res-btn opt p-3 rounded-xl"><div class="text-xs font-black text-white">📼 Source</div><div class="text-[11px] text-[var(--c-secondary)] font-mono font-bold">Original size</div></button>
      </div>
    </div>
    <div>
      <div class="flex justify-between items-center mb-2.5">
        <span class="text-xs uppercase font-extrabold text-slate-300 font-mono tracking-wider">SCAN PRECISION</span>
        <span class="text-[11px] text-slate-400 font-mono">How often the detector runs &amp; at what size (batched on GPU)</span>
      </div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <button type="button" data-mode="lightning" class="mode-btn opt p-3 rounded-xl"><div class="text-xs font-black text-white">⚡ Lightning</div><div class="text-[11px] text-slate-400 font-mono">960px • every 4th frame • fastest</div></button>
        <button type="button" data-mode="fast" class="mode-btn opt active p-3 rounded-xl"><div class="text-xs font-black text-white">🚀 Turbo</div><div class="text-[11px] text-slate-400 font-mono">960px • every 2nd frame • default</div></button>
        <button type="button" data-mode="balanced" class="mode-btn opt p-3 rounded-xl"><div class="text-xs font-black text-white">⚖️ Balanced</div><div class="text-[11px] text-slate-400 font-mono">1280px • every 2nd frame</div></button>
        <button type="button" data-mode="ultra" class="mode-btn opt p-3 rounded-xl"><div class="text-xs font-black text-white">🔬 Ultra</div><div class="text-[11px] text-slate-400 font-mono">1600px • every frame • best ball</div></button>
      </div>
    </div>
  </div>

  <label class="glass-panel rounded-2xl px-5 py-3 flex items-center gap-3 text-xs font-mono text-slate-300 cursor-pointer"><input type="checkbox" id="optBurn" checked> Burn graphics into the video (untick for a clean video with fully toggleable live overlays)</label>
  <label class="glass-panel rounded-2xl px-5 py-3 flex items-center gap-3 text-xs font-mono text-slate-300 cursor-pointer"><input type="checkbox" id="optReid" checked> Persistent player IDs: the same player keeps one ID across camera cuts and while off-screen (matched by head, shirt, shorts &amp; socks colours)</label>
  <label class="glass-panel rounded-2xl px-5 py-3 flex items-center gap-3 text-xs font-mono text-slate-300">Upload streams <select id="upStreams" class="bg-slate-900 border border-white/10 rounded-lg px-2 py-1"><option value="1">1</option><option value="2">2</option><option value="3" selected>3</option><option value="4">4</option></select> <span class="text-slate-500">(if upload feels slow, try 1–2; or skip uploading entirely with the LOCAL VIDEOS tab)</span></label>
  <div id="panelUpload" class="space-y-5">
    <div id="dropZone" class="glass-panel corner-reticle rounded-3xl p-10 sm:p-14 text-center cursor-pointer transition-all duration-300 relative overflow-hidden group hover:border-[var(--c-primary)]">
      <div class="scanline-beam"></div>
      <input type="file" id="fileInput" class="hidden" accept="video/*" />
      <div class="relative z-10 flex flex-col items-center">
        <div class="w-20 h-20 rounded-2xl bg-white/[0.04] border border-white/10 flex items-center justify-center mb-4 group-hover:scale-110 group-hover:border-[var(--c-primary)] transition-all duration-300">
          <svg class="w-9 h-9 text-[var(--c-primary)]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>
        </div>
        <h3 class="text-base sm:text-lg font-extrabold text-white tracking-wide">Drop Match Broadcast or Click to Browse</h3>
        <p class="text-xs text-slate-400 font-mono mt-1 max-w-md">Resumable chunk upload • corrupt containers repaired automatically</p>
        <p id="selectedFileText" class="text-xs font-mono font-bold mt-4 px-3.5 py-1.5 rounded-full bg-white/5 border border-white/10 text-[var(--c-primary)] hidden"></p>
      </div>
    </div>
    <button id="uploadBtn" disabled class="w-full py-4 rounded-2xl bg-slate-800/80 text-slate-500 font-black tracking-wider text-sm shadow-xl transition-all duration-300 cursor-not-allowed uppercase font-mono">START TACTICAL ANALYSIS</button>
  </div>

  <div id="panelLocal" class="hidden glass-panel corner-reticle rounded-3xl p-6 sm:p-8 space-y-4">
    <div>
      <label class="text-xs uppercase font-extrabold text-slate-300 font-mono tracking-wider">Local videos folder</label>
      <p class="text-xs text-slate-400 mt-1 mb-3">Instant ingestion from a video already in the videos folder (type just the file name).</p>
      <input type="text" id="localFileInput" placeholder="match.mp4" class="w-full bg-slate-950/80 border border-white/10 rounded-xl px-4 py-3.5 text-sm text-white focus:outline-none focus:border-[var(--c-primary)] font-mono transition" />
    </div>
    <button id="localStartBtn" class="w-full py-4 rounded-xl btn-cyber-primary font-black text-sm uppercase tracking-wider font-mono">ANALYZE INSTANT FILE (0s Upload)</button>
  </div>

  <div id="errorBanner" class="hidden glass-panel border-red-500/50 bg-red-950/40 text-red-200 px-5 py-4 rounded-2xl text-xs font-mono shadow-2xl flex items-center gap-3">
    <span class="w-2.5 h-2.5 rounded-full bg-red-500 animate-ping"></span><span id="errorText"></span>
  </div>

  <div id="statusCard" class="hidden glass-panel corner-reticle rounded-3xl p-6 sm:p-8 space-y-5">
    <div class="flex flex-wrap justify-between items-center gap-2">
      <div>
        <span id="badgeStatus" class="px-3 py-1 rounded-full text-xs font-mono font-bold bg-white/5 border border-white/10 text-[var(--c-primary)] tracking-wider">INITIALIZING</span>
        <h2 id="phaseLabel" class="text-lg sm:text-xl font-black text-white mt-2 font-orbitron tracking-wide">AI Telemetry Processing...</h2>
      </div>
      <div class="text-right font-mono">
        <span id="rateStat" class="text-[var(--c-primary)] font-black text-xl">0.0 FPS</span>
        <div id="frameCounter" class="text-slate-400 text-xs mt-0.5">Frame: 0 / 0</div>
      </div>
    </div>
    <div class="w-full bg-slate-950 rounded-full h-3.5 overflow-hidden p-0.5 border border-white/10"><div id="progressBar" class="dual-gradient-bar h-full rounded-full transition-all duration-300" style="width:0%"></div></div>
    <div class="flex justify-between items-center text-xs text-slate-400 font-mono">
      <span id="percentageText">0%</span><span id="subDetailText">Processing</span>
      <button id="cancelBtn" class="px-3 py-1 rounded-lg border border-red-500/40 text-red-300 hover:bg-red-500/10 transition hidden">CANCEL</button>
    </div>
  </div>

  <div id="previewCard" class="hidden glass-panel corner-reticle rounded-3xl p-5 sm:p-7 space-y-5">
    <div class="flex flex-wrap justify-between items-center gap-2 pb-3 border-b border-white/10">
      <div>
        <span class="px-3 py-1 rounded-full text-xs font-mono font-extrabold tint">● ANALYSIS SYNCHRONIZED</span>
        <h2 class="text-lg sm:text-xl font-black text-white mt-1.5 font-orbitron">Tactical Pitch Inspector</h2>
      </div>
      <div class="flex flex-wrap items-center gap-2 font-mono text-xs">
        <span id="activeResBadge" class="text-slate-400 px-3 py-1.5 rounded-lg bg-slate-900 border border-white/10"></span>
        <button id="snapBtn" class="px-3 py-1.5 rounded-lg border border-white/15 text-slate-200 hover:border-[var(--c-primary)] transition">📸 SNAPSHOT</button>
        <button id="csvBtn" class="px-3 py-1.5 rounded-lg border border-white/15 text-slate-200 hover:border-[var(--c-primary)] transition">⬇ CSV</button>
        <a id="dlVideo" class="px-3 py-1.5 rounded-lg border border-white/15 text-slate-200 hover:border-[var(--c-primary)] transition" download="tactical_output.mp4">⬇ VIDEO</a>
        <a id="dlMeta" class="px-3 py-1.5 rounded-lg border border-white/15 text-slate-200 hover:border-[var(--c-primary)] transition" download="tactical_metadata.json">⬇ JSON</a>
      </div>
    </div>

    <div class="grid grid-cols-1 xl:grid-cols-3 gap-6">
      <div class="xl:col-span-2 space-y-3">
        <div id="stage" class="relative rounded-2xl overflow-hidden border border-white/10 bg-black shadow-2xl" style="aspect-ratio:16/9">
          <video id="player" controls playsinline preload="auto" class="absolute inset-0 w-full h-full"></video>
          <canvas id="overlayCanvas" class="absolute top-0 left-0 w-full h-full pointer-events-none" style="bottom:48px"></canvas>
        </div>
        <canvas id="timeline" class="w-full rounded-lg border border-white/10 cursor-pointer" height="44" title="Possession strip (A/B colours), wide-shot bands and events. Click to seek."></canvas>
        <div class="flex flex-wrap items-center gap-2 font-mono text-xs text-slate-300">
          <button id="stepBack" class="opt chip">⏮ −1f</button><button id="stepFwd" class="opt chip">+1f ⏭</button>
          <select id="speedSel" class="bg-slate-900 border border-white/10 rounded-lg px-2 py-1"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="1.5">1.5×</option><option value="2">2×</option></select>
          <span class="text-slate-500 mx-1">|</span>
          <label class="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" id="optLabels" checked> Labels</label>
          <label class="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" id="optCarrierOnly"> Carrier only</label>
          <label class="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" id="optDim" checked> Dim others when pinned</label>
          <span class="text-slate-500 mx-1">|</span>
          <span id="lblChips" class="flex gap-1">
            <button data-f="all" class="opt active chip">All</button><button data-f="0" class="opt chip">A</button><button data-f="1" class="opt chip">B</button><button data-f="2" class="opt chip">GK</button><button data-f="3" class="opt chip">REF</button>
          </span>
        </div>
        <p class="text-[10px] font-mono text-slate-500">Shortcuts: Space play/pause • ←/→ step frame (Shift = 1s) • L labels • P unpin • M mute</p>
      </div>

      <div class="bg-slate-950/80 border border-white/10 rounded-2xl p-4 flex flex-col h-[680px] xl:h-auto shadow-inner">
        <div class="p-3.5 rounded-xl bg-gradient-to-r from-amber-950/30 via-slate-900 to-slate-950 border border-amber-500/40 flex items-center justify-between text-xs font-mono mb-3 shadow-lg">
          <span class="text-amber-400 uppercase font-black flex items-center gap-2"><span class="w-2.5 h-2.5 rounded-full bg-amber-400 animate-ping"></span>⚽ POSSESSION</span>
          <span id="ballCarrierHud" class="text-amber-300 font-bold px-2 py-0.5 rounded bg-black/60 border border-amber-500/30">Free ball</span>
        </div>
        <div class="mb-3">
          <div class="flex justify-between text-[11px] font-mono mb-1"><span id="possTxtA" class="font-bold">A 0%</span><span class="text-slate-500">LIVE TEAM POSSESSION</span><span id="possTxtB" class="font-bold">B 0%</span></div>
          <div class="h-2.5 rounded-full overflow-hidden flex bg-slate-800"><div id="possBarA" style="width:50%"></div><div id="possBarB" style="width:50%"></div></div>
        </div>
        <div id="pinCard" class="hidden mb-3 p-3 rounded-xl stat text-xs font-mono space-y-1.5">
          <div class="flex justify-between items-center"><span id="pinName" class="font-black text-white"></span><button id="pinClear" class="text-slate-400 hover:text-white">✕ unpin</button></div>
          <div id="pinStats" class="grid grid-cols-3 gap-2 text-[11px] text-slate-300"></div>
        </div>
        <div class="flex justify-between items-center pb-2 border-b border-white/10 mb-2">
          <h3 class="text-xs font-extrabold text-white uppercase tracking-wider font-mono flex items-center gap-2"><span class="w-2 h-2 rounded-full bg-[var(--c-primary)]"></span>SQUAD</h3>
          <span id="playerCount" class="text-xs font-mono text-slate-400">0</span>
        </div>
        <div class="flex gap-2 mb-2 font-mono text-[11px]">
          <button data-m="live" class="rmode opt active chip">On screen</button><button data-m="all" class="rmode opt chip">All tracked</button>
          <input id="rosterSearch" placeholder="search…" class="flex-1 min-w-0 bg-slate-900 border border-white/10 rounded-lg px-2 py-1 focus:outline-none focus:border-[var(--c-primary)]">
          <select id="rosterSort" class="bg-slate-900 border border-white/10 rounded-lg px-1 py-1"><option value="team">Team</option><option value="id">ID</option><option value="touches">Touches</option><option value="speed">Top speed</option><option value="dist">Distance</option></select>
        </div>
        <div id="rosterList" class="flex-1 overflow-y-auto space-y-2 pr-1 custom-scrollbar"></div>
      </div>
    </div>

    <div id="statsGrid" class="grid grid-cols-2 md:grid-cols-5 gap-3 font-mono"></div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 font-mono">
      <div class="stat rounded-xl p-3 flex flex-col h-72">
        <div class="flex justify-between items-center mb-2"><span class="text-xs font-black text-white uppercase tracking-wider">Match events</span>
          <select id="evFilter" class="bg-slate-900 border border-white/10 rounded-lg px-1 py-0.5 text-[11px]"><option value="all">All</option><option value="pass">Passes</option><option value="won">Ball won</option><option value="strike">Strikes</option><option value="sprint">Sprints</option></select></div>
        <div id="eventList" class="flex-1 overflow-y-auto custom-scrollbar space-y-0.5"></div>
      </div>
      <div class="stat rounded-xl p-3 flex flex-col h-72">
        <span class="text-xs font-black text-white uppercase tracking-wider mb-2">Fastest runners (est.)</span>
        <div id="speedList" class="flex-1 overflow-y-auto custom-scrollbar space-y-0.5"></div>
      </div>
      <div class="stat rounded-xl p-3 flex flex-col h-72">
        <span class="text-xs font-black text-white uppercase tracking-wider mb-2">Passing links</span>
        <div id="linkList" class="flex-1 overflow-y-auto custom-scrollbar space-y-0.5"></div>
      </div>
    </div>
    <div class="stat rounded-xl p-3 font-mono">
      <div class="flex flex-wrap justify-between items-center gap-2 mb-2">
        <span class="text-xs font-black text-white uppercase tracking-wider">🏃 Distance run per player ID (est.)</span>
        <span id="distTotals" class="text-[11px] text-slate-400"></span>
      </div>
      <div id="distList" class="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1 max-h-80 overflow-y-auto custom-scrollbar pr-1"></div>
    </div>
    <p class="text-[10px] text-slate-500 font-mono">Speeds, distances and ball speed are camera-compensated estimates (player height used as a ruler). With persistent IDs on, each player keeps one ID (A1, B4, GK1, REF1…) across cuts and while off-screen; they are matched by colour signature, so same-kit teammates are the hardest case — check the ID confidence in the pinned card. With it off, IDs restart after every cut.</p>
  </div>
</div>

<!-- THEME MODAL -->
<div id="settingsModal" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md hidden opacity-0 transition-opacity duration-300">
  <div class="glass-panel corner-reticle rounded-3xl p-6 sm:p-8 max-w-lg w-full space-y-6 border border-white/15 shadow-2xl transform scale-95 transition-transform duration-300" id="settingsModalCard">
    <div class="flex justify-between items-center pb-4 border-b border-white/10">
      <h3 class="font-orbitron text-lg font-bold text-white">THEME CUSTOMIZER</h3>
      <button id="btnCloseSettings" class="text-slate-400 hover:text-white transition p-1 text-xl">✕</button>
    </div>
    <div class="grid grid-cols-2 gap-4">
      <div class="bg-slate-900/80 p-3.5 rounded-xl border border-white/10 space-y-2">
        <label class="text-[11px] font-mono font-bold text-slate-300 uppercase">Primary Accent</label>
        <div class="flex items-center gap-2"><input type="color" id="primaryPicker" value="#00ff87" class="w-10 h-10 rounded-lg bg-transparent border-0 cursor-pointer" /><span id="primaryHexLabel" class="font-mono text-xs text-slate-300 font-bold">#00ff87</span></div>
      </div>
      <div class="bg-slate-900/80 p-3.5 rounded-xl border border-white/10 space-y-2">
        <label class="text-[11px] font-mono font-bold text-slate-300 uppercase">Secondary Accent</label>
        <div class="flex items-center gap-2"><input type="color" id="secondaryPicker" value="#60efff" class="w-10 h-10 rounded-lg bg-transparent border-0 cursor-pointer" /><span id="secondaryHexLabel" class="font-mono text-xs text-slate-300 font-bold">#60efff</span></div>
      </div>
    </div>
    <div class="space-y-2.5">
      <span class="text-[11px] font-mono font-bold uppercase tracking-wider text-slate-400">TACTICAL PRESET KITS</span>
      <div class="grid grid-cols-2 gap-2 font-mono text-xs">
        <button type="button" class="preset-btn p-2.5 rounded-xl bg-slate-900/60 border border-white/10 hover:border-white/30 text-left flex items-center gap-2" data-p="#00ff87" data-s="#60efff"><span class="w-4 h-4 rounded-full bg-gradient-to-r from-[#00ff87] to-[#60efff]"></span><span class="text-white font-bold">Cyber Emerald</span></button>
        <button type="button" class="preset-btn p-2.5 rounded-xl bg-slate-900/60 border border-white/10 hover:border-white/30 text-left flex items-center gap-2" data-p="#ff007f" data-s="#7928ca"><span class="w-4 h-4 rounded-full bg-gradient-to-r from-[#ff007f] to-[#7928ca]"></span><span class="text-white font-bold">Neon Synth</span></button>
        <button type="button" class="preset-btn p-2.5 rounded-xl bg-slate-900/60 border border-white/10 hover:border-white/30 text-left flex items-center gap-2" data-p="#f59e0b" data-s="#ef4444"><span class="w-4 h-4 rounded-full bg-gradient-to-r from-[#f59e0b] to-[#ef4444]"></span><span class="text-white font-bold">War Room Amber</span></button>
        <button type="button" class="preset-btn p-2.5 rounded-xl bg-slate-900/60 border border-white/10 hover:border-white/30 text-left flex items-center gap-2" data-p="#00f0ff" data-s="#7000ff"><span class="w-4 h-4 rounded-full bg-gradient-to-r from-[#00f0ff] to-[#7000ff]"></span><span class="text-white font-bold">Hyper Velocity</span></button>
      </div>
    </div>
    <button id="btnApplySettings" class="w-full py-3.5 rounded-xl btn-cyber-primary font-mono font-bold text-xs uppercase tracking-wider">APPLY THEME</button>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

// ---------------- THEME ENGINE ---------------- //
function hexToRgb(hex){hex=hex.replace('#','');if(hex.length===3)hex=hex.split('').map(x=>x+x).join('');const n=parseInt(hex,16);return `${(n>>16)&255}, ${(n>>8)&255}, ${n&255}`}
function applyColors(p,s){
  const r=document.documentElement;
  r.style.setProperty('--c-primary',p);r.style.setProperty('--c-secondary',s);
  r.style.setProperty('--primary-rgb',hexToRgb(p));r.style.setProperty('--secondary-rgb',hexToRgb(s));
  $('primaryPicker').value=p;$('secondaryPicker').value=s;$('primaryHexLabel').innerText=p;$('secondaryHexLabel').innerText=s;
  try{localStorage.setItem('tactical_p_color',p);localStorage.setItem('tactical_s_color',s)}catch(e){}
}
let sp='#00ff87',ss='#60efff';
try{sp=localStorage.getItem('tactical_p_color')||sp;ss=localStorage.getItem('tactical_s_color')||ss}catch(e){}
applyColors(sp,ss);
$('primaryPicker').oninput=e=>applyColors(e.target.value,$('secondaryPicker').value);
$('secondaryPicker').oninput=e=>applyColors($('primaryPicker').value,e.target.value);
document.querySelectorAll('.preset-btn').forEach(b=>b.onclick=()=>applyColors(b.dataset.p,b.dataset.s));
function openSettings(){$('settingsModal').classList.remove('hidden');setTimeout(()=>{$('settingsModal').classList.remove('opacity-0');$('settingsModalCard').classList.remove('scale-95')},10)}
function closeSettings(){$('settingsModal').classList.add('opacity-0');$('settingsModalCard').classList.add('scale-95');setTimeout(()=>$('settingsModal').classList.add('hidden'),250)}
$('btnOpenSettings').onclick=openSettings;$('btnCloseSettings').onclick=closeSettings;$('btnApplySettings').onclick=closeSettings;
$('settingsModal').onclick=e=>{if(e.target===$('settingsModal'))closeSettings()};

fetch('/api/info').then(r=>r.json()).then(i=>{$('deviceName').innerText=(i.gpu?'GPU • ':'CPU • ')+i.device.toUpperCase()}).catch(()=>{$('deviceName').innerText='UNKNOWN'});

// ---------------- STATE ---------------- //
let selectedFile=null,selRes='720p',selMode='fast',trackingData=null,segs=[],curCarrier=-2,loopOn=false;
const nameMap={};
const CHUNK_SIZE=8*1024*1024;
const TEAM_KEYS=['team_a','team_b','goalkeeper','official'];
const TEAM_NAMES=['Team A','Team B','GK','REF'];

function bindGroup(sel,attr,setter){document.querySelectorAll(sel).forEach(b=>b.onclick=()=>{document.querySelectorAll(sel).forEach(x=>x.classList.remove('active'));b.classList.add('active');setter(b.dataset[attr])})}
bindGroup('.res-btn','res',v=>selRes=v);
bindGroup('.mode-btn','mode',v=>selMode=v);

$('tabUpload').onclick=()=>{$('tabUpload').classList.add('active');$('tabLocal').classList.remove('active');$('panelUpload').classList.remove('hidden');$('panelLocal').classList.add('hidden')};
$('tabLocal').onclick=()=>{$('tabLocal').classList.add('active');$('tabUpload').classList.remove('active');$('panelLocal').classList.remove('hidden');$('panelUpload').classList.add('hidden')};

const dz=$('dropZone');
dz.onclick=()=>$('fileInput').click();
$('fileInput').onchange=e=>handleFile(e.target.files[0]);
dz.ondragover=e=>{e.preventDefault();dz.classList.add('glass-panel-glow')};
dz.ondragleave=()=>dz.classList.remove('glass-panel-glow');
dz.ondrop=e=>{e.preventDefault();dz.classList.remove('glass-panel-glow');if(e.dataTransfer.files.length)handleFile(e.dataTransfer.files[0])};
function handleFile(f){
  if(!f)return;selectedFile=f;
  $('selectedFileText').innerText=`${f.name} (${(f.size/1048576).toFixed(1)} MB)`;$('selectedFileText').classList.remove('hidden');
  const b=$('uploadBtn');b.disabled=false;b.classList.remove('bg-slate-800/80','text-slate-500','cursor-not-allowed');b.classList.add('btn-cyber-primary');
}
function showError(m){$('errorText').innerText=m;$('errorBanner').classList.remove('hidden')}
function fmtEta(s){if(!s||s<=0)return '';const m=Math.floor(s/60);return m>0?`${m}m ${s%60}s left`:`${s}s left`}

async function startAnalysis(fileName,source){
  const fd=new FormData();fd.append('fileName',fileName);fd.append('targetRes',selRes);fd.append('mode',selMode);fd.append('source',source);fd.append('burn',$('optBurn').checked?'1':'0');fd.append('reid',$('optReid').checked?'1':'0');
  const r=await fetch('/api/start-analysis',{method:'POST',body:fd});
  if(!r.ok){let m='Failed to start analysis.';try{const j=await r.json();m=j.error||j.detail||m}catch(e){}throw new Error(m)}
  poll();
}

$('localStartBtn').onclick=async()=>{
  const fn=$('localFileInput').value.trim();
  if(!fn)return alert('Enter the video file name.');
  $('previewCard').classList.add('hidden');$('statusCard').classList.remove('hidden');$('errorBanner').classList.add('hidden');
  $('badgeStatus').innerText='LOCAL INGESTION';$('phaseLabel').innerText='Loading from the videos folder...';
  try{await startAnalysis(fn,'local')}catch(e){$('statusCard').classList.add('hidden');showError(e.message)}
};

$('uploadBtn').onclick=async()=>{
  if(!selectedFile)return;
  const btn=$('uploadBtn');btn.disabled=true;dz.style.pointerEvents='none';$('errorBanner').classList.add('hidden');
  $('previewCard').classList.add('hidden');$('statusCard').classList.remove('hidden');$('cancelBtn').classList.add('hidden');
  $('badgeStatus').innerText='UPLOADING';$('phaseLabel').innerText='Streaming Video to Server...';
  $('progressBar').style.width='0%';$('percentageText').innerText='0%';
  try{
    const total=Math.ceil(selectedFile.size/CHUNK_SIZE),t0=Date.now();let bytes=0,failed=null;
    const send=async i=>{
      const s=i*CHUNK_SIZE,e=Math.min(s+CHUNK_SIZE,selectedFile.size);let lastErr='';
      for(let a=0;a<5;a++){
        const fd=new FormData();fd.append('chunk',selectedFile.slice(s,e));fd.append('fileName',selectedFile.name);fd.append('chunkIndex',i);fd.append('totalChunks',total);fd.append('offset',s);
        try{
          const r=await fetch('/api/upload-chunk',{method:'POST',body:fd});
          if(r.ok){
            bytes+=e-s;const pct=(bytes/selectedFile.size*100).toFixed(1);
            $('progressBar').style.width=pct+'%';$('percentageText').innerText=pct+'%';
            $('rateStat').innerText=`${((bytes/1048576)/((Date.now()-t0)/1000+.1)).toFixed(1)} MB/s`;
            $('subDetailText').innerText=`${(bytes/1048576).toFixed(0)} / ${(selectedFile.size/1048576).toFixed(0)} MB • ${$('upStreams').value} stream(s)`;
            return;
          }
          const j=await r.json().catch(()=>({}));lastErr=j.error||('HTTP '+r.status);if(r.status===409||r.status===400)break;
        }catch(err){lastErr=err.message;await new Promise(r=>setTimeout(r,600*(a+1)))}
      }
      throw new Error(`Packet ${i+1}/${total} failed: ${lastErr}`);
    };
    await send(0);
    let next=1;
    const worker=async()=>{while(failed===null){const i=next++;if(i>=total)return;try{await send(i)}catch(err){failed=err;throw err}}};
    await Promise.all(Array.from({length:+$('upStreams').value},worker));
    $('badgeStatus').innerText='INITIALIZING AI';$('rateStat').innerText='Active...';
    await startAnalysis(selectedFile.name,'upload');
  }catch(err){
    $('statusCard').classList.add('hidden');showError('Upload Error: '+err.message);
    btn.disabled=false;dz.style.pointerEvents='auto';
  }
};

$('cancelBtn').onclick=()=>fetch('/api/cancel',{method:'POST'});

async function poll(){
  let d;
  try{d=await (await fetch('/api/status')).json()}catch(e){setTimeout(poll,2000);return}
  if(['scanning','processing','finalizing'].includes(d.status)){
    $('previewCard').classList.add('hidden');$('statusCard').classList.remove('hidden');$('cancelBtn').classList.remove('hidden');
    $('badgeStatus').innerText=d.status==='scanning'?'CALIBRATING':d.status==='finalizing'?'FINALIZING':'FOOTBALL AI ACTIVE';
    $('phaseLabel').innerText=d.phase||'Processing...';
    $('progressBar').style.width=d.progress+'%';$('percentageText').innerText=d.progress+'%';
    $('rateStat').innerText=`${d.fps} FPS`;$('frameCounter').innerText=`Frame: ${d.current_frame} / ${d.total_frames||'?'}`;
    $('subDetailText').innerText=fmtEta(d.eta)||'Detector + tracker + possession engine';
    setTimeout(poll,900);
  }else if(d.status==='completed'&&d.video_url){await loadResult(d)}
  else if(d.status==='error'){$('statusCard').classList.add('hidden');showError('Analysis Error: '+d.error_msg);resetUploadUi()}
  else if(d.status==='cancelled'){$('statusCard').classList.add('hidden');showError('Analysis cancelled.');resetUploadUi()}
  else setTimeout(poll,1000);
}
function resetUploadUi(){dz.style.pointerEvents='auto';if(selectedFile)$('uploadBtn').disabled=false}

async function loadResult(d){
  try{
    trackingData=await (await fetch(d.meta_url)).json();
    buildPoss(trackingData.poss_events,trackingData.total_frames);
    initUi();
    $('statusCard').classList.add('hidden');$('previewCard').classList.remove('hidden');resetUploadUi();
    $('stage').style.aspectRatio=`${trackingData.width} / ${trackingData.height}`;
    const pf=trackingData.perf;$('activeResBadge').innerText=`${trackingData.width}×${trackingData.height} • ${trackingData.tracker}`+(pf?` • analysed in ${pf.seconds}s (${pf.fps} fps, ${pf.realtime}× realtime, ${pf.encoder}) • bottleneck: ${pf.bottleneck}`:'');
    $('dlVideo').href=d.video_url;$('dlMeta').href=d.meta_url;
    const p=$('player');
    p.onloadedmetadata=()=>{$('overlayCanvas').width=p.videoWidth;$('overlayCanvas').height=p.videoHeight;startLoop()};
    p.oncanplay=()=>{p.muted=true;p.play().catch(()=>{})};
    p.src=d.video_url;p.load();
  }catch(e){showError('Failed to initialize player: '+e.message)}
}

// ---------------- LIVE UI (v4) ---------------- //
let lastF=0,tlStatic=null,pinned=null,recent=[],rosterSig='',rosterMode='live',lblFilter='all',saveT=null,nameKey='';
const rowEls=new Map();
const lab=id=>{const i=trackingData.id_info&&trackingData.id_info[id];return i?i.label:`#${id}`};
const nm=id=>nameMap[id]||lab(id);
let onScreen=new Set();
const tcol=c=>trackingData.team_colors[TEAM_KEYS[c]]||'#fff';
const fmtT=f=>{const s=Math.floor(f/trackingData.fps);return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`};
const seekF=f=>{$('player').currentTime=Math.max(0,f/trackingData.fps)};
const pl=id=>(trackingData.stats.players[id]||{});

function saveNames(){
  clearTimeout(saveT);
  saveT=setTimeout(()=>{try{localStorage.setItem(nameKey,JSON.stringify(nameMap))}catch(e){};buildEventList();buildSideLists();buildDistList()},500);
}
function initUi(){
  Object.keys(nameMap).forEach(k=>delete nameMap[k]);
  const td=trackingData;nameKey=`tactical_names_${td.width}x${td.height}x${td.total_frames}`;
  try{Object.assign(nameMap,JSON.parse(localStorage.getItem(nameKey)||'{}'))}catch(e){}
  rowEls.clear();recent=[];pinned=null;rosterSig='';curCarrier=-2;
  rosterMode=(td.id_info&&Object.keys(td.id_info).length)?'all':'live';
  document.querySelectorAll('.rmode').forEach(x=>x.classList.toggle('active',x.dataset.m===rosterMode));
  $('rosterList').innerHTML='';
  buildStats();buildEventList();buildSideLists();buildDistList();buildTimeline();updatePinCard();
  $('playerCount').innerText=`${td.unique_ids.length} IDs`;
}

// ---- possession segments ----
function buildPoss(ev,total){
  segs=[];let a=0,b=0;
  for(let i=0;i<ev.length;i++){
    const s=ev[i][0],e=i+1<ev.length?ev[i+1][0]:total;
    segs.push({s,e,team:ev[i][2],a,b});
    if(ev[i][2]===0)a+=e-s;else if(ev[i][2]===1)b+=e-s;
  }
}
function possAt(f){
  let lo=0,hi=segs.length-1,k=-1;
  while(lo<=hi){const m=(lo+hi)>>1;if(segs[m].s<=f){k=m;lo=m+1}else hi=m-1}
  if(k<0)return [0,0];
  const g=segs[k];let a=g.a,b=g.b;const dd=Math.max(0,Math.min(f,g.e)-g.s);
  if(g.team===0)a+=dd;else if(g.team===1)b+=dd;
  return [a,b];
}
function updatePoss(f){
  const [a,b]=possAt(f),t=a+b,pa=t?a/t*100:50,pb=t?100-pa:50,tc=trackingData.team_colors;
  $('possBarA').style.width=pa+'%';$('possBarB').style.width=pb+'%';
  $('possBarA').style.background=tc.team_a;$('possBarB').style.background=tc.team_b;
  $('possTxtA').innerText=`A ${t?pa.toFixed(0):'--'}%`;$('possTxtB').innerText=`B ${t?pb.toFixed(0):'--'}%`;
}

// ---- stats cards / side lists ----
function buildStats(){
  const s=trackingData.stats,tc=trackingData.team_colors;
  const card=(t,v,sub)=>`<div class="stat rounded-xl p-3"><div class="text-[10px] uppercase text-slate-400 tracking-wider">${t}</div><div class="text-lg font-black text-white mt-1">${v}</div><div class="text-[10px] text-slate-500 mt-0.5">${sub||''}</div></div>`;
  const ab=(x,y)=>`<span style="color:${tc.team_a}">${x}</span> / <span style="color:${tc.team_b}">${y}</span>`;
  $('statsGrid').innerHTML=
    card('Possession',ab(s.possession_a+'%',s.possession_b+'%'),'Team A / Team B')+
    card('Passes',ab(s.passes_a,s.passes_b),'completed, A / B')+
    card('Pass accuracy',ab(s.pass_acc_a+'%',s.pass_acc_b+'%'),'completed / (completed + lost)')+
    card('Ball won',ab(s.gains_a,s.gains_b),'turnovers gained')+
    card('Strikes',s.strikes,`ball > ${75} km/h (est.)`)+
    card('Ball speed',`${s.ball_max_kmh} km/h`,`max • avg ${s.ball_avg_kmh} km/h`)+
    card('Sprints',s.sprints,'> 25 km/h bursts')+
    card('Ball tracked',`${s.ball_visible_pct}%`,'of wide-view frames')+
    card('Players identified',s.reid_identities||trackingData.unique_ids.length,`${s.swaps_fixed} ID swaps fixed`)+
    card('Tactical view',`${s.wide_pct}%`,`${trackingData.shots.length} shots • ${trackingData.unique_ids.length} IDs`);
  if(s.shots_a==null)return;   // older analyses have no event stats
  const mmss=v=>{const t=Math.round(v);return `${Math.floor(t/60)}:${String(t%60).padStart(2,'0')}`},est=' (est.)';
  $('statsGrid').innerHTML+=
    card('Shots',ab(s.shots_a,s.shots_b),`on target ${s.shots_on_a} / ${s.shots_on_b}`+est)+
    card('xG',ab(s.xg_a.toFixed(2),s.xg_b.toFixed(2)),'expected goals'+est)+
    card('Saves',ab(s.saves_a,s.saves_b),'by each goalkeeper'+est)+
    card('Tackles',ab(s.tackles_a,s.tackles_b),`interceptions ${s.interceptions_a} / ${s.interceptions_b}`+est)+
    card('Defensive actions',ab(s.def_actions_a,s.def_actions_b),'tackles + interceptions + clearances')+
    card('Dispossessed',ab(s.dispossessed_a,s.dispossessed_b),'lost the ball to a tackle'+est)+
    card('Dribbles',ab(s.dribbles_a,s.dribbles_b),'beat a close defender'+est)+
    card('Crosses',ab(s.crosses_a,s.crosses_b),`completed ${s.crosses_ok_a} / ${s.crosses_ok_b}`+est)+
    card('Long balls',ab(s.long_balls_a,s.long_balls_b),`completed (30 m+), tried ${s.long_balls_att_a} / ${s.long_balls_att_b}`)+
    card('Box touches',ab(s.box_touches_a,s.box_touches_b),'in the opposition box'+est)+
    card('Corners',ab(s.corners_a,s.corners_b),`free kicks ${s.free_kicks_a} / ${s.free_kicks_b}`+est)+
    card('Final third',ab(mmss(s.final_third_s_a),mmss(s.final_third_s_b)),'ball in each side\'s attacking third')+
    card('Fastest strike',ab(s.fastest_strike_a+' km/h',s.fastest_strike_b+' km/h'),'ball speed off the foot')+
    card('Sprint speed',ab(s.sprint_kmh_a+' km/h',s.sprint_kmh_b+' km/h'),'average sprint peak');
}
function fmtDist(m){return m>=1000?`${(m/1000).toFixed(2)} km`:`${Math.round(m)} m`}
function buildDistList(){
  const td=trackingData,rows=td.unique_ids.map(id=>({id,code:td.team_info[id],d:pl(id).dist_m||0,s:pl(id).secs||0})).filter(r=>r.code!==3&&r.d>0).sort((a,b)=>b.d-a.d);
  const max=rows.length?rows[0].d:1,tot=[0,0,0];
  rows.forEach(r=>{if(r.code<3)tot[r.code]+=r.d});
  $('distTotals').innerHTML=`Tracked total: <span style="color:${tcol(0)}">A ${fmtDist(tot[0])}</span> • <span style="color:${tcol(1)}">B ${fmtDist(tot[1])}</span> • GK ${fmtDist(tot[2])}`;
  $('distList').innerHTML=rows.map(r=>`<button class="ev-row" data-id="${r.id}" title="Click to pin"><span class="w-2 h-2 rounded-full shrink-0" style="background:${tcol(r.code)}"></span><span class="text-slate-200 w-28 truncate">${nm(r.id)}</span><span class="flex-1 h-1.5 rounded-full bg-slate-800 overflow-hidden"><span class="block h-full rounded-full" style="width:${Math.max(2,r.d/max*100)}%;background:${tcol(r.code)}"></span></span><span class="text-white font-bold w-20 text-right">${fmtDist(r.d)}</span><span class="text-slate-500 w-12 text-right">${Math.round(r.s)}s</span></button>`).join('')||'<div class="text-slate-500 text-xs p-2">No distance data.</div>';
}
$('distList').onclick=e=>{const b=e.target.closest('[data-id]');if(b)togglePin(+b.dataset.id)};
function buildEventList(){
  const td=trackingData,f=$('evFilter').value,icons={pass:'➡️',won:'🛡️',strike:'💥',sprint:'💨'};
  const rows=td.events.filter(e=>f==='all'||e[1]===f).slice(0,500).map(e=>{
    const col=e[3]>=0?tcol(e[3]):'#64748b';let txt='';
    if(e[1]==='pass')txt=`${nm(e[4])} → ${nm(e[2])}`;
    else if(e[1]==='won')txt=`${nm(e[2])} wins the ball`;
    else if(e[1]==='strike')txt=`Strike ~${e[4]} km/h`;
    else txt=`${nm(e[2])} sprint ${e[4]} km/h`;
    return `<button class="ev-row" data-f="${e[0]}"><span class="text-slate-500 w-10">${fmtT(e[0])}</span><span>${icons[e[1]]||'•'}</span><span class="w-2 h-2 rounded-full" style="background:${col}"></span><span class="text-slate-200 truncate">${txt}</span></button>`;
  });
  $('eventList').innerHTML=rows.join('')||'<div class="text-slate-500 text-xs p-2">No events of this type.</div>';
}
function buildSideLists(){
  const td=trackingData;
  $('speedList').innerHTML=td.stats.top_speeds.map((r,i)=>`<button class="ev-row" data-f="${r[2]}"><span class="text-slate-500 w-5">${i+1}</span><span class="w-2 h-2 rounded-full" style="background:${tcol(r[3])}"></span><span class="text-slate-200 flex-1 truncate">${nm(r[0])}</span><span class="text-[var(--c-primary)] font-bold">${r[1]} km/h</span></button>`).join('')||'<div class="text-slate-500 text-xs p-2">No data.</div>';
  $('linkList').innerHTML=td.links.slice(0,20).map(l=>`<div class="ev-row"><span class="text-slate-200 flex-1 truncate">${nm(l[0])} → ${nm(l[1])}</span><span class="text-[var(--c-secondary)] font-bold">×${l[2]}</span></div>`).join('')||'<div class="text-slate-500 text-xs p-2">No passes detected.</div>';
}
$('eventList').onclick=e=>{const b=e.target.closest('[data-f]');if(b)seekF(+b.dataset.f-trackingData.fps*2)};
$('speedList').onclick=e=>{const b=e.target.closest('[data-f]');if(b)seekF(+b.dataset.f-trackingData.fps*2)};
$('evFilter').onchange=buildEventList;

// ---- timeline strip ----
function buildTimeline(){
  const cv=$('timeline'),td=trackingData;cv.width=cv.clientWidth||900;
  const W=cv.width,H=cv.height,T=Math.max(1,td.total_frames);
  tlStatic=document.createElement('canvas');tlStatic.width=W;tlStatic.height=H;const c=tlStatic.getContext('2d');
  c.fillStyle='#050912';c.fillRect(0,0,W,H);
  c.fillStyle='rgba(255,255,255,.08)';td.shots.forEach(s=>c.fillRect(s[0]/T*W,0,Math.max(1,(s[1]-s[0])/T*W),H));
  segs.forEach(g=>{if(g.team<0)return;c.fillStyle=g.team===0?td.team_colors.team_a:td.team_colors.team_b;c.fillRect(g.s/T*W,6,Math.max(1,(g.e-g.s)/T*W),12)});
  td.events.forEach(e=>{
    const x=e[0]/T*W;
    if(e[1]==='strike'){c.fillStyle='#fbbf24';c.fillRect(x,22,2,18)}
    else if(e[1]==='pass'){c.fillStyle=tcol(e[3]);c.fillRect(x,26,1,8)}
    else if(e[1]==='won'){c.fillStyle='#f87171';c.fillRect(x,22,1,6)}
    else if(e[1]==='sprint'){c.fillStyle='#22d3ee';c.fillRect(x,34,1,6)}
  });
}
function drawTimeline(f){
  const cv=$('timeline');if(!tlStatic)return;
  const c=cv.getContext('2d');c.drawImage(tlStatic,0,0);
  const x=f/Math.max(1,trackingData.total_frames)*cv.width;c.fillStyle='#fff';c.fillRect(x-1,0,2,cv.height);
}
$('timeline').onclick=e=>{const r=e.target.getBoundingClientRect();seekF((e.clientX-r.left)/r.width*trackingData.total_frames)};
let rsT=null;window.addEventListener('resize',()=>{clearTimeout(rsT);rsT=setTimeout(()=>{if(trackingData){buildTimeline();drawTimeline(lastF)}},250)});

// ---- roster (carrier jumps to the TOP; nothing scrolls the user around) ----
function rowFor(id){
  let r=rowEls.get(id);if(r)return r;
  const td=trackingData,code=td.team_info[id],col=tcol(code),st=pl(id);
  r=document.createElement('div');r.dataset.id=id;
  r.className='flex items-center gap-2 bg-slate-900/60 p-2 rounded-xl border border-white/5 hover:border-white/20 transition cursor-pointer';
  r.innerHTML=`<span class="px-2 py-1 rounded-lg text-xs font-mono font-black min-w-[46px] text-center" style="background:${col}33;border:1px solid ${col};color:#fff">${lab(id)}</span>
    <input type="text" value="${nameMap[id]||''}" placeholder="${lab(id)} · ${TEAM_NAMES[code]}" class="flex-1 min-w-0 bg-slate-950/80 border border-white/10 focus:border-[var(--c-primary)] rounded-lg px-2 py-1 text-xs text-white focus:outline-none transition" />
    <span class="text-[10px] font-mono text-slate-400 whitespace-nowrap" title="touches • top speed">${st.touches||0}✋ ${st.top_kmh||0}</span>
    <button class="seek text-slate-400 hover:text-white text-xs" title="Jump to first appearance">⏵</button>
    <span class="ball hidden px-1.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/60 text-xs animate-bounce">⚽</span>`;
  const inp=r.querySelector('input');
  inp.addEventListener('input',e=>{const v=e.target.value.trim();if(v)nameMap[id]=v;else delete nameMap[id];saveNames()});
  inp.addEventListener('click',e=>e.stopPropagation());
  r.querySelector('.seek').onclick=e=>{e.stopPropagation();const fs=td.first_seen[id];if(fs!==undefined)seekF(fs)};
  r.onclick=()=>togglePin(id);
  rowEls.set(id,r);return r;
}
function styleRows(){
  rowEls.forEach((r,id)=>{
    r.classList.toggle('carrier-row',id===curCarrier);
    r.classList.toggle('pinned-row',id===pinned);
    r.classList.toggle('off-row',!onScreen.has(id));
    r.querySelector('.ball').classList.toggle('hidden',id!==curCarrier);
  });
}
function updateRoster(fr){
  onScreen=new Set(fr?fr.p.map(p=>p[0]):[]);
  const td=trackingData,q=$('rosterSearch').value.trim().toLowerCase(),sort=$('rosterSort').value;
  let ids=rosterMode==='live'?(fr?fr.p.map(p=>p[0]):[]):[...td.unique_ids].sort((a,b)=>(td.track_len[b]||0)-(td.track_len[a]||0)).slice(0,300);
  ids=ids.filter(id=>td.team_info[id]!==undefined);
  if(q)ids=ids.filter(id=>String(id).includes(q)||nm(id).toLowerCase().includes(q));
  const key={team:id=>td.team_info[id]*1e7+id,id:id=>id,touches:id=>-(pl(id).touches||0),speed:id=>-(pl(id).top_kmh||0),dist:id=>-(pl(id).dist_m||0)}[sort];
  ids.sort((a,b)=>key(a)-key(b));
  const head=[];
  if(curCarrier>0&&ids.includes(curCarrier))head.push(curCarrier);
  recent.forEach(r=>{if(r!==curCarrier&&ids.includes(r)&&head.length<4)head.push(r)});
  ids=[...head,...ids.filter(id=>!head.includes(id))];
  $('playerCount').innerText=rosterMode==='live'?`${ids.length} on screen`:`${ids.length} tracked`;
  const sig=`${rosterMode}|${sort}|${q}|${curCarrier}|${recent.join(',')}|${pinned}|${ids.join(',')}`;
  if(sig===rosterSig)return;
  const ae=document.activeElement;
  if(ae&&ae.tagName==='INPUT'&&ae.closest('#rosterList'))return;
  rosterSig=sig;
  $('rosterList').replaceChildren(...ids.map(rowFor));
  styleRows();
}
function setCarrier(id){
  if(id===curCarrier)return;
  if(curCarrier>0)recent=[curCarrier,...recent.filter(r=>r!==curCarrier&&r!==id)].slice(0,3);
  curCarrier=id;
  $('ballCarrierHud').innerText=id>0?nm(id):'Free ball';
  if(id>0)$('rosterList').scrollTop=0;
}
document.querySelectorAll('.rmode').forEach(b=>b.onclick=()=>{document.querySelectorAll('.rmode').forEach(x=>x.classList.remove('active'));b.classList.add('active');rosterMode=b.dataset.m;rosterSig=''});
$('rosterSearch').oninput=()=>{rosterSig=''};$('rosterSort').onchange=()=>{rosterSig=''};

// ---- pin / spotlight ----
function togglePin(id){pinned=pinned===id?null:id;rosterSig='';updatePinCard();renderAt(lastF)}
function updatePinCard(){
  const c=$('pinCard');
  if(pinned===null){c.classList.add('hidden');return}
  const st=pl(pinned),code=trackingData.team_info[pinned],info=trackingData.id_info&&trackingData.id_info[pinned];
  c.classList.remove('hidden');
  $('pinName').innerHTML=`<span style="color:${tcol(code)}">●</span> ${nm(pinned)} <span class="text-slate-500 font-normal">${TEAM_NAMES[code]}</span>`;
  const cell=(l,v)=>`<div><div class="text-slate-500">${l}</div><div class="text-white font-bold">${v}</div></div>`;
  $('pinStats').innerHTML=cell('Touches',st.touches||0)+cell('On ball',(st.on_ball_s||0)+'s')+cell('Tracked',(st.secs||0)+'s')+cell('Distance',(st.dist_m||0)+' m')+cell('Top speed',(st.top_kmh||0)+' km/h')+cell('Sprints',st.sprints||0)+(info?cell('ID confidence',info.conf+'%')+cell('Matched in',info.tracklets+' shots'):'');
}
$('pinClear').onclick=()=>{if(pinned!==null)togglePin(pinned)};

// ---- label filter chips ----
document.querySelectorAll('#lblChips button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#lblChips button').forEach(x=>x.classList.remove('active'));b.classList.add('active');lblFilter=b.dataset.f;renderAt(lastF)});
['optLabels','optCarrierOnly','optDim'].forEach(i=>$(i).onchange=()=>renderAt(lastF));

// ---- playback helpers ----
$('stepBack').onclick=()=>{const p=$('player');p.pause();p.currentTime=Math.max(0,p.currentTime-1/trackingData.fps)};
$('stepFwd').onclick=()=>{const p=$('player');p.pause();p.currentTime+=1/trackingData.fps};
$('speedSel').onchange=e=>{$('player').playbackRate=+e.target.value};
document.addEventListener('keydown',e=>{
  if(!trackingData||['INPUT','SELECT','TEXTAREA'].includes((e.target.tagName||'')))return;
  const p=$('player'),fps=trackingData.fps;
  if(e.code==='Space'){e.preventDefault();p.paused?p.play():p.pause()}
  else if(e.code==='ArrowRight'){p.pause();p.currentTime+=e.shiftKey?1:1/fps}
  else if(e.code==='ArrowLeft'){p.pause();p.currentTime=Math.max(0,p.currentTime-(e.shiftKey?1:1/fps))}
  else if(e.key==='l'||e.key==='L'){$('optLabels').checked=!$('optLabels').checked;renderAt(lastF)}
  else if(e.key==='p'||e.key==='P'){if(pinned!==null)togglePin(pinned)}
  else if(e.key==='m'||e.key==='M'){p.muted=!p.muted}
});
$('snapBtn').onclick=()=>{
  const p=$('player'),c=document.createElement('canvas');c.width=p.videoWidth;c.height=p.videoHeight;
  const x=c.getContext('2d');x.drawImage(p,0,0);x.drawImage($('overlayCanvas'),0,0);
  c.toBlob(b=>{const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=`snapshot_f${lastF}.png`;a.click()});
};
$('csvBtn').onclick=()=>{
  const td=trackingData,rows=[['id','name','team','seconds_tracked','touches','on_ball_s','distance_m_est','top_speed_kmh_est','sprints']];
  td.unique_ids.forEach(id=>{const s=pl(id);rows.push([lab(id),(nameMap[id]||'').replace(/,/g,' '),TEAM_NAMES[td.team_info[id]],s.secs,s.touches,s.on_ball_s,s.dist_m,s.top_kmh,s.sprints])});
  const b=new Blob([rows.map(r=>r.join(',')).join(String.fromCharCode(10))],{type:'text/csv'});
  const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='tactical_players.csv';a.click();
};

// ---------------- OVERLAY RENDER ---------------- //
function startLoop(){
  if(loopOn)return;loopOn=true;const p=$('player');
  const at=f=>renderAt(f);
  if('requestVideoFrameCallback' in p){
    const cb=(now,meta)=>{if(trackingData)at(Math.round(meta.mediaTime*trackingData.fps));p.requestVideoFrameCallback(cb)};
    p.requestVideoFrameCallback(cb);
  }else{const tick=()=>{if(trackingData)at(Math.round(p.currentTime*trackingData.fps));requestAnimationFrame(tick)};tick()}
  ['seeked','pause'].forEach(ev=>p.addEventListener(ev,()=>{if(trackingData)at(Math.round(p.currentTime*trackingData.fps))}));
}
function drawGraphics(ctx,f,fr,cv){
  const td=trackingData,lw=Math.max(2,cv.height/450);
  for(const [id,x1,y1,x2,y2,code] of fr.p){
    const cx=(x1+x2)/2,ax=Math.max(8,(x2-x1)*.62),ay=Math.max(3,ax*.32),car=id===fr.c;
    ctx.beginPath();ctx.ellipse(cx,y2,ax,ay,0,-.5,3.67);ctx.strokeStyle='#000';ctx.lineWidth=lw+2;ctx.stroke();
    ctx.strokeStyle=tcol(code);ctx.lineWidth=lw;ctx.stroke();
    if(car){
      ctx.beginPath();ctx.ellipse(cx,y2,ax*1.3,ay*1.35,0,0,6.29);ctx.strokeStyle='#ffd700';ctx.lineWidth=lw+1;ctx.stroke();
      ctx.fillStyle='#ffd700';ctx.beginPath();ctx.moveTo(cx-8,y1-20);ctx.lineTo(cx+8,y1-20);ctx.lineTo(cx,y1-6);ctx.fill();
    }
  }
  if(fr.b){ctx.beginPath();ctx.arc(fr.b[0],fr.b[1],7,0,6.29);ctx.fillStyle=fr.b[2]?'#aaa':'#fff';ctx.fill();ctx.lineWidth=2;ctx.strokeStyle='#000';ctx.stroke()}
}
function drawPinned(ctx,f,fr,cv){
  if(pinned===null)return;
  const pts=[];
  for(let k=Math.max(0,f-60);k<=f;k+=2){const q=trackingData.frames[k];if(!q)continue;const p=q.p.find(r=>r[0]===pinned);if(p)pts.push([(p[1]+p[3])/2,p[4]])}
  const col=getComputedStyle(document.documentElement).getPropertyValue('--c-primary').trim()||'#00ff87';
  if(pts.length>1){ctx.beginPath();pts.forEach((q,i)=>i?ctx.lineTo(q[0],q[1]):ctx.moveTo(q[0],q[1]));ctx.strokeStyle=col;ctx.lineWidth=Math.max(2,cv.height/400);ctx.globalAlpha=.8;ctx.stroke();ctx.globalAlpha=1}
  const p=fr.p.find(r=>r[0]===pinned);
  if(p){ctx.shadowColor=col;ctx.shadowBlur=18;ctx.strokeStyle=col;ctx.lineWidth=Math.max(2,cv.height/360);ctx.strokeRect(p[1]-4,p[2]-4,p[3]-p[1]+8,p[4]-p[2]+8);ctx.shadowBlur=0}
}
function drawLabels(ctx,fr,cv){
  if(!$('optLabels').checked)return;
  const only=$('optCarrierOnly').checked,dim=$('optDim').checked&&pinned!==null,fs=Math.max(11,Math.round(cv.height/72));
  ctx.font=`bold ${fs}px monospace`;ctx.textBaseline='middle';
  for(const [id,x1,y1,x2,y2,code] of fr.p){
    if(trackingData.team_info[id]===undefined)continue;
    if(only&&id!==fr.c&&id!==pinned)continue;
    if(lblFilter!=='all'&&String(code)!==lblFilter&&id!==pinned&&id!==fr.c)continue;
    const label=(id===fr.c?'⚽ ':'')+nm(id),w=ctx.measureText(label).width+10,h=fs+8;
    const base=id===fr.c?'#f59e0b':tcol(code),ty=Math.max(0,y1-h-6);
    ctx.globalAlpha=(dim&&id!==pinned&&id!==fr.c)?.35:1;
    ctx.fillStyle=base;ctx.fillRect(x1,ty,w,h);
    const rgb=parseInt(base.slice(1),16),lum=((rgb>>16)&255)*.299+((rgb>>8)&255)*.587+(rgb&255)*.114;
    ctx.fillStyle=lum>150?'#000':'#fff';ctx.fillText(label,x1+5,ty+h/2);
  }
  ctx.globalAlpha=1;
}
function renderAt(f){
  lastF=f;
  const cv=$('overlayCanvas'),ctx=cv.getContext('2d');
  ctx.clearRect(0,0,cv.width,cv.height);
  updatePoss(f);drawTimeline(f);
  const fr=trackingData.frames[f];
  if(!fr){setCarrier(-1);updateRoster(null);styleRows();return}
  setCarrier(fr.c);updateRoster(fr);styleRows();
  if(!trackingData.burned)drawGraphics(ctx,f,fr,cv);
  drawPinned(ctx,f,fr,cv);
  drawLabels(ctx,fr,cv);
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def get_dashboard():
    return HTML

# ----------------------------------------------------------------------------
# TRIVELA APP (landing page, dashboard and analysis app) served from this same server
# ----------------------------------------------------------------------------
TRIVELA_FILE = Path(__file__).resolve().with_name("trivela.html")
# Point the app's saved model-server URL at whatever address served the page (Cloudflare tunnel or
# Colab proxy), so it is connected to this engine without pasting anything.
TRIVELA_HOOK = "<script>try{localStorage.setItem('trivela.engineUrl',location.origin)}catch(_){}</script>"

@app.get("/trivela", response_class=HTMLResponse)
def get_trivela():
    if not TRIVELA_FILE.exists():
        raise HTTPException(status_code=404, detail="trivela.html is missing from the app folder.")
    return TRIVELA_FILE.read_text(encoding="utf-8").replace("<body>", "<body>" + TRIVELA_HOOK, 1)


# ----------------------------------------------------------------------------
# Chat assistant: an OpenAI-compatible endpoint (AMD Token Factory). Configured with environment
# variables (see .env.example). The key is only ever sent to the endpoint, never to the browser.
# ----------------------------------------------------------------------------
import urllib.request as _urlreq
import urllib.error as _urlerr
from fastapi.responses import FileResponse as _FileResponse

APP_DIR = Path(__file__).resolve().parent
CHAT_BASE = os.environ.get("AMD_API_BASE", "").strip().rstrip("/")
CHAT_KEY = os.environ.get("AMD_API_KEY", "").strip()
CHAT_MODEL = os.environ.get("AMD_CHAT_MODEL", "").strip()
_chat_model_state = {"id": CHAT_MODEL or None}

CHAT_SYSTEM = (
    "You are the Trivela assistant, helping users with football tactical analysis and with the Trivela app. "
    "Be concise and practical. When analysis data is provided, answer from it and quote the numbers exactly. "
    "Say that stats marked as estimates are approximate, because positions come from the camera image without "
    "pitch calibration. If the data does not contain the answer, say so instead of guessing."
)

def _chat_configured() -> bool:
    return bool(CHAT_BASE and CHAT_KEY)

def _amd_call(path: str, payload=None, timeout: int = 120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = _urlreq.Request(CHAT_BASE + path, data=data,
                          method="POST" if payload is not None else "GET",
                          headers={"Authorization": "Bearer " + CHAT_KEY, "Content-Type": "application/json"})
    with _urlreq.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _chat_model_id() -> str:
    if _chat_model_state["id"]:
        return _chat_model_state["id"]
    ids = [m.get("id") for m in _amd_call("/models").get("data", []) if m.get("id")]
    if not ids:
        raise RuntimeError("the endpoint lists no models")
    _chat_model_state["id"] = ids[0]
    print("AMD_CHAT_MODEL is not set, using the first listed model:", ids[0], flush=True)
    print("Models the endpoint offers:", ", ".join(ids), flush=True)
    return ids[0]

def _analysis_context(max_chars: int = 6000):
    path = PROCESSED_DIR / "summary.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    keep = {k: data[k] for k in ("stats", "team_info", "tracker", "total_frames", "fps") if k in data}
    return json.dumps(keep, default=str)[:max_chars]

@app.get("/chat.js")
def chat_script():
    return _FileResponse(APP_DIR / "chat.js", media_type="application/javascript")

@app.get("/api/chat/status")
def chat_status():
    return {"configured": _chat_configured(), "model": _chat_model_state["id"],
            "has_analysis": (PROCESSED_DIR / "summary.json").exists()}

@app.post("/api/chat")
def chat(body: dict):
    if not _chat_configured():
        raise HTTPException(status_code=503, detail="The assistant is not set up. Add AMD_API_BASE and AMD_API_KEY to .env and restart.")
    clean = []
    for m in (body.get("messages") or [])[-20:]:
        role, content = m.get("role"), str(m.get("content", ""))[:4000]
        if role in ("user", "assistant") and content.strip():
            clean.append({"role": role, "content": content})
    if not clean or clean[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="Send a message first.")
    system, ctx = CHAT_SYSTEM, None
    if body.get("use_analysis"):
        ctx = _analysis_context()
        if ctx:
            system += "\n\nAnalysis data for the user's video (JSON):\n" + ctx
    try:
        model = _chat_model_id()
        resp = _amd_call("/chat/completions", {"model": model, "temperature": 0.3,
                         "messages": [{"role": "system", "content": system}] + clean})
        reply = resp["choices"][0]["message"]["content"]
    except _urlerr.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"The model endpoint returned an error ({e.code}).")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not get an answer from the model endpoint ({type(e).__name__}).")
    return {"reply": reply, "model": model, "used_analysis": bool(ctx)}
