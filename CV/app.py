import os, re, cv2, time, json, math, gzip, shutil, inspect, threading, queue, subprocess, asyncio, urllib.request, urllib.error
import numpy as np
import torch
import yaml
from collections import deque, defaultdict, Counter
from pathlib import Path
from typing import Dict, Any
from fastapi import FastAPI, Request, Form, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from ultralytics import YOLO
from ultralytics.utils import IterableSimpleNamespace

from urllib.parse import urlsplit

# API docs are off: the engine is public behind a tunnel and nothing uses them
app = FastAPI(title="Tactical Vision AI", docs_url=None, redoc_url=None, openapi_url=None)

# Sites allowed to call the engine from a browser: its own pages, localhost, and TRIVELA_ALLOWED_ORIGINS
# (comma-separated, e.g. https://you.github.io for a hosted Trivela site; "*" allows any site).
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("TRIVELA_ALLOWED_ORIGINS", "").split(",") if o.strip()]
ANY_ORIGIN = "*" in ALLOWED_ORIGINS
LOCAL_ORIGIN = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
app.add_middleware(CORSMiddleware, allow_origins=["*"] if ANY_ORIGIN else ALLOWED_ORIGINS,
                   allow_origin_regex=None if ANY_ORIGIN else LOCAL_ORIGIN, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["*"])

def origin_allowed(origin: str, host: str) -> bool:
    if ANY_ORIGIN or origin.rstrip("/") in ALLOWED_ORIGINS or re.match(LOCAL_ORIGIN, origin): return True
    return urlsplit(origin).netloc == host   # same site (the HUD, /trivela, /web, also through a tunnel)

