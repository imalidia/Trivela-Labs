"""The final video pass (IDs burned in above the players, officials hidden) and the referee kit model, with real
OpenCV and ffmpeg but torch / Ultralytics stubbed out. Skipped when OpenCV or ffmpeg isn't installed."""
import json, os, shutil, sys, types
import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))

@pytest.fixture(scope="module")
def app(tmp_path_factory):
    if not shutil.which("ffmpeg"): pytest.skip("ffmpeg is not installed")
    if "cv2" in sys.modules and not hasattr(sys.modules["cv2"], "VideoCapture"): del sys.modules["cv2"]   # another test's stub
    pytest.importorskip("cv2"); pytest.importorskip("yaml")
    stubs = {}
    for name in ("torch", "ultralytics", "ultralytics.utils"):
        if name not in sys.modules: stubs[name] = sys.modules[name] = types.ModuleType(name)
    if "torch" in stubs: sys.modules["torch"].cuda = types.SimpleNamespace(is_available=lambda: False, get_device_name=lambda i: "")
    if "ultralytics" in stubs: sys.modules["ultralytics"].YOLO = object
    if "ultralytics.utils" in stubs: sys.modules["ultralytics.utils"].IterableSimpleNamespace = object
    work = tmp_path_factory.mktemp("engine")
    for f in ("app.py", "hud.html"): shutil.copy(os.path.join(HERE, "..", f), work)
    cwd = os.getcwd(); os.chdir(work); sys.path.insert(0, str(work)); sys.modules.pop("app", None)
    import app as engine
    yield engine
    os.chdir(cwd); sys.path.remove(str(work)); sys.modules.pop("app", None)
    for name in stubs: sys.modules.pop(name, None)

def make_clip(cv2, path, n=12, size=(320, 240)):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, size)
    for _ in range(n): w.write(np.full((size[1], size[0], 3), (40, 140, 40), np.uint8))
    w.release()

def test_burns_in_ids_and_hides_officials(app, tmp_path):
    cv2 = app.cv2
    src, out, log = tmp_path / "in.mp4", tmp_path / "out.mp4", tmp_path / "log.jsonl"
    make_clip(cv2, src)
    with open(log, "w") as f:
        for i in range(12):
            rec = {"p": [[5, 60, 100, 90, 180, 0], [7, 200, 100, 230, 180, 0]], "c": -1, "b": None, "q": [0, 0]}
            f.write(f"{i}\t{json.dumps(rec)}\n")
    kit = dict(app.DEFAULT_BGR)
    ids = {5: (1, 0), 7: (None, 3)}         # 5 is listed as ID 1; 7 turned out to be the referee
    name, _ = app.render_video(str(src), str(out), (320, 240), 25.0, str(log), ids, kit, True)
    cap = cv2.VideoCapture(str(out)); frames = []
    while True:
        ok, fr = cap.read()
        if not ok: break
        frames.append(fr.astype(int))
    assert len(frames) == 12
    base = np.array([40, 140, 40])
    diff = lambda fr, y0, y1, x0, x1: np.abs(fr[y0:y1, x0:x1] - base).sum(axis=2).max()
    fr = frames[6]
    assert diff(fr, 170, 192, 50, 100) > 120          # ring under the player
    assert diff(fr, 70, 98, 55, 95) > 120             # his ID tag above the head
    assert diff(fr, 60, 192, 185, 245) < 60           # nothing drawn on the referee

def test_marker_draws_the_number(app):
    blank = np.zeros((240, 320, 3), np.uint8)
    a = blank.copy(); app.draw_marker(a, (100, 100, 130, 180), (60, 60, 225))
    b = blank.copy(); app.draw_marker(b, (100, 100, 130, 180), (60, 60, 225), label=7)
    assert not a[60:98].any() and b[60:98].any()

def lab_bgr(cv2, bgr):
    return cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2LAB).reshape(3).astype(np.float32)

def test_referee_kit_is_found_and_labelled_official(app):
    cv2 = app.cv2; rng = np.random.default_rng(1)
    red, white, yellow, gk = (40, 40, 200), (235, 235, 235), (20, 230, 240), (200, 60, 200)
    red_shade, white_shade = (20, 20, 110), (150, 150, 150)       # kits in shadow are not referees, even seen more often
    tm = app.TeamModel(None)
    for colour, n in ((red, 200), (white, 200), (yellow, 22), (gk, 8), (red_shade, 25), (white_shade, 25)):
        c = lab_bgr(cv2, colour)
        tm.samples += [c + rng.normal(0, 3, 3).astype(np.float32) for _ in range(n)]
    tm.fit()
    assert tm.ref is not None
    assert tm.nearest(lab_bgr(cv2, yellow)) == "official"
    assert tm.nearest(lab_bgr(cv2, red)) == "team_a" and tm.nearest(lab_bgr(cv2, white)) == "team_b"
    assert tm.nearest(lab_bgr(cv2, red_shade)) == "team_a" and tm.nearest(lab_bgr(cv2, white_shade)) == "team_b"
    assert tm.nearest(lab_bgr(cv2, gk)) != "official"

def test_no_referee_cluster_without_one(app):
    cv2 = app.cv2; rng = np.random.default_rng(2)
    tm = app.TeamModel(None)
    for colour, n in (((40, 40, 200), 200), ((235, 235, 235), 200), ((20, 20, 110), 25), ((150, 150, 150), 25)):
        c = lab_bgr(cv2, colour)
        tm.samples += [c + rng.normal(0, 4, 3).astype(np.float32) for _ in range(n)]
    tm.fit()
    assert tm.ref is None
