#!/usr/bin/env python3
"""Run MiniMax H3 reference-to-video jobs on one reusable Colab session."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import queue
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


SKILL_DIR = Path(__file__).resolve().parents[1]
NOTEBOOK = SKILL_DIR / "assets" / "MiniMax_H3_Turbo_Colab.ipynb"
logger = logging.getLogger("h3_runner")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PICTURE_RE = re.compile(r"<Picture\s+(\d+)>")
NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
AUTH = os.environ.get("COLAB_AUTH", "oauth2")


class ColabCommandError(RuntimeError):
    def __init__(self, label: str, returncode: int, output: list[str]):
        self.label = label
        self.returncode = returncode
        self.output = output
        tail = "\n".join(line for line in output[-12:] if line)
        super().__init__(f"{label} failed (exit {returncode})" + (f":\n{tail}" if tail else ""))


class ColabTimeoutError(TimeoutError):
    pass


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def clean_output(value: str) -> str:
    return ANSI_RE.sub("", value).replace("\r", "").strip()


def normalize_path(raw: str | Path | None) -> Path | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    if os.name != "nt" and len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        drive = s[0].lower()
        rest = s[2:].replace("\\", "/").lstrip("/")
        s = f"/mnt/{drive}/{rest}"
    elif os.name != "nt" and "\\" in s:
        s = s.replace("\\", "/")
    return Path(s).expanduser().resolve()


def write_progress(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temp.write_text(content, encoding="utf-8")
        # Retry os.replace in case Windows host is reading the file simultaneously
        for attempt in range(5):
            try:
                os.replace(temp, path)
                break
            except (PermissionError, OSError):
                time.sleep(0.05 * (attempt + 1))
        else:
            # Fallback direct write
            try:
                path.write_text(content, encoding="utf-8")
            except Exception:
                pass
        if temp.exists():
            try:
                temp.unlink()
            except Exception:
                pass
    except Exception:
        # Never crash the main inference job just for a progress update
        pass


def parse_usage(output: str) -> dict[str, Any]:
    """Parse labelled `colab usage` output; v0.7.4 has no JSON option."""
    normalized = clean_output(output)

    def number(pattern: str, label: str) -> float:
        match = re.search(pattern, normalized, re.IGNORECASE | re.MULTILINE)
        if not match:
            raise ValueError(f"colab usage output did not contain {label!r}.")
        return float(match.group(1).replace(",", ""))

    balance = number(r"^Current balance:\s*([\d,]+(?:\.\d+)?)\s+compute units\s*$", "Current balance")
    rate = number(r"^Usage rate:\s*([\d,]+(?:\.\d+)?)\s*/\s*hr\s*$", "Usage rate")
    assignments = number(r"^Active assignments:\s*(\d+)\s*$", "Active assignments")
    return {
        "balance": balance,
        "rate_per_hour": rate,
        "active_assignments": int(assignments),
        "checked_at": now_iso(),
    }


def _colab_path() -> str:
    path = shutil.which("colab")
    if not path:
        for cand in ("/root/.local/bin/colab", "/usr/local/bin/colab", "/usr/bin/colab"):
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        raise FileNotFoundError("Colab CLI is not installed or is not on PATH. Install google-colab-cli and sign in with OAuth2 first.")
    return path


def _session_config_args() -> list[str]:
    """Mỗi tài khoản (COLAB_TOKEN_PATH) dùng 1 file sessions_<email>.json riêng biệt để không bao giờ xóa nhầm phiên của tài khoản khác."""
    tok_path = os.environ.get("COLAB_TOKEN_PATH", "").strip()
    if tok_path:
        p = Path(tok_path)
        cfg_file = p.parent / f"sessions_{p.stem}.json"
        return [f"--config={cfg_file}"]
    return []


def _sync_or_cleanup_server_assignments(session: str, gpu: str, on_line: Callable[[str], None] | None = None) -> bool:
    """
    Đồng bộ trực tiếp với máy chủ Google Colab của tài khoản hiện tại:
    - Nếu tài khoản đang có phiên CPU (Hardware: NONE) hoặc phiên GPU khác loại đang treo: tự động ngắt (unassign) để tránh lỗi Allocation refused (412).
    - Nếu tài khoản đang có sẵn phiên GPU đúng loại (ví dụ A100) trên máy chủ (kể cả khi bị mất tên local '[?]'):
      tự động gắn lại (re-bind) vào `session` để tái sử dụng ngay!
    - Nếu tài khoản mới thêm vào / chưa có phiên nào: trả về False để chạy `colab new` cấp phát và cài đặt từ đầu.
    """
    colab_py = Path("/root/.local/share/uv/tools/google-colab-cli/bin/python")
    if not colab_py.exists():
        colab_py = Path(sys.executable or "/usr/bin/python3")
    if not colab_py.exists():
        return False
    cfg_args = _session_config_args()
    cfg_path = cfg_args[0].split("=", 1)[1] if cfg_args else ""
    py_code = (
        "import os, sys\n"
        "from colab_cli.common import state\n"
        "from colab_cli.state import SessionState\n"
        f"state.config_path = {cfg_path!r} or None\n"
        f"target_sess = {session!r}\n"
        f"target_gpu = {gpu.upper()!r}\n"
        "assignments = state.client.list_assignments()\n"
        "if not assignments:\n"
        "    try: state.store.remove(target_sess)\n"
        "    except Exception: pass\n"
        "    print('NO_ASSIGNMENTS')\n"
        "    sys.exit(0)\n"
        "rebound = False\n"
        "old_s = None\n"
        "try: old_s = state.store.get(target_sess)\n"
        "except Exception: pass\n"
        "for a in assignments:\n"
        "    acc_val = getattr(a.accelerator, 'value', str(a.accelerator)).upper()\n"
        "    var_val = getattr(a.variant, 'name', str(a.variant))\n"
        "    shp_val = getattr(a.machine_shape, 'name', str(a.machine_shape))\n"
        "    if (acc_val == target_gpu or (target_gpu != 'CLEANUP_ALL' and acc_val in ('A100', 'L4', 'T4', 'V100'))) and not rebound:\n"
        "        rpi = a.runtime_proxy_info\n"
        "        s = SessionState(\n"
        "            name=target_sess,\n"
        "            token=rpi.token if rpi else (old_s.token if old_s else ''),\n"
        "            url=rpi.url if rpi else (old_s.url if old_s else ''),\n"
        "            endpoint=a.endpoint,\n"
        "            token_expires_at=rpi.expires_at() if rpi else (old_s.token_expires_at if old_s else ''),\n"
        "            variant=var_val,\n"
        "            accelerator=acc_val,\n"
        "            machine_shape=shp_val,\n"
        "            kernel_id=old_s.kernel_id if (old_s and old_s.endpoint == a.endpoint) else None,\n"
        "            session_id=old_s.session_id if (old_s and old_s.endpoint == a.endpoint) else None,\n"
        "        )\n"
        "        state.store.add(s)\n"
        "        rebound = True\n"
        "        print(f'REBOUND:{a.endpoint}:{acc_val}')\n"
        "    else:\n"
        "        try:\n"
        "            state.client.unassign(a.endpoint)\n"
        "            print(f'UNASSIGNED:{a.endpoint}:{acc_val}')\n"
        "        except Exception as e:\n"
        "            print(f'UNASSIGN_ERR:{e}')\n"
    )
    try:
        res = subprocess.run(
            [str(colab_py), "-c", py_code],
            capture_output=True,
            text=True,
            timeout=25,
            encoding="utf-8",
            errors="replace",
        )
        out = (res.stdout or "") + "\n" + (res.stderr or "")
        for ln in out.splitlines():
            ln_s = ln.strip()
            if ln_s.startswith("UNASSIGNED:") and on_line:
                on_line(f"[colab] Cleaned up old/CPU assignment on server ({ln_s}).")
            elif ln_s.startswith("REBOUND:") and on_line:
                on_line(f"[colab] Attached existing {gpu} server assignment ({ln_s}) to '{session}'.")
        return "REBOUND:" in out
    except Exception:
        return False


def call_colab(
    arguments: list[str],
    *,
    label: str,
    timeout: float,
    on_line: Callable[[str], None] | None = None,
) -> str:
    command = [_colab_path(), f"--auth={AUTH}", *_session_config_args(), *arguments]
    popen_kwargs: dict[str, Any] = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    child = subprocess.Popen(command, **popen_kwargs)
    lines: queue.Queue[str | None] = queue.Queue()

    def collect() -> None:
        assert child.stdout is not None
        for line in child.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    output: list[str] = []
    eof = False
    started = time.time()
    last_activity = time.time()
    max_idle = 600.0 if ("exec" in arguments) else timeout
    while not eof or child.poll() is None:
        now_ts = time.time()
        is_timed_out = (now_ts - started > timeout)
        is_idle_dead = (now_ts - last_activity > max_idle)
        if (is_timed_out or is_idle_dead) and child.poll() is None:
            try:
                if sys.platform == "win32":
                    child.terminate()
                else:
                    os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    if sys.platform == "win32":
                        child.kill()
                    else:
                        os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            reader.join(timeout=2)
            if child.stdout is not None:
                child.stdout.close()
            if is_idle_dead and not is_timed_out:
                raise ColabCommandError(label, 1, output + ["RuntimeError: Connection was lost (idle timeout / PC sleep detected)."])
            raise ColabTimeoutError(f"{label} exceeded {timeout:g} seconds. The remote kernel may still be busy; the batch will stop its Colab session instead of retrying and duplicating compute.")
        try:
            item = lines.get(timeout=0.25)
        except queue.Empty:
            continue
        last_activity = time.time()
        if item is None:
            eof = True
            continue
        line = clean_output(item)
        output.append(line)
        if on_line:
            on_line(line)
        if "RuntimeError: Connection was lost" in line or "Connection was lost." in line:
            try:
                if sys.platform == "win32":
                    child.kill()
                else:
                    os.killpg(child.pid, signal.SIGKILL)
            except Exception:
                pass
            reader.join(timeout=2)
            if child.stdout is not None:
                child.stdout.close()
            raise ColabCommandError(label, 1, output)
    returncode = child.wait()
    reader.join(timeout=2)
    if child.stdout is not None:
        child.stdout.close()
    if returncode != 0:
        raise ColabCommandError(label, returncode, output)
    return "\n".join(line for line in output if line)


def check_cli() -> dict[str, str]:
    version = call_colab(["version"], label="colab version", timeout=20)
    return {"version": version, "auth": AUTH}


def get_usage() -> dict[str, Any]:
    output = call_colab(["usage"], label="colab usage", timeout=30)
    return parse_usage(output)


def start_session(
    session: str,
    gpu: str,
    high_mem: bool,
    progress_path: Path | None = None,
) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session):
        raise ValueError("Session name may contain only letters, numbers, underscores, and hyphens (up to 64 characters).")
    state: dict[str, Any] = {"task": "start", "status": "starting", "session": session, "gpu": gpu, "log_tail": [], "updated_at": now_iso()}
    write_progress(progress_path, state)

    def on_line(line: str) -> None:
        state["log_tail"] = (state["log_tail"] + ([line] if line else []))[-30:]
        state["updated_at"] = now_iso()
        write_progress(progress_path, state)

    # 1. Đồng bộ trạng thái thực tế trên máy chủ Colab của tài khoản này:
    #    - Nếu tài khoản đang có phiên CPU (NONE) thì tự tắt phiên CPU đó.
    #    - Nếu tài khoản đang có sẵn phiên GPU (A100) thì gắn lại vào `session` và kiểm tra kết nối.
    #    - Nếu là tài khoản mới thêm vào / chưa có phiên nào, sẽ đi xuống bước 2 để chạy `colab new` và cài đặt từ đầu!
    # Thử kết nối trực tiếp vào phiên ấm hiện có TRƯỚC (không ghi đè kernel_id/session_id nếu phiên đang sống!)
    try:
        probe_out = call_colab(["ls", "--session", session, "/content"], label="reuse warm Colab session", timeout=20)
        on_line(f"[colab] Reusing active warm GPU session '{session}'.")
        state.update({"status": "active", "session": session, "output": probe_out, "reused": True, "updated_at": now_iso()})
        write_progress(progress_path, state)
        return
    except Exception:
        pass

    _sync_or_cleanup_server_assignments(session, gpu, on_line=on_line)
    try:
        probe_out = call_colab(["ls", "--session", session, "/content"], label="reuse rebound Colab session", timeout=25)
        on_line(f"[colab] Reusing rebound warm GPU session '{session}'.")
        state.update({"status": "active", "session": session, "output": probe_out, "reused": True, "updated_at": now_iso()})
        write_progress(progress_path, state)
        return
    except Exception:
        pass

    # 2. Tài khoản mới / chưa có phiên GPU đang chạy -> Cấp phát máy ảo GPU mới (Notebook sẽ tự động cài ComfyUI & tải Model từ đầu)
    args = ["new", "--session", session, "--gpu", gpu]
    if high_mem:
        args.append("--high-mem")
    try:
        output = call_colab(args, label="create Colab session", timeout=900, on_line=on_line)
        state.update({"status": "active", "session": session, "output": output, "updated_at": now_iso()})
        write_progress(progress_path, state)
    except Exception as exc:
        err_s = str(exc).lower()
        if any(k in err_s for k in ("allocation refused", "precondition failed", "already exists", "service unavailable", "bad gateway", "503", "502", "timed out")):
            for attempt in range(1, 4):
                on_line(f"[colab] Transient Colab error or lingering assignment (attempt {attempt}/3); cleaning up and retrying '{session}'...")
                try:
                    call_colab(["stop", "--session", session], label="stop lingering Colab session", timeout=90, on_line=on_line)
                except Exception:
                    pass
                _sync_or_cleanup_server_assignments(session, "CLEANUP_ALL", on_line=on_line)
                time.sleep(4 * attempt)
                try:
                    output = call_colab(args, label=f"create Colab session (retry {attempt})", timeout=900, on_line=on_line)
                    state.update({"status": "active", "session": session, "output": output, "updated_at": now_iso()})
                    write_progress(progress_path, state)
                    return
                except Exception as retry_exc:
                    exc = retry_exc
                    if not any(k in str(exc).lower() for k in ("allocation refused", "precondition failed", "already exists", "service unavailable", "bad gateway", "503", "502", "timed out")):
                        break
        state.update({"status": "failed", "error": str(exc), "updated_at": now_iso()})
        write_progress(progress_path, state)
        raise


def stop_session(session: str, progress_path: Path | None = None) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session):
        raise ValueError("Invalid Colab session name.")
    state: dict[str, Any] = {"task": "stop", "status": "stopping", "session": session, "log_tail": [], "updated_at": now_iso()}
    write_progress(progress_path, state)

    def on_line(line: str) -> None:
        state["log_tail"] = (state["log_tail"] + ([line] if line else []))[-30:]
        state["updated_at"] = now_iso()
        write_progress(progress_path, state)

    try:
        output = call_colab(["stop", "--session", session], label="stop Colab session", timeout=300, on_line=on_line)
        state.update({"status": "stopped", "output": output, "updated_at": now_iso()})
        write_progress(progress_path, state)
    except Exception as exc:
        state.update({"status": "failed", "error": str(exc), "updated_at": now_iso()})
        write_progress(progress_path, state)
        raise


def safe_job_id(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,48}", value):
        return value
    return uuid.uuid4().hex[:16]


def safe_name(value: Any, fallback: str) -> str:
    candidate = NAME_RE.sub("_", str(value or "")).strip("_-")[:64]
    return candidate or fallback


def shot_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    minutes, remainder = divmod(milliseconds, 60_000)
    whole_seconds, millis = divmod(remainder, 1_000)
    return f"{minutes:02d}:{whole_seconds:02d}.{millis:03d}"


def compose_ref2va_prompt(
    *,
    subject_definitions: str,
    summary: str,
    retention_analysis: str,
    shots: list[dict[str, Any]],
    overall_soundscape: str,
    non_diegetic_music: str,
    image_count: int,
    duration_seconds: float,
    detailed_description: str = "",
) -> str:
    """Build the complete Ref2VA prompt used by guided web-UI jobs."""
    if not 1 <= image_count <= 9:
        raise ValueError("Ref2VA prompts require 1–9 reference images.")
    if not math.isfinite(duration_seconds) or not 4 <= duration_seconds <= 15:
        raise ValueError("Video duration must be between 4 and 15 seconds.")
    if not isinstance(shots, list) or not shots:
        raise ValueError("Add at least one shot.")

    sections = {
        "subject_definitions": subject_definitions,
        "summary": summary,
        "retention_analysis": retention_analysis,
        "overall_soundscape": overall_soundscape,
        "non_diegetic_music": non_diegetic_music,
    }
    for label, value in sections.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label.replace('_', ' ')} cannot be empty.")

    details: list[str] = []
    previous_start = -1.0
    for number, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            raise ValueError(f"Shot {number} must be an object.")
        try:
            start = float(shot.get("start_seconds"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Shot {number} needs a numeric start time.") from exc
        description = shot.get("description")
        if not math.isfinite(start) or not 0 <= start < duration_seconds:
            raise ValueError(f"Shot {number} must start between 0 and the video duration.")
        if number == 1 and start != 0:
            raise ValueError("Shot 1 must start at 0 seconds.")
        if start <= previous_start:
            raise ValueError("Shot start times must be strictly increasing.")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"Shot {number} needs a description.")
        if number > 1 and round(start * 1000) <= round(previous_start * 1000):
            raise ValueError("Shot start times must differ by at least 0.001 seconds.")
        rendered = description.strip()
        dialogue = str(shot.get("dialogue") or "").strip()
        if dialogue:
            # Dialogue is intentionally a single Chinese line so it can safely
            # be represented by the model's spoken-audio marker.
            dialogue = " ".join(dialogue.split())
            if "</d>" in dialogue:
                raise ValueError(f"Shot {number} dialogue contains a reserved closing marker.")
            speaker = str(shot.get("speaker") or "The subject (S1)").strip()
            rendered += f" {speaker} says, <d>[Chinese] {dialogue}</d>"
        if number == 1:
            details.append(f"[Shot 1] {rendered}")
        else:
            transition_starters = (
                "the camera cuts to", "the shot cuts to", "the shot transitions to",
                "the shot changes to", "the shot switches to",
            )
            transition = rendered if rendered.lower().startswith(transition_starters) else "the camera cuts to " + rendered
            details.append(f"[Shot {number}] At {shot_timestamp(start)}, {transition}")
        previous_start = start

    detail_parts = ([detailed_description.strip()] if detailed_description.strip() else []) + details
    prompt = (
        "subject_definitions:\n" + subject_definitions.strip() + "\n\n"
        "summary:\n" + summary.strip() + "\n\n"
        "retention_analysis:\n" + retention_analysis.strip() + "\n\n"
        "detailed_description:\n" + "\n\n".join(detail_parts) + "\n\n"
        "overall_soundscape:\n" + overall_soundscape.strip() + "\n\n"
        "non_diegetic_music:\n" + non_diegetic_music.strip()
    )
    referenced = [int(number) for number in PICTURE_RE.findall(prompt) if int(number) >= 1]
    if referenced and max(referenced) > image_count:
        image_count = min(9, max(referenced))
    invalid = sorted({int(number) for number in PICTURE_RE.findall(prompt) if int(number) < 1 or int(number) > image_count})
    if invalid:
        raise ValueError(f"Prompt references Picture {invalid}; this job has {image_count} images.")
    return prompt


def resolve_jobs(manifest: dict[str, Any], output_dir: Path) -> list[dict[str, Any]]:
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("Manifest must contain a non-empty jobs list.")
    if len(jobs) > 200:
        raise ValueError("A single batch may contain at most 200 videos.")
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_outputs: set[Path] = set()
    for index, raw in enumerate(jobs, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Job {index} must be an object.")
        mode = (str(raw.get("mode") or os.environ.get("H3_INFERENCE_MODE") or "reference").strip().lower()) or "reference"
        if mode in ("cam_previs", "audio_sync", "fasth3_omni"):
            mode = "reference"
        elif mode in ("fasth3_t2v", "fasth3_trim"):
            mode = "t2v"
        elif mode == "skeleton_v2v":
            mode = "char_swap"
        swap_edit_mode = str(raw.get("swap_edit_mode") or manifest.get("swap_edit_mode") or os.environ.get("H3_SWAP_EDIT_MODE", "person")).strip().lower() or "person"
        if mode in ("t2v", "text_to_video", "ai_upscale") or (mode in ("char_swap", "v2v") and swap_edit_mode == "remove"):
            image_paths: list[Path] = []
        else:
            refs = raw.get("reference_images", raw.get("images"))
            has_vid_ref = bool(raw.get("source_video") or raw.get("video") or os.environ.get("H3_SOURCE_VIDEO"))
            if not isinstance(refs, list) or (mode not in ("director", "longshot", "continuum", "face_refine") and not (1 <= len(refs) <= 9)):
                if (mode in ("director", "longshot", "continuum", "face_refine") or has_vid_ref) and (not refs or not isinstance(refs, list)):
                    refs = []
                else:
                    raise ValueError(f"Job {index} must have 1–9 reference images for mode {mode!r}.")
            image_paths = []
            for image in refs:
                path = normalize_path(image)
                if not path.is_file() or path.stat().st_size == 0:
                    raise ValueError(f"Reference image is missing or empty: {path}")
                if mode in ("reference", "r2v", "director", "longshot", "continuum"):
                    try:
                        from scripts.blocking_storyboard import prepare_clean_h3_ref_image
                        path = prepare_clean_h3_ref_image(path)
                    except Exception:
                        pass
                image_paths.append(path)

        prompt_file = raw.get("prompt_file")
        if prompt_file:
            prompt_path = normalize_path(prompt_file)
            if not prompt_path.is_file() or prompt_path.stat().st_size == 0:
                if prompt_path.is_dir() or not prompt_path.exists():
                    prompt_file = None
                    prompt = str(raw.get("prompt", ""))
                else:
                    raise ValueError(f"Prompt file is missing or empty: {prompt_path}")
            else:
                prompt = prompt_path.read_text(encoding="utf-8")
        else:
            prompt = str(raw.get("prompt", ""))

        if mode in ("reference", "r2v"):
            try:
                from scripts.blocking_storyboard import auto_upgrade_raw_prompt_to_h3_r2v
                prompt = auto_upgrade_raw_prompt_to_h3_r2v(prompt, image_paths=image_paths)
            except Exception:
                prompt = re.sub(
                    r"For the target video,\s*at 0\.00 seconds into the target video,[^\n]*\n*",
                    "",
                    prompt,
                    flags=re.IGNORECASE,
                ).strip()

        director_pack = normalize_path(raw.get("director_pack") or raw.get("pack") or os.environ.get("H3_DIRECTOR_PACK"))
        if director_pack and (not director_pack.is_file() or director_pack.stat().st_size == 0):
            director_pack = None
        if mode != "director":
            director_pack = None

        if not prompt.strip() and director_pack and director_pack.is_file():
            try:
                import zipfile
                with zipfile.ZipFile(director_pack) as z:
                    tdata = json.loads(z.read("timeline.json"))
                    prompt = tdata.get("global", {}).get("prompt", "")
                    if not prompt and tdata.get("segments"):
                        prompt = tdata["segments"][0].get("prompt", "")
            except Exception:
                pass

        if not prompt.strip():
            if mode in ("director", "ai_upscale", "face_refine", "char_swap", "v2v"):
                prompt = "Photorealistic cinematic scene, high detail, 8k resolution, natural lighting."
            else:
                raise ValueError(f"Job {index} needs a non-empty UTF-8 prompt.")

        image_count = len(image_paths)
        if mode not in ("t2v", "text_to_video", "director", "longshot", "continuum", "ai_upscale", "face_refine") and image_count > 0:
            referenced_numbers = sorted({int(n) for n in PICTURE_RE.findall(prompt) if int(n) >= 1})
            extra_numbers = [n for n in referenced_numbers if n > image_count]
            if extra_numbers:
                cleaned_prompt = prompt
                for ep in extra_numbers:
                    cleaned_prompt = re.sub(
                        rf",\s*<Picture\s+{ep}>\s*\([^)]*\)",
                        "",
                        cleaned_prompt,
                        flags=re.IGNORECASE,
                    )
                    cleaned_prompt = re.sub(
                        rf"<Picture\s+{ep}>\s*\([^)]*\)\s*,\s*",
                        "",
                        cleaned_prompt,
                        flags=re.IGNORECASE,
                    )
                    cleaned_prompt = re.sub(
                        rf"<Picture\s+{ep}>\s*là ảnh tham chiếu bối cảnh duy nhất\s*:\s*",
                        "Bối cảnh không gian: ",
                        cleaned_prompt,
                        flags=re.IGNORECASE,
                    )
                    cleaned_prompt = re.sub(
                        rf"<Picture\s+{ep}>\s*is the sole environment reference\s*:\s*",
                        "Environment setting: ",
                        cleaned_prompt,
                        flags=re.IGNORECASE,
                    )
                    cleaned_prompt = re.sub(
                        rf"[;,.]?\s*<Picture\s+{ep}>\s*(không chứa người|contains no person)\.?",
                        ".",
                        cleaned_prompt,
                        flags=re.IGNORECASE,
                    )
                    cleaned_prompt = re.sub(
                        rf"<Picture\s+{ep}>",
                        "the environment",
                        cleaned_prompt,
                        flags=re.IGNORECASE,
                    )
                if cleaned_prompt != prompt:
                    logger.info(
                        "Job %s prompt referenced Picture %s with %s image(s) provided; converted extra <Picture> tags to text environment description.",
                        index, extra_numbers, image_count,
                    )
                    prompt = cleaned_prompt
                    if prompt_file:
                        try:
                            normalize_path(prompt_file).write_text(prompt, encoding="utf-8")
                        except Exception:
                            pass

            remaining_numbers = [int(n) for n in PICTURE_RE.findall(prompt) if int(n) >= 1]
            max_needed = max(remaining_numbers) if remaining_numbers else image_count
            if max_needed > image_count:
                max_needed = min(max_needed, 9)
                last_img = image_paths[-1]
                logger.info(
                    "Job %s prompt references up to Picture %s, but only %s provided. Auto-padding %s image(s) using %s.",
                    index, max_needed, image_count, max_needed - image_count, last_img.name
                )
                while len(image_paths) < max_needed:
                    image_paths.append(last_img)
                image_count = len(image_paths)

            invalid = sorted({int(n) for n in PICTURE_RE.findall(prompt) if int(n) < 1 or int(n) > image_count})
            if invalid:
                raise ValueError(f"Job {index} prompt references Picture {invalid}; it has {image_count} images.")

        if mode in ("t2v", "text_to_video"):
            prompt = re.sub(r"For the target video,\s*at 0\.00 seconds into the target video,[^\n]*\n*", "", prompt, flags=re.IGNORECASE)
            prompt = re.sub(r"\b(in|from|of)\s*<Picture\s+\d+>", "", prompt, flags=re.IGNORECASE)
            prompt = re.sub(r"<Picture\s+\d+>", "", prompt, flags=re.IGNORECASE)

        duration = float(raw.get("duration_seconds", raw.get("duration", 12)))
        max_dur = 480.0 if mode in ("longshot", "continuum") else 15.0
        if not math.isfinite(duration) or not 2.0 <= duration <= max_dur:
            raise ValueError(f"Job {index} duration must be between 2 and {max_dur:g} seconds.")
        seed = raw.get("seed")
        seed = random.SystemRandom().randrange(0, 2**64) if seed in (None, "") else int(seed)
        if not 0 <= seed < 2**64:
            raise ValueError(f"Job {index} seed must be between 0 and 2^64-1.")
        job_id = safe_job_id(raw.get("id"))
        if job_id in seen_ids:
            raise ValueError(f"Job {index} duplicates job id {job_id!r}.")
        seen_ids.add(job_id)
        stem = safe_name(raw.get("output_name") or raw.get("title"), f"h3_{index:02d}")
        output = normalize_path(raw.get("output_path") or output_dir / f"{stem}_{job_id}.mp4")
        if output in seen_outputs:
            raise ValueError(f"Multiple jobs target the same output path: {output}")
        seen_outputs.add(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        width = int(raw.get("width") or os.environ.get("H3_WIDTH", 1024))
        height = int(raw.get("height") or os.environ.get("H3_HEIGHT", 576))
        ref_size = str(raw.get("ref_size") or os.environ.get("H3_REF_SIZE", "match"))
        lora = str(raw.get("lora") or os.environ.get("H3_LORA", "pdmd")).strip().lower()

        audio_file = normalize_path(raw.get("audio_file") or raw.get("audio") or os.environ.get("H3_AUDIO_PATH"))
        if audio_file and (not audio_file.is_file() or audio_file.stat().st_size == 0):
            audio_file = None

        source_video = normalize_path(raw.get("source_video") or raw.get("video") or os.environ.get("H3_SOURCE_VIDEO"))
        if source_video and (not source_video.is_file() or source_video.stat().st_size == 0):
            source_video = None
        if mode in ("char_swap", "v2v", "face_refine") and not source_video:
            raise ValueError(f"Job {index} in {mode!r} mode requires a valid source video (<Video 1>).")

        director_pack = normalize_path(raw.get("director_pack") or raw.get("pack") or os.environ.get("H3_DIRECTOR_PACK"))
        if director_pack and (not director_pack.is_file() or director_pack.stat().st_size == 0):
            director_pack = None
        if mode != "director":
            director_pack = None

        guides: list[dict[str, Any]] = []
        for g in (raw.get("guides") or []):
            if not isinstance(g, dict):
                continue
            gp = normalize_path(g.get("path"))
            if not gp or not gp.is_file():
                continue
            guides.append({**g, "path": gp})

        resolved.append({
            "id": job_id,
            "title": str(raw.get("title") or f"Video {index}")[:120],
            "prompt": prompt,
            "reference_images": image_paths,
            "duration_seconds": duration,
            "width": width,
            "height": height,
            "ref_size": ref_size,
            "seed": seed,
            "output_path": output,
            "mode": mode,
            "lora": lora,
            "audio_file": audio_file,
            "source_video": source_video,
            "director_pack": director_pack,
            "chain_last_frame": bool(raw.get("chain_last_frame", manifest.get("chain_last_frame", False))),
            # Kiểu nối đoạn: last_frame (cũ) | av (1.6s hình+tiếng) | video (1.6s hình, tiếng mới) | cut (cảnh mới)
            "chain_join": str(raw.get("chain_join") or manifest.get("chain_join") or "last_frame").strip().lower(),
            "guides": guides,
            "audio_as_ref": bool(raw.get("audio_as_ref", manifest.get("audio_as_ref", False))),
            "ai_upscale": bool(raw.get("ai_upscale", manifest.get("ai_upscale", False)) or str(os.environ.get("H3_AI_UPSCALE", "0")).strip() == "1"),
            "selflift": bool(raw.get("selflift", manifest.get("selflift", False)) or str(os.environ.get("H3_SELFLIFT", "0")).strip().lower() in ("1", "true", "yes")),
            "tst": bool(raw.get("tst", manifest.get("tst", True)) and str(os.environ.get("H3_TST", "1")).strip().lower() not in ("0", "false", "no")),
            "masked_swap": bool(raw.get("masked_swap", manifest.get("masked_swap", True)) and str(os.environ.get("H3_MASKED_SWAP", "1")).strip().lower() not in ("0", "false", "no")),
            "swap_edit_mode": swap_edit_mode,
            "swap_target": str(raw.get("swap_target") or manifest.get("swap_target") or os.environ.get("H3_SWAP_TARGET", "person")).strip() or "person",
            "swap_hat_fix": bool(raw.get("swap_hat_fix", manifest.get("swap_hat_fix", False)) or str(os.environ.get("H3_SWAP_HAT_FIX", "0")).strip().lower() in ("1", "true", "yes")),
            "swap_no_lipsync": bool(raw.get("swap_no_lipsync", manifest.get("swap_no_lipsync", False)) or str(os.environ.get("H3_SWAP_NO_LIPSYNC", "0")).strip().lower() in ("1", "true", "yes")),
            "face_refine": bool(raw.get("face_refine", manifest.get("face_refine", False)) or str(os.environ.get("H3_FACE_REFINE", "0")).strip().lower() in ("1", "true", "yes")),
            "color_grade": bool(raw.get("color_grade", manifest.get("color_grade", True)) and str(os.environ.get("H3_COLOR_GRADE", "1")).strip().lower() not in ("0", "false", "no")),
        })
    return resolved


def _drift_control_stabilize_frame(frame_png: Path, anchor_ref_png: Path | None = None) -> None:
    """Drift-Control Anti-Burn: prevent cumulative color/saturation drift and highlight burn across chained clips."""
    if not frame_png.is_file():
        return
    try:
        from PIL import Image
        import numpy as np
        arr = np.asarray(Image.open(frame_png).convert("RGB"), dtype=np.float32)
        if anchor_ref_png and Path(anchor_ref_png).is_file():
            ref = np.asarray(Image.open(anchor_ref_png).convert("RGB"), dtype=np.float32)
            for ch in range(3):
                m_cur, s_cur = float(arr[:, :, ch].mean()), max(float(arr[:, :, ch].std()), 1.0)
                m_ref, s_ref = float(ref[:, :, ch].mean()), max(float(ref[:, :, ch].std()), 1.0)
                t_mean = m_cur * 0.70 + m_ref * 0.30
                t_std = s_cur * 0.75 + s_ref * 0.25
                arr[:, :, ch] = (arr[:, :, ch] - m_cur) * (t_std / s_cur) + t_mean
        arr = np.clip(arr, 2.0, 253.0).round().astype(np.uint8)
        Image.fromarray(arr).save(frame_png, format="PNG")
    except Exception:
        pass


def extract_last_frame_png(video_path: Path, out_png: Path, anchor_ref_png: Path | None = None) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not video_path.is_file():
        return False
    for sseof in ("-0.05", "-0.12", "-0.25"):
        try:
            subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-sseof", sseof, "-i", str(video_path), "-frames:v", "1", "-q:v", "1", str(out_png)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if out_png.is_file() and out_png.stat().st_size > 500:
                _drift_control_stabilize_frame(out_png, anchor_ref_png)
                return True
        except Exception:
            pass
    return False


def _merge_refs_for_slot8_composite(refs_to_merge: list[Path], out_png: Path) -> Path:
    """When a job already has 9 reference images and we need slot 9 for the previous scene's last frame,
    merge images 8..N into a high-resolution composite at slot 8 (<Picture 8>) so zero references are lost."""
    try:
        from PIL import Image
        imgs = [Image.open(p).convert("RGB") for p in refs_to_merge if p.is_file()]
        if not imgs:
            return refs_to_merge[0]
        if len(imgs) == 1:
            return refs_to_merge[0]
        cell_w, cell_h = 640, 540
        cols = min(3, len(imgs))
        rows = (len(imgs) + cols - 1) // cols
        canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (18, 32, 36))
        for i, im in enumerate(imgs):
            im_fit = im.copy()
            im_fit.thumbnail((cell_w - 16, cell_h - 16))
            c = i % cols
            r = i // cols
            ox = c * cell_w + (cell_w - im_fit.width) // 2
            oy = r * cell_h + (cell_h - im_fit.height) // 2
            canvas.paste(im_fit, (ox, oy))
        out_png.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_png, format="PNG")
        if out_png.is_file() and out_png.stat().st_size > 500:
            return out_png
    except Exception:
        pass
    return refs_to_merge[0]


def apply_continuity_frame_to_job(job: dict[str, Any], last_frame_png: Path | str, prev_job_id: str = "") -> None:
    """Combine previous clip's extracted last frame alongside all of the next scene's reference images."""
    last_frame_png = Path(str(last_frame_png))
    mode = str(job.get("mode", "reference")).strip().lower()
    refs = [Path(str(r)) for r in (job.get("reference_images") or job.get("images") or [])]
    prompt = str(job.get("prompt") or "").strip()
    prev_label = prev_job_id or "previous scene"

    if mode == "first_frame":
        job["reference_images"] = [last_frame_png]
        return

    if mode in ("t2v", "text_to_video"):
        job["mode"] = "reference"
        job["reference_images"] = [last_frame_png]
        job["prompt"] = (
            "For the target video, at 0.00 seconds into the target video, <Picture 1> is fully referenced as the exact opening frame, background environment, lighting, and character pose.\n"
            "Starting seamlessly from the exact frame in <Picture 1> with zero background jump or camera cut, continue the motion naturally:\n\n"
            + prompt
        )
        return

    if mode in ("char_swap", "v2v"):
        base_refs = refs[:2]
        base_refs.append(last_frame_png)
        pic_idx = len(base_refs)
        job["reference_images"] = base_refs
        if f"<Picture {pic_idx}>" not in prompt:
            job["prompt"] = (
                prompt
                + f"\n\nContinuity Lock: Strictly match the rendered character appearance, skin tone, outfit details, lighting, and background from the previous segment's final frame shown in <Picture {pic_idx}> so the transition is 100% invisible."
            )
        return

    if mode == "longshot" and prompt.startswith("{"):
        try:
            ls_data = json.loads(prompt)
            base_refs = refs[:8]
            base_refs.append(last_frame_png)
            pic_idx = len(base_refs)
            job["reference_images"] = base_refs
            cast = list(ls_data.get("cast") or [])
            while len(cast) < pic_idx - 1:
                idx_c = len(cast) + 1
                cast.append({"label": f"<Picture {idx_c}>", "role": "appearance", "desc": f"Character reference <Picture {idx_c}>"})
            cast.append({
                "label": f"<Picture {pic_idx}>",
                "role": "scene",
                "desc": f"Exact opening frame, background environment, lighting, and pose from the end of the previous clip (<Picture {pic_idx}>)",
            })
            ls_data["cast"] = cast
            shots = list(ls_data.get("shots") or [])
            if shots and isinstance(shots[0], dict):
                s0 = str(shots[0].get("text") or "").strip()
                if f"<Picture {pic_idx}>" not in s0:
                    shots[0]["text"] = f"Starting seamlessly from the exact opening frame, background environment, lighting, and pose in <Picture {pic_idx}> while keeping the character identity of <Picture 1>: {s0}"
                ls_data["shots"] = shots
            job["prompt"] = json.dumps(ls_data, ensure_ascii=False)
            return
        except Exception:
            pass

    # Default: reference / r2v / director (combine all current scene clean Ref_*.png + previous scene's last frame; never include Storyboard_E01Sxxx.png)
    clean_refs = [r for r in refs if not r.name.lower().startswith("storyboard_")]
    if len(clean_refs) >= 9:
        comp_slot8 = last_frame_png.parent / f"Composite_Slot8_{job.get('id', 'scene')}.png"
        merged_8 = _merge_refs_for_slot8_composite(clean_refs[7:], comp_slot8)
        base_refs = clean_refs[:7] + [merged_8, last_frame_png]
        pic_idx = 9
        prompt = re.sub(r"<Picture\s+9>", "<Picture 8>", prompt)
    else:
        base_refs = list(clean_refs) + [last_frame_png]
        pic_idx = len(base_refs)

    job["reference_images"] = base_refs
    ref_range_str = f"<Picture 1>..<Picture {pic_idx - 1}>" if pic_idx > 2 else "<Picture 1>"
    if f"<Picture {pic_idx}>" not in prompt:
        if "subject_definitions:" in prompt:
            prompt = re.sub(
                r"(subject_definitions:\n(?:[^\n]+\n?)+?)(?=\n\s*summary:)",
                rf"\1\n<Picture {pic_idx}> is the first frame of [Shot 1], capturing the closing camera framing, lighting, and spatial continuity from {prev_label}.",
                prompt,
                count=1,
            )
            prompt = prompt.replace("[reference generation]", "[reference generation + keyframe completion]", 1)
            prompt = re.sub(
                r"(retention_analysis:\n(?:[^\n]+\n?)+?)(?=\n\s*detailed_description:)",
                rf"\1\n<Picture {pic_idx}> ([Shot 1] first frame): fully_preserved",
                prompt,
                count=1,
            )
            if "detailed_description:" in prompt and "[Shot 1]" in prompt:
                prompt = re.sub(
                    r"(detailed_description:\s*\n(?:[^\n]+\n)*?\[Shot 1\])\s*",
                    rf"\1 The shot begins from <Picture {pic_idx}>. ",
                    prompt,
                    count=1,
                )
            elif "[Shot 1] " in prompt:
                prompt = prompt.replace(
                    "[Shot 1] ",
                    f"[Shot 1] The shot begins from <Picture {pic_idx}>. ",
                    1,
                )
            job["prompt"] = prompt
        else:
            job["prompt"] = (
                f"{prompt}\n\n"
                f"Opening Continuity (<Picture {pic_idx}>): The shot begins from <Picture {pic_idx}> ({prev_label}) while preserving all subject and prop designs from {ref_range_str}."
            )
    else:
        job["prompt"] = prompt


def verify_mp4(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"Downloaded MP4 is missing or empty: {path}")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode != 0:
        raise ValueError(f"Downloaded output is not a readable MP4: {clean_output(probe.stderr)}")
    streams = {item.get("codec_type") for item in json.loads(probe.stdout).get("streams", [])}
    if not {"video", "audio"} <= streams:
        raise ValueError(f"Downloaded MP4 must contain video and audio streams; found {sorted(streams)}.")


def _recover_comfy_render_after_ws_drop(
    *,
    session: str,
    job_id: str,
    job_title: str,
    submitted_prompt_id: str,
    remote_output: str,
    remote_audio: str,
    work_dir: Path,
    timeout: float,
    on_line: Callable[[str], None] | None = None,
) -> bool:
    """If the Jupyter WebSocket drops while ComfyUI is rendering on the remote Colab VM,
    reconnect to the existing session and poll ComfyUI until the MP4 finishes."""
    output_prefix = f"MiniMax_H3_{job_id[:20]}"
    recover_script = work_dir / f"recover_{job_id[:8]}.py"
    recover_code = f"""import json, os, shutil, subprocess, time, requests
