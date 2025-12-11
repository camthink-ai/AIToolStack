"""API 路由定义"""
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
from backend.config import settings
from PIL import Image as PILImage
import io
import logging


router = APIRouter()
logger = logging.getLogger(__name__)


# ========== Pydantic 模型 ==========

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
        """从 ORM 对象创建响应模型"""
        return cls(
            id=obj.id,
            name=obj.name,
            description=obj.description or "",
            created_at=obj.created_at.isoformat() if obj.created_at else None,
            updated_at=obj.updated_at.isoformat() if obj.updated_at else None
        )


class TrainingRequest(BaseModel):
    model_size: str = 'n'  # 'n', 's', 'm', 'l', 'x'
    epochs: int = 100
    imgsz: int = 640
    batch: int = 16
    device: Optional[str] = None
    # 学习率相关
    lr0: Optional[float] = None  # 初始学习率
    lrf: Optional[float] = None  # 最终学习率
    # 优化器相关
    optimizer: Optional[str] = None  # 'SGD', 'Adam', 'AdamW', 'RMSProp', 'auto'
    momentum: Optional[float] = None  # 动量
    weight_decay: Optional[float] = None  # 权重衰减
    # 训练控制
    patience: Optional[int] = None  # 早停耐心值
    workers: Optional[int] = None  # 数据加载线程数
    val: Optional[bool] = None  # 是否进行验证
    save_period: Optional[int] = None  # 保存周期（-1表示不保存中间模型）
    amp: Optional[bool] = None  # 是否使用混合精度训练
    # 数据增强（高级选项）
    hsv_h: Optional[float] = None  # HSV色调增强
    hsv_s: Optional[float] = None  # HSV饱和度增强
    hsv_v: Optional[float] = None  # HSV明度增强
    degrees: Optional[float] = None  # 旋转角度
    translate: Optional[float] = None  # 平移
    scale: Optional[float] = None  # 缩放
    shear: Optional[float] = None  # 剪切
    perspective: Optional[float] = None  # 透视变换
    flipud: Optional[float] = None  # 上下翻转概率
    fliplr: Optional[float] = None  # 左右翻转概率
    mosaic: Optional[float] = None  # Mosaic增强概率
    mixup: Optional[float] = None  # Mixup增强概率
    
    class Config:
        protected_namespaces = ()  # 解决 model_size 字段警告


class ClassCreate(BaseModel):
    name: str
    color: str  # HEX 颜色
    shortcut_key: str = None


class AnnotationCreate(BaseModel):
    type: str  # bbox, polygon, keypoint
    data: dict  # 标注数据
    class_id: int


class AnnotationUpdate(BaseModel):
    data: dict = None
    class_id: int = None


# ========== 项目相关 ==========

@router.post("/projects", response_model=ProjectResponse)
def create_project(project: ProjectCreate, db: Session = Depends(get_db)):
    """创建新项目"""
    project_id = str(uuid.uuid4())
    
    db_project = Project(
        id=project_id,
        name=project.name.strip(),
        description=project.description.strip() if project.description else ""
    )
    
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    
    # 创建项目目录
    (settings.DATASETS_ROOT / project_id / "raw").mkdir(parents=True, exist_ok=True)
    
    return ProjectResponse.from_orm(db_project)


@router.get("/projects", response_model=List[ProjectResponse])
def list_projects(db: Session = Depends(get_db)):
    """列出所有项目"""
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return [ProjectResponse.from_orm(p) for p in projects]


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, db: Session = Depends(get_db)):
    """获取项目详情"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse.from_orm(project)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    """删除项目"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    db.delete(project)
    db.commit()
    
    # 删除项目目录
    project_dir = settings.DATASETS_ROOT / project_id
    if project_dir.exists():
        import shutil
        shutil.rmtree(project_dir)
    
    return {"message": "Project deleted"}


# ========== 类别相关 ==========

@router.post("/projects/{project_id}/classes")
def create_class(project_id: str, class_data: ClassCreate, db: Session = Depends(get_db)):
    """创建类别"""
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
    """列出项目所有类别"""
    classes = db.query(Class).filter(Class.project_id == project_id).all()
    return classes


@router.delete("/projects/{project_id}/classes/{class_id}")
def delete_class(project_id: str, class_id: int, db: Session = Depends(get_db)):
    """删除类别"""
    db_class = db.query(Class).filter(
        Class.id == class_id,
        Class.project_id == project_id
    ).first()
    
    if not db_class:
        raise HTTPException(status_code=404, detail="Class not found")
    
    # 检查是否有标注使用此类别
    annotation_count = db.query(Annotation).filter(Annotation.class_id == class_id).count()
    if annotation_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete class: {annotation_count} annotation(s) are using this class"
        )
    
    db.delete(db_class)
    db.commit()
    
    return {"message": "Class deleted"}


# ========== 图像相关 ==========

