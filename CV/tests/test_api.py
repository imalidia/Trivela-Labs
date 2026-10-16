"""HTTP tests for app.py's saved analyses, AI coach forwarding and pages, with the video libraries stubbed out.
Needs fastapi and httpx (pip install fastapi httpx python-multipart numpy pyyaml); skipped when they aren't installed."""
import json, os, shutil, sys, threading, types
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest

pytest.importorskip("fastapi"); pytest.importorskip("httpx")
from fastapi.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
ID = "11111111-2222-4333-8444-555555555555"
ROW = {"home": "A <b>", "away": "B", "hc": "red;x", "ac": "#123456", "stats": {"Possession": [55, 45]},
       "players": [{"player_key": "t1", "team": "home"}], "engine": {"duration_s": 600, "maps": {"big": 1}},
       "context": {"events": []}, "created_at": "2026-10-14T10:00:00Z", "extra": "dropped"}

class FakeCoach(BaseHTTPRequestHandler):
    seen = {}
    def do_POST(self):
        FakeCoach.seen = {"body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))), "visitor": self.headers.get("X-Visitor")}
        out = json.dumps({"success": True, "data": {"text": "ok"}, "error": None}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)
    def log_message(self, *args): pass

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    for name in ("cv2", "torch", "ultralytics", "ultralytics.utils"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["torch"].cuda = types.SimpleNamespace(is_available=lambda: False, get_device_name=lambda i: "")
    sys.modules["ultralytics"].YOLO = object
    sys.modules["ultralytics.utils"].IterableSimpleNamespace = object
    coach = HTTPServer(("127.0.0.1", 0), FakeCoach)
    threading.Thread(target=coach.serve_forever, daemon=True).start()
    work = tmp_path_factory.mktemp("engine")
    for f in ("app.py", "hud.html"): shutil.copy(os.path.join(HERE, "..", f), work)
    cwd = os.getcwd(); os.chdir(work); sys.path.insert(0, str(work))
    os.environ["TRIVELA_COACH_URL"] = f"http://127.0.0.1:{coach.server_port}"
    import app
    yield TestClient(app.app)
    os.chdir(cwd); sys.path.remove(str(work)); sys.modules.pop("app", None); coach.shutdown()

def test_saves_lists_and_cleans_analyses(client):
    assert client.put(f"/api/analyses/{ID}", json=ROW).status_code == 200
    [row] = client.get("/api/analyses").json()
    assert row["id"] == ID and row["duration_s"] == 600 and "players" not in row
    full = client.get(f"/api/analyses/{ID}").json()
    assert full["hc"] is None and full["ac"] == "#123456" and "extra" not in full

def test_refuses_bad_ids_bodies_and_other_sites(client):
    assert client.put("/api/analyses/not-a-uuid", json=ROW).status_code == 400
    assert client.get("/api/analyses/..%2F..%2Fapp.py").status_code in (400, 404)
    assert client.put(f"/api/analyses/{ID}", content=b"[1,2]", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.put(f"/api/analyses/{ID}", json=ROW, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.put(f"/api/analyses/{ID}", json=ROW, headers={"Origin": "http://localhost:5500"}).status_code == 200

def test_coach_gets_the_stored_match_not_the_browsers(client):
    client.put(f"/api/analyses/{ID}", json=ROW)
    r = client.post("/api/coach", json={"match_id": ID, "feature": "report", "match": {"home": "forged"}})
    assert r.status_code == 200 and r.json()["data"] == {"text": "ok"}
    sent = FakeCoach.seen["body"]
    assert sent["match"]["home"] == "A <b>" and sent["match"]["engine"] == {"duration_s": 600, "ball": None, "kit_learned": None, "swaps_fixed": None}
    assert FakeCoach.seen["visitor"] == "testclient"

def test_coach_errors(client):
    assert client.post("/api/coach", json={"match_id": "11111111-2222-4333-8444-000000000000", "feature": "report"}).status_code == 404
    assert client.post("/api/coach", content=b"x" * 5000).status_code == 413

def test_pages(client):
    assert "<html" in client.get("/").text.lower()
    assert client.get("/trivela", follow_redirects=False).headers["location"] == "/web/"
    assert client.get("/docs").status_code == 404

def test_rebuilds_an_upload_sent_out_of_order(client):
    # the site sends chunk 0 first, then several chunks at once, each with its byte offset
    size = 4
    data = bytes(range(256)) * 64                       # 16 KB of varied bytes
    parts = [data[i:i + size * 1024] for i in range(0, len(data), size * 1024)]
    send = lambda i: client.post("/api/upload-chunk", files={"chunk": ("c", parts[i])},
                                 data={"fileName": "m.mp4", "chunkIndex": i, "totalChunks": len(parts), "offset": i * size * 1024})
    assert send(0).status_code == 200
    for i in (3, 1, 2):
        assert send(i).status_code == 200
    assert client.get("/api/status").json()["status"] == "uploaded"
    from app import UPLOAD_DIR
    assert (UPLOAD_DIR / "source_match.mp4").read_bytes() == data

class FakeWorker:
    """Stands in for modal_app.ModalWorker: the analysis runs elsewhere and its progress is copied in."""
    def __init__(self): self.started, self.cancelled, self.commits, self.remote = None, 0, 0, None
    def info(self): return {"device": "NVIDIA A10 (test)", "gpu": True}
    def start(self, *args): self.started = args
    def cancel(self): self.cancelled += 1
    def commit(self): self.commits += 1
    def sync(self, J):
        if self.remote: J.update(self.remote)

def test_hands_analyses_to_a_remote_worker(client):
    import app
    w = app.WORKER = FakeWorker()
    try:
        assert client.get("/api/info").json() == {"device": "NVIDIA A10 (test)", "gpu": True}
        (app.UPLOAD_DIR / "source_match.mp4").write_bytes(b"video")
        r = client.post("/api/start-analysis", data={"fileName": "source_match.mp4", "mode": "fast", "targetRes": "720p"})
        assert r.status_code == 200 and w.started[1:] == ("fast", "720p", True, True)
        assert w.started[0].endswith("source_match.mp4")
        w.remote = {"status": "processing", "progress": 42.0}
        assert client.get("/api/status").json()["progress"] == 42.0
        assert client.post("/api/start-analysis", data={"fileName": "source_match.mp4"}).status_code == 409
        client.post("/api/cancel"); assert w.cancelled == 1
        client.put(f"/api/analyses/{ID}", json=ROW); assert w.commits == 1
    finally:
        app.WORKER = None; app.JOB_STATE.update(status="idle")

class FakeWorker:
    """Stands in for modal_app.ModalWorker: the analysis runs elsewhere and its progress is copied in."""
    def __init__(self): self.started, self.cancelled, self.commits, self.remote = None, 0, 0, None
    def info(self): return {"device": "NVIDIA A10 (test)", "gpu": True}
    def start(self, *args): self.started = args
    def cancel(self): self.cancelled += 1
    def commit(self): self.commits += 1
    def sync(self, J):
        if self.remote: J.update(self.remote)

def test_hands_analyses_to_a_remote_worker(client):
    import app
    w = app.WORKER = FakeWorker()
    try:
        assert client.get("/api/info").json() == {"device": "NVIDIA A10 (test)", "gpu": True}
        (app.UPLOAD_DIR / "source_match.mp4").write_bytes(b"video")
        r = client.post("/api/start-analysis", data={"fileName": "source_match.mp4", "mode": "fast", "targetRes": "720p"})
        assert r.status_code == 200 and w.started[1:] == ("fast", "720p", True, True)
        assert w.started[0].endswith("source_match.mp4")
        w.remote = {"status": "processing", "progress": 42.0}
        assert client.get("/api/status").json()["progress"] == 42.0
        assert client.post("/api/start-analysis", data={"fileName": "source_match.mp4"}).status_code == 409
        client.post("/api/cancel"); assert w.cancelled == 1
        client.put(f"/api/analyses/{ID}", json=ROW); assert w.commits == 1
    finally:
        app.WORKER = None; app.JOB_STATE.update(status="idle")

def test_score_reader_turns_score_changes_into_goals(client):
    import app
    sr = app.ScoreReader.__new__(app.ScoreReader)
    # a misread first frame, 0-0, a one-off misread, two goals for the left side, a goal ruled out (shown for a while),
    # one for the right; then a 1 misread as 0 twice, which is no goal ruled out
    sr.reads = [(0, 5, 2), (50, 0, 0), (100, 0, 0), (150, 7, 0), (200, 1, 0), (250, 1, 0), (300, 2, 0), (350, 2, 0),
                (400, 1, 0), (450, 1, 0), (500, 1, 0), (550, 1, 0), (600, 1, 0), (650, 1, 1), (700, 1, 1),
                (750, 1, 0), (800, 1, 0), (850, 1, 1), (900, 1, 1)]
    assert sr.goals() == [(200, 0), (650, 1)]
    assert app._score_of("05:04 ROM 0-0 INT") == (0, 0) and app._score_of("32:07") is None

