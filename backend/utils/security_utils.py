"""Shared security utilities.

Central helpers for:
- Upload size limits (chunked reads so a huge body can't exhaust memory)
- Zip extraction with Zip-Slip and zip-bomb protection
- SSRF validation for user-supplied broker hosts
- Audit logging for sensitive operations
- Password masking over the API
- Certificate common-name validation
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import re
import socket
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from backend.config import settings

logger = logging.getLogger(__name__)

# Sentinel returned instead of real passwords in API responses. When a client
# sends this value back on an update, the stored secret is kept unchanged.
PASSWORD_MASK = "__SAVED__"

AUDIT_LOG_PATH = settings.DATASETS_ROOT.parent / "data" / "audit.log"

_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def _mb(value_mb: int) -> int:
    return value_mb * 1024 * 1024


async def save_upload_limited(upload_file, dest_path: Path, max_mb: int) -> int:
    """Stream an UploadFile to disk, enforcing a hard size cap.

    Raises ValueError when the upload exceeds max_mb so callers can map it to
    HTTP 413. Chunked writing avoids loading the whole file into memory.
    """
    max_bytes = _mb(max_mb)
    written = 0
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as out:
        while True:
            chunk = await upload_file.read(_CHUNK_SIZE)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                out.close()
                try:
                    os.unlink(dest_path)
                except OSError:
                    pass
                raise ValueError(
                    f"Uploaded file exceeds the maximum allowed size of {max_mb} MB"
                )
            out.write(chunk)
    return written


def extract_zip_safe(
    zip_path: Path,
    dest_dir: Path,
    *,
    allowed_extensions: Optional[Iterable[str]] = None,
    max_uncompressed_mb: Optional[int] = None,
    max_files: int = 20000,
    max_ratio: int = 300,
    flatten: bool = False,
) -> List[Path]:
    """Extract a zip archive with Zip-Slip and zip-bomb protection.

    - Every entry target is resolved and verified to stay inside dest_dir.
    - Total uncompressed size and compression ratio are bounded (zip bomb).
    - When allowed_extensions is given, only matching entries are extracted.
    - When flatten is True, files are extracted directly into dest_dir with
      de-duplicated names (directory structure is discarded).

    Returns the list of extracted file paths.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest_dir.resolve()

    if max_uncompressed_mb is None:
        max_uncompressed_mb = _mb(settings.MAX_ZIP_UNCOMPRESSED_MB)
    else:
        max_uncompressed_mb = _mb(max_uncompressed_mb)

    allowed = (
        {e.lower() for e in allowed_extensions} if allowed_extensions else None
    )

    extracted: List[Path] = []
    total_uncompressed = 0
    used_names: set = set()

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) > max_files:
            raise ValueError(f"Archive contains too many files (>{max_files})")

        # Cheap bomb pre-check against declared sizes before touching the disk
        for m in members:
            total_uncompressed += m.file_size
        if total_uncompressed > max_uncompressed_mb:
            raise ValueError(
                f"Archive expands to {total_uncompressed // (1024 * 1024)} MB, "
                f"exceeding the {max_uncompressed_mb // (1024 * 1024)} MB limit"
            )
        total_compressed = sum(m.compress_size for m in members) or 1
        if total_uncompressed / total_compressed > max_ratio and total_uncompressed > _mb(64):
            raise ValueError("Archive has a suspicious compression ratio (possible zip bomb)")

        for member in members:
            name = member.filename
            # Normalize separators and reject traversal segments outright
            normalized = name.replace("\\", "/")
            parts = [p for p in normalized.split("/") if p not in ("", ".")]
            if any(p == ".." for p in parts) or normalized.startswith("/"):
                logger.warning(f"[Security] Skipped zip entry with path traversal: {name!r}")
                continue
            if allowed is not None:
                ext = Path(normalized).suffix.lower()
                if ext not in allowed:
                    continue

            if flatten:
                base = Path(parts[-1]).name if parts else "file"
                stem, suffix = Path(base).stem, Path(base).suffix
                candidate = base
                counter = 1
                while candidate in used_names:
                    candidate = f"{stem}_{counter}{suffix}"
                    counter += 1
                target = (dest_resolved / candidate)
            else:
                target = dest_resolved.joinpath(*parts)

            resolved_target = target.resolve()
            if not resolved_target.is_relative_to(dest_resolved):
                logger.warning(f"[Security] Blocked zip entry escaping destination: {name!r}")
                continue

            # zipfile.extract would create intermediate dirs; do the same here
            resolved_target.parent.mkdir(parents=True, exist_ok=True)

            with zf.open(member) as src, open(resolved_target, "wb") as out:
                written = 0
                while True:
                    chunk = src.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > member.file_size:
                        # Declared size lied; stop before a bomb inflates on disk
                        raise ValueError(f"Zip entry {name!r} is larger than declared")
                    out.write(chunk)

            used_names.add(resolved_target.name)
            extracted.append(resolved_target)

    return extracted


