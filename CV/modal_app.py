"""Trivela on Modal: the engine (app.py) with an on-demand GPU, so nothing costs money while nobody is analysing.

  web       always-reachable address, CPU only, sleeps when unused: the site, uploads, progress, saved analyses,
            video playback, and the AI coach (trivela-frontend/coach, run next to it on 127.0.0.1)
  analyze   one NVIDIA A10, started for each analysis and stopped right after it
  volume    "trivela-data": uploads, processed video and saved analyses, shared by both
  dict      "trivela-jobs": the running job's progress, written by the GPU worker and read by web

Deploy: GitHub Actions runs `modal deploy CV/modal_app.py` on every push to main (.github/workflows/deploy-modal.yml).
By hand: pip install modal, then `modal deploy CV/modal_app.py` from the repo root.
The coach's model key is the Modal secret "trivela-coach" (the variables of trivela-frontend/coach/.env.example).
"""
import os, sys, time, uuid, subprocess, threading
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent            # CV/
SITE_DIR = HERE.parent / "trivela-frontend" / "landing"
COACH_DIR = HERE.parent / "trivela-frontend" / "coach"
DATA = "/data"
GPU = "A10"
SITE_ORIGINS = "https://trivela-lab.vercel.app"   # the Vercel site, allowed to start analyses
BUSY = ("scanning", "processing", "finalizing")    # same as app.py

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "libgl1", "libglib2.0-0", "curl", "xz-utils", "tesseract-ocr")   # tesseract: the score bug
    .run_commands("curl -fsSL https://nodejs.org/dist/v22.11.0/node-v22.11.0-linux-x64.tar.xz"
                  " | tar -xJ -C /usr/local --strip-components=1")
    .pip_install("torch", "torchvision", index_url="https://download.pytorch.org/whl/cu126")
    .pip_install("ultralytics", "fastapi", "uvicorn", "python-multipart", "pyyaml", "huggingface_hub", "scipy", "lap>=0.5.12",
                 "pytesseract", "onnxruntime-gpu==1.22.0", "boto3")   # 1.22: CUDA 12 + cuDNN 9, as torch cu126
    # football detection weights, baked into the image
    .run_commands("mkdir -p /app && cd /app && python -c \"from huggingface_hub import hf_hub_download; "
                  "hf_hub_download(repo_id='martinjolif/yolo-football-player-detection', "
                  "filename='yolo-football-player-detection.pt', local_dir='.')\"")
    # person re-identification (appearance embeddings), from the Ultralytics assets
    .run_commands("cd /app && python -c \"from ultralytics.utils.downloads import attempt_download_asset; "
                  "attempt_download_asset('yolo26s-reid.onnx')\" && ls -la /app/yolo26s-reid.onnx")
    # pitch keypoints (29 landmarks of the markings), for the homography to pitch metres
    .run_commands("cd /app && python -c \"from huggingface_hub import hf_hub_download; import shutil; "
                  "shutil.copy(hf_hub_download(repo_id='cdqqqqqq/Soccana_Keypoint', filename='Model/weights/best.pt'), "
                  "'pitch-keypoints.pt')\"")
    .env({"PYTHONUNBUFFERED": "1", "YOLO_CONFIG_DIR": "/tmp/Ultralytics", "NVIDIA_DRIVER_CAPABILITIES": "all",
          "TRIVELA_WORKSPACE": f"{DATA}/workspace", "TRIVELA_COACH_URL": "http://127.0.0.1:5600",
          "TRIVELA_ALLOWED_ORIGINS": SITE_ORIGINS})
    .workdir("/app")
    # code last: changing it redeploys in seconds without rebuilding the layers above
    .add_local_file(HERE / "app.py", "/app/app.py")
    .add_local_file(HERE / "hud.html", "/app/hud.html")
    .add_local_file(HERE.parent / "trivela-frontend/scripts/demo-match.json", "/app/demo-match.json")
    .add_local_dir(SITE_DIR, "/app/web")
    .add_local_dir(COACH_DIR, "/coach", ignore=[".env", ".env.*", "Dockerfile", "dev-site.mjs"])
)

