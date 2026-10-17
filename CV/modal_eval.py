"""Accuracy checks for the engine on Modal, on a fixed test clip, run by .github/workflows/eval-modal.yml.

  modal run CV/modal_eval.py                         snapshot of the last analysis made on the site
  modal run CV/modal_eval.py --rerun balanced        analyse the test clip again with this code (A10), then snapshot it
  --times 10,95.5                                    also grab full-size frames of the processed video at these seconds
  --keep tactical.mp4 / --clip tactical.mp4          save the site's last upload as a test clip / rerun on that clip

The test clip is a copy of the first video found in the site's uploads, kept at /data/eval/clip.mp4 so later uploads
don't replace it. Reruns write to /data/eval/workspace, never over the site's own results.
Output (written to --out): summary.json, the per-frame tracking data, a contact sheet and the requested frames.
"""
import json, os, shutil, subprocess, sys
from pathlib import Path

import modal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from modal_app import image, volume, DATA, GPU, load_engine, r2_secret, r2_client, r2_key

app = modal.App("trivela-eval", image=image.add_local_python_source("modal_app"))   # rerun imports it in the container
CLIP = Path(DATA) / "eval" / "clip.mp4"
WORKSPACES = {"live": Path(DATA) / "workspace", "eval": Path(DATA) / "eval" / "workspace"}


@app.function(gpu=GPU, cpu=8.0, memory=16384, timeout=2 * 60 * 60, volumes={DATA: volume},
              env={"TRIVELA_WORKSPACE": str(WORKSPACES["eval"])})
def rerun(mode: str, res: str, clip: str = "clip.mp4", kits: list = None) -> dict:
    volume.reload()
    engine = load_engine()
    engine.process_football_match(str(CLIP.parent / clip), mode, res, True, True, kits)
    volume.commit()
    return {k: v for k, v in engine.JOB_STATE.items()}


@app.function(cpu=4.0, memory=8192, timeout=30 * 60, volumes={DATA: volume})
def snapshot(source: str, times: list, export: bool = False, keep: str = "", export_clip: str = "") -> dict:
    volume.reload()
    live_upload = WORKSPACES["live"] / "uploads" / "source_match.mp4"
    if keep and live_upload.exists():               # keep the site's last upload as another test clip
        shutil.copy(live_upload, CLIP.parent / keep); volume.commit()
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
    if export:                                   # the raw clip and the detector weights, for local CPU test runs
        src = CLIP.parent / (export_clip or CLIP.name)
        if src.exists():                         # re-encoded so it stays under GitHub's 100 MB file limit
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "24", "-an", "/tmp/export.mp4"], check=True)
            out["clip.mp4"] = Path("/tmp/export.mp4").read_bytes()
        out["yolo-football-player-detection.pt"] = Path("/app/yolo-football-player-detection.pt").read_bytes()
    ad = WORKSPACES["live"] / "analyses"           # the site's saved reports: name, size, time
    if ad.is_dir():
        import time as _t
        rows = sorted(ad.glob("*.json"), key=lambda f: f.stat().st_mtime)
        out["analyses.txt"] = "\n".join(f"{f.name} {f.stat().st_size} {_t.strftime('%Y-%m-%d %H:%M:%S', _t.gmtime(f.stat().st_mtime))}" for f in rows).encode()
    up = WORKSPACES["live"] / "uploads" / "source_match.mp4"
    if up.exists(): out["upload.txt"] = f"{up.stat().st_size} {up.stat().st_mtime}".encode()
    out["clip.txt"] = (f"clip: {CLIP} ({CLIP.stat().st_size} bytes)" if CLIP.exists() else "no test clip yet").encode()
    return out


@app.function(cpu=2.0, timeout=10 * 60)
def hf_search(queries: list) -> dict:
    """Candidate pitch-keypoint models on Hugging Face: id, downloads, likes, licence, weight files"""
    from huggingface_hub import HfApi
    api, out = HfApi(), {}
    for q in queries:
        rows = []; q, _, n = q.partition(":")
        for m in api.list_models(search=q, sort="downloads", limit=int(n or 25)):
            try:
                info = api.model_info(m.id, files_metadata=True)
                files = [(f.rfilename, f.size) for f in info.siblings if f.rfilename.endswith((".pt", ".onnx", ".pth", ".safetensors", ".md", ".yaml"))]
                lic = next((t for t in (info.tags or []) if t.startswith("license:")), None)
            except Exception as e:
                files, lic = [str(e)[:100]], None
            rows.append({"id": m.id, "downloads": getattr(m, "downloads", None), "likes": getattr(m, "likes", None),
                         "license": lic, "files": files[:12]})
        out[q] = rows
    return out


