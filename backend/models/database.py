"""Database model definitions"""
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, Boolean, ForeignKey, Table
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
    devices = relationship(
        "Device",
        secondary="device_project_association",
        back_populates="projects"
    )


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


class Device(Base):
    """Device table"""
    __tablename__ = "devices"

    # Device identity
    id = Column(String, primary_key=True)  # device_id (SN / MAC / UUID)
    name = Column(String, nullable=True)  # Human readable name
    type = Column(String, nullable=True)  # Device type, e.g. NE101 / NE301
    model = Column(String, nullable=True)  # Hardware model
    serial_number = Column(String, nullable=True)
    mac_address = Column(String, nullable=True)

    # Runtime status
    status = Column(String, default="offline")  # online/offline/error/unknown
    last_seen = Column(DateTime, nullable=True)
    last_ip = Column(String, nullable=True)
    firmware_version = Column(String, nullable=True)
    hardware_version = Column(String, nullable=True)
    power_supply_type = Column(String, nullable=True)  # battery / dc / other

    # Extra info
    last_report = Column(Text, nullable=True)  # Raw JSON payload of last report
    tags = Column(String, nullable=True)  # Comma separated tags
    extra_info = Column(Text, nullable=True)  # JSON string for arbitrary metadata

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Many-to-many relationship with projects
    projects = relationship(
        "Project",
        secondary="device_project_association",
        back_populates="devices"
    )
    # One-to-many relationship with reports
    reports = relationship("DeviceReport", back_populates="device", cascade="all, delete-orphan", order_by="DeviceReport.created_at.desc()")


# Many-to-many association table for Device <-> Project
device_project_association = Table(
    "device_project_association",
    Base.metadata,
    Column("device_id", String, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True),
    Column("project_id", String, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime, default=datetime.utcnow)
)


class DeviceReport(Base):
    """Device report history table"""
    __tablename__ = "device_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    report_data = Column(Text, nullable=False)  # Raw JSON payload
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    device = relationship("Device", back_populates="reports")