@router.post("/projects/{project_id}/images/upload")
async def upload_image(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """上传图像文件到项目"""
    # 校验项目是否存在
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 校验文件类型
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {file_ext}。支持的格式: {', '.join(allowed_extensions)}"
        )
    
    try:
        # 读取文件内容
        file_content = await file.read()
        
        # 校验文件大小
        size_mb = len(file_content) / (1024 * 1024)
        if size_mb > settings.MAX_IMAGE_SIZE_MB:
            raise HTTPException(
                status_code=400,
                detail=f"文件太大: {size_mb:.2f}MB (最大: {settings.MAX_IMAGE_SIZE_MB}MB)"
            )
        
        # 验证是否为有效图像并获取尺寸
        try:
            img = PILImage.open(io.BytesIO(file_content))
            img_width, img_height = img.size
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"无效的图像文件: {str(e)}")
        
        # 生成存储路径
        project_dir = settings.DATASETS_ROOT / project_id / "raw"
        project_dir.mkdir(parents=True, exist_ok=True)
        
        # 处理文件名冲突和中文文件名
        original_filename = file.filename or f"image_{uuid.uuid4().hex[:8]}{file_ext}"
        # 处理中文文件名：使用UUID避免编码问题，但保留原始扩展名
        filename_stem = f"img_{uuid.uuid4().hex[:8]}"
        filename = f"{filename_stem}{file_ext}"
        file_path = project_dir / filename
        
        # 如果文件名冲突，添加时间戳
        counter = 0
        while file_path.exists():
            counter += 1
            filename = f"{filename_stem}_{counter}{file_ext}"
            file_path = project_dir / filename
        
        # 保存文件（如果图像格式需要转换，则在保存时转换）
        if img.mode != 'RGB' and file_ext in ['.jpg', '.jpeg']:
            # JPG格式需要RGB模式
            img_rgb = img.convert('RGB')
            img_rgb.save(file_path, 'JPEG', quality=95)
        else:
            # 其他格式直接保存原始内容
            file_path.write_bytes(file_content)
        
        # 生成相对路径（仅包含 raw/filename，不包含 project_id）
        relative_path = f"raw/{filename}"
        
        # 存入数据库（存储原始文件名和相对路径）
        db_image = Image(
            project_id=project_id,
            filename=original_filename,  # 存储原始文件名
            path=relative_path,  # 存储相对路径 raw/filename
            width=img_width,
            height=img_height,
            status="UNLABELED",
            source="UPLOAD"
        )
        db.add(db_image)
        db.commit()
        db.refresh(db_image)
        
        # 通过 WebSocket 通知前端
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
            "message": "图像上传成功"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Upload] Error uploading image: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@router.get("/projects/{project_id}/images")
def list_images(project_id: str, db: Session = Depends(get_db)):
    """列出项目所有图像"""
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
    """获取图像详情（含标注）"""
    image = db.query(Image).filter(
        Image.id == image_id,
        Image.project_id == project_id
    ).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    # 获取标注
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
    """删除图像"""
    image = db.query(Image).filter(
        Image.id == image_id,
        Image.project_id == project_id
    ).first()
    
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    # 先删除关联的标注数据，避免残留孤立记录
    annotations = db.query(Annotation).filter(Annotation.image_id == image_id).all()
    for ann in annotations:
        db.delete(ann)
    
    # 删除图像文件
    image_path = settings.DATASETS_ROOT / project_id / image.path
    if image_path.exists():
        try:
            image_path.unlink()
            print(f"[Delete] Deleted image file: {image_path}")
        except Exception as e:
            print(f"[Delete] Error deleting file {image_path}: {e}")
            # 继续删除数据库记录，即使文件删除失败
    
    # 删除数据库记录（级联删除标注）
    db.delete(image)
    db.commit()
    
    # 通过 WebSocket 通知前端
    websocket_manager.broadcast_project_update(project_id, {
        "type": "image_deleted",
        "image_id": image_id
    })
    
    return {"message": "Image deleted"}


# ========== 标注相关 ==========

@router.post("/images/{image_id}/annotations")
def create_annotation(image_id: int, annotation: AnnotationCreate, db: Session = Depends(get_db)):
    """创建标注"""
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
    
    # 更新图像状态
    was_unlabeled = image.status == "UNLABELED"
    image.status = "LABELED"
    
    db.commit()
    db.refresh(db_annotation)
    
    # 如果状态从 UNLABELED 变为 LABELED，通知前端更新图像列表
    if was_unlabeled:
        websocket_manager.broadcast_project_update(project_id, {
            "type": "image_status_updated",
            "image_id": image_id,
            "status": "LABELED"
        })
    
    return db_annotation


@router.put("/annotations/{annotation_id}")
def update_annotation(annotation_id: int, annotation: AnnotationUpdate, db: Session = Depends(get_db)):
    """更新标注"""
    db_ann = db.query(Annotation).filter(Annotation.id == annotation_id).first()
    if not db_ann:
        raise HTTPException(status_code=404, detail="Annotation not found")
    
    image = db.query(Image).filter(Image.id == db_ann.image_id).first()
    project_id = image.project_id if image else None
    
    if annotation.data is not None:
        db_ann.data = json.dumps(annotation.data)
    
    if annotation.class_id is not None:
        db_ann.class_id = annotation.class_id
    
    # 确保图像状态为 LABELED（如果之前不是）
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
    """删除标注"""
    db_ann = db.query(Annotation).filter(Annotation.id == annotation_id).first()
    if not db_ann:
        raise HTTPException(status_code=404, detail="Annotation not found")
    
    image_id = db_ann.image_id
    image = db.query(Image).filter(Image.id == image_id).first()
    project_id = image.project_id if image else None
    
    db.delete(db_ann)
    
    # 检查是否还有标注，如果没有则更新状态
    remaining = db.query(Annotation).filter(Annotation.image_id == image_id).count()
    status_changed = False
    if remaining == 0:
        if image and image.status == "LABELED":
            image.status = "UNLABELED"
            status_changed = True
    
    db.commit()
    
    # 通过 WebSocket 通知前端
    if project_id:
        from backend.services.websocket_manager import websocket_manager
        websocket_manager.broadcast_project_update(project_id, {
            "type": "annotation_deleted",
            "annotation_id": annotation_id,
            "image_id": image_id
        })
        
        # 如果状态改变，也通知图像状态更新
        if status_changed:
            websocket_manager.broadcast_project_update(project_id, {
                "type": "image_status_updated",
                "image_id": image_id,
                "status": "UNLABELED"
        })
    
    return {"message": "Annotation deleted"}


# ========== WebSocket ==========
# 注意：WebSocket 路由不在 router 中注册，需要在 main.py 中单独注册
# 这样路径就不会有 /api 前缀


# ========== 图像文件服务 ==========