app = modal.App("trivela", image=image)
volume = modal.Volume.from_name("trivela-data", create_if_missing=True)
jobs = modal.Dict.from_name("trivela-jobs", create_if_missing=True)


def load_engine():
    sys.path.insert(0, "/app")
    import app as engine
    return engine


# ----------------------------------------------------------------------------
# GPU worker: runs app.py's pipeline unchanged and reports its progress every second
# ----------------------------------------------------------------------------
@app.function(gpu=GPU, cpu=8.0, memory=16384, timeout=2 * 60 * 60, volumes={DATA: volume},
              max_containers=1, scaledown_window=10)
def analyze(job: str, video: str, mode: str, res: str, burn: bool, reid: bool, kits: list = None):
    volume.reload()                                # see the upload web just committed
    engine = load_engine()
    J = engine.JOB_STATE
    J.update(status="scanning", phase=f"GPU ready ({engine.DEVICE_NAME}): opening the video", total_frames=0,
             current_frame=0, fps=0.0, progress=0.0, eta=0, video_url="", meta_url="", error_msg="")
    engine.JOB_CANCEL.clear()
    done = threading.Event()

    def report(final=False):
        s = dict(J, job=job)
        # the files are only readable by web after the commit below, so the job isn't over until then
        if not final and s["status"] not in BUSY: s.update(status="finalizing", phase="Saving the results")
        jobs["state"] = s

    def pump():
        while not done.wait(1.0):
            try:
                report()
                if jobs.get("cancel") == job: engine.JOB_CANCEL.set()
            except Exception as e:
                print("progress report failed:", e)

    threading.Thread(target=pump, daemon=True).start()
    try:
        engine.process_football_match(video, mode, res, burn, reid, kits)   # catches its own errors into J
    finally:
        done.set()
        volume.commit()
        report(final=True)


# ----------------------------------------------------------------------------
# Videos on Cloudflare R2 (Modal secret "trivela-r2": R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET).
# Each saved analysis' annotated video is uploaded there; the site plays it from a short-lived signed link, so the
# bucket stays private and playback comes from Cloudflare's network instead of this small web server.
# ----------------------------------------------------------------------------
r2_secret = modal.Secret.from_name("trivela-r2")
R2_LINK_S = 6 * 3600

def r2_client():
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                        region_name="auto", config=Config(signature_version="s3v4", retries={"max_attempts": 5}))

def r2_key(match_id): return f"videos/{match_id}.mp4"

@app.function(cpu=2.0, memory=2048, timeout=60 * 60, volumes={DATA: volume}, secrets=[r2_secret])
def upload_video(match_id: str):
    """The saved copy of an analysis' video (workspace/videos) -> R2, in parts so a full match goes up fine"""
    from boto3.s3.transfer import TransferConfig
    volume.reload()
    src = Path(DATA) / "workspace" / "videos" / f"{match_id}.mp4"
    if not src.is_file(): print("no video to upload for", match_id); return False
    r2_client().upload_file(str(src), os.environ["R2_BUCKET"], r2_key(match_id),
                            ExtraArgs={"ContentType": "video/mp4", "CacheControl": "public, max-age=604800, immutable"},
                            Config=TransferConfig(multipart_threshold=64 * 2**20, multipart_chunksize=64 * 2**20, max_concurrency=8))
    print("uploaded", match_id, src.stat().st_size, "bytes to R2")
    return True