@app.function(gpu=GPU, cpu=4.0, memory=16384, timeout=30 * 60, volumes={DATA: volume})
def kp_probe(repo: str, clip: str, times: list) -> dict:
    """Try a pitch-keypoint model from Hugging Face on frames of a test clip: its model card, the raw keypoints and
    the frames with the keypoints drawn and numbered"""
    import cv2
    from huggingface_hub import snapshot_download
    from ultralytics import YOLO
    d = Path(snapshot_download(repo, allow_patterns=["*.pt", "*.md", "*.yaml", "*.json", "*.txt"]))
    out = {"files.txt": "\n".join(str(f.relative_to(d)) + f" {f.stat().st_size}" for f in d.rglob("*") if f.is_file()).encode()}
    for f in d.rglob("*.md"): out["card_" + f.name] = f.read_bytes()
    for f in d.rglob("*.yaml"): out["cfg_" + f.name] = f.read_bytes()
    pts = sorted(d.rglob("*.pt"), key=lambda f: -f.stat().st_size)
    if not pts: return out
    m = YOLO(str(pts[0])); res = {"weights": str(pts[0].relative_to(d)), "names": m.names, "frames": {}}
    cap = cv2.VideoCapture(str(CLIP.parent / clip))
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000); ok, fr = cap.read()
        if not ok: continue
        r = m.predict(fr, imgsz=640, conf=0.25, verbose=False)[0]
        k = r.keypoints
        xy = k.xy.cpu().numpy().tolist() if k is not None else []
        cf = k.conf.cpu().numpy().tolist() if k is not None and k.conf is not None else []
        res["frames"][str(t)] = {"boxes": r.boxes.data.cpu().numpy().tolist(), "xy": xy, "conf": cf}
        for inst in range(len(xy)):
            for j, (x, y) in enumerate(xy[inst]):
                c = cf[inst][j] if cf else 1.0
                if c < 0.5 or (x == 0 and y == 0): continue
                cv2.circle(fr, (int(x), int(y)), 6, (0, 0, 255), -1)
                cv2.putText(fr, str(j), (int(x) + 6, int(y) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
        ok, jpg = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 80]); out[f"kp_{t:07.2f}.jpg"] = jpg.tobytes()
    out["kp.json"] = json.dumps(res).encode()
    return out


@app.function(cpu=2.0, timeout=30 * 60, volumes={DATA: volume}, secrets=[r2_secret])
def r2check() -> dict:
    """Uploads the newest saved video to R2 and plays the signed link back: proves the keys and the bucket work"""
    import urllib.request
    volume.reload()
    vids = sorted((Path(DATA) / "workspace" / "videos").glob("*.mp4"), key=lambda f: f.stat().st_mtime)
    if not vids: vids = [Path(DATA) / "workspace" / "processed" / "tactical_output.mp4"]
    v = vids[-1]; mid = v.stem if v.parent.name == "videos" else "r2-check"; c = r2_client()
    c.upload_file(str(v), os.environ["R2_BUCKET"], r2_key(mid), ExtraArgs={"ContentType": "video/mp4"})
    url = c.generate_presigned_url("get_object", Params={"Bucket": os.environ["R2_BUCKET"], "Key": r2_key(mid)}, ExpiresIn=600)
    r = urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=0-1023"}), timeout=30)
    return {"r2.txt": f"uploaded {mid} ({v.stat().st_size} bytes); signed link answered {r.status}, {len(r.read())} bytes".encode()}


@app.function(cpu=1.0, timeout=10 * 60, volumes={DATA: volume}, secrets=[r2_secret])
def r2ls(delete: list = None) -> dict:
    """The videos in R2, oldest first, with the match each belongs to; deletes the given keys first"""
    volume.reload()
    c = r2_client(); b = os.environ["R2_BUCKET"]; log = []
    for k in delete or []:
        c.delete_object(Bucket=b, Key=k); log.append(f"deleted {k}")
    objs = sorted(c.list_objects_v2(Bucket=b).get("Contents", []), key=lambda o: o["LastModified"])
    ad = Path(DATA) / "workspace" / "analyses"
    def name(k):
        f = ad / f"{Path(k).stem}.json"
        if not f.is_file(): return "(no saved analysis)"
        r = json.loads(f.read_text()); return f"{r.get('home')} vs {r.get('away')}"
    rows = [f"{o['LastModified']:%Y-%m-%d %H:%M} {o['Size'] / 1e6:7.1f} MB {o['Key']} {name(o['Key'])}" for o in objs]
    return {"r2ls.txt": "\n".join(log + rows + [f"total {sum(o['Size'] for o in objs) / 1e9:.2f} GB in {len(objs)} videos"]).encode()}


@app.local_entrypoint()
def main(rerun_mode: str = "", res: str = "1080p", times: str = "", out: str = "eval-out", export: bool = False, hf: str = "",
         clip: str = "clip.mp4", keep: str = "", kp: str = "", kits: str = "", r2: bool = False, r2list: bool = False, r2del: str = ""):
    # kits: team-picker clicks as role:t:x:y,... (x, y as shares of the frame)
    kl = [{"role": r, "t": float(t), "x": float(x), "y": float(y)} for r, t, x, y in (k.split(":") for k in kits.split(",") if k)] or None
    if r2list or r2del:
        dest = Path(out); dest.mkdir(parents=True, exist_ok=True)
        for name, data in r2ls.remote([k for k in r2del.split(";") if k]).items(): (dest / name).write_bytes(data)
        return
    if r2:
        dest = Path(out); dest.mkdir(parents=True, exist_ok=True)
        for name, data in r2check.remote().items(): (dest / name).write_bytes(data)
        return
    if kp:
        dest = Path(out); dest.mkdir(parents=True, exist_ok=True)
        for name, data in kp_probe.remote(kp, clip, [float(t) for t in times.split(",") if t.strip()]).items():
            (dest / name).write_bytes(data)
        return
    if hf:
        dest = Path(out); dest.mkdir(parents=True, exist_ok=True)
        (dest / "hf_search.json").write_text(json.dumps(hf_search.remote(hf.split("|")), indent=1))
        return
    state = rerun.remote(rerun_mode, res, clip, kl) if rerun_mode else None
    files = snapshot.remote("eval" if rerun_mode else "live", [float(t) for t in times.split(",") if t.strip()], export, keep, clip if export else "")
    dest = Path(out); dest.mkdir(parents=True, exist_ok=True)
    for name, data in files.items(): (dest / name).write_bytes(data)
    if state is not None: (dest / "job_state.json").write_text(json.dumps(state, indent=1))
    print("wrote", sorted(files), "to", dest)
