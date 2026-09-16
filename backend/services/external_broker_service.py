"""External MQTT Broker management service"""
from typing import List, Optional
from pydantic import BaseModel, Field
from backend.models.database import SessionLocal, ExternalMQTTBroker
from backend.utils.security_utils import PASSWORD_MASK, mask_password
from backend.config import settings


class ExternalBrokerCreate(BaseModel):
    """Model for creating a new external broker"""
    name: str = Field(..., min_length=1, max_length=100)
    enabled: bool = True
    protocol: str = Field("mqtt", pattern="^(mqtt|mqtts)$")
    host: str = Field(..., min_length=1)
    port: int = Field(..., ge=1, le=65535)
    username: Optional[str] = None
    password: Optional[str] = None
    qos: int = Field(1, ge=0, le=2)
    keepalive: int = Field(120, ge=10)
    tls_enabled: bool = False
    tls_ca_cert_path: Optional[str] = None
    tls_client_cert_path: Optional[str] = None
    tls_client_key_path: Optional[str] = None
    tls_insecure_skip_verify: bool = False
    topic_pattern: Optional[str] = None  # If None, uses default from settings


class ExternalBrokerUpdate(BaseModel):
    """Model for updating an external broker"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    enabled: Optional[bool] = None
    protocol: Optional[str] = Field(None, pattern="^(mqtt|mqtts)$")
    host: Optional[str] = Field(None, min_length=1)
    port: Optional[int] = Field(None, ge=1, le=65535)
    username: Optional[str] = None
    password: Optional[str] = None
    qos: Optional[int] = Field(None, ge=0, le=2)
    keepalive: Optional[int] = Field(None, ge=10)
    tls_enabled: Optional[bool] = None
    tls_ca_cert_path: Optional[str] = None
    tls_client_cert_path: Optional[str] = None
    tls_client_key_path: Optional[str] = None
    tls_insecure_skip_verify: Optional[bool] = None
    topic_pattern: Optional[str] = None


class ExternalBrokerResponse(BaseModel):
    """Response model for external broker"""
    id: int
    name: str
    enabled: bool
    protocol: str
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]  # Note: In production, consider not returning password
    qos: int
    keepalive: int
    tls_enabled: bool
    tls_ca_cert_path: Optional[str]
    tls_client_cert_path: Optional[str]
    tls_client_key_path: Optional[str]
    tls_insecure_skip_verify: bool
    topic_pattern: Optional[str]
    connected: Optional[bool] = None  # Connection status from MQTT service
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class ExternalBrokerService:
    """Service for managing external MQTT brokers"""

    def get_all(self) -> List[ExternalBrokerResponse]:
        """Get all external brokers"""
        db = SessionLocal()
        try:
            brokers = db.query(ExternalMQTTBroker).order_by(ExternalMQTTBroker.created_at).all()
            return [ExternalBrokerResponse(
                id=broker.id,
                name=broker.name,
                enabled=broker.enabled,
                protocol=broker.protocol,
                host=broker.host,
                port=broker.port,
                username=broker.username,
                password=mask_password(broker.password),
                qos=broker.qos,
                keepalive=broker.keepalive,
                tls_enabled=broker.tls_enabled,
                tls_ca_cert_path=broker.tls_ca_cert_path,
                tls_client_cert_path=broker.tls_client_cert_path,
                tls_client_key_path=broker.tls_client_key_path,
                tls_insecure_skip_verify=broker.tls_insecure_skip_verify,
                topic_pattern=broker.topic_pattern,
                connected=None,  # Will be set by API route from MQTT service
                created_at=broker.created_at.isoformat() if broker.created_at else "",
                updated_at=broker.updated_at.isoformat() if broker.updated_at else "",
            ) for broker in brokers]
        finally:
            db.close()

    def get_by_id(self, broker_id: int) -> Optional[ExternalBrokerResponse]:
        """Get broker by ID"""
        db = SessionLocal()
        try:
            broker = db.query(ExternalMQTTBroker).filter(ExternalMQTTBroker.id == broker_id).first()
            if not broker:
                return None
            return ExternalBrokerResponse(
                id=broker.id,
                name=broker.name,
                enabled=broker.enabled,
                protocol=broker.protocol,
                host=broker.host,
                port=broker.port,
                username=broker.username,
                password=mask_password(broker.password),
                qos=broker.qos,
                keepalive=broker.keepalive,
                tls_enabled=broker.tls_enabled,
                tls_ca_cert_path=broker.tls_ca_cert_path,
                tls_client_cert_path=broker.tls_client_cert_path,
                tls_client_key_path=broker.tls_client_key_path,
                tls_insecure_skip_verify=broker.tls_insecure_skip_verify,
                topic_pattern=broker.topic_pattern,
                connected=None,  # Will be set by API route from MQTT service
                created_at=broker.created_at.isoformat() if broker.created_at else "",
                updated_at=broker.updated_at.isoformat() if broker.updated_at else "",
            )
        finally:
            db.close()

    def create(self, broker_data: ExternalBrokerCreate) -> ExternalBrokerResponse:
        """Create a new external broker"""
        db = SessionLocal()
        try:
            broker = ExternalMQTTBroker(
                name=broker_data.name,
                enabled=broker_data.enabled,
                protocol=broker_data.protocol,
                host=broker_data.host,
                port=broker_data.port,
                username=broker_data.username,
                # A masked value on create means "no real password supplied"
                password=(
                    None
                    if broker_data.password == PASSWORD_MASK
                    else broker_data.password
                ),
                qos=broker_data.qos,
                keepalive=broker_data.keepalive,
                tls_enabled=broker_data.tls_enabled,
                tls_ca_cert_path=broker_data.tls_ca_cert_path,
                tls_client_cert_path=broker_data.tls_client_cert_path,
                tls_client_key_path=broker_data.tls_client_key_path,
                tls_insecure_skip_verify=broker_data.tls_insecure_skip_verify,
                # topic_pattern is always None - backend will use default logic for subscription
                topic_pattern=None,
            )
            db.add(broker)
            db.commit()
            db.refresh(broker)
            return ExternalBrokerResponse(
                id=broker.id,
                name=broker.name,
                enabled=broker.enabled,
                protocol=broker.protocol,
                host=broker.host,
                port=broker.port,
                username=broker.username,
                password=mask_password(broker.password),
                qos=broker.qos,
                keepalive=broker.keepalive,
                tls_enabled=broker.tls_enabled,
                tls_ca_cert_path=broker.tls_ca_cert_path,
                tls_client_cert_path=broker.tls_client_cert_path,
                tls_client_key_path=broker.tls_client_key_path,
                tls_insecure_skip_verify=broker.tls_insecure_skip_verify,
                topic_pattern=broker.topic_pattern,
                connected=None,  # Will be set by API route from MQTT service
                created_at=broker.created_at.isoformat() if broker.created_at else "",
                updated_at=broker.updated_at.isoformat() if broker.updated_at else "",
            )
        finally:
            db.close()

    def update(self, broker_id: int, broker_data: ExternalBrokerUpdate) -> Optional[ExternalBrokerResponse]:
        """Update an existing broker"""
        db = SessionLocal()
        try:
            broker = db.query(ExternalMQTTBroker).filter(ExternalMQTTBroker.id == broker_id).first()
            if not broker:
                return None

            # Update fields
            update_dict = broker_data.model_dump(exclude_unset=True, exclude_none=False)
            # SECURITY: a masked password returned by GET must not overwrite
            # the stored secret — keep the existing value in that case.
            if update_dict.get("password") == PASSWORD_MASK:
                update_dict["password"] = broker.password
            for field, value in update_dict.items():
                if hasattr(broker, field):
                    setattr(broker, field, value)

            # topic_pattern is always None - backend will use default logic for subscription
            if 'topic_pattern' in update_dict:
                broker.topic_pattern = None

            db.commit()
            db.refresh(broker)
            return ExternalBrokerResponse(
                id=broker.id,
                name=broker.name,
                enabled=broker.enabled,
                protocol=broker.protocol,
                host=broker.host,
                port=broker.port,
                username=broker.username,
                password=mask_password(broker.password),
                qos=broker.qos,
                keepalive=broker.keepalive,
                tls_enabled=broker.tls_enabled,
                tls_ca_cert_path=broker.tls_ca_cert_path,
                tls_client_cert_path=broker.tls_client_cert_path,
                tls_client_key_path=broker.tls_client_key_path,
                tls_insecure_skip_verify=broker.tls_insecure_skip_verify,
                topic_pattern=broker.topic_pattern,
                connected=None,  # Will be set by API route from MQTT service
                created_at=broker.created_at.isoformat() if broker.created_at else "",
                updated_at=broker.updated_at.isoformat() if broker.updated_at else "",
            )
        finally:
            db.close()

    def delete(self, broker_id: int) -> bool:
        """Delete a broker"""
        db = SessionLocal()
        try:
            broker = db.query(ExternalMQTTBroker).filter(ExternalMQTTBroker.id == broker_id).first()
            if not broker:
                return False
            db.delete(broker)
            db.commit()
            return True
        finally:
            db.close()

    def get_enabled_brokers(self) -> List[ExternalBrokerResponse]:
        """Get all enabled external brokers"""
        db = SessionLocal()
        try:
            brokers = db.query(ExternalMQTTBroker).filter(
                ExternalMQTTBroker.enabled == True
            ).order_by(ExternalMQTTBroker.created_at).all()
            return [ExternalBrokerResponse(
                id=broker.id,
                name=broker.name,
                enabled=broker.enabled,
                protocol=broker.protocol,
                host=broker.host,
                port=broker.port,
                username=broker.username,
                password=mask_password(broker.password),
                qos=broker.qos,
                keepalive=broker.keepalive,
                tls_enabled=broker.tls_enabled,
                tls_ca_cert_path=broker.tls_ca_cert_path,
                tls_client_cert_path=broker.tls_client_cert_path,
                tls_client_key_path=broker.tls_client_key_path,
                tls_insecure_skip_verify=broker.tls_insecure_skip_verify,
                topic_pattern=broker.topic_pattern or settings.MQTT_UPLOAD_TOPIC,
                connected=None,  # Will be set by API route from MQTT service
                created_at=broker.created_at.isoformat() if broker.created_at else "",
                updated_at=broker.updated_at.isoformat() if broker.updated_at else "",
            ) for broker in brokers]
        finally:
            db.close()


# Global instance
external_broker_service = ExternalBrokerService()
