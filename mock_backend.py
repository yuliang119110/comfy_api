"""
Mock ComfyUI backend.

When COMFY_MOCK=true (or mock_mode=true in config.yaml), every
generation endpoint returns pre-made placeholder images/videos so
that the full routing / load-balancing / workflow-building chain can
be exercised without real GPU or model files.

Switch to real mode by either:
  - removing COMFY_MOCK env var  /  setting mock_mode: false in config.yaml
  - dropping real workflow templates into comfy_api/templates/
"""
from __future__ import annotations

import base64
import os
import struct
import time
import uuid
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Tiny placeholder assets built in-memory ──────────────────────────

def _make_minimal_png(width: int = 64, height: int = 64, color_rgb: tuple = (120, 80, 200)) -> bytes:
    """Return a minimal valid PNG filled with a solid colour."""
    r, g, b = color_rgb
    raw = b""
    for _ in range(height):
        raw += b"\x00"  # filter type=None for each row
        raw += bytes([r, g, b] * width)

    def _chunk(name: bytes, data: bytes) -> bytes:
        c = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", c)

    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(raw))
    png += _chunk(b"IEND", b"")
    return png


# Task colour map: different hue per modality so the tester can visually verify routing
_TASK_COLOURS = {
    "txt2img":         (80,  160, 240),   # blue
    "img2img":         (240, 120,  80),   # orange
    "txt_img2img":     (80,  200, 120),   # green
    "txt2vid":         (200,  80, 200),   # purple
    "img2vid":         (240, 200,  60),   # yellow
    "vid2vid":         (60,  220, 220),   # cyan
    "txt_img2vid":     (240,  60, 120),   # pink
    "txt_img_vid2vid": (180, 180,  60),   # olive
}


# ── Mock store ────────────────────────────────────────────────────────

class _MockTask:
    def __init__(self, prompt_id: str, task_name: str, params: Dict[str, Any]):
        self.prompt_id = prompt_id
        self.task_name = task_name
        self.params = params
        self.created_at = time.time()
        self.node_id = "mock-node"


_task_store: Dict[str, _MockTask] = {}
_output_dir: Optional[Path] = None


def _ensure_output_dir() -> Path:
    global _output_dir
    if _output_dir is None:
        from pathlib import Path as _Path
        _output_dir = _Path(__file__).parent / "static" / "outputs" / "mock"
        _output_dir.mkdir(parents=True, exist_ok=True)
    return _output_dir


def is_mock_mode() -> bool:
    """Check if mock mode is enabled via env var or config."""
    env = os.getenv("COMFY_MOCK", "").lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    # Fall back to config.yaml
    try:
        from .config import load_config
        cfg = load_config()
        return getattr(cfg, "mock_mode", False)
    except Exception:
        return False


def register_mock_task(task_name: str, params: Dict[str, Any]) -> str:
    """Create a mock task and return its prompt_id."""
    prompt_id = str(uuid.uuid4())
    _task_store[prompt_id] = _MockTask(prompt_id, task_name, params)
    return prompt_id


def get_mock_outputs(prompt_id: str) -> List[Dict[str, Any]]:
    """Return list of mock output file descriptors (like ComfyUI history API)."""
    task = _task_store.get(prompt_id)
    if task is None:
        return []

    out_dir = _ensure_output_dir()
    colour = _TASK_COLOURS.get(task.task_name, (128, 128, 128))
    is_video = "vid" in task.task_name

    if is_video:
        # For video: produce a sequence of 8 tiny PNGs simulating frames
        filenames = []
        for i in range(8):
            fname = f"{prompt_id}_frame{i:04d}.png"
            fpath = out_dir / fname
            if not fpath.exists():
                png = _make_minimal_png(64, 64, colour)
                fpath.write_bytes(png)
            filenames.append({
                "filename": fname,
                "subfolder": "mock",
                "type": "output",
                "url": f"/outputs/mock/{fname}",
            })
        return filenames
    else:
        fname = f"{prompt_id}_image.png"
        fpath = out_dir / fname
        if not fpath.exists():
            png = _make_minimal_png(
                task.params.get("width", 64),
                task.params.get("height", 64),
                colour,
            )
            fpath.write_bytes(png)
        return [{
            "filename": fname,
            "subfolder": "mock",
            "type": "output",
            "url": f"/outputs/mock/{fname}",
        }]