from pathlib import Path

BASE_URL = "http://127.0.0.1:8188"
COMFY_OUT = Path("/content/ComfyUI/output")
LOG_PATH = Path("/content/comfyui_h3.log")
REMOTE_OUTPUT = Path({json.dumps(remote_output)})
REMOTE_AUDIO = {json.dumps(remote_audio)}
OUTPUT_PREFIX = {json.dumps(output_prefix)}
PROMPT_ID = {json.dumps(submitted_prompt_id or "")}.strip()

if REMOTE_OUTPUT.is_file() and REMOTE_OUTPUT.stat().st_size > 1000:
    print("COLAB_CLI_OUTPUT_PATH:", REMOTE_OUTPUT, flush=True)
    raise SystemExit(0)

if not PROMPT_ID and Path("/content/h3_last_prompt_id.txt").is_file():
    try:
        PROMPT_ID = Path("/content/h3_last_prompt_id.txt").read_text(encoding="utf-8").strip()
    except Exception:
        pass

print("[colab] Đã kết nối lại phiên GPU — đang kiểm tra tiến trình ComfyUI...", flush=True)
deadline = time.time() + {min(max(float(timeout), 900.0), 2400.0)}
last_report = 0.0
video_path = None

while time.time() < deadline:
    try:
        if not PROMPT_ID:
            q_info = requests.get(BASE_URL + "/queue", timeout=15).json()
            q_all = q_info.get("queue_running", []) + q_info.get("queue_pending", [])
            if q_all and isinstance(q_all[0], list) and len(q_all[0]) > 1:
                PROMPT_ID = str(q_all[0][1])
        if PROMPT_ID:
            hist = requests.get(BASE_URL + f"/history/{{PROMPT_ID}}", timeout=20).json()
            if PROMPT_ID in hist:
                run = hist[PROMPT_ID]
                if run.get("status", {{}}).get("status_str") == "error":
                    raise RuntimeError("ComfyUI inference error: " + json.dumps(run.get("status", {{}}), ensure_ascii=False))
                outputs = run.get("outputs", {{}})
                if "15" in outputs:
                    filenames = outputs["15"].get("gifs", [])
                    v_info = next((it for it in filenames if str(it.get("filename", "")).endswith(".mp4")), None)
                    if v_info:
                        cand = COMFY_OUT / v_info.get("subfolder", "") / v_info["filename"]
                        if cand.is_file() and cand.stat().st_size > 1000:
                            video_path = cand
                            break
    except RuntimeError:
        raise
    except Exception:
        pass

    if video_path is None and COMFY_OUT.is_dir():
        cands = sorted(
            [p for p in COMFY_OUT.rglob(f"{{OUTPUT_PREFIX}}*.mp4") if p.is_file() and p.stat().st_size > 1000],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
        if cands:
            try:
                q_info = requests.get(BASE_URL + "/queue", timeout=10).json()
                is_busy = bool(q_info.get("queue_running") or q_info.get("queue_pending"))
            except Exception:
                is_busy = False
            if not is_busy:
                video_path = cands[0]
                break

    if time.time() - last_report >= 10:
        log_snip = ""
        if LOG_PATH.is_file():
            lines = [ln.strip() for ln in LOG_PATH.read_text(errors="ignore").splitlines() if ln.strip()]
            if lines:
                log_snip = " | " + lines[-1][-120:]
        print("仍在生成影片…" + log_snip, flush=True)
        last_report = time.time()
    time.sleep(5)

if video_path is None or not video_path.is_file():
    raise RuntimeError("Không tìm thấy MP4 đầu ra từ ComfyUI sau khi kết nối lại.")

if REMOTE_AUDIO and Path(REMOTE_AUDIO).is_file():
    muxed_path = video_path.with_name(video_path.stem + "_muxed.mp4")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path), "-i", str(REMOTE_AUDIO),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0", "-map", "1:a:0", "-shortest", str(muxed_path)
        ], check=True, capture_output=True)
        if muxed_path.is_file() and muxed_path.stat().st_size > 1000:
            video_path = muxed_path
    except Exception:
        pass