@router.get("/images/{project_id}/{image_path:path}")
def get_image_file(project_id: str, image_path: str):
    """获取图像文件"""
    import os
    from pathlib import Path
    
    print(f"[Image] Request received: project_id={project_id}, image_path={image_path}")
    
    # image_path 应该是 raw/filename 格式
    # 移除可能的 project_id 前缀（兼容旧数据）
    if image_path.startswith(f"{project_id}/"):
        image_path = image_path[len(project_id) + 1:]
    
    # 确保路径以 raw/ 开头
    if not image_path.startswith("raw/"):
        # 如果路径不包含 raw/，可能是旧格式，尝试添加
        image_path = f"raw/{image_path}"
    
    # 构建文件路径
    file_path = settings.DATASETS_ROOT / project_id / image_path
    
    # 规范化路径，处理可能的路径遍历攻击
    try:
        resolved_path = file_path.resolve()
        datasets_root = settings.DATASETS_ROOT.resolve()
        # 确保解析后的路径在数据集根目录下
        resolved_path.relative_to(datasets_root)
    except ValueError:
        print(f"[Image] Security check failed: {resolved_path} not under {datasets_root}")
        raise HTTPException(status_code=403, detail="Access denied: Invalid path")
    
    print(f"[Image] Resolved path: {resolved_path}")
    print(f"[Image] Path exists: {resolved_path.exists()}")
    print(f"[Image] DATASETS_ROOT: {datasets_root}")
    
    if not resolved_path.exists():
        # 尝试列出目录内容以便调试
        project_dir = settings.DATASETS_ROOT / project_id / "raw"
        if project_dir.exists():
            files = list(project_dir.glob("*"))
            print(f"[Image] Files in raw dir: {[f.name for f in files]}")
        else:
            print(f"[Image] Raw directory does not exist: {project_dir}")
        raise HTTPException(status_code=404, detail=f"Image not found: {image_path} (resolved: {resolved_path})")
    
    # 确保是文件而不是目录
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="Path is not a file")
    
    return FileResponse(str(resolved_path))


# ========== YOLO 导出 ==========