class ModalWorker:
    """app.WORKER on the web server: hands jobs to analyze() and reads its progress back."""
    CHECK_EVERY = 15   # seconds between checks that a busy worker is still alive

    def __init__(self, J):
        self.J, self.last, self.checked = J, None, 0.0
        self.on_r2 = set()                          # analyses whose video is known to be on R2

    def info(self):
        return {"device": f"NVIDIA {GPU} (Modal, starts when an analysis starts)", "gpu": True}

    def commit(self):
        volume.commit()

    def store_video(self, match_id):
        """After the web server saved a video copy: upload it to R2 in the background"""
        try:
            volume.commit(); upload_video.spawn(match_id)
        except Exception as e:
            print("could not start the R2 upload:", e)

    def delete_video(self, match_id):
        self.on_r2.discard(match_id)
        try: r2_client().delete_object(Bucket=os.environ["R2_BUCKET"], Key=r2_key(match_id))
        except Exception as e: print("R2 delete failed:", e)

    def video_link(self, match_id):
        """A signed R2 link to an analysis' video, or None if it isn't there (yet)"""
        if match_id not in self.on_r2:
            try:
                c = r2_client(); c.head_object(Bucket=os.environ["R2_BUCKET"], Key=r2_key(match_id)); self.on_r2.add(match_id)
            except Exception:
                return None
        try:
            return r2_client().generate_presigned_url("get_object", Params={"Bucket": os.environ["R2_BUCKET"], "Key": r2_key(match_id)},
                                                      ExpiresIn=R2_LINK_S)
        except Exception as e:
            print("R2 link failed:", e); return None

    def start(self, video, mode, res, burn, reid, kits=None):
        job = uuid.uuid4().hex
        s = dict(self.J, phase="Starting the GPU (about a minute)", job=job)
        try:
            volume.commit()                         # the upload, so the worker can read it
            jobs["cancel"] = ""
            jobs["state"] = s
            jobs["call"] = analyze.spawn(job, video, mode, res, burn, reid, kits).object_id
        except Exception as e:
            print("could not start the GPU worker:", e)
            s = dict(s, status="error", error_msg=f"Could not start the GPU: {e}")
            jobs["state"] = s
        self.last = s
        self.J.update({k: v for k, v in s.items() if k != "job"})

    def cancel(self):
        s = jobs.get("state") or {}
        if s.get("status") in BUSY: jobs["cancel"] = s.get("job")

    def sync(self, J):
        s = jobs.get("state")
        if not s: return
        busy = s["status"] in BUSY
        if not busy and J["status"] in ("uploading", "uploaded"): return   # a new upload here is newer than the last job
        if busy and time.time() - self.checked > self.CHECK_EVERY:
            self.checked = time.time()
            s = self._still_running(s)
        if s != self.last:
            if not busy:
                try: volume.reload()                # the finished video and summary
                except Exception as e: print("volume reload failed:", e)
            self.last = s
        J.update({k: v for k, v in s.items() if k != "job"})

    def _still_running(self, s):
        # a worker that crashed (out of memory, timeout) never reports again: turn its job into an error
        try:
            modal.FunctionCall.from_id(jobs.get("call")).get(timeout=0)
            err = "The GPU worker stopped before it finished."
        except (TimeoutError, modal.exception.TimeoutError):
            return s                                # still running
        except Exception as e:
            err = f"The GPU worker stopped: {e}"
        latest = jobs.get("state") or s
        if latest["status"] not in BUSY: return latest   # it finished between the two reads
        s = dict(latest, status="error", error_msg=err)
        jobs["state"] = s
        return s


# ----------------------------------------------------------------------------
# Web server: app.py's API and pages, plus the AI coach on 127.0.0.1:5600 (no public port, as in docker-compose)
# One container, so uploads and progress live in one place; it sleeps after 5 idle minutes.
# ----------------------------------------------------------------------------
@app.cls(cpu=2.0, memory=2048, timeout=60 * 60, volumes={DATA: volume}, max_containers=1, scaledown_window=300,
         secrets=[modal.Secret.from_name("trivela-coach"), r2_secret])
@modal.concurrent(max_inputs=100)
class Web:
    @modal.enter()
    def start_coach(self):
        self.coach = subprocess.Popen(["node", "/coach/server.mjs"], env={**os.environ, "PORT": "5600"})

    @modal.exit()
    def stop_coach(self):
        self.coach.terminate()

    @modal.asgi_app(label="trivela")
    def serve(self):
        engine = load_engine()
        engine.WORKER = ModalWorker(engine.JOB_STATE)
        return engine.app
