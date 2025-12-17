"""Database model definitions"""
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from backend.config import settings

Base = declarative_base()
engine = create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Project(Base):
    """Project table"""
    __tablename__ = "projects"
    
    id = Column(String, primary_key=True)  # project_id (UUID)
    name = Column(String, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    images = relationship("Image", back_populates="project", cascade="all, delete-orphan")
    classes = relationship("Class", back_populates="project", cascade="all, delete-orphan")


class Image(Base):
    """Image table"""
    __tablename__ = "images"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    filename = Column(String, nullable=False)
    path = Column(String, nullable=False)  # Relative path
    width = Column(Integer)
    height = Column(Integer)
    status = Column(String, default="UNLABELED")  # UNLABELED, LABELED, REVIEWED
    source = Column(String)  # MQTT:device_id, UPLOAD, etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    project = relationship("Project", back_populates="images")
    annotations = relationship("Annotation", back_populates="image", cascade="all, delete-orphan")


class Class(Base):
    """Class table"""
    __tablename__ = "classes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    name = Column(String, nullable=False)
    color = Column(String, nullable=False)  # HEX color code
    shortcut_key = Column(String)  # Shortcut key (1-9)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    project = relationship("Project", back_populates="classes")
    annotations = relationship("Annotation", back_populates="class_")


class Annotation(Base):
    """Annotation table"""
    __tablename__ = "annotations"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    image_id = Column(Integer, ForeignKey("images.id"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.id"), nullable=False)
    
    # Annotation type: bbox, polygon, keypoint
    type = Column(String, nullable=False)
    
    # Annotation data (stored in JSON format)
    # bbox: {"x_min": float, "y_min": float, "x_max": float, "y_max": float}
    # polygon: {"points": [[x, y], ...]}
    # keypoint: {"points": [[x, y, index], ...], "skeleton": [[i, j], ...]}
    data = Column(Text, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    image = relationship("Image", back_populates="annotations")
    class_ = relationship("Class", back_populates="annotations")


class TrainingRecord(Base):
    """Training record table"""
    __tablename__ = "training_records"

    training_id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    status = Column(String, default="running")
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    model_size = Column(String, nullable=True)
    epochs = Column(Integer, nullable=True)
    imgsz = Column(Integer, nullable=True)
    batch = Column(Integer, nullable=True)
    device = Column(String, nullable=True)
    metrics = Column(Text, nullable=True)  # JSON string
    error = Column(Text, nullable=True)
    model_path = Column(Text, nullable=True)
    log_count = Column(Integer, default=0)

    project = relationship("Project")


class ModelRegistry(Base):
    """Unified model registry (includes training-produced and externally imported models)"""
    __tablename__ = "model_registry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    source = Column(String, nullable=False, default="training")  # training / import / other
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)
    training_id = Column(String, ForeignKey("training_records.training_id"), nullable=True, index=True)
    model_type = Column(String, nullable=True)  # yolov8 / yolov11 / tflite / ne301 / onnx / etc.
    format = Column(String, nullable=True)  # pt / tflite / onnx / bin / etc.
    model_path = Column(Text, nullable=False)
    input_width = Column(Integer, nullable=True)
    input_height = Column(Integer, nullable=True)
    num_classes = Column(Integer, nullable=True)
    class_names = Column(Text, nullable=True)  # JSON array string
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project")


class TrainingLog(Base):
    """Training log table"""
    __tablename__ = "training_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    training_id = Column(String, ForeignKey("training_records.training_id"), index=True, nullable=False)
    project_id = Column(String, ForeignKey("projects.id"), index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    message = Column(Text, nullable=False)

    training_record = relationship("TrainingRecord")


def init_db():
    """Initialize database, create all tables"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