class TrainingLog(Base):
    """Training log table"""
    __tablename__ = "training_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    training_id = Column(String, ForeignKey("training_records.training_id"), index=True, nullable=False)
    project_id = Column(String, ForeignKey("projects.id"), index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    message = Column(Text, nullable=False)

    training_record = relationship("TrainingRecord")


class ExternalMQTTBroker(Base):
    """External MQTT Broker configuration table (supports multiple brokers)"""
    __tablename__ = "external_mqtt_brokers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # User-friendly name for the broker
    enabled = Column(Boolean, default=True, nullable=False)
    protocol = Column(String, default="mqtt", nullable=False)  # mqtt / mqtts
    host = Column(String, nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String, nullable=True)
    password = Column(String, nullable=True)
    qos = Column(Integer, default=1, nullable=False)
    keepalive = Column(Integer, default=120, nullable=False)
    tls_enabled = Column(Boolean, default=False, nullable=False)
    tls_ca_cert_path = Column(Text, nullable=True)
    tls_client_cert_path = Column(Text, nullable=True)
    tls_client_key_path = Column(Text, nullable=True)
    tls_insecure_skip_verify = Column(Boolean, default=False, nullable=False)
    # Topic subscription pattern (e.g., "annotator/upload/+" or "device/+/image")
    topic_pattern = Column(String, nullable=True)  # If None, uses default from settings
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MQTTSettings(Base):
    """MQTT settings table (single-row configuration)"""
    __tablename__ = "mqtt_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Core switches
    enabled = Column(Boolean, default=True, nullable=False)
    # Historical field: was used for builtin/external mode selection; kept for
    # backward compatibility but no longer drives runtime behaviour directly.
    mode = Column(String, default="builtin", nullable=False)
    # Whether to actively connect to external MQTT Broker in addition to the
    # built-in broker. Built-in broker is considered available by default when
    # MQTT is enabled.
    external_enabled = Column(Boolean, default=False, nullable=False)

    # Built-in broker configuration
    builtin_protocol = Column(String, default="mqtt", nullable=False)  # mqtt / mqtts
    builtin_broker_host = Column(String, nullable=True)  # Manual override for broker host IP (if None, auto-detect)
    builtin_tcp_port = Column(Integer, nullable=True)
    builtin_tls_port = Column(Integer, nullable=True)
    builtin_allow_anonymous = Column(Boolean, default=True, nullable=False)
    builtin_username = Column(String, nullable=True)  # Username for authentication when anonymous is disabled
    builtin_password = Column(String, nullable=True)  # Password for authentication when anonymous is disabled
    builtin_max_connections = Column(Integer, default=100, nullable=False)
    builtin_keepalive_timeout = Column(Integer, default=300, nullable=False)  # Broker-side timeout for disconnecting unresponsive clients
    builtin_qos = Column(Integer, default=1, nullable=False)  # Client-side QoS (used by mqtt_service when connecting as client, not broker config)
    builtin_keepalive = Column(Integer, default=120, nullable=False)  # Client-side keepalive (used by mqtt_service when connecting as client, not broker config)
    builtin_tls_enabled = Column(Boolean, default=False, nullable=False)
    builtin_tls_ca_cert_path = Column(Text, nullable=True)
    builtin_tls_client_cert_path = Column(Text, nullable=True)
    builtin_tls_client_key_path = Column(Text, nullable=True)
    builtin_tls_insecure_skip_verify = Column(Boolean, default=False, nullable=False)
    builtin_tls_require_client_cert = Column(Boolean, default=False, nullable=False)  # Whether to require client certificates (mTLS)

    # External broker configuration
    external_protocol = Column(String, default="mqtt", nullable=False)  # mqtt / mqtts
    external_host = Column(String, nullable=True)
    external_port = Column(Integer, nullable=True)
    external_username = Column(String, nullable=True)
    external_password = Column(String, nullable=True)
    external_qos = Column(Integer, default=1, nullable=False)
    external_keepalive = Column(Integer, default=120, nullable=False)
    external_tls_enabled = Column(Boolean, default=False, nullable=False)
    external_tls_ca_cert_path = Column(Text, nullable=True)
    external_tls_client_cert_path = Column(Text, nullable=True)
    external_tls_client_key_path = Column(Text, nullable=True)
    external_tls_insecure_skip_verify = Column(Boolean, default=False, nullable=False)

    # Legacy fields (kept for backward compatibility, will be migrated gradually)
    protocol = Column(String, default="mqtt", nullable=False)  # Deprecated: use builtin_protocol/external_protocol
    host = Column(String, nullable=True)  # Deprecated: use external_host
    port = Column(Integer, nullable=True)  # Deprecated: use external_port
    username = Column(String, nullable=True)  # Deprecated: use external_username
    password = Column(String, nullable=True)  # Deprecated: use external_password
    qos = Column(Integer, default=1, nullable=False)  # Deprecated: use builtin_qos/external_qos
    keepalive = Column(Integer, default=120, nullable=False)  # Deprecated: use builtin_keepalive/external_keepalive
    tls_enabled = Column(Boolean, default=False, nullable=False)  # Deprecated: use builtin_tls_enabled/external_tls_enabled
    tls_ca_cert_path = Column(Text, nullable=True)  # Deprecated: use builtin_tls_*/external_tls_*
    tls_client_cert_path = Column(Text, nullable=True)  # Deprecated
    tls_client_key_path = Column(Text, nullable=True)  # Deprecated
    tls_insecure_skip_verify = Column(Boolean, default=False, nullable=False)  # Deprecated


def migrate_mqtt_settings():
    """Migrate mqtt_settings table to add new columns if they don't exist"""
    from sqlalchemy import inspect, text
    import logging
    
    logger = logging.getLogger(__name__)
    
    inspector = inspect(engine)
    if not inspector.has_table("mqtt_settings"):
        logger.info("[DB Migration] mqtt_settings table does not exist, will be created by create_all")
        return  # Table doesn't exist yet, will be created by create_all
    
    existing_columns = {col['name'] for col in inspector.get_columns("mqtt_settings")}
    logger.info(f"[DB Migration] Existing mqtt_settings columns: {sorted(existing_columns)}")
    
    # Define new columns to add (SQLite compatible syntax)
    # Note: SQLite doesn't have native BOOLEAN, uses INTEGER (0/1)
    new_columns = {
        'external_enabled': 'INTEGER DEFAULT 0',
        'builtin_protocol': 'VARCHAR(10) DEFAULT "mqtt"',
        'builtin_broker_host': 'VARCHAR(255)',
        'builtin_qos': 'INTEGER DEFAULT 1',
        'builtin_keepalive': 'INTEGER DEFAULT 120',
        'builtin_tls_enabled': 'INTEGER DEFAULT 0',
        'builtin_tls_ca_cert_path': 'TEXT',
        'builtin_tls_client_cert_path': 'TEXT',
        'builtin_tls_client_key_path': 'TEXT',
        'builtin_tls_insecure_skip_verify': 'INTEGER DEFAULT 0',
        'builtin_tls_require_client_cert': 'INTEGER DEFAULT 0',
        'builtin_username': 'VARCHAR(255)',
        'builtin_password': 'VARCHAR(255)',
        'external_protocol': 'VARCHAR(10) DEFAULT "mqtt"',
        'external_host': 'VARCHAR(255)',
        'external_port': 'INTEGER',
        'external_username': 'VARCHAR(255)',
        'external_password': 'VARCHAR(255)',
        'external_qos': 'INTEGER DEFAULT 1',
        'external_keepalive': 'INTEGER DEFAULT 120',
        'external_tls_enabled': 'INTEGER DEFAULT 0',
        'external_tls_ca_cert_path': 'TEXT',
        'external_tls_client_cert_path': 'TEXT',
        'external_tls_client_key_path': 'TEXT',
        'external_tls_insecure_skip_verify': 'INTEGER DEFAULT 0',
    }
    
    columns_added = []
    with engine.begin() as conn:  # Use begin() for automatic transaction management
        for col_name, col_def in new_columns.items():
            if col_name not in existing_columns:
                try:
                    conn.execute(text(f'ALTER TABLE mqtt_settings ADD COLUMN {col_name} {col_def}'))
                    columns_added.append(col_name)
                    logger.info(f"[DB Migration] Added column: {col_name}")
                except Exception as e:
                    # Column might already exist or other error
                    logger.warning(f"[DB Migration] Could not add column {col_name}: {e}")
    
    if columns_added:
        logger.info(f"[DB Migration] Successfully added {len(columns_added)} new columns to mqtt_settings table")
    else:
        logger.info("[DB Migration] No new columns needed for mqtt_settings table")


def init_db():
    """Initialize database, create all tables"""
    Base.metadata.create_all(bind=engine)
    # Run migration for existing tables
    migrate_mqtt_settings()


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

