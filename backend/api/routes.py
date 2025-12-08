"""API 路由定义"""
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
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

from backend.models.database import get_db, Project, Image, Class, Annotation
from backend.services.websocket_manager import websocket_manager
from backend.utils.yolo_export import YOLOExporter
from backend.config import settings
from PIL import Image as PILImage
import io


router = APIRouter()


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
    
    db_annotation = Annotation(
        image_id=image_id,
        class_id=annotation.class_id,
        type=annotation.type,
        data=json.dumps(annotation.data)
    )
    
    db.add(db_annotation)
    
    # 更新图像状态
    image.status = "LABELED"
    
    db.commit()
    db.refresh(db_annotation)
    
    return db_annotation


@router.put("/annotations/{annotation_id}")
def update_annotation(annotation_id: int, annotation: AnnotationUpdate, db: Session = Depends(get_db)):
    """更新标注"""
    db_ann = db.query(Annotation).filter(Annotation.id == annotation_id).first()
    if not db_ann:
        raise HTTPException(status_code=404, detail="Annotation not found")
    
    if annotation.data is not None:
        db_ann.data = json.dumps(annotation.data)
    
    if annotation.class_id is not None:
        db_ann.class_id = annotation.class_id
    
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
    if remaining == 0:
        if image:
            image.status = "UNLABELED"
    
    db.commit()
    
    # 通过 WebSocket 通知前端标注已删除
    if project_id:
        from backend.services.websocket_manager import websocket_manager
        websocket_manager.broadcast_project_update(project_id, {
            "type": "annotation_deleted",
            "annotation_id": annotation_id,
            "image_id": image_id
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

