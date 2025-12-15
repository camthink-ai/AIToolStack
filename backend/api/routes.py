"""API route definitions"""
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional
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

from backend.models.database import get_db, Project, Image, Class, Annotation
from backend.services.websocket_manager import websocket_manager
from backend.services.mqtt_service import mqtt_service
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
    
    class Config:
        from_attributes = True

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
    
    class Config:
        protected_namespaces = ()  # Fix model_size field warning


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
    """Export trained model"""
    
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
    
    return FileResponse(
        path=model_path,
        filename=Path(model_path).name,
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

            cfg = {
                "model": {
                    "name": f"{Path(model_path).stem}_ne301",
                    "uc": str(project_id),
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
        filename = file_path.name
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
        filename = file_path.name
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
        filename = file_path.name
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
        filename = model_bin_path.name
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


# ========== MQTT Service Management ==========
@router.get("/mqtt/status")
def get_mqtt_status(request: Request):
    """Get MQTT service status"""
    from backend.config import get_mqtt_broker_host, get_local_ip
    
    if settings.MQTT_USE_BUILTIN_BROKER:
        # Use new function to get externally visible host IP (not container internal IP)
        broker = get_mqtt_broker_host(request)
        port = settings.MQTT_BUILTIN_PORT
        broker_type = "builtin"
    else:
        broker = settings.MQTT_BROKER
        port = settings.MQTT_PORT
        broker_type = "external"
    
    # Get server IP (for display in project information)
    # get_mqtt_broker_host already contains logic to get from request headers, use it directly here
    server_ip = get_mqtt_broker_host(request)
    
    return {
        "enabled": settings.MQTT_ENABLED,
        "use_builtin": settings.MQTT_USE_BUILTIN_BROKER if settings.MQTT_ENABLED else False,
        "broker_type": broker_type if settings.MQTT_ENABLED else None,
        "connected": mqtt_service.is_connected if settings.MQTT_ENABLED else False,
        "broker": broker if settings.MQTT_ENABLED else None,
        "port": port if settings.MQTT_ENABLED else None,
        "topic": settings.MQTT_UPLOAD_TOPIC if settings.MQTT_ENABLED else None,
        "server_ip": server_ip,  # Server IP address
        "server_port": settings.PORT  # Server port
    }


@router.post("/mqtt/test")
def test_mqtt_connection():
    """Test MQTT connection"""
    if not settings.MQTT_ENABLED:
        return {
            "success": False,
            "message": "MQTT service is disabled",
            "error": "MQTT_ENABLED is False"
        }
    
    import socket
    import time
    
    try:
        from backend.config import get_local_ip
        
        # Determine Broker address to test
        if settings.MQTT_USE_BUILTIN_BROKER:
            broker_host = get_local_ip()
            broker_port = settings.MQTT_BUILTIN_PORT
        else:
            broker_host = settings.MQTT_BROKER
            broker_port = settings.MQTT_PORT
        
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
                
                # Built-in Broker does not require authentication, external Broker does
                if not settings.MQTT_USE_BUILTIN_BROKER:
                    if settings.MQTT_USERNAME and settings.MQTT_PASSWORD:
                        test_client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
                
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
                    "broker_type": "builtin" if settings.MQTT_USE_BUILTIN_BROKER else "external",
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
                    "broker_type": "builtin" if settings.MQTT_USE_BUILTIN_BROKER else "external",
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
                "broker_type": "builtin" if settings.MQTT_USE_BUILTIN_BROKER else "external",
                "tcp_connected": False,
                "error": "TCP connection failed",
                "error_code": result
            }
    except socket.gaierror as e:
        return {
            "success": False,
            "message": f"Cannot resolve MQTT Broker address: {str(e)}",
            "broker": f"{settings.MQTT_BROKER}:{settings.MQTT_PORT}",
            "tcp_connected": False,
            "error": "DNS resolution failed"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Connection test failed: {str(e)}",
            "broker": f"{settings.MQTT_BROKER}:{settings.MQTT_PORT}",
            "tcp_connected": False,
            "error": str(e)
    }

