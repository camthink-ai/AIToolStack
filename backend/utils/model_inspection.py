"""Sandboxed inspection of uploaded YOLO .pt models.

Loading a .pt file with ``YOLO()`` runs ``torch.load`` on a pickle, which
executes arbitrary code embedded in the file. We never trust the upload, so
inspection happens in a short-lived subprocess with ``weights_only`` forced
for every ``torch.load`` call, blocking pickle-based code execution while
still allowing genuine Ultralytics checkpoints to be read.

The subprocess reports the model's class count/names so the upload endpoint
no longer needs to load untrusted files inside the API process at all.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

INSPECT_TIMEOUT_SECONDS = 120

# Environment variables forcing weights_only=True regardless of what the
# loading code requests (supported by recent PyTorch; harmless if ignored).
_SAFE_LOAD_ENV = {
    "TORCH_FORCE_WEIGHTS_ONLY_LOAD": "1",
    "TORCH_FORCE_WEIGHTS_ONLY": "1",
}

_INSPECT_SCRIPT = r"""
import json, sys

path = sys.argv[1]
result = {"num_classes": None, "class_names": None, "model_type": None}

from ultralytics import YOLO
model = YOLO(path)

if hasattr(model, "names") and model.names:
    names = model.names
    if isinstance(names, dict):
        result["num_classes"] = len(names)
        result["class_names"] = [str(names[i]) for i in sorted(names)]
    elif isinstance(names, (list, tuple)):
        result["num_classes"] = len(names)
        result["class_names"] = [str(n) for n in names]
elif hasattr(model, "model") and hasattr(model.model, "nc"):
    result["num_classes"] = int(model.model.nc)
    inner = getattr(model.model, "names", None)
    if isinstance(inner, dict):
        result["class_names"] = [str(inner[i]) for i in sorted(inner)]

print("__MODEL_INFO__" + json.dumps(result))
"""


class UnsafeModelError(Exception):
    """Raised when a model cannot be loaded with safe (weights-only) loading.

    This almost always means the pickle contains code or non-standard objects
    and must be treated as untrusted.
    """


def inspect_yolo_model(
    model_path: Path,
    timeout: int = INSPECT_TIMEOUT_SECONDS,
) -> Optional[dict]:
    """Inspect a .pt model in an isolated subprocess.

    Returns a dict with num_classes / class_names when the model is safe.

    Raises UnsafeModelError when the model fails safe loading (malicious or
    incompatible pickle) and subprocess.TimeoutExpired on hang.
    """
    cmd = [sys.executable, "-c", _INSPECT_SCRIPT, str(model_path)]
    env = _build_safe_env()

    logger.info(f"[ModelSecurity] Sandbox-inspecting model: {model_path}")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise UnsafeModelError(
            "Model inspection timed out; the file was not accepted"
        )

    if proc.returncode != 0:
        stderr_tail = (proc.stderr or "").strip().splitlines()[-3:]
        logger.warning(
            f"[ModelSecurity] Safe model load FAILED (rc={proc.returncode}): "
            f"{' | '.join(stderr_tail)}"
        )
        raise UnsafeModelError(
            "Model was rejected: it cannot be loaded safely. "
            "Only genuine Ultralytics .pt checkpoints are accepted. "
            f"(loader error: {stderr_tail[-1][:200] if stderr_tail else 'unknown'})"
        )

    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith("__MODEL_INFO__"):
            try:
                info = json.loads(line[len("__MODEL_INFO__"):])
                logger.info(
                    f"[ModelSecurity] Safe load OK: {info.get('num_classes')} classes"
                )
                return info
            except json.JSONDecodeError:
                break

    raise UnsafeModelError("Model inspection produced no usable metadata")


def _build_safe_env() -> dict:
    env = dict(os.environ)
    env.update(_SAFE_LOAD_ENV)
    # Keep the child quiet except for what we parse
    env.setdefault("PYTHONWARNINGS", "ignore")
    return env
