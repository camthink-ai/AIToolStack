"""MQTT runtime configuration service"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from backend.config import settings
from backend.models.database import SessionLocal, MQTTSettings


class MQTTConfig(BaseModel):
    """Runtime MQTT configuration used by services.

    Design Philosophy:
    - Broker Configuration: Manages the Mosquitto broker itself (ports, TLS, authentication, etc.)
    - Client Connection: AIToolStack automatically connects to the built-in broker using broker config
      (protocol, port, TLS settings are auto-inferred from broker config)
    """

    # Overall mode: 'builtin' or 'external' (kept mainly for backward compatibility / UI display)
    mode: Optional[str] = None

    enabled: bool = True
    external_enabled: bool = False

    # ========== Built-in Broker Configuration (Mosquitto Management) ==========
    # These settings control the Mosquitto broker itself and affect all clients connecting to it.
    builtin_protocol: str = Field("mqtt", pattern="^(mqtt|mqtts)$")  # Broker protocol: determines which port broker listens on (mqtt->1883, mqtts->8883)
    builtin_broker_host: Optional[str] = None  # Manual override for broker host IP (if None, auto-detect)
    builtin_tcp_port: Optional[int] = None  # Fixed: 1883 for MQTT
    builtin_tls_port: Optional[int] = None  # Fixed: 8883 for MQTTS
    # SECURITY (H-1): anonymous broker access is disabled by default. Devices
    # must authenticate with username/password (or mTLS) — configure
    # builtin_username / builtin_password before devices can connect.
    builtin_allow_anonymous: bool = False  # Broker setting: whether to allow anonymous connections
    builtin_username: Optional[str] = None  # Broker setting: username for external devices (when anonymous is disabled)
    builtin_password: Optional[str] = None  # Broker setting: password for external devices (when anonymous is disabled)
    builtin_max_connections: int = 100  # Broker setting: maximum concurrent connections
    builtin_keepalive_timeout: int = 300  # Broker setting: timeout for disconnecting unresponsive clients
    
    # Broker TLS configuration (for Mosquitto server certificates)
    builtin_tls_enabled: bool = False  # Whether broker should listen on TLS port (8883)
    builtin_tls_ca_cert_path: Optional[str] = None  # CA certificate path (used by broker to verify client certs in mTLS, and by clients to verify server cert)
    builtin_tls_server_cert_path: Optional[str] = None  # Server certificate path (for broker)
    builtin_tls_server_key_path: Optional[str] = None  # Server private key path (for broker)
    builtin_tls_require_client_cert: bool = False  # Whether to require client certificates (mTLS). If False, only server certificate verification is required (one-way TLS)
    
    # ========== Client Connection Configuration (AIToolStack as Client) ==========
    # These settings control how AIToolStack connects to the built-in broker.
    # Most settings are auto-inferred from broker config, but some can be customized.
    builtin_qos: int = 1  # Client-side QoS (used by mqtt_service when connecting as client)
    builtin_keepalive: int = 120  # Client-side keepalive (used by mqtt_service when connecting as client)
    
    # Client TLS configuration (for AIToolStack connecting to broker)
    # These are auto-inferred from broker config, but can be overridden if needed
    builtin_tls_client_cert_path: Optional[str] = None  # Deprecated: No longer used - AIToolStack always uses default client certs (/mosquitto/config/certs/client.crt)
    builtin_tls_client_key_path: Optional[str] = None  # Deprecated: No longer used - AIToolStack always uses default client keys (/mosquitto/config/certs/client.key)
    builtin_tls_insecure_skip_verify: bool = False  # Deprecated: No longer used - AIToolStack always verifies certificates

    # External broker configuration
    external_protocol: str = Field("mqtt", pattern="^(mqtt|mqtts)$")
    external_host: Optional[str] = None
    external_port: Optional[int] = None
    external_username: Optional[str] = None
    external_password: Optional[str] = None
    external_qos: int = 1
    external_keepalive: int = 120
    external_tls_enabled: bool = False
    external_tls_ca_cert_path: Optional[str] = None
    external_tls_client_cert_path: Optional[str] = None  # Used for external broker mTLS connections
    external_tls_client_key_path: Optional[str] = None  # Used for external broker mTLS connections
    external_tls_insecure_skip_verify: bool = False  # Deprecated: No longer used - AIToolStack always verifies certificates

    # Legacy fields (for backward compatibility, will be migrated from old fields if new fields are None)
    protocol: Optional[str] = None  # Deprecated: use builtin_protocol/external_protocol
    host: Optional[str] = None  # Deprecated: use external_host
    port: Optional[int] = None  # Deprecated: use external_port
    username: Optional[str] = None  # Deprecated: use external_username
    password: Optional[str] = None  # Deprecated: use external_password
    qos: Optional[int] = None  # Deprecated: use builtin_qos/external_qos
    keepalive: Optional[int] = None  # Deprecated: use builtin_keepalive/external_keepalive
    tls_enabled: Optional[bool] = None  # Deprecated: use builtin_tls_enabled/external_tls_enabled
    tls_ca_cert_path: Optional[str] = None  # Deprecated: use builtin_tls_*/external_tls_*
    tls_client_cert_path: Optional[str] = None  # Deprecated
    tls_client_key_path: Optional[str] = None  # Deprecated
    tls_insecure_skip_verify: Optional[bool] = None  # Deprecated

    class Config:
        from_attributes = True


class MQTTConfigService:
    """Service to load and persist MQTT configuration.

    - Single-row table `mqtt_settings` is used as the backend storage.
    - Falls back to `backend.config.settings` for initial defaults.
    """

    def _create_default_row(self, db) -> MQTTSettings:
        """Create default MQTTSettings row based on current `settings`."""
        row = MQTTSettings(
            enabled=settings.MQTT_ENABLED,
            mode="builtin" if settings.MQTT_USE_BUILTIN_BROKER else "external",
            external_enabled=False,
            # Built-in broker defaults
            builtin_protocol="mqtt",
            builtin_broker_host=None,  # Auto-detect by default
            # Ports are fixed: 1883 for MQTT, 8883 for MQTTS (user cannot modify)
            builtin_tcp_port=1883,
            builtin_tls_port=8883,
            builtin_allow_anonymous=False,  # Secure default (H-1): devices must authenticate
            builtin_username=None,
            builtin_password=None,
            builtin_max_connections=100,
            builtin_keepalive_timeout=300,
            builtin_qos=settings.MQTT_QOS,
            builtin_keepalive=120,
            builtin_tls_enabled=False,
            # Default CA path for auto-generated Mosquitto TLS certificates
            # (used when connecting to the built-in Mosquitto broker via MQTTS)
            builtin_tls_ca_cert_path="/mosquitto/config/certs/ca.crt",
            builtin_tls_client_cert_path=None,
            builtin_tls_client_key_path=None,
            builtin_tls_insecure_skip_verify=False,
            builtin_tls_require_client_cert=False,  # Default: one-way TLS (only verify server cert)
            # External broker defaults
            external_protocol="mqtt",
            external_host=settings.MQTT_BROKER or None,
            external_port=settings.MQTT_PORT or None,
            external_username=settings.MQTT_USERNAME or None,
            external_password=settings.MQTT_PASSWORD or None,
            external_qos=settings.MQTT_QOS,
            external_keepalive=120,
            external_tls_enabled=False,
            external_tls_ca_cert_path=None,
            external_tls_client_cert_path=None,
            external_tls_client_key_path=None,
            external_tls_insecure_skip_verify=False,
            # Legacy fields (for backward compatibility)
            protocol="mqtt",
            host=settings.MQTT_BROKER or None,
            port=settings.MQTT_PORT or None,
            username=settings.MQTT_USERNAME or None,
            password=settings.MQTT_PASSWORD or None,
            qos=settings.MQTT_QOS,
            keepalive=120,
            tls_enabled=False,
            tls_ca_cert_path=None,
            tls_client_cert_path=None,
            tls_client_key_path=None,
            tls_insecure_skip_verify=False,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def load_config(self) -> MQTTConfig:
        """Load current MQTTConfig from DB, creating defaults when necessary."""
        db = SessionLocal()
        try:
            row: Optional[MQTTSettings] = db.query(MQTTSettings).first()
            if row is None:
                row = self._create_default_row(db)

            # Migrate legacy fields to new fields if new fields are not set
            # This handles backward compatibility for existing databases
            builtin_protocol = row.builtin_protocol or row.protocol or "mqtt"
            external_protocol = row.external_protocol or row.protocol or "mqtt"
            # Ports are fixed: 1883 for MQTT, 8883 for MQTTS (user cannot modify)
            builtin_tcp_port = 1883
            builtin_tls_port = 8883
            external_host = row.external_host or row.host
            external_port = row.external_port or row.port
            external_username = row.external_username or row.username
            external_password = row.external_password or row.password
            builtin_qos = row.builtin_qos if row.builtin_qos is not None else (row.qos or settings.MQTT_QOS)
            builtin_keepalive = row.builtin_keepalive if row.builtin_keepalive is not None else (row.keepalive or 120)
            external_qos = row.external_qos if row.external_qos is not None else (row.qos or settings.MQTT_QOS)
            external_keepalive = row.external_keepalive if row.external_keepalive is not None else (row.keepalive or 120)
            builtin_tls_enabled = row.builtin_tls_enabled if row.builtin_tls_enabled is not None else (row.tls_enabled or False)
            external_tls_enabled = row.external_tls_enabled if row.external_tls_enabled is not None else (row.tls_enabled or False)
            mode = row.mode or ("builtin" if settings.MQTT_USE_BUILTIN_BROKER else "external")

            return MQTTConfig(
                mode=mode,
                enabled=row.enabled,
                external_enabled=row.external_enabled,
                # Built-in broker
                builtin_protocol=builtin_protocol,
                builtin_broker_host=row.builtin_broker_host,
                builtin_tcp_port=builtin_tcp_port,
                builtin_tls_port=builtin_tls_port,
                builtin_allow_anonymous=row.builtin_allow_anonymous,
                builtin_username=row.builtin_username,
                builtin_password=row.builtin_password,
                builtin_max_connections=row.builtin_max_connections,
                builtin_keepalive_timeout=row.builtin_keepalive_timeout,
                builtin_qos=builtin_qos,
                builtin_keepalive=builtin_keepalive,
                builtin_tls_enabled=builtin_tls_enabled,
                builtin_tls_ca_cert_path=row.builtin_tls_ca_cert_path or row.tls_ca_cert_path or "/mosquitto/config/certs/ca.crt",
                builtin_tls_client_cert_path=row.builtin_tls_client_cert_path or row.tls_client_cert_path,
                builtin_tls_client_key_path=row.builtin_tls_client_key_path or row.tls_client_key_path,
                builtin_tls_insecure_skip_verify=row.builtin_tls_insecure_skip_verify if row.builtin_tls_insecure_skip_verify is not None else (row.tls_insecure_skip_verify or False),
                builtin_tls_require_client_cert=row.builtin_tls_require_client_cert if row.builtin_tls_require_client_cert is not None else False,
                # External broker
                external_protocol=external_protocol,
                external_host=external_host,
                external_port=external_port,
                external_username=external_username,
                external_password=external_password,
                external_qos=external_qos,
                external_keepalive=external_keepalive,
                external_tls_enabled=external_tls_enabled,
                external_tls_ca_cert_path=row.external_tls_ca_cert_path or row.tls_ca_cert_path,
                external_tls_client_cert_path=row.external_tls_client_cert_path or row.tls_client_cert_path,
                external_tls_client_key_path=row.external_tls_client_key_path or row.tls_client_key_path,
                external_tls_insecure_skip_verify=row.external_tls_insecure_skip_verify if row.external_tls_insecure_skip_verify is not None else (row.tls_insecure_skip_verify or False),
                # Legacy fields (for backward compatibility)
                protocol=row.protocol,
                host=row.host,
                port=row.port,
                username=row.username,
                password=row.password,
                qos=row.qos,
                keepalive=row.keepalive,
                tls_enabled=row.tls_enabled,
                tls_ca_cert_path=row.tls_ca_cert_path,
                tls_client_cert_path=row.tls_client_cert_path,
                tls_client_key_path=row.tls_client_key_path,
                tls_insecure_skip_verify=row.tls_insecure_skip_verify,
            )
        finally:
            db.close()

    def save_config(self, config: MQTTConfig) -> MQTTConfig:
        """Persist full MQTTConfig to DB and return normalized version."""
        db = SessionLocal()
        try:
            row: Optional[MQTTSettings] = db.query(MQTTSettings).first()
            if row is None:
                row = self._create_default_row(db)

            row.enabled = config.enabled
            row.mode = row.mode or "builtin"
            row.external_enabled = config.external_enabled

            # Built-in broker fields
            row.builtin_protocol = config.builtin_protocol
            row.builtin_broker_host = config.builtin_broker_host
            # Ports are fixed: 1883 for MQTT, 8883 for MQTTS (user cannot modify)
            row.builtin_tcp_port = 1883
            row.builtin_tls_port = 8883
            row.builtin_allow_anonymous = config.builtin_allow_anonymous
            row.builtin_username = config.builtin_username
            row.builtin_password = config.builtin_password
            row.builtin_max_connections = config.builtin_max_connections
            row.builtin_keepalive_timeout = config.builtin_keepalive_timeout
            row.builtin_qos = config.builtin_qos
            row.builtin_keepalive = config.builtin_keepalive
            row.builtin_tls_enabled = config.builtin_tls_enabled
            row.builtin_tls_ca_cert_path = config.builtin_tls_ca_cert_path
            row.builtin_tls_client_cert_path = config.builtin_tls_client_cert_path
            row.builtin_tls_client_key_path = config.builtin_tls_client_key_path
            row.builtin_tls_insecure_skip_verify = config.builtin_tls_insecure_skip_verify
            row.builtin_tls_require_client_cert = config.builtin_tls_require_client_cert

            # External broker fields
            row.external_protocol = config.external_protocol
            row.external_host = config.external_host
            row.external_port = config.external_port
            row.external_username = config.external_username
            row.external_password = config.external_password
            row.external_qos = config.external_qos
            row.external_keepalive = config.external_keepalive
            row.external_tls_enabled = config.external_tls_enabled
            row.external_tls_ca_cert_path = config.external_tls_ca_cert_path
            row.external_tls_client_cert_path = config.external_tls_client_cert_path
            row.external_tls_client_key_path = config.external_tls_client_key_path
            row.external_tls_insecure_skip_verify = config.external_tls_insecure_skip_verify

            # Legacy fields (keep for backward compatibility, but prefer new fields)
            if config.protocol is not None:
                row.protocol = config.protocol
            if config.host is not None:
                row.host = config.host
            if config.port is not None:
                row.port = config.port
            if config.username is not None:
                row.username = config.username
            if config.password is not None:
                row.password = config.password
            if config.qos is not None:
                row.qos = config.qos
            if config.keepalive is not None:
                row.keepalive = config.keepalive
            if config.tls_enabled is not None:
                row.tls_enabled = config.tls_enabled
            if config.tls_ca_cert_path is not None:
                row.tls_ca_cert_path = config.tls_ca_cert_path
            if config.tls_client_cert_path is not None:
                row.tls_client_cert_path = config.tls_client_cert_path
            if config.tls_client_key_path is not None:
                row.tls_client_key_path = config.tls_client_key_path
            if config.tls_insecure_skip_verify is not None:
                row.tls_insecure_skip_verify = config.tls_insecure_skip_verify

            db.commit()
            db.refresh(row)

            return self.load_config()
        finally:
            db.close()


# Global shared instance
mqtt_config_service = MQTTConfigService()
