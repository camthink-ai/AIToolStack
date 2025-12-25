"""API route definitions"""
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Query, Form, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional, Union
from pydantic import BaseModel
import uuid
import json
from pathlib import Path
from datetime import datetime
import zipfile
import shutil
import sys
import os
import subprocess
import yaml
import re

from backend.models.database import get_db, Project, Image, Class, Annotation, TrainingRecord, ModelRegistry, Device, DeviceReport
from backend.services.websocket_manager import websocket_manager
import logging

logger = logging.getLogger(__name__)
from backend.services.mqtt_service import mqtt_service
from backend.services.mqtt_config_service import MQTTConfig, mqtt_config_service
from backend.services.external_broker_service import (
    external_broker_service,
    ExternalBrokerCreate,
    ExternalBrokerUpdate,
    ExternalBrokerResponse,
)
from backend.services.training_service import training_service
from backend.utils.yolo_export import YOLOExporter
from backend.utils.dataset_import import DatasetImporter, generate_color
from backend.config import settings
from PIL import Image as PILImage
import io
import logging
import tempfile


router = APIRouter()
logger = logging.getLogger(__name__)


def _slugify(text: str, max_len: int = 40) -> str:
    """Create filesystem-friendly slug."""
    if not text:
        return "unnamed"
    text = text.strip()
    # Replace whitespace with underscores
    text = re.sub(r"\s+", "_", text)
    # Remove invalid chars
    text = re.sub(r"[^0-9a-zA-Z_\-]+", "", text)
    if not text:
        text = "unnamed"
    return text[:max_len]


