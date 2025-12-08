"""数据库模型定义"""
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from backend.config import settings

Base = declarative_base()
engine = create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Project(Base):
    """项目表"""
    __tablename__ = "projects"
    
    id = Column(String, primary_key=True)  # project_id (UUID)
    name = Column(String, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关联关系
    images = relationship("Image", back_populates="project", cascade="all, delete-orphan")
    classes = relationship("Class", back_populates="project", cascade="all, delete-orphan")


class Image(Base):
    """图像表"""
    __tablename__ = "images"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    filename = Column(String, nullable=False)
    path = Column(String, nullable=False)  # 相对路径
    width = Column(Integer)
    height = Column(Integer)
    status = Column(String, default="UNLABELED")  # UNLABELED, LABELED, REVIEWED
    source = Column(String)  # MQTT:device_id, UPLOAD, etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关联关系
    project = relationship("Project", back_populates="images")
    annotations = relationship("Annotation", back_populates="image", cascade="all, delete-orphan")


class Class(Base):
    """类别表"""
    __tablename__ = "classes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    name = Column(String, nullable=False)
    color = Column(String, nullable=False)  # HEX 颜色代码
    shortcut_key = Column(String)  # 快捷键 (1-9)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关联关系
    project = relationship("Project", back_populates="classes")
    annotations = relationship("Annotation", back_populates="class_")


class Annotation(Base):
    """标注表"""
    __tablename__ = "annotations"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    image_id = Column(Integer, ForeignKey("images.id"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=False)
    
    # 标注类型: bbox, polygon, keypoint
    type = Column(String, nullable=False)
    
    # 标注数据 (JSON 格式存储)
    # bbox: {"x_min": float, "y_min": float, "x_max": float, "y_max": float}
    # polygon: {"points": [[x, y], ...]}
    # keypoint: {"points": [[x, y, index], ...], "skeleton": [[i, j], ...]}
    data = Column(Text, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关联关系
    image = relationship("Image", back_populates="annotations")
    class_ = relationship("Class", back_populates="annotations")


def init_db():
    """初始化数据库，创建所有表"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