# Hosts that must never be reachable through user-supplied broker test requests
_SSRF_BLOCKED_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
]

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


def validate_broker_host(host: str) -> str:
    """Validate a user-supplied broker host before the server connects to it.

    LAN brokers are a legitimate use case here, so private ranges stay allowed;
    we only reject malformed values and infrastructure-critical targets
    (link-local metadata, unspecified and loopback wildcard addresses).
    """
    host = (host or "").strip()
    if not host or len(host) > 253:
        raise ValueError("Invalid broker host")
    # Reject anything that smells like a URL or contains shell/whitespace chars
    if re.search(r"[\s/:@?#\[\]]", host):
        raise ValueError("Invalid broker host: expected a bare hostname or IP address")

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME_RE.match(host):
            raise ValueError("Invalid broker host format")
        return host

    for network in _SSRF_BLOCKED_NETWORKS:
        if addr in network:
            raise ValueError(f"Broker host {host} is not allowed")
    return host


def validate_common_name(common_name: str) -> str:
    """Validate a certificate CN so it is safe for openssl.cnf and filenames."""
    cn = (common_name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", cn):
        raise ValueError(
            "Invalid common_name: use 1-64 characters (letters, digits, dot, dash, underscore), "
            "starting with a letter or digit"
        )
    return cn


def mask_password(value: Optional[str]) -> Optional[str]:
    """Return the mask sentinel for API responses, hiding the real secret."""
    return PASSWORD_MASK if value else value


def unmask_password(incoming: Optional[str], stored: Optional[str]) -> Optional[str]:
    """Resolve a client-supplied password: keep the stored one when masked."""
    if incoming == PASSWORD_MASK:
        return stored
    return incoming


def audit_event(
    action: str,
    *,
    result: str = "success",
    client_ip: Optional[str] = None,
    **details,
) -> None:
    """Append a JSON line describing a sensitive operation to the audit log."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "result": result,
        "client_ip": client_ip,
        "details": details,
    }
    line = json.dumps(event, ensure_ascii=False, default=str)
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        try:
            os.chmod(AUDIT_LOG_PATH, 0o600)
        except OSError:
            pass
    except Exception:
        logger.warning(f"[Audit] Failed to write audit event: {line}")
    logger.info(f"[Audit] {line}")


def client_ip_from_request(request) -> str:
    try:
        return request.client.host if request and request.client else "unknown"
    except Exception:
        return "unknown"


# ========== API key authentication ==========

def get_configured_api_keys() -> List[str]:
    """Parse the configured API keys (comma-separated API_KEY setting)."""
    raw = (settings.API_KEY or "").strip()
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def extract_presented_key(request) -> Optional[str]:
    """Pull the API key from a request: X-API-Key header, Bearer token or
    api_key query parameter (the query parameter exists for <img> tags and
    WebSocket URLs, which cannot send custom headers)."""
    key = request.headers.get("X-API-Key")
    if not key:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[len("bearer "):].strip()
    if not key:
        try:
            key = request.query_params.get("api_key")
        except Exception:
            key = None
    return (key or "").strip() or None


def is_valid_api_key(presented: Optional[str]) -> bool:
    if not presented:
        return False
    configured = get_configured_api_keys()
    return any(hmac.compare_digest(presented, expected) for expected in configured)


# ========== WebSocket helpers ==========

def is_allowed_websocket_origin(websocket) -> bool:
    """Reject cross-origin browser WebSocket connections (CSWSH).

    Non-browser clients may omit Origin entirely and are allowed; a browser
    Origin must match the server host or an explicitly configured CORS origin.
    """
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    try:
        from urllib.parse import urlparse
        origin_host = urlparse(origin).netloc
        host_header = websocket.headers.get("host", "")
    except Exception:
        return False
    if origin_host and origin_host == host_header:
        return True
    allowed = [o.strip() for o in (settings.CORS_ORIGINS or "").split(",") if o.strip()]
    return origin in allowed or origin_host in allowed