def _get_project_class_names(project_id: str) -> list[str]:
    """
    Load class names from project's data.yaml if available.
    Used for building human-friendly model filenames.
    """
    data_yaml = settings.DATASETS_ROOT / project_id / "yolo_export" / "data.yaml"
    names: list[str] = []
    if data_yaml.exists():
        try:
            with open(data_yaml, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            raw_names = data.get("names")
            if isinstance(raw_names, dict):
                # {0: 'person', 1: 'car', ...}
                names = [v for _, v in sorted(raw_names.items(), key=lambda kv: int(kv[0]))]
            elif isinstance(raw_names, list):
                names = [str(n) for n in raw_names]
        except Exception:
            names = []
    return names


def _build_model_basename(
    project: Project,
    training_record: Optional[TrainingRecord],
    project_id: str,
    training_id: Optional[str],
    class_names: list[str],
) -> str:
    """
    Build a common base name for model files:
    <project>__<classes>__<training_id>__<timestamp>
    """
    project_slug = _slugify(project.name or project_id)

    if class_names:
        primary = class_names[:3]
        classes_part = "-".join(_slugify(c, max_len=16) for c in primary)
    else:
        classes_part = "no-class"

    # Prefer end_time, then start_time, then now
    dt: Optional[datetime] = None
    if training_record:
        if training_record.end_time:
            dt = training_record.end_time
        elif training_record.start_time:
            dt = training_record.start_time
    if dt is None:
        dt = datetime.utcnow()
    ts = dt.strftime("%Y%m%d-%H%M%S")

    tid = training_id or "no-id"

    return f"{project_slug}__{classes_part}__{tid}__{ts}"


# ========== Pydantic Models ==========

class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_orm(cls, obj: Project):
        """Create response model from ORM object"""
        return cls(
            id=obj.id,
            name=obj.name,
            description=obj.description or "",
            created_at=obj.created_at.isoformat() if obj.created_at else None,
            updated_at=obj.updated_at.isoformat() if obj.updated_at else None
        )


class DeviceOut(BaseModel):
    """Device response model"""

    id: str
    name: Optional[str] = None
    type: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    mac_address: Optional[str] = None
    project_ids: Optional[List[str]] = None  # List of bound project IDs
    status: Optional[str] = None
    last_seen: Optional[datetime] = None
    last_ip: Optional[str] = None
    firmware_version: Optional[str] = None
    hardware_version: Optional[str] = None
    power_supply_type: Optional[str] = None
    last_report: Optional[str] = None  # Raw JSON payload of last report
    extra_info: Optional[str] = None  # JSON string for arbitrary metadata

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_device(cls, device):
        """Convert Device ORM object to DeviceOut, including project_ids"""
        return cls(
            id=device.id,
            name=device.name,
            type=device.type,
            model=device.model,
            serial_number=device.serial_number,
            mac_address=device.mac_address,
            project_ids=[p.id for p in device.projects] if device.projects else [],
            status=device.status,
            last_seen=device.last_seen,
            last_ip=device.last_ip,
            firmware_version=device.firmware_version,
            hardware_version=device.hardware_version,
            power_supply_type=device.power_supply_type,
            last_report=device.last_report,
            extra_info=device.extra_info,
        )


class DeviceBindProjectRequest(BaseModel):
    """Request model for binding a device to a project."""

    project_id: str  # Required: project ID to bind


class DeviceUnbindProjectRequest(BaseModel):
    """Request model for unbinding a device from a project."""

    project_id: str  # Required: project ID to unbind


class DeviceCreate(BaseModel):
    """Request model for manually creating/registering a device.

    This is mainly used to:
    - Let the system generate a device_id automatically.
    - Generate the corresponding MQTT topic for the user to configure on the device.
    """

    name: Optional[str] = None
    type: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    mac_address: Optional[str] = None
    # Optional: bind to one or more projects when creating
    project_ids: Optional[List[str]] = None
    extra_info: Optional[str] = None


class DeviceWithTopic(DeviceOut):
    """Device response model with MQTT topic information for configuration."""

    uplink_topic: str  # MQTT uplink topic pattern for this device (device/{device_id}/uplink)


class ModelInfo(BaseModel):
    """Global model info for model space"""
    # Basic identity
    model_id: Optional[int] = None  # For ModelRegistry
    training_id: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    name: Optional[str] = None  # Model name (user-defined for imported models)

    # Source & type
    source: str  # training / import / other
    model_type: Optional[str] = None
    format: Optional[str] = None

    # Training-related (for training-produced models)
    status: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    model_size: Optional[str] = None
    epochs: Optional[int] = None
    imgsz: Optional[int] = None
    batch: Optional[int] = None
    device: Optional[str] = None
    metrics: Optional[dict] = None
    error: Optional[str] = None
    model_path: Optional[str] = None
    log_count: int = 0

    # Class / label information
    num_classes: Optional[int] = None
    class_names: Optional[List[str]] = None


class TrainingRequest(BaseModel):
    model_type: str = 'yolov8'  # 'yolov8', 'yolov11', 'yolov12', etc.
    model_size: str = 'n'  # 'n', 's', 'm', 'l', 'x'
    epochs: int = 100
    imgsz: int = 640
    batch: int = 16
    device: Optional[str] = None
    # Learning rate related
    lr0: Optional[float] = None  # Initial learning rate
    lrf: Optional[float] = None  # Final learning rate
    # Optimizer related
    optimizer: Optional[str] = None  # 'SGD', 'Adam', 'AdamW', 'RMSProp', 'auto'
    momentum: Optional[float] = None  # Momentum
    weight_decay: Optional[float] = None  # Weight decay
    # Training control
    patience: Optional[int] = None  # Early stopping patience
    workers: Optional[int] = None  # Data loading thread count
    val: Optional[bool] = None  # Whether to perform validation
    save_period: Optional[int] = None  # Save period (-1 means no intermediate model saving)
    amp: Optional[bool] = None  # Whether to use mixed precision training
    # Data augmentation (advanced options)
    hsv_h: Optional[float] = None  # HSV hue augmentation
    hsv_s: Optional[float] = None  # HSV saturation augmentation
    hsv_v: Optional[float] = None  # HSV value augmentation
    degrees: Optional[float] = None  # Rotation angle
    translate: Optional[float] = None  # Translation
    scale: Optional[float] = None  # Scaling
    shear: Optional[float] = None  # Shearing
    perspective: Optional[float] = None  # Perspective transformation
    flipud: Optional[float] = None  # Vertical flip probability
    fliplr: Optional[float] = None  # Horizontal flip probability
    mosaic: Optional[float] = None  # Mosaic augmentation probability
    mixup: Optional[float] = None  # Mixup augmentation probability


class ClassCreate(BaseModel):
    name: str
    color: str  # HEX color
    shortcut_key: str = None


class AnnotationCreate(BaseModel):
    type: str  # bbox, polygon, keypoint
    data: dict  # Annotation data
    class_id: int


class AnnotationUpdate(BaseModel):
    data: dict = None
    class_id: int = None


class MQTTConfigUpdate(BaseModel):
    """Partial MQTT configuration update model for system settings API."""

    enabled: Optional[bool] = None
    external_enabled: Optional[bool] = None

    # Built-in broker configuration
    builtin_protocol: Optional[str] = None  # mqtt / mqtts
    builtin_broker_host: Optional[str] = None  # Manual override for broker host IP (if None, auto-detect)
    builtin_tcp_port: Optional[int] = None
    builtin_tls_port: Optional[int] = None
    builtin_allow_anonymous: Optional[bool] = None
    builtin_username: Optional[str] = None  # Username for authentication when anonymous is disabled
    builtin_password: Optional[str] = None  # Password for authentication when anonymous is disabled
    builtin_max_connections: Optional[int] = None
    builtin_keepalive_timeout: Optional[int] = None
    builtin_qos: Optional[int] = None
    builtin_keepalive: Optional[int] = None
    builtin_tls_enabled: Optional[bool] = None
    builtin_tls_ca_cert_path: Optional[str] = None
    builtin_tls_client_cert_path: Optional[str] = None
    builtin_tls_client_key_path: Optional[str] = None
    builtin_tls_insecure_skip_verify: Optional[bool] = None
    builtin_tls_require_client_cert: Optional[bool] = None  # Whether to require client certificates (mTLS)

    # External broker configuration
    external_protocol: Optional[str] = None  # mqtt / mqtts
    external_host: Optional[str] = None
    external_port: Optional[int] = None
    external_username: Optional[str] = None
    external_password: Optional[str] = None
    external_qos: Optional[int] = None
    external_keepalive: Optional[int] = None
    external_tls_enabled: Optional[bool] = None
    external_tls_ca_cert_path: Optional[str] = None
    external_tls_client_cert_path: Optional[str] = None
    external_tls_client_key_path: Optional[str] = None
    external_tls_insecure_skip_verify: Optional[bool] = None


# ========== Project Related ==========

@router.post("/projects", response_model=ProjectResponse)
def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    """Create new project"""
    # Generate short project id (8 hex chars) and ensure uniqueness
    while True:
        project_id = uuid.uuid4().hex[:8]
        if not db.query(Project).filter(Project.id == project_id).first():
            break
    
    db_project = Project(
        id=project_id,
        name=project.name.strip(),
        description=project.description.strip() if project.description else ""
    )
    
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    
    # Create project directory
    (settings.DATASETS_ROOT / project_id / "raw").mkdir(parents=True, exist_ok=True)
    
    return ProjectResponse.from_orm(db_project)


@router.get("/projects", response_model=List[ProjectResponse])
def list_projects(db: Session = Depends(get_db)):
    """List all projects"""
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return [ProjectResponse.from_orm(p) for p in projects]


@router.get("/models", response_model=List[ModelInfo])
def list_models(db: Session = Depends(get_db)):
    """
    List all models across projects (for model space).
    Includes:
    - training-produced models (from TrainingRecord)
    - externally imported models (from ModelRegistry)
    """

    results: List[ModelInfo] = []

    def infer_model_type(
        model_size: Optional[str] = None,
        path: Optional[str] = None,
        training_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Derive a friendly model type name.
        
        Target display (frontend will append format):
        - yolov8n
        - yolov8s
        - yolov11m
        - ne301
        
        Strategy:
        1. Try to read from training directory's args.yaml (Ultralytics saves model info there)
        2. Parse from model path if it contains architecture info
        3. Default to yolov8 if we have model_size but no architecture
        """
        arch: Optional[str] = None

        # First, try to read from training directory's args.yaml (most reliable)
        if training_id and project_id and path:
            try:
                model_path_obj = Path(path)
                # args.yaml is typically in the same directory as weights/ or in the training root
                possible_args_paths = [
                    model_path_obj.parent.parent / "args.yaml",  # weights/best.pt -> train_xxx/args.yaml
                    model_path_obj.parent / "args.yaml",  # direct path
                    settings.DATASETS_ROOT / project_id / f"train_{training_id}" / "args.yaml",
                    settings.DATASETS_ROOT / project_id / f"train_{project_id}" / "args.yaml",
                ]
                
                for args_path in possible_args_paths:
                    if args_path.exists():
                        try:
                            with open(args_path, "r", encoding="utf-8") as f:
                                args_data = yaml.safe_load(f) or {}
                            # Ultralytics args.yaml contains 'model' field like 'yolov8n.pt' or 'yolov8n'
                            model_str = args_data.get("model", "")
                            if model_str:
                                # Extract architecture (yolov8, yolov11, etc.) and size (n, s, m, l, x)
                                model_str_lower = model_str.lower().replace(".pt", "").replace(".yaml", "")
                                # Match patterns like yolov8n, yolov11s, etc.
                                import re
                                match = re.match(r"(yolov\d+)([nsmlx])", model_str_lower)
                                if match:
                                    arch = match.group(1)  # yolov8, yolov11, etc.
                                    detected_size = match.group(2)  # n, s, m, l, x
                                    # Use detected size if model_size not provided
                                    if not model_size:
                                        model_size = detected_size
                                    break
                                # Fallback: try to extract just architecture
                                if "yolov11" in model_str_lower:
                                    arch = "yolov11"
                                    break
                                elif "yolov8" in model_str_lower:
                                    arch = "yolov8"
                                    break
                        except Exception:
                            continue
            except Exception:
                pass

        # Fallback: try to infer from path
        if not arch and path:
            name = Path(path).name.lower()
            if "yolov11" in name:
                arch = "yolov11"
            elif "yolov8" in name:
                arch = "yolov8"
            elif "yolo" in name:
                arch = "yolo"
            # Don't use generic file names like "best" as architecture

        # Default to yolov8 if we have model_size but no architecture (most common case)
        if not arch and model_size:
            arch = "yolov8"

        # If we know YOLO family and also have size (n/s/m/l/x), merge them (yolov8n, yolov11s, etc.)
        if arch in ("yolov8", "yolov11", "yolo") and model_size:
            return f"{arch}{model_size}"

        # If we don't know architecture but have size, keep size as a very last fallback
        if model_size and not arch:
            return model_size

        # Otherwise use detected architecture or a generic default
        return arch or "yolo"

    def infer_format(path: Optional[str], default: Optional[str] = None) -> Optional[str]:
        """Infer file format from path suffix."""
        if path:
            suffix = Path(path).suffix.lower().lstrip(".")
            if suffix:
                return suffix
        return default

    # ---------- Training-produced models ----------
    training_records = (
        db.query(TrainingRecord, Project)
        .join(Project, TrainingRecord.project_id == Project.id)
        .order_by(TrainingRecord.start_time.desc())
        .all()
    )

    # Cache per-project class names from dataset data.yaml if available
    project_classes_cache: dict[str, List[str]] = {}

    def get_project_classes(project_id: str) -> List[str]:
        if project_id in project_classes_cache:
            return project_classes_cache[project_id]
        names: List[str] = []
        data_yaml = settings.DATASETS_ROOT / project_id / "yolo_export" / "data.yaml"
        if data_yaml.exists():
            try:
                with open(data_yaml, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                # Ultralytics style: names can be dict or list
                raw_names = data.get("names")
                if isinstance(raw_names, dict):
                    # {0: 'person', 1: 'car', ...}
                    names = [v for _, v in sorted(raw_names.items(), key=lambda kv: int(kv[0]))]
                elif isinstance(raw_names, list):
                    names = [str(n) for n in raw_names]
            except Exception:
                names = []
        project_classes_cache[project_id] = names
        return names

    for rec, proj in training_records:
        # Parse metrics JSON safely
        metrics: Optional[dict] = None
        if rec.metrics:
            try:
                metrics = json.loads(rec.metrics)
            except json.JSONDecodeError:
                metrics = None

        class_names = get_project_classes(rec.project_id)

        results.append(
            ModelInfo(
                model_id=None,
                training_id=rec.training_id,
                project_id=rec.project_id,
                project_name=proj.name,
                source="training",
                model_type=infer_model_type(
                    rec.model_size,
                    rec.model_path,
                    rec.training_id,
                    rec.project_id,
                ),
                format=infer_format(rec.model_path, "pt"),
                status=rec.status or "unknown",
                start_time=rec.start_time.isoformat() if rec.start_time else None,
                end_time=rec.end_time.isoformat() if rec.end_time else None,
                model_size=rec.model_size,
                epochs=rec.epochs,
                imgsz=rec.imgsz,
                batch=rec.batch,
                device=rec.device,
                metrics=metrics,
                error=rec.error,
                model_path=rec.model_path,
                log_count=rec.log_count or 0,
                num_classes=len(class_names) or None,
                class_names=class_names or None,
            )
        )

    # ---------- Externally imported models ----------
    registry_rows = (
        db.query(ModelRegistry, Project)
        .outerjoin(Project, ModelRegistry.project_id == Project.id)
        .order_by(ModelRegistry.created_at.desc())
        .all()
    )

    for reg, proj in registry_rows:
        # Skip NE301 TFLite models (only show NE301 bin packages)
        if (reg.format or "").lower() == "tflite" and (reg.model_type or "").lower() == "ne301":
            continue

        # Parse class names from registry (if stored)
        class_names: Optional[List[str]] = None
        if reg.class_names:
            try:
                raw = json.loads(reg.class_names)
                if isinstance(raw, list):
                    class_names = [str(x) for x in raw]
            except json.JSONDecodeError:
                class_names = None

        # Try to reuse training metrics for quantized / registry models
        metrics: Optional[dict] = None
        if reg.training_id and reg.project_id:
            tr = (
                db.query(TrainingRecord)
                .filter(
                    TrainingRecord.project_id == reg.project_id,
                    TrainingRecord.training_id == reg.training_id,
                )
                .first()
            )
            if tr and tr.metrics:
                try:
                    metrics = json.loads(tr.metrics)
                except json.JSONDecodeError:
                    metrics = None

        # Determine model_type for registry models
        registry_model_type: Optional[str] = None
        # For externally imported models, always use the stored model_type (user-defined)
        if reg.source == "import" and reg.model_type:
            registry_model_type = reg.model_type
        elif reg.model_type and (reg.model_type or "").lower() != "yolo":
            # Use explicit registry model_type if it's not generic "yolo"
            registry_model_type = reg.model_type
        elif 'tr' in locals() and tr:
            # Try to infer from linked training record
            registry_model_type = infer_model_type(
                tr.model_size,
                tr.model_path,
                tr.training_id,
                tr.project_id,
            )
        else:
            # Fallback: infer from registry model_path
            registry_model_type = infer_model_type(
                None,
                reg.model_path,
                reg.training_id,
                reg.project_id,
            )

        results.append(
            ModelInfo(
                model_id=reg.id,
                training_id=reg.training_id,
                project_id=reg.project_id,
                project_name=proj.name if proj else None,
                name=reg.name,  # Include user-defined model name
                source=reg.source or "import",
                model_type=registry_model_type,
                format=reg.format or infer_format(reg.model_path),
                # Registry models默认视为“已完成/可用”
                status="completed",
                start_time=reg.created_at.isoformat() if reg.created_at else None,
                # Use created_at as end_time for registry models (model creation/storage time)
                end_time=reg.created_at.isoformat() if reg.created_at else None,
                model_size=None,
                epochs=None,
                imgsz=reg.input_width,  # simplified: use width as imgsz
                batch=None,
                device=None,
                metrics=metrics,
                error=None,
                model_path=reg.model_path,
                log_count=0,
                num_classes=reg.num_classes,
                class_names=class_names,
            )
        )

    return results


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, db: Session = Depends(get_db)):
    """Get project details"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse.from_orm(project)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    """Delete project"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    db.delete(project)
    db.commit()
    
    # Delete project directory
    project_dir = settings.DATASETS_ROOT / project_id
    if project_dir.exists():
        import shutil
        shutil.rmtree(project_dir)
    
    return {"message": "Project deleted"}


# ========== Class Related ==========

@router.post("/projects/{project_id}/classes")
def create_class(project_id: str, class_data: ClassCreate, db: Session = Depends(get_db)):
    """Create class"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    db_class = Class(
        project_id=project_id,
        name=class_data.name,
        color=class_data.color,
        shortcut_key=class_data.shortcut_key
    )
    
    db.add(db_class)
    db.commit()
    db.refresh(db_class)
    
    return db_class


@router.get("/projects/{project_id}/classes")
def list_classes(project_id: str, db: Session = Depends(get_db)):
    """List all classes in project"""
    classes = db.query(Class).filter(Class.project_id == project_id).all()
    return classes


@router.delete("/projects/{project_id}/classes/{class_id}")
def delete_class(project_id: str, class_id: int, db: Session = Depends(get_db)):
    """Delete class"""
    db_class = db.query(Class).filter(
        Class.id == class_id,
        Class.project_id == project_id
    ).first()
    
    if not db_class:
        raise HTTPException(status_code=404, detail="Class not found")
    
    # Check if any annotations use this class
    annotation_count = db.query(Annotation).filter(Annotation.class_id == class_id).count()
    if annotation_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete class: {annotation_count} annotation(s) are using this class"
        )
    
    db.delete(db_class)
    db.commit()
    
    return {"message": "Class deleted"}


# ========== Image Related ==========

@router.post("/projects/{project_id}/images/upload")
async def upload_image(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Upload image file to project"""
    # Verify project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Verify file type
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: {file_ext}. Supported formats: {', '.join(allowed_extensions)}"
        )
    
    try:
        # Read file content
        file_content = await file.read()
        
        # Verify file size
        size_mb = len(file_content) / (1024 * 1024)
        if size_mb > settings.MAX_IMAGE_SIZE_MB:
            raise HTTPException(
                status_code=400,
                detail=f"File too large: {size_mb:.2f}MB (max: {settings.MAX_IMAGE_SIZE_MB}MB)"
            )
        
        # Verify if valid image and get dimensions
        try:
            img = PILImage.open(io.BytesIO(file_content))
            img_width, img_height = img.size
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid image file: {str(e)}")
        
        # Generate storage path
        project_dir = settings.DATASETS_ROOT / project_id / "raw"
        project_dir.mkdir(parents=True, exist_ok=True)
        
        # Handle filename conflicts and Chinese filenames
        original_filename = file.filename or f"image_{uuid.uuid4().hex[:8]}{file_ext}"
        # Handle Chinese filenames: use UUID to avoid encoding issues, but keep original extension
        filename_stem = f"img_{uuid.uuid4().hex[:8]}"
        filename = f"{filename_stem}{file_ext}"
        file_path = project_dir / filename
        
        # If filename conflicts, add timestamp
        counter = 0
        while file_path.exists():
            counter += 1
            filename = f"{filename_stem}_{counter}{file_ext}"
            file_path = project_dir / filename
        
        # Save file (convert image format if needed when saving)
        if img.mode != 'RGB' and file_ext in ['.jpg', '.jpeg']:
            # JPG format requires RGB mode
            img_rgb = img.convert('RGB')
            img_rgb.save(file_path, 'JPEG', quality=95)
        else:
            # Other formats save original content directly
            file_path.write_bytes(file_content)
        
        # Generate relative path (only includes raw/filename, not project_id)
        relative_path = f"raw/{filename}"
        
        # Save to database (store original filename and relative path)
        db_image = Image(
            project_id=project_id,
            filename=original_filename,  # Store original filename
            path=relative_path,  # Store relative path raw/filename
            width=img_width,
            height=img_height,
            status="UNLABELED",
            source="UPLOAD"
        )
        db.add(db_image)
        db.commit()
        db.refresh(db_image)
        
        # Notify frontend via WebSocket
        websocket_manager.broadcast_project_update(project_id, {
            "type": "new_image",
            "image_id": db_image.id,
            "filename": filename,
            "path": relative_path,
            "width": img_width,
            "height": img_height
        })
        
        return {
            "id": db_image.id,
            "filename": filename,
            "path": relative_path,
            "width": img_width,
            "height": img_height,
            "status": db_image.status,
            "message": "Image uploaded successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Upload] Error uploading image: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.get("/projects/{project_id}/images")
def list_images(project_id: str, db: Session = Depends(get_db)):
    """List all images in project"""
    images = db.query(Image).filter(Image.project_id == project_id).order_by(Image.created_at.desc()).all()
    
    result = []
    for img in images:
        result.append({
            "id": img.id,
            "filename": img.filename,
            "path": img.path,
            "width": img.width,
            "height": img.height,
            "status": img.status,
            "created_at": img.created_at.isoformat() if img.created_at else None
        })
    
    return result


@router.get("/projects/{project_id}/images/{image_id}")
def get_image(project_id: str, image_id: int, db: Session = Depends(get_db)):
    """Get image details (including annotations)"""
    image = db.query(Image).filter(
        Image.id == image_id,
        Image.project_id == project_id
    ).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    # Get annotations
    annotations = db.query(Annotation).filter(Annotation.image_id == image_id).all()
    
    ann_list = []
    for ann in annotations:
        class_obj = db.query(Class).filter(Class.id == ann.class_id).first()
        ann_list.append({
            "id": ann.id,
            "type": ann.type,
            "data": json.loads(ann.data) if isinstance(ann.data, str) else ann.data,
            "class_id": ann.class_id,
            "class_name": class_obj.name if class_obj else None,
            "class_color": class_obj.color if class_obj else None
        })
    
    return {
        "id": image.id,
        "filename": image.filename,
        "path": image.path,
        "width": image.width,
        "height": image.height,
        "status": image.status,
        "annotations": ann_list
    }


@router.delete("/projects/{project_id}/images/{image_id}")
def delete_image(project_id: str, image_id: int, db: Session = Depends(get_db)):
    """Delete image"""
    image = db.query(Image).filter(
        Image.id == image_id,
        Image.project_id == project_id
    ).first()
    
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    # Delete associated annotation data first to avoid orphaned records
    annotations = db.query(Annotation).filter(Annotation.image_id == image_id).all()
    for ann in annotations:
        db.delete(ann)
    
    # Delete image file
    image_path = settings.DATASETS_ROOT / project_id / image.path
    if image_path.exists():
        try:
            image_path.unlink()
            print(f"[Delete] Deleted image file: {image_path}")
        except Exception as e:
            print(f"[Delete] Error deleting file {image_path}: {e}")
            # Continue deleting database record even if file deletion fails
    
    # Delete database record (cascade delete annotations)
    db.delete(image)
    db.commit()
    
    # Notify frontend via WebSocket
    websocket_manager.broadcast_project_update(project_id, {
        "type": "image_deleted",
        "image_id": image_id
    })
    
    return {"message": "Image deleted"}


# ========== Annotation Related ==========

@router.post("/images/{image_id}/annotations")
def create_annotation(image_id: int, annotation: AnnotationCreate, db: Session = Depends(get_db)):
    """Create annotation"""
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    project_id = image.project_id
    
    db_annotation = Annotation(
        image_id=image_id,
        class_id=annotation.class_id,
        type=annotation.type,
        data=json.dumps(annotation.data)
    )
    
    db.add(db_annotation)
    
    # Update image status
    was_unlabeled = image.status == "UNLABELED"
    image.status = "LABELED"
    
    db.commit()
    db.refresh(db_annotation)
    
    # If status changed from UNLABELED to LABELED, notify frontend to update image list
    if was_unlabeled:
        websocket_manager.broadcast_project_update(project_id, {
            "type": "image_status_updated",
            "image_id": image_id,
            "status": "LABELED"
        })
    
    return db_annotation


@router.put("/annotations/{annotation_id}")
def update_annotation(annotation_id: int, annotation: AnnotationUpdate, db: Session = Depends(get_db)):
    """Update annotation"""
    db_ann = db.query(Annotation).filter(Annotation.id == annotation_id).first()
    if not db_ann:
        raise HTTPException(status_code=404, detail="Annotation not found")
    
    image = db.query(Image).filter(Image.id == db_ann.image_id).first()
    project_id = image.project_id if image else None
    
    if annotation.data is not None:
        db_ann.data = json.dumps(annotation.data)
    
    if annotation.class_id is not None:
        db_ann.class_id = annotation.class_id
    
    # Ensure image status is LABELED (if it wasn't before)
    if image and image.status != "LABELED":
        image.status = "LABELED"
        if project_id:
            websocket_manager.broadcast_project_update(project_id, {
                "type": "image_status_updated",
                "image_id": db_ann.image_id,
                "status": "LABELED"
            })
    
    db.commit()
    db.refresh(db_ann)
    
    return db_ann


@router.delete("/annotations/{annotation_id}")
def delete_annotation(annotation_id: int, db: Session = Depends(get_db)):
    """Delete annotation"""
    db_ann = db.query(Annotation).filter(Annotation.id == annotation_id).first()
    if not db_ann:
        raise HTTPException(status_code=404, detail="Annotation not found")
    
    image_id = db_ann.image_id
    image = db.query(Image).filter(Image.id == image_id).first()
    project_id = image.project_id if image else None
    
    db.delete(db_ann)
    
    # Check if there are remaining annotations, update status if none
    remaining = db.query(Annotation).filter(Annotation.image_id == image_id).count()
    status_changed = False
    if remaining == 0:
        if image and image.status == "LABELED":
            image.status = "UNLABELED"
            status_changed = True
    
    db.commit()
    
    # Notify frontend via WebSocket
    if project_id:
        from backend.services.websocket_manager import websocket_manager
        websocket_manager.broadcast_project_update(project_id, {
            "type": "annotation_deleted",
            "annotation_id": annotation_id,
            "image_id": image_id
        })
        
        # If status changed, also notify image status update
        if status_changed:
            websocket_manager.broadcast_project_update(project_id, {
                "type": "image_status_updated",
                "image_id": image_id,
                "status": "UNLABELED"
        })
    
    return {"message": "Annotation deleted"}


# ========== Dataset Import Related ==========

@router.post("/projects/{project_id}/dataset/import")
async def import_dataset(
    project_id: str,
    file: UploadFile = File(...),
    format_type: str = Query(..., description="Dataset format: 'coco', 'yolo' or 'project_zip'"),
    db: Session = Depends(get_db)
):
    """Import dataset in COCO / YOLO format or project exported ZIP"""
    # Verify project exists
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if format_type not in ['coco', 'yolo', 'project_zip']:
        raise HTTPException(status_code=400, detail=f"Unsupported format type: {format_type}. Supported: 'coco', 'yolo', 'project_zip'")
    
    temp_file = None
    temp_dir = None
    try:
        # Save uploaded file to temp location
        file_ext = Path(file.filename).suffix.lower() if file.filename else ''
        temp_file = Path(tempfile.mkdtemp()) / f"dataset{file_ext}"
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        
        file_content = await file.read()
        temp_file.write_bytes(file_content)
        
        # Parse dataset
        if format_type == 'coco':
            dataset_data = DatasetImporter.import_dataset(project_id, temp_file, 'coco')
        elif format_type == 'yolo':
            dataset_data = DatasetImporter.import_dataset(project_id, temp_file, 'yolo')
        elif format_type == 'project_zip':
            temp_dir = Path(tempfile.mkdtemp())
            try:
                with zipfile.ZipFile(temp_file, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to unzip project package: {str(e)}")

            classes_file = temp_dir / "classes.json"
            classes_data = []
            class_id_map = {}
            if classes_file.exists():
                try:
                    classes_json = json.loads(classes_file.read_text())
                    classes_data = classes_json.get("classes", [])
                    for cls in classes_data:
                        if "id" in cls:
                            class_id_map[cls["id"]] = cls
                except Exception as e:
                    logger.warning(f"[Dataset Import] Failed to parse classes.json: {e}")

            images_dir = temp_dir / "images"
            if not images_dir.exists():
                images_dir = temp_dir

            annotations_dir = temp_dir / "annotations"

            categories = []
            category_name_set = set()
            images_data = []

            def ensure_category(cat_name: str, cat_color: str = None):
                if cat_name and cat_name not in category_name_set:
                    category_name_set.add(cat_name)
                    categories.append({
                        "id": len(categories) + 1,
                        "name": cat_name,
                        "color": cat_color
                    })

            # Preload categories from classes.json
            for cls in classes_data:
                name = cls.get("name")
                if name:
                    ensure_category(name, cls.get("color"))

            allowed_exts = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
            for img_path in images_dir.rglob("*"):
                if not img_path.is_file() or img_path.suffix.lower() not in allowed_exts:
                    continue

                try:
                    with PILImage.open(img_path) as im:
                        img_width, img_height = im.size
                except Exception as e:
                    logger.warning(f"[Dataset Import] Failed to read image size for {img_path}: {e}")
                    continue

                ann_list = []
                ann_path = annotations_dir / f"{img_path.stem}.json"
                if ann_path.exists():
                    try:
                        ann_json = json.loads(ann_path.read_text())
                        if isinstance(ann_json, list):
                            for ann in ann_json:
                                cat_name = ann.get("class_name") or ann.get("category_name")
                                if not cat_name:
                                    cat_id = ann.get("class_id") or ann.get("category_id")
                                    if cat_id in class_id_map:
                                        cat_name = class_id_map[cat_id].get("name")
                                if not cat_name:
                                    continue
                                ensure_category(cat_name)
                                ann_list.append({
                                    "category_name": cat_name,
                                    "type": ann.get("type", "bbox"),
                                    "data": ann.get("data", {})
                                })
                    except Exception as e:
                        logger.warning(f"[Dataset Import] Failed to parse annotations for {img_path.name}: {e}")

                images_data.append({
                    "file_name": img_path.name,
                    "width": img_width,
                    "height": img_height,
                    "annotations": ann_list
                })

            dataset_data = {
                "categories": categories,
                "images": images_data,
                "images_dir": str(images_dir)
            }
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported format: {format_type}")
        
        # Get or create classes
        existing_classes = {cls.name: cls for cls in db.query(Class).filter(Class.project_id == project_id).all()}
        class_name_to_id = {name: cls.id for name, cls in existing_classes.items()}
        
        categories = dataset_data.get("categories", [])
        classes_created = 0
        
        for cat in categories:
            cat_name = cat["name"]
            if cat_name not in class_name_to_id:
                # Create new class
                color = cat.get("color") or generate_color(len(class_name_to_id))
                db_class = Class(
                    project_id=project_id,
                    name=cat_name,
                    color=color
                )
                db.add(db_class)
                db.flush()  # Get the ID
                class_name_to_id[cat_name] = db_class.id
                existing_classes[cat_name] = db_class
                classes_created += 1
        
        db.commit()
        
        # Import images and annotations
        images_dir = Path(dataset_data.get("images_dir", ""))
        images_data = dataset_data.get("images", [])
        project_dir = settings.DATASETS_ROOT / project_id / "raw"
        project_dir.mkdir(parents=True, exist_ok=True)
        
        images_imported = 0
        annotations_imported = 0
        errors = []
        
        for img_data in images_data:
            try:
                img_filename = img_data["file_name"]
                img_width = img_data["width"]
                img_height = img_data["height"]
                
                # Find image file
                if images_dir and Path(images_dir).exists():
                    img_path = Path(images_dir) / img_filename
                else:
                    # Try to find in temp directory
                    img_path = temp_file.parent / img_filename
                    if not img_path.exists():
                        # Try various subdirectories
                        for subdir in ["images", "train/images", "val/images", "test/images"]:
                            potential_path = temp_file.parent / subdir / img_filename
                            if potential_path.exists():
                                img_path = potential_path
                                break
                
                if not img_path.exists():
                    errors.append(f"Image file not found: {img_filename}")
                    continue
                
                # Copy image to project directory
                file_ext = img_path.suffix
                filename_stem = f"img_{uuid.uuid4().hex[:8]}"
                filename = f"{filename_stem}{file_ext}"
                dest_path = project_dir / filename
                
                # Handle filename conflicts
                counter = 0
                while dest_path.exists():
                    counter += 1
                    filename = f"{filename_stem}_{counter}{file_ext}"
                    dest_path = project_dir / filename
                
                # Copy file
                shutil.copy2(img_path, dest_path)
                
                relative_path = f"raw/{filename}"
                
                # Create image record
                db_image = Image(
                    project_id=project_id,
                    filename=img_filename,  # Original filename
                    path=relative_path,
                    width=img_width,
                    height=img_height,
                    status="LABELED" if img_data.get("annotations") else "UNLABELED",
                    source="DATASET_IMPORT"
                )
                db.add(db_image)
                db.flush()  # Get the ID
                
                images_imported += 1
                
                # Create annotations
                annotations_data = img_data.get("annotations", [])
                for ann_data in annotations_data:
                    cat_name = ann_data.get("category_name")
                    if cat_name not in class_name_to_id:
                        errors.append(f"Category not found: {cat_name} for image {img_filename}")
                        continue
                    
                    class_id = class_name_to_id[cat_name]
                    annotation_type = ann_data.get("type", "bbox")
                    annotation_data = ann_data.get("data", {})
                    
                    db_annotation = Annotation(
                        image_id=db_image.id,
                        class_id=class_id,
                        type=annotation_type,
                        data=json.dumps(annotation_data)
                    )
                    db.add(db_annotation)
                    annotations_imported += 1
                
            except Exception as e:
                errors.append(f"Failed to import image {img_data.get('file_name', 'unknown')}: {str(e)}")
                logger.error(f"[Dataset Import] Failed to import image: {e}", exc_info=True)
        
        db.commit()
        
        # Notify frontend via WebSocket
        websocket_manager.broadcast_project_update(project_id, {
            "type": "dataset_imported",
            "images_count": images_imported,
            "annotations_count": annotations_imported,
            "classes_created": classes_created
        })
        
        result = {
            "message": "Dataset imported successfully",
            "images_imported": images_imported,
            "annotations_imported": annotations_imported,
            "classes_created": classes_created,
            "errors": errors[:10] if errors else []  # Limit errors to first 10
        }
        
        if errors:
            result["error_count"] = len(errors)
        
        return result
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[Dataset Import] Import failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")
    finally:
        # Cleanup temp files
        if temp_file and temp_file.exists():
            try:
                if temp_file.is_file():
                    temp_file.unlink()
                elif temp_file.is_dir():
                    shutil.rmtree(temp_file, ignore_errors=True)
            except Exception:
                pass
        if temp_dir and Path(temp_dir).exists():
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


# ========== WebSocket ==========
# Note: WebSocket routes are not registered in router, need to register separately in main.py
# This way the path won't have /api prefix


# ========== Image File Service ==========

@router.get("/images/{project_id}/{image_path:path}")
def get_image_file(project_id: str, image_path: str):
    """Retrieve image file"""
    import os
    from pathlib import Path
    
    print(f"[Image] Request received: project_id={project_id}, image_path={image_path}")
    
    # image_path should be in raw/filename format
    # Remove possible project_id prefix (for compatibility with old data)
    if image_path.startswith(f"{project_id}/"):
        image_path = image_path[len(project_id) + 1:]
    
    # Ensure path starts with raw/
    if not image_path.startswith("raw/"):
        # If path doesn't contain raw/, might be old format, try to add
        image_path = f"raw/{image_path}"
    
    # Build file path
    file_path = settings.DATASETS_ROOT / project_id / image_path
    
    # Normalize path, handle potential path traversal attacks
    try:
        resolved_path = file_path.resolve()
        datasets_root = settings.DATASETS_ROOT.resolve()
        # Ensure resolved path is within datasets root directory
        resolved_path.relative_to(datasets_root)
    except ValueError:
        print(f"[Image] Security check failed: {resolved_path} not under {datasets_root}")
        raise HTTPException(status_code=403, detail="Access denied: Invalid path")
    
    print(f"[Image] Resolved path: {resolved_path}")
    print(f"[Image] Path exists: {resolved_path.exists()}")
    print(f"[Image] DATASETS_ROOT: {datasets_root}")
    
    if not resolved_path.exists():
        # Try listing directory contents for debugging
        project_dir = settings.DATASETS_ROOT / project_id / "raw"
        if project_dir.exists():
            files = list(project_dir.glob("*"))
            print(f"[Image] Files in raw dir: {[f.name for f in files]}")
        else:
            print(f"[Image] Raw directory does not exist: {project_dir}")
        raise HTTPException(status_code=404, detail=f"Image not found: {image_path} (resolved: {resolved_path})")
    
    # Ensure it's a file, not a directory
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="Path is not a file")
    
    return FileResponse(str(resolved_path))


# ========== YOLO Export ==========

@router.post("/projects/{project_id}/export/yolo")
def export_yolo(project_id: str, db: Session = Depends(get_db)):
    """Export project as YOLO format"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Get all images and annotations
    images = db.query(Image).filter(Image.project_id == project_id).all()
    classes = db.query(Class).filter(Class.project_id == project_id).all()
    
    # Build export data
    project_data = {
        "id": project_id,
        "name": project.name,
        "classes": [{"id": c.id, "name": c.name, "color": c.color} for c in classes],
        "images": []
    }
    
    for img in images:
        annotations = db.query(Annotation).filter(Annotation.image_id == img.id).all()
        
        ann_list = []
        for ann in annotations:
            class_obj = db.query(Class).filter(Class.id == ann.class_id).first()
            ann_list.append({
                "id": ann.id,
                "type": ann.type,
                "data": json.loads(ann.data) if isinstance(ann.data, str) else ann.data,
                "class_name": class_obj.name if class_obj else None
            })
        
        project_data["images"].append({
            "id": img.id,
            "filename": img.filename,
            "path": img.path,
            "width": img.width,
            "height": img.height,
            "annotations": ann_list
        })
    
    # Export
    output_dir = settings.DATASETS_ROOT / project_id / "yolo_export"
    result = YOLOExporter.export_project(project_data, output_dir, settings.DATASETS_ROOT)
    
    return {
        "message": "Export completed",
        "output_dir": str(output_dir.relative_to(settings.DATASETS_ROOT)),
        "images_count": result['images_count'],
        "classes_count": result['classes_count']
    }


@router.get("/projects/{project_id}/export/yolo/download")
def download_yolo_export(project_id: str, db: Session = Depends(get_db)):
    """Download YOLO format dataset zip package"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    output_dir = settings.DATASETS_ROOT / project_id / "yolo_export"
    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="YOLO export not found. Please export first.")
    
    # Create temporary zip file
    zip_path = settings.DATASETS_ROOT / project_id / f"{project.name}_yolo_dataset.zip"
    
    def generate_zip():
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in output_dir.rglob('*'):
                if file_path.is_file():
                    arcname = file_path.relative_to(output_dir)
                    zipf.write(file_path, arcname)
        
        with open(zip_path, 'rb') as f:
            yield from f
        
        # Clean up temporary files
        if zip_path.exists():
            zip_path.unlink()
    
    return StreamingResponse(
        generate_zip(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={project.name}_yolo_dataset.zip"
        }
    )


@router.get("/projects/{project_id}/export/zip")
def export_dataset_zip(project_id: str, db: Session = Depends(get_db)):
    """Export complete dataset zip package (contains all images and annotations)"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    images = db.query(Image).filter(Image.project_id == project_id).all()
    classes = db.query(Class).filter(Class.project_id == project_id).all()
    
    if not images:
        raise HTTPException(status_code=400, detail="No images in project")
    
    # Create temporary zip file
    zip_path = settings.DATASETS_ROOT / project_id / f"{project.name}_dataset.zip"
    
    def generate_zip():
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add class information
            classes_info = {
                "classes": [{"id": c.id, "name": c.name, "color": c.color} for c in classes]
            }
            zipf.writestr("classes.json", json.dumps(classes_info, ensure_ascii=False, indent=2))
            
            # Add images and annotations
            for img in images:
                # Add image file
                img_path = settings.DATASETS_ROOT / project_id / img.path
                if img_path.exists():
                    zipf.write(img_path, f"images/{img.filename}")
                
                # Get annotations
                annotations = db.query(Annotation).filter(Annotation.image_id == img.id).all()
                if annotations:
                    ann_list = []
                    for ann in annotations:
                        class_obj = db.query(Class).filter(Class.id == ann.class_id).first()
                        ann_data = json.loads(ann.data) if isinstance(ann.data, str) else ann.data
                        ann_list.append({
                            "id": ann.id,
                            "type": ann.type,
                            "data": ann_data,
                            "class_id": ann.class_id,
                            "class_name": class_obj.name if class_obj else None
                        })
                    
                    # Save annotations as JSON
                    ann_filename = Path(img.filename).stem + ".json"
                    zipf.writestr(f"annotations/{ann_filename}", json.dumps(ann_list, ensure_ascii=False, indent=2))
        
        with open(zip_path, 'rb') as f:
            yield from f
        
        # Clean up temporary files
        if zip_path.exists():
            zip_path.unlink()
    
    return StreamingResponse(
        generate_zip(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={project.name}_dataset.zip"
        }
    )


# ========== MQTT Service Management ==========

# ========== Model Training ==========

@router.post("/projects/{project_id}/train")
async def start_training(project_id: str, request: TrainingRequest, db: Session = Depends(get_db)):
    """Start model training: automatically export latest YOLO dataset from current project data and train"""
    import asyncio
    import logging
    from concurrent.futures import ThreadPoolExecutor
    
    logger = logging.getLogger(__name__)
    print(f"[Training] Received training request for project {project_id}")
    print(f"[Training] Request parameters: model_size={request.model_size}, epochs={request.epochs}, batch={request.batch}")
    logger.info(f"[Training] Received training request for project {project_id}")
    logger.info(f"[Training] Request parameters: model_size={request.model_size}, epochs={request.epochs}, batch={request.batch}")
    
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Get classes
    classes = db.query(Class).filter(Class.project_id == project_id).all()
    if len(classes) == 0:
        raise HTTPException(
            status_code=400,
            detail="No classes found. Please create at least one class."
        )
    
    # Prepare export data (use latest data from current database)
    images = db.query(Image).filter(Image.project_id == project_id).all()
    project_data = {
        "id": project_id,
        "name": project.name,
        "classes": [{"id": c.id, "name": c.name, "color": c.color} for c in classes],
        "images": []
    }
    for img in images:
        anns = db.query(Annotation).filter(Annotation.image_id == img.id).all()
        ann_list = []
        for ann in anns:
            class_obj = next((c for c in classes if c.id == ann.class_id), None)
            ann_list.append({
                "id": ann.id,
                "type": ann.type,
                "data": json.loads(ann.data) if isinstance(ann.data, str) else ann.data,
                "class_name": class_obj.name if class_obj else None
            })
        project_data["images"].append({
            "id": img.id,
            "filename": img.filename,
            "path": img.path,
            "width": img.width,
            "height": img.height,
            "annotations": ann_list
        })
    
    # Export YOLO dataset (overwrite old yolo_export) - run in thread pool to avoid blocking
    yolo_export_dir = settings.DATASETS_ROOT / project_id / "yolo_export"
    print(f"[Training] Preparing to export dataset to {yolo_export_dir}")
    print(f"[Training] Project data: {len(project_data['images'])} images, {len(project_data['classes'])} classes")
    try:
        # Run export in thread pool to avoid blocking the request
        # Use get_running_loop() instead of get_event_loop() for better compatibility with FastAPI
        print(f"[Training] Starting dataset export in thread pool...")
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor() as executor:
            await loop.run_in_executor(
                executor,
                YOLOExporter.export_project,
                project_data,
                yolo_export_dir,
                settings.DATASETS_ROOT
            )
        print(f"[Training] Dataset export completed successfully")
    except RuntimeError as e:
        # Fallback if no running loop (should not happen in FastAPI async context)
        error_msg = f"Event loop error: {str(e)}"
        print(f"[Training] ERROR: {error_msg}")
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)
    except Exception as e:
        error_msg = f"Auto export dataset failed: {str(e)}"
        print(f"[Training] ERROR: {error_msg}")
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)
    
    # data.yaml path
    data_yaml = yolo_export_dir / "data.yaml"
    print(f"[Training] Checking for data.yaml at {data_yaml}")
    if not data_yaml.exists():
        error_msg = f"Missing data.yaml after auto export at {data_yaml}"
        print(f"[Training] ERROR: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
    print(f"[Training] data.yaml found, proceeding to start training")
    
    # Start training using latest exported dataset - this is fast, just creates a thread
    print(f"[Training] Starting training service for project {project_id}")
    logger.info(f"[Training] Starting training service for project {project_id}")
    try:
        print(f"[Training] Calling training_service.start_training()...")
        training_info = training_service.start_training(
            project_id=project_id,
            dataset_path=yolo_export_dir,
            model_type=request.model_type,
            model_size=request.model_size,
            epochs=request.epochs,
            imgsz=request.imgsz,
            batch=request.batch,
            device=request.device,
            lr0=request.lr0,
            lrf=request.lrf,
            optimizer=request.optimizer,
            momentum=request.momentum,
            weight_decay=request.weight_decay,
            patience=request.patience,
            workers=request.workers,
            val=request.val,
            save_period=request.save_period,
            amp=request.amp,
            hsv_h=request.hsv_h,
            hsv_s=request.hsv_s,
            hsv_v=request.hsv_v,
            degrees=request.degrees,
            translate=request.translate,
            scale=request.scale,
            shear=request.shear,
            perspective=request.perspective,
            flipud=request.flipud,
            fliplr=request.fliplr,
            mosaic=request.mosaic,
            mixup=request.mixup,
        )
        training_id = training_info.get('training_id')
        print(f"[Training] Training started successfully. Training ID: {training_id}")
        logger.info(f"[Training] Training started successfully. Training ID: {training_id}")
        return training_info
    except ValueError as e:
        error_msg = str(e)
        print(f"[Training] ValueError when starting training: {error_msg}")
        logger.error(f"[Training] ValueError when starting training: {error_msg}")
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        error_msg = str(e)
        print(f"[Training] Exception when starting training: {error_msg}")
        import traceback
        print(traceback.format_exc())
        logger.error(f"[Training] Exception when starting training: {error_msg}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start training: {error_msg}")


@router.get("/projects/{project_id}/train/records")
def get_training_records(project_id: str, db: Session = Depends(get_db)):
    """Get all training records for project"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    records = training_service.get_training_records(project_id)
    # Only return basic information, not full logs
    return [{
        'training_id': r.get('training_id'),
        'status': r.get('status'),
        'start_time': r.get('start_time'),
        'end_time': r.get('end_time'),
        'model_size': r.get('model_size'),
        'epochs': r.get('epochs'),
        'imgsz': r.get('imgsz'),
        'batch': r.get('batch'),
        'device': r.get('device'),
        'current_epoch': r.get('current_epoch'),
        'metrics': r.get('metrics'),
        'error': r.get('error'),
        'model_path': r.get('model_path'),
        'log_count': r.get('log_count', len(r.get('logs', [])) if isinstance(r, dict) else 0)
    } for r in records]

@router.get("/projects/{project_id}/train/status")
def get_training_status(project_id: str, training_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Get training status"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    if training_id:
        status = training_service.get_training_record(project_id, training_id)
    else:
        status = training_service.get_training_status(project_id)
    
    if status is None:
        return {"status": "not_started"}
    
    return status

@router.get("/projects/{project_id}/train/{training_id}/logs")
def get_training_logs(project_id: str, training_id: str, db: Session = Depends(get_db)):
    """Get training logs"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    record = training_service.get_training_record(project_id, training_id)
    if not record:
        raise HTTPException(status_code=404, detail="Training record not found")
    
    return {
        "training_id": training_id,
        "logs": record.get('logs', [])
    }

@router.get("/projects/{project_id}/train/{training_id}/export")
def export_trained_model(project_id: str, training_id: str, db: Session = Depends(get_db)):
    """Export trained PT model with friendly filename."""

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    record_dict = training_service.get_training_record(project_id, training_id)
    if not record_dict:
        raise HTTPException(status_code=404, detail="Training record not found")

    if record_dict.get('status') != 'completed':
        raise HTTPException(status_code=400, detail="Training is not completed")

    model_path = record_dict.get('model_path')
    if not model_path or not Path(model_path).exists():
        raise HTTPException(status_code=404, detail="Model file not found")

    # Load DB training record for timestamp
    db_record: Optional[TrainingRecord] = (
        db.query(TrainingRecord)
        .filter(
            TrainingRecord.project_id == project_id,
            TrainingRecord.training_id == training_id,
        )
        .first()
    )
    class_names = _get_project_class_names(project_id)
    base_name = _build_model_basename(project, db_record, project_id, training_id, class_names)
    filename = f"{base_name}__pt.pt"

    return FileResponse(
        path=model_path,
        filename=filename,
        media_type='application/octet-stream'
    )


@router.post("/projects/{project_id}/train/{training_id}/export/tflite")
def export_tflite_model(
    project_id: str,
    training_id: str,
    imgsz: int = Query(256, ge=32, le=2048),
    int8: bool = Query(True),
    fraction: float = Query(0.2, ge=0.0, le=1.0),
    ne301: bool = Query(True, description="Whether to generate NE301 device quantization model (default checked)"),
    db: Session = Depends(get_db)
):
    """
    Export TFLite quantized model (default int8, imgsz=256, fraction=0.2)
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    record = training_service.get_training_record(project_id, training_id)
    if not record:
        raise HTTPException(status_code=404, detail="Training record not found")
    
    if record.get('status') != 'completed':
        raise HTTPException(status_code=400, detail="Training is not completed")
    
    model_path = record.get('model_path')
    if not model_path or not Path(model_path).exists():
        raise HTTPException(status_code=404, detail="Model file not found")

    # data.yaml path (for calibration/class information)
    data_yaml = settings.DATASETS_ROOT / project_id / "yolo_export" / "data.yaml"
    if not data_yaml.exists():
        raise HTTPException(status_code=400, detail="data.yaml not found, please ensure dataset export exists.")
    
    try:
        from ultralytics import YOLO
        model = YOLO(model_path)
        export_path = model.export(
            format='tflite',
            imgsz=imgsz,
            int8=int8,
            data=str(data_yaml),
            fraction=fraction
        )
        # export_path might be Path or str
        export_path = Path(export_path)

        # NE301 related variables (ensure defined in correct scope)
        ne301_path: Optional[str] = None
        ne301_model_bin_path: Optional[str] = None
        ne301_json_path: Optional[str] = None
        ne301_model_bin_error: Optional[str] = None
        
        if ne301:
            # Add NE301 quantization step: generate config and call stm32ai script
            quant_dir = Path(__file__).resolve().parent.parent / "quantization"
            script_path = quant_dir / "tflite_quant.py"
            if not script_path.exists():
                raise HTTPException(status_code=500, detail="Missing NE301 quantization script, please check backend/quantization/tflite_quant.py")

            # SavedModel directory: path returned after Ultralytics tflite export is under best_saved_model
            saved_model_dir = export_path.parent  # e.g. .../weights/best_saved_model
            print(f"[NE301] export_path={export_path} saved_model_dir={saved_model_dir}")
            if not saved_model_dir.exists():
                raise HTTPException(status_code=500, detail="SavedModel directory not found, cannot perform NE301 quantization")

            # Calibration set defaults to exported YOLO dataset val (fallback to train if not exists)
            calib_dir = settings.DATASETS_ROOT / project_id / "yolo_export" / "images" / "val"
            if not calib_dir.exists():
                calib_dir = settings.DATASETS_ROOT / project_id / "yolo_export" / "images" / "train"
            if not calib_dir.exists():
                raise HTTPException(status_code=400, detail="Calibration set does not exist, cannot perform NE301 quantization, please export dataset first")

            quant_workdir = saved_model_dir.parent / "ne301_quant"
            quant_workdir.mkdir(parents=True, exist_ok=True)
            config_path = quant_workdir / "user_config_quant.yaml"

            # Generate unique identifier for this quantization (timestamp + training_id)
            # This ensures each quantization creates unique files
            quant_timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            unique_uc = f"{project_id}_{training_id}_{quant_timestamp}"

            cfg = {
                "model": {
                    "name": f"{Path(model_path).stem}_ne301",
                    "uc": unique_uc,  # Use unique identifier to avoid file overwrites
                    "model_path": str(saved_model_dir.resolve()),
                    "input_shape": [imgsz, imgsz, 3],
                },
                "quantization": {
                    "fake": False,
                    "quantization_type": "per_channel",
                    "quantization_input_type": "uint8",
                    "quantization_output_type": "int8",
                    "calib_dataset_path": str(calib_dir.resolve()),
                    "export_path": str((quant_workdir / "quantized_models").resolve()),
                },
                "pre_processing": {"rescaling": {"scale": 255, "offset": 0}},
            }

            with open(config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, allow_unicode=True)

            # hydra config_name pass filename without extension (e.g., user_config_quant)
            cmd = [
                sys.executable,
                str(script_path),
                "--config-name",
                config_path.stem,
                "--config-path",
                str(quant_workdir),
            ]
            env = os.environ.copy()
            env["HYDRA_FULL_ERROR"] = "1"
            print(f"[NE301] run cmd={cmd} cwd={quant_workdir}")
            proc = subprocess.run(
                cmd,
                cwd=str(quant_workdir),
                capture_output=True,
                text=True,
                env=env,
            )
            if proc.returncode != 0:
                logger.error(
                    "[NE301] quantization failed rc=%s stdout=%s stderr=%s",
                    proc.returncode,
                    proc.stdout,
                    proc.stderr,
                )
                print(
                    f"[NE301] quantization failed rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"NE301 quantization failed: {proc.stderr or proc.stdout}",
                )

            export_dir = Path(cfg["quantization"]["export_path"])
            tflites = sorted(
                export_dir.glob("*.tflite"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not tflites:
                logger.error("[NE301] No TFLite file found in quantized_models directory: %s", export_dir)
                raise HTTPException(
                    status_code=500,
                    detail="NE301 quantization completed but generated tflite file not found",
                )
            ne301_path = str(tflites[0])
            print(f"[NE301] quantized tflite ready: {ne301_path}")

            # Verify file actually exists
            if not Path(ne301_path).exists():
                logger.error(f"[NE301] TFLite file does not exist: {ne301_path}")
                raise HTTPException(status_code=500, detail=f"NE301 TFLite file generation failed: {ne301_path}")

        # If need to generate NE301 JSON config and compile, ensure JSON file is saved first (even if compilation fails)
        if ne301 and ne301_path:
            # Save JSON config file first (this step must be outside try-except to ensure it's saved even if subsequent steps fail)
            try:
                from backend.utils.ne301_export import (
                    generate_ne301_json_config,
                    _convert_to_json_serializable
                )
                
                # Read data.yaml to get class information
                with open(data_yaml, "r", encoding="utf-8") as f:
                    data_info = yaml.safe_load(f)
                
                class_names = data_info.get("names", [])
                if isinstance(class_names, dict):
                    # If dict format {0: "class1", 1: "class2"}, convert to list
                    max_idx = max(class_names.keys())
                    names_list = [""] * (max_idx + 1)
                    for idx, name in class_names.items():
                        names_list[int(idx)] = name
                    class_names = names_list
                elif not isinstance(class_names, list):
                    class_names = []
                
                num_classes = len(class_names) if class_names else 80  # Default COCO 80 classes
                
                # Generate model name (without extension)
                tflite_file = Path(ne301_path)
                model_base_name = tflite_file.stem  # e.g., best_ne301_quant_pc_ui_xxx
                
                # Generate JSON config (will try to extract real quantization parameters and output dimensions from TFLite model)
                json_config = generate_ne301_json_config(
                    tflite_path=tflite_file,
                    model_name=model_base_name,
                    input_size=imgsz,
                    num_classes=num_classes,
                    class_names=class_names,
                    output_scale=None,  # Extract from model
                    output_zero_point=None,  # Extract from model
                    confidence_threshold=0.25,
                    iou_threshold=0.45,
                    output_shape=None,  # Extract from model
                )
                
                # Ensure JSON file is saved in same directory as TFLite file (quantized_models) for easy download
                json_output_dir = tflite_file.parent  # quantized_models directory
                json_output_dir.mkdir(parents=True, exist_ok=True)  # Ensure directory exists
                json_file_path = json_output_dir / f"{model_base_name}.json"
                
                # Save JSON config file (ensure all values are JSON serializable)
                json_config_clean = _convert_to_json_serializable(json_config)
                with open(json_file_path, "w", encoding="utf-8") as f:
                    json.dump(json_config_clean, f, indent=2, ensure_ascii=False)
                
                # Verify file was actually saved successfully
                if not json_file_path.exists():
                    logger.error(f"[NE301] Failed to save JSON file: {json_file_path}")
                    raise RuntimeError(f"JSON config file save failed: {json_file_path}")
                
                # Save JSON path (in outer scope, ensure it's returned even if subsequent steps fail)
                ne301_json_path = str(json_file_path)
                logger.info(f"[NE301] ✓ JSON config saved to: {json_file_path}")
                print(f"[NE301] ✓ JSON config saved to: {json_file_path}")
                
                # Save json_config to outer scope for subsequent use
                json_config_saved = json_config
                
            except Exception as e:
                logger.error(f"[NE301] Failed to generate JSON config file: {e}", exc_info=True)
                print(f"[NE301] Failed to generate JSON config file: {e}")
                # JSON generation failure does not affect TFLite file return
                json_config_saved = None
                ne301_json_path = None  # Ensure variable is defined to avoid NameError
            
            # Try to compile NE301 model package (this step failure does not affect file download)
            logger.info("[NE301] Starting NE301 model package compilation...")
            print("[NE301] Starting NE301 model package compilation...")
            try:
                # Ensure json_config is available (if previous generation failed, try to read from file)
                json_config_for_copy = json_config_saved if 'json_config_saved' in locals() and json_config_saved is not None else None
                if json_config_for_copy is None:
                    # If json_config doesn't exist, try to read from saved JSON file
                    if ne301_json_path and Path(ne301_json_path).exists():
                        with open(ne301_json_path, "r", encoding="utf-8") as f:
                            json_config_for_copy = json.load(f)
                            logger.info(f"[NE301] Reading JSON config from file: {ne301_json_path}")
                            print(f"[NE301] Reading JSON config from file: {ne301_json_path}")
                    else:
                        # If JSON file also doesn't exist, skip compilation step
                        logger.warning("[NE301] JSON config not available, skipping compilation step")
                        print("[NE301] JSON config not available, skipping compilation step")
                        raise RuntimeError("JSON config not available, cannot continue compilation")
                
                from backend.utils.ne301_export import (
                    copy_model_to_ne301_project,
                    build_ne301_model
                )
                
                # Get NE301 project path (prefer using initialized path)
                from backend.utils.ne301_init import get_ne301_project_path
                try:
                    ne301_project_path = get_ne301_project_path()
                except Exception as e:
                    logger.warning(f"[NE301] Failed to get NE301 project path: {e}")
                    # Fallback to environment variable or config
                    ne301_project_path = settings.NE301_PROJECT_PATH or os.environ.get("NE301_PROJECT_PATH")
                    if ne301_project_path:
                        ne301_project_path = Path(ne301_project_path)
                    else:
                        ne301_project_path = settings.DATASETS_ROOT.parent / "ne301"
                
                if not isinstance(ne301_project_path, Path):
                    ne301_project_path = Path(ne301_project_path)
                
                logger.info(f"[NE301] Checking NE301 project path: {ne301_project_path}")
                print(f"[NE301] Checking NE301 project path: {ne301_project_path}")
                logger.info(f"[NE301] Project path exists: {ne301_project_path.exists()}")
                print(f"[NE301] Project path exists: {ne301_project_path.exists()}")
                
                if not ne301_project_path.exists() or not (ne301_project_path / "Model").exists():
                    logger.warning(f"[NE301] NE301 project directory does not exist or is incomplete: {ne301_project_path}, JSON saved to: {ne301_json_path}")
                    print(f"[NE301] Project directory not found or incomplete: {ne301_project_path}")
                    print(f"[NE301] JSON config has been saved to: {ne301_json_path}")
                    print(f"[NE301] The project should be automatically cloned on startup.")
                    print(f"[NE301] If this error persists, check the startup logs for NE301 initialization.")
                    print(f"[NE301] Or manually copy files to NE301 project:")
                    print(f"  cp {tflite_file} {ne301_project_path}/Model/weights/")
                    print(f"  cp {ne301_json_path} {ne301_project_path}/Model/weights/")
                    print(f"  cd {ne301_project_path} && make model")
                    raise FileNotFoundError(f"NE301 project directory does not exist or is incomplete: {ne301_project_path}")
                else:
                    logger.info(f"[NE301] ✓ NE301 project path validation passed")
                    print(f"[NE301] ✓ NE301 project path validation passed")
                    
                    # Copy model and JSON to NE301 project
                    logger.info(f"[NE301] Starting to copy model files to NE301 project...")
                    print(f"[NE301] Starting to copy model files to NE301 project...")
                    tflite_dest, json_dest = copy_model_to_ne301_project(
                        tflite_path=tflite_file,
                        json_config=json_config_for_copy,
                        ne301_project_path=ne301_project_path,
                        model_name=model_base_name
                    )
                    logger.info(f"[NE301] Model files copied to NE301 project: {tflite_dest}, {json_dest}")
                    print(f"[NE301] Model files copied to NE301 project: {tflite_dest}, {json_dest}")
                    
                    # Use Docker to compile model (from config or environment variable)
                    use_docker = settings.NE301_USE_DOCKER if hasattr(settings, 'NE301_USE_DOCKER') else (
                        os.environ.get("NE301_USE_DOCKER", "true").lower() == "true"
                    )
                    docker_image = settings.NE301_DOCKER_IMAGE if hasattr(settings, 'NE301_DOCKER_IMAGE') else (
                        os.environ.get("NE301_DOCKER_IMAGE", "camthink/ne301-dev:latest")
                    )
                    
                    logger.info(f"[NE301] Building model package using {'Docker' if use_docker else 'local'} (image: {docker_image})...")
                    print(f"[NE301] Building model package using {'Docker' if use_docker else 'local'} (image: {docker_image})...")
                    model_bin = build_ne301_model(
                        ne301_project_path=ne301_project_path,
                        model_name=model_base_name,
                        docker_image=docker_image,
                        use_docker=use_docker
                    )
                    
                    if model_bin:
                        ne301_model_bin_path = str(model_bin)
                        logger.info(f"[NE301] ✓ Model package ready: {ne301_model_bin_path}")
                        print(f"[NE301] ✓ Model package ready: {ne301_model_bin_path}")
                    else:
                        logger.warning("[NE301] Model build completed but no package file found")
                        print("[NE301] ⚠️ Model build completed but no package file found")
                            
            except Exception as e:
                # Compilation failure does not affect quantization result return
                # But generated files (TFLite, JSON) are still available for download
                logger.error(f"[NE301] Model package build failed: {e}", exc_info=True)
                print(f"[NE301] ✗ Model package build failed: {type(e).__name__}: {e}")
                print(f"[NE301] Note: TFLite and JSON files have been generated and are available for download")
                ne301_model_bin_error = str(e)
                if ne301_path:
                    print(f"[NE301]   - TFLite: {ne301_path}")
                if 'ne301_json_path' in locals() and ne301_json_path:
                    print(f"[NE301]   - JSON: {ne301_json_path}")
                import traceback
                traceback.print_exc()

        result = {
            "message": "TFLite export success",
            "tflite_path": str(export_path),
            "params": {
                "imgsz": imgsz,
                "int8": int8,
                "fraction": fraction,
                "data": str(data_yaml)
            },
            "ne301": ne301,
        }
        
        # Add NE301 related paths (even if compilation fails, return generated files)
        if ne301 and ne301_path:
            # Verify and add TFLite file path
            tflite_path_obj = Path(ne301_path)
            if tflite_path_obj.exists():
                result["ne301_tflite"] = ne301_path
                file_size = tflite_path_obj.stat().st_size
                logger.info(f"[NE301] ✓ TFLite file generated and available for download: {ne301_path} (size: {file_size} bytes)")
            else:
                logger.error(f"[NE301] ✗ TFLite file does not exist: {ne301_path}")
                # Return path even if file doesn't exist, let frontend know where to look
            
            # Verify and add JSON config file path (should return even if compilation fails)
            if ne301_json_path:
                json_path_obj = Path(ne301_json_path)
                if json_path_obj.exists():
                    result["ne301_json"] = ne301_json_path
                    file_size = json_path_obj.stat().st_size
                    logger.info(f"[NE301] ✓ JSON config file generated and available for download: {ne301_json_path} (size: {file_size} bytes)")
                else:
                    logger.error(f"[NE301] ✗ JSON config file does not exist: {ne301_json_path}")
                    # Return path even if file doesn't exist, let frontend know where to look
            
            # Verify and add compiled model package path (only when compilation succeeds)
            if ne301_model_bin_path:
                bin_path_obj = Path(ne301_model_bin_path)
                if bin_path_obj.exists():
                    result["ne301_model_bin"] = ne301_model_bin_path
                    file_size = bin_path_obj.stat().st_size
                    logger.info(f"[NE301] ✓ Model package generated and available for download: {ne301_model_bin_path} (size: {file_size} bytes)")
                else:
                    logger.warning(f"[NE301] ⚠️ Model package does not exist (compilation may have failed): {ne301_model_bin_path}")
            elif ne301_model_bin_error:
                # Expose build failure reason so frontend can inform user
                result["ne301_model_bin_error"] = ne301_model_bin_error
        
        # Verify original TFLite file
        export_path_obj = Path(export_path)
        if export_path_obj.exists():
            file_size = export_path_obj.stat().st_size
            logger.info(f"[Export] ✓ Original TFLite file generated: {export_path} (size: {file_size} bytes)")
        else:
            logger.error(f"[Export] ✗ Original TFLite file does not exist: {export_path}")
        
        # Save quantized models to ModelRegistry
        try:
            # Read class names from data.yaml
            with open(data_yaml, "r", encoding="utf-8") as f:
                data_info = yaml.safe_load(f)
            
            class_names = data_info.get("names", [])
            if isinstance(class_names, dict):
                max_idx = max(class_names.keys())
                names_list = [""] * (max_idx + 1)
                for idx, name in class_names.items():
                    names_list[int(idx)] = name
                class_names = names_list
            elif not isinstance(class_names, list):
                class_names = []
            
            num_classes = len(class_names) if class_names else 0
            class_names_json = json.dumps(class_names) if class_names else None
            
            # Save original TFLite model
            if export_path_obj.exists():
                db_model = ModelRegistry(
                    name=f"{Path(model_path).stem}_tflite_{imgsz}",
                    source="quantization",
                    project_id=project_id,
                    training_id=training_id,
                    model_type="yolo",
                    format="tflite",
                    model_path=str(export_path),
                    input_width=imgsz,
                    input_height=imgsz,
                    num_classes=num_classes,
                    class_names=class_names_json
                )
                db.add(db_model)
                logger.info(f"[ModelRegistry] Saved original TFLite model: {export_path}")
            
            # Save NE301 quantized TFLite model
            if ne301 and ne301_path and Path(ne301_path).exists():
                # Use timestamp in name to ensure uniqueness for multiple quantizations
                quant_timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
                db_model_ne301 = ModelRegistry(
                    name=f"{Path(model_path).stem}_ne301_tflite_{imgsz}_{quant_timestamp}",
                    source="quantization",
                    project_id=project_id,
                    training_id=training_id,
                    model_type="ne301",
                    format="tflite",
                    model_path=ne301_path,
                    input_width=imgsz,
                    input_height=imgsz,
                    num_classes=num_classes,
                    class_names=class_names_json
                )
                db.add(db_model_ne301)
                logger.info(f"[ModelRegistry] Saved NE301 TFLite model: {ne301_path}")

            # Save NE301 compiled bin package (if available)
            if ne301 and ne301_model_bin_path and Path(ne301_model_bin_path).exists():
                # Use the same timestamp for consistency
                db_model_ne301_bin = ModelRegistry(
                    name=f"{Path(model_path).stem}_ne301_bin_{imgsz}_{quant_timestamp}",
                    source="quantization",
                    project_id=project_id,
                    training_id=training_id,
                    model_type="ne301",
                    format="bin",
                    model_path=ne301_model_bin_path,
                    input_width=imgsz,
                    input_height=imgsz,
                    num_classes=num_classes,
                    class_names=class_names_json
                )
                db.add(db_model_ne301_bin)
                logger.info(f"[ModelRegistry] Saved NE301 bin package: {ne301_model_bin_path}")
            
            db.commit()
            
        except Exception as e:
            logger.error(f"[ModelRegistry] Failed to save quantized models: {e}", exc_info=True)
            db.rollback()
            # Don't fail the entire request if database save fails
        
        return result
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Ultralytics or TensorFlow not installed: {str(e)}")
    except ConnectionError as e:
        # Network download failure (usually when ultralytics tries to download calibration data file)
        error_msg = str(e)
        import traceback
        tb = traceback.format_exc()
        logger.error("[Export] Network download failed during TFLite export: %s", tb)
        print(f"[Export] Network download failed: {tb}")
        
        # Check if it's a calibration data download issue
        if "calibration_image_sample_data" in error_msg or "Download failure" in error_msg:
            detail_msg = (
                "Model quantization export failed: Unable to download calibration sample data from GitHub.\n\n"
                "Possible causes:\n"
                "1. Network connection issues (SSL/TLS certificate verification failed)\n"
                "2. GitHub access restricted\n"
                "3. Firewall or proxy settings issues\n\n"
                "Solutions:\n"
                "1. Check network connection and firewall settings\n"
                "2. If using proxy, configure proxy environment variables (HTTP_PROXY, HTTPS_PROXY)\n"
                "3. Check if system SSL certificates are up to date\n"
                "4. Try manually testing GitHub access on server: curl -I https://github.com\n"
                "5. If problem persists, try running export operation outside Docker container\n\n"
                f"Detailed error: {error_msg}"
            )
        else:
            detail_msg = f"Network connection error: {error_msg}"
        
        raise HTTPException(status_code=500, detail=detail_msg)
    except Exception as e:
        try:
            import traceback
            tb = traceback.format_exc()
            logger.error("[Export] TFLite export failed: %s", tb)
            print(f"[Export] TFLite export failed: {tb}")
            
            # Check if error message contains download-related keywords
            error_msg = str(e)
            if "Download failure" in error_msg or "curl return value" in error_msg.lower() or "ConnectionError" in str(type(e).__name__):
                detail_msg = (
                    "Model quantization export failed: Network download error.\n\n"
                    "Possible causes:\n"
                    "1. Network connection issues\n"
                    "2. SSL/TLS certificate verification failed\n"
                    "3. GitHub access restricted\n\n"
                    "Please check network connection or contact administrator.\n\n"
                    f"Detailed error: {error_msg}"
                )
                raise HTTPException(status_code=500, detail=detail_msg)
        except HTTPException:
            raise
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"TFLite export failed: {str(e)}")

@router.get("/projects/{project_id}/train/{training_id}/export/tflite/download")
def download_tflite_export(
    project_id: str,
    training_id: str,
    file_type: str = Query(..., description="File type: tflite, ne301_tflite, ne301_json, ne301_model_bin"),
    db: Session = Depends(get_db)
):
    """
    Download model quantization export files
    
    Args:
        project_id: Project ID
        training_id: Training ID
        file_type: File type (tflite, ne301_tflite, ne301_json, ne301_model_bin)
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Load DB training record for timestamp and imgsz
    db_record: Optional[TrainingRecord] = (
        db.query(TrainingRecord)
        .filter(
            TrainingRecord.project_id == project_id,
            TrainingRecord.training_id == training_id,
        )
        .first()
    )
    class_names = _get_project_class_names(project_id)
    base_name = _build_model_basename(project, db_record, project_id, training_id, class_names)
    
    # Build possible file paths
    # Note: Training directory may be train_{training_id} or train_{project_id} (if training_id contains timestamp)
    # Try multiple possible paths
    possible_base_dirs = [
        settings.DATASETS_ROOT / project_id / f"train_{training_id}",
        settings.DATASETS_ROOT / project_id / f"train_{project_id}",  # Fallback to project_id
    ]
    
    # If training_id contains timestamp (format: xxx_yyyymmdd_hhmmss), also try removing timestamp part
    if "_" in training_id:
        parts = training_id.rsplit("_", 2)  # Split into at most 3 parts, support xxx_yyyymmdd_hhmmss
        if len(parts) >= 2:
            # Try removing last timestamp part
            base_id = "_".join(parts[:-2]) if len(parts) > 2 else parts[0]
            possible_base_dirs.insert(1, settings.DATASETS_ROOT / project_id / f"train_{base_id}")
    
    # Find actual existing training directory
    base_dir = None
    for possible_dir in possible_base_dirs:
        if possible_dir.exists() and (possible_dir / "weights").exists():
            base_dir = possible_dir
            logger.info(f"[Download] Found training directory: {base_dir}")
            break
    
    if not base_dir:
        # If all not found, use first as default (will error in subsequent checks)
        base_dir = possible_base_dirs[0]
        logger.warning(f"[Download] Training directory not found, using default path: {base_dir} (may not exist)")
        logger.info(f"[Download] Tried paths: {[str(d) for d in possible_base_dirs]}")
    
    weights_dir = base_dir / "weights"
    
    logger.info(f"[Download] Searching for file type: {file_type}, base directory: {base_dir}")
    
    file_path = None
    filename = None
    
    if file_type == "tflite":
        # Find latest TFLite file (original TFLite exported by Ultralytics)
        tflite_files = list(weights_dir.glob("*.tflite"))
        logger.info(f"[Download] Found {len(tflite_files)} TFLite files in {weights_dir}")
        if not tflite_files:
            # Also check best_saved_model directory
            saved_model_dir = weights_dir / "best_saved_model"
            if saved_model_dir.exists():
                tflite_files = list(saved_model_dir.glob("*.tflite"))
                logger.info(f"[Download] Found {len(tflite_files)} TFLite files in best_saved_model directory")
        if not tflite_files:
            raise HTTPException(status_code=404, detail=f"TFLite file not found in {weights_dir}")
        file_path = max(tflite_files, key=lambda p: p.stat().st_mtime)
        filename = f"{base_name}__tflite.tflite"
        logger.info(f"[Download] Selected file: {file_path}")
    elif file_type == "ne301_tflite":
        # NE301 quantized TFLite file
        ne301_dir = weights_dir / "ne301_quant" / "quantized_models"
        logger.info(f"[Download] Searching for NE301 TFLite file, directory: {ne301_dir}")
        if not ne301_dir.exists():
            logger.error(f"[Download] NE301 directory does not exist: {ne301_dir}")
            raise HTTPException(status_code=404, detail=f"NE301 TFLite directory not found: {ne301_dir}")
        tflite_files = list(ne301_dir.glob("*.tflite"))
        logger.info(f"[Download] Found {len(tflite_files)} TFLite files in {ne301_dir}: {[f.name for f in tflite_files]}")
        if not tflite_files:
            raise HTTPException(status_code=404, detail=f"NE301 TFLite file not found in {ne301_dir}")
        file_path = max(tflite_files, key=lambda p: p.stat().st_mtime)
        filename = f"{base_name}__ne301.tflite"
        logger.info(f"[Download] Selected file: {file_path}")
    elif file_type == "ne301_json":
        # NE301 JSON configuration file
        ne301_dir = weights_dir / "ne301_quant" / "quantized_models"
        logger.info(f"[Download] Searching for NE301 JSON file, directory: {ne301_dir}")
        if not ne301_dir.exists():
            logger.error(f"[Download] NE301 directory does not exist: {ne301_dir}")
            raise HTTPException(status_code=404, detail=f"NE301 JSON directory not found: {ne301_dir}")
        json_files = list(ne301_dir.glob("*.json"))
        logger.info(f"[Download] Found {len(json_files)} JSON files in {ne301_dir}: {[f.name for f in json_files]}")
        if not json_files:
            raise HTTPException(status_code=404, detail=f"NE301 JSON file not found in {ne301_dir}")
        file_path = max(json_files, key=lambda p: p.stat().st_mtime)
        filename = f"{base_name}__ne301.json"
        logger.info(f"[Download] Selected file: {file_path}")
    elif file_type == "ne301_model_bin":
        # NE301 compiled device update package (prefer finding packaged _pkg.bin files)
        from backend.utils.ne301_init import get_ne301_project_path
        try:
            ne301_project_path = get_ne301_project_path()
        except Exception:
            ne301_project_path = Path(os.environ.get("NE301_PROJECT_PATH", "/workspace/ne301"))
        
        logger.info(f"[Download] Searching for NE301 model package, project path: {ne301_project_path}")
        
        # Prefer finding packaged device update package (format: *_v*_pkg.bin)
        build_dir = ne301_project_path / "build"
        model_bin_path = None
        
        if build_dir.exists():
            # Find all _pkg.bin files (device-updatable packages)
            pkg_files = list(build_dir.glob("*_pkg.bin"))
            if pkg_files:
                # Sort by modification time, select newest
                pkg_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                model_bin_path = pkg_files[0]
                logger.info(f"[Download] Found device-updatable package: {model_bin_path}")
        
        # If packaged file not found, try to find original .bin file
        if not model_bin_path:
            possible_paths = [
                ne301_project_path / "build" / "ne301_Model.bin",
                ne301_project_path / "Model" / "build" / "ne301_Model.bin",
                ne301_project_path / "build" / "Model.bin",
            ]
            
            for path in possible_paths:
                if path.exists():
                    model_bin_path = path
                    logger.info(f"[Download] Found raw model package (unpackaged): {model_bin_path}")
                    logger.warning(f"[Download] Note: This is a raw .bin file, not a device-updatable package format")
                    break
        
        if not model_bin_path:
            logger.error(f"[Download] Model package not found, tried searching for packaged files (*_pkg.bin) and raw files")
            raise HTTPException(status_code=404, detail=f"NE301 model package not found in {build_dir}")
        
        file_path = model_bin_path
        filename = f"{base_name}__ne301.bin"
    else:
        raise HTTPException(status_code=400, detail=f"Invalid file_type: {file_type}. Must be one of: tflite, ne301_tflite, ne301_json, ne301_model_bin")
    
    # Final verification that file exists
    if not file_path or not file_path.exists():
        logger.error(f"[Download] ✗ File does not exist: {file_path} (file_type: {file_type})")
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    
    # Verify file size (file should not be empty)
    file_size = file_path.stat().st_size
    if file_size == 0:
        logger.warning(f"[Download] ⚠️ File size is 0: {file_path}")
    
    logger.info(f"[Download] ✓ File validation passed, ready for download: {file_path} (size: {file_size} bytes)")
    
    # Determine media type
    media_type = 'application/octet-stream'
    if file_type.endswith('json'):
        media_type = 'application/json'
    
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type
    )

@router.post("/projects/{project_id}/train/stop")
def stop_training(project_id: str, training_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Stop training (optionally pass training_id; defaults to stopping current active training)"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    success = training_service.stop_training(project_id, training_id)
    if not success:
        raise HTTPException(status_code=400, detail="No active running training found")
    
    return {"message": "Training stopped"}

@router.post("/projects/{project_id}/train/{training_id}/test")
async def test_model(
    project_id: str,
    training_id: str,
    file: UploadFile = File(...),
    conf: float = Query(0.25, ge=0.0, le=1.0),
    iou: float = Query(0.45, ge=0.0, le=1.0),
    db: Session = Depends(get_db)
):
    """Test image using trained model"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    record = training_service.get_training_record(project_id, training_id)
    if not record:
        raise HTTPException(status_code=404, detail="Training record not found")
    
    if record.get('status') != 'completed':
        raise HTTPException(status_code=400, detail="Training is not completed")
    
    model_path = record.get('model_path')
    if not model_path or not Path(model_path).exists():
        raise HTTPException(status_code=404, detail="Model file not found")
    
    try:
        from ultralytics import YOLO
        
        # Read uploaded image
        image_bytes = await file.read()
        image = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")  # Ensure RGB to avoid detection issues with transparency/grayscale
        
        # Load model
        model = YOLO(model_path)
        
        # Perform inference
        results = model.predict(
            source=image,
            conf=conf,
            iou=iou,
            save=False,
            verbose=False
        )
        
        # Parse results
        result = results[0]
        detections = []
        
        # Get class names (from data.yaml or model)
        names = result.names if hasattr(result, 'names') else {}
        
        # Check if there are detection boxes
        if hasattr(result, 'boxes') and result.boxes is not None:
            for box in result.boxes:
                # Get bounding box coordinates
                xyxy = box.xyxy[0].cpu().numpy()
                conf_score = float(box.conf[0].cpu().numpy())
                cls_id = int(box.cls[0].cpu().numpy())
                cls_name = names.get(cls_id, f"class_{cls_id}")
                
                detections.append({
                    "class_id": cls_id,
                    "class_name": cls_name,
                    "confidence": conf_score,
                    "bbox": {
                        "x1": float(xyxy[0]),
                        "y1": float(xyxy[1]),
                        "x2": float(xyxy[2]),
                        "y2": float(xyxy[3])
                    }
                })
        
        # Debug log: record inference output summary (use both print and logger to avoid missing logs if log level not configured)
        has_boxes = hasattr(result, 'boxes') and result.boxes is not None
        boxes_count = len(result.boxes) if has_boxes else 0
        debug_line = (
            f"[TestPredict] project={project_id} training_id={training_id} "
            f"img={image.width}x{image.height} boxes_count={boxes_count} detections={len(detections)} "
            f"conf={conf:.3f} iou={iou:.3f} names={list(names.values()) if names else 'N/A'} "
            f"has_boxes={has_boxes}"
        )
        try:
            logger.info(debug_line)
        except Exception:
            pass
        print(debug_line)
        
        # Draw detection results on image (result.plot returns BGR, need to convert to RGB)
        annotated_bgr = result.plot()
        annotated_rgb = annotated_bgr[..., ::-1]  # BGR -> RGB
        annotated_pil = PILImage.fromarray(annotated_rgb)
        
        # Convert to base64
        import base64
        img_buffer = io.BytesIO()
        annotated_pil.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        img_base64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')
        
        return {
            "detections": detections,
            "detection_count": len(detections),
            "annotated_image": f"data:image/png;base64,{img_base64}",
            "image_size": {
                "width": image.width,
                "height": image.height
            }
        }
        
    except ImportError:
        raise HTTPException(status_code=500, detail="Ultralytics library is not installed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Model inference failed: {str(e)}")


@router.delete("/projects/{project_id}/train")
def clear_training(project_id: str, training_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Clear training record"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    training_service.clear_training(project_id, training_id)
    return {"message": "Training record cleared"}


# ========== Model Registry Management ==========

@router.post("/models/upload")
async def upload_model(
    file: UploadFile = File(...),
    model_name: str = Form(..., description="Model name"),
    model_type: str = Form("yolov8n", description="Model type (e.g., yolov8n)"),
    input_size: int = Form(640, ge=32, le=2048, description="Input image size"),
    num_classes: int = Form(80, ge=1, description="Number of classes"),
    class_names: Optional[str] = Form(None, description="JSON array of class names"),
    db: Session = Depends(get_db)
):
    """
    Upload a pre-trained .pt model file to model space.
    The model will be stored in ModelRegistry as a standalone model (not associated with any project).
    """
    # Validate file extension
    if not file.filename or not file.filename.lower().endswith('.pt'):
        raise HTTPException(status_code=400, detail="Only .pt model files are supported")
    
    # Parse class names
    parsed_class_names: List[str] = []
    if class_names:
        try:
            raw = json.loads(class_names)
            if isinstance(raw, list):
                parsed_class_names = [str(x) for x in raw]
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid class_names JSON format")
    
    # Standalone models: store in standalone_models directory (will create subdirectory after getting model_id)
    upload_dir = settings.DATASETS_ROOT / "standalone_models"
    upload_dir.mkdir(parents=True, exist_ok=True)
    # Use temporary filename first, will move to model_id subdirectory after creation
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    safe_name = _slugify(model_name, max_len=50)
    filename = f"{safe_name}_{timestamp}.pt"
    file_path = upload_dir / filename
    source_type = "standalone"
    
    # Save uploaded file (temporary location for standalone models)
    try:
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
    except Exception as e:
        logger.error(f"Failed to save uploaded model: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded model: {str(e)}")
    
    # Use provided model_type or try to infer from file
    final_model_type = model_type
    # Only infer if model_type is not provided or is empty
    if not final_model_type:
        # Try to infer model type from file (basic check)
        try:
            from ultralytics import YOLO
            model = YOLO(str(file_path))
            # Try to get model info
            model_info = model.info(verbose=False)
            # Infer from model name or architecture
            if hasattr(model, 'model') and hasattr(model.model, 'yaml'):
                yaml_path = model.model.yaml
                if yaml_path and Path(yaml_path).exists():
                    with open(yaml_path, 'r') as f:
                        yaml_data = yaml.safe_load(f)
                        arch = yaml_data.get('yaml_file', '').lower()
                        if 'yolov8' in arch:
                            final_model_type = 'yolov8n'  # Default to yolov8n if yolov8 detected
                        elif 'yolov11' in arch:
                            final_model_type = 'yolov11n'  # Default to yolov11n if yolov11 detected
        except Exception as e:
            logger.warning(f"Could not infer model type from file: {e}")
            final_model_type = 'yolov8n'  # Default fallback
    # If user provided model_type, use it directly (don't override)
    
    # Store in ModelRegistry (use temporary path for standalone models)
    model_reg = ModelRegistry(
        name=model_name,
        source=source_type,
        project_id=None,  # Standalone models are not associated with any project
        training_id=None,
        model_type=final_model_type,
        format="pt",
        model_path=str(file_path.resolve()),
        input_width=input_size,
        input_height=input_size,
        num_classes=num_classes,
        class_names=json.dumps(parsed_class_names) if parsed_class_names else None,
    )
    
    db.add(model_reg)
    db.commit()
    db.refresh(model_reg)
    
    # Move file to model_id subdirectory for standalone models
    model_dir = upload_dir / str(model_reg.id)
    model_dir.mkdir(parents=True, exist_ok=True)
    final_file_path = model_dir / filename
    try:
        shutil.move(str(file_path), str(final_file_path))
        # Update model_path in database
        model_reg.model_path = str(final_file_path.resolve())
        db.commit()
        file_path = final_file_path
    except Exception as e:
        logger.error(f"Failed to move standalone model to subdirectory: {e}", exc_info=True)
        # If move fails, keep using original path
        pass
    
    logger.info(f"Uploaded model: {model_name} (ID: {model_reg.id}, Path: {file_path})")
    
    return {
        "model_id": model_reg.id,
        "model_name": model_name,
        "file_path": str(file_path),
        "message": "Model uploaded successfully"
    }


@router.post("/models/{model_id}/quantize/ne301")
async def quantize_model_to_ne301(
    model_id: int,
    imgsz: int = Query(256, ge=256, le=640, description="Input image size for quantization"),
    int8: bool = Query(True, description="Use int8 quantization"),
    fraction: float = Query(0.2, ge=0.0, le=1.0, description="Calibration dataset fraction"),
    db: Session = Depends(get_db)
):
    """
    Quantize an uploaded .pt model to NE301 .bin format.
    This endpoint:
    1. Converts .pt to TFLite (with quantization)
    2. Generates NE301 JSON config
    3. Compiles to NE301 .bin package
    4. Stores the result in ModelRegistry
    """
    # Get model from registry
    model_reg = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not model_reg:
        raise HTTPException(status_code=404, detail="Model not found")
    
    if model_reg.format != "pt":
        raise HTTPException(status_code=400, detail="Only .pt models can be quantized to NE301")
    
    model_path = Path(model_reg.model_path)
    if not model_path.exists():
        raise HTTPException(status_code=404, detail="Model file not found")
    
    # Get class names
    class_names: List[str] = []
    if model_reg.class_names:
        try:
            raw = json.loads(model_reg.class_names)
            if isinstance(raw, list):
                class_names = [str(x) for x in raw]
        except json.JSONDecodeError:
            pass
    
    num_classes = model_reg.num_classes or len(class_names) or 80
    
    # Create temporary data.yaml for export (required by Ultralytics)
    temp_dir = settings.DATASETS_ROOT / "temp_quant" / str(model_id)
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    data_yaml_path = temp_dir / "data.yaml"
    data_yaml_content = {
        "path": str(temp_dir),
        "train": "images/train",
        "val": "images/val",
        "names": class_names if class_names else {i: f"class_{i}" for i in range(num_classes)}
    }
    
    with open(data_yaml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data_yaml_content, f, allow_unicode=True)
    
    # Create minimal calibration dataset (fake data for quantization)
    calib_dir = temp_dir / "images" / "val"
    calib_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate a few fake calibration images (required by quantization)
    try:
        import numpy as np
        from PIL import Image
        for i in range(10):  # Generate 10 fake images
            fake_img = Image.fromarray(np.random.randint(0, 255, (imgsz, imgsz, 3), dtype=np.uint8))
            fake_img.save(calib_dir / f"calib_{i:03d}.jpg")
    except Exception as e:
        logger.warning(f"Could not generate fake calibration images: {e}")
    
    try:
        # Step 1: Export to TFLite
        from ultralytics import YOLO
        model = YOLO(str(model_path))
        export_path = model.export(
            format='tflite',
            imgsz=imgsz,
            int8=int8,
            data=str(data_yaml_path),
            fraction=fraction
        )
        export_path = Path(export_path)
        
        if not export_path.exists():
            raise HTTPException(status_code=500, detail="TFLite export failed: output file not found")
        
        # Step 2: NE301 quantization (if needed, use the existing quantization script)
        quant_dir = Path(__file__).resolve().parent.parent / "quantization"
        script_path = quant_dir / "tflite_quant.py"
        
        saved_model_dir = export_path.parent
        if not saved_model_dir.exists():
            raise HTTPException(status_code=500, detail="SavedModel directory not found")
        
        quant_workdir = saved_model_dir.parent / "ne301_quant"
        quant_workdir.mkdir(parents=True, exist_ok=True)
        config_path = quant_workdir / "user_config_quant.yaml"
        
        # Generate unique identifier for this quantization (timestamp)
        # This ensures each quantization creates unique files
        quant_timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        unique_uc = f"{model_id}_{quant_timestamp}"
        
        cfg = {
            "model": {
                "name": f"{model_path.stem}_ne301",
                "uc": unique_uc,  # Use unique identifier to avoid file overwrites
                "model_path": str(saved_model_dir.resolve()),
                "input_shape": [imgsz, imgsz, 3],
            },
            "quantization": {
                "fake": False,
                "quantization_type": "per_channel",
                "quantization_input_type": "uint8",
                "quantization_output_type": "int8",
                "calib_dataset_path": str(calib_dir.resolve()),
                "export_path": str((quant_workdir / "quantized_models").resolve()),
            },
            "pre_processing": {"rescaling": {"scale": 255, "offset": 0}},
        }
        
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)
        
        cmd = [
            sys.executable,
            str(script_path),
            "--config-name",
            config_path.stem,
            "--config-path",
            str(quant_workdir),
        ]
        env = os.environ.copy()
        env["HYDRA_FULL_ERROR"] = "1"
        
        proc = subprocess.run(
            cmd,
            cwd=str(quant_workdir),
            capture_output=True,
            text=True,
            env=env,
        )
        
        if proc.returncode != 0:
            logger.error(f"[NE301] Quantization failed: {proc.stderr or proc.stdout}")
            raise HTTPException(
                status_code=500,
                detail=f"NE301 quantization failed: {proc.stderr or proc.stdout}"
            )
        
        export_dir = Path(cfg["quantization"]["export_path"])
        tflites = sorted(
            export_dir.glob("*.tflite"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        
        if not tflites:
            raise HTTPException(
                status_code=500,
                detail="NE301 quantization completed but TFLite file not found"
            )
        
        ne301_tflite_path = tflites[0]
        
        # Step 3: Generate NE301 JSON config
        from backend.utils.ne301_export import (
            generate_ne301_json_config,
            copy_model_to_ne301_project,
            build_ne301_model
        )
        
        model_base_name = ne301_tflite_path.stem
        json_config = generate_ne301_json_config(
            tflite_path=ne301_tflite_path,
            model_name=model_base_name,
            input_size=imgsz,
            num_classes=num_classes,
            class_names=class_names,
            output_scale=None,
            output_zero_point=None,
            confidence_threshold=0.25,
            iou_threshold=0.45,
            output_shape=None,
        )
        
        json_output_dir = ne301_tflite_path.parent
        json_file_path = json_output_dir / f"{model_base_name}.json"
        with open(json_file_path, "w", encoding="utf-8") as f:
            json.dump(json_config, f, indent=2, ensure_ascii=False)
        
        # Step 4: Copy to NE301 project and compile
        ne301_project_path = Path(settings.NE301_PROJECT_PATH) if settings.NE301_PROJECT_PATH else Path("/workspace/ne301")
        if not ne301_project_path.exists():
            raise HTTPException(
                status_code=500,
                detail=f"NE301 project path does not exist: {ne301_project_path}"
            )
        
        copy_model_to_ne301_project(
            tflite_path=ne301_tflite_path,
            json_config=json_config,
            ne301_project_path=ne301_project_path,
            model_name=model_base_name
        )
        
        # Build NE301 model
        ne301_bin_path = build_ne301_model(
            ne301_project_path=ne301_project_path,
            model_name=model_base_name,
            docker_image=settings.NE301_DOCKER_IMAGE,
            use_docker=settings.NE301_USE_DOCKER
        )
        
        if not ne301_bin_path or not ne301_bin_path.exists():
            raise HTTPException(
                status_code=500,
                detail="NE301 compilation failed: .bin file not generated"
            )
        
        # Step 5: For standalone models, copy files to model directory
        is_standalone = model_reg.source == "standalone" or not model_reg.project_id
        if is_standalone:
            # Determine standalone model directory
            model_dir = settings.DATASETS_ROOT / "standalone_models" / str(model_id)
            model_dir.mkdir(parents=True, exist_ok=True)
            
            # Use timestamp to ensure unique filenames for each quantization
            # Extract base name and add timestamp to avoid overwrites
            tflite_base = ne301_tflite_path.stem  # filename without extension
            tflite_ext = ne301_tflite_path.suffix  # .tflite
            standalone_tflite_path = model_dir / f"{tflite_base}_{quant_timestamp}{tflite_ext}"
            shutil.copy2(str(ne301_tflite_path), str(standalone_tflite_path))
            ne301_tflite_path = standalone_tflite_path
            
            # Copy JSON file with timestamp
            json_base = json_file_path.stem
            json_ext = json_file_path.suffix
            standalone_json_path = model_dir / f"{json_base}_{quant_timestamp}{json_ext}"
            shutil.copy2(str(json_file_path), str(standalone_json_path))
            
            # Copy BIN file with timestamp
            bin_base = ne301_bin_path.stem
            bin_ext = ne301_bin_path.suffix
            standalone_bin_path = model_dir / f"{bin_base}_{quant_timestamp}{bin_ext}"
            shutil.copy2(str(ne301_bin_path), str(standalone_bin_path))
            ne301_bin_path = standalone_bin_path
        
        # Step 6: Store NE301 TFLite and BIN in ModelRegistry
        # Use quant_timestamp for consistent naming across files and database records
        
        # Store TFLite
        tflite_reg = ModelRegistry(
            name=f"{model_reg.name}_ne301_tflite_{quant_timestamp}",
            source=model_reg.source,  # Inherit source from original model
            project_id=model_reg.project_id,
            training_id=None,
            model_type="ne301",
            format="tflite",
            model_path=str(ne301_tflite_path.resolve()),
            input_width=imgsz,
            input_height=imgsz,
            num_classes=num_classes,
            class_names=model_reg.class_names,
        )
        db.add(tflite_reg)
        db.flush()
        
        # Store BIN
        bin_reg = ModelRegistry(
            name=f"{model_reg.name}_ne301_bin_{quant_timestamp}",
            source=model_reg.source,  # Inherit source from original model
            project_id=model_reg.project_id,
            training_id=None,
            model_type="ne301",
            format="bin",
            model_path=str(ne301_bin_path.resolve()),
            input_width=imgsz,
            input_height=imgsz,
            num_classes=num_classes,
            class_names=model_reg.class_names,
        )
        db.add(bin_reg)
        db.commit()
        
        logger.info(f"Quantized model {model_id} to NE301: TFLite ID={tflite_reg.id}, BIN ID={bin_reg.id}")
        
        return {
            "model_id": model_id,
            "ne301_tflite_id": tflite_reg.id,
            "ne301_bin_id": bin_reg.id,
            "ne301_tflite_path": str(ne301_tflite_path),
            "ne301_bin_path": str(ne301_bin_path),
            "message": "Model quantized to NE301 successfully"
        }
        
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Required library not installed: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Quantization failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Quantization failed: {str(e)}"
        )
    finally:
        # Cleanup temp directory (optional, can keep for debugging)
        # shutil.rmtree(temp_dir, ignore_errors=True)
        pass


@router.get("/models/{model_id}/download")
def download_model(model_id: int, db: Session = Depends(get_db)):
    """Download model from ModelRegistry by model_id"""
    model_reg = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not model_reg:
        raise HTTPException(status_code=404, detail="Model not found")
    
    model_path = Path(model_reg.model_path)
    if not model_path.exists():
        raise HTTPException(status_code=404, detail="Model file not found")
    
    # Build friendly filename
    project = db.query(Project).filter(Project.id == model_reg.project_id).first() if model_reg.project_id else None
    class_names: List[str] = []
    if model_reg.class_names:
        try:
            raw = json.loads(model_reg.class_names)
            if isinstance(raw, list):
                class_names = [str(x) for x in raw]
        except json.JSONDecodeError:
            pass
    
    db_record = None
    if model_reg.training_id and model_reg.project_id:
        db_record = (
            db.query(TrainingRecord)
            .filter(
                TrainingRecord.project_id == model_reg.project_id,
                TrainingRecord.training_id == model_reg.training_id,
            )
            .first()
        )
    
    base_name = _build_model_basename(
        project or Project(id=model_reg.project_id or "", name=model_reg.name or ""),
        db_record,
        model_reg.project_id or "",
        model_reg.training_id,
        class_names,
    )
    
    # Determine file extension and suffix
    fmt = (model_reg.format or "").lower()
    if fmt == "tflite":
        # Distinguish NE301 vs normal TFLite
        if (model_reg.model_type or "").lower() == "ne301":
            filename = f"{base_name}__ne301.tflite"
        else:
            filename = f"{base_name}__tflite.tflite"
    elif fmt in ("bin", "ne301_bin"):
        # NE301 compiled device package
        filename = f"{base_name}__ne301.bin"
    else:
        # Default to PT
        filename = f"{base_name}__pt.pt"
    
    return FileResponse(
        path=str(model_path),
        filename=filename,
        media_type='application/octet-stream'
    )


@router.get("/models/{model_id}/related")
def get_model_related_files(model_id: int, db: Session = Depends(get_db)):
    """Get related files for NE301 bin package (tflite and json)"""
    model_reg = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not model_reg:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Only for NE301 bin packages
    if (model_reg.format or "").lower() != "bin" or (model_reg.model_type or "").lower() != "ne301":
        raise HTTPException(
            status_code=400,
            detail="This endpoint is only for NE301 bin packages"
        )
    
    # Find related tflite and json files
    related_files = {
        "tflite": None,
        "json": None
    }
    
    is_standalone = model_reg.source == "standalone" or not model_reg.project_id
    
    if is_standalone:
        # For standalone models: find by name pattern and source
        # BIN model name format: {name}_ne301_bin
        # TFLite model name format: {name}_ne301_tflite
        bin_name = model_reg.name
        if bin_name.endswith("_ne301_bin"):
            base_name = bin_name[:-10]  # Remove "_ne301_bin" suffix
            expected_tflite_name = f"{base_name}_ne301_tflite"
            
            # Find TFLite in ModelRegistry
            tflite_reg = (
                db.query(ModelRegistry)
                .filter(
                    ModelRegistry.name == expected_tflite_name,
                    ModelRegistry.format == "tflite",
                    ModelRegistry.model_type == "ne301",
                    ModelRegistry.source == "standalone",
                    ModelRegistry.project_id.is_(None),
                    ModelRegistry.training_id.is_(None)
                )
                .first()
            )
            if tflite_reg and Path(tflite_reg.model_path).exists():
                related_files["tflite"] = {
                    "model_id": tflite_reg.id,
                    "path": tflite_reg.model_path,
                    "name": tflite_reg.name
                }
            
            # Find JSON file in standalone model directory
            # JSON is stored in: standalone_models/{original_pt_model_id}/*.json
            # We need to find the original PT model ID from the BIN model name
            # BIN model name format: {original_name}_ne301_bin
            # Original PT model name: {original_name}
            original_model_name = base_name  # base_name is the original model name without _ne301_bin suffix
            
            # Find original PT model to get its ID
            original_pt_model = (
                db.query(ModelRegistry)
                .filter(
                    ModelRegistry.name == original_model_name,
                    ModelRegistry.format == "pt",
                    ModelRegistry.source == "standalone",
                    ModelRegistry.project_id.is_(None),
                    ModelRegistry.training_id.is_(None)
                )
                .first()
            )
            
            # Use original PT model ID to find JSON file
            original_model_id = original_pt_model.id if original_pt_model else model_id
            model_dir = settings.DATASETS_ROOT / "standalone_models" / str(original_model_id)
            if model_dir.exists():
                json_files = list(model_dir.glob("*.json"))
                if json_files:
                    # Use the most recent json file
                    json_file = max(json_files, key=lambda p: p.stat().st_mtime)
                    related_files["json"] = {
                        "path": str(json_file),
                        "name": json_file.name
                    }
    elif model_reg.training_id and model_reg.project_id:
        # For project-associated models: find from same training
        # Find NE301 tflite from same training
        tflite_reg = (
            db.query(ModelRegistry)
            .filter(
                ModelRegistry.project_id == model_reg.project_id,
                ModelRegistry.training_id == model_reg.training_id,
                ModelRegistry.format == "tflite",
                ModelRegistry.model_type == "ne301"
            )
            .first()
        )
        if tflite_reg and Path(tflite_reg.model_path).exists():
            related_files["tflite"] = {
                "model_id": tflite_reg.id,
                "path": tflite_reg.model_path,
                "name": tflite_reg.name
            }
        
        # Find json file by inferring path from training directory
        # JSON is typically in: .../train_{training_id}/weights/ne301_quant/quantized_models/*.json
        training_dir = settings.DATASETS_ROOT / model_reg.project_id / f"train_{model_reg.training_id}"
        if training_dir.exists():
            json_dir = training_dir / "weights" / "ne301_quant" / "quantized_models"
            if json_dir.exists():
                json_files = list(json_dir.glob("*.json"))
                if json_files:
                    # Use the most recent json file
                    json_file = max(json_files, key=lambda p: p.stat().st_mtime)
                    related_files["json"] = {
                        "path": str(json_file),
                        "name": json_file.name
                    }
    
    return related_files


@router.get("/models/{model_id}/related/{file_type}/download")
def download_related_file(
    model_id: int,
    file_type: str,
    db: Session = Depends(get_db)
):
    """Download related file (tflite or json) for NE301 bin package"""
    model_reg = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not model_reg:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Only for NE301 bin packages
    if (model_reg.format or "").lower() != "bin" or (model_reg.model_type or "").lower() != "ne301":
        raise HTTPException(
            status_code=400,
            detail="This endpoint is only for NE301 bin packages"
        )
    
    is_standalone = model_reg.source == "standalone" or not model_reg.project_id
    
    if file_type == "tflite":
        tflite_reg = None
        
        if is_standalone:
            # For standalone models: find by name pattern
            bin_name = model_reg.name
            if bin_name.endswith("_ne301_bin"):
                base_name = bin_name[:-10]  # Remove "_ne301_bin" suffix
                expected_tflite_name = f"{base_name}_ne301_tflite"
                
                tflite_reg = (
                    db.query(ModelRegistry)
                    .filter(
                        ModelRegistry.name == expected_tflite_name,
                        ModelRegistry.format == "tflite",
                        ModelRegistry.model_type == "ne301",
                        ModelRegistry.source == "standalone",
                        ModelRegistry.project_id.is_(None),
                        ModelRegistry.training_id.is_(None)
                    )
                    .first()
                )
        else:
            # For project-associated models: find from same training
            if not model_reg.training_id or not model_reg.project_id:
                raise HTTPException(status_code=404, detail="Training information not found")
            
            tflite_reg = (
                db.query(ModelRegistry)
                .filter(
                    ModelRegistry.project_id == model_reg.project_id,
                    ModelRegistry.training_id == model_reg.training_id,
                    ModelRegistry.format == "tflite",
                    ModelRegistry.model_type == "ne301"
                )
                .first()
            )
        
        if not tflite_reg:
            raise HTTPException(status_code=404, detail="TFLite file not found")
        
        tflite_path = Path(tflite_reg.model_path)
        if not tflite_path.exists():
            raise HTTPException(status_code=404, detail="TFLite file does not exist")
        
        # Build friendly filename
        if is_standalone:
            # For standalone models, use model name
            filename = f"{tflite_reg.name}.tflite" if not tflite_reg.name.endswith('.tflite') else tflite_reg.name
        else:
            project = db.query(Project).filter(Project.id == tflite_reg.project_id).first() if tflite_reg.project_id else None
            class_names: List[str] = []
            if tflite_reg.class_names:
                try:
                    raw = json.loads(tflite_reg.class_names)
                    if isinstance(raw, list):
                        class_names = [str(x) for x in raw]
                except json.JSONDecodeError:
                    pass
            
            db_record = None
            if tflite_reg.training_id and tflite_reg.project_id:
                db_record = (
                    db.query(TrainingRecord)
                    .filter(
                        TrainingRecord.project_id == tflite_reg.project_id,
                        TrainingRecord.training_id == tflite_reg.training_id,
                    )
                    .first()
                )
            
            base_name = _build_model_basename(
                project or Project(id=tflite_reg.project_id or "", name=tflite_reg.name or ""),
                db_record,
                tflite_reg.project_id or "",
                tflite_reg.training_id,
                class_names,
            )
            filename = f"{base_name}__ne301.tflite"
        
        return FileResponse(
            path=str(tflite_path),
            filename=filename,
            media_type='application/octet-stream'
        )
    
    elif file_type == "json":
        json_file = None
        
        if is_standalone:
            # For standalone models: find in standalone model directory
            # JSON is stored in: standalone_models/{original_pt_model_id}/*.json
            # We need to find the original PT model ID from the BIN model name
            bin_name = model_reg.name
            if bin_name.endswith("_ne301_bin"):
                base_name = bin_name[:-10]  # Remove "_ne301_bin" suffix
                original_model_name = base_name
                
                # Find original PT model to get its ID
                original_pt_model = (
                    db.query(ModelRegistry)
                    .filter(
                        ModelRegistry.name == original_model_name,
                        ModelRegistry.format == "pt",
                        ModelRegistry.source == "standalone",
                        ModelRegistry.project_id.is_(None),
                        ModelRegistry.training_id.is_(None)
                    )
                    .first()
                )
                
                # Use original PT model ID to find JSON file
                original_model_id = original_pt_model.id if original_pt_model else model_id
            else:
                original_model_id = model_id
            
            model_dir = settings.DATASETS_ROOT / "standalone_models" / str(original_model_id)
            if not model_dir.exists():
                raise HTTPException(status_code=404, detail="Standalone model directory not found")
            
            json_files = list(model_dir.glob("*.json"))
            if not json_files:
                raise HTTPException(status_code=404, detail="JSON file not found")
            
            json_file = max(json_files, key=lambda p: p.stat().st_mtime)
            filename = json_file.name
        else:
            # For project-associated models: find from training directory
            if not model_reg.training_id or not model_reg.project_id:
                raise HTTPException(status_code=404, detail="Training information not found")
            
            training_dir = settings.DATASETS_ROOT / model_reg.project_id / f"train_{model_reg.training_id}"
            if not training_dir.exists():
                raise HTTPException(status_code=404, detail="Training directory not found")
            
            json_dir = training_dir / "weights" / "ne301_quant" / "quantized_models"
            if not json_dir.exists():
                raise HTTPException(status_code=404, detail="JSON directory not found")
            
            json_files = list(json_dir.glob("*.json"))
            if not json_files:
                raise HTTPException(status_code=404, detail="JSON file not found")
            
            json_file = max(json_files, key=lambda p: p.stat().st_mtime)
            
            # Build friendly filename
            project = db.query(Project).filter(Project.id == model_reg.project_id).first() if model_reg.project_id else None
            class_names: List[str] = []
            if model_reg.class_names:
                try:
                    raw = json.loads(model_reg.class_names)
                    if isinstance(raw, list):
                        class_names = [str(x) for x in raw]
                except json.JSONDecodeError:
                    pass
            
            db_record = None
            if model_reg.training_id and model_reg.project_id:
                db_record = (
                    db.query(TrainingRecord)
                    .filter(
                        TrainingRecord.project_id == model_reg.project_id,
                        TrainingRecord.training_id == model_reg.training_id,
                    )
                    .first()
                )
            
            base_name = _build_model_basename(
                project or Project(id=model_reg.project_id or "", name=model_reg.name or ""),
                db_record,
                model_reg.project_id or "",
                model_reg.training_id,
                class_names,
            )
            filename = f"{base_name}__ne301.json"
        
        return FileResponse(
            path=str(json_file),
            filename=filename,
            media_type='application/json'
        )
    
    else:
        raise HTTPException(status_code=400, detail="Invalid file_type. Must be 'tflite' or 'json'")


@router.delete("/models/{model_id}")
def delete_model(model_id: int, db: Session = Depends(get_db)):
    """Delete model from ModelRegistry"""
    model_reg = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not model_reg:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Delete model file if exists
    model_path = Path(model_reg.model_path)
    if model_path.exists():
        try:
            model_path.unlink()
            logger.info(f"[ModelRegistry] Deleted model file: {model_path}")
        except Exception as e:
            logger.warning(f"[ModelRegistry] Failed to delete model file {model_path}: {e}")
    
    # Delete database record
    db.delete(model_reg)
    db.commit()
    
    return {"message": "Model deleted successfully"}


async def _test_tflite_model(
    model_path: Path,
    model_reg: ModelRegistry,
    file: UploadFile,
    conf: float,
    iou: float,
    db: Session
):
    """
    Test TFLite model (including quantized models with uint8 input)
    Uses TensorFlow Lite Interpreter for proper uint8/int8 handling
    """
    try:
        import numpy as np
        import tensorflow as tf
        
        # Read uploaded image
        image_bytes = await file.read()
        image = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
        
        # Load TFLite model first to get actual input shape
        interpreter = tf.lite.Interpreter(model_path=str(model_path))
        interpreter.allocate_tensors()
        
        # Get input and output details
        input_details = interpreter.get_input_details()
        output_details = interpreter.get_output_details()
        
        # Validate input and output details
        if not input_details:
            raise ValueError("Model has no input tensors")
        if not output_details:
            raise ValueError("Model has no output tensors")
        
        logger.info(f"[TFLiteTest] Input details: {len(input_details)} inputs")
        logger.info(f"[TFLiteTest] Output details: {len(output_details)} outputs")
        for i, inp in enumerate(input_details):
            logger.info(f"[TFLiteTest] Input {i}: shape={inp['shape']}, dtype={inp['dtype']}, name={inp.get('name', 'N/A')}")
        for i, out in enumerate(output_details):
            logger.info(f"[TFLiteTest] Output {i}: shape={out['shape']}, dtype={out['dtype']}, name={out.get('name', 'N/A')}")
        
        # Check input type and shape
        input_dtype = input_details[0]['dtype']
        input_shape = input_details[0]['shape']
        
        # Get actual input size from model's input shape (prefer model's actual shape over registry)
        # TFLite YOLO models typically have input shape [1, height, width, 3]
        if len(input_shape) >= 4:
            # Extract height and width from shape [batch, height, width, channels]
            # For YOLO models, shape is usually [1, H, W, 3] where H == W (square input)
            height = input_shape[1]
            width = input_shape[2]
            
            # Validate: height and width should be equal for square inputs, and channels should be 3
            if height == width and input_shape[3] == 3:
                input_size = height
                logger.info(f"[TFLiteTest] Extracted input size {input_size} from model shape {input_shape}")
            else:
                # Non-square or unexpected shape, use the larger dimension
                input_size = max(height, width)
                logger.warning(f"[TFLiteTest] Non-square input shape {input_shape}, using max dimension: {input_size}")
        else:
            # Fallback to registry or default
            input_size = model_reg.input_width or model_reg.input_height or 640
            if model_reg.input_width and model_reg.input_height:
                input_size = model_reg.input_width  # Use width (assuming square)
            logger.warning(f"[TFLiteTest] Could not extract input size from shape {input_shape}, using registry/default: {input_size}")
        
        # Resize image to model input size
        image_resized = image.resize((input_size, input_size), PILImage.Resampling.LANCZOS)
        image_array = np.array(image_resized, dtype=np.uint8)
        
        # Prepare input data based on model requirements
        # Handle dynamic shapes (where shape contains -1 or 0)
        actual_input_shape = list(input_shape)
        for i, dim in enumerate(actual_input_shape):
            if dim <= 0:  # Handle -1 (dynamic) or 0
                if i == 1:  # height dimension
                    actual_input_shape[i] = input_size
                elif i == 2:  # width dimension
                    actual_input_shape[i] = input_size
                else:
                    actual_input_shape[i] = 1 if i == 0 else 3 if i == 3 else dim
        
        # Ensure image array matches expected shape [1, H, W, 3]
        if len(image_array.shape) == 3:  # (H, W, 3)
            image_array = np.expand_dims(image_array, axis=0)  # Add batch dimension -> (1, H, W, 3)
        
        # Prepare input data based on model requirements
        if input_dtype == np.uint8:
            # For uint8 input, use image array directly (0-255 range)
            input_data = image_array.reshape(tuple(actual_input_shape)).astype(np.uint8)
        elif input_dtype == np.int8:
            # For int8 input, convert from uint8 to int8 (0-255 -> -128 to 127)
            input_data = (image_array.astype(np.int16) - 128).astype(np.int8).reshape(tuple(actual_input_shape))
        elif input_dtype == np.float32:
            # For float32 input, normalize to 0-1 range
            input_data = (image_array.astype(np.float32) / 255.0).reshape(tuple(actual_input_shape))
        else:
            input_data = image_array.reshape(tuple(actual_input_shape)).astype(input_dtype)
        
        # Set input tensor
        interpreter.set_tensor(input_details[0]['index'], input_data)
        
        # Run inference
        interpreter.invoke()
        
        # Get output (use first output tensor)
        output_detail = output_details[0]
        output_data = interpreter.get_tensor(output_detail['index'])
        
        # Check if output is quantized (int8) and dequantize if needed
        quant_params = output_detail.get('quantization_parameters', {})
        output_scales = quant_params.get('scales', [])
        output_zero_points = quant_params.get('zero_points', [])
        
        # Safely extract scale and zero_point with detailed logging
        logger.info(f"[TFLiteTest] Quantization params: scales={output_scales}, zero_points={output_zero_points}")
        if output_scales and len(output_scales) > 0:
            output_scale = output_scales[0]
        else:
            output_scale = None
            logger.info(f"[TFLiteTest] No output scale available (scales list is empty or missing)")
        
        if output_zero_points and len(output_zero_points) > 0:
            output_zero_point = output_zero_points[0]
        else:
            output_zero_point = None
            logger.info(f"[TFLiteTest] No output zero_point available (zero_points list is empty or missing)")
        
        if output_data.dtype == np.int8 and output_scale is not None:
            # Dequantize int8 output to float32
            output_data = (output_data.astype(np.float32) - (output_zero_point or 0)) * output_scale
            logger.info(f"[TFLiteTest] Dequantized output: scale={output_scale}, zero_point={output_zero_point}")
        elif output_data.dtype == np.uint8 and output_scale is not None:
            # Dequantize uint8 output to float32
            output_data = (output_data.astype(np.float32) - (output_zero_point or 0)) * output_scale
            logger.info(f"[TFLiteTest] Dequantized output: scale={output_scale}, zero_point={output_zero_point}")
        
        # Parse YOLO output format
        # YOLOv8 TFLite output shape is typically: (1, 84, 8400) or (1, num_features, num_boxes)
        # Format: [x_center, y_center, width, height, class_scores...] (no separate objectness)
        # Where 84 = 4 (bbox) + 80 (classes) for COCO, or 4 + num_classes for custom
        output_shape = output_data.shape
        logger.info(f"[TFLiteTest] Output shape: {output_shape}, dtype: {output_data.dtype}")
        logger.info(f"[TFLiteTest] Output min/max: {np.min(output_data)}, {np.max(output_data)}")
        
        # Get class names
        class_names: List[str] = []
        if model_reg.class_names:
            try:
                raw = json.loads(model_reg.class_names)
                if isinstance(raw, list):
                    class_names = [str(x) for x in raw]
            except json.JSONDecodeError:
                pass
        
        num_classes = len(class_names) if class_names else (model_reg.num_classes or 80)
        logger.info(f"[TFLiteTest] Num classes: {num_classes}, class names: {class_names[:5] if class_names else 'N/A'}")
        
        # Parse YOLO detections from output
        detections = []
        
        # YOLOv8 TFLite output is typically (1, 4+num_classes, num_boxes)
        # Transpose to (num_boxes, 4+num_classes) for easier processing
        if len(output_shape) == 3:
            # Shape: (batch, features, boxes) -> (boxes, features)
            output_data = output_data[0].transpose()  # Remove batch, transpose to (boxes, features)
        elif len(output_shape) == 2:
            # Shape: (features, boxes) -> (boxes, features)
            output_data = output_data.transpose()
        
        # Now output_data is (num_boxes, 4+num_classes)
        num_boxes = output_data.shape[0]
        features_per_box = output_data.shape[1]
        
        logger.info(f"[TFLiteTest] Processed shape: {output_data.shape}, num_boxes: {num_boxes}, features_per_box: {features_per_box}")
        
        # Process each detection box
        for i in range(num_boxes):
            box_data = output_data[i]
            
            if features_per_box < 4:
                continue
            
            # Extract box coordinates (normalized 0-1)
            x_center = float(box_data[0])
            y_center = float(box_data[1])
            width = float(box_data[2])
            height = float(box_data[3])
            
            # YOLOv8 format: [x_center, y_center, width, height, class_scores...]
            # No separate objectness score, confidence is max(class_scores)
            if features_per_box >= 4 + num_classes:
                # Get class scores (elements 4 to 4+num_classes)
                class_scores = box_data[4:4+num_classes]
                class_id = int(np.argmax(class_scores))
                confidence = float(class_scores[class_id])
            elif features_per_box > 4:
                # Fallback: use remaining elements as class scores
                class_scores = box_data[4:]
                if len(class_scores) > 0:
                    class_id = int(np.argmax(class_scores))
                    confidence = float(class_scores[class_id])
                else:
                    continue
            else:
                # No class scores, skip
                continue
            
            # Apply confidence threshold
            if confidence < conf:
                continue
            
            # Convert from center format to corner format (normalized coordinates)
            x1_norm = x_center - width / 2
            y1_norm = y_center - height / 2
            x2_norm = x_center + width / 2
            y2_norm = y_center + height / 2
            
            # Clamp to [0, 1]
            x1_norm = max(0.0, min(1.0, x1_norm))
            y1_norm = max(0.0, min(1.0, y1_norm))
            x2_norm = max(0.0, min(1.0, x2_norm))
            y2_norm = max(0.0, min(1.0, y2_norm))
            
            # Scale to original image size
            scale_x = image.width / input_size
            scale_y = image.height / input_size
            x1 = float(x1_norm * input_size * scale_x)
            y1 = float(y1_norm * input_size * scale_y)
            x2 = float(x2_norm * input_size * scale_x)
            y2 = float(y2_norm * input_size * scale_y)
            
            # Ensure valid bbox
            if x2 <= x1 or y2 <= y1:
                continue
            
            cls_name = class_names[class_id] if class_names and class_id < len(class_names) else f"class_{class_id}"
            
            detections.append({
                "class_id": class_id,
                "class_name": cls_name,
                "confidence": confidence,
                "bbox": {
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2
                }
            })
        
        logger.info(f"[TFLiteTest] Parsed {len(detections)} detections before NMS")
        
        # Apply NMS (Non-Maximum Suppression) using IoU threshold
        if len(detections) > 1:
            # Simple NMS implementation
            detections_sorted = sorted(detections, key=lambda d: d['confidence'], reverse=True)
            detections_filtered = []
            used = [False] * len(detections_sorted)
            
            for i, det1 in enumerate(detections_sorted):
                if used[i]:
                    continue
                detections_filtered.append(det1)
                
                for j in range(i + 1, len(detections_sorted)):
                    if used[j]:
                        continue
                    det2 = detections_sorted[j]
                    
                    # Calculate IoU
                    bbox1 = det1['bbox']
                    bbox2 = det2['bbox']
                    
                    # Intersection
                    x1_i = max(bbox1['x1'], bbox2['x1'])
                    y1_i = max(bbox1['y1'], bbox2['y1'])
                    x2_i = min(bbox1['x2'], bbox2['x2'])
                    y2_i = min(bbox1['y2'], bbox2['y2'])
                    
                    if x2_i <= x1_i or y2_i <= y1_i:
                        continue
                    
                    intersection = (x2_i - x1_i) * (y2_i - y1_i)
                    area1 = (bbox1['x2'] - bbox1['x1']) * (bbox1['y2'] - bbox1['y1'])
                    area2 = (bbox2['x2'] - bbox2['x1']) * (bbox2['y2'] - bbox2['y1'])
                    union = area1 + area2 - intersection
                    
                    if union > 0:
                        iou_score = intersection / union
                        if iou_score > iou:
                            used[j] = True
            
            detections = detections_filtered
        
        # Draw detections on image
        from PIL import ImageDraw, ImageFont
        annotated_image = image.copy()
        draw = ImageDraw.Draw(annotated_image)
        
        for det in detections:
            bbox = det['bbox']
            x1, y1, x2, y2 = bbox['x1'], bbox['y1'], bbox['x2'], bbox['y2']
            
            # Draw bounding box
            draw.rectangle([x1, y1, x2, y2], outline='red', width=2)
            
            # Draw label
            label = f"{det['class_name']} {det['confidence']:.2f}"
            draw.text((x1, y1 - 15), label, fill='red')
        
        # Convert to base64
        import base64
        img_buffer = io.BytesIO()
        annotated_image.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        img_base64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')
        
        debug_line = (
            f"[TFLiteTest] model_path={model_path} input_dtype={input_dtype} "
            f"img={image.width}x{image.height} input_size={input_size} "
            f"detections={len(detections)} conf={conf:.3f} iou={iou:.3f}"
        )
        logger.info(debug_line)
        print(debug_line)
        
        return {
            "detections": detections,
            "detection_count": len(detections),
            "annotated_image": f"data:image/png;base64,{img_base64}",
            "image_size": {
                "width": image.width,
                "height": image.height
            }
        }
        
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"TensorFlow Lite library is not installed: {str(e)}"
        )
    except Exception as e:
        logger.error(f"[TFLiteTest] Error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"TFLite model inference failed: {str(e)}"
        )


@router.post("/models/{model_id}/test")
async def test_model_by_id(
    model_id: int,
    file: UploadFile = File(...),
    conf: float = Query(0.25, ge=0.0, le=1.0),
    iou: float = Query(0.45, ge=0.0, le=1.0),
    db: Session = Depends(get_db)
):
    """Test model from ModelRegistry by model_id (supports PT, TFLite, and NE301 bin via tflite)"""
    model_reg = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not model_reg:
        raise HTTPException(status_code=404, detail="Model not found")
    
    model_path = Path(model_reg.model_path)
    if not model_path.exists():
        raise HTTPException(status_code=404, detail="Model file not found")
    
    model_format = (model_reg.format or "pt").lower()
    model_type = (model_reg.model_type or "").lower()
    
    # For NE301 bin packages, find and use the associated ne301.tflite file for testing
    tflite_path = None
    if model_format in ("bin", "ne301_bin") and model_type == "ne301":
        # Find associated NE301 tflite file
        tflite_reg = None
        is_standalone = model_reg.source == "standalone" or not model_reg.project_id
        
        if is_standalone:
            # For standalone models: find by name pattern and source
            # BIN model name format: {name}_ne301_bin
            # TFLite model name format: {name}_ne301_tflite
            bin_name = model_reg.name
            if bin_name.endswith("_ne301_bin"):
                base_name = bin_name[:-10]  # Remove "_ne301_bin" suffix
                expected_tflite_name = f"{base_name}_ne301_tflite"
                
                tflite_reg = (
                    db.query(ModelRegistry)
                    .filter(
                        ModelRegistry.name == expected_tflite_name,
                        ModelRegistry.format == "tflite",
                        ModelRegistry.model_type == "ne301",
                        ModelRegistry.source == "standalone",
                        ModelRegistry.project_id.is_(None),
                        ModelRegistry.training_id.is_(None)
                    )
                    .first()
                )
            
            # Fallback: find any standalone NE301 tflite with matching input size
            if not tflite_reg:
                tflite_reg = (
                    db.query(ModelRegistry)
                    .filter(
                        ModelRegistry.format == "tflite",
                        ModelRegistry.model_type == "ne301",
                        ModelRegistry.source == "standalone",
                        ModelRegistry.project_id.is_(None),
                        ModelRegistry.training_id.is_(None),
                        ModelRegistry.input_width == model_reg.input_width,
                        ModelRegistry.input_height == model_reg.input_height
                    )
                    .order_by(ModelRegistry.created_at.desc())
                    .first()
                )
        elif model_reg.training_id:
            # For models from training: find by project_id and training_id
            tflite_reg = (
                db.query(ModelRegistry)
                .filter(
                    ModelRegistry.project_id == model_reg.project_id,
                    ModelRegistry.training_id == model_reg.training_id,
                    ModelRegistry.format == "tflite",
                    ModelRegistry.model_type == "ne301"
                )
                .first()
            )
        else:
            # For externally imported models with project: find by project_id and name pattern
            # BIN model name format: {name}_ne301_bin
            # TFLite model name format: {name}_ne301_tflite
            bin_name = model_reg.name
            if bin_name.endswith("_ne301_bin"):
                base_name = bin_name[:-10]  # Remove "_ne301_bin" suffix
                expected_tflite_name = f"{base_name}_ne301_tflite"
                
                tflite_reg = (
                    db.query(ModelRegistry)
                    .filter(
                        ModelRegistry.project_id == model_reg.project_id,
                        ModelRegistry.name == expected_tflite_name,
                        ModelRegistry.format == "tflite",
                        ModelRegistry.model_type == "ne301",
                        ModelRegistry.training_id.is_(None)  # Also from import
                    )
                    .first()
                )
            
            # Fallback: find any NE301 tflite in the same project with matching input size
            if not tflite_reg:
                tflite_reg = (
                    db.query(ModelRegistry)
                    .filter(
                        ModelRegistry.project_id == model_reg.project_id,
                        ModelRegistry.format == "tflite",
                        ModelRegistry.model_type == "ne301",
                        ModelRegistry.training_id.is_(None),
                        ModelRegistry.input_width == model_reg.input_width,
                        ModelRegistry.input_height == model_reg.input_height
                    )
                    .order_by(ModelRegistry.created_at.desc())
                    .first()
                )
        
        if not tflite_reg:
            raise HTTPException(
                status_code=404,
                detail="Associated NE301 TFLite file not found. Please ensure quantization was completed."
            )
        
        tflite_path = Path(tflite_reg.model_path)
        if not tflite_path.exists():
            raise HTTPException(
                status_code=404,
                detail="Associated NE301 TFLite file does not exist on disk."
            )
        
        # Use tflite file for testing
        model_path = tflite_path
        model_format = "tflite"
        model_reg = tflite_reg  # Use tflite registry for class names, etc.
        logger.info(f"[Test] Using associated TFLite file for NE301 bin testing: {tflite_path}")
    
    # Handle TFLite models (including NE301 quantized models with uint8 input)
    if model_format == "tflite":
        return await _test_tflite_model(
            model_path=model_path,
            model_reg=model_reg,
            file=file,
            conf=conf,
            iou=iou,
            db=db
        )
    
    try:
        from ultralytics import YOLO
        
        # Read uploaded image
        image_bytes = await file.read()
        image = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
        
        # Load model (YOLO supports PT and TFLite when environment is configured)
        model = YOLO(str(model_path))
        
        # Perform inference
        results = model.predict(
            source=image,
            conf=conf,
            iou=iou,
            save=False,
            verbose=False
        )
        
        # Parse results
        result = results[0]
        detections = []
        
        # Get class names (from model or ModelRegistry)
        names = result.names if hasattr(result, 'names') else {}
        if not names and model_reg.class_names:
            try:
                raw = json.loads(model_reg.class_names)
                if isinstance(raw, list):
                    names = {i: name for i, name in enumerate(raw)}
            except json.JSONDecodeError:
                pass
        
        # Check if there are detection boxes
        if hasattr(result, 'boxes') and result.boxes is not None:
            for box in result.boxes:
                # Get bounding box coordinates
                xyxy = box.xyxy[0].cpu().numpy()
                conf_score = float(box.conf[0].cpu().numpy())
                cls_id = int(box.cls[0].cpu().numpy())
                cls_name = names.get(cls_id, f"class_{cls_id}")
                
                detections.append({
                    "class_id": cls_id,
                    "class_name": cls_name,
                    "confidence": conf_score,
                    "bbox": {
                        "x1": float(xyxy[0]),
                        "y1": float(xyxy[1]),
                        "x2": float(xyxy[2]),
                        "y2": float(xyxy[3])
                    }
                })
        
        # Debug log
        has_boxes = hasattr(result, 'boxes') and result.boxes is not None
        boxes_count = len(result.boxes) if has_boxes else 0
        debug_line = (
            f"[TestPredict] model_id={model_id} format={model_format} "
            f"img={image.width}x{image.height} boxes_count={boxes_count} detections={len(detections)} "
            f"conf={conf:.3f} iou={iou:.3f} names={list(names.values()) if names else 'N/A'}"
        )
        try:
            logger.info(debug_line)
        except Exception:
            pass
        print(debug_line)
        
        # Draw detection results on image
        annotated_bgr = result.plot()
        annotated_rgb = annotated_bgr[..., ::-1]  # BGR -> RGB
        annotated_pil = PILImage.fromarray(annotated_rgb)
        
        # Convert to base64
        import base64
        img_buffer = io.BytesIO()
        annotated_pil.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        img_base64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')
        
        return {
            "detections": detections,
            "detection_count": len(detections),
            "annotated_image": f"data:image/png;base64,{img_base64}",
            "image_size": {
                "width": image.width,
                "height": image.height
            }
        }
        
    except ImportError:
        raise HTTPException(status_code=500, detail="Ultralytics library is not installed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Model inference failed: {str(e)}")


# ========== MQTT Service Management ==========
@router.get("/mqtt/status")
def get_mqtt_status(request: Request):
    """Get MQTT configuration and runtime status"""
    from backend.config import get_mqtt_broker_host

    cfg: MQTTConfig = mqtt_config_service.load_config()

    # Server IP (from request headers when possible, for device bootstrap)
    server_ip = get_mqtt_broker_host(request)
    
    # Detailed status from MQTTService
    service_status = {}
    if cfg.enabled:
        try:
            service_status = mqtt_service.get_status()
        except Exception as e:
            logger.warning(f"Failed to get MQTT service status: {e}")
    
    # Extract per-broker runtime info (if available)
    brokers_info = service_status.get("brokers") or []
    builtin_connected = any(
        b.get("type") == "builtin" and b.get("connected") for b in brokers_info
    )
    external_connected = any(
        b.get("type") == "external" and b.get("connected") for b in brokers_info
    )
    
    # If no broker info available but service is enabled, check if builtin broker is running
    # Also check if broker is running even if we have broker info (connection might be in progress)
    if cfg.enabled:
        try:
            from backend.services.mqtt_broker import builtin_mqtt_broker
            # If builtin broker is running, consider it connected or at least available
            # (client connection is async and may take time after restart)
            if builtin_mqtt_broker.is_running:
                # If we have broker info, use it; otherwise assume connected
                # Don't override actual connection status - if broker info shows disconnected,
                # respect that (connection may have failed due to auth issues, etc.)
                # Only use broker running status as fallback when we have no broker info at all
                if not brokers_info:
                    builtin_connected = True
                # If we have broker info, use the actual connection status from it
        except Exception:
            pass

    # Built-in broker status (always available when MQTT is enabled)
    builtin_port = (
        (cfg.builtin_tls_port if cfg.builtin_protocol == "mqtts" else cfg.builtin_tcp_port)
        or settings.MQTT_BUILTIN_PORT
    )
    builtin_status = {
        "enabled": bool(cfg.enabled),
        "host": server_ip,
        "port": builtin_port,
        "protocol": cfg.builtin_protocol,
        "connected": bool(cfg.enabled and builtin_connected),
    }

    # External broker status (optional, only when configured)
    external_status = {
        "enabled": bool(cfg.enabled and cfg.external_enabled),
        "configured": bool(cfg.external_host or cfg.external_port),
        "host": cfg.external_host,
        "port": cfg.external_port,
        "protocol": cfg.external_protocol,
        "connected": bool(cfg.enabled and cfg.external_enabled and external_connected),
    }
    
    base_status = {
        "enabled": cfg.enabled,
        "connected": mqtt_service.is_connected if cfg.enabled else False,
        "broker": f"{mqtt_service.broker_host}:{mqtt_service.broker_port}"
        if cfg.enabled and mqtt_service.broker_host
        else None,
        "upload_topic": settings.MQTT_UPLOAD_TOPIC if cfg.enabled else None,
        "response_topic_prefix": settings.MQTT_RESPONSE_TOPIC_PREFIX if cfg.enabled else None,
        "server_ip": server_ip,
        "server_port": settings.PORT,
        # New: per-broker status for UI
        "builtin": builtin_status,
        "external": external_status,
    }
    
    # Merge service status details
    if service_status:
        base_status.update(
            {
            "connection_count": service_status.get("connection_count", 0),
            "disconnection_count": service_status.get("disconnection_count", 0),
            "message_count": service_status.get("message_count", 0),
            "last_connect_time": service_status.get("last_connect_time"),
            "last_disconnect_time": service_status.get("last_disconnect_time"),
            "last_message_time": service_status.get("last_message_time"),
                "recent_errors": service_status.get("recent_errors", []),
            }
        )
    
    return base_status


@router.get("/system/mqtt/config", response_model=MQTTConfig)
def get_mqtt_config():
    """Get current MQTT configuration (for system settings UI)."""
    return mqtt_config_service.load_config()


@router.put("/system/mqtt/config", response_model=MQTTConfig)
def update_mqtt_config(update: MQTTConfigUpdate):
    """
    Update MQTT configuration and apply it at runtime.
    
    Design Philosophy:
    - This endpoint manages Broker configuration (Mosquitto settings)
    - Client connection (AIToolStack) automatically infers settings from broker config
    - Changes to broker config (protocol, TLS, auth) automatically affect client connection
    """
    current = mqtt_config_service.load_config()
    # Use model_dump for Pydantic V2 compatibility
    if hasattr(current, 'model_dump'):
        data = current.model_dump()
    else:
        data = current.dict()

    # Apply partial updates - handle both MQTTConfigUpdate and full MQTTConfig objects
    # Use model_dump for Pydantic V2 compatibility
    if hasattr(update, 'model_dump'):
        update_dict = update.model_dump(exclude_unset=True, exclude_none=False)
        # Get valid fields from MQTTConfigUpdate model (Pydantic V2 compatible)
        valid_fields = set(MQTTConfigUpdate.model_fields.keys())
    else:
        update_dict = update.dict(exclude_unset=True, exclude_none=False)
        # Get valid fields from MQTTConfigUpdate model (Pydantic V1 compatible)
        valid_fields = set(MQTTConfigUpdate.__fields__.keys())
    
    for field, value in update_dict.items():
        # Only update fields that are in MQTTConfigUpdate
        if field in valid_fields:
            # Handle empty strings as None for optional string fields
            if isinstance(value, str) and value == "":
                # Convert empty string to None for optional fields
                data[field] = None
            else:
                data[field] = value

    new_cfg = MQTTConfig(**data)

    # Basic validation and normalization
    if new_cfg.builtin_protocol not in ("mqtt", "mqtts"):
        raise HTTPException(status_code=400, detail="Invalid builtin_protocol, must be 'mqtt' or 'mqtts'")
    if new_cfg.external_protocol not in ("mqtt", "mqtts"):
        raise HTTPException(status_code=400, detail="Invalid external_protocol, must be 'mqtt' or 'mqtts'")
    
    # SECURITY VALIDATION: Prevent insecure configuration
    # When allow_anonymous=true, builtin_tls_enabled=true, and builtin_tls_require_client_cert=false,
    # clients with wrong CA certificates can still connect. This is a security vulnerability.
    # We must either:
    # 1. Force require_certificate=true (but this prevents anonymous connections without certificates)
    # 2. Reject this configuration and require user to enable mTLS or disable anonymous access
    # We choose option 1: Force require_certificate=true when allow_anonymous=true and TLS is enabled
    # This means anonymous connections without certificates will be rejected (security trade-off)
    if new_cfg.builtin_allow_anonymous and new_cfg.builtin_tls_enabled and not new_cfg.builtin_tls_require_client_cert:
        logger.warning(
            "SECURITY: Detected insecure configuration: allow_anonymous=true, TLS enabled, but mTLS disabled. "
            "This allows clients with wrong CA certificates to connect. "
            "Forcing require_certificate=true to enforce CA validation. "
            "Note: Anonymous connections without certificates will be rejected."
        )
    
    # Note: When anonymous is disabled, FileAuthPlugin will be enabled.
    # If username/password are not provided, an invalid user entry will be created
    # to ensure all connections are rejected until valid credentials are configured.
    # This is handled in mqtt_broker.py, so we don't need to enforce it here.

    # Persist configuration
    saved = mqtt_config_service.save_config(new_cfg)

    # When using external Mosquitto instead of Python built-in broker,
    # enforce username/password requirement when anonymous access is disabled,
    # and synchronize Mosquitto passwordfile & allow_anonymous so that clients can authenticate.
    from backend.config import settings as app_settings
    if not app_settings.MQTT_USE_BUILTIN_BROKER:
        # If anonymous is disabled, username/password must be provided
        if not saved.builtin_allow_anonymous:
            if not saved.builtin_username or not saved.builtin_password:
                raise HTTPException(
                    status_code=400,
                    detail="When disabling anonymous access, builtin_username and builtin_password are required.",
                )

            # Try to update Mosquitto passwordfile via mosquitto_passwd.
            # This requires:
            # - camthink service mounting ./mosquitto/config:/mosquitto/config
            # - mosquitto-clients installed in the runtime image
            try:
                import subprocess
                from pathlib import Path

                passwordfile = Path("/mosquitto/config/passwordfile")
                # Ensure passwordfile exists so mosquitto can start even before any user is created
                if not passwordfile.exists():
                    passwordfile.parent.mkdir(parents=True, exist_ok=True)
                    passwordfile.touch()
                    # Set secure permissions (read/write for owner only)
                    os.chmod(passwordfile, 0o600)

                # Use mosquitto_passwd to create or update the user.
                # Do NOT use -c here to avoid wiping other possible users.
                # Note: If user already exists, mosquitto_passwd -b will update the password
                cmd = [
                    "mosquitto_passwd",
                    "-b",
                    str(passwordfile),
                    saved.builtin_username,
                    saved.builtin_password,
                ]
                result = subprocess.run(cmd, check=True, capture_output=True, text=True)
                # Ensure secure permissions after updating password file
                os.chmod(passwordfile, 0o600)
                logger.info(f"Successfully updated Mosquitto passwordfile: user '{saved.builtin_username}' added/updated")
                
                # Verify password file was updated correctly
                if passwordfile.exists():
                    with open(passwordfile, 'r', encoding='utf-8') as f:
                        users = [line.split(':')[0] for line in f if line.strip() and ':' in line]
                        if saved.builtin_username in users:
                            logger.info(f"Verified: user '{saved.builtin_username}' exists in password file")
                        else:
                            logger.error(f"WARNING: user '{saved.builtin_username}' not found in password file after update!")
                
                # Restart Mosquitto container to ensure password file is reloaded
                # Note: Some versions of Mosquitto may not properly reload password file on SIGHUP
                # Restarting the container is more reliable for ensuring password file changes take effect
                try:
                    result = subprocess.run(
                        ["docker", "restart", "camthink-mosquitto"],
                        check=True,
                        timeout=10,
                        capture_output=True,
                        text=True,
                    )
                    logger.info("Restarted Mosquitto container to reload password file")
                    # Wait a moment for Mosquitto to fully restart
                    import time
                    time.sleep(2)
                except Exception as e:
                    logger.warning(f"Failed to restart Mosquitto container after password file update: {e}")
                    # If restart fails, try SIGHUP as fallback
                    try:
                        subprocess.run(
                            ["docker", "kill", "-s", "HUP", "camthink-mosquitto"],
                            check=True,
                            timeout=5,
                            capture_output=True,
                            text=True,
                        )
                        logger.info("Sent SIGHUP to Mosquitto as fallback (restart failed)")
                    except Exception as sighup_e:
                        logger.error(f"Both restart and SIGHUP failed: {e}, {sighup_e}")
                        # The password file is updated, and Mosquitto will use it on next restart
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to update Mosquitto passwordfile: {e.stderr if hasattr(e, 'stderr') and e.stderr else str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to update Mosquitto password file: {e.stderr if hasattr(e, 'stderr') and e.stderr else str(e)}"
                )
            except Exception as e:
                logger.error(f"Failed to update Mosquitto passwordfile: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to update Mosquitto password file: {str(e)}"
                )

        # Broker Configuration Management: Update Mosquitto broker settings
        # - allow_anonymous: Controls whether external devices can connect anonymously
        # - require_certificate: For MQTTS, enforces client certificate verification (mTLS)
        # Note: Client connection (AIToolStack) automatically matches these broker settings
        mosquitto_restarted = False  # Track if Mosquitto was restarted (for client reconnection logic)
        try:
            from pathlib import Path
            import re
            import subprocess

            conf_path = Path("/mosquitto/config/mosquitto.conf")
            if conf_path.exists():
                conf_text = conf_path.read_text()
                desired = "true" if saved.builtin_allow_anonymous else "false"
                
                # Check current allow_anonymous value in config file
                current_allow_anonymous = None
                allow_anonymous_match = re.search(r"^allow_anonymous\s+(true|false)\s*$", conf_text, flags=re.MULTILINE)
                if allow_anonymous_match:
                    current_allow_anonymous = allow_anonymous_match.group(1)
                
                # Only update if value actually changed
                allow_anonymous_changed = current_allow_anonymous != desired
                
                if allow_anonymous_changed:
                    # Replace or insert allow_anonymous line
                    if allow_anonymous_match:
                        new_text = re.sub(
                            r"^allow_anonymous\s+(true|false)\s*$",
                            f"allow_anonymous {desired}",
                            conf_text,
                            flags=re.MULTILINE,
                        )
                    else:
                        # If no line exists, append one after listener block or at end of file
                        new_text = conf_text.rstrip() + f"\nallow_anonymous {desired}\n"
                else:
                    # No change needed, keep original text
                    new_text = conf_text

                # Broker Configuration: Update require_certificate based on TLS enabled status and user configuration
                # When builtin_tls_enabled is True, set require_certificate based on builtin_tls_require_client_cert:
                # 
                # Mode 1: One-way TLS (builtin_tls_require_client_cert = False)
                #   - require_certificate = false
                #   - Clients can connect WITHOUT certificates
                #   - Authentication: 
                #     * If allow_anonymous=true: Clients can connect anonymously (no username/password needed)
                #     * If allow_anonymous=false: Clients must provide username/password 
                #   - Clients can OPTIONALLY provide certificates
                #   - If clients provide certificates, Mosquitto validates them against cafile
                #   - BUT: If certificate validation fails (wrong CA), connection is NOT rejected
                #   - Instead, client can authenticate using username/password (if allow_anonymous=false)
                #     or connect anonymously (if allow_anonymous=true)
                #   - This is the standard one-way TLS behavior (server cert verification only)
                #   - Client requirements: CA certificate (to verify server cert)
                #   - Client does NOT need: client certificate, client key
                #   - NOTE: This mode allows anonymous TLS connections when allow_anonymous=true
                #
                # Mode 2: mTLS / Two-way TLS (builtin_tls_require_client_cert = True)
                #   - require_certificate = true
                #   - Clients MUST provide certificates
                #   - Certificates MUST be signed by the CA specified in cafile
                #   - If certificate validation fails (wrong CA), connection is REJECTED
                #   - use_identity_as_username = true (CN from cert is used as username)
                #   - Only CNs in password file can connect
                #   - This enforces strict certificate-based authentication
                #   - Client requirements: CA certificate, client certificate, client key
                #
                # Note: builtin_protocol only affects which port clients connect to by default,
                # not the broker's TLS configuration (require_certificate is independent of protocol)
                if saved.builtin_tls_enabled:
                    # Find the listener 8883 block and update require_certificate
                    # Pattern: match from "listener 8883" to the end of that listener block (before next listener or end of file)
                    # Split into lines for processing
                    lines = new_text.split('\n')
                    listener_8883_start = -1
                    listener_8883_end = -1
                    
                    # Find listener 8883 block
                    for i, line in enumerate(lines):
                        stripped = line.strip()
                        if not stripped or stripped.startswith('#'):
                            continue
                        if re.match(r'^listener\s+8883', stripped):
                            listener_8883_start = i
                        elif listener_8883_start >= 0:
                            # Check if this is the start of a new listener block or a global config directive
                            # Listener blocks end when we encounter another listener or a global config (like persistence, log_dest, etc.)
                            if re.match(r'^listener\s+', stripped):
                                # Another listener block starts
                                listener_8883_end = i
                                break
                            elif re.match(r'^(persistence|log_dest|log_timestamp|password_file|allow_anonymous)', stripped):
                                # Global config directive (not part of listener block)
                                listener_8883_end = i
                                break
                    
                    if listener_8883_start >= 0:
                        if listener_8883_end < 0:
                            listener_8883_end = len(lines)
                        
                        logger.debug(f"Found listener 8883 block: start={listener_8883_start}, end={listener_8883_end}")
                        
                        # SECURITY FIX: When allow_anonymous=true and TLS is enabled, we must enforce CA validation
                        # If require_certificate=false, clients with wrong CA certificates can still connect
                        # This is a security vulnerability. We need to force require_certificate=true when
                        # allow_anonymous=true to ensure only clients with valid CA-signed certificates can connect.
                        # However, we still allow clients without certificates if allow_anonymous=true and
                        # require_certificate=false (they can connect anonymously).
                        # 
                        # The solution: When allow_anonymous=true and builtin_tls_require_client_cert=false,
                        # we still set require_certificate=true but use a special configuration:
                        # - require_certificate=true: Forces CA validation for any provided certificates
                        # - use_identity_as_username=true: Uses CN from certificate as username
                        # - But we need to allow anonymous connections too, so we can't use use_identity_as_username
                        #
                        # Actually, Mosquitto doesn't support this directly. The best we can do is:
                        # - If allow_anonymous=true and builtin_tls_require_client_cert=false:
                        #   * Set require_certificate=true (force CA validation)
                        #   * Set use_identity_as_username=true (use CN as username)
                        #   * This means clients MUST provide certificates signed by our CA
                        #   * Anonymous connections are NOT allowed in this case (security trade-off)
                        #
                        # Alternative: Warn user that allow_anonymous=true with require_certificate=false is insecure
                        # and recommend enabling mTLS or disabling anonymous access.
                        
                        # Determine require_certificate value based on user configuration
                        # SECURITY FIX: When allow_anonymous=true and mTLS is disabled, we must enforce CA validation
                        # to prevent clients with wrong CA certificates from connecting.
                        # We force require_certificate=true in this case, which means:
                        # - Clients MUST provide certificates signed by our CA
                        # - Anonymous connections without certificates will be rejected (security trade-off)
                        # - This is necessary because Mosquitto doesn't validate CA for optional certificates
                        #   when require_certificate=false
                        if saved.builtin_allow_anonymous and not saved.builtin_tls_require_client_cert:
                            # Force require_certificate=true to enforce CA validation
                            require_cert_value = "true"
                            logger.warning(
                                f"SECURITY: allow_anonymous=true with mTLS disabled is insecure. "
                                f"Force setting require_certificate=true to enforce CA validation. "
                                f"Clients must provide certificates signed by our CA. "
                                f"Anonymous connections without certificates will be rejected."
                            )
                        else:
                            require_cert_value = "true" if saved.builtin_tls_require_client_cert else "false"
                        logger.info(f"Setting require_certificate to {require_cert_value} (builtin_tls_require_client_cert={saved.builtin_tls_require_client_cert}, allow_anonymous={saved.builtin_allow_anonymous})")
                        
                        # Check if require_certificate already exists in this block
                        require_cert_found = False
                        for i in range(listener_8883_start, listener_8883_end):
                            line_stripped = lines[i].strip()
                            # Match require_certificate line (with or without leading whitespace)
                            if re.match(r'^\s*require_certificate\s+', lines[i]) or line_stripped.startswith('require_certificate'):
                                # Replace existing require_certificate line
                                lines[i] = re.sub(
                                    r'^\s*require_certificate\s+.*',
                                    f'require_certificate {require_cert_value}',
                                    lines[i]
                                )
                                require_cert_found = True
                                logger.info(f"Updated existing require_certificate line at line {i+1} to {require_cert_value}")
                                break
                        
                        if not require_cert_found:
                            # Insert require_certificate after keyfile line (last TLS config line)
                            insert_pos = listener_8883_end
                            # Try to find keyfile line (usually the last TLS config line)
                            for i in range(listener_8883_start, listener_8883_end):
                                if re.match(r'^\s*keyfile\s+', lines[i]):
                                    insert_pos = i + 1
                                    break
                            lines.insert(insert_pos, f'require_certificate {require_cert_value}')
                            logger.info(f"Inserted require_certificate={require_cert_value} at line {insert_pos+1}")
                        
                        # If mTLS is enabled OR if allow_anonymous=true with mTLS disabled (security fix),
                        # add use_identity_as_username to enforce CN validation
                        # This ensures only certificates with CN matching a user in password file can connect
                        # SECURITY: When allow_anonymous=true and mTLS is disabled, we force require_certificate=true
                        # and use_identity_as_username=true to enforce CA validation
                        if saved.builtin_tls_require_client_cert or (saved.builtin_allow_anonymous and not saved.builtin_tls_require_client_cert):
                            # Find existing use_identity_as_username line
                            use_identity_found = False
                            use_identity_line_index = -1
                            for i in range(listener_8883_start, listener_8883_end):
                                if re.match(r'^\s*use_identity_as_username\s+', lines[i]):
                                    use_identity_line_index = i
                                    use_identity_found = True
                                    break
                            
                            if not use_identity_found:
                                # Insert use_identity_as_username after require_certificate
                                insert_pos = listener_8883_end
                                for i in range(listener_8883_start, listener_8883_end):
                                    if re.match(r'^\s*require_certificate\s+', lines[i]):
                                        insert_pos = i + 1
                                        break
                                lines.insert(insert_pos, 'use_identity_as_username true')
                                logger.info(f"Inserted use_identity_as_username=true at line {insert_pos+1} (enforces CN validation)")
                            else:
                                # Ensure it's set to true
                                lines[use_identity_line_index] = re.sub(
                                    r'^\s*use_identity_as_username\s+.*',
                                    'use_identity_as_username true',
                                    lines[use_identity_line_index]
                                )
                                logger.info(f"Updated use_identity_as_username=true at line {use_identity_line_index+1}")
                            
                            # SECURITY: When allow_anonymous=true and mTLS is disabled, we force require_certificate=true
                            # to enforce CA validation. This means clients MUST provide certificates signed by our CA.
                            # We need to sync all device certificate CNs to password file so they can connect.
                            if saved.builtin_allow_anonymous and not saved.builtin_tls_require_client_cert:
                                # Sync all device certificate CNs to password file
                                # This ensures any client certificate signed by our CA can connect
                                try:
                                    certs_dir = Path("/mosquitto/config/certs")
                                    if certs_dir.exists():
                                        import re
                                        pattern = re.compile(r'^client-(.+)\.crt$')
                                        synced_count = 0
                                        for cert_file in certs_dir.glob("client-*.crt"):
                                            match = pattern.match(cert_file.name)
                                            if match:
                                                cn = match.group(1)
                                                try:
                                                    _add_device_to_password_file(cn)
                                                    synced_count += 1
                                                except Exception as e:
                                                    logger.warning(f"Failed to add device CN '{cn}' to password file: {e}")
                                        if synced_count > 0:
                                            logger.info(f"Synced {synced_count} device certificate CN(s) to password file for CA validation enforcement")
                                except Exception as e:
                                    logger.warning(f"Failed to sync device certificates to password file: {e}")
                            
                            # Manage crlfile configuration based on CRL file validity
                            # Only add crlfile if CRL file exists and is valid (non-empty and properly formatted)
                            crl_file = Path("/mosquitto/config/certs/revoked.crl")
                            crl_is_valid = _is_valid_crl_file(crl_file)
                            
                            # Find existing crlfile line
                            crlfile_line_index = -1
                            for i in range(listener_8883_start, listener_8883_end):
                                if re.match(r'^\s*crlfile\s+', lines[i]):
                                    crlfile_line_index = i
                                    break
                            
                            if crl_is_valid:
                                # CRL file is valid, ensure crlfile is configured
                                if crlfile_line_index >= 0:
                                    # Update existing crlfile line
                                    lines[crlfile_line_index] = re.sub(
                                        r'^\s*crlfile\s+.*',
                                        f'crlfile {crl_file}',
                                        lines[crlfile_line_index]
                                    )
                                    logger.info(f"Updated crlfile line at line {crlfile_line_index+1}")
                                else:
                                    # Insert crlfile after use_identity_as_username or require_certificate
                                    insert_pos = listener_8883_end
                                    for i in range(listener_8883_start, listener_8883_end):
                                        if re.match(r'^\s*use_identity_as_username\s+', lines[i]):
                                            insert_pos = i + 1
                                            break
                                        elif re.match(r'^\s*require_certificate\s+', lines[i]):
                                            insert_pos = i + 1
                                    lines.insert(insert_pos, f'crlfile {crl_file}')
                                    logger.info(f"Inserted crlfile at line {insert_pos+1}")
                            else:
                                # CRL file is invalid or doesn't exist, remove crlfile configuration if present
                                if crlfile_line_index >= 0:
                                    lines.pop(crlfile_line_index)
                                    logger.info("Removed crlfile configuration (CRL file is invalid or empty)")
                        else:
                            # If mTLS is disabled AND allow_anonymous=false, remove use_identity_as_username
                            # to allow username/password authentication
                            # When use_identity_as_username is true, Mosquitto expects username from certificate CN
                            # If client doesn't provide certificate, connection will be rejected
                            # So we must remove it when mTLS is disabled and allow_anonymous=false to allow username/password connections
                            
                            # Find existing use_identity_as_username line
                            use_identity_line_index = -1
                            for i in range(listener_8883_start, listener_8883_end):
                                if re.match(r'^\s*use_identity_as_username\s+', lines[i]):
                                    use_identity_line_index = i
                                    break
                            
                            if use_identity_line_index >= 0:
                                # Remove use_identity_as_username when mTLS is disabled and allow_anonymous=false
                                lines.pop(use_identity_line_index)
                                logger.info(f"Removed use_identity_as_username at line {use_identity_line_index+1} (mTLS disabled and allow_anonymous=false, allowing username/password auth)")
                            
                            # IMPORTANT: When mTLS is disabled (one-way TLS) and allow_anonymous=false:
                            # - require_certificate = false
                            # - Clients can connect WITHOUT certificates using username/password
                            # - Clients can OPTIONALLY provide certificates
                            # - If clients provide certificates, Mosquitto validates them against cafile
                            # - However, if certificate validation fails (wrong CA), Mosquitto will NOT reject the connection
                            #   Instead, it will allow the client to authenticate using username/password
                            # - This is the expected behavior for one-way TLS (server cert verification only)
                            # - Note: We cannot use use_identity_as_username=true here because it would prevent
                            #   clients without certificates from connecting (they need username/password auth)
                            #
                            # SECURITY NOTE: When allow_anonymous=true and mTLS is disabled, we force require_certificate=true
                            # and use_identity_as_username=true to prevent clients with wrong CA certificates from connecting.
                            # This means anonymous connections without certificates are NOT allowed in this case (security trade-off).
                        
                        new_text = '\n'.join(lines)
                        
                        # After updating require_certificate, manage crlfile based on CRL file validity
                        # This ensures that if CRL file is invalid, crlfile is removed from config
                        # Only write if require_certificate actually changed (not just format differences)
                        # Compare normalized content (ignore whitespace differences)
                        if new_text.strip() != conf_text.strip():
                            conf_path.write_text(new_text)
                            # Re-read to get the updated content for crlfile management
                            conf_text = conf_path.read_text()
                            lines = conf_text.split('\n')
                            # Re-find listener 8883 block after write
                            listener_8883_start = -1
                            listener_8883_end = -1
                            for i, line in enumerate(lines):
                                stripped = line.strip()
                                if not stripped or stripped.startswith('#'):
                                    continue
                                if re.match(r'^listener\s+8883', stripped):
                                    listener_8883_start = i
                                elif listener_8883_start >= 0:
                                    if re.match(r'^listener\s+', stripped):
                                        listener_8883_end = i
                                        break
                                    elif re.match(r'^(persistence|log_dest|log_timestamp|password_file|allow_anonymous)', stripped):
                                        listener_8883_end = i
                                        break
                            if listener_8883_end < 0:
                                listener_8883_end = len(lines)
                        
                        # Manage crlfile configuration based on CRL file validity
                        if saved.builtin_tls_require_client_cert:
                            _ensure_crlfile_in_mosquitto_conf()
                        tls_mode = "mTLS (client cert required)" if saved.builtin_tls_require_client_cert else "one-way TLS (server cert only)"
                        logger.info(f"Updated require_certificate={require_cert_value} for listener 8883 ({tls_mode})")
                    else:
                        logger.warning("listener 8883 block not found in mosquitto.conf, cannot set require_certificate")
                else:
                    # If TLS is disabled, set require_certificate to false for listener 8883 (if it exists)
                    # This ensures that if TLS is disabled, mTLS is also disabled
                    if current.builtin_tls_enabled and not saved.builtin_tls_enabled:
                        # TLS was disabled, set require_certificate to false for listener 8883
                        lines = new_text.split('\n')
                        in_listener_8883 = False
                        listener_8883_start = -1
                        listener_8883_end = -1
                        
                        for i, line in enumerate(lines):
                            if re.match(r'^\s*listener\s+8883', line):
                                in_listener_8883 = True
                                listener_8883_start = i
                            elif in_listener_8883 and (re.match(r'^\s*listener\s+', line) or re.match(r'^\s*[a-zA-Z_]', line) and not line.strip().startswith('#')):
                                listener_8883_end = i
                                break
                        
                        if listener_8883_start >= 0:
                            if listener_8883_end < 0:
                                listener_8883_end = len(lines)
                            
                            for i in range(listener_8883_start, listener_8883_end):
                                if re.match(r'^\s*require_certificate\s+', lines[i]):
                                    lines[i] = 'require_certificate false'
                                    break
                            
                            new_text = '\n'.join(lines)

                # Only consider config changed if content actually differs (ignoring whitespace)
                # This prevents unnecessary SIGHUP/restart when only format changes
                config_changed = new_text.strip() != conf_text.strip()
                
                # Check if require_certificate actually changed by reading current value from config
                require_cert_changed = False
                if saved.builtin_tls_enabled:
                    # Read current require_certificate value from config file
                    current_require_cert = None
                    if conf_path.exists():
                        current_conf_lines = conf_path.read_text().split('\n')
                        in_listener_8883 = False
                        for line in current_conf_lines:
                            stripped = line.strip()
                            if re.match(r'^listener\s+8883', stripped):
                                in_listener_8883 = True
                            elif in_listener_8883:
                                if re.match(r'^\s*require_certificate\s+', line):
                                    current_require_cert = stripped.split()[1] if len(stripped.split()) > 1 else None
                                    break
                                elif re.match(r'^(listener|persistence|log_dest|log_timestamp|password_file|allow_anonymous)', stripped):
                                    break
                    
                    # Compare with new value
                    new_require_cert = "true" if saved.builtin_tls_require_client_cert else "false"
                    if current_require_cert != new_require_cert:
                        require_cert_changed = True
                        logger.info(f"require_certificate changed: {current_require_cert} -> {new_require_cert}")
                
                # Also check if TLS enabled status changed (affects require_certificate)
                tls_enabled_changed = current.builtin_tls_enabled != saved.builtin_tls_enabled
                mtls_status_changed = current.builtin_tls_require_client_cert != saved.builtin_tls_require_client_cert
                
                if config_changed:
                    conf_path.write_text(new_text)
                    require_cert_str = f"{saved.builtin_tls_require_client_cert}" if saved.builtin_tls_enabled else 'false'
                    logger.info(f"Updated mosquitto.conf: allow_anonymous={desired}, require_certificate={require_cert_str}")
                    
                    # CRITICAL SECURITY CHECK: Verify that require_certificate was correctly set
                    # Read back the config to ensure it was written correctly
                    verify_conf = conf_path.read_text()
                    if saved.builtin_tls_enabled and saved.builtin_tls_require_client_cert:
                        # When mTLS is enabled, require_certificate MUST be true
                        # Check if require_certificate true exists in listener 8883 block
                        in_listener_8883 = False
                        found_require_cert_true = False
                        for line in verify_conf.split('\n'):
                            stripped = line.strip()
                            if re.match(r'^listener\s+8883', stripped):
                                in_listener_8883 = True
                            elif in_listener_8883:
                                if re.match(r'^\s*require_certificate\s+true', stripped):
                                    found_require_cert_true = True
                                    break
                                elif re.match(r'^(listener|persistence|log_dest|log_timestamp|password_file|allow_anonymous)', stripped):
                                    break
                        
                        if not found_require_cert_true:
                            logger.error("CRITICAL: require_certificate was not set to true when mTLS is enabled!")
                            raise RuntimeError("Failed to set require_certificate=true in mosquitto.conf. This is a security issue.")
                        logger.info("✓ Verified: require_certificate=true is correctly set in mosquitto.conf")
                
                # After writing config, manage crlfile based on CRL file validity
                # This ensures that if CRL file is invalid, crlfile is removed from config
                if saved.builtin_tls_enabled and saved.builtin_tls_require_client_cert:
                    _ensure_crlfile_in_mosquitto_conf()
                    
                    # CRITICAL: When mTLS is enabled, ensure all existing device certificates are in password file
                    # This is required because use_identity_as_username=true means only users in password file can connect
                    # Always sync device certificates to password file when mTLS is enabled (not just on status change)
                    # This ensures that even if certificates were generated before mTLS was enabled, they are still added
                    logger.info("mTLS is enabled, ensuring all existing device certificates are in password file...")
                    try:
                        # Get all device certificates
                        certs_dir = Path("/mosquitto/config/certs")
                        if certs_dir.exists():
                            import re
                            pattern = re.compile(r'^client-(.+)\.crt$')
                            device_certs = []
                            for cert_file in certs_dir.glob("client-*.crt"):
                                match = pattern.match(cert_file.name)
                                if match:
                                    cn = match.group(1)
                                    device_certs.append(cn)
                            
                            # Add each device CN to password file
                            added_count = 0
                            for cn in device_certs:
                                try:
                                    _add_device_to_password_file(cn)
                                    added_count += 1
                                    logger.info(f"Added device certificate CN '{cn}' to password file")
                                except Exception as e:
                                    logger.warning(f"Failed to add device CN '{cn}' to password file: {e}")
                            
                            if device_certs:
                                logger.info(f"Synced {added_count}/{len(device_certs)} device certificate(s) to password file")
                            else:
                                logger.info("No device certificates found to sync to password file")
                    except Exception as e:
                        logger.warning(f"Failed to sync device certificates to password file: {e}. Some devices may not be able to connect.")
                
                # CRITICAL: require_certificate cannot be reloaded via SIGHUP, must restart container
                # Only restart if require_certificate actually changed or TLS/mTLS status changed
                # allow_anonymous can be reloaded via SIGHUP without restart
                need_restart = (
                    require_cert_changed or
                    tls_enabled_changed or
                    (saved.builtin_tls_enabled and mtls_status_changed)
                )
                
                if need_restart:
                    try:
                        logger.info("Restarting Mosquitto container to apply require_certificate changes...")
                        subprocess.run(
                            ["docker", "restart", "camthink-mosquitto"],
                            check=True,
                            timeout=30,
                        )
                        logger.info("Mosquitto container restarted successfully")
                        mosquitto_restarted = True
                    except subprocess.TimeoutExpired:
                        logger.error("Mosquitto container restart timed out")
                    except Exception as e:
                        # If restart fails (e.g. docker CLI or permissions), log warning but don't block API.
                        logger.warning(f"Failed to restart Mosquitto container: {e}")
                        logger.warning("Please manually restart Mosquitto container for require_certificate changes to take effect")
                elif config_changed:
                    # Config changed but require_certificate didn't change, reload via SIGHUP
                    # This allows allow_anonymous and other reloadable settings to be updated without restart
                    try:
                        subprocess.run(
                            ["docker", "kill", "-s", "HUP", "camthink-mosquitto"],
                            check=True,
                        )
                        logger.info("Sent SIGHUP to Mosquitto container to reload configuration (allow_anonymous, etc.)")
                    except Exception as e:
                        logger.warning(f"Failed to send HUP to Mosquitto container: {e}")
        except Exception as e:
            logger.warning(f"Failed to synchronize Mosquitto allow_anonymous or require_certificate: {e}")

    # Apply to running services
    from backend.services.mqtt_broker import builtin_mqtt_broker
    from backend.config import settings as app_settings
    import time

    # Check if client-related configuration changed
    # Only reconnect if configuration changes that actually require client reconnection:
    # - enabled/external_enabled: Service start/stop
    # - protocol: Client must connect to different port (1883 vs 8883)
    # - broker_host: Client must connect to different host
    # - TCP/TLS port: Client must connect to different port
    # - TLS enabled: Client must use TLS or not
    # - mTLS enabled: Client must send client certificates or not (affects TLS configuration)
    # Note: allow_anonymous, username, password, max_connections, keepalive_timeout, ca_cert_path changes
    # don't require client reconnection (these are broker-side settings that can be reloaded via SIGHUP
    # without affecting existing client connections)
    client_config_changed = (
        current.enabled != saved.enabled or
        current.external_enabled != saved.external_enabled or
        current.builtin_protocol != saved.builtin_protocol or
        current.builtin_broker_host != saved.builtin_broker_host or
        current.builtin_tcp_port != saved.builtin_tcp_port or
        current.builtin_tls_port != saved.builtin_tls_port or
        current.builtin_tls_enabled != saved.builtin_tls_enabled or
        current.builtin_tls_require_client_cert != saved.builtin_tls_require_client_cert
    )
    
    # Also reconnect if Mosquitto broker was actually restarted (not just SIGHUP)
    # When broker restarts, existing connections are lost, so client must reconnect
    # mosquitto_restarted is set in the config update block above when need_restart=True
    # Note: mosquitto_restarted variable is defined in the try block above, so we need to handle it carefully
    # For now, we check if client_config_changed OR if we detect that a restart should have happened
    # The restart detection is handled in the mosquitto config update block above

    # Reload MQTT client connection if needed
    # If client config changed OR Mosquitto was restarted, reconnect client
    if client_config_changed or (saved.enabled and mosquitto_restarted):
        try:
            mqtt_service.reload_and_reconnect()
            # Wait a bit for connection to establish (connection is async)
            # Don't wait too long to avoid blocking the API response
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"Failed to reconnect MQTT client after config update: {e}")

    return saved


# ========== Built-in MQTT TLS Certificate Management ==========


@router.post("/system/mqtt/tls/upload/{kind}")
async def upload_mqtt_tls_file(
    kind: str,
    file: UploadFile = File(...),
):
    """
    Upload TLS-related files for built-in Mosquitto broker and MQTT client.

    Supported kinds:
    - 'ca'           -> /mosquitto/config/certs/ca.crt
    - 'server_cert'  -> /mosquitto/config/certs/server.crt
    - 'server_key'   -> /mosquitto/config/certs/server.key
    - 'client_cert'  -> /mosquitto/config/certs/client.crt  (used by MQTT client for mTLS, optional)
    - 'client_key'   -> /mosquitto/config/certs/client.key  (used by MQTT client for mTLS, optional)
    """
    kind = kind.lower()
    if kind not in {"ca", "server_cert", "server_key", "client_cert", "client_key"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid kind, must be one of: ca, server_cert, server_key, client_cert, client_key",
        )

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if kind in {"ca", "server_cert", "client_cert"} and ext not in {".crt", ".pem"}:
        raise HTTPException(status_code=400, detail="Certificate file must be .crt or .pem")
    if kind in {"server_key", "client_key"} and ext not in {".key", ".pem"}:
        raise HTTPException(status_code=400, detail="Key file must be .key or .pem")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    text = content.decode("utf-8", errors="ignore")
    # Basic PEM format checks
    if kind in {"ca", "server_cert", "client_cert"} and "BEGIN CERTIFICATE" not in text:
        raise HTTPException(status_code=400, detail="Invalid certificate file: missing BEGIN CERTIFICATE")
    if kind in {"server_key", "client_key"} and "BEGIN" not in text and "PRIVATE KEY" not in text:
        raise HTTPException(status_code=400, detail="Invalid key file: missing PRIVATE KEY block")

    # CRITICAL SECURITY: Validate certificate/key files using OpenSSL before accepting them
    # This prevents accepting invalid files (empty files, corrupted files, etc.)
    import tempfile
    import os
    
    if kind in {"ca", "server_cert", "client_cert"}:
        # Write content to temp file for OpenSSL validation
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.crt') as tmp_file:
            tmp_file.write(content)
            tmp_cert_path = tmp_file.name
        
        try:
            # Use OpenSSL to verify the certificate is actually valid
            result = subprocess.run(
                ["openssl", "x509", "-in", tmp_cert_path, "-noout", "-text"],
                capture_output=True,
                text=True,
                check=False,
            )
            
            if result.returncode != 0:
                logger.error(f"Invalid certificate file uploaded: {result.stderr}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid certificate file: OpenSSL validation failed. {result.stderr[:200]}"
                )
            
            # Additional check: verify certificate has valid structure
            # Extract subject to ensure it's a real certificate
            subject_result = subprocess.run(
                ["openssl", "x509", "-in", tmp_cert_path, "-noout", "-subject"],
                capture_output=True,
                text=True,
                check=False,
            )
            
            if subject_result.returncode != 0 or not subject_result.stdout.strip():
                raise HTTPException(
                    status_code=400,
                    detail="Invalid certificate file: cannot extract certificate subject"
                )
            
            logger.info(f"Certificate validation passed: {subject_result.stdout.strip()[:100]}")
        except HTTPException:
            raise
        except subprocess.CalledProcessError as e:
            logger.error(f"OpenSSL validation error: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Certificate validation failed: {str(e)}"
            )
        except FileNotFoundError:
            logger.warning("OpenSSL not available, skipping certificate validation")
            # If OpenSSL is not available, we can't validate, but this should not happen in production
        finally:
            try:
                os.unlink(tmp_cert_path)
            except:
                pass
    
    elif kind in {"server_key", "client_key"}:
        # Write content to temp file for OpenSSL validation
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.key') as tmp_file:
            tmp_file.write(content)
            tmp_key_path = tmp_file.name
        
        try:
            # Use OpenSSL to verify the key is actually valid
            result = subprocess.run(
                ["openssl", "rsa", "-in", tmp_key_path, "-check", "-noout"],
                capture_output=True,
                text=True,
                check=False,
            )
            
            if result.returncode != 0:
                # Try EC key format
                result = subprocess.run(
                    ["openssl", "ec", "-in", tmp_key_path, "-check", "-noout"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            
            if result.returncode != 0:
                logger.error(f"Invalid key file uploaded: {result.stderr}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid key file: OpenSSL validation failed. Key may be corrupted or in wrong format."
                )
            
            logger.info("Key validation passed")
        except HTTPException:
            raise
        except subprocess.CalledProcessError as e:
            logger.error(f"OpenSSL validation error: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Key validation failed: {str(e)}"
            )
        except FileNotFoundError:
            logger.warning("OpenSSL not available, skipping key validation")
        finally:
            try:
                os.unlink(tmp_key_path)
            except:
                pass

    base_dir = Path("/mosquitto/config/certs")
    base_dir.mkdir(parents=True, exist_ok=True)

    if kind == "ca":
        dest = base_dir / "ca.crt"
        # CRITICAL SECURITY CHECK: Verify that uploaded CA matches existing CA (if exists)
        # This prevents replacing the CA with a different one, which would allow unauthorized certificates
        if dest.exists():
            try:
                # Get fingerprints of both certificates
                import tempfile
                import hashlib
                
                # Read existing CA certificate
                existing_ca_content = dest.read_bytes()
                
                # Calculate SHA256 fingerprints
                existing_fingerprint = hashlib.sha256(existing_ca_content).hexdigest()
                new_fingerprint = hashlib.sha256(content).hexdigest()
                
                # If fingerprints don't match, reject the upload
                if existing_fingerprint != new_fingerprint:
                    logger.warning(f"CA certificate replacement attempt detected. Existing fingerprint: {existing_fingerprint[:16]}..., New fingerprint: {new_fingerprint[:16]}...")
                    raise HTTPException(
                        status_code=403,
                        detail="Cannot replace CA certificate with a different one. This would allow unauthorized certificates to connect. If you need to change the CA, please delete all existing certificates first or contact system administrator."
                    )
                
                # Also verify using OpenSSL to ensure certificates are actually the same
                # (in case of certificate renewal with same key but different validity period)
                try:
                    # Extract subject and issuer from both certificates
                    existing_subject = subprocess.run(
                        ["openssl", "x509", "-in", str(dest), "-noout", "-subject"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout.strip()
                    
                    existing_issuer = subprocess.run(
                        ["openssl", "x509", "-in", str(dest), "-noout", "-issuer"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout.strip()
                    
                    # Write new CA to temp file for comparison
                    with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.crt') as tmp_ca:
                        tmp_ca.write(content)
                        tmp_ca_path = tmp_ca.name
                    
                    try:
                        new_subject = subprocess.run(
                            ["openssl", "x509", "-in", tmp_ca_path, "-noout", "-subject"],
                            capture_output=True,
                            text=True,
                            check=True,
                        ).stdout.strip()
                        
                        new_issuer = subprocess.run(
                            ["openssl", "x509", "-in", tmp_ca_path, "-noout", "-issuer"],
                            capture_output=True,
                            text=True,
                            check=True,
                        ).stdout.strip()
                        
                        # Subject and issuer must match (CA certificates are self-signed, so subject == issuer)
                        if existing_subject != new_subject or existing_issuer != new_issuer:
                            logger.warning(f"CA certificate subject/issuer mismatch. Existing: {existing_subject}, New: {new_subject}")
                            raise HTTPException(
                                status_code=403,
                                detail="Cannot replace CA certificate with a different one. The new CA has different subject/issuer. This would allow unauthorized certificates to connect."
                            )
                    finally:
                        try:
                            os.unlink(tmp_ca_path)
                        except:
                            pass
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Failed to verify CA certificate using OpenSSL: {e}. Proceeding with fingerprint check only.")
                except FileNotFoundError:
                    logger.warning("OpenSSL not available for CA certificate verification. Proceeding with fingerprint check only.")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error validating CA certificate: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to validate CA certificate: {str(e)}"
                )
    elif kind == "server_cert":
        dest = base_dir / "server.crt"
        # Validate server certificate matches server key (if both exist)
        server_key = base_dir / "server.key"
        if server_key.exists():
            try:
                # Write temp server cert for validation
                with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.crt') as tmp_cert:
                    tmp_cert.write(content)
                    tmp_cert_path = tmp_cert.name
                
                try:
                    # Extract public key from certificate
                    cert_pubkey = subprocess.run(
                        ["openssl", "x509", "-in", tmp_cert_path, "-noout", "-pubkey"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout
                    
                    # Extract public key from key file
                    key_pubkey = subprocess.run(
                        ["openssl", "rsa", "-in", str(server_key), "-pubout"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout
                    
                    if cert_pubkey != key_pubkey:
                        raise HTTPException(
                            status_code=400,
                            detail="Server certificate does not match server key. Certificate and key must be a matching pair."
                        )
                    
                    logger.info("Server certificate matches server key")
                finally:
                    try:
                        os.unlink(tmp_cert_path)
                    except:
                        pass
            except HTTPException:
                raise
            except subprocess.CalledProcessError as e:
                logger.warning(f"Failed to verify server cert/key match: {e}")
            except FileNotFoundError:
                logger.warning("OpenSSL not available, skipping server cert/key validation")
    elif kind == "server_key":
        dest = base_dir / "server.key"
    elif kind == "client_cert":
        dest = base_dir / "client.crt"
        # Validate client certificate is signed by CA (if CA exists)
        ca_crt = base_dir / "ca.crt"
        if ca_crt.exists():
            try:
                # Write temp client cert for validation
                with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.crt') as tmp_cert:
                    tmp_cert.write(content)
                    tmp_cert_path = tmp_cert.name
                
                try:
                    # Verify certificate is signed by CA
                    verify_result = subprocess.run(
                        ["openssl", "verify", "-CAfile", str(ca_crt), tmp_cert_path],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    
                    if verify_result.returncode != 0:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Client certificate is not signed by the configured CA. {verify_result.stderr[:200]}"
                        )
                    
                    logger.info("Client certificate verified against CA")
                finally:
                    try:
                        os.unlink(tmp_cert_path)
                    except:
                        pass
            except HTTPException:
                raise
            except subprocess.CalledProcessError as e:
                logger.warning(f"Failed to verify client cert against CA: {e}")
            except FileNotFoundError:
                logger.warning("OpenSSL not available, skipping client cert CA validation")
    else:  # client_key
        dest = base_dir / "client.key"
        # Validate client key matches client cert (if both exist)
        client_crt = base_dir / "client.crt"
        if client_crt.exists():
            try:
                # Write temp client key for validation
                with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.key') as tmp_key:
                    tmp_key.write(content)
                    tmp_key_path = tmp_key.name
                
                try:
                    # Extract public key from certificate
                    cert_pubkey = subprocess.run(
                        ["openssl", "x509", "-in", str(client_crt), "-noout", "-pubkey"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout
                    
                    # Extract public key from key file
                    key_pubkey = subprocess.run(
                        ["openssl", "rsa", "-in", tmp_key_path, "-pubout"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout
                    
                    if cert_pubkey != key_pubkey:
                        raise HTTPException(
                            status_code=400,
                            detail="Client key does not match client certificate. Certificate and key must be a matching pair."
                        )
                    
                    logger.info("Client key matches client certificate")
                finally:
                    try:
                        os.unlink(tmp_key_path)
                    except:
                        pass
            except HTTPException:
                raise
            except subprocess.CalledProcessError as e:
                logger.warning(f"Failed to verify client key/cert match: {e}")
            except FileNotFoundError:
                logger.warning("OpenSSL not available, skipping client key/cert validation")

    try:
        dest.write_bytes(content)
        if kind in {"server_key", "client_key"}:
            dest.chmod(0o600)
    except Exception as e:
        logger.error(f"Failed to write TLS file {dest}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    # If CA was updated, also update builtin TLS CA path in config so clients pick it up.
    if kind == "ca":
        cfg = mqtt_config_service.load_config()
        cfg.builtin_tls_ca_cert_path = str(dest)
        mqtt_config_service.save_config(cfg)
        # CRITICAL: CA certificate change requires Mosquitto restart (not just SIGHUP)
        # This ensures the new CA is properly loaded and used for verification
        logger.info("CA certificate updated, restarting Mosquitto to apply changes")
        try:
            subprocess.run(
                ["docker", "restart", "camthink-mosquitto"],
                check=True,
                timeout=30,
            )
            logger.info("Mosquitto restarted successfully after CA certificate update")
        except Exception as e:
            logger.error(f"Failed to restart Mosquitto after CA certificate update: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"CA certificate updated but failed to restart Mosquitto: {e}. Please restart manually."
            )
    # Note: Client cert/key paths are no longer persisted - AIToolStack always uses default paths
    # (/mosquitto/config/certs/client.crt and /mosquitto/config/certs/client.key)

    # Ask Mosquitto container to reload configuration (including new certs)
    # Note: For CA changes, we restart above, so this is only for other certificate types
    if kind != "ca":
        try:
            subprocess.run(
                ["docker", "kill", "-s", "HUP", "camthink-mosquitto"],
                check=True,
            )
        except Exception as e:
            logger.warning(f"Failed to send HUP to Mosquitto container after TLS upload: {e}")

    return {"success": True, "path": str(dest)}


@router.post("/system/mqtt/tls/upload-external/{kind}")
async def upload_external_broker_tls_file(
    kind: str,
    file: UploadFile = File(...),
):
    """
    Upload TLS-related files for external MQTT brokers.
    These files are stored separately from built-in broker certificates.

    Supported kinds:
    - 'ca'           -> /mosquitto/config/certs/external/ca-{timestamp}.crt
    - 'client_cert'  -> /mosquitto/config/certs/external/client-cert-{timestamp}.crt
    - 'client_key'   -> /mosquitto/config/certs/external/client-key-{timestamp}.key
    """
    import time
    kind = kind.lower()
    if kind not in {"ca", "client_cert", "client_key"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid kind, must be one of: ca, client_cert, client_key",
        )

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if kind in {"ca", "client_cert"} and ext not in {".crt", ".pem"}:
        raise HTTPException(status_code=400, detail="Certificate file must be .crt or .pem")
    if kind == "client_key" and ext not in {".key", ".pem"}:
        raise HTTPException(status_code=400, detail="Key file must be .key or .pem")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    text = content.decode("utf-8", errors="ignore")
    # Basic PEM format checks
    if kind in {"ca", "client_cert"} and "BEGIN CERTIFICATE" not in text:
        raise HTTPException(status_code=400, detail="Invalid certificate file: missing BEGIN CERTIFICATE")
    if kind == "client_key" and "BEGIN" not in text and "PRIVATE KEY" not in text:
        raise HTTPException(status_code=400, detail="Invalid key file: missing PRIVATE KEY block")

    base_dir = Path("/mosquitto/config/certs/external")
    base_dir.mkdir(parents=True, exist_ok=True)

    # Use timestamp to make unique filenames
    timestamp = int(time.time())
    if kind == "ca":
        dest = base_dir / f"ca-{timestamp}.crt"
    elif kind == "client_cert":
        dest = base_dir / f"client-cert-{timestamp}.crt"
    else:  # client_key
        dest = base_dir / f"client-key-{timestamp}.key"

    try:
        dest.write_bytes(content)
        if kind == "client_key":
            dest.chmod(0o600)
    except Exception as e:
        logger.error(f"Failed to write external TLS file {dest}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    return {"success": True, "path": str(dest)}


@router.get("/system/mqtt/tls/ca")
def download_mqtt_ca_certificate():
    """Download current CA certificate used by built-in Mosquitto broker for MQTTS."""
    ca_path = Path("/mosquitto/config/certs/ca.crt")
    if not ca_path.exists():
        raise HTTPException(status_code=404, detail="CA certificate not found")
    return FileResponse(
        str(ca_path),
        media_type="application/x-x509-ca-cert",
        filename="mqtt-ca.crt",
    )


@router.get("/system/mqtt/tls/client-cert")
def download_mqtt_client_certificate():
    """Download current client certificate for mTLS (used by external devices to connect to MQTTS)."""
    client_cert_path = Path("/mosquitto/config/certs/client.crt")
    if not client_cert_path.exists():
        raise HTTPException(status_code=404, detail="Client certificate not found. Please upload a client certificate first.")
    return FileResponse(
        str(client_cert_path),
        media_type="application/x-x509-user-cert",
        filename="mqtt-client.crt",
    )


@router.get("/system/mqtt/tls/client-key")
def download_mqtt_client_key():
    """Download current client private key for mTLS (used by AIToolStack to connect to MQTTS)."""
    client_key_path = Path("/mosquitto/config/certs/client.key")
    if not client_key_path.exists():
        raise HTTPException(status_code=404, detail="Client key not found. Please upload a client key first.")
    return FileResponse(
        str(client_key_path),
        media_type="application/x-pem-file",
        filename="mqtt-client.key",
    )


@router.get("/system/mqtt/tls/device-cert/{common_name}")
def download_device_client_certificate(common_name: str):
    """Download client certificate for a specific device (by CN)."""
    from urllib.parse import unquote
    safe_cn = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in unquote(common_name))
    client_cert_path = Path(f"/mosquitto/config/certs/client-{safe_cn}.crt")
    if not client_cert_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Client certificate for device '{common_name}' not found. Please generate it first.",
        )
    return FileResponse(
        str(client_cert_path),
        media_type="application/x-x509-user-cert",
        filename=f"mqtt-client-{safe_cn}.crt",
    )


@router.get("/system/mqtt/tls/device-key/{common_name}")
def download_device_client_key(common_name: str):
    """Download client private key for a specific device (by CN)."""
    from urllib.parse import unquote
    safe_cn = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in unquote(common_name))
    client_key_path = Path(f"/mosquitto/config/certs/client-{safe_cn}.key")
    if not client_key_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Client key for device '{common_name}' not found. Please generate it first.",
        )
    return FileResponse(
        str(client_key_path),
        media_type="application/x-pem-file",
        filename=f"mqtt-client-{safe_cn}.key",
    )


@router.get("/system/mqtt/tls/external/{kind}/{filename}")
def download_external_broker_tls_file(kind: str, filename: str):
    """Download TLS files for external MQTT brokers.
    
    Args:
        kind: 'ca', 'client-cert', or 'client-key'
        filename: Filename without extension (e.g., 'ca-1766456884' or 'client-cert-1766456884')
    """
    from urllib.parse import unquote
    
    kind = kind.lower()
    if kind not in {"ca", "client-cert", "client-key"}:
        raise HTTPException(status_code=400, detail="Invalid kind, must be one of: ca, client-cert, client-key")
    
    safe_filename = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in unquote(filename))
    
    base_dir = Path("/mosquitto/config/certs/external")
    if kind == "ca":
        file_path = base_dir / f"{safe_filename}.crt"
        media_type = "application/x-x509-ca-cert"
        download_filename = f"mqtt-external-ca-{safe_filename}.crt"
    elif kind == "client-cert":
        file_path = base_dir / f"{safe_filename}.crt"
        media_type = "application/x-x509-user-cert"
        download_filename = f"mqtt-external-client-{safe_filename}.crt"
    else:  # client-key
        file_path = base_dir / f"{safe_filename}.key"
        media_type = "application/x-pem-file"
        download_filename = f"mqtt-external-client-{safe_filename}.key"
    
    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {file_path.name}"
        )
    
    return FileResponse(
        str(file_path),
        media_type=media_type,
        filename=download_filename,
    )


@router.post("/system/mqtt/tls/sync-device-certificates")
def sync_device_certificates_to_password_file():
    """Sync all device certificate CNs to Mosquitto password file.
    
    This is required when mTLS is enabled and use_identity_as_username=true.
    Only users in the password file can connect when use_identity_as_username is enabled.
    """
    from pathlib import Path
    import re
    
    certs_dir = Path("/mosquitto/config/certs")
    if not certs_dir.exists():
        return {"message": "No certificates directory found", "synced": 0}
    
    # Get all device certificates (client-{CN}.crt pattern)
    pattern = re.compile(r'^client-(.+)\.crt$')
    device_certs = []
    for cert_file in certs_dir.glob("client-*.crt"):
        match = pattern.match(cert_file.name)
        if match:
            cn = match.group(1)
            device_certs.append(cn)
    
    # Add each device CN to password file
    synced_count = 0
    failed_count = 0
    for cn in device_certs:
        try:
            _add_device_to_password_file(cn)
            synced_count += 1
            logger.info(f"Synced device certificate CN '{cn}' to password file")
        except Exception as e:
            failed_count += 1
            logger.warning(f"Failed to sync device CN '{cn}' to password file: {e}")
    
    return {
        "message": f"Synced {synced_count} device certificate(s) to password file",
        "synced": synced_count,
        "failed": failed_count,
        "total": len(device_certs)
    }


@router.get("/system/mqtt/tls/device-certificates")
def list_device_certificates():
    """List all device client certificates (excluding AIToolStack's client.crt)."""
    from pathlib import Path
    import re
    
    certs_dir = Path("/mosquitto/config/certs")
    if not certs_dir.exists():
        return {"devices": []}
    
    # Find all device certificates (client-{CN}.crt pattern)
    device_certs = []
    pattern = re.compile(r'^client-(.+)\.crt$')
    
    for cert_file in certs_dir.glob("client-*.crt"):
        match = pattern.match(cert_file.name)
        if match:
            cn = match.group(1)
            key_file = certs_dir / f"client-{cn}.key"
            cert_path = cert_file
            key_path = key_file if key_file.exists() else None
            
            # Get file modification time
            try:
                mtime = cert_file.stat().st_mtime
                from datetime import datetime
                created_at = datetime.fromtimestamp(mtime).isoformat()
            except:
                created_at = None
            
            device_certs.append({
                "common_name": cn,
                "cert_path": str(cert_path),
                "key_path": str(key_path) if key_path else None,
                "cert_exists": cert_path.exists(),
                "key_exists": key_path is not None and key_path.exists(),
                "created_at": created_at,
            })
    
    # Sort by common name
    device_certs.sort(key=lambda x: x["common_name"])
    
    return {"devices": device_certs}


def _add_certificate_to_ca_database(cert_path: Path, certs_dir: Path, common_name: str):
    """Add a certificate to the CA database (index.txt) for CRL support.
    
    This allows the certificate to be properly revoked later using openssl ca -revoke.
    """
    ca_db_dir = certs_dir / "ca_db"
    ca_db_dir.mkdir(exist_ok=True)
    index_file = ca_db_dir / "index.txt"
    serial_file = ca_db_dir / "serial"
    
    # Initialize files if needed
    if not index_file.exists():
        index_file.write_text("")
    if not serial_file.exists():
        serial_file.write_text("01")
    
    # Extract certificate serial number
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(cert_path), "-noout", "-serial"],
            capture_output=True,
            text=True,
            check=True,
        )
        cert_serial = result.stdout.strip().replace("serial=", "").replace(":", "").upper()
    except Exception as e:
        logger.warning(f"Failed to extract certificate serial: {e}")
        return  # Cannot add to database without serial
    
    # Check if certificate is already in database
    if index_file.exists():
        index_content = index_file.read_text()
        if cert_serial in index_content:
            logger.debug(f"Certificate {cert_serial} already in CA database")
            return
    
    # Extract certificate subject
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(cert_path), "-noout", "-subject", "-nameopt", "RFC2253"],
            capture_output=True,
            text=True,
            check=True,
        )
        cert_subject = result.stdout.strip().replace("subject=", "")
    except:
        cert_subject = f"/CN={common_name}"
    
    # Extract certificate validity dates
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(cert_path), "-noout", "-dates"],
            capture_output=True,
            text=True,
            check=True,
        )
        # Parse notBefore date
        not_before = None
        for line in result.stdout.split('\n'):
            if 'notBefore=' in line:
                not_before_str = line.split('=')[1].strip()
                # Convert to index.txt format (YYMMDDHHMMSSZ)
                from datetime import datetime
                try:
                    dt = datetime.strptime(not_before_str, "%b %d %H:%M:%S %Y %Z")
                    not_before = dt.strftime("%y%m%d%H%M%S") + "Z"
                except:
                    not_before = datetime.utcnow().strftime("%y%m%d%H%M%S") + "Z"
                break
        if not not_before:
            not_before = datetime.utcnow().strftime("%y%m%d%H%M%S") + "Z"
    except:
        from datetime import datetime
        not_before = datetime.utcnow().strftime("%y%m%d%H%M%S") + "Z"
    
    # Add certificate to index.txt (format: V\t{notBefore}\t\t{serial}\tunknown\t{subject})
    # V = Valid status
    index_entry = f"V\t{not_before}\t\t{cert_serial}\tunknown\t{cert_subject}\n"
    index_file.write_text(index_file.read_text() + index_entry)
    logger.info(f"Added certificate {cert_serial} (CN: {common_name}) to CA database")


def _add_certificate_to_crl(cert_content: bytes, certs_dir: Path):
    """Add a certificate to the Certificate Revocation List (CRL).
    
    This function creates or updates a CRL file that Mosquitto can use to reject
    revoked certificates. The CRL is created using OpenSSL.
    
    Note: This requires proper CA database setup. We initialize it if needed.
    """
    crl_file = certs_dir / "revoked.crl"
    ca_cert = certs_dir / "ca.crt"
    ca_key = certs_dir / "ca.key"
    
    # Check if CA files exist
    if not ca_cert.exists() or not ca_key.exists():
        raise ValueError("CA certificate or key not found. Cannot create CRL.")
    
    # Write certificate to temporary file for processing
    import tempfile
    with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.crt') as tmp_cert:
        tmp_cert.write(cert_content)
        tmp_cert_path = tmp_cert.name
    
    try:
        # Initialize CA database directory
        ca_db_dir = certs_dir / "ca_db"
        ca_db_dir.mkdir(exist_ok=True)
        index_file = ca_db_dir / "index.txt"
        serial_file = ca_db_dir / "serial"
        crlnumber_file = ca_db_dir / "crlnumber"
        
        # Initialize CA database files if they don't exist
        if not index_file.exists():
            index_file.write_text("")
        if not serial_file.exists():
            serial_file.write_text("01")
        if not crlnumber_file.exists():
            crlnumber_file.write_text("01")
        
        # Create OpenSSL CA configuration
        ca_config_content = f"""[ ca ]
default_ca = CA_default

[ CA_default ]
dir = {ca_db_dir}
certs = {certs_dir}
new_certs_dir = {ca_db_dir}
database = {index_file}
serial = {serial_file}
RANDFILE = {ca_db_dir}/.rand
private_key = {ca_key}
certificate = {ca_cert}
crlnumber = {crlnumber_file}
crl = {crl_file}
x509_extensions = v3_ca
name_opt = ca_default
cert_opt = ca_default
default_days = 365
default_crl_days = 30
default_md = sha256
preserve = no
policy = policy_match

[ policy_match ]
countryName = optional
stateOrProvinceName = optional
organizationName = optional
organizationalUnitName = optional
commonName = supplied
emailAddress = optional

[ v3_ca ]
"""
        ca_config_file = ca_db_dir / "openssl.cnf"
        ca_config_file.write_text(ca_config_content)
        
        # Extract certificate serial number for tracking
        cert_serial = None
        try:
            result = subprocess.run(
                ["openssl", "x509", "-in", tmp_cert_path, "-noout", "-serial"],
                capture_output=True,
                text=True,
                check=True,
            )
            cert_serial = result.stdout.strip().replace("serial=", "").replace(":", "").upper()
        except Exception as e:
            logger.warning(f"Failed to extract certificate serial: {e}")
        
        # Check if certificate is already in the database
        cert_in_db = False
        if cert_serial and index_file.exists():
            index_content = index_file.read_text()
            if cert_serial in index_content:
                cert_in_db = True
        
        # If certificate is not in database, we need to add it first
        # Extract certificate subject for database entry
        cert_subject = "/CN=unknown"
        if not cert_in_db:
            try:
                result = subprocess.run(
                    ["openssl", "x509", "-in", tmp_cert_path, "-noout", "-subject", "-nameopt", "RFC2253"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                cert_subject = result.stdout.strip().replace("subject=", "").replace("subject=", "")
            except:
                pass
            
            # Add certificate to database as revoked (R status)
            from datetime import datetime
            now = datetime.utcnow()
            revoke_date = now.strftime("%y%m%d%H%M%S") + "Z"
            if cert_serial:
                index_entry = f"R\t{revoke_date}\t\t{cert_serial}\tunknown\t{cert_subject}\n"
                index_file.write_text(index_file.read_text() + index_entry)
        
        # Try to revoke the certificate using openssl ca
        try:
            subprocess.run(
                ["openssl", "ca", "-revoke", tmp_cert_path,
                 "-keyfile", str(ca_key),
                 "-cert", str(ca_cert),
                 "-config", str(ca_config_file),
                 "-crl_reason", "superseded"],
                check=True,
                capture_output=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            # If revoke fails, ensure the certificate is marked as revoked in index.txt
            if cert_serial and index_file.exists():
                content = index_file.read_text()
                lines = content.split('\n')
                updated = False
                for i, line in enumerate(lines):
                    if cert_serial in line and line.startswith('V\t'):
                        from datetime import datetime
                        now = datetime.utcnow()
                        revoke_date = now.strftime("%y%m%d%H%M%S") + "Z"
                        # Update status from V (Valid) to R (Revoked)
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            parts[0] = 'R'
                            parts[1] = revoke_date
                            lines[i] = '\t'.join(parts)
                            updated = True
                            break
                if updated:
                    index_file.write_text('\n'.join(lines))
            logger.warning(f"OpenSSL ca -revoke failed, manually updated index.txt: {e}")
        
        # Generate CRL
        crl_generated = False
        try:
            result = subprocess.run(
                ["openssl", "ca", "-gencrl",
                 "-keyfile", str(ca_key),
                 "-cert", str(ca_cert),
                 "-config", str(ca_config_file),
                 "-out", str(crl_file)],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Verify CRL was generated successfully
            if crl_file.exists() and crl_file.stat().st_size > 0:
                crl_generated = True
                logger.info(f"CRL generated successfully: {crl_file}")
            else:
                logger.warning("CRL generation completed but file is empty or missing")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            # If CRL generation fails, log warning but continue
            logger.warning(f"Failed to generate CRL: {e}")
            logger.warning("CRL file generation failed. Certificate revocation may not work properly.")
        
        # Only update mosquitto.conf if CRL was successfully generated
        if crl_generated:
            _ensure_crlfile_in_mosquitto_conf()
    finally:
        # Clean up temporary file
        try:
            os.unlink(tmp_cert_path)
        except:
            pass


def _is_valid_crl_file(crl_file: Path) -> bool:
    """Check if CRL file exists and is valid (non-empty and has valid PEM format)."""
    if not crl_file.exists():
        return False
    
    try:
        content = crl_file.read_text()
        # CRL file must be non-empty and contain PEM format markers
        if not content.strip():
            return False
        # Check for PEM format (should contain BEGIN/END markers)
        if "BEGIN" in content and "END" in content:
            return True
        # If it's a valid binary CRL, it should have some content
        if len(content) > 100:  # Valid CRL files are typically larger
            return True
        return False
    except Exception as e:
        logger.warning(f"Error checking CRL file validity: {e}")
        return False


def _ensure_crlfile_in_mosquitto_conf():
    """Ensure mosquitto.conf includes crlfile configuration for listener 8883 only if CRL file is valid."""
    conf_path = Path("/mosquitto/config/mosquitto.conf")
    crl_file = Path("/mosquitto/config/certs/revoked.crl")
    
    if not conf_path.exists():
        logger.warning("mosquitto.conf not found, cannot manage crlfile")
        return
    
    conf_text = conf_path.read_text()
    lines = conf_text.split('\n')
    
    # Find listener 8883 block
    listener_8883_start = -1
    listener_8883_end = -1
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if re.match(r'^listener\s+8883', stripped):
            listener_8883_start = i
        elif listener_8883_start >= 0:
            if re.match(r'^listener\s+', stripped):
                listener_8883_end = i
                break
            elif re.match(r'^(persistence|log_dest|log_timestamp|password_file|allow_anonymous)', stripped):
                listener_8883_end = i
                break
    
    if listener_8883_start < 0:
        logger.warning("listener 8883 block not found in mosquitto.conf")
        return
    
    if listener_8883_end < 0:
        listener_8883_end = len(lines)
    
    # Check if CRL file is valid
    crl_is_valid = _is_valid_crl_file(crl_file)
    
    # Find existing crlfile line
    crlfile_line_index = -1
    for i in range(listener_8883_start, listener_8883_end):
        if re.match(r'^\s*crlfile\s+', lines[i]):
            crlfile_line_index = i
            break
    
    if crl_is_valid:
        # CRL file is valid, ensure crlfile is configured
        if crlfile_line_index >= 0:
            # Update existing crlfile line
            lines[crlfile_line_index] = re.sub(
                r'^\s*crlfile\s+.*',
                f'crlfile {crl_file}',
                lines[crlfile_line_index]
            )
            logger.info(f"Updated crlfile line at line {crlfile_line_index+1}")
        else:
            # Insert crlfile after require_certificate or keyfile
            insert_pos = listener_8883_end
            for i in range(listener_8883_start, listener_8883_end):
                if re.match(r'^\s*require_certificate\s+', lines[i]):
                    insert_pos = i + 1
                    break
                elif re.match(r'^\s*keyfile\s+', lines[i]):
                    insert_pos = i + 1
            lines.insert(insert_pos, f'crlfile {crl_file}')
            logger.info(f"Inserted crlfile at line {insert_pos+1}")
    else:
        # CRL file is invalid or doesn't exist, remove crlfile configuration if present
        if crlfile_line_index >= 0:
            lines.pop(crlfile_line_index)
            logger.info(f"Removed crlfile configuration (CRL file is invalid or empty)")
    
    new_text = '\n'.join(lines)
    if new_text != conf_text:
        conf_path.write_text(new_text)
        if crl_is_valid:
            logger.info(f"Updated mosquitto.conf to include crlfile: {crl_file}")
        else:
            logger.info("Removed crlfile from mosquitto.conf (CRL file is invalid or empty)")


def _add_device_to_password_file(common_name: str):
    """Add a device CN to Mosquitto password file for mTLS authentication.
    
    When use_identity_as_username is enabled, only users in password file can connect.
    This function ensures the device CN is added to the password file.
    """
    try:
        passwordfile = Path("/mosquitto/config/passwordfile")
        # Ensure passwordfile exists
        if not passwordfile.exists():
            passwordfile.parent.mkdir(parents=True, exist_ok=True)
            passwordfile.touch()
            os.chmod(passwordfile, 0o600)
        
        # Check if user already exists in password file
        user_exists = False
        if passwordfile.exists():
            with open(passwordfile, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and ':' in line:
                        user = line.split(':', 1)[0]
                        if user == common_name:
                            user_exists = True
                            break
        
        # Add user to password file if it doesn't exist
        # For mTLS, we don't need a password - the certificate itself is the authentication
        # But Mosquitto requires a password entry. We'll use a random secure password that's never used.
        if not user_exists:
            import secrets
            # Generate a random password that will never be used (certificate is the auth method)
            dummy_password = secrets.token_urlsafe(32)
            cmd = [
                "mosquitto_passwd",
                "-b",
                str(passwordfile),
                common_name,
                dummy_password,
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            os.chmod(passwordfile, 0o600)
            logger.info(f"Added device CN '{common_name}' to Mosquitto password file for mTLS authentication")
        else:
            logger.debug(f"Device CN '{common_name}' already exists in password file")
    except Exception as e:
        logger.warning(f"Failed to add device CN to password file: {e}. Certificate generated but device may not be able to connect if use_identity_as_username is enabled.")


def _remove_device_from_password_file(common_name: str):
    """Remove a device CN from Mosquitto password file."""
    try:
        passwordfile = Path("/mosquitto/config/passwordfile")
        if not passwordfile.exists():
            return
        
        # Read all lines except the one matching the CN
        lines = []
        removed = False
        with open(passwordfile, 'r', encoding='utf-8') as f:
            for line in f:
                line_stripped = line.strip()
                if line_stripped and ':' in line_stripped:
                    user = line_stripped.split(':', 1)[0]
                    if user != common_name:
                        lines.append(line)
                    else:
                        removed = True
        
        # Write back the file without the removed user
        if removed:
            with open(passwordfile, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            os.chmod(passwordfile, 0o600)
            logger.info(f"Removed device CN '{common_name}' from Mosquitto password file")
    except Exception as e:
        logger.warning(f"Failed to remove device CN from password file: {e}")


@router.delete("/system/mqtt/tls/device-certificate/{common_name}")
def delete_device_certificate(common_name: str):
    """Delete a device client certificate and key, and add it to CRL for immediate revocation."""
    from urllib.parse import unquote
    from pathlib import Path
    import subprocess
    
    safe_cn = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in unquote(common_name))
    certs_dir = Path("/mosquitto/config/certs")
    
    cert_path = certs_dir / f"client-{safe_cn}.crt"
    key_path = certs_dir / f"client-{safe_cn}.key"
    csr_path = certs_dir / f"client-{safe_cn}.csr"
    
    # Read certificate content before deletion (needed for CRL)
    cert_content = None
    if cert_path.exists():
        cert_content = cert_path.read_bytes()
    
    # Remove device CN from password file (required when use_identity_as_username is enabled)
    _remove_device_from_password_file(common_name)
    
    deleted_files = []
    
    if cert_path.exists():
        cert_path.unlink()
        deleted_files.append(str(cert_path))
    
    if key_path.exists():
        key_path.unlink()
        deleted_files.append(str(key_path))
    
    if csr_path.exists():
        csr_path.unlink()
        deleted_files.append(str(csr_path))
    
    if not deleted_files:
        raise HTTPException(
            status_code=404,
            detail=f"Device certificate for '{common_name}' not found.",
        )
    
    # Add certificate to CRL for immediate revocation
    # This ensures that even if the client still has the certificate, it cannot connect
    if cert_content:
        try:
            _add_certificate_to_crl(cert_content, certs_dir)
            logger.info(f"Added certificate for device '{common_name}' to CRL")
        except Exception as e:
            logger.warning(f"Failed to add certificate to CRL: {e}. Certificate deleted but may still be usable until Mosquitto restart.")
    
    # Update mosquitto.conf to include crlfile if mTLS is enabled and CRL is valid
    # Only restart if CRL was successfully generated
    try:
        cfg = mqtt_config_service.load_config()
        if cfg.builtin_protocol == "mqtts" and cfg.builtin_tls_require_client_cert:
            # Check if CRL file is now valid (after generation attempt)
            crl_file = Path("/mosquitto/config/certs/revoked.crl")
            if _is_valid_crl_file(crl_file):
                _ensure_crlfile_in_mosquitto_conf()
                # Restart Mosquitto to apply CRL changes (CRL cannot be reloaded via SIGHUP)
                try:
                    subprocess.run(
                        ["docker", "restart", "camthink-mosquitto"],
                        check=True,
                        timeout=30,
                    )
                    logger.info("Mosquitto restarted to apply CRL changes")
                except Exception as e:
                    logger.warning(f"Failed to restart Mosquitto after certificate deletion: {e}")
            else:
                logger.warning("CRL file is invalid or empty, not updating mosquitto.conf")
    except Exception as e:
        logger.warning(f"Failed to update mosquitto.conf with CRL: {e}")
    
    return {
        "success": True,
        "message": f"Deleted certificate for device '{common_name}' and added to revocation list",
        "deleted_files": deleted_files,
    }


@router.post("/system/mqtt/tls/generate-client-cert")
def generate_client_certificate(
    common_name: str = "mqtt-client",
    days: int = 3650,
    for_aitoolstack: bool = True,
):
    """
    Generate a client certificate and key signed by the CA for mTLS.
    
    Args:
        common_name: CN (Common Name) for the client certificate (default: "mqtt-client")
        days: Validity period in days (default: 3650, ~10 years)
        for_aitoolstack: If True, generates certificate for AIToolStack (saves to client.crt/client.key).
                        If False, generates certificate for external device (saves to client-{CN}.crt/client-{CN}.key).
    
    Returns:
        Success message with paths to generated files
    """
    import subprocess
    from pathlib import Path
    
    certs_dir = Path("/mosquitto/config/certs")
    certs_dir.mkdir(parents=True, exist_ok=True)
    
    ca_crt = certs_dir / "ca.crt"
    ca_key = certs_dir / "ca.key"
    
    # SECURITY CHECK: Verify that CA certificate and key match (prevent using wrong CA)
    # This ensures we're using the correct CA to sign certificates
    if ca_crt.exists() and ca_key.exists():
        try:
            # Verify that the CA certificate matches the CA key
            # Extract public key from certificate and compare with key file
            cert_pubkey = subprocess.run(
                ["openssl", "x509", "-in", str(ca_crt), "-noout", "-pubkey"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            
            key_pubkey = subprocess.run(
                ["openssl", "rsa", "-in", str(ca_key), "-pubout"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            
            if cert_pubkey != key_pubkey:
                logger.error("CA certificate and key do not match! This is a security issue.")
                raise HTTPException(
                    status_code=500,
                    detail="CA certificate and key mismatch. Cannot generate client certificates with mismatched CA."
                )
            
            # Also verify that the CA certificate is self-signed (subject == issuer)
            cert_subject = subprocess.run(
                ["openssl", "x509", "-in", str(ca_crt), "-noout", "-subject"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            
            cert_issuer = subprocess.run(
                ["openssl", "x509", "-in", str(ca_crt), "-noout", "-issuer"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            
            if cert_subject != cert_issuer:
                logger.warning(f"CA certificate is not self-signed. Subject: {cert_subject}, Issuer: {cert_issuer}")
                # This is not necessarily an error, but worth logging
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to verify CA certificate/key match: {e}. Proceeding with certificate generation.")
        except FileNotFoundError:
            logger.warning("OpenSSL not available for CA verification. Proceeding with certificate generation.")
    
    # Determine output file names based on whether this is for AIToolStack or external device
    if for_aitoolstack:
        # For AIToolStack: use default names (will be used by AIToolStack client)
        client_key = certs_dir / "client.key"
        client_crt = certs_dir / "client.crt"
        client_csr = certs_dir / "client.csr"
    else:
        # For external device: use CN-based names (allows multiple devices)
        # Sanitize CN for filename (replace special chars with underscore)
        safe_cn = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in common_name)
        client_key = certs_dir / f"client-{safe_cn}.key"
        client_crt = certs_dir / f"client-{safe_cn}.crt"
        client_csr = certs_dir / f"client-{safe_cn}.csr"
    
    # Check if CA exists
    if not ca_crt.exists() or not ca_key.exists():
        raise HTTPException(
            status_code=400,
            detail="CA certificate or key not found. Please ensure CA is properly configured.",
        )
    
    try:
        # Get broker host/IP for SAN (Subject Alternative Names)
        # This allows devices to connect using IP address without certificate validation errors
        from backend.config import get_mqtt_broker_host, get_local_ip
        import socket
        
        broker_host = None
        try:
            # Try to get broker host from config (may be IP or hostname)
            from backend.services.mqtt_config_service import mqtt_config_service
            cfg = mqtt_config_service.load_config()
            if cfg.builtin_broker_host and cfg.builtin_broker_host.strip():
                broker_host = cfg.builtin_broker_host.strip()
        except:
            pass
        
        # If not set, try to get from environment or auto-detect
        if not broker_host:
            try:
                broker_host = get_mqtt_broker_host()
            except:
                broker_host = get_local_ip()
        
        # Build SAN list: include IP addresses and hostnames
        san_list = []
        
        # Add localhost for local connections
        san_list.append("IP:127.0.0.1")
        san_list.append("DNS:localhost")
        
        # Add broker host/IP
        if broker_host:
            try:
                # Check if it's an IP address
                socket.inet_aton(broker_host)
                # It's an IP address
                if broker_host not in ["127.0.0.1", "localhost"]:
                    san_list.append(f"IP:{broker_host}")
            except socket.error:
                # It's a hostname, add as DNS
                if broker_host not in ["localhost"]:
                    san_list.append(f"DNS:{broker_host}")
                    # Try to resolve hostname to IP
                    try:
                        resolved_ip = socket.gethostbyname(broker_host)
                        if resolved_ip not in ["127.0.0.1"]:
                            san_list.append(f"IP:{resolved_ip}")
                    except:
                        pass
        
        # Also add auto-detected local IP if different
        try:
            local_ip = get_local_ip()
            if local_ip and local_ip not in ["127.0.0.1", "localhost"]:
                # Check if already in list
                if not any(f"IP:{local_ip}" in san for san in san_list):
                    san_list.append(f"IP:{local_ip}")
        except:
            pass
        
        # Build SAN extension string
        san_string = ",".join(san_list)
        
        # Generate client private key
        subprocess.run(
            [
                "openssl", "genrsa",
                "-out", str(client_key),
                "2048",
            ],
            check=True,
            capture_output=True,
        )
        client_key.chmod(0o600)
        
        # Generate client certificate signing request (CSR) with SAN extension
        # Use OpenSSL config file to include SAN in CSR (compatible with all OpenSSL versions)
        import tempfile
        import os
        
        # Create temporary config file for CSR with SAN extension
        # Use prompt = no to avoid interactive prompts
        san_config_content = f"""[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
C = CN
ST = Local
L = Local
O = Camthink
OU = Dev
CN = {common_name}

[v3_req]
subjectAltName = {san_string}
"""
        # Write config to a temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.cnf', delete=False) as san_config_file:
            san_config_file.write(san_config_content)
            san_config_path = san_config_file.name
        
        try:
            # Generate CSR with SAN extension included in the request
            # Use -batch to avoid any interactive prompts (additional safety)
            subprocess.run(
                [
                    "openssl", "req", "-new",
                    "-key", str(client_key),
                    "-out", str(client_csr),
                    "-config", san_config_path,
                    "-extensions", "v3_req",
                    "-batch",  # Avoid interactive prompts
                ],
                check=True,
                capture_output=True,
                text=True,  # Capture text output for better error messages
            )
            
            # Sign client certificate with CA
            # The SAN extension is already in the CSR, so we just sign it normally
            sign_cmd = [
                "openssl", "x509", "-req",
                "-days", str(days),
                "-in", str(client_csr),
                "-CA", str(ca_crt),
                "-CAkey", str(ca_key),
                "-CAcreateserial",
                "-out", str(client_crt),
            ]
            
            result = subprocess.run(
                sign_cmd,
                check=True,
                capture_output=True,
                text=True,  # Capture text output for better error messages
            )
            
            # CRITICAL SECURITY CHECK: Verify the signed certificate was actually signed by the CA
            # This ensures the certificate chain is valid and prevents using wrong CA
            try:
                verify_result = subprocess.run(
                    ["openssl", "verify", "-CAfile", str(ca_crt), str(client_crt)],
                    capture_output=True,
                    text=True,
                    check=False,  # Don't raise on non-zero exit, we'll check the output
                )
                if verify_result.returncode != 0:
                    # Certificate verification failed - this should not happen if CA/key are correct
                    logger.error(f"Generated certificate failed CA verification: {verify_result.stderr}")
                    # Delete the invalid certificate
                    if client_crt.exists():
                        client_crt.unlink()
                    raise HTTPException(
                        status_code=500,
                        detail=f"Generated certificate failed CA verification. This may indicate CA certificate/key mismatch or corruption."
                    )
                logger.debug(f"Certificate verification passed: {verify_result.stdout.strip()}")
            except FileNotFoundError:
                logger.warning("OpenSSL verify not available, skipping certificate verification")
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"Certificate verification check failed: {e}. Certificate generated but verification skipped.")
        finally:
            # Clean up temporary config file
            try:
                os.unlink(san_config_path)
            except:
                pass
        
        # Add certificate to CA database for CRL support
        # This allows the certificate to be properly revoked later
        try:
            _add_certificate_to_ca_database(client_crt, certs_dir, common_name)
        except Exception as e:
            logger.warning(f"Failed to add certificate to CA database: {e}. Certificate generated but may not be revocable.")
        
        # If this is for an external device (not AIToolStack), add the CN as a user in password file
        # This is required when use_identity_as_username is enabled - only users in password file can connect
        if not for_aitoolstack:
            _add_device_to_password_file(common_name)
        
        # Update config to reference the generated client cert/key (only if for AIToolStack)
        if for_aitoolstack:
            cfg = mqtt_config_service.load_config()
            # Note: builtin_tls_client_cert_path/key_path are no longer used - AIToolStack always uses default client certs
            # The generated certificate is saved to the default location (/mosquitto/config/certs/client.crt/key)
            # No need to persist paths in config
        
        logger.info(f"Generated client certificate: CN={common_name}, valid for {days} days, for_aitoolstack={for_aitoolstack}")
        
        result = {
            "success": True,
            "message": f"Client certificate generated successfully (CN: {common_name})",
            "client_cert_path": str(client_crt),
            "client_key_path": str(client_key),
            "ca_cert_path": str(ca_crt),
            "for_aitoolstack": for_aitoolstack,
        }
        
        # If for external device, provide download URLs
        if not for_aitoolstack:
            result["download_cert_url"] = f"/system/mqtt/tls/device-cert/{common_name}"
            result["download_key_url"] = f"/system/mqtt/tls/device-key/{common_name}"
        
        return result
    except subprocess.CalledProcessError as e:
        # With text=True, stderr is already a string
        error_msg = e.stderr if e.stderr else (e.stdout if e.stdout else str(e))
        logger.error(f"Failed to generate client certificate: {error_msg}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate client certificate: {error_msg}",
        )
    except Exception as e:
        logger.error(f"Error generating client certificate: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error generating client certificate: {e}")


# ========== External MQTT Brokers Management ==========

@router.get("/system/mqtt/external-brokers", response_model=List[ExternalBrokerResponse])
def get_external_brokers():
    """Get all external MQTT brokers with connection status"""
    brokers = external_broker_service.get_all()
    
    # Get connection status from MQTT service
    try:
        service_status = mqtt_service.get_status()
        brokers_info = service_status.get("brokers", [])
        
        # Create a map of broker_id -> connected status for external brokers
        # Also create a fallback map of (host, port) -> connected status
        connection_map_by_id = {}
        connection_map_by_addr = {}
        for broker_info in brokers_info:
            if broker_info.get("type") == "external":
                broker_id = broker_info.get("broker_id")
                host = broker_info.get("host")
                port = broker_info.get("port")
                connected = broker_info.get("connected", False)
                
                if broker_id is not None:
                    connection_map_by_id[broker_id] = connected
                if host and port:
                    connection_map_by_addr[(host, port)] = connected
        
        # Add connection status to each broker
        for broker in brokers:
            if not broker.enabled:
                broker.connected = None
            else:
                # Try to match by broker_id first, then by (host, port)
                if broker.id in connection_map_by_id:
                    broker.connected = connection_map_by_id[broker.id]
                else:
                    key = (broker.host, broker.port)
                    broker.connected = connection_map_by_addr.get(key, False)
    except Exception as e:
        logger.warning(f"Failed to get connection status for external brokers: {e}")
        # If status fetch fails, set all to None (unknown)
        for broker in brokers:
            broker.connected = None
    
    return brokers


@router.get("/system/mqtt/external-brokers/{broker_id}", response_model=ExternalBrokerResponse)
def get_external_broker(broker_id: int):
    """Get a specific external MQTT broker by ID"""
    broker = external_broker_service.get_by_id(broker_id)
    if not broker:
        raise HTTPException(status_code=404, detail=f"External broker with ID {broker_id} not found")
    return broker


@router.post("/system/mqtt/external-brokers", response_model=ExternalBrokerResponse)
def create_external_broker(broker: ExternalBrokerCreate):
    """Create a new external MQTT broker (connection test should be done before calling this)"""
    # Verify connection before creating
    import socket
    import time
    import ssl
    import paho.mqtt.client as mqtt
    import uuid
    
    try:
        # Test TCP connection first
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((broker.host, broker.port))
        sock.close()
        
        if result != 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot connect to {broker.host}:{broker.port}, please check if the address and port are correct"
            )
        
        # Test MQTT connection
        test_client = None
        connection_result = {"success": False, "message": ""}
        
        def on_connect_test(client, userdata, flags, rc):
            if rc == 0:
                connection_result["success"] = True
                connection_result["message"] = "Connection successful"
            else:
                error_messages = {
                    1: "Incorrect protocol version",
                    2: "Invalid client identifier",
                    3: "Server unavailable",
                    4: "Bad username or password",
                    5: "Not authorized"
                }
                connection_result["message"] = error_messages.get(rc, f"Connection failed (error code: {rc})")
            client.disconnect()
        
        def on_disconnect_test(client, userdata, rc):
            pass
        
        # Create test client
        client_id = f"test_client_{uuid.uuid4().hex[:8]}"
        test_client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True
        )
        
        # Set authentication if provided
        if broker.username and broker.password:
            test_client.username_pw_set(broker.username, broker.password)
        
        # Configure TLS if needed
        if broker.protocol == "mqtts":
            tls_kwargs = {}
            if broker.tls_ca_cert_path:
                tls_kwargs["ca_certs"] = broker.tls_ca_cert_path
            if broker.tls_client_cert_path and broker.tls_client_key_path:
                tls_kwargs["certfile"] = broker.tls_client_cert_path
                tls_kwargs["keyfile"] = broker.tls_client_key_path
            
            if tls_kwargs:
                tls_kwargs["tls_version"] = ssl.PROTOCOL_TLSv1_2
                test_client.tls_set(**tls_kwargs)
            # AIToolStack as client should always verify server certificate
            # AIToolStack as client should always verify server certificate (tls_insecure_skip_verify is not applicable)
            test_client.tls_insecure_set(False)
        
        # Set callbacks
        test_client.on_connect = on_connect_test
        test_client.on_disconnect = on_disconnect_test
        
        # Set connection timeout
        test_client.connect(broker.host, broker.port, keepalive=broker.keepalive or 60)
        
        # Wait for connection (with timeout)
        start_time = time.time()
        timeout = 10  # 10 seconds timeout
        test_client.loop_start()
        
        while time.time() - start_time < timeout:
            if connection_result["success"] or connection_result["message"]:
                break
            time.sleep(0.1)
        
        test_client.loop_stop()
        
        if not connection_result["success"]:
            raise HTTPException(
                status_code=400,
                detail=f"MQTT 连接测试失败：{connection_result.get('message', '连接超时')}。只有连接成功才能添加 Broker。"
            )
    except HTTPException:
        raise
    except socket.timeout:
        raise HTTPException(
            status_code=400,
            detail=f"连接超时：无法在 5 秒内连接到 {broker.host}:{broker.port}"
        )
    except socket.gaierror as e:
        raise HTTPException(
            status_code=400,
            detail=f"无法解析主机名 {broker.host}：{str(e)}"
        )
    except Exception as e:
        logger.error(f"Connection test failed during broker creation: {e}", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail=f"连接测试失败：{str(e)}。只有连接成功才能添加 Broker。"
        )
    
    # Connection test passed, create the broker
    try:
        created = external_broker_service.create(broker)
        # Reload MQTT connections to include the new broker
        try:
            mqtt_service.reload_and_reconnect()
        except Exception as e:
            logger.warning(f"Failed to reconnect MQTT clients after creating broker: {e}")
        return created
    except Exception as e:
        logger.error(f"Failed to create external broker: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/system/mqtt/external-brokers/{broker_id}", response_model=ExternalBrokerResponse)
def update_external_broker(broker_id: int, broker: ExternalBrokerUpdate):
    """Update an existing external MQTT broker"""
    updated = external_broker_service.update(broker_id, broker)
    if not updated:
        raise HTTPException(status_code=404, detail=f"External broker with ID {broker_id} not found")
    # Reload MQTT connections to apply changes
    try:
        mqtt_service.reload_and_reconnect()
    except Exception as e:
        logger.warning(f"Failed to reconnect MQTT clients after updating broker: {e}")
    return updated


@router.delete("/system/mqtt/external-brokers/{broker_id}")
def delete_external_broker(broker_id: int):
    """Delete an external MQTT broker"""
    success = external_broker_service.delete(broker_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"External broker with ID {broker_id} not found")
    # Reload MQTT connections to remove the deleted broker
    try:
        mqtt_service.reload_and_reconnect()
    except Exception as e:
        logger.warning(f"Failed to reconnect MQTT clients after deleting broker: {e}")
    return {"message": f"External broker {broker_id} deleted successfully"}


@router.post("/system/mqtt/external-brokers/test")
def test_external_broker_connection(broker: ExternalBrokerCreate):
    """Test connection to an external MQTT broker before adding/updating"""
    import socket
    import time
    import ssl
    import paho.mqtt.client as mqtt
    import uuid
    
    try:
        # First, test TCP connection
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((broker.host, broker.port))
        sock.close()
        
        if result != 0:
            return {
                "success": False,
                "message": f"Cannot connect to {broker.host}:{broker.port}",
                "error": f"TCP connection failed (error code: {result})"
            }
        
        # Try MQTT connection
        test_client = None
        connection_result = {"success": False, "message": ""}
        
        def on_connect_test(client, userdata, flags, rc):
            if rc == 0:
                connection_result["success"] = True
                connection_result["message"] = "Connection successful"
            else:
                error_messages = {
                    1: "Incorrect protocol version",
                    2: "Invalid client identifier",
                    3: "Server unavailable",
                    4: "Bad username or password",
                    5: "Not authorized"
                }
                connection_result["message"] = error_messages.get(rc, f"Connection failed (error code: {rc})")
            client.disconnect()
        
        def on_disconnect_test(client, userdata, rc):
            pass
        
        # Create test client
        client_id = f"test_client_{uuid.uuid4().hex[:8]}"
        test_client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True
        )
        
        # Set authentication if provided
        if broker.username and broker.password:
            test_client.username_pw_set(broker.username, broker.password)
        
        # Configure TLS if needed
        if broker.protocol == "mqtts":
            tls_kwargs = {}
            if broker.tls_ca_cert_path:
                tls_kwargs["ca_certs"] = broker.tls_ca_cert_path
            if broker.tls_client_cert_path and broker.tls_client_key_path:
                tls_kwargs["certfile"] = broker.tls_client_cert_path
                tls_kwargs["keyfile"] = broker.tls_client_key_path
            
            if tls_kwargs:
                tls_kwargs["tls_version"] = ssl.PROTOCOL_TLSv1_2
                test_client.tls_set(**tls_kwargs)
            # AIToolStack as client should always verify server certificate
            # AIToolStack as client should always verify server certificate (tls_insecure_skip_verify is not applicable)
            test_client.tls_insecure_set(False)
        
        # Set callbacks
        test_client.on_connect = on_connect_test
        test_client.on_disconnect = on_disconnect_test
        
        # Set connection timeout
        test_client.connect(broker.host, broker.port, keepalive=broker.keepalive or 60)
        
        # Wait for connection (with timeout)
        start_time = time.time()
        timeout = 10  # 10 seconds timeout
        test_client.loop_start()
        
        while time.time() - start_time < timeout:
            if connection_result["success"] or connection_result["message"]:
                break
            time.sleep(0.1)
        
        test_client.loop_stop()
        
        if connection_result["success"]:
            return {
                "success": True,
                "message": f"成功连接到 {broker.host}:{broker.port}",
            }
        else:
            return {
                "success": False,
                "message": connection_result.get("message", "连接超时"),
                "error": connection_result.get("message", "Connection timeout")
            }
            
    except socket.timeout:
        return {
            "success": False,
            "message": f"连接超时：无法在 5 秒内连接到 {broker.host}:{broker.port}",
            "error": "Connection timeout"
        }
    except socket.gaierror as e:
        return {
            "success": False,
            "message": f"无法解析主机名 {broker.host}",
            "error": f"DNS resolution failed: {str(e)}"
        }
    except Exception as e:
        logger.error(f"Failed to test external broker connection: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"连接测试失败: {str(e)}",
            "error": str(e)
        }


@router.post("/mqtt/test")
def test_mqtt_connection():
    """Test MQTT connection"""
    cfg: MQTTConfig = mqtt_config_service.load_config()

    if not cfg.enabled:
        return {
            "success": False,
            "message": "MQTT service is disabled",
            "error": "MQTT_ENABLED is False",
        }
    
    import socket
    import time
    
    try:
        from backend.config import get_local_ip
        
        # Determine Broker address to test based on runtime config
        if cfg.mode == "builtin":
            broker_host = get_local_ip()
            broker_port = cfg.builtin_tcp_port or settings.MQTT_BUILTIN_PORT
            broker_type = "builtin"
        else:
            broker_host = cfg.host or settings.MQTT_BROKER
            broker_port = cfg.port or settings.MQTT_PORT
            broker_type = "external"
        
        # Test TCP connection
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex((broker_host, broker_port))
        sock.close()
        
        if result == 0:
            # TCP connection successful, try MQTT connection
            import paho.mqtt.client as mqtt
            import uuid
            
            test_client = None
            connection_result = {"success": False, "message": ""}
            
            def on_connect_test(client, userdata, flags, rc):
                connection_result["success"] = (rc == 0)
                if rc == 0:
                    connection_result["message"] = "MQTT connection successful"
                else:
                    connection_result["message"] = f"MQTT connection failed, error code: {rc}"
                client.disconnect()
            
            def on_connect_fail_test(client, userdata):
                connection_result["success"] = False
                connection_result["message"] = "MQTT connection timeout"
            
            try:
                # Explicitly specify MQTT 3.1.1 protocol (aMQTT broker does not support MQTT 5.0)
                test_client = mqtt.Client(
                    client_id=f"test_client_{uuid.uuid4().hex[:8]}",
                    protocol=mqtt.MQTTv311
                )
                test_client.on_connect = on_connect_test
                test_client.on_connect_fail = on_connect_fail_test
                
                # Set authentication based on broker type
                if broker_type == "builtin":
                    # Built-in Broker authentication (if anonymous is disabled)
                    if not cfg.builtin_allow_anonymous and cfg.builtin_username and cfg.builtin_password:
                        test_client.username_pw_set(cfg.builtin_username, cfg.builtin_password)
                elif broker_type == "external":
                    # External Broker authentication
                    if cfg.username and cfg.password:
                        test_client.username_pw_set(cfg.username, cfg.password)
                
                test_client.connect(broker_host, broker_port, keepalive=5)
                test_client.loop_start()
                
                # Wait for connection result (wait up to 3 seconds)
                timeout = 3
                elapsed = 0
                while elapsed < timeout and connection_result["message"] == "":
                    time.sleep(0.1)
                    elapsed += 0.1
                
                test_client.loop_stop()
                test_client.disconnect()
                
                if connection_result["message"] == "":
                    connection_result["message"] = "Connection timeout"
                
                return {
                    "success": connection_result["success"],
                    "message": connection_result["message"],
                    "broker": f"{broker_host}:{broker_port}",
                    "broker_type": broker_type,
                    "tcp_connected": True
                }
            except Exception as e:
                if test_client:
                    try:
                        test_client.loop_stop()
                        test_client.disconnect()
                    except:
                        pass
                return {
                    "success": False,
                    "message": f"MQTT connection test failed: {str(e)}",
                    "broker": f"{broker_host}:{broker_port}",
                    "broker_type": broker_type,
                    "tcp_connected": True
                }
        else:
            error_msg = f"Cannot connect to MQTT Broker ({broker_host}:{broker_port})"
            error_detail = ""
            
            # macOS/Linux error codes
            if result == 61 or result == 111:  # ECONNREFUSED (macOS: 61, Linux: 111)
                error_msg += " - Connection refused"
                error_detail = "MQTT Broker may not be running. Please start MQTT Broker service."
            elif result == 60 or result == 110:  # ETIMEDOUT (macOS: 60, Linux: 110)
                error_msg += " - Connection timeout"
                error_detail = "Network connection timeout, please check network and firewall settings."
            elif result == 64 or result == 113:  # EHOSTUNREACH (macOS: 64, Linux: 113)
                error_msg += " - Host unreachable"
                error_detail = "Cannot reach MQTT Broker address, please check Broker address configuration."
            elif result == 51:  # ENETUNREACH (macOS)
                error_msg += " - Network unreachable"
                error_detail = "Network unreachable, please check network connection and Broker address."
            else:
                error_msg += f" - Error code: {result}"
                error_detail = "Please check MQTT Broker configuration and running status."
            
            return {
                "success": False,
                "message": error_msg,
                "detail": error_detail,
                "broker": f"{broker_host}:{broker_port}",
                "broker_type": broker_type,
                "tcp_connected": False,
                "error": "TCP connection failed",
                "error_code": result
            }
    except socket.gaierror as e:
        return {
            "success": False,
            "message": f"Cannot resolve MQTT Broker address: {str(e)}",
            "broker": f"{cfg.host or settings.MQTT_BROKER}:{cfg.port or settings.MQTT_PORT}",
            "tcp_connected": False,
            "error": "DNS resolution failed"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Connection test failed: {str(e)}",
            "broker": f"{cfg.host or settings.MQTT_BROKER}:{cfg.port or settings.MQTT_PORT}",
            "tcp_connected": False,
            "error": str(e)
    }


@router.get("/device/bootstrap")
def get_device_bootstrap(request: Request):
    """Provide device-side bootstrap info for MQTT connection.

    Backward compatible:
    - `broker_host` / `broker_port` / `broker_type` keep current meaning: the *preferred*
      broker according to current runtime config.
    - New fields `builtin_broker` and `external_broker` (when available) expose
      detailed information for multiple MQTT Brokers where the same application
      protocol is supported, so devices can choose the most convenient one
      (e.g. local vs cloud) while still speaking the same topic/payload format.
    """
    from backend.config import get_mqtt_broker_host

    cfg: MQTTConfig = mqtt_config_service.load_config()
    server_ip = get_mqtt_broker_host(request)

    # Preferred broker for backward compatibility (single entry)
    # Default to built-in broker, use external if external is enabled and configured
    if cfg.external_enabled and (cfg.external_host or cfg.external_port):
        preferred_broker_host = cfg.external_host or server_ip
        preferred_broker_port = cfg.external_port or settings.MQTT_PORT
        preferred_broker_type = "external"
    else:
        preferred_broker_host = server_ip
        preferred_broker_port = cfg.builtin_tcp_port or settings.MQTT_BUILTIN_PORT
        preferred_broker_type = "builtin"

    # Built-in broker information
    builtin_broker = {
        "enabled": bool(cfg.enabled),
        "host": server_ip,
        "port": cfg.builtin_tcp_port or settings.MQTT_BUILTIN_PORT,
        "protocol": cfg.builtin_protocol,
    }

    # External broker information (only when explicitly enabled and configured)
    external_broker = None
    if cfg.external_enabled and (cfg.external_host or cfg.external_port):
        external_broker = {
            "enabled": bool(cfg.enabled and cfg.external_enabled),
            "host": cfg.external_host or server_ip,
            "port": cfg.external_port or settings.MQTT_PORT,
            "protocol": cfg.external_protocol,
            "username": cfg.external_username,
            "password": None,  # Never expose password to devices in bootstrap
        }

    payload = {
        "enabled": cfg.enabled,
        "mode": preferred_broker_type,  # For backward compatibility, use preferred broker type
        "protocol": cfg.builtin_protocol,  # Use builtin protocol as default for backward compatibility
        # Preferred broker (for backward compatibility with existing devices)
        "broker_type": preferred_broker_type,
        "broker_host": preferred_broker_host,
        "broker_port": preferred_broker_port,
        # Rich multi-broker info (for newer devices / tooling)
        "builtin_broker": builtin_broker,
        "external_broker": external_broker,
        "upload_topic_format": settings.MQTT_UPLOAD_TOPIC,
        "response_topic_prefix": settings.MQTT_RESPONSE_TOPIC_PREFIX,
        "server_ip": server_ip,
        "server_port": settings.PORT,
    }

    return payload


# ========== Device Management ==========

@router.get("/devices", response_model=List[DeviceOut])
def list_devices(db: Session = Depends(get_db)):
    """List all devices that have reported data or been registered."""
    from backend.models.database import DeviceReport
    
    # Query devices with their latest report time
    devices = (
        db.query(Device)
        .order_by(Device.created_at.desc())
        .all()
    )
    
    # For each device, get the latest report time
    result = []
    for device in devices:
        # Get the latest report time for this device
        latest_report = (
            db.query(DeviceReport)
            .filter(DeviceReport.device_id == device.id)
            .order_by(DeviceReport.created_at.desc())
            .first()
        )
        
        # Create DeviceOut with last_seen from latest report if available
        device_out = DeviceOut.from_orm_device(device)
        if latest_report:
            device_out.last_seen = latest_report.created_at
        result.append(device_out)
    
    return result


@router.get("/devices/{device_id}", response_model=DeviceOut)
def get_device(device_id: str, db: Session = Depends(get_db)):
    """Get device details by id."""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return DeviceOut.from_orm_device(device)


class DeviceUpdate(BaseModel):
    """Device update request model"""
    name: Optional[str] = None


@router.patch("/devices/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: str,
    payload: DeviceUpdate,
    db: Session = Depends(get_db),
):
    """Update device information.
    
    - Currently supports updating device name.
    - Manually set names will be preserved and not overwritten by device reports.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Update name if provided
    if payload.name is not None:
        # Set name to None if empty string, otherwise use the provided name
        device.name = payload.name.strip() if payload.name and payload.name.strip() else None
        # Mark that this is a manually set name by storing a flag in extra_info
        # This flag will be checked in _upsert_device_from_payload to prevent overwriting
        if device.extra_info:
            try:
                extra_info = json.loads(device.extra_info)
            except (json.JSONDecodeError, TypeError):
                extra_info = {}
        else:
            extra_info = {}
        extra_info['name_manually_set'] = True
        device.extra_info = json.dumps(extra_info)
    
    db.commit()
    db.refresh(device)
    logger.info(f"Device {device_id} updated: name={device.name}")
    return DeviceOut.from_orm_device(device)


@router.post("/devices", response_model=DeviceWithTopic)
def create_device(payload: DeviceCreate, db: Session = Depends(get_db)):
    """Manually create/register a device.

    - System will auto-generate a unique device_id.
    - Returns the MQTT uplink topic so that user can configure the device.
    - Optionally bind the device to one or more projects at creation time.
    """
    # Generate a short unique device ID (8-character hex string)
    # Example: "a1b2c3d4"
    # Ensure no collision in the devices table.
    while True:
        candidate = uuid.uuid4().hex[:8]
        existing = db.query(Device).filter(Device.id == candidate).first()
        if not existing:
            device_id = candidate
            break
    now = datetime.utcnow()

    # Prepare extra_info with name_manually_set flag if name is provided
    extra_info_dict = {}
    if payload.extra_info:
        try:
            extra_info_dict = json.loads(payload.extra_info)
        except (json.JSONDecodeError, TypeError):
            extra_info_dict = {}
    
    # Mark name as manually set if user provided a name
    if payload.name and payload.name.strip():
        extra_info_dict['name_manually_set'] = True
    
    extra_info_str = json.dumps(extra_info_dict) if extra_info_dict else None

    # Create device ORM instance
    device = Device(
        id=device_id,
        name=payload.name or device_id,
        type=payload.type or "Other",
        model=payload.model,
        serial_number=payload.serial_number,
        mac_address=payload.mac_address,
        status="offline",
        last_seen=None,
        last_ip=None,
        firmware_version=None,
        hardware_version=None,
        power_supply_type=None,
        last_report=None,
        extra_info=extra_info_str,
    )

    # Optional: bind to projects
    if payload.project_ids:
        projects = (
            db.query(Project)
            .filter(Project.id.in_(payload.project_ids))
            .all()
        )
        if projects:
            device.projects = projects

    db.add(device)
    db.commit()
    db.refresh(device)

    uplink_topic = f"device/{device_id}/uplink"

    base_out = DeviceOut.from_orm_device(device)
    return DeviceWithTopic(**base_out.dict(), uplink_topic=uplink_topic)


@router.post("/devices/{device_id}/bind-project", response_model=DeviceOut)
def bind_device_project(
    device_id: str,
    payload: DeviceBindProjectRequest,
    db: Session = Depends(get_db),
):
    """Bind a device to a project (supports multiple projects).

    - Device can be bound to multiple projects simultaneously.
    - If device is already bound to the project, this is a no-op.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    project = db.query(Project).filter(Project.id == payload.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Add project to device's projects if not already bound
    if project not in device.projects:
        device.projects.append(project)
        db.commit()
        logger.info(f"Device {device_id} bound to project {payload.project_id}")
    else:
        logger.info(f"Device {device_id} already bound to project {payload.project_id}")

    db.refresh(device)
    return DeviceOut.from_orm_device(device)


@router.post("/devices/{device_id}/unbind-project", response_model=DeviceOut)
def unbind_device_project(
    device_id: str,
    payload: DeviceUnbindProjectRequest,
    db: Session = Depends(get_db),
):
    """Unbind a device from a project.

    - Removes the binding between device and project.
    - If device is not bound to the project, this is a no-op.
    """
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    project = db.query(Project).filter(Project.id == payload.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Remove project from device's projects if bound
    if project in device.projects:
        device.projects.remove(project)
        db.commit()
        logger.info(f"Device {device_id} unbound from project {payload.project_id}")
    else:
        logger.info(f"Device {device_id} not bound to project {payload.project_id}")

    db.refresh(device)
    return DeviceOut.from_orm_device(device)


@router.get("/projects/{project_id}/devices", response_model=List[DeviceOut])
def list_project_devices(project_id: str, db: Session = Depends(get_db)):
    """List all devices bound to a specific project."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    devices = project.devices if project.devices else []
    return [DeviceOut.from_orm_device(d) for d in devices]


class DeviceReportOut(BaseModel):
    """Device report response model"""
    id: int
    device_id: str
    report_data: str
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/devices/{device_id}/reports", response_model=List[DeviceReportOut])
def list_device_reports(
    device_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """List device report history with pagination."""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    reports = (
        db.query(DeviceReport)
        .filter(DeviceReport.device_id == device_id)
        .order_by(DeviceReport.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return reports


@router.delete("/devices/{device_id}")
def delete_device(device_id: str, db: Session = Depends(get_db)):
    """Delete a device, all its reports, and associated certificate if exists."""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Delete associated device certificate if exists
    # Device certificates use device_id as common_name (CN)
    try:
        from pathlib import Path
        from urllib.parse import unquote
        
        safe_cn = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in unquote(device_id))
        certs_dir = Path("/mosquitto/config/certs")
        
        cert_path = certs_dir / f"client-{safe_cn}.crt"
        key_path = certs_dir / f"client-{safe_cn}.key"
        csr_path = certs_dir / f"client-{safe_cn}.csr"
        
        # Check if certificate exists
        if cert_path.exists():
            # Read certificate content before deletion (needed for CRL)
            cert_content = cert_path.read_bytes()
            
            # Remove device CN from password file (required when use_identity_as_username is enabled)
            _remove_device_from_password_file(device_id)
            
            # Delete certificate files
            deleted_files = []
            if cert_path.exists():
                cert_path.unlink()
                deleted_files.append(str(cert_path))
            if key_path.exists():
                key_path.unlink()
                deleted_files.append(str(key_path))
            if csr_path.exists():
                csr_path.unlink()
                deleted_files.append(str(csr_path))
            
            logger.info(f"Deleted certificate files for device {device_id}: {deleted_files}")
            
            # Add certificate to CRL for immediate revocation
            if cert_content:
                try:
                    _add_certificate_to_crl(cert_content, certs_dir)
                    logger.info(f"Added certificate for device '{device_id}' to CRL")
                except Exception as e:
                    logger.warning(f"Failed to add certificate to CRL: {e}. Certificate deleted but may still be usable until Mosquitto restart.")
            
            # Update mosquitto.conf to include crlfile if mTLS is enabled and CRL is valid
            try:
                cfg = mqtt_config_service.load_config()
                if cfg.builtin_protocol == "mqtts" and cfg.builtin_tls_require_client_cert:
                    crl_file = Path("/mosquitto/config/certs/revoked.crl")
                    if _is_valid_crl_file(crl_file):
                        _ensure_crlfile_in_mosquitto_conf()
                        # Restart Mosquitto to apply CRL changes (CRL cannot be reloaded via SIGHUP)
                        try:
                            import subprocess
                            subprocess.run(
                                ["docker", "restart", "camthink-mosquitto"],
                                check=True,
                                timeout=30,
                            )
                            logger.info("Mosquitto restarted to apply CRL changes after device deletion")
                        except Exception as e:
                            logger.warning(f"Failed to restart Mosquitto after certificate deletion: {e}")
            except Exception as e:
                logger.warning(f"Failed to update mosquitto.conf with CRL: {e}")
    except Exception as e:
        # Log error but don't fail device deletion if certificate deletion fails
        logger.warning(f"Failed to delete certificate for device {device_id}: {e}")
    
    # Delete device (cascade will delete all reports due to relationship)
    db.delete(device)
    db.commit()
    logger.info(f"Device {device_id} deleted")
    
    return {"message": "Device deleted successfully"}