REMOTE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(video_path, REMOTE_OUTPUT)
print("COLAB_CLI_OUTPUT_PATH:", REMOTE_OUTPUT, flush=True)
"""
    recover_script.write_text(recover_code, encoding="utf-8")
    for attempt in range(1, 4):
        time.sleep(4 * attempt)
        if on_line:
            on_line(f"[colab] Kết nối WebSocket bị gián đoạn tạm thời — Đang kết nối lại GPU để lấy video (lần {attempt}/3)...")
        try:
            rec_out = call_colab(
                ["exec", "--session", session, "--timeout", str(min(max(float(timeout), 900.0), 2400.0)), "--file", str(recover_script)],
                label=f"recover render for {job_title} (attempt {attempt})",
                timeout=min(max(float(timeout), 900.0), 2400.0) + 60,
                on_line=on_line,
            )
            if "COLAB_CLI_OUTPUT_PATH:" in rec_out:
                return True
        except Exception as rec_exc:
            if on_line:
                on_line(f"[colab] Thử kết nối lại lần {attempt} chưa thành công: {str(rec_exc)[:120]}")
    return False


def run_batch(
    manifest_path: Path,
    *,
    session: str | None,
    gpu: str,
    high_mem: bool,
    stop_on_complete: bool,
    progress_path: Path | None,
    output_dir: Path,
    exec_timeout: float,
    create_session_if_named: bool = False,
) -> dict[str, Any]:
    if not NOTEBOOK.is_file():
        raise FileNotFoundError(f"Bundled inference notebook not found: {NOTEBOOK}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    jobs = resolve_jobs(manifest, output_dir)
    is_anonymous_session = session is None
    owns_session = is_anonymous_session or create_session_if_named
    if session is None:
        session = f"h3-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session):
        raise ValueError("Invalid Colab session name.")

    progress: dict[str, Any] = {
        "task": "batch",
        "status": "starting",
        "session": session,
        "gpu": gpu,
        "jobs": [{"id": job["id"], "title": job["title"], "status": "queued", "output": str(job["output_path"])} for job in jobs],
        "log_tail": [],
        "updated_at": now_iso(),
    }
    write_progress(progress_path, progress)

    def log_line(line: str) -> None:
        # Avoid copying full prompt text into logs/state files.
        if not line or len(line) > 320 or "<Picture" in line or "<Subject" in line or "[Chinese]" in line:
            return
        progress["log_tail"] = (progress["log_tail"] + [line])[-30:]
        progress["updated_at"] = now_iso()
        write_progress(progress_path, progress)

    results: list[dict[str, Any]] = []
    batch_error: str | None = None
    work_root = manifest_path.parent / f"work_{uuid.uuid4().hex[:8]}"
    work_root.mkdir(parents=True, exist_ok=True)
    prev_completed_mp4: Path | None = None
    try:
        if owns_session:
            start_session(session, gpu, high_mem)
        progress["status"] = "running"
        progress["updated_at"] = now_iso()
        write_progress(progress_path, progress)

        uploaded_remote_cache: dict[str, str] = {}
        _r2v_t2v_modes = ("reference", "r2v", "t2v", "text_to_video")
        can_single_pass_multi = (
            len(jobs) >= 2
            and all(
                not j.get("source_video")
                and not j.get("audio_file")
                and not j.get("director_pack")
                and not j.get("guides")
                and str(j.get("lora") or "pdmd").strip().lower() == str(jobs[0].get("lora") or "pdmd").strip().lower()
                and str(j.get("lora") or "pdmd").strip().lower() not in ("fasth3_t2v", "fasth3_trim")
                for j in jobs
            )
            and (
                all(str(j.get("mode") or "reference").strip().lower() in _r2v_t2v_modes for j in jobs)
                or all(
                    str(j.get("mode") or "reference").strip().lower() == "first_frame"
                    and int(j.get("width", 1024)) == int(jobs[0].get("width", 1024))
                    and int(j.get("height", 576)) == int(jobs[0].get("height", 576))
                    and float(j.get("duration_seconds", 12)) == float(jobs[0].get("duration_seconds", 12))
                    and [str(p) for p in j.get("reference_images", [])] == [str(p) for p in jobs[0].get("reference_images", [])]
                    for j in jobs
                )
            )
        )

        for index, job in enumerate(jobs):
            current = progress["jobs"][index]
            _join = str(job.get("chain_join") or "last_frame")
            if not can_single_pass_multi and index > 0 and job.get("chain_last_frame") and prev_completed_mp4 and prev_completed_mp4.is_file() and _join != "cut":
                prev_id = str(jobs[index - 1].get("id") or f"clip_{index:02d}")
                sb_refs_dir = SKILL_DIR / "output" / "storyboard_refs"
                sb_refs_dir.mkdir(parents=True, exist_ok=True)
                _anchor_ref = (job.get("reference_images") or [None])[0]
                lf_png = sb_refs_dir / f"LastFrame_{prev_id}.png"
                if not extract_last_frame_png(prev_completed_mp4, lf_png, _anchor_ref):
                    lf_png = work_root / f"LastFrame_{prev_id}.png"
                if lf_png.is_file() or extract_last_frame_png(prev_completed_mp4, lf_png, _anchor_ref):
                    apply_continuity_frame_to_job(job, lf_png, prev_job_id=prev_id)
                    current["chained_last_frame"] = str(lf_png)
                    current["reference_images"] = [str(p) for p in job.get("reference_images", [])]
                    current["updated_prompt"] = job.get("prompt", "")
                if _join in ("av", "video"):
                    # Mang 39 frame cuối (~1.6s) của đoạn trước sang frame 0 của đoạn này (MiniMaxH3AddGuide)
                    job["guides"] = list(job.get("guides") or []) + [{
                        "path": prev_completed_mp4, "kind": "clip", "frame": 0,
                        "tail_frames": 39, "with_audio": _join == "av",
                    }]
                    current["carry_frames"] = 39

            current.update({"status": "uploading", "started_at": now_iso()})
            progress["current_job"] = job["id"]
            progress["updated_at"] = now_iso()
            write_progress(progress_path, progress)
            remote_refs: list[str] = []
            remote_prefix = f"/content/h3_{job['id']}"
            for image_index, image_path in enumerate(job["reference_images"], start=1):
                cache_key = str(Path(image_path).resolve())
                if cache_key in uploaded_remote_cache:
                    remote_refs.append(uploaded_remote_cache[cache_key])
                    continue
                remote = f"{remote_prefix}_reference_{image_index}.img"
                call_colab(
                    ["upload", "--session", session, str(image_path), remote],
                    label=f"upload reference {image_index} for {job['title']}",
                    timeout=600,
                    on_line=log_line,
                )
                uploaded_remote_cache[cache_key] = remote
                remote_refs.append(remote)

            prompt_path = work_root / f"{job['id']}.txt"
            prompt_path.write_text(job["prompt"], encoding="utf-8")
            remote_prompt = f"{remote_prefix}_prompt.txt"
            remote_output = f"{remote_prefix}_output.mp4"
            call_colab(
                ["upload", "--session", session, str(prompt_path), remote_prompt],
                label=f"upload prompt for {job['title']}",
                timeout=120,
                on_line=log_line,
            )

            remote_audio = ""
            if job.get("audio_file"):
                audio_path = job["audio_file"]
                remote_audio = f"{remote_prefix}_audio{audio_path.suffix or '.mp3'}"
                call_colab(
                    ["upload", "--session", session, str(audio_path), remote_audio],
                    label=f"upload audio for {job['title']}",
                    timeout=300,
                    on_line=log_line,
                )

            remote_source_video = ""
            if job.get("source_video"):
                sv_path = job["source_video"]
                remote_source_video = f"{remote_prefix}_source_video{sv_path.suffix or '.mp4'}"
                call_colab(
                    ["upload", "--session", session, str(sv_path), remote_source_video],
                    label=f"upload source video for {job['title']}",
                    timeout=600,
                    on_line=log_line,
                )

            remote_pack = ""
            if job.get("director_pack"):
                pack_path = job["director_pack"]
                remote_pack = f"{remote_prefix}_pack.zip"
                call_colab(
                    ["upload", "--session", session, str(pack_path), remote_pack],
                    label=f"upload director pack for {job['title']}",
                    timeout=300,
                    on_line=log_line,
                )

            remote_guides: list[dict[str, Any]] = []
            for g_index, g in enumerate(job.get("guides") or []):
                g_path = Path(g["path"])
                remote_g = f"{remote_prefix}_guide_{g_index}{g_path.suffix or '.bin'}"
                call_colab(
                    ["upload", "--session", session, str(g_path), remote_g],
                    label=f"upload guide {g_index + 1} for {job['title']}",
                    timeout=600,
                    on_line=log_line,
                )
                rg = {k: v for k, v in g.items() if k != "path" and isinstance(v, (str, int, float, bool))}
                rg["path"] = remote_g
                remote_guides.append(rg)

            job_notebook_dir = work_root / job["id"]
            job_notebook_dir.mkdir(parents=True, exist_ok=True)
            notebook_copy = job_notebook_dir / "MiniMax_H3_Turbo_Colab.ipynb"
            shutil.copy2(NOTEBOOK, notebook_copy)
            try:
                nb_json = json.loads(notebook_copy.read_text(encoding="utf-8"))
                for cell in nb_json.get("cells", []):
                    if cell.get("cell_type") == "code" and isinstance(cell.get("source"), list):
                        src_joined = "".join(cell["source"])
                        if "SETUP_MARKER = Path(" in src_joined and "MINIMUM_FREE_DISK" in src_joined:
                            src_joined = src_joined.replace(
                                'from pathlib import Path\n',
                                'from pathlib import Path\nCOMFY = Path("/content/ComfyUI")\n',
                                1,
                            )
                            src_joined = src_joined.replace(
                                'INFERENCE_MODE = os.environ.get("H3_INFERENCE_MODE", "reference").strip().lower()',
                                'INFERENCE_MODE = (os.environ.get("H3_INFERENCE_MODE") or "reference").strip().lower() or "reference"\n'
                                'if INFERENCE_MODE in {"cam_previs", "audio_sync", "fasth3_omni"}:\n'
                                '    INFERENCE_MODE = "reference"\n'
                                'elif INFERENCE_MODE in {"fasth3_t2v", "fasth3_trim"}:\n'
                                '    INFERENCE_MODE = "t2v"\n'
                                'elif INFERENCE_MODE == "skeleton_v2v":\n'
                                '    INFERENCE_MODE = "char_swap"',
                            )
                            src_joined = src_joined.replace(
                                'assert shutil.disk_usage("/content").free >= MINIMUM_FREE_DISK, (',
                                '_existing_models_bytes = sum(f.stat().st_size for f in (COMFY / "models").rglob("*") if f.is_file()) if (COMFY / "models").is_dir() else 0\n'
                                'assert (shutil.disk_usage("/content").free + _existing_models_bytes) >= MINIMUM_FREE_DISK, (',
                            )
                            cell["source"] = [ln + "\n" for ln in src_joined.splitlines()]
                        elif "h3_source_video_24fps.mp4" in src_joined and "vf_chain =" in src_joined:
                            src_joined = src_joined.replace(
                                '        vf_chain = f"fps=24,scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2"',
                                '        _sv_w = max(320, (int(WIDTH * 0.56) // 32) * 32) if INFERENCE_MODE in ("reference", "r2v") else WIDTH\n'
                                '        _sv_h = max(320, (int(HEIGHT * 0.56) // 32) * 32) if INFERENCE_MODE in ("reference", "r2v") else HEIGHT\n'
                                '        vf_chain = f"fps=24,scale={_sv_w}:{_sv_h}:force_original_aspect_ratio=decrease,pad={_sv_w}:{_sv_h}:(ow-iw)/2:(oh-ih)/2"',
                            )
                            cell["source"] = [ln + "\n" for ln in src_joined.splitlines()]
                        elif "PORT = 8188" in src_joined and "COMFY_PROCESS" in src_joined:
                            if 'COMFY = Path("/content/ComfyUI")' not in src_joined:
                                cell["source"].insert(0, 'from pathlib import Path; COMFY = Path("/content/ComfyUI")\n')
                        elif 'prompt_id = job["prompt_id"]' in src_joined and '已提交推論：' in src_joined:
                            src_joined = src_joined.replace(
                                'prompt_id = job["prompt_id"]\nprint("已提交推論：", prompt_id)',
                                'prompt_id = job["prompt_id"]\n'
                                'try:\n'
                                '    Path("/content/h3_last_prompt_id.txt").write_text(str(prompt_id), encoding="utf-8")\n'
                                'except Exception:\n'
                                '    pass\n'
                                'print("已提交推論：", prompt_id, flush=True)',
                            )
                            cell["source"] = [ln + "\n" for ln in src_joined.splitlines()]
                        elif "job_timeout_seconds =" in src_joined and "H3_JOB_TIMEOUT_SECONDS" in src_joined:
                            src_joined = src_joined.replace(
                                'job_timeout_seconds = min(float(os.environ.get("H3_JOB_TIMEOUT_SECONDS", "1200" if INFERENCE_MODE == "longshot" else "720")), 1800.0 if INFERENCE_MODE == "longshot" else 900.0)',
                                'job_timeout_seconds = min(float(os.environ.get("H3_JOB_TIMEOUT_SECONDS", "2400")), 3600.0)',
                            )
                            src_joined = src_joined.replace(
                                '    history = requests.get(BASE_URL + f"/history/{prompt_id}", timeout=30).json()',
                                '    try:\n'
                                '        history = requests.get(BASE_URL + f"/history/{prompt_id}", timeout=30).json()\n'
                                '    except Exception:\n'
                                '        history = {}',
                            )
                            src_joined = src_joined.replace(
                                '    if time.time() - last_report >= 30:',
                                '    if time.time() - last_report >= 10:',
                            )
                            src_joined = src_joined.replace(
                                '        print("仍在生成影片…" + log_snip)',
                                '        print("仍在生成影片…" + log_snip, flush=True)',
                            )
                            src_joined = src_joined.replace(
                                '    time.sleep(10)',
                                '    time.sleep(5)',
                            )
                            cell["source"] = [ln + "\n" for ln in src_joined.splitlines()]
                notebook_copy.write_text(json.dumps(nb_json, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass
            eff_mode = (str(job.get("mode") or "reference").strip().lower()) or "reference"
            if eff_mode in ("cam_previs", "audio_sync", "fasth3_omni"):
                eff_mode = "reference"
            elif eff_mode in ("fasth3_t2v", "fasth3_trim"):
                eff_mode = "t2v"
            elif eff_mode == "skeleton_v2v":
                eff_mode = "char_swap"
            eff_exec_timeout = max(float(exec_timeout), 2400.0 * (len(jobs) if can_single_pass_multi else 1))
            remote_multi_file = ""
            if can_single_pass_multi and index == 0:
                multi_payload = []
                for j in jobs[1:]:
                    j_remote_refs: list[str] = []
                    j_prefix = f"/content/h3_{j['id']}"
                    for img_idx, img_p in enumerate(j.get("reference_images", []), start=1):
                        c_key = str(Path(img_p).resolve())
                        if c_key in uploaded_remote_cache:
                            j_remote_refs.append(uploaded_remote_cache[c_key])
                            continue
                        rem_r = f"{j_prefix}_reference_{img_idx}.img"
                        call_colab(
                            ["upload", "--session", session, str(img_p), rem_r],
                            label=f"upload reference {img_idx} for {j['title']}",
                            timeout=600,
                            on_line=log_line,
                        )
                        uploaded_remote_cache[c_key] = rem_r
                        j_remote_refs.append(rem_r)
                    j_eff_mode = (str(j.get("mode") or "reference").strip().lower()) or "reference"
                    if j_eff_mode in ("cam_previs", "audio_sync", "fasth3_omni"):
                        j_eff_mode = "reference"
                    elif j_eff_mode in ("fasth3_t2v", "fasth3_trim"):
                        j_eff_mode = "t2v"
                    multi_payload.append({
                        "id": j["id"],
                        "title": j["title"],
                        "prompt": j["prompt"],
                        "seed": j["seed"],
                        "mode": j_eff_mode,
                        "width": int(j.get("width", 1024)),
                        "height": int(j.get("height", 576)),
                        "duration_seconds": float(j.get("duration_seconds", 12)),
                        "remote_refs": j_remote_refs,
                        "remote_output": f"/content/h3_{j['id']}_output.mp4",
                        "chain_last_frame": bool(j.get("chain_last_frame") and str(j.get("chain_join") or "last_frame") != "cut"),
                    })
                local_multi = work_root / "h3_multi_jobs.json"
                local_multi.write_text(json.dumps(multi_payload, ensure_ascii=False), encoding="utf-8")
                remote_multi_file = f"{remote_prefix}_multi_jobs.json"
                call_colab(
                    ["upload", "--session", session, str(local_multi), remote_multi_file],
                    label="upload multi-clip manifest",
                    timeout=120,
                    on_line=log_line,
                )

            env_values = [
                "H3_INFERENCE_MODE=" + eff_mode,
                "H3_LORA=" + (str(job.get("lora") or "pdmd").strip().lower() or "pdmd"),
                "H3_REFERENCE_IMAGES=" + json.dumps(remote_refs, separators=(",", ":")),
                "H3_INPUT_IMAGE=" + (remote_refs[0] if remote_refs else ""),
                "H3_PROMPT_FILE=" + remote_prompt,
                "H3_DURATION_SECONDS=" + str(job["duration_seconds"]),
                "H3_WIDTH=" + str(job.get("width", 1024)),
                "H3_HEIGHT=" + str(job.get("height", 576)),
                "H3_REF_SIZE=" + str(job.get("ref_size", "match")),
                "H3_SEED=" + str(job["seed"]),
                "H3_OUTPUT_PREFIX=MiniMax_H3_" + job["id"][:20],
                "H3_OUTPUT_PATH=" + remote_output,
                "H3_JOB_TIMEOUT_SECONDS=" + str(min(eff_exec_timeout, 3600.0)),
                "H3_AUDIO_PATH=" + remote_audio,
                "H3_AUDIO_AS_REF=" + (("1" if job.get("audio_as_ref") else os.environ.get("H3_AUDIO_AS_REF", "0")) if remote_audio else "0"),
                "H3_SOURCE_VIDEO=" + remote_source_video,
                "H3_DIRECTOR_PACK=" + remote_pack,
                "H3_GUIDES=" + json.dumps(remote_guides, separators=(",", ":")),
                "H3_AI_UPSCALE=" + ("1" if str(job.get("ai_upscale", False)).lower() in ("true", "1") else "0"),
                "H3_SELFLIFT=" + ("1" if job.get("selflift", False) else "0"),
                "H3_TST=" + ("1" if job.get("tst", True) else "0"),
                "H3_MASKED_SWAP=" + ("1" if job.get("masked_swap", True) else "0"),
                "H3_SWAP_EDIT_MODE=" + str(job.get("swap_edit_mode") or "person"),
                "H3_SWAP_TARGET=" + str(job.get("swap_target") or "person"),
                "H3_SWAP_HAT_FIX=" + ("1" if job.get("swap_hat_fix", False) else "0"),
                "H3_SWAP_NO_LIPSYNC=" + ("1" if job.get("swap_no_lipsync", False) else "0"),
                "H3_FACE_REFINE=" + ("1" if job.get("face_refine", False) else "0"),
                "H3_COLOR_GRADE=" + ("1" if job.get("color_grade", True) else "0"),
                "H3_MULTI_JOBS_FILE=" + remote_multi_file,
            ]
            exec_args = ["exec", "--session", session, "--timeout", str(eff_exec_timeout)]
            for value in env_values:
                exec_args.extend(["--env", value])
            exec_args.extend(["--file", str(notebook_copy)])
            current.update({"status": "generating", "seed": job["seed"], "duration_seconds": job["duration_seconds"]})
            progress["updated_at"] = now_iso()
            write_progress(progress_path, progress)

            exec_state = {"submitted_prompt_id": "", "comfy_started": False}
            bg_dl_threads: list[threading.Thread] = []
            downloaded_indices: set[int] = set()
            failed_indices: set[int] = set()

            def _spawn_clip_download(clip_idx: int, rem_out: str) -> None:
                if clip_idx in downloaded_indices or clip_idx in failed_indices or clip_idx >= len(jobs):
                    return
                downloaded_indices.add(clip_idx)
                target_job = jobs[clip_idx]
                target_prog = progress["jobs"][clip_idx]

                def _dl_worker() -> None:
                    target_prog["status"] = "downloading"
                    progress["updated_at"] = now_iso()
                    write_progress(progress_path, progress)
                    try:
                        call_colab(
                            ["download", "--session", session, rem_out, str(target_job["output_path"])],
                            label=f"download {target_job['title']}",
                            timeout=900,
                            on_line=log_line,
                        )
                    except Exception:
                        time.sleep(4)
                        call_colab(
                            ["download", "--session", session, rem_out, str(target_job["output_path"])],
                            label=f"retry download {target_job['title']}",
                            timeout=900,
                            on_line=log_line,
                        )
                    verify_mp4(target_job["output_path"])
                    target_prog.update({
                        "status": "completed",
                        "finished_at": now_iso(),
                        "bytes": target_job["output_path"].stat().st_size,
                    })
                    results.append({"id": target_job["id"], "output": str(target_job["output_path"]), "status": "completed"})
                    progress["updated_at"] = now_iso()
                    write_progress(progress_path, progress)

                t_dl = threading.Thread(target=_dl_worker, daemon=True)
                t_dl.start()
                bg_dl_threads.append(t_dl)

            def _on_exec_line(ln: str) -> None:
                log_line(ln)
                if "已提交推論：" in ln:
                    exec_state["comfy_started"] = True
                    exec_state["submitted_prompt_id"] = ln.split("已提交推論：", 1)[1].strip()
                elif "仍在生成影片" in ln or "ComfyUI 已就緒" in ln:
                    exec_state["comfy_started"] = True
                if can_single_pass_multi:
                    if "COLAB_CLI_OUTPUT_PATH:" in ln:
                        _spawn_clip_download(0, remote_output)
                        if len(jobs) > 1:
                            progress["current_job"] = jobs[1]["id"]
                            progress["jobs"][1].update({"status": "generating", "started_at": now_iso(), "seed": jobs[1]["seed"], "duration_seconds": jobs[1]["duration_seconds"]})
                            progress["updated_at"] = now_iso()
                            write_progress(progress_path, progress)
                    elif "H3_MULTI_CLIP_READY:" in ln:
                        try:
                            _rest = ln.split("H3_MULTI_CLIP_READY:", 1)[1].strip()
                            _idx_s, _rem_p = _rest.split(":", 1)
                            _c_idx = int(_idx_s)
                            _spawn_clip_download(_c_idx, _rem_p.strip())
                            if _c_idx + 1 < len(jobs):
                                progress["current_job"] = jobs[_c_idx + 1]["id"]
                                progress["jobs"][_c_idx + 1].update({"status": "generating", "started_at": now_iso(), "seed": jobs[_c_idx + 1]["seed"], "duration_seconds": jobs[_c_idx + 1]["duration_seconds"]})
                                progress["updated_at"] = now_iso()
                                write_progress(progress_path, progress)
                        except Exception:
                            pass
                    elif "H3_MULTI_CLIP_ERROR:" in ln:
                        try:
                            _rest = ln.split("H3_MULTI_CLIP_ERROR:", 1)[1].strip()
                            _idx_s, _err_m = _rest.split(":", 1)
                            _c_idx = int(_idx_s)
                            failed_indices.add(_c_idx)
                            if _c_idx < len(progress["jobs"]):
                                progress["jobs"][_c_idx].update({"status": "failed", "error": _err_m.strip(), "finished_at": now_iso()})
                            if _c_idx + 1 < len(jobs):
                                progress["current_job"] = jobs[_c_idx + 1]["id"]
                                progress["jobs"][_c_idx + 1].update({"status": "generating", "started_at": now_iso(), "seed": jobs[_c_idx + 1]["seed"], "duration_seconds": jobs[_c_idx + 1]["duration_seconds"]})
                            progress["updated_at"] = now_iso()
                            write_progress(progress_path, progress)
                        except Exception:
                            pass

            try:
                call_colab(exec_args, label=f"generate {job['title']}", timeout=eff_exec_timeout + 60, on_line=_on_exec_line)
            except (ColabCommandError, ColabTimeoutError) as exec_err:
                err_str = str(exec_err)
                is_ws_drop = ("Connection was lost" in err_str or "idle timeout" in err_str or isinstance(exec_err, ColabTimeoutError))
                if exec_state["comfy_started"] and is_ws_drop:
                    recovered = _recover_comfy_render_after_ws_drop(
                        session=session,
                        job_id=job["id"],
                        job_title=job["title"],
                        submitted_prompt_id=exec_state["submitted_prompt_id"],
                        remote_output=remote_output,
                        remote_audio=remote_audio,
                        work_dir=job_notebook_dir,
                        timeout=eff_exec_timeout,
                        on_line=log_line,
                    )
                    if not recovered:
                        raise
                elif is_ws_drop:
                    log_line("[colab] Kết nối WebSocket bị ngắt trước khi gửi lệnh render — Đang tự động kết nối lại phiên GPU...")
                    time.sleep(5)
                    try:
                        call_colab(exec_args, label=f"retry generate {job['title']}", timeout=eff_exec_timeout + 60, on_line=_on_exec_line)
                    except (ColabCommandError, ColabTimeoutError) as retry_err:
                        if exec_state["comfy_started"]:
                            recovered = _recover_comfy_render_after_ws_drop(
                                session=session,
                                job_id=job["id"],
                                job_title=job["title"],
                                submitted_prompt_id=exec_state["submitted_prompt_id"],
                                remote_output=remote_output,
                                remote_audio=remote_audio,
                                work_dir=job_notebook_dir,
                                timeout=eff_exec_timeout,
                                on_line=log_line,
                            )
                            if not recovered:
                                raise retry_err
                        else:
                            raise
                else:
                    raise

            if can_single_pass_multi:
                _spawn_clip_download(0, remote_output)
                for _m_i in range(1, len(jobs)):
                    _spawn_clip_download(_m_i, f"/content/h3_{jobs[_m_i]['id']}_output.mp4")
                for _t in bg_dl_threads:
                    _t.join(timeout=960)
                break

            current["status"] = "downloading"
            progress["updated_at"] = now_iso()
            write_progress(progress_path, progress)
            try:
                call_colab(
                    ["download", "--session", session, remote_output, str(job["output_path"])],
                    label=f"download {job['title']}",
                    timeout=900,
                    on_line=log_line,
                )
            except Exception:
                time.sleep(4)
                call_colab(
                    ["download", "--session", session, remote_output, str(job["output_path"])],
                    label=f"retry download {job['title']}",
                    timeout=900,
                    on_line=log_line,
                )
            verify_mp4(job["output_path"])
            prev_completed_mp4 = job["output_path"]
            current.update({"status": "completed", "finished_at": now_iso(), "bytes": job["output_path"].stat().st_size})
            results.append({"id": job["id"], "output": str(job["output_path"]), "status": "completed"})
            progress["updated_at"] = now_iso()
            write_progress(progress_path, progress)
    except ColabTimeoutError as exc:
        batch_error = str(exc)
        if "current_job" in progress:
            current = next((item for item in progress["jobs"] if item["id"] == progress["current_job"]), None)
            if current and current["status"] not in {"completed", "failed"}:
                current.update({"status": "failed", "error": batch_error, "finished_at": now_iso()})
        for item in progress["jobs"]:
            if item["status"] == "queued":
                item["status"] = "cancelled"
    except Exception as exc:
        batch_error = str(exc)
        if "current_job" in progress:
            current = next((item for item in progress["jobs"] if item["id"] == progress["current_job"]), None)
            if current and current["status"] not in {"completed", "failed"}:
                current.update({"status": "failed", "error": batch_error, "finished_at": now_iso()})
        for item in progress["jobs"]:
            if item["status"] == "queued":
                item["status"] = "cancelled"
    finally:
        if (is_anonymous_session or stop_on_complete or batch_error) and session:
            progress["status"] = "stopping_session"
            progress["updated_at"] = now_iso()
            write_progress(progress_path, progress)
            try:
                stop_session(session)
                progress["session_status"] = "stopped"
            except Exception as exc:
                progress["session_status"] = "stop_failed"
                progress["cleanup_error"] = str(exc)
                batch_error = batch_error or f"Batch ended, but Colab session {session} could not be stopped: {exc}"
        else:
            progress["session_status"] = "active"
        shutil.rmtree(work_root, ignore_errors=True)

    completed = sum(item["status"] == "completed" for item in progress["jobs"])
    failed = sum(item["status"] == "failed" for item in progress["jobs"])
    cancelled = sum(item["status"] == "cancelled" for item in progress["jobs"])
    progress.update({
        "status": "completed" if not batch_error and completed == len(jobs) else "partial" if completed else "failed",
        "completed_count": completed,
        "failed_count": failed,
        "cancelled_count": cancelled,
        "error": batch_error,
        "updated_at": now_iso(),
        "results": results,
    })
    write_progress(progress_path, progress)
    return progress


def main() -> int:
    parser = argparse.ArgumentParser(description="MiniMax H3 Colab session and batch runner")
    sub = parser.add_subparsers(dest="command", required=True)

    usage_parser = sub.add_parser("usage", help="Read Colab compute-unit balance and usage rate")
    usage_parser.add_argument("--json", action="store_true", help="Print parsed fields as JSON")

    start_parser = sub.add_parser("start", help="Create a persistent Colab GPU session")
    start_parser.add_argument("--session", required=True)
    start_parser.add_argument("--gpu", default="A100")
    start_parser.add_argument("--no-high-mem", action="store_true")
    start_parser.add_argument("--progress", type=Path)

    stop_parser = sub.add_parser("stop", help="Stop a Colab session")
    stop_parser.add_argument("--session", required=True)
    stop_parser.add_argument("--progress", type=Path)

    batch_parser = sub.add_parser("batch", help="Render multiple videos sequentially on one Colab session")
    batch_parser.add_argument("--manifest", type=Path, required=True)
    batch_parser.add_argument("--session", help="Reuse an existing session instead of creating one")
    batch_parser.add_argument("--gpu", default=os.environ.get("COLAB_GPU", "A100"))
    batch_parser.add_argument("--no-high-mem", action="store_true")
    batch_parser.add_argument("--stop-on-complete", action="store_true", help="Stop even a provided session after this queue")
    batch_parser.add_argument("--progress", type=Path)
    batch_parser.add_argument("--output-dir", type=Path, default=Path("output"))
    batch_parser.add_argument("--timeout", type=float, default=float(os.environ.get("COLAB_EXEC_TIMEOUT", "2400")))

    single_parser = sub.add_parser("single", help="Compatibility interface for run_colab_inference.sh")
    single_parser.add_argument("--image", "-i", action="append", default=[])
    single_parser.add_argument("--prompt", "-p", default="", required=False)
    single_parser.add_argument("--output", "-o")
    single_parser.add_argument("--audio", "-a", help="Optional audio file (MP3/WAV)")
    single_parser.add_argument("--source-video", "-v", help="Optional source video file (<Video 1>) for Character Swap")
    single_parser.add_argument("--mode", "-m", default=os.environ.get("H3_INFERENCE_MODE", "reference"))
    single_parser.add_argument("--lora", "-l", default=os.environ.get("H3_LORA", "pdmd"))
    single_parser.add_argument("--pack", help="Optional director pack zip")
    single_parser.add_argument("--guides", help="Optional JSON file: list of AddGuide anchors [{path, kind, second|frame, with_audio}]")
    single_parser.add_argument("--gpu", default=os.environ.get("COLAB_GPU", "A100"))
    single_parser.add_argument("--no-high-mem", action="store_true")
    single_parser.add_argument("--progress", type=Path)
    single_parser.add_argument("--timeout", type=float, default=float(os.environ.get("COLAB_EXEC_TIMEOUT", "2400")))

    args = parser.parse_args()
    try:
        if args.command == "usage":
            result = get_usage()
            if args.json:
                print(json.dumps({"ok": True, **result}, ensure_ascii=False))
            else:
                print(f"Current balance: {result['balance']:.2f} compute units")
                print(f"Usage rate: {result['rate_per_hour']:.2f}/hr")
                print(f"Active assignments: {result['active_assignments']}")
            return 0
        if args.command == "start":
            start_session(args.session, args.gpu, not args.no_high_mem, args.progress)
            if args.progress is None:
                print(f"Colab session ready: {args.session}")
            return 0
        if args.command == "stop":
            stop_session(args.session, args.progress)
            if args.progress is None:
                print(f"Stopped Colab session: {args.session}")
            return 0
        if args.command == "single":
            if args.image:
                first = normalize_path(args.image[0])
                output = normalize_path(args.output) if args.output else first.with_name(first.stem + "_minimax_h3.mp4")
            else:
                first = Path.cwd() / "minimax_h3"
                output = normalize_path(args.output) if args.output else first.with_name(f"minimax_h3_{int(time.time())}.mp4")
            
            p_arg = args.prompt or ""
            p_file = None
            p_text = ""
            if p_arg:
                p_cand = normalize_path(p_arg)
                if p_cand.is_file() and p_cand.stat().st_size > 0:
                    p_file = str(p_cand)
                elif not p_cand.is_dir():
                    p_text = str(p_arg)

            guides_list: list[Any] = []
            if getattr(args, "guides", None):
                try:
                    g_file = normalize_path(args.guides)
                    if g_file and g_file.is_file():
                        guides_list = json.loads(g_file.read_text(encoding="utf-8")) or []
                except Exception as g_err:
                    print(f"Warning: cannot read guides file: {g_err}", file=sys.stderr)

            manifest = {
                "jobs": [{
                    "title": output.stem,
                    "prompt_file": p_file,
                    "prompt": p_text,
                    "reference_images": [str(normalize_path(img)) for img in (args.image or [])],
                    "duration_seconds": float(os.environ.get("H3_DURATION_SECONDS", "12")),
                    "seed": int(os.environ["H3_SEED"]) if (os.environ.get("H3_SEED") and os.environ.get("H3_SEED").isdigit()) else random.randint(1, 2**31 - 1),
                    "output_path": str(output),
                    "mode": args.mode,
                    "lora": args.lora,
                    "audio_file": str(normalize_path(args.audio)) if args.audio else None,
                    "source_video": str(normalize_path(args.source_video)) if args.source_video else None,
                    "director_pack": str(normalize_path(args.pack)) if args.pack else None,
                    "guides": guides_list,
                }]
            }
            temp_manifest = output.parent / f".h3_{uuid.uuid4().hex[:10]}.json"
            temp_manifest.parent.mkdir(parents=True, exist_ok=True)
            temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            try:
                progress = run_batch(
                    temp_manifest,
                    session=os.environ.get("COLAB_SESSION_NAME"),
                    gpu=args.gpu,
                    high_mem=not args.no_high_mem,
                    stop_on_complete=os.environ.get("H3_STOP_ON_COMPLETE", "1") == "1",
                    progress_path=normalize_path(args.progress) if args.progress else None,
                    output_dir=output.parent,
                    exec_timeout=args.timeout,
                    create_session_if_named=True,
                )
            finally:
                temp_manifest.unlink(missing_ok=True)
            if progress["status"] != "completed":
                print(progress.get("error") or "One or more videos failed.", file=sys.stderr)
                return 1
            print(f"Saved inference output: {output}")
            return 0
        if args.command == "batch":
            batch_sess = args.session or os.environ.get("COLAB_SESSION_NAME")
            stop_flag = args.stop_on_complete or (os.environ.get("H3_STOP_ON_COMPLETE", "0") == "1" if batch_sess else True)
            progress = run_batch(
                normalize_path(args.manifest),
                session=batch_sess,
                gpu=args.gpu,
                high_mem=not args.no_high_mem,
                stop_on_complete=stop_flag,
                progress_path=normalize_path(args.progress) if args.progress else None,
                output_dir=normalize_path(args.output_dir),
                exec_timeout=args.timeout,
                create_session_if_named=bool(batch_sess),
            )
            print(json.dumps(progress, ensure_ascii=False))
            return 0 if progress["status"] == "completed" else 1
    except Exception as exc:
        if args.command == "usage" and args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