@app.middleware("http")
async def guard_requests(request: Request, call_next):
    # CORS only hides responses; this stops other websites from starting, cancelling or overwriting uploads
    origin = request.headers.get("origin")
    if request.method in ("POST", "PUT", "DELETE") and origin and not origin_allowed(origin, request.headers.get("host", "")):
        return JSONResponse({"error": "This site is not allowed to use the engine."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response

BASE_DIR = Path(os.environ.get("TRIVELA_WORKSPACE", "./tactical_workspace"))   # a Modal volume when deployed by modal_app.py
UPLOAD_DIR = BASE_DIR / "uploads"
PARTS_DIR = UPLOAD_DIR / "parts"
PROCESSED_DIR = BASE_DIR / "processed"
for d in [UPLOAD_DIR, PROCESSED_DIR]:
    d.mkdir(parents=True, exist_ok=True)

MODEL_PATH = "yolo-football-player-detection.pt"
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
BALL_ZOOM = os.environ.get("TRIVELA_BALL_ZOOM", "1") != "0"   # second look at 2x zoom in frames without the ball
DET_CONF = 0.10
PERSON_MIN_CONF = 0.12   # people boxes passed to the tracker (it starts tracks only from new_track_thresh up)
SC = 0.5            # scale of the grass-mask working image
WIDE_MIN = 0.12     # minimum grass ratio to treat a shot as a wide tactical view
MIN_TRACK_LEN = 25  # frames a track must live to be listed as a real player
CAM_SC = 0.25        # scale used for camera-motion estimation
PLAYER_H_M = 1.78    # average player height (metres) used as a pixel->metre ruler
SPRINT_MS = 7.0      # m/s (25.2 km/h): a sprint, measured on the speed over the last second
SPRINT_END_MS = 6.0  # ...which ends once that speed drops below this
SPRINT_MIN_S = 0.5   # a sprint counts once that 1-second speed has stayed above SPRINT_MS this long
MAX_RUN_MS = 10.4    # 37.4 km/h, about the fastest a footballer runs: faster readings are tracking glitches
SPEED_WIN_S = 1.0    # speeds (top speed, sprints) are averaged over this window: single frames jitter
STRIKE_KMH = 65.0    # smoothed ball speed that counts as a strike (a shot, cross, long ball or clearance)
STRIKE_REARM_KMH = 45.0  # after a strike the ball must slow below this, or turn by 60+ degrees, before the next one counts
BALL_DEPTH_POW = 0.5 # a ball in flight rises: its vertical image motion is part height, part depth (see GroundModel)

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

def drop_part_boxes(xyxy, conf, keep):
    """The detector often adds a second, smaller box on part of a player (his upper body, or legs). Such a box lies
    almost entirely inside a stronger one and ends at about the same feet row or head row: keep only the stronger."""
    order = sorted(keep, key=lambda i: -float(conf[i])); out = []
    for i in order:
        a = xyxy[i]; aa = max(1.0, (a[2] - a[0]) * (a[3] - a[1])); h = max(1.0, a[3] - a[1])
        dup = False
        for j in out:
            b = xyxy[j]
            ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
            if ix * iy / aa > 0.85 and (abs(a[3] - b[3]) < 0.2 * h or abs(a[1] - b[1]) < 0.2 * h): dup = True; break
        if not dup: out.append(i)
    return sorted(out)

def fuse_people(cur, prev, skip_cls):
    """People boxes of this frame merged with the previous frame's (both detected: the ball needs every frame). A
    detector's confidence flickers frame to frame (a keeper against the net: 0.44, 0.78, 0.24, 0.76...): a box that
    is weak now but was strong a frame ago keeps the stronger score, and a person missed now is taken from then"""
    if prev is None or not len(prev): return cur
    d = np.array(cur.data, np.float32, copy=True); pd = np.asarray(prev.data, np.float32); add = []
    for r in pd:
        if int(r[5]) in skip_cls or r[4] < 0.3: continue
        best, j = 0.0, -1
        for i, q in enumerate(d):
            if int(q[5]) in skip_cls: continue
            v = box_iou(r[:4], q[:4])
            if v > best: best, j = v, i
        if best >= 0.5: d[j, 4] = max(d[j, 4], r[4])
        elif best < 0.2: add.append(r)
    if add: d = np.concatenate([d, np.array(add, np.float32)])
    return type(cur)(d, cur.orig_shape)

def ball_over_pitch(gm, x, y, H):
    """True when there is turf at or just below the ball in the image: on the pitch or in the air above it. Off it
    (stands, boards, benches, the scoreboard) the detector's 'balls' are heads, lamps and logos"""
    xs, ys = int(x * SC), int(y * SC)
    if not (0 <= xs < gm.shape[1] and 0 <= ys < gm.shape[0]): return False
    col = gm[ys:min(gm.shape[0], ys + int(0.12 * H * SC) + 2), max(0, xs - 3):xs + 4]
    return col.size > 0 and np.count_nonzero(col) / col.size >= 0.25

def grass_top(gm):
    # first image row that is mostly turf: roughly the far touchline in a wide shot
    rows = np.flatnonzero(gm.mean(axis=1) > 0.45 * 255)
    return int(rows[0] / SC) if rows.size else 0

# ----------------------------------------------------------------------------
# TEAM CLASSIFICATION (K-means kit clustering + per-track temporal voting)
# ----------------------------------------------------------------------------
LABW = np.array([0.7, 1.3, 1.3], np.float32)
# A shirt sample this far from both kits (as a share of the distance between the two kits) is "off-kit". Single frames
# are noisy (shadows, shirt numbers, blur), so a track is only nobody's team when most of its life is off-kit.
# Measured on a Serie A broadcast: players are off-kit in at most 35% of their samples, the referee in 97%;
# in a track's first 20 samples a player can briefly reach 60%, so nothing is decided before that
KIT_OUTLIER = 0.45      # 0.3 on a Serie A broadcast turned Roma's red (shadowed, blurred) "off-kit": keepers everywhere
OFF_KIT_SHARE = 0.7
OFF_KIT_MIN = 20
# ...except a track that is off-kit almost every time from the start: a keeper half-hidden by the post or a crowd
# often only lives a few dozen frames per piece, never reaching OFF_KIT_MIN samples
OFF_KIT_EARLY, OFF_KIT_EARLY_SHARE = 8, 0.9
ROLE_SHARE = 0.25       # share of a track's detections the detector must call "referee"/"goalkeeper" to make it stick
ROLE_MIN = 10           # detections seen before that share is trusted
KIT_DECAY = 0.85        # older kit votes fade, so a track that slid onto another player follows his shirt
KIT_EVERY = 2           # detections between kit samples once a track has its first ones: quick to notice a slid track
KIT_SETTLED = 3.0       # decayed vote weight that makes a team "settled" for a track
REF_KIT_SHARE = 0.4     # share of a track's shirt samples nearest the officials' kit that makes it an official
REF_SAMPLE_CONF = 0.5   # detector "referee" boxes at least this sure teach the engine the officials' kit...
REF_MIN_SAMPLES = 12    # ...once there are this many (a few stray "referee" boxes are ball boys, stewards, keepers)

class TeamModel:
    def __init__(self, grass):
        self.grass = grass
        self.samples = []
        self.centroids = None; self.sep = 1.0
        self.votes = defaultdict(Counter)       # decayed kit votes per track: team_a / team_b / other
        self.nvotes = Counter()                 # kit samples taken per track
        self.noff = Counter()                   # ...of which off-kit
        self.roles = defaultdict(Counter)       # detector class votes per track: ref / gk / player
        self.settled = {}                       # track -> team its shirt settled on
        self.flipped = False                    # the last label_for() saw a settled track change team
        self.ref_samples = []; self.ref_c = None # the officials' shirt colour, learned from the detector's referee boxes
        self.fsum = {}                          # sum of shirt samples per track (debug)
        self.nref = Counter()                   # shirt samples per track nearest the officials' colour
        self.recent = defaultdict(lambda: deque(maxlen=8))   # the last shirt samples per track: team_a / team_b / "off" / "ref"
        self.rrole = defaultdict(lambda: deque(maxlen=8))    # the detector's class for the same calls: player / gk / ref
        self.calls = Counter()                  # label_for calls per track (one per detection of it)

    @property
    def ready(self): return self.centroids is not None

    def seed(self, home, away, ref):
        """Shirt colours from the user's clicks on one player of each team and the referee. Each team's colour is the
        median of the sampled shirts nearest its click (so one shadowed frame doesn't decide it), Team A stays the darker
        kit; returns "a"/"b" for the home team, or None without both clicks"""
        out = None
        if home is not None and away is not None:
            c = np.array([home, away], np.float32)
            if len(self.samples) >= 20:
                X = np.array(self.samples, np.float32)
                d = np.linalg.norm((X[:, None, :] - c[None, :, :]) * LABW, axis=2)
                lim = 0.5 * float(np.linalg.norm((c[0] - c[1]) * LABW))
                for k in (0, 1):
                    near = X[(np.argmin(d, axis=1) == k) & (d[:, k] <= lim)]
                    if len(near) >= 8: c[k] = np.median(near, axis=0)
            order = np.argsort(c[:, 0])
            self.centroids = c[order]
            self.sep = max(1.0, float(np.linalg.norm((self.centroids[0] - self.centroids[1]) * LABW)))
            out = "a" if order[0] == 0 else "b"
        if ref is not None:
            self.ref_c = np.asarray(ref, np.float32); self.ref_locked = True
        return out

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
        self.sep = max(1.0, float(np.linalg.norm((self.centroids[0] - self.centroids[1]) * LABW)))

    def kit_bgr(self):
        out = dict(DEFAULT_BGR)
        if self.ready:
            bgr = cv2.cvtColor(self.centroids.reshape(-1, 1, 3).clip(0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR).reshape(-1, 3)
            out["team_a"] = tuple(int(v) for v in bgr[0]); out["team_b"] = tuple(int(v) for v in bgr[1])
        return out

    def nearest(self, f):
        """(nearest team, off-kit?) for one shirt sample"""
        d = np.linalg.norm((self.centroids - f) * LABW, axis=1)
        return ("team_a" if np.argmin(d) == 0 else "team_b"), bool(d.min() > KIT_OUTLIER * self.sep)

    def add_ref_sample(self, frame, box):
        """A confident detector 'referee' box: its shirt teaches the officials' colour (refit while it is still young)"""
        if len(self.ref_samples) >= 400 or getattr(self, "ref_locked", False): return
        f = self.feature(frame, np.asarray(box).astype(int))
        if f is None: return
        self.ref_samples.append(f)
        n = len(self.ref_samples)
        if n >= REF_MIN_SAMPLES and (n < 64 or n % 32 == 0):
            self.ref_c = np.median(np.array(self.ref_samples, np.float32), axis=0)

    def refish(self, f):
        """The sample is nearer the officials' colour than both kits (and clearly off both kits)"""
        if self.ref_c is None: return False
        dr = float(np.linalg.norm((self.ref_c - f) * LABW))
        dt = float(np.min(np.linalg.norm((self.centroids - f) * LABW, axis=1)))
        # a bright referee shirt can sit fairly close to a white kit: then being much nearer the officials' colour is enough
        return dr < 0.75 * dt and (dt > 0.4 * KIT_OUTLIER * self.sep or dr < 0.45 * dt)

    def quick(self, frame, box):
        if not self.ready: return None
        f = self.feature(frame, np.asarray(box).astype(int))
        return None if f is None else self.nearest(f)[0]

    def label_for(self, cid, frame, box, idx, role=None):
        """Team, goalkeeper or official for a track. Roles are decided over the track's whole life, not per frame: the
        detector calls a referee "player" most of the time, and a player "referee" now and then. The team follows the
        recent shirt colour; `flipped` reports a track whose shirt settled on the other team (it slid onto someone else)"""
        c = self.votes[cid]; r = self.roles[cid]; self.flipped = False
        r[role or "player"] += 1; self.calls[cid] += 1
        # counted per track, not on the frame number: with people detected every 2nd frame, (frame + id) % 4 never hit
        # half of the ids, whose shirts then stopped being sampled after their first 20
        if self.nvotes[cid] < OFF_KIT_MIN or self.calls[cid] % KIT_EVERY == 0:
            f = self.feature(frame, box)
            if f is not None:
                if self.ready:
                    near, off = self.nearest(f)
                    for k in c: c[k] *= KIT_DECAY
                    rf = self.refish(f)
                    c[near] += 1; self.nvotes[cid] += 1; self.noff[cid] += off; self.nref[cid] += rf
                    self.fsum[cid] = self.fsum.get(cid, 0.0) + f
                    self.recent[cid].append("ref" if rf else "off" if off else near)
                    self.rrole[cid].append(role or "player")
                else:
                    self.samples.append(f)
                    if len(self.samples) >= 150: self.fit()
        n = sum(r.values()); nv = self.nvotes[cid]; rec = self.recent[cid]
        if len(rec) >= OFF_KIT_EARLY:
            # the latest shirt samples first: a track that slid from a player onto a keeper (or a referee) behind him
            # wears another shirt from then on, while its long history still says "player"
            n_off = sum(1 for x in rec if x in ("off", "ref")); n_ref = sum(1 for x in rec if x == "ref")
            # mid-track, a team player only becomes a keeper/official when the detector agrees about the new person too:
            # a striped or shadowed shirt reads "off-kit" for a while without anybody having changed
            det = self.rrole[cid]; det_ok = sum(1 for x in det if x != "player") >= 0.5 * max(1, len(det))
            mid = self.settled.get(cid) in ("team_a", "team_b")
            if len(rec) == rec.maxlen and n_off >= 0.875 * len(rec) and (det_ok or not mid):
                role = "official" if (n_ref >= 0.5 * len(rec) or (r["ref"] > r["gk"] and r["ref"] >= 0.1 * n)) else "goalkeeper"
                if self.settled.get(cid) in ("team_a", "team_b"): self.flipped = True
                self.settled[cid] = role
                return role
            if n_off <= 0.25 * len(rec) and self.settled.get(cid) in ("goalkeeper", "official") and not det_ok:
                self.flipped = True; self.settled.pop(cid, None)          # slid from a keeper onto a player
                nv = 0                                                     # judge the new player on his own shirt
        if nv >= OFF_KIT_MIN or (nv >= OFF_KIT_EARLY and self.noff[cid] >= OFF_KIT_EARLY_SHARE * nv):
            if self.nref[cid] >= REF_KIT_SHARE * nv: return "official"            # wears the officials' colours
            if self.noff[cid] >= OFF_KIT_SHARE * nv:                             # wears neither kit
                if r["gk"] > r["ref"]: return "goalkeeper"
                if r["ref"] >= 0.1 * n: return "official"               # the detector calls him referee now and then
                # the detector often calls a keeper "player"; once the officials' colour is known, an off-kit shirt
                # that isn't theirs is a keeper's (before that, the old rule: the detector's vote decides)
                return "goalkeeper" if self.ref_c is not None or r["ref"] == 0 else "official"
        if n >= ROLE_MIN:
            if r["gk"] >= ROLE_SHARE * n: return "goalkeeper"
            # a player the detector now and then calls "referee" keeps his team when his shirt is clearly a kit
            if r["ref"] >= ROLE_SHARE * n and (nv < OFF_KIT_MIN or self.noff[cid] >= 0.3 * nv or self.nref[cid] >= 0.25 * nv):
                return "official"
        elif role:                                                # too new to judge: trust this detection
            return "official" if role == "ref" else "goalkeeper"
        team = max(("team_a", "team_b"), key=lambda t: c[t])
        if c[team] >= KIT_SETTLED:
            prev = self.settled.get(cid); self.settled[cid] = team
            if prev in ("team_a", "team_b") and prev != team:
                if sum(1 for x in rec if x == team) >= 0.75 * rec.maxlen:
                    self.flipped = True
                else:
                    team = prev; self.settled[cid] = prev          # not sure yet: he keeps his team (and his number)
        return team if c[team] else "team_a"

    def hand_over(self, old, new, team):
        # a split-off track starts with the shirt it has now, not the votes of the player it left
        self.votes[new] = Counter({team: self.votes[old][team]})
        rec = self.recent[old]; self.recent[new] = deque(rec, maxlen=8); self.rrole[new] = deque(self.rrole[old], maxlen=8)
        if team in ("goalkeeper", "official") or self.settled.get(old) is None:
            # a role split, or back to a player: count only the recent shirts, not the other person's history
            self.nvotes[new] = len(rec); self.noff[new] = sum(1 for x in rec if x in ("off", "ref"))
            self.nref[new] = sum(1 for x in rec if x == "ref")
        else:
            self.nvotes[new] = self.nvotes[old]; self.noff[new] = self.noff[old]; self.nref[new] = self.nref[old]
        self.settled[new] = team

# ----------------------------------------------------------------------------
# TRACKING HELPERS
# ----------------------------------------------------------------------------
TRACKER_YAML = BASE_DIR / "botsort_football.yaml"
# Thresholds measured on broadcast footage: a keeper in front of the net or a half-hidden player in a crowded box is
# often detected at 0.30-0.45, so a new track must start below that; weaker boxes (0.12+) only extend existing tracks
TRACKER_YAML.write_text("""tracker_type: botsort
track_high_thresh: 0.25
track_low_thresh: 0.12
new_track_thresh: 0.22
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

def build_tracker(cls, cfg, fr):
    # Older Ultralytics: Tracker(args, frame_rate) scales track_buffer by frame_rate/30 itself.
    # Newer (8.4+): Tracker(args) reads track_buffer as frames, so scale it here to keep the same lost-track time.
    if "frame_rate" in inspect.signature(cls.__init__).parameters:
        return cls(IterableSimpleNamespace(**cfg), frame_rate=fr)
    return cls(IterableSimpleNamespace(**{**cfg, "track_buffer": max(1, int(fr / 30 * cfg["track_buffer"]))}))

def make_tracker(fps):
    cfg = yaml.safe_load(TRACKER_YAML.read_text())
    fr = max(1, int(round(fps)))
    try:
        from ultralytics.trackers.bot_sort import BOTSORT
        return build_tracker(BOTSORT, cfg, fr), "BoT-SORT + camera-motion compensation"
    except Exception as e:
        print("BoT-SORT unavailable:", e)
    try:
        from ultralytics.trackers.byte_tracker import BYTETracker
        return build_tracker(BYTETracker, cfg, fr), "ByteTrack"
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

class GroundModel:
    """The pitch's perspective, measured from the players: their height in pixels grows linearly with the image row of
    their feet (h = a + b*row, reaching zero at the horizon; on a Serie A broadcast this puts the camera ~20 m high).
    Sideways, one pixel is 1.78/h metres. Depth is foreshortened on top of that by k = f*b/h, 2-4x on broadcast
    footage, so a run towards the camera measured with the sideways ruler alone reads 2-4x too slow. The focal length
    f follows from a typical camera distance (CAM_DIST_M) to the middle of the picture."""
    def __init__(self, H): self.H = H; self.reset()
    def reset(self): self.a = None; self.b = 0.0
    def update(self, boxes):
        if len(boxes) < 5: return
        y = np.array([b[3] for b in boxes], np.float32); h = np.array([b[3] - b[1] for b in boxes], np.float32)
        if y.std() < 0.04 * self.H: return              # everyone on about one row: no perspective to read
        b, a = np.polyfit(y, h, 1)
        b = float(min(0.4, max(0.0, b)))
        a = float(np.median(h - b * y))                  # robust intercept for that slope
        if self.a is None: self.a, self.b = a, b
        else: self.a, self.b = 0.8 * self.a + 0.2 * a, 0.8 * self.b + 0.2 * b
    def height(self, y):
        return None if self.a is None else max(6.0, self.a + self.b * y)
    def depth_factor(self, y):
        """k: metres per vertical pixel / metres per sideways pixel, at image row y (1 when unknown)"""
        h = self.height(y)
        if h is None or self.b <= 0.0: return 1.0
        f = self.height(self.H / 2.0) * CAM_DIST_M / PLAYER_H_M
        return float(min(6.0, max(1.0, f * self.b / h)))
    def metres(self, dx, dy, y, h_px, depth_pow=1.0):
        s = PLAYER_H_M / max(1.0, h_px)
        return float(math.hypot(dx * s, dy * s * self.depth_factor(y) ** depth_pow))

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

SPLIT_BASE = 5_000_000   # identities split off a track that changed player; below the re-identified 10_000_000+ range

# ----------------------------------------------------------------------------
# APPEARANCE RE-ID: a person re-identification network (Ultralytics yolo26s-reid) turns each player crop into a
# vector; the same player gives similar vectors even when his kit matches his teammates' (build, hair, skin, boots).
# Used to re-link lost tracks, to check swaps and to join tracklets at the end. Optional: without the weights the
# engine falls back to colour histograms only.
# ----------------------------------------------------------------------------
REID_MODEL = os.environ.get("TRIVELA_REID_MODEL", "yolo26s-reid.onnx")
EMB_EVERY = 3            # every 3rd detection frame, every track gets a new embedding (new tracks: always)
EMB_GATE = 0.45          # cosine distance above which two crops are never the same player
EMB_MIN_H = 40           # px: smaller crops carry too little detail to embed

class Embedder:
    def __init__(self):
        self.ok = False; self.n = 0
        if not os.path.isfile(REID_MODEL): return
        try:
            try:                                    # use the CUDA / cuDNN libraries that came with torch
                import onnxruntime as ort
                if hasattr(ort, "preload_dlls"): ort.preload_dlls()
                print("re-id providers:", ort.get_available_providers())
            except Exception as e:
                print("onnxruntime preload:", e)
            from ultralytics.trackers.utils.reid import ReID
            self.m = ReID(REID_MODEL, fp16=HAS_GPU); self.ok = True
        except Exception as e:
            print("re-id model not loaded:", e)

    def __call__(self, frame, boxes):
        """L2-normalised embedding per xyxy box (None for boxes too small or failed)"""
        out = [None] * len(boxes)
        if not self.ok or not len(boxes): return out
        keep = [i for i, b in enumerate(boxes) if b[3] - b[1] >= EMB_MIN_H]
        if not keep: return out
        b = np.array([boxes[i] for i in keep], np.float32)
        xywh = np.c_[(b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2, b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]]
        try:
            feats = self.m(frame, xywh)
        except Exception as e:
            print("re-id failed, turned off:", e); self.ok = False; return out
        for i, f in zip(keep, feats):
            if f is None: continue
            f = np.asarray(f, np.float32).ravel(); nrm = float(np.linalg.norm(f))
            if nrm > 1e-9: out[i] = f / nrm; self.n += 1
        return out

def emb_dist(a, b):
    return None if a is None or b is None else float(1.0 - np.dot(a, b))

class IdentityManager:
    # Globally unique IDs. Tracker IDs restart after every reset, so a base offset keeps identities from colliding.
    # Lost tracks are re-linked with Hungarian matching on WORLD-space motion + appearance + team (never across teams),
    # and IDs that may have been swapped after an occlusion are verified against an appearance gallery.
    def __init__(self, fps):
        self.fps = fps; self.max_age = int(fps * 6.0)   # a player hidden behind others for up to 6 s keeps his id
        self.alias = {}; self.info = {}; self.raw_of = {}; self.overlap = {}; self.last_ov = {}
        self.base = 0; self.next_id = 1
    def new_shot(self):
        self.alias.clear(); self.raw_of.clear(); self.overlap.clear(); self.last_ov.clear(); self.info.clear()
        self.base = self.next_id
    def _mint(self, raw):
        cid = raw + self.base; self.next_id = max(self.next_id, cid + 1); return cid
    def split(self, raw, f=None, box=None, lab=None, active=(), emb=None):
        """The tracker kept this raw id on a different player. He is most likely someone whose own track was just lost
        (the two crossed): the nearest lost identity of his team takes him back, else he gets a new one"""
        if box is not None:
            h = float(max(1.0, box[3] - box[1])); x, y = (box[0] + box[2]) / 2, float(box[3])
            best, bd = None, 9.0
            for cid, i in self.info.items():
                age = f - i["last"]
                if cid in active or i["lab"] != lab or not 1 <= age <= self.max_age: continue
                e = emb_dist(emb, i.get("emb"))
                if e is not None and e > EMB_GATE: continue              # looks like someone else
                b = i["box"]; d = math.hypot((b[0] + b[2]) / 2 - x, b[3] - y) / max(h, i["h"])
                lim = 1.5 + 3.0 * age / self.fps
                if d <= lim and d / lim < bd: best, bd = cid, d / lim
            if best is not None:
                self.alias[raw] = best; self.relinked = getattr(self, "relinked", 0) + 1; return best
        self.splits = getattr(self, "splits", 0) + 1
        cid = SPLIT_BASE + self.splits; self.alias[raw] = cid; return cid
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
                    e0 = emb_dist(n.get("emb"), i.get("emb"))
                    if e0 is not None and e0 < 0.5 * EMB_GATE: lim *= 1.6     # clearly the same person: allow a longer way
                    if d > lim: continue
                    ad = app_dist(n["app"], i["app"]) if (n["app"] is not None and i["app"] is not None) else 0.25
                    if ad > 0.55: continue
                    e = emb_dist(n.get("emb"), i.get("emb"))
                    if e is None:
                        C[a, b] = 0.55 * d / lim + 0.45 * min(1.0, ad / 0.5)
                    elif e <= EMB_GATE:
                        C[a, b] = 0.4 * d / lim + 0.2 * min(1.0, ad / 0.5) + 0.4 * e / EMB_GATE
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
    def update(self, cid, raw, f, box, cls, lab, wpos, h, app, clean, emb=None):
        i = self.info.get(cid)
        if i is None:
            self.info[cid] = dict(last=f, wpos=wpos.copy(), wvel=np.zeros(2, np.float32), h=h, cls=cls, lab=lab, app=app, box=box.copy(),
                                  emb=emb)
        else:
            if emb is not None and clean:
                g = i.get("emb")
                if g is None: i["emb"] = emb
                else: g = 0.85 * g + 0.15 * emb; i["emb"] = g / max(1e-9, float(np.linalg.norm(g)))
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
    cents, wsum, mem, assign, ecs = [], [], [], {}, []
    def conflict(k, it): return any(a < it["t1"] and it["t0"] < b for a, b, _ in mem[k])
    def pick(it):
        cand = [k for k in range(len(cents)) if not conflict(k, it)]
        if not cand: return None, 9.0
        ds = sig_dists(np.stack([cents[k] for k in cand]), it["sig"])
        e = it.get("emb")
        if e is not None:          # half colour signature, half appearance embedding where both are known
            for j, k in enumerate(cand):
                if ecs[k] is not None: ds[j] = 0.5 * ds[j] + 0.5 * float(1.0 - np.dot(ecs[k], e))
        j = int(np.argmin(ds))
        return cand[j], float(ds[j])
    def put(k, it):
        e = it.get("emb")
        if k == len(cents):
            cents.append(it["sig"].copy()); wsum.append(it["w"]); mem.append([]); ecs.append(None if e is None else e.copy())
        else:
            cents[k] = (cents[k] * wsum[k] + it["sig"] * it["w"]) / (wsum[k] + it["w"]); wsum[k] += it["w"]
            if e is not None:
                g = e if ecs[k] is None else ecs[k] + 0.5 * e
                ecs[k] = g / max(1e-9, float(np.linalg.norm(g)))
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

BALL_SHOW_LOST = 8      # frames a lost ball is still drawn where the filter predicts it
BALL_REACQ = 4          # after this many frames without the ball, it is found again anywhere, not near the prediction
BALL_RESET = 14

class BallTracker:
    """Kalman-filtered ball with gating and re-acquisition. cands: (x, y, conf, over the pitch). A candidate off the
    pitch (stands, boards) can only continue a flight close to where the filter expects the ball, never start one:
    otherwise, while a fast ball is blurred and missed, the search area grows and a 'ball' in the crowd takes over."""
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
        pick = None; fresh = pred is None or self.lost > BALL_REACQ
        if cands:
            if fresh:
                ok = [c for c in cands if c[3] and c[2] >= 0.25]
                if ok: pick = max(ok, key=lambda t: t[2])
            else:
                gate = min(0.15 * W, 0.035 * W * (1 + 0.7 * self.lost))
                bs = -1e9
                for c in cands:
                    d = math.hypot(c[0] - pred[0], c[1] - pred[1])
                    if not c[3] and d > 0.5 * gate: continue
                    if d <= gate or (c[3] and c[2] >= 0.6):
                        s = c[2] - 0.8 * d / gate
                        if s > bs: bs, pick = s, c
        if pick is not None:
            if fresh:
                self.kf.statePost = np.array([[pick[0]], [pick[1]], [0], [0]], np.float32)
                self.kf.errorCovPost = np.eye(4, dtype=np.float32) * 10
                self.init = True
            else:
                self.kf.correct(np.array([[pick[0]], [pick[1]]], np.float32))
            st = self.kf.statePost; self.lost = 0
            out = (float(st[0, 0]), float(st[1, 0]), float(pick[2]), False)
        elif self.init:
            self.lost += 1
            if self.lost > BALL_RESET:
                self.reset(); return None
            self.kf.statePost = self.kf.statePre.copy(); self.kf.errorCovPost = self.kf.errorCovPre.copy()
            if self.lost > BALL_SHOW_LOST: return None
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
CAM_DIST_M = 60.0       # typical distance from the main broadcast camera to the middle of the picture (sets depth scale)
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
STRIKE_SETTLE_S = 1.5   # a strike waits this long for the kick tracker before it counts as a shot on its own
STRIKE_FLIGHT_S = 0.6   # ball points after a strike used to see which way it went
STRIKE_BLIND_M = 22.0   # a strike this close to a goal counts even when the camera loses the ball straight away
DRIBBLE_M, DRIBBLE_PRESS_M = 5.0, 2.0
DEAD_BALL_S, DEAD_BALL_M = 1.0, 0.8
SAVE_WINDOW_S, SAVE_REACH_M = 2.5, 2.5
MAX_MAP_PASSES = 1500
COUNT_STATS = ("tackles", "interceptions", "clearances", "dispossessed", "dribbles", "crosses", "crosses_ok",
               "box_touches", "shots", "shots_on", "goals", "saves", "corners", "free_kicks", "long_balls", "long_balls_att")
GOAL_LOOKBACK_S = 150   # a goal (score change) belongs to the last shot up to this long before the score bug changed

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
        self.kpH = {}; self.hold = 30; self.kp_of = {}; self.cam = {}; self.kp_pct = 0.0

    def scale(self, fit, y):
        return PLAYER_H_M / max(6.0, fit[0] + fit[1] * y)

    def rel_x(self, C, fit, u, y):
        # a panning broadcast camera rotates: every depth shifts by the same pixels, so the pan (C, metres at the
        # reference row) is converted back to pixels and rescaled to the depth of row y
        return (C / self.scale(fit, self.H * REF_ROW) + u - self.W / 2.0) * self.scale(fit, y)

    def depth_y(self, fit, gtop, y):
        """Metres from the far touchline (the top of the turf) to image row y: the distance from the camera grows as
        players look smaller (pinhole camera: distance ~ 1/height), scaled so the middle of the picture is CAM_DIST_M
        away (see GroundModel). None without a perspective reading (everyone on one row)"""
        a, b = fit
        if b <= 1e-3: return None
        dist = lambda v: CAM_DIST_M * max(6.0, a + b * self.H / 2.0) / max(6.0, a + b * v)
        return min(PITCH_W, max(0.0, dist(gtop) - dist(y)))

    def _kp_point(self, f, u, y):
        g = self.kp_of.get(f)
        if g is None: return None
        u = u - self.cam[f] + self.cam[g]                # into that frame's picture: undo the camera pan in between
        q = self.kpH[g] @ np.array([u, y, 1.0])
        if abs(q[2]) < 1e-9: return None
        x, yy = q[0] / q[2], q[1] / q[2]
        if not (-5.0 <= x <= PITCH_L + 5.0 and -5.0 <= yy <= PITCH_W + 5.0): return None
        return min(PITCH_L, max(0.0, x)), min(PITCH_W, max(0.0, yy))

    def to_pitch(self, f, u, y):
        k = self._kp_point(f, u, y)
        if k is not None:
            x, yy = k
            return (PITCH_L - x, PITCH_W - yy) if self.flip else (x, yy)
        seg, C, fit, gtop = self.frame[f]
        x = min(PITCH_L, max(0.0, self.off.get(seg, PITCH_L / 2) + self.rel_x(C, fit, u, y)))
        yy = self.depth_y(fit, gtop, y)
        if yy is None:
            t = min(1.0, max(0.0, (y - gtop) / max(1.0, self.H - gtop)))
            yy = PITCH_W * t ** DEPTH_GAMMA
        return (PITCH_L - x, PITCH_W - yy) if self.flip else (x, yy)

    def build(self, path):
        segs = {}; prev = None          # prev = (segment, camera x, camera centre in metres, height fit)
        for f, r in _frames(path):
            ps = r["p"]; seg = r.get("s", 0); camx = r.get("k", 0.0)
            same = prev is not None and prev[0] == seg
            fit = _fit_height(ps) or (prev[3] if same else (0.08 * self.H, 0.0))
            if same and prev[3] is not None:      # smoothed within a camera shot: one frame's few players are noisy
                fit = (0.85 * prev[3][0] + 0.15 * fit[0], 0.85 * prev[3][1] + 0.15 * fit[1])
            C = 0.0
            if same:
                C = prev[2] - (camx - prev[1]) * self.scale(fit, self.H * REF_ROW)
            self.frame[f] = (seg, C, fit, r.get("g", 0)); self.cam[f] = camx
            sd = segs.get(seg)
            if sd is None:
                sd = segs[seg] = {"f0": f, "n": 0, "xs": [], "gk": [], "tx": [[0.0, 0], [0.0, 0]], "votes": 0}
            sd["f1"] = f; sd["C1"] = C; sd["n"] += 1
            self._observe(sd, ps, C, fit, f)
            prev = (seg, camx, C, fit)
        self._link_keypoints()
        self._solve(segs)

    def _link_keypoints(self):
        """Each frame -> the nearest frame of the same camera shot with a homography, within KP_HOLD_S"""
        if not self.kpH: return
        ks = sorted(g for g in self.kpH if g in self.frame); import bisect
        for f in self.frame:
            i = bisect.bisect_left(ks, f); best = None
            for g in ks[max(0, i - 1): i + 1]:
                if abs(g - f) <= self.hold and self.frame[g][0] == self.frame[f][0] and (best is None or abs(g - f) < abs(best - f)):
                    best = g
            if best is not None: self.kp_of[f] = best
        self.kp_pct = round(100.0 * len(self.kp_of) / max(1, len(self.frame)), 1)

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
    def __init__(self, pm, fps, G, events, gains, team_of, strikes=(), goals=()):
        self.pm, self.fps, self.G, self.team_of = pm, float(fps), G, team_of
        self.goals = sorted(goals); self.strike_log = []; self.shot_meta = []; self.side_team = {}; self.ball_img = None
        self.img_trail = deque(); self.gk_trail = deque()   # the ball / the keepers in the picture over the last second
        # hard strikes measured in image space (see STRIKE_KMH): [frame, team]. One in the attacking third counts as a
        # shot when the kick tracker missed it, which it does when the pitch estimate jitters
        self.strikes = sorted(strikes); self.si = 0; self.pending = []; self.shot_frames = []
        self.events = sorted(events, key=lambda e: e[0]); self.gains = sorted(gains, key=lambda e: e[0])
        self.ei = self.gi = 0
        self.t = {k: [0, 0] for k in COUNT_STATS}
        self.final_third = [0, 0]; self.xg = [0.0, 0.0]; self.fastest = [0.0, 0.0]
        self.heat = {k: [0] * (HEAT_NX * HEAT_NY) for k in ("a_on", "a_off", "b_on", "b_off")}
        self.pl = defaultdict(lambda: defaultdict(float)); self.pheat = {}
        self.passes = []; self.shots = []; self.saves = []
        self.gk_half = defaultdict(lambda: [0, 0])          # keeper gid -> frames in front of [left, right] goal
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
            if p[5] == 2: self.gk_half[g][0 if x < PITCH_L / 2 else 1] += 1
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
        b = r.get("b")
        if b is not None and not b[2]:
            self.ball_img = (f, b[0], b[1]); self.img_trail.append(self.ball_img)
        while self.img_trail and f - self.img_trail[0][0] > 0.5 * self.fps: self.img_trail.popleft()
        self.gk_trail.extend((f, (p[1] + p[3]) / 2.0, (p[2] + p[4]) / 2.0, p[4] - p[2]) for p in r["p"] if p[5] == 2)
        while self.gk_trail and f - self.gk_trail[0][0] > 1.0 * self.fps: self.gk_trail.popleft()
        while self.si < len(self.strikes) and self.strikes[self.si][0] <= f:
            self._note_strike(f, *self.strikes[self.si]); self.si += 1
        gks = [(f, (p[1] + p[3]) / 2.0, (p[2] + p[4]) / 2.0, p[4] - p[2]) for p in r["p"] if p[5] == 2]
        for it in self.pending:                                     # the flight after each recent strike
            if not 0 < f - it["f"] <= STRIKE_SETTLE_S * self.fps: continue
            it["gks"].extend(gks)
            if b is not None and not b[2]:
                it["img"].append((f, b[0], b[1]))
                if self.ball and self.ball[-1][0] == f and f - it["f"] <= STRIKE_FLIGHT_S * self.fps: it["pts"].append(self.ball[-1])
        while self.pending and f - self.pending[0]["f"] > STRIKE_SETTLE_S * self.fps:
            self._strike_shot(self.pending.pop(0))

    def _heat(self, g, x, y, team, on):
        if team in (0, 1):
            i = min(HEAT_NX - 1, int(x / PITCH_L * HEAT_NX)) + HEAT_NX * min(HEAT_NY - 1, int(y / PITCH_W * HEAT_NY))
            self.heat[("a_" if team == 0 else "b_") + ("on" if on else "off")][i] += 1
            if (x >= PITCH_L - THIRD) if team == 0 else (x <= THIRD): self.pl[g]["ft_f"] += 1
        ph = self.pheat.get(g)                # per player: [on the ball, off the ball]
        if ph is None: ph = self.pheat[g] = ([0] * (PHEAT_NX * PHEAT_NY), [0] * (PHEAT_NX * PHEAT_NY))
        ph[0 if on else 1][min(PHEAT_NX - 1, int(x / PITCH_L * PHEAT_NX)) + PHEAT_NX * min(PHEAT_NY - 1, int(y / PITCH_W * PHEAT_NY))] += 1

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
            if k["gk"] < 0: k["gk"] = self._near_gk(x, y, k["team"])
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
        self.kick = {"f0": f, "x": sx, "y": sy, "g": g, "team": team, "v": v, "pts": [], "dead": dead, "gk": -1}

    def _kicker(self, f, x, y, dead):
        lc = self.last_carrier
        if not dead and lc is not None and f - lc[2] <= self.fps: return lc[0], lc[1]
        best, bg = 4.0, -1                # a dead ball is taken by whoever stands over it
        for g, q in self.pos.items():
            d = math.hypot(q[0] - x, q[1] - y)
            if q[2] != 3 and d < best: best, bg = d, g
        return bg, (self.side_of(bg) if bg >= 0 else -1)

    def _near_gk(self, x, y, team):
        # the defending goalkeeper (in front of the goal `team` attacks) within reach of the ball: his gid, else -1
        for g, q in self.pos.items():
            if q[2] == 2 and att(q[0], q[1], team)[0] >= PITCH_L - 20 and math.hypot(q[0] - x, q[1] - y) <= SAVE_REACH_M:
                return g
        return -1

    def _check_saves(self, f, x, y):
        keep = []
        for f0, team in self.saves:
            gk = self._near_gk(x, y, team)
            if gk >= 0: self.bump("saves", 1 - team, gk)
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
        self.shot_frames.append(k["f0"])
        self.shot_meta.append({"f": k["f0"], "team": team, "g": g, "on": on, "i": len(self.shots), "goal": False})
        xg = shot_xg(ox, oy)
        self.bump("shots", team, g); self.xg[team] += xg
        if g >= 0: self.pl[g]["xg"] += xg
        if on:
            self.bump("shots_on", team, g)
            if k["gk"] >= 0: self.bump("saves", 1 - team, k["gk"])
            else: self.saves.append((k["f0"], team))
        # plotted where its team attacks: team 0 towards x = 105, team 1 towards x = 0, whichever end it was at in the video
        px, py = (ox, oy) if team == 0 else (PITCH_L - ox, PITCH_W - oy)
        self.shots.append([round(px, 1), round(py, 1), team, 1 if on else 0, round(xg, 2), round(v)])

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
        while self.pending: self._strike_shot(self.pending.pop(0))
        for gf, side in self.goals: self._goal(gf, side)

    def _goal(self, gf, side):
        """A score change read off the broadcast: the goal is the last shot before it (else the last hard strike, which
        then becomes a shot); it is on target, and its team tells which side of the score bug each team is"""
        win = GOAL_LOOKBACK_S * self.fps
        team = self.side_team.get(side, -1)
        shots = [m for m in self.shot_meta if 0 <= gf - m["f"] <= win and not m["goal"] and (team < 0 or m["team"] == team)]
        m = max(shots, key=lambda m: m["f"]) if shots else None
        if m is None:
            st = [it for it in self.strike_log if 0 <= gf - it["f"] <= win and (team < 0 or it["team"] == team)]
            if st:
                it = max(st, key=lambda it: it["f"])
                end = PITCH_L if it["x"] > PITCH_L / 2 else 0.0
                ax, ay = (it["x"], it["y"]) if end == PITCH_L else (PITCH_L - it["x"], PITCH_W - it["y"])
                self._shot({"x": it["x"], "y": it["y"], "gk": -1, "f0": it["f"]}, it["team"], it["g"], ax, ay, True, 0.0)
                m = self.shot_meta[-1]
        if m is None:
            if team in (0, 1): self.bump("goals", team)          # a goal without a tracked shot (scored off camera)
            return
        m["goal"] = True; team = m["team"]
        self.side_team.setdefault(side, team); self.side_team.setdefault(1 - side, 1 - team)
        if not m["on"]:
            m["on"] = True; self.bump("shots_on", team, m["g"]); self.shots[m["i"]][3] = 1
        self.bump("goals", team, m["g"])
        self.shots[m["i"]].append(1)                               # [x, y, team, on, xg, kmh, goal]

    def _note_strike(self, f, sf, team):
        # where the ball was struck from: its last measured point, else the last carrier's feet
        lc = self.last_carrier
        if team not in (0, 1) and lc is not None and f - lc[2] <= self.fps: team = lc[1]
        if team not in (0, 1): return
        if self.ball and f - self.ball[-1][0] <= 0.5 * self.fps: x, y = self.ball[-1][1], self.ball[-1][2]
        elif lc is not None and f - lc[2] <= self.fps and lc[0] in self.carry: x, y = self.carry[lc[0]][:2]
        else: return
        g = lc[0] if lc is not None and lc[1] == team and f - lc[2] <= self.fps else -1
        # a strike is confirmed a few frames into the flight: judge the flight from where it began (up to 0.4 s back)
        pre = [q for q in self.img_trail if sf - q[0] <= 0.4 * self.fps]
        img0 = (pre[0][1], pre[0][2]) if pre else None
        self.pending.append({"f": sf, "team": team, "g": g, "x": x, "y": y, "pts": [], "img": pre[1:], "gks": list(self.gk_trail),
                             "img0": img0})
        self.strike_log.append(self.pending[-1])

    def _toward_keeper(self, it):
        """(shot, on target) judged in the picture: the flight heads for a goalkeeper and gets close to him. Needs no
        pitch estimate, which near the goal (steep perspective, few lines in view) is at its least reliable"""
        pts, gks, p0 = it["img"], it["gks"], it["img0"]
        if p0 is None or not pts or not gks: return False, False
        xe, ye = pts[-1][1], pts[-1][2]
        g = min(gks, key=lambda q: math.hypot(q[1] - xe, q[2] - ye))
        h = max(10.0, g[3])
        d0 = math.hypot(g[1] - p0[0], g[2] - p0[1])
        ds = [math.hypot(g[1] - q[1], g[2] - q[2]) for q in pts]; de = min(ds)
        if d0 < 2.0 * h or d0 > 12.0 * h: return False, False   # his own kick / a scramble, or from too far (a long ball)
        shot = de <= 0.55 * d0 and de <= 4.0 * h
        # on target: it reaches him, or is lost from view still closing in on him (into the net, or a save)
        lost_closing = ds[-1] == de and de <= 1.5 * h and pts[-1][0] - it["f"] <= 0.2 * self.fps
        return shot, shot and (de <= 1.3 * h or lost_closing)

    def _strike_shot(self, item):
        """A hard strike near a goal that heads towards it is a shot; away from it, a clearance. Judged first in the
        picture (towards a keeper), else on the goal the ball travels to in the pitch estimate, not on which way the
        teams attack: on broadcast footage that direction is often guessed wrong"""
        sf, team, g, x, y = item["f"], item["team"], item["g"], item["x"], item["y"]
        if any(abs(sf - f0) <= STRIKE_SETTLE_S * self.fps for f0 in self.shot_frames): return   # already a shot
        end = PITCH_L if x > PITCH_L / 2 else 0.0                 # the goal nearer the strike
        ax, ay = (x, y) if end == PITCH_L else (PITCH_L - x, PITCH_W - y)   # in the frame where that goal is at x = 105
        dist = math.hypot(end - x, PITCH_W / 2 - y)
        shot, on = self._toward_keeper(item)
        if shot and dist <= SHOT_RANGE_M + 8.0:                   # the pitch estimate agrees it's within range
            self._shot({"x": x, "y": y, "gk": -1, "f0": sf}, team, g, ax, ay, on, 0.0); return
        if dist > SHOT_RANGE_M: return
        pts = item["pts"]
        if pts:
            if abs(end - pts[-1][1]) >= abs(end - x) - 2.0: return  # flew away from that goal: a clearance or a pass
        elif dist > STRIKE_BLIND_M: return
        self._shot({"x": x, "y": y, "gk": -1, "f0": sf}, team, g, ax, ay, False, 0.0)

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
            row = {k: int(d.get(k, 0)) for k in COUNT_STATS if k != "crosses_ok"}
            on, off = self.pheat.get(g, ([], []))
            row.update(xg=round(d.get("xg", 0.0), 2), final_third_s=round(d.get("ft_f", 0) / fps, 1),
                       heat=_norm_grid([a + b for a, b in zip(on, off)]), heat_on=_norm_grid(on), heat_off=_norm_grid(off))
            if g in self.gk_half:                    # a keeper: the side (0 = A, 1 = B) whose goal he stood in front of most
                left, right = self.gk_half[g]; row["gk_side"] = 0 if left >= right else 1
            players[g] = row
        heat = dict(self.heat)
        for s in "ab": heat[s + "_all"] = [a + b for a, b in zip(heat[s + "_on"], heat[s + "_off"])]
        maps = {"heat": {k: _norm_grid(v) for k, v in heat.items()}, "heat_size": [HEAT_NX, HEAT_NY],
                "player_heat_size": [PHEAT_NX, PHEAT_NY], "passes": self.passes, "shots": self.shots}
        return {"team": team, "players": players, "maps": maps}

def match_analytics(path, G, fps, W, H, events, gains, team_of, keep, strikes=(), pm_out=None, goals=(), kpH=None):
    """Two passes over the per-frame log: map pixels to the pitch, then count. keep = player ids to report."""
    pm = PitchMapper(W, H); pm.kpH = kpH or {}; pm.hold = int(1.0 * fps); pm.build(path)   # a homography covers frames 1 s away
    ma = MatchAnalytics(pm, fps, G, events, gains, team_of, strikes, goals)
    for f, r in _frames(path): ma.frame(f, r)
    ma.finish()
    out = ma.result(keep)
    out["pitch"] = {"estimated": True, "anchored_pct": pm.anchored_pct, "flipped": pm.flip, "markings_pct": pm.kp_pct}
    if pm_out is not None: pm_out.append(pm)     # the pitch estimate, for the tactical radar drawn into the video
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
                todo = [p for p in pend if p["det"]]
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
                need = wide and (cut or not prev_wide or idx - last_det >= self.stride)   # people (tracker) frames
                if need: last_det = idx
                prev_wide = wide
                # every wide frame is detected: the ball moves too fast to skip frames (a shot crosses the box in ~10),
                # people are only re-detected every `stride` frames and predicted in between
                pend.append({"idx": idx, "frame": fr, "gm": gm, "wide": wide, "cut": cut, "need": need, "det": wide, "bn": None})
                n_need += 1 if wide else 0
                if n_need >= self.batch or len(pend) >= self.cap:
                    flush(); n_need = 0
            flush()
        except Exception as e:
            import traceback; traceback.print_exc()
            self._put({"error": e})
        self._put(None)

# ----------------------------------------------------------------------------
# GOALS FROM THE SCORE BUG: broadcasts show the score in a corner graphic ("ROM 1-0 INT"). It is read with OCR every
# few seconds in a worker thread; a goal is the score going up by one, confirmed by a second read. Optional: without
# the tesseract binary (pip pytesseract + apt tesseract-ocr) nothing is read and goals come from the ball alone.
# ----------------------------------------------------------------------------
SCORE_EVERY_S = 2.0
SCORE_DOWN_READS = 5   # reads in a row before a lower score is believed (about 10 s)
SCORE_BAND = 0.18        # the bug sits in the top 18% of the picture
SCORE_RE = re.compile(r"(?<![\d:.])(\d{1,2}) ?[-\u2013\u2014] ?(\d{1,2})(?![\d:])")

def _score_of(text):
    """The score in an OCR'd line, or None. The match clock (mm:ss) never matches: a score uses a dash"""
    for m in SCORE_RE.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        if a <= 15 and b <= 15: return a, b
    return None

class ScoreReader:
    def __init__(self, fps):
        self.every = max(1, int(round(SCORE_EVERY_S * fps))); self.reads = []; self.box = None; self.fails = 0
        self.q = queue.Queue(6); self.stop = False; self.ok = False
        try:
            import pytesseract
            pytesseract.get_tesseract_version(); self.tess = pytesseract; self.ok = True
        except Exception:
            return
        self.t = threading.Thread(target=self._run, daemon=True); self.t.start()

    def offer(self, idx, frame):
        if not self.ok or idx % self.every: return
        H = frame.shape[0]
        try: self.q.put_nowait((idx, frame[: int(H * SCORE_BAND)].copy()))
        except queue.Full: pass                        # OCR is behind: skip this sample

    def _ocr(self, img, psm=11):
        g = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        for im in (g, 255 - g):                        # dark text on light, then light on dark
            d = self.tess.image_to_data(im, config=f"--psm {psm}", output_type=self.tess.Output.DICT)
            words = [(d["text"][i], d["left"][i], d["top"][i], d["width"][i], d["height"][i], d["line_num"][i], d["block_num"][i])
                     for i in range(len(d["text"])) if d["text"][i].strip()]
            line = " ".join(w[0] for w in words)
            sc = _score_of(line)
            if sc is None:                             # the dash may be its own word: join neighbours of digits
                for i in range(len(words) - 2):
                    sc = _score_of("".join(w[0] for w in words[i:i + 3]))
                    if sc: break
            if sc is not None:
                hit = [w for w in words if any(ch.isdigit() for ch in w[0]) and _score_of(w[0]) or w[0] in ("-", "\u2013")]
                return sc, (hit[0][1] // 2, hit[0][2] // 2, hit[0][3] // 2, hit[0][4] // 2) if hit else None
        return None, None

    def _run(self):
        while not self.stop:
            try: idx, band = self.q.get(timeout=0.5)
            except queue.Empty: continue
            try:
                if self.box is not None:               # read just around the score once it has been found
                    x, y, w, h = self.box; x0, y0 = max(0, x - 8 * h), max(0, y - 2 * h)
                    sc, where = self._ocr(band[y0: y + 3 * h, x0: x + w + 8 * h], 6)
                else:                                  # find it: each half of the band on its own reads far better
                    W_ = band.shape[1]; sc = where = None
                    for x0 in (0, W_ // 2):
                        sc, where = self._ocr(band[:, x0: x0 + W_ // 2])
                        if sc is not None:
                            if where is not None: where = (where[0] + x0, where[1], where[2], where[3])
                            break
                if sc is None and self.box is not None:
                    self.fails += 1
                    if self.fails >= 4: self.box = None; self.fails = 0
                elif sc is not None:
                    self.fails = 0
                    if self.box is None and where is not None: self.box = where
                    self.reads.append((idx, sc[0], sc[1]))
            except Exception as e:
                print("score OCR failed:", e)

    def close(self, timeout=20.0):
        if not self.ok: return
        t0 = time.time()
        while not self.q.empty() and time.time() - t0 < timeout: time.sleep(0.2)
        self.stop = True; self.t.join(timeout=5)

    def goals(self):
        """[(frame, side)]: side 0 = the score's left team, 1 = the right. A new score counts once read twice in a row;
        a score that goes back down (a goal ruled out) removes that side's last goal"""
        reads = sorted(self.reads); out = []; cur = None; cand = None; n = 0
        for idx, a, b in reads:
            sc = (a, b)
            if cur is None:
                if sc == (0, 0) or (cand is not None and cand[1] == sc): cur = sc; cand = None   # 0-0 needs no second read
                else: cand = (idx, sc)
                continue
            if sc == cur: cand = None; continue
            if cand is None or cand[1] != sc: cand = (idx, sc); n = 1; continue
            n += 1
            # a goal needs 2 reads in a row; a lower score (a goal ruled out) needs 5, since a 3 misread as 2 looks the same
            down = sc[0] < cur[0] or sc[1] < cur[1]
            if n < (SCORE_DOWN_READS if down else 2): continue
            f0, (na, nb) = cand; cand = None
            if na >= cur[0] and nb >= cur[1] and (na - cur[0]) + (nb - cur[1]) <= 2:
                out += [(f0, 0)] * (na - cur[0]) + [(f0, 1)] * (nb - cur[1])
            elif na <= cur[0] and nb <= cur[1]:
                for side, n_ in ((0, cur[0] - na), (1, cur[1] - nb)):
                    for _ in range(n_):
                        j = max((i for i, g in enumerate(out) if g[1] == side), default=None)
                        if j is not None: out.pop(j)
            else:
                continue                               # an impossible jump: a misread, keep the current score
            cur = (na, nb)
        return out

# ----------------------------------------------------------------------------
# PITCH FROM ITS MARKINGS: a keypoint model (YOLO11 pose, cdqqqqqq/Soccana_Keypoint on Hugging Face) finds up to 29
# landmarks of the pitch (corners, box corners, centre circle...) every few frames; the ones it is sure of give the
# homography from the picture to pitch metres. Optional: without the weights the pitch is estimated as before.
# ----------------------------------------------------------------------------
KP_MODEL_PATH = os.environ.get("TRIVELA_KP_MODEL", "pitch-keypoints.pt")
KP_EVERY_S = 0.2         # one keypoint reading every 0.2 s
KP_CONF = 0.5
KP_MIN_POINTS = 4
_BX, _GX = (PITCH_W - 40.32) / 2, (PITCH_W - 18.32) / 2
_DX = 11.0 + 9.15        # the D of the penalty area: the penalty spot plus the circle's radius
# metres (x along the touchline, y from the far touchline), in the model's keypoint order
KP_PITCH = np.array([
    (0, 0), (0, _BX), (BOX_D, _BX), (0, PITCH_W - _BX), (BOX_D, PITCH_W - _BX),
    (0, _GX), (5.5, _GX), (0, PITCH_W - _GX), (5.5, PITCH_W - _GX), (0, PITCH_W),
    (_DX, PITCH_W / 2), (PITCH_L / 2, 0), (PITCH_L / 2, PITCH_W), (PITCH_L / 2, PITCH_W / 2 - 9.15),
    (PITCH_L / 2, PITCH_W / 2 + 9.15), (PITCH_L / 2, PITCH_W / 2), (PITCH_L, 0),
    (PITCH_L, _BX), (PITCH_L - BOX_D, _BX), (PITCH_L, PITCH_W - _BX), (PITCH_L - BOX_D, PITCH_W - _BX),
    (PITCH_L, _GX), (PITCH_L - 5.5, _GX), (PITCH_L, PITCH_W - _GX), (PITCH_L - 5.5, PITCH_W - _GX), (PITCH_L, PITCH_W),
    (PITCH_L - _DX, PITCH_W / 2), (PITCH_L / 2 - 9.15, PITCH_W / 2), (PITCH_L / 2 + 9.15, PITCH_W / 2)], np.float32)

def kp_homography(xy, conf):
    """Picture -> pitch metres from one frame's keypoints, or None when too few or too clustered to trust"""
    ok = (conf >= KP_CONF) & (xy[:, 0] > 0) & (xy[:, 1] > 0)
    if ok.sum() < KP_MIN_POINTS: return None
    src, dst = xy[ok].astype(np.float32), KP_PITCH[ok]
    if cv2.contourArea(cv2.convexHull(dst)) < 60.0: return None      # on one line (the halfway line alone) or a tiny patch
    Hm, inl = cv2.findHomography(src, dst, cv2.RANSAC, 2.0)          # 2 m reprojection tolerance
    if Hm is None or inl is None or inl.sum() < KP_MIN_POINTS: return None
    if cv2.contourArea(cv2.convexHull(dst[inl.ravel() > 0])) < 60.0: return None
    return Hm

class KeypointReader:
    """Pitch keypoints on sampled frames in a worker thread, on the GPU next to the detector"""
    def __init__(self, fps):
        self.every = max(1, int(round(KP_EVERY_S * fps))); self.H = {}; self.ok = False
        self.q = queue.Queue(8); self.stop = False; self.tried = 0; self.last = None
        if not os.path.isfile(KP_MODEL_PATH): return
        try:
            self.model = YOLO(KP_MODEL_PATH); self.ok = True
        except Exception as e:
            print("pitch keypoint model not loaded:", e); return
        self.t = threading.Thread(target=self._run, daemon=True); self.t.start()

    def offer(self, idx, frame, cam=(0.0, 0.0)):
        if not self.ok or idx % self.every: return
        sc = min(1.0, 1280.0 / frame.shape[1])
        img = cv2.resize(frame, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA) if sc < 1.0 else frame.copy()
        try: self.q.put_nowait((idx, img, sc, (float(cam[0]), float(cam[1]))))
        except queue.Full: pass

    def ground(self, idx, w0, w1, max_age):
        """Metres between two camera-compensated ground points (wpos), both through the latest homography (one and
        the same, so its refits add no jitter), or None without a recent one"""
        last = self.last
        if last is None or idx - last[0] > max_age: return None
        _, Hm, (cx, cy) = last
        q = Hm @ np.array([[w0[0] + cx, w1[0] + cx], [w0[1] + cy, w1[1] + cy], [1.0, 1.0]])
        if np.any(np.abs(q[2]) < 1e-9): return None
        x, y = q[0] / q[2], q[1] / q[2]
        if not (np.all((-10 < x) & (x < PITCH_L + 10)) and np.all((-10 < y) & (y < PITCH_W + 10))): return None
        return float(math.hypot(x[1] - x[0], y[1] - y[0]))

    def _run(self):
        while not self.stop or not self.q.empty():
            try: idx, img, sc, cam = self.q.get(timeout=0.5)
            except queue.Empty: continue
            try:
                r = self.model.predict(img, imgsz=640, conf=0.25, half=HAS_GPU, device=DEVICE_TARGET, verbose=False)[0]
                self.tried += 1
                k = r.keypoints
                if k is None or k.conf is None or len(k.xy) == 0: continue
                j = int(np.argmax(r.boxes.conf.cpu().numpy())) if r.boxes is not None and len(r.boxes) else 0
                Hm = kp_homography(k.xy[j].cpu().numpy() / sc, k.conf[j].cpu().numpy())
                if Hm is not None: self.H[idx] = Hm; self.last = (idx, Hm, cam)
            except Exception as e:
                print("pitch keypoints failed:", e)

    def close(self, timeout=60.0):
        if not self.ok: return
        self.stop = True; self.t.join(timeout=timeout)

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

def to_h264(src, dst, height=0):
    """Re-encodes a video this OpenCV build can't decode (AV1/VP9, as YouTube serves) to H.264. The system ffmpeg has
    the software decoders; encoding uses NVENC when the GPU has it. height > 0 also scales down to that height."""
    args, _ = pick_encoder()
    vf = ["-vf", f"scale=-2:{even(height)}"] if height else []
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, *vf, *args, "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart", dst], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dst):
        raise RuntimeError("This video's format can't be read, and converting it failed: " + (r.stderr or "").strip()[-300:])
    return dst

class Encoder:
    # frames are handed to a writer thread, so encoding overlaps with detection and tracking
    def __init__(self, path, w, h, fps, audio_src):
        self.path = path; self.p = None; self.cv = None; self.raw = path; self.err = None; self.name = "OpenCV"; self.blocked = 0.0
        try:
            vargs, self.name = pick_encoder()
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                   "-r", f"{fps:.4f}", "-i", "-", "-i", audio_src, "-map", "0:v:0", "-map", "1:a:0?"] + vargs + \
                  ["-g", str(max(1, int(round(fps)))), "-maxrate", "5M", "-bufsize", "10M",   # keyframe each second: quick seeking
                   "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", path]
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

NUMBERS_PER_TEAM = 11   # each team is numbered 1..11 by screen time (a team has 11); shorter fragments keep their ring only

def player_numbers(real, team_info, appear):
    """Numbers for the report and the video: each team 1, 2, 3... by screen time (its colour tells the teams apart),
    goalkeepers "GK". Officials get none"""
    nums = {}
    for grp in ("team_a", "team_b"):
        ids = sorted((c for c in real if team_info.get(c) == grp), key=lambda c: (-appear[c], c))
        nums.update({c: i + 1 for i, c in enumerate(ids[:NUMBERS_PER_TEAM])})
    nums.update({c: "GK" for c in real if team_info.get(c) == "goalkeeper"})
    return nums

def draw_number(frame, b, n, color, carrier=False):
    x1, y1, x2, _ = b; H = frame.shape[0]
    fs = max(0.42, H / 1700.0); th = 1 if H < 900 else 2
    t = str(n); (tw, tht), _ = cv2.getTextSize(t, cv2.FONT_HERSHEY_DUPLEX, fs, th)
    cx = (x1 + x2) // 2; by = y1 - (24 if carrier else 6)              # above the carrier's gold arrow
    px, py = 4, 3
    x0, y0 = cx - tw // 2 - px, by - tht - 2 * py
    cv2.rectangle(frame, (x0, y0), (x0 + tw + 2 * px, by), color, -1, cv2.LINE_AA)
    cv2.rectangle(frame, (x0, y0), (x0 + tw + 2 * px, by), (0, 0, 0), 1, cv2.LINE_AA)
    ink = (0, 0, 0) if sum(color) > 450 else (255, 255, 255)             # dark text on light kits
    cv2.putText(frame, t, (x0 + px, by - py), cv2.FONT_HERSHEY_DUPLEX, fs, ink, th, cv2.LINE_AA)

RADAR_W = 0.24          # tactical radar width, as a share of the video width
RADAR_EMA = 0.35        # smoothing of radar dots: the pitch estimate jitters frame to frame

def draw_radar(frame, dots, ball, kit):
    """2D tactical view in the bottom-right corner. dots: [(number or None, label, x m, y m)], ball: (x m, y m) or None"""
    H, W = frame.shape[:2]
    rw = int(W * RADAR_W); rh = int(rw * PITCH_W / PITCH_L); m = max(8, W // 120)
    x0, y0 = W - rw - m, H - rh - m - int(H * 0.06)               # clear of the possession bar
    roi = frame[y0:y0 + rh, x0:x0 + rw]
    bg = np.empty_like(roi); bg[:] = (40, 90, 30)
    cv2.addWeighted(bg, 0.78, roi, 0.22, 0, roi)
    P = lambda x, y: (x0 + int(x / PITCH_L * (rw - 1)), y0 + int(y / PITCH_W * (rh - 1)))
    ln = (225, 235, 225); t = 1
    cv2.rectangle(frame, P(0, 0), P(PITCH_L, PITCH_W), ln, t)
    cv2.line(frame, P(PITCH_L / 2, 0), P(PITCH_L / 2, PITCH_W), ln, t)
    cv2.circle(frame, P(PITCH_L / 2, PITCH_W / 2), int(9.15 / PITCH_L * rw), ln, t, cv2.LINE_AA)
    for gx in (0.0, PITCH_L - BOX_D):
        cv2.rectangle(frame, P(gx, BOX_Y0), P(gx + BOX_D, BOX_Y1), ln, t)
    r = max(3, rw // 60); fs = max(0.28, r / 14.0)
    for n, lab, x, y in dots:
        c = P(x, y); col = kit.get(lab, (200, 200, 200))
        cv2.circle(frame, c, r + 1, (0, 0, 0), -1, cv2.LINE_AA); cv2.circle(frame, c, r, col, -1, cv2.LINE_AA)
        if n is not None and r >= 5:
            tx = str(n); (tw, th_), _ = cv2.getTextSize(tx, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
            cv2.putText(frame, tx, (c[0] - tw // 2, c[1] + th_ // 2), cv2.FONT_HERSHEY_SIMPLEX, fs,
                        (0, 0, 0) if sum(col) > 450 else (255, 255, 255), 1, cv2.LINE_AA)
    if ball is not None:
        c = P(*ball); cv2.circle(frame, c, max(2, r - 1), (0, 0, 0), -1, cv2.LINE_AA); cv2.circle(frame, c, max(1, r - 2), (255, 255, 255), -1, cv2.LINE_AA)

def cam_view(pm, p):
    # the radar in the video shows the pitch as the camera sees it, not turned so that team A attacks to the right
    return (PITCH_L - p[0], PITCH_W - p[1]) if pm.flip else p

def burn_numbers(video, log_path, G, nums, kit, W, H, fps, audio_src, on_progress=None, pm=None):
    """Second pass over the finished video: the final player numbers are only known after re-identification, so
    they are drawn here, from the frame log, and the video is replaced"""
    lab_of = {v: k for k, v in TEAM_CODE.items()}
    tmp = video + ".numbers.mp4"; smooth = {}
    cap = cv2.VideoCapture(video); enc = Encoder(tmp, W, H, fps, audio_src)
    total = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    try:
        with open(log_path) as fin:
            nxt = fin.readline(); idx = 0
            while True:
                ok, frame = cap.read()
                if not ok: break
                while nxt:
                    fi, _, body = nxt.partition("\t")
                    if int(fi) > idx: break
                    if int(fi) == idx:
                        r_ = json.loads(body); car = G(r_["c"]) if r_["c"] >= 0 else -1
                        dots = []
                        for c, x1, y1, x2, y2, code in r_["p"]:
                            n = nums.get(G(c)); lab = lab_of.get(code, "team_a")
                            if n: draw_number(frame, (x1, y1, x2, y2), n, kit[lab], G(c) == car)
                            if pm is not None and lab != "official" and idx in pm.frame:
                                x, y = cam_view(pm, pm.to_pitch(idx, (x1 + x2) / 2.0, y2)); q = smooth.get(G(c))
                                if q is not None and idx - q[2] <= 3:
                                    x, y = q[0] + RADAR_EMA * (x - q[0]), q[1] + RADAR_EMA * (y - q[1])
                                smooth[G(c)] = (x, y, idx); dots.append((n, lab, x, y))
                        if pm is not None and idx in pm.frame:
                            b_ = r_.get("b")
                            draw_radar(frame, dots, cam_view(pm, pm.to_pitch(idx, b_[0], b_[1])) if b_ else None, kit)
                    nxt = fin.readline()
                enc.write(frame); idx += 1
                if on_progress and idx % 250 == 0: on_progress(idx / total)
    finally:
        cap.release(); enc.close()
    os.replace(tmp, video)

# ----------------------------------------------------------------------------
# MAIN PIPELINE
# ----------------------------------------------------------------------------
def process_football_match(input_path: str, mode_key: str, res_key: str, burn: bool = True, reid: bool = True, kits=None):
    J = JOB_STATE
    cap = reader = enc = gz = dthread = None
    t_job = time.time()
    mode = MODES.get(mode_key, MODES["balanced"])
    out_path = str(PROCESSED_DIR / "tactical_output.mp4")
    meta_path = str(PROCESSED_DIR / "tactical_metadata.json.gz")
    fixed = str(UPLOAD_DIR / "fixed_input.mp4")
    converted = str(UPLOAD_DIR / "h264_input.mp4")
    summ_path = str(PROCESSED_DIR / "summary.json")
    for p in [out_path, meta_path, fixed, converted, summ_path]:
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
        # Opens but no frame decodes: a codec this OpenCV build lacks (AV1/VP9 from YouTube). Convert it to H.264 once,
        # straight to the analysis height so the conversion and everything after it handle fewer pixels
        decodes = cap.read()[0]
        src_h0 = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)); cap.release()
        if not decodes:
            J.update(phase="Converting the video to H.264 (its format can't be read directly)")
            th0 = RES_H.get(res_key, 0)
            src = to_h264(src, converted, th0 if th0 and src_h0 > th0 else 0)
        cap = cv2.VideoCapture(src)   # reopened either way, so reading starts again at frame 0
        if not cap.isOpened() or not cap.read()[0]:
            raise RuntimeError("Could not read any frames from the video.")
        cap.release(); cap = cv2.VideoCapture(src)

        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0 or np.isnan(fps): fps = 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            total = int(probe_duration(src) * fps)
        th = RES_H.get(res_key, 1080)
        scale = 1.0 if th == 0 else min(1.0, th / float(src_h))     # aspect ratio preserved, never upscaled
        W, H = even(src_w * scale), even(src_h * scale)

        model = YOLO(MODEL_PATH)
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
                        if k in ref_ids and c >= REF_SAMPLE_CONF and valid_geometry(xy.astype(int), W, H):
                            team.ref_samples.append(team.feature(fr, xy.astype(int))); continue
                        if k in ball_ids or k in ref_ids or k in gk_ids or c < 0.5: continue
                        bx = xy.astype(int)
                        if valid_geometry(bx, W, H) and on_turf(gm, bx):
                            f = team.feature(fr, bx)
                            if f is not None: team.samples.append(f)
            if len(team.samples) >= 60: team.fit()
            team.ref_samples = [f_ for f_ in team.ref_samples if f_ is not None]
            if len(team.ref_samples) >= REF_MIN_SAMPLES: team.ref_c = np.median(np.array(team.ref_samples, np.float32), axis=0)
        home_kit = None
        if kits:                                   # the user clicked a player of each team (and the referee) on a frame
            picked = {}
            cap2 = cv2.VideoCapture(src)
            for k_ in kits:
                cap2.set(cv2.CAP_PROP_POS_MSEC, float(k_["t"]) * 1000.0); ok, fr = cap2.read()
                if not ok or fr is None: continue
                fr = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA)
                px, py = float(k_["x"]) * W, float(k_["y"]) * H
                bn = run_batch([fr])[0].boxes.cpu().numpy(); best = None
                for xy, k in zip(bn.xyxy, bn.cls.astype(int)):
                    if k in ball_ids: continue
                    # the nearest person to the click, within half a body height of his box (players move a little
                    # between the frame shown in the picker and the one read here)
                    x1, y1, x2, y2 = xy; h_ = y2 - y1
                    d = math.hypot(max(x1 - px, 0, px - x2), max(y1 - py, 0, py - y2))
                    if d <= 0.5 * h_ and (best is None or d < best[0]): best = (d, xy.astype(int))
                f = team.feature(fr, best[1]) if best is not None else None
                if f is not None: picked.setdefault(k_["role"], []).append(f)
            cap2.release()
            med = lambda r: np.median(np.array(picked[r], np.float32), axis=0) if picked.get(r) else None
            home_kit = team.seed(med("home"), med("away"), med("ref"))
            print("kit clicks:", {r: len(v) for r, v in picked.items()}, "of", len(kits), "home kit:", home_kit)
        kit = team.kit_bgr(); tm["pre"] = time.time() - t_pre0; tm["gpu"] = 0.0; tm["batches"] = 0

        tracker, tracker_name = make_tracker(fps)
        score = ScoreReader(fps); kpr = KeypointReader(fps)
        ident = IdentityManager(fps); cam = CameraMotion(); ground = GroundModel(H)
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
        bw = {"p": None, "f": -10, "v": 0.0, "raw": 0.0, "armed": True, "dir": None}; ball_max = 0.0; ball_sum = 0.0; ball_n = 0
        strikes = 0; swaps_n = 0; splits_n = 0; prev_bn = None
        sigacc = {}; last_seen = {}; embacc = {}; det_n = [0]; embedder = Embedder()
        zoom_m = YOLO(MODEL_PATH) if BALL_ZOOM else None; zn = [0, 0]     # its own copy: the detector thread uses the other
        seg_n = [0]          # camera segment id for the analytics log: bumped whenever the camera state resets

        def reset_state(f):
            tracks.clear(); ball.reset(); ident.new_shot(); cam.reset(); poss.drop(f); seg_n[0] += 1
            bw.update(p=None, v=0.0, raw=0.0, armed=True, dir=None)
            ground.reset()
            for p_ in phys.values():
                p_["wp"] = None; p_["above"] = None
                if p_["on"]: p_["on"] = False; p_["pk_sum"] += p_["pk"]; p_["pk_n"] += 1
            try: tracker.reset()
            except Exception: pass

        while True:
            if JOB_CANCEL.is_set():
                cancelled = True; break
            t_w = time.time(); item = dthread.get(); tm["wait"] += time.time() - t_w
            if item is None: break
            if "error" in item: raise item["error"]
            frame = item["frame"]; idx = item["idx"]
            score.offer(idx, frame); kpr.offer(idx, frame, cam.cum)

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

            bn = item["bn"]
            do_det = bn is not None and item["need"]          # people this frame (the ball is looked for on every frame)
            if do_det and prev_bn is not None and prev_bn[0] == idx - 1:
                bn = fuse_people(bn, prev_bn[1], ball_ids)
            prev_bn = (idx, item["bn"]) if item["bn"] is not None else None
            cands = []
            if bn is not None:
                xyxy = bn.xyxy; conf = bn.conf; cls = bn.cls.astype(int)
                for i in range(len(cls)):
                    if int(cls[i]) not in ball_ids: continue
                    c = float(conf[i]); x1, y1, x2, y2 = xyxy[i]
                    if c >= BALL_MIN_CONF and (x2 - x1) <= 0.03 * W and (y2 - y1) <= 0.05 * H:
                        bx_, by_ = (x1 + x2) / 2, (y1 + y2) / 2
                        cands.append((bx_, by_, c, ball_over_pitch(gm, bx_, by_, H)))
            if do_det:
                keep = []
                for i in range(len(cls)):
                    k = int(cls[i]); c = float(conf[i])
                    if k in ball_ids or c < PERSON_MIN_CONF: continue
                    bx = xyxy[i].astype(int)
                    if valid_geometry(bx, W, H) and on_turf(gm, bx): keep.append(i)
                keep = drop_part_boxes(xyxy, conf, keep)
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
                    plab = "official" if k in ref_ids else "goalkeeper" if k in gk_ids else None   # this detection only
                    rows.append((raw, box, k, plab, float(row[5])))
                    if raw not in ident.alias:
                        fresh.append({"raw": raw, "box": box, "cls": k, "h": float(max(1.0, box[3] - box[1])),
                                      "lab": plab or team.quick(frame, box), "app": appearance(frame, box, grass),
                                      "wpos": np.array([(box[0] + box[2]) / 2 - cam.cum[0], box[3] - cam.cum[1]], np.float32)})
                emb_by_raw = {}
                if embedder.ok:
                    det_n[0] += 1
                    pick = [j for j, r_ in enumerate(rows) if r_[0] not in ident.alias or det_n[0] % EMB_EVERY == 0]
                    for j, e_ in zip(pick, embedder(frame, [rows[j][1] for j in pick])):
                        if e_ is not None: emb_by_raw[rows[j][0]] = e_
                    for n_ in fresh: n_["emb"] = emb_by_raw.get(n_["raw"])
                ident.assign_new(idx, fresh, {ident.alias[r_[0]] for r_ in rows if r_[0] in ident.alias})
                act_now = {ident.alias[r_[0]] for r_ in rows if r_[0] in ident.alias}
                used = set(); boxes_now = {}
                for raw, box, k, plab, bconf in rows:
                    cid = ident.alias.get(raw)
                    if cid is None or cid in used: continue
                    used.add(cid)
                    if k in ref_ids and bconf >= REF_SAMPLE_CONF and (idx + cid) % 8 == 0 and team.ready:
                        team.add_ref_sample(frame, box)
                    lab = team.label_for(cid, frame, box.astype(int), idx, "ref" if k in ref_ids else "gk" if k in gk_ids else None)
                    if team.flipped:                     # shirt changed team: the tracker slid onto another player
                        old = cid; cid = ident.split(raw, idx, box, lab, act_now | used, emb_by_raw.get(raw)); used.add(cid); team.hand_over(old, cid, lab); splits_n += 1
                    h = float(max(1.0, box[3] - box[1]))
                    wpos = np.array([(box[0] + box[2]) / 2 - cam.cum[0], box[3] - cam.cum[1]], np.float32)
                    clean = idx - ident.last_ov.get(cid, -999) > 12
                    known = ident.info.get(cid)
                    app = appearance(frame, box, grass) if (known is None or known["app"] is None or (clean and (idx + cid) % 6 == 0)) else None
                    e_ = emb_by_raw.get(raw)
                    ident.update(cid, raw, idx, box, k, lab, wpos, h, app, clean, e_)
                    if e_ is not None and clean:          # the tracklet's appearance, for joining tracklets at the end
                        ea = embacc.get(cid)
                        if ea is None: embacc[cid] = [e_.copy(), 1]
                        else: ea[0] += e_; ea[1] += 1
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
                    # speed over the last second, in metres with the pitch's perspective (GroundModel); distance is
                    # that speed integrated over time, so frame-to-frame box jitter adds nothing
                    ph = phys.setdefault(cid, {"wp": None, "hist": deque(), "df": idx, "dist": 0.0, "top": 0.0, "top_f": idx,
                                               "spr": 0, "on": False, "above": None, "pk": 0.0, "pk_sum": 0.0, "pk_n": 0})
                    hh = ident.info[cid]["h"]; fy = float(box[3]); hist = ph["hist"]
                    if ph["wp"] is None: hist.clear(); ph["df"] = idx
                    ph["wp"] = wpos
                    hist.append((idx, wpos.copy(), fy, hh))
                    while idx - hist[0][0] > SPEED_WIN_S * fps: hist.popleft()
                    f0, w0, y0, h0 = hist[0]
                    if idx - f0 >= 0.6 * SPEED_WIN_S * fps:
                        # from the pitch markings when they were read in the last 1.5 s, else the perspective estimate
                        d_ = kpr.ground(idx, w0, wpos, 1.5 * fps)
                        if d_ is None: d_ = ground.metres(wpos[0] - w0[0], wpos[1] - w0[1], 0.5 * (fy + y0), 0.5 * (hh + h0))
                        sp = d_ / ((idx - f0) / fps)
                        if sp > MAX_RUN_MS:                      # an id that jumped to another player: start over
                            hist.clear(); hist.append((idx, wpos.copy(), fy, hh)); ph["df"] = idx; ph["last_sp"] = ph["last_sp2"] = 0.0
                        else:
                            if sp >= 0.4: ph["dist"] += sp * min(idx - ph["df"], 0.5 * fps) / fps
                            ph["df"] = idx
                            # a top speed must hold over three readings in a row: one bad box or a pan glitch is no sprint
                            held = min(sp, ph.get("last_sp", 0.0), ph.get("last_sp2", 0.0))
                            ph["last_sp2"] = ph.get("last_sp", 0.0); ph["last_sp"] = sp
                            if held > ph["top"]: ph["top"] = held; ph["top_f"] = idx
                            if ph["on"]:
                                ph["pk"] = max(ph["pk"], sp)
                                if sp < SPRINT_END_MS: ph["on"] = False; ph["pk_sum"] += ph["pk"]; ph["pk_n"] += 1
                            elif sp >= SPRINT_MS:
                                if ph["above"] is None: ph["above"] = idx
                                if idx - ph["above"] >= SPRINT_MIN_S * fps:
                                    ph["on"] = True; ph["above"] = None; ph["spr"] += 1; ph["pk"] = sp
                                    if lab != "official" and idx - last_sprint.get(cid, -10**9) > fps * 3:
                                        last_sprint[cid] = idx; xev.append([idx, "sprint", cid, TEAM_CODE[lab], round(sp * 3.6, 1)])
                            else:
                                ph["above"] = None
                ground.update([b_ for c_, b_ in boxes_now.items() if team_info.get(c_) in ("team_a", "team_b")])
                ident.note_overlaps(idx, boxes_now)
                for a_, b_ in ident.check_swaps(idx, boxes_now, frame, grass):
                    if a_ in tracks and b_ in tracks:
                        tracks[a_], tracks[b_] = tracks[b_], tracks[a_]
                        tracks[a_]["lab"], tracks[b_]["lab"] = tracks[b_]["lab"], tracks[a_]["lab"]
                    if a_ in phys and b_ in phys: phys[a_]["wp"], phys[b_]["wp"] = phys[b_]["wp"], phys[a_]["wp"]
                    swaps_n += 1
                for cid in [c for c, t in tracks.items() if idx - t["last"] > max(10, int(fps))]:
                    del tracks[cid]

            if not cands and wide and zoom_m is not None:
                # no ball in this frame: look again at 2x zoom where it should be (the filter's prediction, else the
                # feet of the last player on the ball); a small, fast ball often only shows up when zoomed in
                at = None
                if ball.init and ball.lost <= BALL_RESET and ball.trail: at = ball.trail[-1]
                elif poss.owner is not None and poss.owner in tracks:
                    tb = tracks[poss.owner]["box"]; at = ((tb[0] + tb[2]) / 2, tb[3])
                if at is not None:
                    cw, ch = max(320, W // 4), max(180, H // 4)
                    x0 = int(min(max(0, at[0] - cw / 2), W - cw)); y0 = int(min(max(0, at[1] - ch / 2), H - ch))
                    try:
                        rz = zoom_m.predict(frame[y0:y0 + ch, x0:x0 + cw], imgsz=640, conf=BALL_MIN_CONF, half=HAS_GPU,
                                            device=DEVICE_TARGET, verbose=False, classes=sorted(ball_ids), max_det=5)[0]
                        zn[0] += 1
                        for (x1, y1, x2, y2), c in zip(rz.boxes.xyxy.cpu().numpy(), rz.boxes.conf.cpu().numpy()):
                            if (x2 - x1) <= 0.03 * W and (y2 - y1) <= 0.05 * H:
                                bx_, by_ = x0 + (x1 + x2) / 2, y0 + (y1 + y2) / 2
                                cands.append((bx_, by_, float(c), ball_over_pitch(gm, bx_, by_, H)))
                        if cands: zn[1] += 1
                    except Exception as e:
                        print("ball zoom failed, turned off:", e); zoom_m = None
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
                    # the ball's speed with the pitch's perspective: player height at the ball's row as the ruler
                    hb = ground.height(ball_pt[1]) or float(np.median([b_[3] - b_[1] for _, b_, _ in cur]))
                    step = bwp - bw["p"]
                    v = ground.metres(step[0], step[1], ball_pt[1], hb, BALL_DEPTH_POW) / (idx - bw["f"]) * fps * 3.6
                    if v < 160:
                        bw["v"] = 0.5 * bw["v"] + 0.5 * v
                        ball_max = max(ball_max, bw["v"]); ball_sum += bw["v"]; ball_n += 1
                        # a cross and the volley that follows it are two strikes a second apart: the next strike counts
                        # once the ball has slowed (controlled) or turned sharply (struck again), not after a fixed wait
                        if bw["v"] < STRIKE_REARM_KMH: bw["armed"] = True
                        elif not bw["armed"] and bw["dir"] is not None:
                            n_ = float(np.hypot(*step)) * float(np.hypot(*bw["dir"]))
                            if n_ > 0 and float(np.dot(step, bw["dir"])) < 0.5 * n_: bw["armed"] = True
                        # two fast readings in a row: a struck ball is only detected in a few frames of its flight
                        # (motion blur), too few for the smoothed speed to build up
                        if bw["armed"] and v > STRIKE_KMH and bw["raw"] > 0.7 * STRIKE_KMH:
                            bw["armed"] = False; bw["dir"] = step; strikes += 1
                            xev.append([idx, "strike", -1, poss.last_team if poss.last_team is not None else -1, round(max(v, bw["v"]), 1)])
                        bw["raw"] = v
                bw["p"] = bwp; bw["f"] = idx

            # ---- burned-in graphics ----
            if burn:
                for cid, b, lab in cur:
                    if cid != carrier and lab != "official": draw_marker(frame, b, kit[lab])   # officials stay unmarked
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
        score.close(); goals = [] if cancelled else score.goals(); kpr.close()
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
                ea = embacc.get(c); em = None
                if ea is not None and ea[1] >= 3: em = ea[0] / max(1e-9, float(np.linalg.norm(ea[0])))
                items[grp].append({"cid": c, "sig": acc_[0] / acc_[1], "emb": em, "w": float(acc_[1]), "t0": first_seen.get(c, 0),
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
        ma = None; pm_box = []
        try:
            ma = match_analytics(tmp_path, G, fps, W, H, poss.evlog, poss.events,
                                 {c: TEAM_CODE[l_] for c, l_ in team_info.items()}, real,
                                 [[e_[0], e_[3]] for e_ in xev if e_[1] == "strike"], pm_box, goals, kpr.H)
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
            "ids_split": splits_n, "ids_relinked": getattr(ident, "relinked", 0), "reid_embeddings": embedder.n, "ball_zoom_tries": zn[0], "ball_zoom_found": zn[1],
            "wide_frames": wide_frames, "wide_pct": round(100.0 * wide_frames / max(1, idx + 1), 1),
            "players": pl, "top_speeds": top_speeds,
            "sprint_kmh_a": round(spk[0][0] / spk[0][1] * 3.6, 1) if spk[0][1] else 0.0,
            "sprint_kmh_b": round(spk[1][0] / spk[1][1] * 3.6, 1) if spk[1][1] else 0.0,
        }
        if ma: stats.update(ma["team"])
        # fastest strike per team from the measured strikes (the kick tracker's pitch-space speed hit its 150 cap on
        # broadcast footage); readings over 130 km/h are glitches
        for t_, s_ in ((0, "a"), (1, "b")):
            v_ = [e_[4] for e_ in xev if e_[1] == "strike" and e_[3] == t_ and e_[4] <= 130]
            stats["fastest_strike_" + s_] = max(v_) if v_ else 0.0
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
                "maps": ma["maps"] if ma else None, "pitch": ma["pitch"] if ma else None,
                "home_kit": home_kit,
                "role_debug": {"ref_c": None if team.ref_c is None else [round(float(v), 3) for v in np.ravel(team.ref_c)],
                               "centroids": [[round(float(v), 3) for v in row] for row in np.asarray(team.centroids)] if team.ready else None,
                               "sep": round(float(getattr(team, "sep", 0.0)), 3),
                               "tracks": [[int(c), G(c), dict(team.roles[c]), int(team.nvotes[c]), int(team.noff[c]), int(team.nref[c]),
                                           [round(float(v), 1) for v in np.ravel(team.fsum.get(c, np.zeros(3)) / max(1, team.nvotes[c]))]]
                                          for c in sorted(team.roles, key=lambda c: -sum(team.roles[c].values()))[:80]]},
                "score": {"ocr": score.ok, "reads": len(score.reads), "sample": score.reads[:: max(1, len(score.reads) // 40)],
                          "goals": goals}}
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
        nums = player_numbers(real, team_info, appear)
        rest["player_no"] = {str(c): n for c, n in nums.items()}
        if burn and nums:
            J.update(phase="Adding player numbers to the video")
            try:
                burn_numbers(out_path, tmp_path, G, nums, kit, W, H, fps, src,
                             lambda q: J.update(phase=f"Adding player numbers to the video ({q * 100:.0f}%)"),
                             pm_box[0] if pm_box else None)
            except Exception:
                import traceback; traceback.print_exc()
                print("Could not add player numbers; the video keeps its rings only.")
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
# WHERE THE ANALYSIS RUNS: in this process by default (Docker, a GPU machine). modal_app.py sets WORKER, which runs it on
# an on-demand Modal GPU instead: start() hands a job over, sync() copies the worker's progress into JOB_STATE,
# commit() saves files to the volume the GPU worker shares with this server.
# ----------------------------------------------------------------------------
WORKER = None

def job_state():
    if WORKER: WORKER.sync(JOB_STATE)
    return JOB_STATE

# ----------------------------------------------------------------------------
# API
# ----------------------------------------------------------------------------
def range_streamer(path: Path, start: int, end: int, chunk: int = 2 * 1024 * 1024):
    with open(path, "rb") as f:
        f.seek(start); remaining = end - start + 1
        while remaining > 0:
            data = f.read(min(chunk, remaining))
            if not data: break
            remaining -= len(data); yield data

@app.get("/api/video-stream/{file_name}")
def stream_video(file_name: str, request: Request):
    return serve_mp4(PROCESSED_DIR / Path(file_name.split("?")[0]).name, request, {"Cache-Control": "no-cache"})

def serve_mp4(path: Path, request: Request, extra: dict):
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
            "Content-Length": str(end - start + 1), "Content-Type": "video/mp4", **extra})
    return StreamingResponse(range_streamer(path, 0, size - 1), headers={
        "Accept-Ranges": "bytes", "Content-Length": str(size), "Content-Type": "video/mp4", **extra})

@app.get("/api/tracking-data/{file_name}")
def get_tracking_data(file_name: str):
    path = PROCESSED_DIR / "tactical_metadata.json.gz"
    if not path.exists(): raise HTTPException(status_code=404, detail="Metadata not found")
    return Response(content=path.read_bytes(), media_type="application/json", headers={"Content-Encoding": "gzip"})

@app.get("/api/status")
def get_status():
    return JSONResponse(job_state())

@app.get("/api/summary")
def get_summary():
    # everything except the per-frame boxes: stats, team info, events, ID labels, perf. Used by the Trivela app.
    path = PROCESSED_DIR / "summary.json"
    if not path.exists(): raise HTTPException(status_code=404, detail="No finished analysis yet.")
    return Response(content=path.read_bytes(), media_type="application/json")

@app.get("/api/info")
def get_info():
    return WORKER.info() if WORKER else {"device": DEVICE_NAME, "gpu": HAS_GPU}

@app.post("/api/cancel")
def cancel():
    if WORKER: WORKER.cancel()
    else: JOB_CANCEL.set()
    return {"status": "cancelling"}

UPLOAD_LOCK = threading.Lock(); UPLOAD_RECV = set()
MAX_CHUNK_BYTES = 16 * 1024 * 1024    # the HUD sends 8 MB chunks, Trivela 4 MB
MAX_UPLOAD_BYTES = int(float(os.environ.get("TRIVELA_MAX_UPLOAD_GB", "20")) * 1024 ** 3)

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
    if (await asyncio.to_thread(job_state))["status"] in BUSY:
        return JSONResponse({"error": "An analysis is already running."}, status_code=409)
    if not (0 <= chunkIndex < totalChunks <= MAX_UPLOAD_BYTES // (1024 * 1024)):
        return JSONResponse({"error": "Invalid chunk index."}, status_code=400)
    data = await chunk.read(MAX_CHUNK_BYTES + 1)
    if len(data) > MAX_CHUNK_BYTES:
        return JSONResponse({"error": "Chunk too large."}, status_code=413)
    end = (offset if offset >= 0 else chunkIndex * 4 * 1024 * 1024) + len(data)
    if offset < -1 or end > MAX_UPLOAD_BYTES:
        return JSONResponse({"error": "The video is too large."}, status_code=413)
    ok = await asyncio.to_thread(_write_chunk, UPLOAD_DIR / "source_match.mp4", data, offset, chunkIndex, totalChunks)
    if not ok: return JSONResponse({"error": "Upload must start with chunk 0."}, status_code=400)
    return {"status": "ok", "index": chunkIndex}

def safe_local_path(name: str):
    allowed = [Path("/content").resolve(), Path.cwd().resolve()]
    p = Path(name.strip())
    cands = [p] if p.is_absolute() else [Path("/content") / p, Path.cwd() / p]
    for c in cands:
        try:
            c = c.resolve()
            if c.is_file() and any(c.is_relative_to(a) for a in allowed): return c
        except Exception:
            continue
    return None

@app.get("/api/upload-frame")
def upload_frame(t: float = -1.0):
    """A frame of the uploaded video (JPEG, at most 1280 wide) for picking the teams; t < 0 = a third of the way in.
    Headers: X-Duration (s) and X-Time (s) of the frame sent"""
    video = UPLOAD_DIR / "source_match.mp4"
    if not video.is_file(): raise HTTPException(status_code=404, detail="No uploaded video.")
    cap = cv2.VideoCapture(str(video))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0; n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        dur = n / fps if n > 0 else 0.0
        t = dur / 3 if t < 0 else min(max(0.0, t), max(0.0, dur - 0.5))
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0); ok, fr = cap.read()
    finally:
        cap.release()
    if not ok or fr is None: raise HTTPException(status_code=422, detail="Could not read the video.")
    if fr.shape[1] > 1280: fr = cv2.resize(fr, (1280, int(fr.shape[0] * 1280 / fr.shape[1])), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(jpg.tobytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "X-Duration": f"{dur:.2f}", "X-Time": f"{t:.2f}",
                             "Access-Control-Expose-Headers": "X-Duration, X-Time"})

def parse_kits(raw: str):
    """[{t, x, y, role}] from the team picker: x, y as shares of the frame, role home / away / ref"""
    if not raw: return None
    try: items = json.loads(raw)
    except ValueError: return None
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            k = {"t": float(it["t"]), "x": float(it["x"]), "y": float(it["y"]), "role": str(it["role"])}
        except (KeyError, TypeError, ValueError):
            continue
        if k["role"] in ("home", "away", "ref") and 0 <= k["x"] <= 1 and 0 <= k["y"] <= 1 and k["t"] >= 0: out.append(k)
    return out[:12] or None

@app.post("/api/start-analysis")
def start_analysis(background_tasks: BackgroundTasks, fileName: str = Form(...), targetRes: str = Form("1080p"),
                   mode: str = Form("balanced"), source: str = Form("upload"), burn: str = Form("1"), reid: str = Form("1"),
                   kits: str = Form("")):
    with JOB_LOCK:
        if job_state()["status"] in BUSY:
            return JSONResponse({"error": "An analysis is already running."}, status_code=409)
        if source == "local":
            video = safe_local_path(fileName)
            if video is None:
                return JSONResponse({"error": "File not found (must be inside /content or the working folder)."}, status_code=400)
        else:
            video = UPLOAD_DIR / "source_match.mp4"
            if not video.exists():
                return JSONResponse({"error": "Video file not found on server."}, status_code=400)
        JOB_CANCEL.clear()
        JOB_STATE.update(status="scanning", phase="Starting...", filename=video.name, total_frames=0, current_frame=0,
                         fps=0.0, progress=0.0, eta=0, video_url="", meta_url="", error_msg="")
        args = (str(video), mode, targetRes, burn != "0", reid != "0", parse_kits(kits))
        if WORKER: WORKER.start(*args)
        else: background_tasks.add_task(process_football_match, *args)
    return {"status": "started"}

# ----------------------------------------------------------------------------
# SAVED ANALYSES: the reports the Trivela app made, kept in the workspace volume so every browser sees them
# ----------------------------------------------------------------------------
ANALYSES_DIR = BASE_DIR / "analyses"
ANALYSES_DIR.mkdir(parents=True, exist_ok=True)
MAX_ANALYSIS_BYTES = 8 * 1024 * 1024
MAX_ANALYSES = 200
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
LIST_FIELDS = ("id", "home", "away", "hc", "ac", "source_name", "created_at", "status", "stats")

def analysis_path(match_id: str) -> Path:
    if not UUID_RE.match(match_id or ""): raise HTTPException(status_code=400, detail="Not a saved analysis id.")
    return ANALYSES_DIR / f"{match_id}.json"

def read_analysis(match_id: str) -> dict:
    path = analysis_path(match_id)
    if not path.is_file(): raise HTTPException(status_code=404, detail="That analysis was not found.")
    return json.loads(path.read_text(encoding="utf-8"))

def clean_analysis(match_id: str, row: Any) -> dict:
    if not isinstance(row, dict): raise HTTPException(status_code=400, detail="An analysis must be a JSON object.")
    text = lambda k, n: str(row.get(k) or "")[:n]
    color = lambda k: row.get(k) if isinstance(row.get(k), str) and COLOR_RE.match(row.get(k)) else None
    obj = lambda k: row.get(k) if isinstance(row.get(k), dict) else {}
    return {"id": match_id, "home": text("home", 60) or "Home", "away": text("away", 60) or "Away", "hc": color("hc"), "ac": color("ac"),
            "source_name": text("source_name", 200), "created_at": text("created_at", 40), "status": "ready",
            "stats": obj("stats"), "engine": obj("engine"), "context": row.get("context") if isinstance(row.get("context"), dict) else None,
            "players": [p for p in row.get("players") or [] if isinstance(p, dict)][:200] if isinstance(row.get("players"), list) else []}

@app.get("/api/analyses")
def list_analyses():
    rows = []
    for f in sorted(ANALYSES_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try: r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError): continue
        rows.append({**{k: r.get(k) for k in LIST_FIELDS}, "duration_s": (r.get("engine") or {}).get("duration_s")})
    return rows

@app.get("/api/analyses/{match_id}")
def get_analysis(match_id: str):
    return read_analysis(match_id)

VIDEOS_DIR = BASE_DIR / "videos"     # each saved analysis keeps its own annotated video (the newest MAX_VIDEOS)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
MAX_VIDEOS = 30

def keep_video(match_id: str):
    """On an analysis' first save: a copy of the video the engine just made, so the report can play it later"""
    src = PROCESSED_DIR / "tactical_output.mp4"
    if not src.is_file() or time.time() - src.stat().st_mtime > 6 * 3600: return False   # not this analysis' video
    dst = VIDEOS_DIR / f"{match_id}.mp4"
    tmp = dst.with_suffix(".tmp"); shutil.copyfile(src, tmp); tmp.replace(dst)
    for old in sorted(VIDEOS_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime)[:-MAX_VIDEOS]:
        old.unlink(missing_ok=True)
    return True

@app.get("/api/video/{match_id}")
def analysis_video(match_id: str, request: Request):
    """The annotated video of a saved analysis, with range requests; it never changes, so browsers may cache it"""
    if not UUID_RE.match(match_id or ""): raise HTTPException(status_code=400, detail="Not a saved analysis id.")
    link = WORKER.video_link(match_id) if WORKER and hasattr(WORKER, "video_link") else None
    if link: return RedirectResponse(link, status_code=302, headers={"Cache-Control": "private, max-age=3600"})   # from R2
    return serve_mp4(VIDEOS_DIR / f"{match_id}.mp4", request, {"Cache-Control": "public, max-age=604800, immutable"})

@app.put("/api/analyses/{match_id}")
async def save_analysis(match_id: str, request: Request):
    path = analysis_path(match_id)
    raw = await request.body()
    if len(raw) > MAX_ANALYSIS_BYTES: raise HTTPException(status_code=413, detail="That analysis is too big to save.")
    try: row = clean_analysis(match_id, json.loads(raw))
    except ValueError: raise HTTPException(status_code=400, detail="The analysis is not valid JSON.")
    first = not path.exists()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(row), encoding="utf-8"); tmp.replace(path)
    for old in sorted(ANALYSES_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)[:-MAX_ANALYSES]:
        old.unlink(missing_ok=True)
    if first and await asyncio.to_thread(keep_video, match_id) and WORKER and hasattr(WORKER, "store_video"):
        await asyncio.to_thread(WORKER.store_video, match_id)
    if WORKER: await asyncio.to_thread(WORKER.commit)
    return {"status": "saved", "id": match_id}

@app.delete("/api/analyses/{match_id}")
async def delete_analysis(match_id: str):
    """Removes a saved analysis, its video copy here and its video on R2"""
    path = analysis_path(match_id)
    if not path.is_file(): raise HTTPException(status_code=404, detail="That analysis was not found.")
    path.unlink(missing_ok=True)
    (VIDEOS_DIR / f"{match_id}.mp4").unlink(missing_ok=True)
    if WORKER:
        if hasattr(WORKER, "delete_video"): await asyncio.to_thread(WORKER.delete_video, match_id)
        await asyncio.to_thread(WORKER.commit)
    return {"status": "deleted", "id": match_id}

# ----------------------------------------------------------------------------
# AI COACH: forwarded to the coach service (trivela-frontend/coach), which holds the model API key.
# The browser names a saved analysis; the match data is read here, so a visitor can't feed the coach made-up numbers.
# ----------------------------------------------------------------------------
COACH_URL = os.environ.get("TRIVELA_COACH_URL", "").rstrip("/")   # http://coach:5600 in docker-compose
MAX_COACH_REQUEST = 4096
COACH_ENGINE_FIELDS = ("duration_s", "ball", "kit_learned", "swaps_fixed")

def coach_reply(status: int, error: str):
    return JSONResponse({"success": False, "data": None, "error": error}, status_code=status)

def _forward_to_coach(payload: bytes, visitor: str):
    req = urllib.request.Request(COACH_URL + "/api/coach", data=payload, method="POST",
                                 headers={"Content-Type": "application/json", "X-Visitor": visitor})
    try:
        with urllib.request.urlopen(req, timeout=150) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()
    except OSError as e:
        print("AI coach unreachable:", e)
        return 503, json.dumps({"success": False, "data": None, "error": "The AI coach is offline right now."}).encode()

@app.post("/api/coach")
async def coach(request: Request):
    if not COACH_URL: return coach_reply(503, "The AI coach is not set up on this server.")
    raw = await request.body()
    if len(raw) > MAX_COACH_REQUEST: return coach_reply(413, "That request is too big.")
    try: body = json.loads(raw)
    except ValueError: return coach_reply(400, "The request is not valid JSON.")
    if not isinstance(body, dict): return coach_reply(400, "The request is not valid JSON.")
    try: row = read_analysis(str(body.get("match_id") or ""))
    except HTTPException as e: return coach_reply(e.status_code, e.detail)
    engine = row.get("engine") or {}
    match = {"home": row.get("home"), "away": row.get("away"), "stats": row.get("stats"), "players": row.get("players"),
             "context": row.get("context"), "engine": {k: engine.get(k) for k in COACH_ENGINE_FIELDS}}
    payload = json.dumps({k: body.get(k) for k in ("match_id", "feature", "question", "chat_id", "refresh")} | {"match": match}).encode()
    # Cloudflare's header when the engine is reached through the tunnel, otherwise the direct caller
    visitor = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
    status, data = await asyncio.to_thread(_forward_to_coach, payload, visitor)
    return Response(content=data, status_code=status, media_type="application/json")

# ----------------------------------------------------------------------------
# PAGES: the engine's HUD at /, the Trivela site (trivela-frontend/landing, copied in as web/) at /web
# ----------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
HUD_HTML = (APP_DIR / "hud.html").read_text(encoding="utf-8")
WEB_DIR = APP_DIR / "web"

@app.get("/", response_class=HTMLResponse)
def get_hud():
    return HUD_HTML

if (WEB_DIR / "app.html").is_file():
    @app.get("/web/assets/js/config.js")
    def web_config():
        return Response("window.TRIVELA_CONFIG = { engineUrl: location.origin };", media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})
    # clean page addresses, as on the Vercel site (vercel.json)
    @app.get("/web/analysis", response_class=HTMLResponse)
    def web_analysis():
        return (WEB_DIR / "app.html").read_text(encoding="utf-8")

    @app.get("/web/dashboard", response_class=HTMLResponse)
    def web_dashboard():
        return (WEB_DIR / "dashboard.html").read_text(encoding="utf-8")
    app.mount("/web", StaticFiles(directory=WEB_DIR, html=True), name="web")

@app.get("/trivela")
def get_trivela():
    return RedirectResponse("/web/")
