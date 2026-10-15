"""Accuracy checks for the engine on Modal, on a fixed test clip, run by .github/workflows/eval-modal.yml.

  modal run CV/modal_eval.py                         snapshot of the last analysis made on the site
  modal run CV/modal_eval.py --rerun balanced        analyse the test clip again with this code (A10), then snapshot it
  --times 10,95.5                                    also grab full-size frames of the processed video at these seconds

The test clip is a copy of the first video found in the site's uploads, kept at /data/eval/clip.mp4 so later uploads
don't replace it. Reruns write to /data/eval/workspace, never over the site's own results.
Output (written to --out): summary.json, the per-frame tracking data, a contact sheet and the requested frames.
"""
import json, shutil, subprocess, sys
from pathlib import Path

import modal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modal_app import image, volume, DATA, GPU, load_engine

app = modal.App("trivela-eval", image=image.add_local_python_source("modal_app"))   # rerun imports it in the container
CLIP = Path(DATA) / "eval" / "clip.mp4"
WORKSPACES = {"live": Path(DATA) / "workspace", "eval": Path(DATA) / "eval" / "workspace"}


@app.function(gpu=GPU, cpu=8.0, memory=16384, timeout=2 * 60 * 60, volumes={DATA: volume},
              env={"TRIVELA_WORKSPACE": str(WORKSPACES["eval"])})
def rerun(mode: str, res: str) -> dict:
    volume.reload()
    engine = load_engine()
    engine.process_football_match(str(CLIP), mode, res, True, True)
    volume.commit()
    return {k: v for k, v in engine.JOB_STATE.items()}


@app.function(cpu=4.0, memory=8192, timeout=30 * 60, volumes={DATA: volume})
def snapshot(source: str, times: list) -> dict:
    volume.reload()
    live_upload = WORKSPACES["live"] / "uploads" / "source_match.mp4"
    if not CLIP.exists() and live_upload.exists():
        CLIP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(live_upload, CLIP)
        volume.commit()
    out = {}
    processed = WORKSPACES[source] / "processed"
    for name in ("summary.json", "tactical_metadata.json.gz"):
        if (processed / name).exists(): out[name] = (processed / name).read_bytes()
    video = processed / "tactical_output.mp4"
    if video.exists():
        tmp = Path("/tmp/frames"); shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir()
        # one frame every 10 s, 6 x 6 per sheet
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", "fps=1/10,scale=480:-2,tile=6x6",
                        "-q:v", "4", str(tmp / "sheet_%02d.jpg")], check=False)
        for t in times:
            subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=1280:-2", "-q:v", "3", str(tmp / f"frame_{t:07.2f}.jpg")], check=False)
        for f in sorted(tmp.glob("*.jpg")): out[f.name] = f.read_bytes()
    out["clip.txt"] = (f"clip: {CLIP} ({CLIP.stat().st_size} bytes)" if CLIP.exists() else "no test clip yet").encode()
    return out


@app.local_entrypoint()
def main(rerun_mode: str = "", res: str = "1080p", times: str = "", out: str = "eval-out"):
    state = rerun.remote(rerun_mode, res) if rerun_mode else None
    files = snapshot.remote("eval" if rerun_mode else "live", [float(t) for t in times.split(",") if t.strip()])
    dest = Path(out); dest.mkdir(parents=True, exist_ok=True)
    for name, data in files.items(): (dest / name).write_bytes(data)
    if state is not None: (dest / "job_state.json").write_text(json.dumps(state, indent=1))
    print("wrote", sorted(files), "to", dest)
