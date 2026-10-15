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