@router.post("/projects/{project_id}/export/yolo")
def export_yolo(project_id: str, db: Session = Depends(get_db)):
    """导出项目为 YOLO 格式"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 获取所有图像和标注
    images = db.query(Image).filter(Image.project_id == project_id).all()
    classes = db.query(Class).filter(Class.project_id == project_id).all()
    
    # 构建导出数据
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
    
    # 导出
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
    """下载 YOLO 格式数据集 zip 包"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    output_dir = settings.DATASETS_ROOT / project_id / "yolo_export"
    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="YOLO export not found. Please export first.")
    
    # 创建临时 zip 文件
    zip_path = settings.DATASETS_ROOT / project_id / f"{project.name}_yolo_dataset.zip"
    
    def generate_zip():
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in output_dir.rglob('*'):
                if file_path.is_file():
                    arcname = file_path.relative_to(output_dir)
                    zipf.write(file_path, arcname)
        
        with open(zip_path, 'rb') as f:
            yield from f
        
        # 清理临时文件
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
    """导出完整数据集 zip 包（包含所有图像和标注）"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    images = db.query(Image).filter(Image.project_id == project_id).all()
    classes = db.query(Class).filter(Class.project_id == project_id).all()
    
    if not images:
        raise HTTPException(status_code=400, detail="No images in project")
    
    # 创建临时 zip 文件
    zip_path = settings.DATASETS_ROOT / project_id / f"{project.name}_dataset.zip"
    
    def generate_zip():
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 添加类别信息
            classes_info = {
                "classes": [{"id": c.id, "name": c.name, "color": c.color} for c in classes]
            }
            zipf.writestr("classes.json", json.dumps(classes_info, ensure_ascii=False, indent=2))
            
            # 添加图像和标注
            for img in images:
                # 添加图像文件
                img_path = settings.DATASETS_ROOT / project_id / img.path
                if img_path.exists():
                    zipf.write(img_path, f"images/{img.filename}")
                
                # 获取标注
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
                    
                    # 保存标注为 JSON
                    ann_filename = Path(img.filename).stem + ".json"
                    zipf.writestr(f"annotations/{ann_filename}", json.dumps(ann_list, ensure_ascii=False, indent=2))
        
        with open(zip_path, 'rb') as f:
            yield from f
        
        # 清理临时文件
        if zip_path.exists():
            zip_path.unlink()
    
    return StreamingResponse(
        generate_zip(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={project.name}_dataset.zip"
        }
    )


# ========== MQTT 服务管理 ==========

# ========== 模型训练 ==========

@router.post("/projects/{project_id}/train")
def start_training(project_id: str, request: TrainingRequest, db: Session = Depends(get_db)):
    """启动模型训练：自动按当前项目数据导出最新 YOLO 数据集再训练"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 获取类别
    classes = db.query(Class).filter(Class.project_id == project_id).all()
    if len(classes) == 0:
        raise HTTPException(
            status_code=400,
            detail="No classes found. Please create at least one class."
        )
    
    # 准备导出数据（使用当前数据库中的最新数据）
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
    
    # 导出 YOLO 数据集（覆盖旧的 yolo_export）
    yolo_export_dir = settings.DATASETS_ROOT / project_id / "yolo_export"
    try:
        YOLOExporter.export_project(project_data, yolo_export_dir, settings.DATASETS_ROOT)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"自动导出数据集失败: {str(e)}")
    
    # data.yaml 路径
    data_yaml = yolo_export_dir / "data.yaml"
    if not data_yaml.exists():
        raise HTTPException(status_code=500, detail="自动导出后缺少 data.yaml")
    
    # 启动训练，使用最新导出的数据集
    try:
        training_info = training_service.start_training(
            project_id=project_id,
            dataset_path=yolo_export_dir,
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
        return training_info
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start training: {str(e)}")


@router.get("/projects/{project_id}/train/records")
def get_training_records(project_id: str, db: Session = Depends(get_db)):
    """获取项目的所有训练记录"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    records = training_service.get_training_records(project_id)
    # 只返回基本信息，不包含完整日志
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
    """获取训练状态"""
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
    """获取训练日志"""
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
    """导出训练好的模型"""
    
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
    ne301: bool = Query(True, description="是否生成 NE301 设备量化模型（默认勾选）"),
    db: Session = Depends(get_db)
):
    """
    导出 TFLite 量化模型（默认 int8，imgsz=256，fraction=0.2）
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

    # data.yaml 路径（用于校准/类别信息）
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
        # export_path 可能是 Path 或 str
        export_path = Path(export_path)

        # NE301 相关变量（确保在正确的作用域中定义）
        ne301_path: Optional[str] = None
        ne301_model_bin_path: Optional[str] = None
        ne301_json_path: Optional[str] = None
        
        if ne301:
            # 追加 NE301 量化步骤：生成配置并调用 stm32ai 脚本
            quant_dir = Path(__file__).resolve().parent.parent / "quantization"
            script_path = quant_dir / "tflite_quant.py"
            if not script_path.exists():
                raise HTTPException(status_code=500, detail="缺少 NE301 量化脚本，请检查 backend/quantization/tflite_quant.py")

            # SavedModel 目录：Ultralytics tflite 导出后返回的路径在 best_saved_model 下
            saved_model_dir = export_path.parent  # e.g. .../weights/best_saved_model
            print(f"[NE301] export_path={export_path} saved_model_dir={saved_model_dir}")
            if not saved_model_dir.exists():
                raise HTTPException(status_code=500, detail="未找到 SavedModel 目录，无法执行 NE301 量化")

            # 校准集默认使用导出的 YOLO 数据集 val（不存在则退回 train）
            calib_dir = settings.DATASETS_ROOT / project_id / "yolo_export" / "images" / "val"
            if not calib_dir.exists():
                calib_dir = settings.DATASETS_ROOT / project_id / "yolo_export" / "images" / "train"
            if not calib_dir.exists():
                raise HTTPException(status_code=400, detail="校准集不存在，无法执行 NE301 量化，请先导出数据集")

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

            # hydra config_name 传入文件名去掉扩展（例如 user_config_quant）
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
                    detail=f"NE301 量化失败: {proc.stderr or proc.stdout}",
                )

            export_dir = Path(cfg["quantization"]["export_path"])
            tflites = sorted(
                export_dir.glob("*.tflite"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not tflites:
                logger.error("[NE301] quantized_models 目录下未找到 tflite: %s", export_dir)
                raise HTTPException(
                    status_code=500,
                    detail="NE301 量化完成但未找到生成的 tflite 文件",
                )
            ne301_path = str(tflites[0])
            print(f"[NE301] quantized tflite ready: {ne301_path}")
            
            # 验证文件是否真的存在
            if not Path(ne301_path).exists():
                logger.error(f"[NE301] TFLite 文件不存在: {ne301_path}")
                raise HTTPException(status_code=500, detail=f"NE301 TFLite 文件生成失败: {ne301_path}")

        # 如果需要生成 NE301 JSON 配置和编译，先确保 JSON 文件保存（即使编译失败也要保存）
        if ne301 and ne301_path:
            # 先保存 JSON 配置文件（这一步必须在 try-except 外面，确保即使后续步骤失败也能保存）
            try:
                from backend.utils.ne301_export import (
                    generate_ne301_json_config,
                    _convert_to_json_serializable
                )
                
                # 读取 data.yaml 获取类别信息
                with open(data_yaml, "r", encoding="utf-8") as f:
                    data_info = yaml.safe_load(f)
                
                class_names = data_info.get("names", [])
                if isinstance(class_names, dict):
                    # 如果是字典格式 {0: "class1", 1: "class2"}, 转换为列表
                    max_idx = max(class_names.keys())
                    names_list = [""] * (max_idx + 1)
                    for idx, name in class_names.items():
                        names_list[int(idx)] = name
                    class_names = names_list
                elif not isinstance(class_names, list):
                    class_names = []
                
                num_classes = len(class_names) if class_names else 80  # 默认 COCO 80 类
                
                # 生成模型名称（不含扩展名）
                tflite_file = Path(ne301_path)
                model_base_name = tflite_file.stem  # 例如: best_ne301_quant_pc_ui_xxx
                
                # 生成 JSON 配置（会尝试从 TFLite 模型提取真实的量化参数和输出尺寸）
                json_config = generate_ne301_json_config(
                    tflite_path=tflite_file,
                    model_name=model_base_name,
                    input_size=imgsz,
                    num_classes=num_classes,
                    class_names=class_names,
                    output_scale=None,  # 从模型提取
                    output_zero_point=None,  # 从模型提取
                    confidence_threshold=0.25,
                    iou_threshold=0.45,
                    output_shape=None,  # 从模型提取
                )
                
                # 确保 JSON 文件保存在与 TFLite 文件相同的目录（quantized_models），便于下载
                json_output_dir = tflite_file.parent  # quantized_models 目录
                json_output_dir.mkdir(parents=True, exist_ok=True)  # 确保目录存在
                json_file_path = json_output_dir / f"{model_base_name}.json"
                
                # 保存 JSON 配置文件（确保所有值都是 JSON 可序列化的）
                json_config_clean = _convert_to_json_serializable(json_config)
                with open(json_file_path, "w", encoding="utf-8") as f:
                    json.dump(json_config_clean, f, indent=2, ensure_ascii=False)
                
                # 验证文件是否真的保存成功
                if not json_file_path.exists():
                    logger.error(f"[NE301] JSON 文件保存失败: {json_file_path}")
                    raise RuntimeError(f"JSON 配置文件保存失败: {json_file_path}")
                
                # 保存 JSON 路径（在外部作用域中，确保即使后续步骤失败也能返回）
                ne301_json_path = str(json_file_path)
                logger.info(f"[NE301] ✓ JSON config saved to: {json_file_path}")
                print(f"[NE301] ✓ JSON config saved to: {json_file_path}")
                
                # 保存 json_config 到外部作用域，供后续使用
                json_config_saved = json_config
                
            except Exception as e:
                logger.error(f"[NE301] JSON 配置文件生成失败: {e}", exc_info=True)
                print(f"[NE301] JSON 配置文件生成失败: {e}")
                # JSON 生成失败不影响 TFLite 文件的返回
                json_config_saved = None
                ne301_json_path = None  # 确保变量被定义，避免后续NameError
            
            # 尝试编译 NE301 模型包（这一步失败不影响文件下载）
            logger.info("[NE301] 开始编译 NE301 模型包...")
            print("[NE301] 开始编译 NE301 模型包...")
            try:
                # 确保 json_config 可用（如果前面的生成失败，尝试从文件读取）
                json_config_for_copy = json_config_saved if 'json_config_saved' in locals() and json_config_saved is not None else None
                if json_config_for_copy is None:
                    # 如果 json_config 不存在，尝试从已保存的 JSON 文件读取
                    if ne301_json_path and Path(ne301_json_path).exists():
                        with open(ne301_json_path, "r", encoding="utf-8") as f:
                            json_config_for_copy = json.load(f)
                            logger.info(f"[NE301] 从文件读取 JSON 配置: {ne301_json_path}")
                            print(f"[NE301] 从文件读取 JSON 配置: {ne301_json_path}")
                    else:
                        # 如果 JSON 文件也不存在，跳过编译步骤
                        logger.warning("[NE301] JSON 配置不可用，跳过编译步骤")
                        print("[NE301] JSON 配置不可用，跳过编译步骤")
                        raise RuntimeError("JSON 配置不可用，无法继续编译")
                
                from backend.utils.ne301_export import (
                    copy_model_to_ne301_project,
                    build_ne301_model
                )
                
                # 获取 NE301 项目路径（优先使用已初始化的路径）
                from backend.utils.ne301_init import get_ne301_project_path
                try:
                    ne301_project_path = get_ne301_project_path()
                except Exception as e:
                    logger.warning(f"[NE301] Failed to get NE301 project path: {e}")
                    # 回退到环境变量或配置
                    ne301_project_path = settings.NE301_PROJECT_PATH or os.environ.get("NE301_PROJECT_PATH")
                    if ne301_project_path:
                        ne301_project_path = Path(ne301_project_path)
                    else:
                        ne301_project_path = settings.DATASETS_ROOT.parent / "ne301"
                
                if not isinstance(ne301_project_path, Path):
                    ne301_project_path = Path(ne301_project_path)
                
                logger.info(f"[NE301] 检查 NE301 项目路径: {ne301_project_path}")
                print(f"[NE301] 检查 NE301 项目路径: {ne301_project_path}")
                logger.info(f"[NE301] 项目路径存在: {ne301_project_path.exists()}")
                print(f"[NE301] 项目路径存在: {ne301_project_path.exists()}")
                
                if not ne301_project_path.exists() or not (ne301_project_path / "Model").exists():
                    logger.warning(f"[NE301] NE301 项目目录不存在或不完整: {ne301_project_path}，JSON 已保存到: {ne301_json_path}")
                    print(f"[NE301] Project directory not found or incomplete: {ne301_project_path}")
                    print(f"[NE301] JSON config has been saved to: {ne301_json_path}")
                    print(f"[NE301] The project should be automatically cloned on startup.")
                    print(f"[NE301] If this error persists, check the startup logs for NE301 initialization.")
                    print(f"[NE301] Or manually copy files to NE301 project:")
                    print(f"  cp {tflite_file} {ne301_project_path}/Model/weights/")
                    print(f"  cp {ne301_json_path} {ne301_project_path}/Model/weights/")
                    print(f"  cd {ne301_project_path} && make model")
                    raise FileNotFoundError(f"NE301 项目目录不存在或不完整: {ne301_project_path}")
                else:
                    logger.info(f"[NE301] ✓ NE301 项目路径验证通过")
                    print(f"[NE301] ✓ NE301 项目路径验证通过")
                    
                    # 复制模型和 JSON 到 NE301 项目
                    logger.info(f"[NE301] 开始复制模型文件到 NE301 项目...")
                    print(f"[NE301] 开始复制模型文件到 NE301 项目...")
                    tflite_dest, json_dest = copy_model_to_ne301_project(
                        tflite_path=tflite_file,
                        json_config=json_config_for_copy,
                        ne301_project_path=ne301_project_path,
                        model_name=model_base_name
                    )
                    logger.info(f"[NE301] Model files copied to NE301 project: {tflite_dest}, {json_dest}")
                    print(f"[NE301] Model files copied to NE301 project: {tflite_dest}, {json_dest}")
                    
                    # 使用 Docker 编译模型（从配置或环境变量）
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
                # 编译失败不影响量化结果的返回
                # 但已生成的文件（TFLite、JSON）仍然可以下载
                logger.error(f"[NE301] Model package build failed: {e}", exc_info=True)
                print(f"[NE301] ✗ Model package build failed: {type(e).__name__}: {e}")
                print(f"[NE301] 注意：TFLite 和 JSON 文件已生成，可以下载使用")
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
        
        # 添加 NE301 相关路径（即使编译失败，也要返回已生成的文件）
        if ne301 and ne301_path:
            # 验证并添加 TFLite 文件路径
            tflite_path_obj = Path(ne301_path)
            if tflite_path_obj.exists():
                result["ne301_tflite"] = ne301_path
                file_size = tflite_path_obj.stat().st_size
                logger.info(f"[NE301] ✓ TFLite 文件已生成并可下载: {ne301_path} (size: {file_size} bytes)")
            else:
                logger.error(f"[NE301] ✗ TFLite 文件不存在: {ne301_path}")
                # 即使文件不存在也返回路径，让前端知道应该在哪里查找
            
            # 验证并添加 JSON 配置文件路径（即使编译失败也应返回）
            if ne301_json_path:
                json_path_obj = Path(ne301_json_path)
                if json_path_obj.exists():
                    result["ne301_json"] = ne301_json_path
                    file_size = json_path_obj.stat().st_size
                    logger.info(f"[NE301] ✓ JSON 配置文件已生成并可下载: {ne301_json_path} (size: {file_size} bytes)")
                else:
                    logger.error(f"[NE301] ✗ JSON 配置文件不存在: {ne301_json_path}")
                    # 即使文件不存在也返回路径，让前端知道应该在哪里查找
            
            # 验证并添加编译后的模型包路径（仅当编译成功时）
            if ne301_model_bin_path:
                bin_path_obj = Path(ne301_model_bin_path)
                if bin_path_obj.exists():
                    result["ne301_model_bin"] = ne301_model_bin_path
                    file_size = bin_path_obj.stat().st_size
                    logger.info(f"[NE301] ✓ 模型包已生成并可下载: {ne301_model_bin_path} (size: {file_size} bytes)")
                else:
                    logger.warning(f"[NE301] ⚠️ 模型包不存在（编译可能失败）: {ne301_model_bin_path}")
        
        # 验证原始 TFLite 文件
        export_path_obj = Path(export_path)
        if export_path_obj.exists():
            file_size = export_path_obj.stat().st_size
            logger.info(f"[Export] ✓ 原始 TFLite 文件已生成: {export_path} (size: {file_size} bytes)")
        else:
            logger.error(f"[Export] ✗ 原始 TFLite 文件不存在: {export_path}")
        
        return result
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Ultralytics or TensorFlow not installed: {str(e)}")
    except Exception as e:
        try:
            import traceback
            tb = traceback.format_exc()
            logger.error("[NE301] TFLite export failed: %s", tb)
            print(f"[NE301] TFLite export failed: {tb}")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"TFLite export failed: {str(e)}")

@router.get("/projects/{project_id}/train/{training_id}/export/tflite/download")
def download_tflite_export(
    project_id: str,
    training_id: str,
    file_type: str = Query(..., description="文件类型: tflite, ne301_tflite, ne301_json, ne301_model_bin"),
    db: Session = Depends(get_db)
):
    """
    下载模型量化导出的文件
    
    Args:
        project_id: 项目ID
        training_id: 训练ID
        file_type: 文件类型 (tflite, ne301_tflite, ne301_json, ne301_model_bin)
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 构建可能的文件路径
    # 注意：训练目录可能是 train_{training_id} 或 train_{project_id}（如果 training_id 包含时间戳）
    # 尝试多个可能的路径
    possible_base_dirs = [
        settings.DATASETS_ROOT / project_id / f"train_{training_id}",
        settings.DATASETS_ROOT / project_id / f"train_{project_id}",  # 回退到 project_id
    ]
    
    # 如果 training_id 包含时间戳（格式：xxx_yyyymmdd_hhmmss），也尝试去掉时间戳的部分
    if "_" in training_id:
        parts = training_id.rsplit("_", 2)  # 最多分成3部分，支持 xxx_yyyymmdd_hhmmss
        if len(parts) >= 2:
            # 尝试去掉最后的时间戳部分
            base_id = "_".join(parts[:-2]) if len(parts) > 2 else parts[0]
            possible_base_dirs.insert(1, settings.DATASETS_ROOT / project_id / f"train_{base_id}")
    
    # 查找实际存在的训练目录
    base_dir = None
    for possible_dir in possible_base_dirs:
        if possible_dir.exists() and (possible_dir / "weights").exists():
            base_dir = possible_dir
            logger.info(f"[Download] 找到训练目录: {base_dir}")
            break
    
    if not base_dir:
        # 如果都找不到，使用第一个作为默认值（会在后续检查中报错）
        base_dir = possible_base_dirs[0]
        logger.warning(f"[Download] 未找到训练目录，使用默认路径: {base_dir} (可能不存在)")
        logger.info(f"[Download] 已尝试的路径: {[str(d) for d in possible_base_dirs]}")
    
    weights_dir = base_dir / "weights"
    
    logger.info(f"[Download] 查找文件类型: {file_type}, 基础目录: {base_dir}")
    
    file_path = None
    filename = None
    
    if file_type == "tflite":
        # 查找最新的 TFLite 文件（Ultralytics 导出的原始 TFLite）
        tflite_files = list(weights_dir.glob("*.tflite"))
        logger.info(f"[Download] 在 {weights_dir} 找到 {len(tflite_files)} 个 TFLite 文件")
        if not tflite_files:
            # 也检查 best_saved_model 目录
            saved_model_dir = weights_dir / "best_saved_model"
            if saved_model_dir.exists():
                tflite_files = list(saved_model_dir.glob("*.tflite"))
                logger.info(f"[Download] 在 best_saved_model 目录找到 {len(tflite_files)} 个 TFLite 文件")
        if not tflite_files:
            raise HTTPException(status_code=404, detail=f"TFLite file not found in {weights_dir}")
        file_path = max(tflite_files, key=lambda p: p.stat().st_mtime)
        filename = file_path.name
        logger.info(f"[Download] 选择文件: {file_path}")
    elif file_type == "ne301_tflite":
        # NE301 量化后的 TFLite 文件
        ne301_dir = weights_dir / "ne301_quant" / "quantized_models"
        logger.info(f"[Download] 查找 NE301 TFLite 文件，目录: {ne301_dir}")
        if not ne301_dir.exists():
            logger.error(f"[Download] NE301 目录不存在: {ne301_dir}")
            raise HTTPException(status_code=404, detail=f"NE301 TFLite directory not found: {ne301_dir}")
        tflite_files = list(ne301_dir.glob("*.tflite"))
        logger.info(f"[Download] 在 {ne301_dir} 找到 {len(tflite_files)} 个 TFLite 文件: {[f.name for f in tflite_files]}")
        if not tflite_files:
            raise HTTPException(status_code=404, detail=f"NE301 TFLite file not found in {ne301_dir}")
        file_path = max(tflite_files, key=lambda p: p.stat().st_mtime)
        filename = file_path.name
        logger.info(f"[Download] 选择文件: {file_path}")
    elif file_type == "ne301_json":
        # NE301 JSON 配置文件
        ne301_dir = weights_dir / "ne301_quant" / "quantized_models"
        logger.info(f"[Download] 查找 NE301 JSON 文件，目录: {ne301_dir}")
        if not ne301_dir.exists():
            logger.error(f"[Download] NE301 目录不存在: {ne301_dir}")
            raise HTTPException(status_code=404, detail=f"NE301 JSON directory not found: {ne301_dir}")
        json_files = list(ne301_dir.glob("*.json"))
        logger.info(f"[Download] 在 {ne301_dir} 找到 {len(json_files)} 个 JSON 文件: {[f.name for f in json_files]}")
        if not json_files:
            raise HTTPException(status_code=404, detail=f"NE301 JSON file not found in {ne301_dir}")
        file_path = max(json_files, key=lambda p: p.stat().st_mtime)
        filename = file_path.name
        logger.info(f"[Download] 选择文件: {file_path}")
    elif file_type == "ne301_model_bin":
        # NE301 编译后的模型包
        # 尝试多个可能的位置
        from backend.utils.ne301_init import get_ne301_project_path
        try:
            ne301_project_path = get_ne301_project_path()
        except Exception:
            ne301_project_path = Path(os.environ.get("NE301_PROJECT_PATH", "/workspace/ne301"))
        
        logger.info(f"[Download] 查找 NE301 模型包，项目路径: {ne301_project_path}")
        
        possible_paths = [
            ne301_project_path / "build" / "ne301_Model.bin",
            ne301_project_path / "Model" / "build" / "ne301_Model.bin",
            ne301_project_path / "build" / "Model.bin",
        ]
        
        model_bin_path = None
        for path in possible_paths:
            if path.exists():
                model_bin_path = path
                logger.info(f"[Download] 找到模型包: {model_bin_path}")
                break
        
        if not model_bin_path:
            logger.error(f"[Download] 模型包未找到，已尝试路径: {possible_paths}")
            raise HTTPException(status_code=404, detail=f"NE301 model package not found. Searched in: {[str(p) for p in possible_paths]}")
        
        file_path = model_bin_path
        filename = model_bin_path.name
    else:
        raise HTTPException(status_code=400, detail=f"Invalid file_type: {file_type}. Must be one of: tflite, ne301_tflite, ne301_json, ne301_model_bin")
    
    # 最终验证文件是否存在
    if not file_path or not file_path.exists():
        logger.error(f"[Download] ✗ 文件不存在: {file_path} (file_type: {file_type})")
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    
    # 验证文件大小（文件不应该为空）
    file_size = file_path.stat().st_size
    if file_size == 0:
        logger.warning(f"[Download] ⚠️ 文件大小为 0: {file_path}")
    
    logger.info(f"[Download] ✓ 文件验证通过，准备下载: {file_path} (size: {file_size} bytes)")
    
    # 确定媒体类型
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
    """停止训练（可选传入 training_id；默认停当前活动训练）"""
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
    """使用训练好的模型测试图像"""
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
        
        # 读取上传的图像
        image_bytes = await file.read()
        image = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")  # 确保RGB，避免透明/灰度导致检测异常
        
        # 加载模型
        model = YOLO(model_path)
        
        # 进行推理
        results = model.predict(
            source=image,
            conf=conf,
            iou=iou,
            save=False,
            verbose=False
        )
        
        # 解析结果
        result = results[0]
        detections = []
        
        # 获取类别名称（从data.yaml或模型）
        names = result.names if hasattr(result, 'names') else {}
        
        for box in result.boxes:
            # 获取边界框坐标
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
        
        # 调试日志：记录推理输出的概要（同时使用 print 和 logger，避免未配置日志级别时看不到）
        debug_line = (
            f"[TestPredict] project={project_id} training_id={training_id} "
            f"img={image.width}x{image.height} detections={len(detections)} "
            f"conf={conf:.3f} iou={iou:.3f} names={list(names.values()) if names else 'N/A'}"
        )
        try:
            logger.info(debug_line)
        except Exception:
            pass
        print(debug_line)
        
        # 将检测结果绘制到图像上（result.plot 返回 BGR，需要转为 RGB）
        annotated_bgr = result.plot()
        annotated_rgb = annotated_bgr[..., ::-1]  # BGR -> RGB
        annotated_pil = PILImage.fromarray(annotated_rgb)
        
        # 转换为base64
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
    """清除训练记录"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    training_service.clear_training(project_id, training_id)
    return {"message": "Training record cleared"}


# ========== MQTT 服务管理 ==========
@router.get("/mqtt/status")
def get_mqtt_status(request: Request):
    """获取 MQTT 服务状态"""
    from backend.config import get_mqtt_broker_host, get_local_ip
    
    if settings.MQTT_USE_BUILTIN_BROKER:
        # 使用新的函数获取对外显示的主机 IP（而不是容器内部 IP）
        broker = get_mqtt_broker_host(request)
        port = settings.MQTT_BUILTIN_PORT
        broker_type = "builtin"
    else:
        broker = settings.MQTT_BROKER
        port = settings.MQTT_PORT
        broker_type = "external"
    
    # 获取服务器 IP（用于显示在项目信息中）
    server_ip = get_mqtt_broker_host(request)
    # 如果获取到的是容器内部 IP 或 localhost，尝试其他方法
    if server_ip in ["localhost", "127.0.0.1", "0.0.0.0"] or server_ip.startswith("172.17.") or server_ip.startswith("172.18.") or server_ip.startswith("172.19."):
        # 尝试从请求的 Host 头获取
        host = request.headers.get("Host", "")
        if host:
            host_ip = host.split(":")[0]
            if host_ip not in ["localhost", "127.0.0.1", "0.0.0.0"]:
                server_ip = host_ip
            else:
                # 如果 Host 头也是 localhost，尝试从 X-Forwarded-Host 获取
                forwarded_host = request.headers.get("X-Forwarded-Host", "")
                if forwarded_host:
                    forwarded_ip = forwarded_host.split(":")[0]
                    if forwarded_ip not in ["localhost", "127.0.0.1", "0.0.0.0"]:
                        server_ip = forwarded_ip
    
    return {
        "enabled": settings.MQTT_ENABLED,
        "use_builtin": settings.MQTT_USE_BUILTIN_BROKER if settings.MQTT_ENABLED else False,
        "broker_type": broker_type if settings.MQTT_ENABLED else None,
        "connected": mqtt_service.is_connected if settings.MQTT_ENABLED else False,
        "broker": broker if settings.MQTT_ENABLED else None,
        "port": port if settings.MQTT_ENABLED else None,
        "topic": settings.MQTT_UPLOAD_TOPIC if settings.MQTT_ENABLED else None,
        "server_ip": server_ip,  # 服务器 IP 地址
        "server_port": settings.PORT  # 服务器端口
    }


@router.post("/mqtt/test")
def test_mqtt_connection():
    """测试 MQTT 连接"""
    if not settings.MQTT_ENABLED:
        return {
            "success": False,
            "message": "MQTT 服务已禁用",
            "error": "MQTT_ENABLED is False"
        }
    
    import socket
    import time
    
    try:
        from backend.config import get_local_ip
        
        # 确定要测试的 Broker 地址
        if settings.MQTT_USE_BUILTIN_BROKER:
            broker_host = get_local_ip()
            broker_port = settings.MQTT_BUILTIN_PORT
        else:
            broker_host = settings.MQTT_BROKER
            broker_port = settings.MQTT_PORT
        
        # 测试 TCP 连接
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex((broker_host, broker_port))
        sock.close()
        
        if result == 0:
            # TCP 连接成功，尝试 MQTT 连接
            import paho.mqtt.client as mqtt
            import uuid
            
            test_client = None
            connection_result = {"success": False, "message": ""}
            
            def on_connect_test(client, userdata, flags, rc):
                connection_result["success"] = (rc == 0)
                if rc == 0:
                    connection_result["message"] = "MQTT 连接成功"
                else:
                    connection_result["message"] = f"MQTT 连接失败，错误代码: {rc}"
                client.disconnect()
            
            def on_connect_fail_test(client, userdata):
                connection_result["success"] = False
                connection_result["message"] = "MQTT 连接超时"
            
            try:
                # 明确指定使用 MQTT 3.1.1 协议（aMQTT broker 不支持 MQTT 5.0）
                test_client = mqtt.Client(
                    client_id=f"test_client_{uuid.uuid4().hex[:8]}",
                    protocol=mqtt.MQTTv311
                )
                test_client.on_connect = on_connect_test
                test_client.on_connect_fail = on_connect_fail_test
                
                # 内置 Broker 不需要认证，外部 Broker 才需要
                if not settings.MQTT_USE_BUILTIN_BROKER:
                    if settings.MQTT_USERNAME and settings.MQTT_PASSWORD:
                        test_client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
                
                test_client.connect(broker_host, broker_port, keepalive=5)
                test_client.loop_start()
                
                # 等待连接结果（最多等待 3 秒）
                timeout = 3
                elapsed = 0
                while elapsed < timeout and connection_result["message"] == "":
                    time.sleep(0.1)
                    elapsed += 0.1
                
                test_client.loop_stop()
                test_client.disconnect()
                
                if connection_result["message"] == "":
                    connection_result["message"] = "连接超时"
                
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
                    "message": f"MQTT 连接测试失败: {str(e)}",
                    "broker": f"{broker_host}:{broker_port}",
                    "broker_type": "builtin" if settings.MQTT_USE_BUILTIN_BROKER else "external",
                    "tcp_connected": True
                }
        else:
            error_msg = f"无法连接到 MQTT Broker ({broker_host}:{broker_port})"
            error_detail = ""
            
            # macOS/Linux 错误代码
            if result == 61 or result == 111:  # ECONNREFUSED (macOS: 61, Linux: 111)
                error_msg += " - 连接被拒绝"
                error_detail = "MQTT Broker 可能未运行。请启动 MQTT Broker 服务。"
            elif result == 60 or result == 110:  # ETIMEDOUT (macOS: 60, Linux: 110)
                error_msg += " - 连接超时"
                error_detail = "网络连接超时，请检查网络和防火墙设置。"
            elif result == 64 or result == 113:  # EHOSTUNREACH (macOS: 64, Linux: 113)
                error_msg += " - 无法到达主机"
                error_detail = "无法到达 MQTT Broker 地址，请检查 Broker 地址配置。"
            elif result == 51:  # ENETUNREACH (macOS)
                error_msg += " - 网络不可达"
                error_detail = "网络不可达，请检查网络连接和 Broker 地址。"
            else:
                error_msg += f" - 错误代码: {result}"
                error_detail = "请检查 MQTT Broker 配置和运行状态。"
            
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
            "message": f"无法解析 MQTT Broker 地址: {str(e)}",
            "broker": f"{settings.MQTT_BROKER}:{settings.MQTT_PORT}",
            "tcp_connected": False,
            "error": "DNS resolution failed"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"连接测试失败: {str(e)}",
            "broker": f"{settings.MQTT_BROKER}:{settings.MQTT_PORT}",
            "tcp_connected": False,
            "error": str(e)
    }

