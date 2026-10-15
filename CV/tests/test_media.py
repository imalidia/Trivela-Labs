"""Tracker setup against the installed Ultralytics, and the AV1/VP9 -> H.264 conversion, with the real libraries.
Needs opencv, ultralytics (and torch) and an ffmpeg that can encode AV1/VP9; skipped when they aren't installed."""
import os, shutil, subprocess, sys
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("ultralytics")
from ultralytics.engine.results import Boxes

HERE = os.path.dirname(__file__)

def ffmpeg_can(encoder):
    if not shutil.which("ffmpeg"): return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return f" {encoder} " in out

@pytest.fixture(scope="module")
def app(tmp_path_factory):
    work = tmp_path_factory.mktemp("engine")
    for f in ("app.py", "hud.html"): shutil.copy(os.path.join(HERE, "..", f), work)
    cwd = os.getcwd(); os.chdir(work); sys.path.insert(0, str(work))
    import app as engine
    yield engine
    os.chdir(cwd); sys.path.remove(str(work)); sys.modules.pop("app", None)

def boxes(rows, shape=(360, 640)):
    return Boxes(np.array(rows, dtype=np.float32), shape)

def test_builds_bot_sort_on_the_installed_ultralytics(app):
    tracker, name = app.make_tracker(25)
    assert name.startswith("BoT-SORT")
    # 150 frames at 30 fps == 125 at 25 fps: same time before a lost player is dropped, whichever API is installed
    assert getattr(tracker, "max_frames_lost", None) == 125 or getattr(tracker, "max_time_lost", None) == 125
    frame = np.full((360, 640, 3), 90, np.uint8)
    ids = []
    for dx in range(0, 12, 2):   # two players walking right
        out = tracker.update(boxes([[100 + dx, 100, 130 + dx, 180, 0.9, 1], [400 + dx, 120, 430 + dx, 200, 0.9, 1]]), frame)
        ids.append(sorted(int(r[4]) for r in out))
    assert len(set(ids[-1])) == 2 and all(i == ids[-1] for i in ids[2:])

def test_passes_frame_rate_to_trackers_that_take_it(app):
    class OldTracker:
        def __init__(self, args, frame_rate=30): self.args, self.frame_rate = args, frame_rate
    t = app.build_tracker(OldTracker, {"track_buffer": 150}, 25)
    assert t.frame_rate == 25 and t.args.track_buffer == 150

@pytest.mark.parametrize("encoder,ext,pix", [("libsvtav1", "mp4", "yuv420p10le"), ("libvpx-vp9", "webm", "yuv420p")])
def test_converts_youtube_codecs_to_h264(app, tmp_path, encoder, ext, pix):
    if not ffmpeg_can(encoder): pytest.skip(f"ffmpeg has no {encoder}")
    src = str(tmp_path / f"in.{ext}")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=2",
                    "-c:v", encoder, "-pix_fmt", pix, src], check=True)
    out = app.to_h264(src, str(tmp_path / "out.mp4"), 360)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,height",
                            "-of", "csv=p=0", out], capture_output=True, text=True).stdout.strip()
    assert probe == "h264,360"
    cap = cv2.VideoCapture(out)
    ok, frame = cap.read(); cap.release()
    assert ok and frame.shape[:2] == (360, 640)

def test_conversion_failure_is_reported(app, tmp_path):
    bad = tmp_path / "bad.mp4"; bad.write_bytes(b"not a video")
    with pytest.raises(RuntimeError, match="converting it failed"):
        app.to_h264(str(bad), str(tmp_path / "out.mp4"))
