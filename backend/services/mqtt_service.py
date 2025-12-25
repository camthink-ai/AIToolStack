"""MQTT service: subscribe to images uploaded by devices"""
import json
import base64
import re
import uuid
import logging
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from collections import deque
import paho.mqtt.client as mqtt
from PIL import Image as PILImage
import io
import hashlib

from backend.config import settings
from backend.models.database import SessionLocal, Image, Project, Device, DeviceReport
from backend.services.websocket_manager import websocket_manager
from sqlalchemy.orm import joinedload
from backend.services.mqtt_broker import builtin_mqtt_broker
from backend.services.mqtt_config_service import MQTTConfig, mqtt_config_service
from backend.services.external_broker_service import external_broker_service

logger = logging.getLogger(__name__)


class MQTTService:
    """MQTT subscription service"""
    
    def __init__(self, config_service=mqtt_config_service):
        # For backward compatibility, self.client keeps a reference to the
        # first created MQTT client (primary broker). When multi-broker
        # support is enabled (mode == \"both\"), additional clients are stored
        # in self.clients and callbacks receive the actual client instance.
        self.client: Optional[mqtt.Client] = None
        self.clients: list[mqtt.Client] = []
        # Track last known broker endpoints and their connection states.
        # Each item: {"type": "builtin"|"external", "host": str, "port": int, "connected": bool}
        self._endpoints: list[dict] = []
        self.is_connected = False
        # For multi-broker scenarios, these fields reflect the *primary* broker
        # (the first one in the list); logging for each client uses per-client
        # attributes instead.
        self.broker_host = ""  # Save current connected broker address (primary)
        self.broker_port = 0
        self._config_service = config_service
        self._config: Optional[MQTTConfig] = None
        
        # Connection statistics and monitoring
        self.connection_count = 0
        self.disconnection_count = 0
        self.last_connect_time: Optional[float] = None
        self.last_disconnect_time: Optional[float] = None
        self.recent_errors = deque(maxlen=10)  # Keep last 10 errors
        self.message_count = 0
        self.last_message_time: Optional[float] = None
        
        # Deduplication: Track processed messages to prevent duplicate image uploads
        # Format: {message_id: timestamp}
        # Messages older than 1 hour are automatically removed
        self._processed_messages: dict[str, float] = {}
        self._dedup_cleanup_interval = 3600  # 1 hour in seconds
        self._dedup_lock = threading.Lock()  # Lock for thread-safe deduplication
        
        # Device status check timer (periodic task to mark offline devices)
        self._status_check_timer: Optional[threading.Timer] = None
        self._status_check_interval = 60  # Check every 60 seconds
        self._device_timeout_seconds = 300  # 5 minutes timeout
    
    def on_connect(self, client, userdata, flags, rc):
        """Connection callback"""
        # #region agent log
        try:
            with open('/Users/shenmingming/Desktop/AIToolStack/.cursor/debug.log', 'a') as f:
                import json
                broker_host = getattr(client, "_camthink_broker_host", "unknown")
                broker_port = getattr(client, "_camthink_broker_port", 0)
                broker_type = getattr(client, "_camthink_broker_type", "unknown")
                f.write(json.dumps({
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "A",
                    "location": "mqtt_service.py:72",
                    "message": "on_connect callback called",
                    "data": {
                        "rc": rc,
                        "broker_host": broker_host,
                        "broker_port": broker_port,
                        "broker_type": broker_type,
                        "flags": str(flags) if flags else None
                    },
                    "timestamp": int(time.time() * 1000)
                }) + "\n")
        except: pass
        # #endregion
        
        if rc == 0:
            self.is_connected = True
            self.connection_count += 1
            self.last_connect_time = time.time()
            # Prefer per-client broker metadata if available
            broker_host = getattr(client, "_camthink_broker_host", self.broker_host)
            broker_port = getattr(client, "_camthink_broker_port", self.broker_port)
            broker_index = getattr(client, "_camthink_broker_index", None)
            if isinstance(broker_index, int) and 0 <= broker_index < len(self._endpoints):
                self._endpoints[broker_index]["connected"] = True
            logger.info(f"Connected to broker at {broker_host}:{broker_port}")
            # Subscribe to image upload topic pattern (project-based uploads)
            upload_topic_pattern = getattr(client, "_camthink_topic_pattern", settings.MQTT_UPLOAD_TOPIC)
            broker_qos = getattr(client, "_camthink_broker_qos", settings.MQTT_QOS)
            try:
                result = client.subscribe(upload_topic_pattern, qos=broker_qos)
                if result[0] == mqtt.MQTT_ERR_SUCCESS:
                    logger.info(f"Subscribed to upload topic pattern: {upload_topic_pattern} (QoS: {broker_qos})")
                else:
                    logger.error(f"Failed to subscribe to upload topic {upload_topic_pattern}: error code {result[0]}")
            except Exception as e:
                logger.error(f"Error subscribing to upload topic: {e}")

            # Subscribe to device uplink topic for unified device-side reporting
            try:
                # Unified uplink: device/{device_id}/uplink
                device_uplink_topic = "device/+/uplink"
                result = client.subscribe(device_uplink_topic, qos=broker_qos)
                if result[0] == mqtt.MQTT_ERR_SUCCESS:
                    logger.info(f"Subscribed to device uplink topic pattern: {device_uplink_topic} (QoS: {broker_qos})")
                else:
                    logger.error(f"Failed to subscribe to device uplink topic {device_uplink_topic}: error code {result[0]}")
            except Exception as e:
                logger.error(f"Error subscribing to device uplink topic: {e}")

            # Subscribe to Mosquitto SYS topics for device connection status tracking
            try:
                # Mosquitto publishes client connect/disconnect events to SYS topics
                # Format: $SYS/broker/clients/connected or $SYS/broker/clients/disconnected
                sys_topics = [
                    "$SYS/broker/clients/connected",
                    "$SYS/broker/clients/disconnected"
                ]
                for sys_topic in sys_topics:
                    result = client.subscribe(sys_topic, qos=1)
                    if result[0] == mqtt.MQTT_ERR_SUCCESS:
                        logger.info(f"Subscribed to SYS topic: {sys_topic} for device connection tracking")
                    else:
                        logger.warning(f"Failed to subscribe to SYS topic {sys_topic}: error code {result[0]} (may not be supported by this broker)")
            except Exception as e:
                logger.warning(f"Error subscribing to SYS topics for device connection tracking: {e} (SYS topics may not be available)")
        else:
            error_msg = self._get_connection_error_message(rc)
            logger.error(f"Connection failed with code {rc}: {error_msg}")
            # Update endpoint connection status to False on connection failure
            broker_index = getattr(client, "_camthink_broker_index", None)
            if isinstance(broker_index, int) and 0 <= broker_index < len(self._endpoints):
                self._endpoints[broker_index]["connected"] = False
            # Update overall connection status
            self.is_connected = any(ep.get("connected", False) for ep in self._endpoints)
            self.recent_errors.append({
                'time': time.time(),
                'type': 'connect_error',
                'code': rc,
                'message': error_msg
            })
    
    def on_disconnect(self, client, userdata, rc):
        """Disconnect callback"""
        broker_index = getattr(client, "_camthink_broker_index", None)
        broker_host = getattr(client, "_camthink_broker_host", "unknown")
        broker_port = getattr(client, "_camthink_broker_port", 0)
        broker_type = getattr(client, "_camthink_broker_type", "unknown")
        
        # #region agent log
        try:
            with open('/Users/shenmingming/Desktop/AIToolStack/.cursor/debug.log', 'a') as f:
                import json
                f.write(json.dumps({
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "A",
                    "location": "mqtt_service.py:135",
                    "message": "on_disconnect callback called",
                    "data": {
                        "rc": rc,
                        "broker_host": broker_host,
                        "broker_port": broker_port,
                        "broker_type": broker_type,
                        "is_abnormal": rc != 0
                    },
                    "timestamp": int(time.time() * 1000)
                }) + "\n")
        except: pass
        # #endregion
        
        if isinstance(broker_index, int) and 0 <= broker_index < len(self._endpoints):
            self._endpoints[broker_index]["connected"] = False
        # Recompute overall connection flag based on all endpoints
        # If any broker is connected, consider service as connected
        self.is_connected = any(ep.get("connected", False) for ep in self._endpoints)
        self.disconnection_count += 1
        self.last_disconnect_time = time.time()
        
        if rc != 0:
            # Abnormal disconnect - paho-mqtt will automatically try to reconnect
            error_msg = self._get_disconnect_error_message(rc)
            broker_host = getattr(client, "_camthink_broker_host", self.broker_host)
            broker_port = getattr(client, "_camthink_broker_port", self.broker_port)
            logger.warning(f"Disconnected from broker {broker_host}:{broker_port} unexpectedly (rc={rc}): {error_msg}")
            self.recent_errors.append({
                'time': time.time(),
                'type': 'disconnect_error',
                'code': rc,
                'message': error_msg
            })
        else:
            # Normal disconnect
            logger.info("Disconnected from broker normally")
        
        # If abnormal disconnect (rc != 0), paho-mqtt will automatically try to reconnect
        # We don't need to manually handle reconnection logic
    
    def on_message(self, client, userdata, msg):
        """Message receive callback"""
        try:
            self.message_count += 1
            self.last_message_time = time.time()
            
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            # Parse JSON payload
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error for topic {topic}: {e}")
                # Try to extract req_id and device_id from raw payload
                req_id = ''
                device_id = ''
                try:
                    temp_data = json.loads(payload)  # This will fail, but we try
                except Exception:
                    pass
                self._send_error_response(client, req_id, device_id, "Invalid JSON format")
                return
            
            # Handle MQTT broker SYS topics for device connection tracking
            if topic.startswith("$SYS/"):
                self._handle_sys_topic_message(topic, payload)
                return

            # Route by topic:
            # - annotator/upload/{project_id}: generic project-based upload (existing behavior)
            # - device/{device_id}/uplink: unified device uplink (status/image/AI result, etc.)
            if topic.startswith("annotator/upload/"):
                parts = topic.split('/')
                if len(parts) < 3:
                    logger.warning(f"Invalid upload topic format: {topic}")
                    return
                project_id = parts[2]

                # Normalize payload for different device types (NE101 / NE301 / others)
                data = self._normalize_payload(data, topic)

                # Handle image upload directly to specified project
                self._handle_image_upload(client, project_id, data, topic)

            elif topic.startswith("device/"):
                parts = topic.split('/')
                if len(parts) < 3:
                    logger.warning(f"Invalid device topic format: {topic}")
                    return
                device_id_from_topic = parts[1]
                sub_topic = parts[2]

                # Unified uplink: device/{device_id}/uplink
                if sub_topic == "uplink":
                    logger.info(f"Received device uplink message from {device_id_from_topic} on topic {topic}")
                    if isinstance(data, dict):
                        logger.debug(f"Device uplink payload keys: {list(data.keys())}, has image_data: {'image_data' in data}, has device_info: {isinstance(data.get('device_info'), dict)}")
                    self._handle_device_uplink_message(client, device_id_from_topic, data, topic)
                else:
                    logger.warning(f"Unknown device sub-topic '{sub_topic}' in topic {topic}")

            else:
                logger.warning(f"Ignoring message on unsupported topic: {topic}")
            
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            req_id = ''
            device_id = ''
            try:
                data = json.loads(msg.payload.decode('utf-8'))
                req_id = data.get('req_id', '')
                device_id = data.get('device_id', '')
            except:
                pass
            self._send_error_response(client, req_id, device_id, str(e))
    
    def _get_message_id(self, data: dict, topic: str) -> str:
        """Generate a unique message ID for deduplication.
        
        Uses content hash to ensure different data gets different IDs.
        For image uploads, also includes image_id if available for better uniqueness.
        
        IMPORTANT: Different data MUST generate different IDs. Only identical content should have the same ID.
        """
        # Create a hash based on the full content to ensure different data = different ID
        # Sort keys to ensure consistent hashing
        content_str = json.dumps(data, sort_keys=True)
        content_hash = hashlib.md5(content_str.encode()).hexdigest()
        
        # Include device_id and topic for additional context
        device_id = data.get('device_id', 'unknown')
        
        # For image data, also include image_id from metadata if available
        if 'image_data' in data:
            metadata = data.get('metadata', {})
            image_id = metadata.get('image_id', '')
            if image_id:
                msg_id = f"img_{device_id}_{image_id}_{content_hash[:8]}"
                logger.debug(f"Generated message_id for image data: {msg_id[:50]}... (hash: {content_hash[:8]})")
                return msg_id
        
        # For requests with req_id, include it but still use content hash
        req_id = data.get('req_id', '')
        if req_id:
            msg_id = f"req_{device_id}_{req_id}_{content_hash[:8]}"
            logger.debug(f"Generated message_id with req_id: {msg_id[:50]}... (hash: {content_hash[:8]})")
            return msg_id
        
        # Default: use content hash with device_id and topic
        msg_id = f"msg_{device_id}_{content_hash[:16]}"
        logger.debug(f"Generated default message_id: {msg_id[:50]}... (hash: {content_hash[:16]})")
        return msg_id

    def _normalize_payload(self, data: dict, topic: str) -> dict:
        """Normalize different device payload formats to internal standard format.

        Internal target format (new format already supported by _handle_image_upload):
        {
            "req_id": "...",
            "device_id": "...",
            "image_data": "base64 or data:image/jpeg;base64,...",
            "encoding": "base64",
            "metadata": {...},
            "device_info": {...},   # optional, standardized device info
            "ai_result": {...}      # optional, passthrough from device
        }

        Supported external formats:
        - NE101:
          {
            "ts": 1740640441620,
            "values": {
              "devName": "...",
              "devMac": "...",
              "devSn": "...",
              "hwVersion": "...",
              "fwVersion": "...",
              "battery": 84,
              "batteryVoltage": 4200,
              "snapType": "Button",
              "localtime": "2025-10-10 12:13:04",
              "imageSize": 68255,
              "image": "data:image/jpeg;base64,..."
            }
          }

        - NE301:
          {
            "metadata": {...},
            "device_info": {...},
            "ai_result": {...},
            "image_data": "data:image/jpeg;base64,...",
            "encoding": "base64"
          }
        """
        try:
            # NE101 format: top-level "values" with "image"
            if isinstance(data, dict) and isinstance(data.get("values"), dict) and "image" in data["values"]:
                values = data["values"]
                ts = data.get("ts")
                # Choose device id: SN > MAC > topic suffix
                device_id = values.get("devSn") or values.get("devMac") or (topic.split("/")[-1] if "/" in topic else "unknown")

                # Standardized device_info section
                device_info = {
                    "device_name": values.get("devName"),
                    "mac_address": values.get("devMac"),
                    "serial_number": values.get("devSn"),
                    "hardware_version": values.get("hwVersion"),
                    "software_version": values.get("fwVersion"),
                    "power_supply_type": "battery",
                    "battery_percent": values.get("battery"),
                    "battery_voltage": values.get("batteryVoltage"),
                    "communication_type": None,
                }

                metadata = {
                    "image_id": f"ne101_{device_id}_{int(time.time())}",
                    "timestamp": int((ts or 0) / 1000) if isinstance(ts, (int, float)) else int(time.time()),
                    "format": "jpeg",
                    "width": None,
                    "height": None,
                    "size": values.get("imageSize"),
                    "snap_type": values.get("snapType"),
                    "localtime": values.get("localtime"),
                }

                normalized = {
                    "req_id": data.get("req_id", str(uuid.uuid4())),
                    "device_id": device_id,
                    "image_data": values.get("image", ""),
                    "encoding": "base64",
                    "metadata": metadata,
                    "device_info": device_info,
                }
                return normalized

            # NE301 format: has "image_data" (may or may not have device_info)
            if "image_data" in data:
                # NE301 may have device_info, but it's optional
                device_info = data.get("device_info") or {}
                if not isinstance(device_info, dict):
                    device_info = {}
                
                # Derive device_id if missing
                if not data.get("device_id"):
                    device_id = (
                        device_info.get("serial_number")
                        or device_info.get("mac_address")
                        or device_info.get("device_name")
                        or (topic.split("/")[-1] if "/" in topic else "unknown")
                    )
                    data["device_id"] = device_id
                
                # If device_info is missing or incomplete, try to create a minimal one
                if not device_info or not isinstance(device_info, dict):
                    data["device_info"] = device_info

                # Ensure metadata exists and contains at least image_id/timestamp/format/size
                metadata = data.get("metadata") or {}
                if "image_id" not in metadata:
                    metadata["image_id"] = f"ne301_{data.get('device_id', 'unknown')}_{int(time.time())}"
                if "timestamp" not in metadata:
                    metadata["timestamp"] = int(time.time())
                if "format" not in metadata:
                    metadata["format"] = "jpeg"
                if "size" not in metadata and isinstance(metadata.get("width"), int) and isinstance(metadata.get("height"), int):
                    metadata["size"] = None  # Unknown exact size, keep placeholder

                data["metadata"] = metadata
                # Keep device_info / ai_result as-is for possible future use
                logger.debug(f"Normalized NE301 payload for device {data.get('device_id', 'unknown')}")
                return data

            # Generic format: top-level "image" field (for devices like camera_01)
            if "image" in data and data.get("image"):
                # Ensure device_id is set
                if not data.get("device_id"):
                    device_id = topic.split("/")[-1] if "/" in topic else "unknown"
                    data["device_id"] = device_id
                
                # Convert "image" to "image_data" for consistency
                if "image_data" not in data:
                    data["image_data"] = data.pop("image")
                
                # Ensure encoding is set
                if "encoding" not in data:
                    data["encoding"] = "base64"
                
                # Ensure metadata exists
                metadata = data.get("metadata") or {}
                if "image_id" not in metadata:
                    metadata["image_id"] = f"generic_{data.get('device_id', 'unknown')}_{int(time.time())}"
                if "timestamp" not in metadata:
                    # Try to extract from data.timestamp or use current time
                    timestamp = data.get("timestamp")
                    if isinstance(timestamp, (int, float)):
                        metadata["timestamp"] = int(timestamp) if timestamp < 1e10 else int(timestamp / 1000)
                    else:
                        metadata["timestamp"] = int(time.time())
                if "format" not in metadata:
                    metadata["format"] = "jpeg"  # Default to jpeg
                data["metadata"] = metadata
                
                logger.debug(f"Normalized generic payload (top-level image) for device {data.get('device_id', 'unknown')}")
                return data

        except Exception as e:
            logger.warning(f"Failed to normalize payload for topic {topic}: {e}", exc_info=True)
            # Log payload structure for debugging
            if isinstance(data, dict):
                logger.debug(f"Payload structure: keys={list(data.keys())[:10]}, has image_data={'image_data' in data}, has device_info={'device_info' in data}")

        # Default: return original data, but ensure device_id is set
        if isinstance(data, dict) and not data.get("device_id"):
            device_id = topic.split("/")[-1] if "/" in topic else "unknown"
            data["device_id"] = device_id
            logger.debug(f"Set default device_id for unnormalized payload: {device_id}")
        
        return data
    
    def _is_duplicate_message(self, message_id: str) -> bool:
        """Check if message has already been processed.
        
        Also performs cleanup of old entries to prevent memory growth.
        Thread-safe implementation to handle concurrent message processing from multiple brokers.
        
        Note: This only prevents processing the EXACT same message content (same hash) within a short time window.
        Different data will have different message_ids and will NOT be blocked.
        """
        with self._dedup_lock:
            current_time = time.time()

            # Cleanup old entries (older than 1 hour)
            if len(self._processed_messages) > 1000:  # Only cleanup when dict is large
                cutoff_time = current_time - self._dedup_cleanup_interval
                self._processed_messages = {
                    msg_id: ts for msg_id, ts in self._processed_messages.items()
                    if ts > cutoff_time
                }

            # Check if message was already processed
            if message_id in self._processed_messages:
                logger.debug(
                    f"Message {message_id[:30]}... was processed "
                    f"{current_time - self._processed_messages[message_id]:.2f}s ago, skipping duplicate"
                )
                return True

            # Mark as processed (but don't commit until processing succeeds)
            # Note: We mark it here to prevent race conditions from multiple brokers
            self._processed_messages[message_id] = current_time
            logger.debug(f"Marking message {message_id[:30]}... as processed")
            return False

    def _handle_device_uplink_message(self, client, device_id_from_topic: str, data: dict, topic: str) -> None:
        """Handle unified device uplink on topic device/{device_id}/uplink.

        - 对 NE101 / NE301 等设备，上行 payload 可能同时包含状态、图片、AI 结果等信息。
        - 我们通过 payload 内容来区分：
          - 如果包含图片字段（NE101 的 values.image，NE301 的 image_data），则尝试进行图片存储：
            - 先归一化 payload（_normalize_payload）
            - 利用设备绑定的 project_id 将图片写入对应项目
          - 无论是否有图片，都会更新/注册设备信息（_upsert_device_from_payload）。
        """
        # Determine device_id with clear priority:
        # 1) If topic carries device_id (device/{device_id}/uplink), ALWAYS use device_id_from_topic.
        # 2) Only when topic does NOT include device_id (e.g., device/uplink), fall back to payload.device_id.
        device_id = device_id_from_topic or data.get("device_id")
        if not device_id:
            logger.warning(
                f"Received device uplink message without device_id in topic or payload (topic={topic}), "
                "using 'unknown' as device_id"
            )
            device_id = "unknown"
        # Keep payload.device_id in sync for downstream processing / logging
        data["device_id"] = device_id
        
        # Check for duplicate message first (before any processing)
        # Note: This check is based on content hash, so different data will have different IDs
        # Only skip if the EXACT same content was processed recently
        message_id = self._get_message_id(data, topic)
        logger.debug(f"Generated message_id for {device_id}: {message_id[:60]}...")
        
        if self._is_duplicate_message(message_id):
            logger.info(f"✗ Duplicate device uplink message detected for {device_id} (message_id: {message_id[:50]}...), skipping processing - this is expected for identical content from multiple brokers")
            # Still send success response to avoid device retries
            req_id = data.get('req_id', '')
            if req_id:
                self._send_success_response(client, req_id, device_id, "Message already processed.")
            return
        
        logger.info(f"✓ Processing new device uplink message for {device_id} (message_id: {message_id[:50]}...)")

        # 1) 先更新/注册设备信息（不依赖图片）
        try:
            # 如果没有 device_info，尝试从不同格式中提取
            if "device_info" not in data or not isinstance(data.get("device_info"), dict):
                # 尝试 NE101 风格的 values
                values = data.get("values") or {}
                if isinstance(values, dict) and values:
                    data["device_info"] = {
                        "device_name": values.get("devName"),
                        "mac_address": values.get("devMac"),
                        "serial_number": values.get("devSn"),
                        "hardware_version": values.get("hwVersion"),
                        "software_version": values.get("fwVersion"),
                        "power_supply_type": "battery",
                        "battery_percent": values.get("battery"),
                        "battery_voltage": values.get("batteryVoltage"),
                    }
                # 如果还是没有，尝试从顶层字段提取（NE301可能直接放在顶层）
                elif not data.get("device_info"):
                    device_info = {}
                    # 尝试从顶层字段提取设备信息
                    if data.get("serial_number"):
                        device_info["serial_number"] = data.get("serial_number")
                    if data.get("mac_address"):
                        device_info["mac_address"] = data.get("mac_address")
                    if data.get("device_name"):
                        device_info["device_name"] = data.get("device_name")
                    if device_info:
                        data["device_info"] = device_info
            
            # 这里 project_id 由业务层绑定，不在纯上行时强制改变
            self._upsert_device_from_payload(project_id=None, device_id=device_id, data=data)
            logger.debug(f"Device {device_id} upserted from uplink message")
        except Exception as e:
            logger.error(f"Failed to upsert device from uplink (device_id={device_id}): {e}", exc_info=True)

        # 2) 判断是否有图片数据，如果没有则仅作为状态上报处理
        # Check for image data in multiple formats:
        # - NE301: top-level "image_data"
        # - NE101: "values.image"
        # - Generic: top-level "image" (for devices like camera_01)
        has_image = False
        image_source = None
        if isinstance(data, dict):
            # NE301 风格: 顶层有 image_data
            if "image_data" in data and data.get("image_data"):
                has_image = True
                image_source = "image_data (NE301)"
            # NE101 风格: values.image
            elif isinstance(data.get("values"), dict) and "image" in data["values"] and data["values"].get("image"):
                has_image = True
                image_source = "values.image (NE101)"
            # Generic 风格: 顶层有 image (for devices like camera_01)
            elif "image" in data and data.get("image"):
                has_image = True
                image_source = "image (Generic)"

        if has_image:
            logger.info(f"Device {device_id} uplink message contains image data (source: {image_source})")
        else:
            # 纯状态/心跳上行，不做图片处理，但设备信息已经在上面的_upsert_device_from_payload中更新了
            logger.info(f"Device {device_id} uplink message (no image data). Device record updated.")
            if data.get("req_id"):
                self._send_success_response(client, data.get("req_id", ""), device_id, "Device report received.")
            return

        # 3) 有图片时，按统一的图片处理逻辑存到绑定项目
        # 先做 NE101 / NE301 的归一化，得到标准 image_data/metadata/device_info
        logger.debug(f"Normalizing payload for device {device_id} (topic: {topic})")
        normalized = self._normalize_payload(data, topic)

        # 绑定逻辑一律使用「主题中的 device_id」（即上面的 device_id）
        # 不允许归一化过程偷偷把 device_id 改成 SN/MAC，避免与前端绑定的设备 ID 不一致
        original_normalized_device_id = normalized.get("device_id")
        normalized_device_id = device_id or data.get("device_id") or device_id_from_topic
        normalized["device_id"] = normalized_device_id

        # 日志中仍然记录如果归一化曾经设置过另一个 device_id，方便排查
        if original_normalized_device_id and original_normalized_device_id != normalized_device_id:
            logger.info(
                "Normalized payload attempted to change device_id "
                f"from {normalized_device_id} to {original_normalized_device_id}; "
                "using topic/device_id_from_uplink for binding consistency."
            )
        else:
            logger.debug(f"Device ID used for binding and saving image: {normalized_device_id}")
        
        # 验证归一化后的数据是否包含图像数据
        if "image_data" not in normalized or not normalized.get("image_data"):
            logger.warning(f"Normalized payload for {normalized_device_id} does not contain image_data. Original data keys: {list(data.keys())}, normalized keys: {list(normalized.keys())}")
            # 如果归一化后没有图像数据，返回错误
            req_id = normalized.get("req_id", data.get("req_id", ""))
            self._send_error_response(client, req_id, normalized_device_id, "Image data not found in normalized payload")
            return

        logger.info(f"Normalized payload for {normalized_device_id} contains image_data (length: {len(normalized.get('image_data', ''))})")

        # 从设备表中查找绑定的所有项目（支持多项目绑定）
        db = SessionLocal()
        bound_projects = []
        try:
            device = db.query(Device).options(joinedload(Device.projects)).filter(Device.id == normalized_device_id).first()
            if device and device.projects:
                bound_projects = [p.id for p in device.projects]
                logger.info(f"Device {normalized_device_id} is bound to {len(bound_projects)} project(s): {bound_projects}")
            else:
                logger.warning(f"Device {normalized_device_id} not found or has no bound projects")
        except Exception as e:
            logger.error(f"Failed to lookup device binding for image upload (device_id={normalized_device_id}): {e}", exc_info=True)
        finally:
            db.close()

        req_id = normalized.get("req_id", "")

        if not bound_projects:
            # 设备尚未绑定任何项目，返回错误给设备但不抛异常
            error_msg = "Device is not bound to any project. Please bind device to a project in management console."
            logger.warning(f"{error_msg} (device_id={normalized_device_id})")
            self._send_error_response(client, req_id, normalized_device_id, error_msg)
            return

        # 将图片推送到所有绑定的项目（支持多项目绑定）
        # 为了支持多项目，我们需要为每个项目单独处理图像上传
        # 但由于图像数据是相同的，我们需要创建一个共享的图像ID或为每个项目创建副本
        
        success_count = 0
        error_count = 0
        successful_projects = []
        
        # Create a copy of normalized data for each project to avoid conflicts
        # Generate base message_id for the normalized data (before project-specific handling)
        base_message_id = self._get_message_id(normalized, topic)
        logger.debug(f"Base message_id for device {normalized_device_id}: {base_message_id[:50]}...")
        
        for project_id in bound_projects:
            try:
                logger.info(f"Attempting to save image from device {normalized_device_id} to project {project_id}")
                # Create a copy of normalized data for this project
                project_normalized = normalized.copy()
                # Generate a project-specific message_id for deduplication to allow saving to multiple projects
                project_specific_message_id = f"{base_message_id}_project_{project_id}"
                # Pass the project-specific message_id to _handle_image_upload via a temporary field
                project_normalized['_dedup_message_id'] = project_specific_message_id
                # The _handle_image_upload will save the image to the specific project
                self._handle_image_upload(client, project_id, project_normalized, topic)
                success_count += 1
                successful_projects.append(project_id)
                logger.info(f"✓ Image from device {normalized_device_id} successfully saved to project {project_id}")
            except Exception as e:
                error_count += 1
                logger.error(f"✗ Failed to save image from device {normalized_device_id} to project {project_id}: {e}", exc_info=True)
                # Don't re-raise here - we want to continue trying other projects even if one fails
        
        # 如果有成功保存到至少一个项目，返回成功；否则返回错误
        if success_count > 0:
            logger.info(f"Image from device {normalized_device_id} saved to {success_count} project(s), {error_count} failed")
            # Send success response with first successful project ID (for backward compatibility)
            self._send_success_response(client, req_id, normalized_device_id, successful_projects[0] if successful_projects else bound_projects[0])
        else:
            error_msg = f"Failed to save image to any bound project ({error_count} project(s) failed)"
            logger.error(f"{error_msg} (device_id={normalized_device_id})")
            self._send_error_response(client, req_id, normalized_device_id, error_msg)
    
    def _handle_image_upload(self, client, project_id: str, data: dict, topic: str):
        """Handle image upload"""
        logger.info(f"Processing image upload for project {project_id}, device {data.get('device_id', 'unknown')}")
        
        # Check for duplicate messages to prevent processing the same image multiple times
        # This can happen when multiple MQTT clients subscribe to the same topic
        # NOTE: For multi-project binding, we need to allow the same image to be saved to different projects
        # So we include project_id in the message_id to allow saving to multiple projects
        
        # Use project-specific message_id if provided (from _handle_device_uplink_message), otherwise generate one
        project_specific_message_id = data.pop('_dedup_message_id', None)
        if project_specific_message_id is None:
            # Fallback: generate message_id if not provided
            message_id = self._get_message_id(data, topic)
            project_specific_message_id = f"{message_id}_project_{project_id}"
        
        if self._is_duplicate_message(project_specific_message_id):
            logger.warning(f"Duplicate message detected for project {project_id} (message_id: {project_specific_message_id[:50]}...), skipping processing")
            # Still send success response to avoid device retries
            req_id = data.get('req_id', '')
            device_id = data.get('device_id', 'unknown')
            self._send_success_response(client, req_id, device_id, project_id)
            return
        
        logger.debug(f"Processing image upload for project {project_id} (message_id: {project_specific_message_id[:50]}...)")
        
        # Adapt to new data structure
        # Support two formats:
        # 1. New format: { "image_data": "...", "encoding": "...", "metadata": {...} }
        # 2. Old format: { "req_id": "...", "device_id": "...", "image": {...} }
        
        # Try new format
        if 'image_data' in data:
            # New format
            req_id = data.get('req_id', str(uuid.uuid4()))
            device_id = data.get('device_id', topic.split('/')[-1] if '/' in topic else 'unknown')
            metadata = data.get('metadata', {})
            encoding = data.get('encoding', 'base64')
            base64_data = data.get('image_data', '')
            
            if not base64_data:
                error_msg = "image_data field is empty"
                logger.error(f"{error_msg} for project {project_id}, device {device_id}")
                self._send_error_response(client, req_id, device_id, error_msg)
                return
            
            # Extract information from metadata
            image_id = metadata.get('image_id', f'img_{int(datetime.utcnow().timestamp())}')
            timestamp = metadata.get('timestamp', int(datetime.utcnow().timestamp()))
            image_format = metadata.get('format', 'jpeg').lower()
            # If metadata has dimension information, use it first
            metadata_width = metadata.get('width')
            metadata_height = metadata.get('height')
            
            # Filename will be generated later based on device_id, device_name and timestamp
        else:
            # Old format (backward compatible)
            req_id = data.get('req_id', str(uuid.uuid4()))
            device_id = data.get('device_id', 'unknown')
            timestamp = data.get('timestamp', int(datetime.utcnow().timestamp()))
            image_data = data.get('image', {})
            metadata = data.get('metadata', {})
            
            image_format = image_data.get('format', 'jpg').lower()
            encoding = image_data.get('encoding', 'base64')
            base64_data = image_data.get('data', '')
            metadata_width = None
            metadata_height = None
        
        # Verify project exists & fetch device name (for filename)
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                error_msg = f"Project {project_id} not found"
                logger.warning(error_msg)
                self._send_error_response(client, req_id, device_id, error_msg)
                return

            # Build filename: deviceId_deviceName_timestamp.ext
            # 1) device name from payload device_info if available
            device_name = None
            device_info = data.get('device_info')
            if isinstance(device_info, dict):
                device_name = device_info.get('device_name') or device_info.get('name')

            # 2) fallback to DB device record
            if not device_name and device_id:
                try:
                    device_obj = db.query(Device).filter(Device.id == device_id).first()
                    if device_obj:
                        device_name = device_obj.name or device_obj.model or device_obj.id
                except Exception as dev_err:
                    logger.warning(f"Failed to fetch device name for filename (device_id={device_id}): {dev_err}")

            if not device_name:
                device_name = "device"

            # Sanitize device_name for filesystem (English only, no spaces/special chars)
            safe_device_name = re.sub(r'[^A-Za-z0-9_\\-]+', '_', str(device_name))

            # Format timestamp as yyyyMMdd_HHmmss
            try:
                # timestamp is in seconds
                ts_dt = datetime.utcfromtimestamp(int(timestamp))
            except Exception:
                ts_dt = datetime.utcnow()
            ts_str = ts_dt.strftime("%Y%m%d_%H%M%S")

            # Decide file extension
            if image_format in ['jpeg', 'jpg']:
                ext = 'jpg'
            elif image_format == 'png':
                ext = 'png'
            else:
                ext = image_format

            filename = f"{device_id}_{safe_device_name}_{ts_str}.{ext}"
            
            # Process base64 data, remove possible data URI prefix
            # Supported formats:
            # 1. data:image/jpeg;base64,xxxxx
            # 2. data:image/png;base64,xxxxx
            # 3. data:image/jpg;base64,xxxxx
            # 4. Pure base64 string
            if base64_data.startswith('data:'):
                # Contains data URI prefix, extract base64 part
                if ',' in base64_data:
                    base64_data = base64_data.split(',')[-1]
                else:
                    # If format is abnormal, try to remove data: prefix
                    base64_data = base64_data.replace('data:', '').split(';')[-1]
            elif ',' in base64_data:
                # Might contain other separators
                base64_data = base64_data.split(',')[-1]
            
            # Clean possible whitespace characters
            base64_data = base64_data.strip()
            
            # Base64 decode
            if encoding != 'base64':
                raise ValueError(f"Unsupported encoding: {encoding}")
            
            try:
                image_bytes = base64.b64decode(base64_data)
            except Exception as e:
                raise ValueError(f"Failed to decode base64 data: {str(e)}")
            
            # Verify image size
            size_mb = len(image_bytes) / (1024 * 1024)
            if size_mb > settings.MAX_IMAGE_SIZE_MB:
                raise ValueError(f"Image too large: {size_mb:.2f}MB (max: {settings.MAX_IMAGE_SIZE_MB}MB)")
            
            # Get image dimensions
            if metadata_width and metadata_height:
                # Use dimension information from metadata
                img_width = metadata_width
                img_height = metadata_height
            else:
                # Open image to get dimensions
                img = PILImage.open(io.BytesIO(image_bytes))
                img_width, img_height = img.size
            
            # Generate storage path
            project_dir = settings.DATASETS_ROOT / project_id / "raw"
            project_dir.mkdir(parents=True, exist_ok=True)
            
            # Handle filename conflicts
            file_path = project_dir / filename
            if file_path.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                timestamp_suffix = int(datetime.utcnow().timestamp())
                filename = f"{stem}_{timestamp_suffix}{suffix}"
                file_path = project_dir / filename
            
            # Save image
            file_path.write_bytes(image_bytes)
            
            # Generate relative path (only includes raw/filename, not project_id)
            relative_path = f"raw/{filename}"
            
            # Save to database
            db_image = Image(
                project_id=project_id,
                filename=filename,
                path=relative_path,
                width=img_width,
                height=img_height,
                status="UNLABELED",
                source=f"MQTT:{device_id}"
            )
            db.add(db_image)
            logger.debug(f"Adding image to database: project_id={project_id}, filename={filename}, path={relative_path}, source=MQTT:{device_id}")
            db.commit()
            db.refresh(db_image)
            
            # Ensure database transaction is fully committed before notifying frontend
            # This prevents frontend from refreshing before the new image is visible in the database
            image_id = db_image.id
            
            # Verify image was saved by querying it back
            verify_image = db.query(Image).filter(Image.id == image_id).first()
            if verify_image:
                logger.info(f"✓ Image saved and verified: {filename} ({img_width}x{img_height}) to project {project_id}, image_id: {image_id}, path: {relative_path}")
            else:
                logger.error(f"✗ Image save verification failed: image_id {image_id} not found in database after commit!")

            # Upsert device info based on payload (if device_id is known)
            try:
                self._upsert_device_from_payload(project_id, device_id, data)
            except Exception as dev_err:
                logger.error(f"Failed to upsert device info for device_id={device_id}: {dev_err}", exc_info=True)
            
            # Send success response
            self._send_success_response(client, req_id, device_id, project_id)
            
            # Notify frontend via WebSocket (after database commit is complete)
            try:
                websocket_manager.broadcast_project_update(project_id, {
                    "type": "new_image",
                    "image_id": image_id,
                    "filename": filename,
                    "path": relative_path,
                    "width": img_width,
                    "height": img_height
                })
                logger.debug(f"WebSocket notification sent for new image {image_id} in project {project_id}")
            except Exception as ws_error:
                logger.error(f"Failed to send WebSocket notification for new image: {ws_error}", exc_info=True)
                # Don't fail the whole operation if WebSocket notification fails
            
        except Exception as e:
            db.rollback()
            error_msg = f"Failed to save image: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self._send_error_response(client, req_id, device_id, error_msg)
            # Re-raise the exception so the caller knows the operation failed
            raise
        finally:
            db.close()

    def _upsert_device_from_payload(self, project_id: str, device_id: str, data: dict) -> None:
        """Create or update device record based on incoming payload.

        - Uses normalized payload produced by _normalize_payload.
        - Supports NE101 (device_info derived from values) and NE301 (device_info field).
        """
        if not device_id or device_id == "unknown":
            return

        device_info = data.get("device_info") or {}
        if not isinstance(device_info, dict):
            device_info = {}

        # Basic fields
        # Extract device name from payload (may be None if not provided)
        reported_name = device_info.get("device_name")
        # Only use device_id as fallback for new devices, not for updates
        name = reported_name if reported_name else None
        serial_number = device_info.get("serial_number")
        mac_address = device_info.get("mac_address")
        hardware_version = device_info.get("hardware_version")
        software_version = device_info.get("software_version")
        power_supply_type = device_info.get("power_supply_type")

        # Determine device type from metadata / device_info
        device_type = None
        model = None
        # Try to infer device type from reported name or metadata
        if isinstance(reported_name, str):
            if "NE101" in reported_name.upper():
                device_type = "NE101"
            elif "NE301" in reported_name.upper():
                device_type = "NE301"
        if not device_type and isinstance(device_info.get("device_name"), str):
            dn = device_info["device_name"]
            if "NE101" in dn.upper():
                device_type = "NE101"
            elif "NE301" in dn.upper():
                device_type = "NE301"
        # If device type cannot be determined, set to "Other"
        if not device_type:
            device_type = "Other"
        model = device_info.get("model") or device_info.get("device_name")

        # Optional network info (for future extension)
        last_ip = device_info.get("ip")

        now = datetime.utcnow()

        db = SessionLocal()
        try:
            device = db.query(Device).filter(Device.id == device_id).first()
            is_new_device = device is None
            if device is None:
                # New device: use reported name if available, otherwise use device_id
                final_name = name if name else device_id
                device = Device(
                    id=device_id,
                    name=final_name,
                    type=device_type,
                    model=model,
                    serial_number=serial_number,
                    mac_address=mac_address,
                    status="online",
                    last_seen=now,
                    last_ip=last_ip,
                    firmware_version=software_version,
                    hardware_version=hardware_version,
                    power_supply_type=power_supply_type,
                    last_report=json.dumps(data),
                )
                db.add(device)
            else:
                # Existing device: preserve manually set name
                # Check if name was manually set by user (stored in extra_info)
                name_manually_set = False
                if device.extra_info:
                    try:
                        extra_info = json.loads(device.extra_info)
                        name_manually_set = extra_info.get('name_manually_set', False)
                    except (json.JSONDecodeError, TypeError):
                        pass
                
                # Only update name if:
                # 1. Name was NOT manually set by user, AND
                # 2. Either device has no name (or name is device_id), OR reported name is available and valid
                if not name_manually_set:
                    current_name = device.name
                    # Check if current name is just the device_id (meaning it was auto-generated)
                    is_auto_generated_name = (current_name == device_id) or (not current_name)
                    
                    if is_auto_generated_name and name and name != device_id:
                        # Device was auto-discovered and now has a real name from payload
                        device.name = name
                    # If current name is valid and reported name is None or device_id, keep current name
                else:
                    # Device has a manually set name, preserve it
                    # Don't update name even if payload has device_name
                    pass
                # Update device type based on current payload
                # If we can't identify as NE101/NE301, set to "Other"
                device.type = device_type
                device.model = model or device.model
                device.serial_number = serial_number or device.serial_number
                device.mac_address = mac_address or device.mac_address
                # Note: project binding is managed via API, not via payload
                # project_id parameter is kept for backward compatibility but not used
                # Update status based on message reception (device is online when reporting)
                # Always set to online immediately when receiving messages
                device.status = "online"
                device.last_seen = now
                device.last_ip = last_ip or device.last_ip
                device.firmware_version = software_version or device.firmware_version
                device.hardware_version = hardware_version or device.hardware_version
                device.power_supply_type = power_supply_type or device.power_supply_type
                device.last_report = json.dumps(data)

            # Save report to history (with deduplication check)
            # Only skip if the EXACT same content was saved recently (within last 10 seconds)
            # This allows different data from the same device to be saved normally
            report_data_str = json.dumps(data, sort_keys=True)  # Sort keys for consistent comparison
            report_hash = hashlib.md5(report_data_str.encode()).hexdigest()
            
            # Check for duplicate report with EXACT same content in the last 10 seconds
            recent_cutoff = now - timedelta(seconds=10)
            existing_reports = db.query(DeviceReport).filter(
                DeviceReport.device_id == device_id,
                DeviceReport.created_at >= recent_cutoff
            ).order_by(DeviceReport.created_at.desc()).all()
            
            # Check if any existing report has the same content hash
            should_save_report = True
            for existing_report in existing_reports:
                try:
                    existing_data_str = existing_report.report_data
                    existing_hash = hashlib.md5(existing_data_str.encode()).hexdigest()
                    if existing_hash == report_hash:
                        should_save_report = False
                        logger.debug(f"Duplicate device report detected for {device_id} (same content hash: {report_hash[:8]}), skipping save")
                        break
                except Exception as e:
                    logger.warning(f"Failed to check duplicate report: {e}")
                    # If we can't check, err on the side of saving (don't lose data)
            
            if should_save_report:
                report = DeviceReport(
                    device_id=device_id,
                    report_data=report_data_str
                )
                db.add(report)
                logger.info(f"✓ Saving new device report for {device_id} (hash: {report_hash[:8]}, checked {len(existing_reports)} recent reports)")
            else:
                logger.info(f"✗ Skipping duplicate report save for {device_id} (content hash {report_hash[:8]} already exists in {len(existing_reports)} recent report(s))")
                # Log the existing report for debugging
                if existing_reports:
                    try:
                        existing_report = existing_reports[0]
                        existing_data_str = existing_report.report_data
                        existing_hash = hashlib.md5(existing_data_str.encode()).hexdigest()
                        if existing_hash == report_hash:
                            logger.debug(f"  Existing report ID: {existing_report.id}, created at: {existing_report.created_at}")
                    except Exception as e:
                        logger.debug(f"  Could not log existing report details: {e}")

            db.commit()
            
            # Broadcast device update via WebSocket
            try:
                websocket_manager.broadcast_device_update_sync({
                    "type": "device_update",
                    "device_id": device_id,
                    "action": "created" if is_new_device else "updated"
                })
                logger.debug(f"WebSocket notification sent for device update: {device_id} (action: {'created' if is_new_device else 'updated'})")
            except Exception as ws_error:
                logger.error(f"Failed to send WebSocket notification for device update: {ws_error}", exc_info=True)
                # Don't fail the whole operation if WebSocket notification fails
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    
    def _handle_sys_topic_message(self, topic: str, payload: str):
        """Handle MQTT broker SYS topic messages for device connection tracking.
        
        Mosquitto publishes client connect/disconnect events to:
        - $SYS/broker/clients/connected: client_id when a client connects
        - $SYS/broker/clients/disconnected: client_id when a client disconnects
        
        Note: The client_id in SYS messages may not directly match device_id,
        so we mainly use this for reference. Primary status tracking is based
        on message reception and periodic timeout checks.
        """
        try:
            # Extract client_id from SYS topic message
            # Format depends on broker, but typically it's just the client_id string
            client_id = payload.strip()
            
            if not client_id:
                return

            # Check if this client_id might be a device_id
            # Devices typically connect with their device_id as client_id or include it in the ID
            db = SessionLocal()
            try:
                # Try exact match first
                device = db.query(Device).filter(Device.id == client_id).first()
                
                # If not found, try to find device where client_id contains device_id or vice versa
                if not device:
                    # Search for devices where client_id might contain device info
                    devices = db.query(Device).all()
                    for d in devices:
                        if client_id.startswith(d.id) or d.id in client_id:
                            device = d
                            break

                if device:
                    now = datetime.utcnow()
                    status_changed = False
                    if "connected" in topic:
                        # Device connected
                        if device.status != "online":
                            device.status = "online"
                            device.last_seen = now
                            status_changed = True
                            logger.info(f"Device {device.id} connected (from SYS topic)")
                    elif "disconnected" in topic:
                        # Device disconnected
                        if device.status != "offline":
                            device.status = "offline"
                            status_changed = True
                            logger.info(f"Device {device.id} disconnected (from SYS topic)")
                    
                    db.commit()
                    
                    # Broadcast device update via WebSocket if status changed
                    if status_changed:
                        try:
                            websocket_manager.broadcast_device_update_sync({
                                "type": "device_update",
                                "device_id": device.id,
                                "action": "updated"
                            })
                            logger.debug(f"WebSocket notification sent for device status update: {device.id}")
                        except Exception as ws_error:
                            logger.error(f"Failed to send WebSocket notification for device status update: {ws_error}", exc_info=True)
            except Exception as e:
                db.rollback()
                logger.warning(f"Error handling SYS topic message for client {client_id}: {e}")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Error processing SYS topic message from {topic}: {e}")

    def _update_device_offline_status(self):
        """Periodically check and update device offline status.
        
        Devices that haven't reported data for more than timeout period
        will be marked as offline. This method is called periodically by a timer.
        """
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            timeout_threshold = now.timestamp() - self._device_timeout_seconds
            
            # Find devices that are online but haven't reported in timeout period
            devices = db.query(Device).filter(Device.status == "online").all()
            updated_count = 0
            
            for device in devices:
                if device.last_seen:
                    last_seen_ts = device.last_seen.timestamp()
                    if last_seen_ts < timeout_threshold:
                        device.status = "offline"
                        updated_count += 1
                        logger.debug(f"Device {device.id} marked as offline (last seen: {device.last_seen})")
            
            if updated_count > 0:
                db.commit()
                logger.info(f"Marked {updated_count} device(s) as offline due to timeout ({self._device_timeout_seconds}s)")
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating device offline status: {e}", exc_info=True)
        finally:
            db.close()
        
        # Schedule next check
        self._schedule_status_check()
    
    def _schedule_status_check(self):
        """Schedule the next device status check."""
        if self._status_check_timer:
            self._status_check_timer.cancel()
        
        self._status_check_timer = threading.Timer(
            self._status_check_interval,
            self._update_device_offline_status
        )
        self._status_check_timer.daemon = True
        self._status_check_timer.start()
    
    def _send_success_response(self, client, req_id: str, device_id: str, project_id: str):
        """Send success response"""
        if not device_id or device_id == 'unknown':
            return
        
        if not client or not self.is_connected:
            logger.warning(f"Cannot send success response: client not connected")
            return
        
        # Get broker-specific QoS
        broker_type = getattr(client, "_camthink_broker_type", "builtin")
        if self._config:
            qos = self._config.builtin_qos if broker_type == "builtin" else self._config.external_qos
        else:
            qos = settings.MQTT_QOS
        
        response_topic = f"{settings.MQTT_RESPONSE_TOPIC_PREFIX}/{device_id}"
        response = {
            "req_id": req_id,
            "status": "success",
            "code": 200,
            "message": f"Image saved to project {project_id}",
            "server_time": int(datetime.utcnow().timestamp())
        }
        
        try:
            result = client.publish(response_topic, json.dumps(response), qos=qos)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.warning(f"Failed to publish success response: error code {result.rc}")
        except Exception as e:
            logger.error(f"Error publishing success response: {e}")
    
    def _send_error_response(self, client, req_id: str, device_id: str, error_message: str):
        """Send error response"""
        if not device_id or device_id == 'unknown':
            return
        
        if not client or not self.is_connected:
            logger.warning(f"Cannot send error response: client not connected")
            return
        
        # Get broker-specific QoS
        broker_type = getattr(client, "_camthink_broker_type", "builtin")
        if self._config:
            qos = self._config.builtin_qos if broker_type == "builtin" else self._config.external_qos
        else:
            qos = settings.MQTT_QOS
        
        response_topic = f"{settings.MQTT_RESPONSE_TOPIC_PREFIX}/{device_id}"
        response = {
            "req_id": req_id,
            "status": "error",
            "code": 400,
            "message": error_message,
            "server_time": int(datetime.utcnow().timestamp())
        }
        
        try:
            result = client.publish(response_topic, json.dumps(response), qos=qos)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.warning(f"Failed to publish error response: error code {result.rc}")
        except Exception as e:
            logger.error(f"Error publishing error response: {e}")
    
    def _get_connection_error_message(self, rc: int) -> str:
        """Get human-readable connection error message"""
        error_messages = {
            1: "Connection refused - incorrect protocol version",
            2: "Connection refused - invalid client identifier",
            3: "Connection refused - server unavailable",
            4: "Connection refused - bad username or password",
            5: "Connection refused - not authorized",
            142: "Session taken over - another client with the same client ID is already connected. Use a unique client ID or ensure old connection is properly closed."
        }
        return error_messages.get(rc, f"Unknown error code {rc}")
    
    def _get_disconnect_error_message(self, rc: int) -> str:
        """Get human-readable disconnect error message"""
        # rc values for on_disconnect:
        # 0 = normal disconnect
        # Non-zero = unexpected disconnect
        # Common values: network error, timeout, etc.
        if rc == 0:
            return "Normal disconnect"
        elif rc == 7:
            return "Network error or timeout - connection may have timed out"
        elif rc == 142:
            return "Session taken over - another client with the same client ID connected. This is normal MQTT behavior when using duplicate client IDs."
        else:
            return f"Unexpected disconnect (error code: {rc})"
    
    def get_status(self) -> dict:
        """Get current MQTT service status"""
        return {
            'connected': self.is_connected,
            'broker': f"{self.broker_host}:{self.broker_port}" if self.broker_host else None,
            'brokers': [
                {
                    'type': ep.get("type"),
                    'host': ep.get("host"),
                    'port': ep.get("port"),
                    'broker_id': ep.get("broker_id"),  # For external brokers
                    'connected': ep.get("connected", False),
                }
                for ep in self._endpoints
            ],
            'connection_count': self.connection_count,
            'disconnection_count': self.disconnection_count,
            'message_count': self.message_count,
            'last_connect_time': self.last_connect_time,
            'last_disconnect_time': self.last_disconnect_time,
            'last_message_time': self.last_message_time,
            'recent_errors': list(self.recent_errors)
        }
    
    def start(self):
        """Start MQTT client using runtime configuration."""
        print("[MQTT Service] ===== start() method called =====")
        logger.info("[MQTT Service] ===== start() method called =====")
        # Load current config
        self._config = self._config_service.load_config()
        print(f"[MQTT Service] Config loaded: enabled={self._config.enabled}, builtin_protocol={self._config.builtin_protocol}")
        logger.info(f"[MQTT Service] Config loaded: enabled={self._config.enabled}, builtin_protocol={self._config.builtin_protocol}")
        print(f"[MQTT Service] Config auth: allow_anonymous={self._config.builtin_allow_anonymous}, username={self._config.builtin_username is not None}, password={self._config.builtin_password is not None}")
        logger.info(f"[MQTT Service] Config auth: allow_anonymous={self._config.builtin_allow_anonymous}, username={'***' if self._config.builtin_username else None}, password={'***' if self._config.builtin_password else None}")
        # Reset previous clients list
        self.clients = []
        self._endpoints = []

        if not self._config.enabled:
            logger.info("MQTT service is disabled in configuration")
            return
        
        try:
            cfg = self._config

            # Build a list of broker endpoints to connect to
            endpoints = []
            # Built-in broker endpoint used by the training/annotation service.
            # 当使用 Python 内置 aMQTT 时，指向容器内的 127.0.0.1:MQTT_BUILTIN_PORT；
            # 当关闭内置 broker（MQTT_USE_BUILTIN_BROKER=false）时，把“内置”端点指向外部 broker，
            # 这样前端的“内置 MQTT Broker 状态”就反映我们实际使用的 Mosquitto/外部服务。
            from backend.config import settings as app_settings  # 避免循环引用

            # Client connection automatically infers protocol and port from broker configuration
            # - If broker_protocol is "mqtts", client connects to TLS port (8883)
            # - If broker_protocol is "mqtt", client connects to TCP port (1883)
            # This ensures client always matches broker's actual configuration
            if cfg.builtin_protocol == "mqtts":
                # Broker is configured for MQTTS, client connects to TLS port
                builtin_port = cfg.builtin_tls_port or 8883
                client_protocol = "mqtts"
            else:
                # Broker is configured for MQTT, client connects to TCP port
                builtin_port = cfg.builtin_tcp_port or app_settings.MQTT_BUILTIN_PORT
                client_protocol = "mqtt"

            if app_settings.MQTT_USE_BUILTIN_BROKER:
                builtin_host = "127.0.0.1"
            else:
                # 不使用内置 broker 时，将“内置”连接指向外部 broker（例如 mosquitto）
                # 在 docker-compose 中通过 MQTT_BROKER=mosquitto 注入
                builtin_host = app_settings.MQTT_BROKER or "localhost"

            endpoints.append(
                {
                    "type": "builtin",
                    "host": builtin_host,
                    "port": builtin_port,
                    "protocol": client_protocol,  # Client protocol matches broker protocol automatically
                    "connected": False,
                }
            )
            # Multiple external brokers from database
            try:
                external_brokers = external_broker_service.get_enabled_brokers()
                for broker in external_brokers:
                    endpoints.append(
                        {
                            "type": "external",
                            "host": broker.host,
                            "port": broker.port,
                            "protocol": broker.protocol,
                            "connected": False,
                            "broker_id": broker.id,
                            "broker_name": broker.name,
                            "username": broker.username,
                            "password": broker.password,
                            "qos": broker.qos,
                            "keepalive": broker.keepalive,
                            "tls_enabled": broker.tls_enabled,
                            "tls_ca_cert_path": broker.tls_ca_cert_path,
                            "tls_client_cert_path": broker.tls_client_cert_path,
                            "tls_client_key_path": broker.tls_client_key_path,
                            # tls_insecure_skip_verify is not applicable - AIToolStack as client should always verify
                            # topic_pattern is None - will use default from settings in MQTT service
                            "topic_pattern": None,
                        }
                    )
            except Exception as e:
                logger.warning(f"Failed to load external brokers from database: {e}")
                # Fallback to legacy single external broker config
                if cfg.external_enabled and (cfg.external_host or cfg.external_port):
                    endpoints.append(
                        {
                            "type": "external",
                            "host": cfg.external_host or settings.MQTT_BROKER,
                            "port": cfg.external_port or settings.MQTT_PORT,
                            "protocol": cfg.external_protocol,
                            "connected": False,
                            "broker_id": None,
                            "broker_name": "Legacy External Broker",
                            "username": cfg.external_username,
                            "password": cfg.external_password,
                            "qos": cfg.external_qos,
                            "keepalive": cfg.external_keepalive,
                            "tls_enabled": cfg.external_tls_enabled,
                            "tls_ca_cert_path": cfg.external_tls_ca_cert_path,
                            "tls_client_cert_path": cfg.external_tls_client_cert_path,
                            "tls_client_key_path": cfg.external_tls_client_key_path,
                            # tls_insecure_skip_verify is not applicable - AIToolStack as client should always verify
                            # topic_pattern is None - will use default from settings in MQTT service
                            "topic_pattern": None,
                        }
                    )

            if not endpoints:
                logger.warning("MQTT config resulted in no endpoints to connect to")
                return

            # Initialize endpoint tracking
            self._endpoints = endpoints.copy()

            # Keep the first endpoint as primary for backward compatible fields
            primary = endpoints[0]
            self.broker_host = primary["host"]
            self.broker_port = primary["port"]

            # Create and connect a client for each endpoint
            import ssl

            for idx, ep in enumerate(endpoints):
                print(f"[MQTT Service] Creating client for endpoint {idx}: type={ep['type']}, host={ep['host']}, port={ep['port']}, protocol={ep.get('protocol', 'N/A')}")
                logger.info(f"[MQTT Service] Creating client for endpoint {idx}: type={ep['type']}, host={ep['host']}, port={ep['port']}, protocol={ep.get('protocol', 'N/A')}")
                client = mqtt.Client(
                    client_id=f"annotator_server_{ep['type']}_{uuid.uuid4().hex[:8]}",
                    protocol=mqtt.MQTTv311,
                    clean_session=True,
                )

                # Attach endpoint info for logging
                setattr(client, "_camthink_broker_host", ep["host"])
                setattr(client, "_camthink_broker_port", ep["port"])
                setattr(client, "_camthink_broker_type", ep["type"])
                setattr(client, "_camthink_broker_index", idx)
                # Store broker_id for external brokers
                if ep.get("broker_id") is not None:
                    setattr(client, "_camthink_broker_id", ep["broker_id"])
                # Store broker-specific QoS
                if ep["type"] == "builtin":
                    setattr(client, "_camthink_broker_qos", cfg.builtin_qos)
                else:
                    setattr(client, "_camthink_broker_qos", ep.get("qos", cfg.external_qos))

                # Set connection timeout and retry parameters
                client.reconnect_delay_set(min_delay=1, max_delay=120)

                # Set callbacks
                client.on_connect = self.on_connect
                client.on_disconnect = self.on_disconnect
                client.on_message = self.on_message
            
                # Configure broker-specific settings
                if ep["type"] == "builtin":
                    # Built-in broker configuration
                    # 当 MQTT_USE_BUILTIN_BROKER=true 时，通常是 Python 内置 aMQTT；
                    # 当 MQTT_USE_BUILTIN_BROKER=false 时，“builtin” 端点会被指向外部 Mosquitto，
                    # 这里统一负责设置认证信息。
                    protocol = ep.get("protocol", cfg.builtin_protocol)  # Use protocol from endpoint (already inferred from broker config)
                    keepalive = cfg.builtin_keepalive or 120  # Client-side keepalive (independent of broker)
                    topic_pattern = settings.MQTT_UPLOAD_TOPIC
                    print(f"[Client Connection] Connecting to built-in broker: protocol={protocol}, port={ep['port']}, host={ep['host']}")
                    logger.info(f"[Client Connection] Connecting to built-in broker: protocol={protocol}, port={ep['port']}, host={ep['host']}")

                    # Authentication: AIToolStack connects to built-in broker
                    # IMPORTANT: builtin_username and builtin_password are broker settings for EXTERNAL devices,
                    # NOT for AIToolStack itself. AIToolStack should use system credentials or connect anonymously.
                    # - If broker allows anonymous: AIToolStack connects anonymously
                    # - If broker requires auth: AIToolStack uses system credentials (MQTT_USERNAME/MQTT_PASSWORD from env/config)
                    #   Note: builtin_username/builtin_password may match system credentials, but they serve different purposes
                    print(f"[Client Connection] Broker allow_anonymous={cfg.builtin_allow_anonymous}")
                    logger.info(f"[Client Connection] Broker allow_anonymous={cfg.builtin_allow_anonymous}")
                    if not cfg.builtin_allow_anonymous:
                        # Broker requires authentication - AIToolStack uses system credentials
                        # These are separate from builtin_username/builtin_password (which are for external devices)
                        from backend.config import settings as app_settings
                        # Use system credentials (from environment/config), not broker's builtin_username/builtin_password
                        # builtin_username/builtin_password are for external devices connecting to the broker
                        system_username = getattr(app_settings, "MQTT_USERNAME", None)
                        system_password = getattr(app_settings, "MQTT_PASSWORD", None)
                        print(f"[Client Connection] Checking system credentials: username={system_username is not None}, password={system_password is not None}")
                        logger.info(f"[Client Connection] Checking system credentials: username={'***' if system_username else None}, password={'***' if system_password else None}")
                        if system_username and system_password:
                            client.username_pw_set(system_username, system_password)
                            print(f"[Client Connection] Using system authentication: username={system_username}")
                            logger.info(f"[Client Connection] Using system authentication: username={system_username}")
                        else:
                            # Fallback: if system credentials not set, try using builtin_username/builtin_password
                            # This is a compatibility fallback, but ideally system credentials should be configured
                            fallback_username = cfg.builtin_username
                            fallback_password = cfg.builtin_password
                            if fallback_username and fallback_password:
                                logger.warning(
                                    "[Client Connection] System credentials (MQTT_USERNAME/MQTT_PASSWORD) not set. "
                                    "Using builtin_username/builtin_password as fallback. "
                                    "Note: builtin_username/builtin_password are for external devices, "
                                    "not for AIToolStack. Please configure MQTT_USERNAME/MQTT_PASSWORD in environment/config."
                                )
                                client.username_pw_set(fallback_username, fallback_password)
                                print(f"[Client Connection] Using fallback authentication: username={fallback_username}")
                                logger.info(f"[Client Connection] Using fallback authentication: username={fallback_username}")
                            else:
                                error_msg = (
                                    "Broker requires authentication but no credentials configured. "
                                    "Please set MQTT_USERNAME and MQTT_PASSWORD in environment/config for AIToolStack, "
                                    "or set builtin_username and builtin_password in MQTT configuration for external devices, "
                                    "or enable allow_anonymous."
                                )
                                print(f"[Client Connection] ERROR: {error_msg}")
                                logger.error(f"[Client Connection] {error_msg}")
                                # Don't attempt connection without credentials when auth is required
                                # This will cause connection failure, but at least we log the issue clearly
                    else:
                        print(f"[Client Connection] Broker allows anonymous, connecting without credentials")
                        logger.info("[Client Connection] Broker allows anonymous, connecting without credentials")

                    # TLS configuration: Client automatically uses broker's TLS configuration
                    # - If broker_protocol is "mqtts", client connects via TLS
                    # - Client uses broker's CA certificate to verify server certificate
                    # - Client uses broker's client certificate/key if configured (for mTLS)
                    if protocol == "mqtts":
                        print(f"[Client TLS] ===== Configuring TLS for client connection (broker protocol: {protocol}, port: {ep['port']}) =====")
                        logger.info(f"[Client TLS] Configuring TLS for client connection (broker protocol: {protocol}, port: {ep['port']})")
                        # Client uses broker's CA certificate to verify server certificate
                        # Since AIToolStack and broker are deployed together, we can access the CA file directly
                        tls_kwargs = {
                            "tls_version": ssl.PROTOCOL_TLSv1_2
                        }
                        
                        # Use CA certificate for certificate verification (required for security)
                        if cfg.builtin_tls_ca_cert_path:
                            # Check if CA certificate file exists
                            import os
                            ca_path = cfg.builtin_tls_ca_cert_path
                            print(f"[TLS Config] CA certificate path from config: {ca_path}")
                            if os.path.exists(ca_path):
                                tls_kwargs["ca_certs"] = ca_path
                                tls_kwargs["cert_reqs"] = ssl.CERT_REQUIRED  # Require certificate verification
                                print(f"[TLS Config] ✓ CA certificate found, setting cert_reqs=CERT_REQUIRED")
                                print(f"[TLS Config] ✓ ca_certs={ca_path}")
                                logger.info(f"[TLS Config] Using CA certificate for verification: {ca_path}")
                            else:
                                error_msg = f"CA certificate file not found: {ca_path}"
                                print(f"[TLS Config] ✗ ERROR: {error_msg}")
                                logger.error(f"[TLS Config] {error_msg}")
                                raise FileNotFoundError(error_msg)
                        else:
                            error_msg = "No CA certificate path configured, cannot verify server certificate"
                            print(f"[TLS Config] ✗ ERROR: {error_msg}")
                            logger.error(f"[TLS Config] {error_msg}")
                            raise ValueError("CA certificate path is required for MQTTS connection")
                        
                        # Use default client certificates if available (AIToolStack always uses default client certs)
                        # CRITICAL: Only use client certificates if mTLS is enabled (require_certificate=true)
                        # If mTLS is disabled, do NOT send client certificates to avoid connection issues
                        default_client_cert = Path("/mosquitto/config/certs/client.crt")
                        default_client_key = Path("/mosquitto/config/certs/client.key")
                        # Check if mTLS is enabled
                        mtls_enabled = getattr(cfg, 'builtin_tls_require_client_cert', False)
                        
                        # #region agent log
                        try:
                            with open('/Users/shenmingming/Desktop/AIToolStack/.cursor/debug.log', 'a') as f:
                                import json
                                f.write(json.dumps({
                                    "sessionId": "debug-session",
                                    "runId": "run1",
                                    "hypothesisId": "B",
                                    "location": "mqtt_service.py:1556",
                                    "message": "Checking mTLS status and client cert availability",
                                    "data": {
                                        "mtls_enabled": mtls_enabled,
                                        "client_cert_exists": default_client_cert.exists(),
                                        "client_key_exists": default_client_key.exists(),
                                        "require_certificate": mtls_enabled
                                    },
                                    "timestamp": int(time.time() * 1000)
                                }) + "\n")
                        except: pass
                        # #endregion
                        
                        if mtls_enabled and default_client_cert.exists() and default_client_key.exists():
                            tls_kwargs["certfile"] = str(default_client_cert)
                            tls_kwargs["keyfile"] = str(default_client_key)
                            print(f"[TLS Config] Using default client certificates: {default_client_cert} (mTLS enabled)")
                            logger.info(f"[TLS Config] Using default client certificates: {default_client_cert} (mTLS enabled)")
                            
                            # #region agent log
                            try:
                                with open('/Users/shenmingming/Desktop/AIToolStack/.cursor/debug.log', 'a') as f:
                                    import json
                                    f.write(json.dumps({
                                        "sessionId": "debug-session",
                                        "runId": "run1",
                                        "hypothesisId": "B",
                                        "location": "mqtt_service.py:1570",
                                        "message": "Client certificates added to TLS config",
                                        "data": {
                                            "certfile": str(default_client_cert),
                                            "keyfile": str(default_client_key)
                                        },
                                        "timestamp": int(time.time() * 1000)
                                    }) + "\n")
                            except: pass
                            # #endregion
                        else:
                            if not mtls_enabled:
                                print(f"[TLS Config] mTLS disabled, NOT using client certificates (one-way TLS)")
                                logger.info("[TLS Config] mTLS disabled, NOT using client certificates (one-way TLS)")
                            else:
                                print(f"[TLS Config] No client certificates found, using CA verification only (secure mode)")
                                logger.info("[TLS Config] Using CA certificate verification (secure mode)")
                            
                            # #region agent log
                            try:
                                with open('/Users/shenmingming/Desktop/AIToolStack/.cursor/debug.log', 'a') as f:
                                    import json
                                    f.write(json.dumps({
                                        "sessionId": "debug-session",
                                        "runId": "run1",
                                        "hypothesisId": "B",
                                        "location": "mqtt_service.py:1585",
                                        "message": "Client certificates NOT added (mTLS disabled or certs missing)",
                                        "data": {
                                            "mtls_enabled": mtls_enabled,
                                            "reason": "mTLS disabled" if not mtls_enabled else "certs missing"
                                        },
                                        "timestamp": int(time.time() * 1000)
                                    }) + "\n")
                            except: pass
                            # #endregion
                        
                        # AIToolStack as client should always verify server certificate
                        use_insecure = False
                        
                        # Configure TLS with CA certificate verification
                        # CRITICAL: For security, we MUST verify the server certificate using the CA
                        # Do NOT allow insecure mode to bypass certificate verification
                        print(f"[TLS Config] Calling tls_set with keys: {list(tls_kwargs.keys())}")
                        print(f"[TLS Config] cert_reqs={tls_kwargs.get('cert_reqs')}, ca_certs={tls_kwargs.get('ca_certs')}")
                        logger.info(f"[TLS Config] Calling tls_set with: {list(tls_kwargs.keys())}")
                        logger.info(f"[TLS Config] cert_reqs={tls_kwargs.get('cert_reqs')}, ca_certs={tls_kwargs.get('ca_certs')}")
                        # #region agent log
                        try:
                            with open('/Users/shenmingming/Desktop/AIToolStack/.cursor/debug.log', 'a') as f:
                                import json
                                f.write(json.dumps({
                                    "sessionId": "debug-session",
                                    "runId": "run1",
                                    "hypothesisId": "D",
                                    "location": "mqtt_service.py:1576",
                                    "message": "Before tls_set() call",
                                    "data": {
                                        "tls_kwargs_keys": list(tls_kwargs.keys()),
                                        "has_certfile": "certfile" in tls_kwargs,
                                        "has_keyfile": "keyfile" in tls_kwargs,
                                        "has_ca_certs": "ca_certs" in tls_kwargs,
                                        "cert_reqs": tls_kwargs.get("cert_reqs")
                                    },
                                    "timestamp": int(time.time() * 1000)
                                }) + "\n")
                        except: pass
                        # #endregion
                        
                        try:
                            client.tls_set(**tls_kwargs)
                            print(f"[TLS Config] ✓ tls_set() completed successfully")
                            logger.info(f"[TLS Config] tls_set() completed successfully with CA certificate verification")
                            
                            # #region agent log
                            try:
                                with open('/Users/shenmingming/Desktop/AIToolStack/.cursor/debug.log', 'a') as f:
                                    import json
                                    f.write(json.dumps({
                                        "sessionId": "debug-session",
                                        "runId": "run1",
                                        "hypothesisId": "D",
                                        "location": "mqtt_service.py:1585",
                                        "message": "tls_set() completed successfully",
                                        "data": {},
                                        "timestamp": int(time.time() * 1000)
                                    }) + "\n")
                            except: pass
                            # #endregion
                        except Exception as tls_err:
                            print(f"[TLS Config] ✗ tls_set() failed: {tls_err}")
                            logger.error(f"[TLS Config] tls_set() failed: {tls_err}", exc_info=True)
                            
                            # #region agent log
                            try:
                                with open('/Users/shenmingming/Desktop/AIToolStack/.cursor/debug.log', 'a') as f:
                                    import json
                                    f.write(json.dumps({
                                        "sessionId": "debug-session",
                                        "runId": "run1",
                                        "hypothesisId": "D",
                                        "location": "mqtt_service.py:1595",
                                        "message": "tls_set() failed",
                                        "data": {"error": str(tls_err)},
                                        "timestamp": int(time.time() * 1000)
                                    }) + "\n")
                            except: pass
                            # #endregion
                            raise
                        
                        # CRITICAL: Always disable insecure mode to enforce certificate verification
                        # Even if user sets insecure_skip_verify, we enforce verification for built-in broker
                        # This ensures only certificates signed by the correct CA can connect
                        use_insecure = False  # Force secure mode for built-in broker
                        print(f"[TLS Config] FORCING tls_insecure_set(False) to enable certificate verification")
                        logger.info(f"[TLS Config] Setting TLS insecure mode: {use_insecure} (FORCED to False for security)")
                        try:
                            client.tls_insecure_set(use_insecure)
                            print(f"[TLS Config] ✓ tls_insecure_set(False) completed successfully")
                            print(f"[TLS Config] ✓ Certificate verification is ENABLED - only certificates signed by CA will be accepted")
                            logger.info(f"[TLS Config] tls_insecure_set({use_insecure}) completed successfully")
                            logger.info(f"[TLS Config] Certificate verification is ENABLED - only certificates signed by CA will be accepted")
                        except Exception as tls_err:
                            print(f"[TLS Config] ✗ tls_insecure_set() failed: {tls_err}")
                            logger.error(f"[TLS Config] tls_insecure_set() failed: {tls_err}", exc_info=True)
                            raise
                        print(f"[TLS Config] ===== TLS configuration completed =====")
                        # Double-check: Verify TLS settings are correct before connecting
                        # Note: paho-mqtt doesn't provide getters, so we can't verify the actual state
                        # But we log what we expect to ensure the code path is correct
                        print(f"[TLS Config] FINAL STATE: cert_reqs=CERT_REQUIRED({ssl.CERT_REQUIRED}), ca_certs={tls_kwargs.get('ca_certs')}, insecure=False")
                    else:
                        logger.info(f"[TLS Config] Protocol is '{protocol}', skipping TLS configuration")
                else:
                    # External broker configuration (from database or legacy config)
                    protocol = ep.get("protocol", cfg.external_protocol)
                    keepalive = ep.get("keepalive", cfg.external_keepalive or 120)
                    # All external brokers use default topic pattern based on system business logic
                    topic_pattern = None  # Will use default from settings.MQTT_UPLOAD_TOPIC
                    # External broker authentication
                    username = ep.get("username") or cfg.external_username
                    password = ep.get("password") or cfg.external_password
                    if username and password:
                        client.username_pw_set(username, password)

                    # TLS configuration for external broker
                    tls_enabled = ep.get("tls_enabled", False) or (protocol == "mqtts" and cfg.external_tls_enabled)
                    if protocol == "mqtts" and tls_enabled:
                        tls_kwargs = {}
                        tls_ca_cert = ep.get("tls_ca_cert_path") or cfg.external_tls_ca_cert_path
                        tls_client_cert = ep.get("tls_client_cert_path") or cfg.external_tls_client_cert_path
                        tls_client_key = ep.get("tls_client_key_path") or cfg.external_tls_client_key_path
                        
                        if tls_ca_cert:
                            tls_kwargs["ca_certs"] = tls_ca_cert
                        if tls_client_cert and tls_client_key:
                            tls_kwargs["certfile"] = tls_client_cert
                            tls_kwargs["keyfile"] = tls_client_key

                        # Use TLS v1.2 as a safe default
                        tls_kwargs["tls_version"] = ssl.PROTOCOL_TLSv1_2

                        if tls_kwargs:
                            client.tls_set(**tls_kwargs)
                        # AIToolStack as client should always verify server certificate
                        # tls_insecure_skip_verify is not applicable for external brokers
                        client.tls_insecure_set(False)

                # Store topic pattern for this client (use default if None)
                # All brokers subscribe to the same default topic pattern based on system business logic
                setattr(client, "_camthink_topic_pattern", topic_pattern or settings.MQTT_UPLOAD_TOPIC)

                # For builtin broker with MQTTS, verify TLS settings one more time before connecting
                if ep["type"] == "builtin" and protocol == "mqtts":
                    print(f"[MQTT Service] Pre-connect verification: Protocol=mqtts, expecting certificate verification")
                    # Ensure insecure mode is still False (in case it was changed elsewhere)
                    client.tls_insecure_set(False)
                    print(f"[MQTT Service] Re-confirmed: tls_insecure_set(False) before connect")
                
                print(f"[MQTT Service] Connecting to {ep['type']} MQTT Broker (protocol: {protocol}) at {ep['host']}:{ep['port']}")
                logger.info(f"[MQTT Service] Connecting to {ep['type']} MQTT Broker (protocol: {protocol}) at {ep['host']}:{ep['port']}")
                try:
                    # For TLS connections, ensure keepalive is set appropriately
                    # TLS connections may need more frequent keepalive to maintain connection
                    if protocol == "mqtts":
                        # Use a slightly shorter keepalive for TLS to detect connection issues faster
                        # But not too short to avoid unnecessary reconnections
                        tls_keepalive = min(keepalive, 60)  # Cap at 60 seconds for TLS
                        logger.info(f"[MQTT Service] TLS connection: using keepalive={tls_keepalive} seconds")
                        
                        # #region agent log
                        try:
                            with open('/Users/shenmingming/Desktop/AIToolStack/.cursor/debug.log', 'a') as f:
                                import json
                                f.write(json.dumps({
                                    "sessionId": "debug-session",
                                    "runId": "run1",
                                    "hypothesisId": "C",
                                    "location": "mqtt_service.py:1663",
                                    "message": "Before TLS connect() call",
                                    "data": {
                                        "host": ep["host"],
                                        "port": ep["port"],
                                        "keepalive": tls_keepalive,
                                        "original_keepalive": keepalive,
                                        "protocol": protocol
                                    },
                                    "timestamp": int(time.time() * 1000)
                                }) + "\n")
                        except: pass
                        # #endregion
                        
                        client.connect(ep["host"], ep["port"], keepalive=tls_keepalive)
                        
                        # #region agent log
                        try:
                            with open('/Users/shenmingming/Desktop/AIToolStack/.cursor/debug.log', 'a') as f:
                                import json
                                f.write(json.dumps({
                                    "sessionId": "debug-session",
                                    "runId": "run1",
                                    "hypothesisId": "A",
                                    "location": "mqtt_service.py:1675",
                                    "message": "After TLS connect() call",
                                    "data": {
                                        "host": ep["host"],
                                        "port": ep["port"],
                                        "keepalive": tls_keepalive
                                    },
                                    "timestamp": int(time.time() * 1000)
                                }) + "\n")
                        except: pass
                        # #endregion
                    else:
                        client.connect(ep["host"], ep["port"], keepalive=keepalive)
                    print(f"[MQTT Service] ✓ connect() call completed for {ep['type']} broker")
                    logger.info(f"[MQTT Service] connect() call completed, starting loop for {ep['type']} broker")
                    client.loop_start()
                    print(f"[MQTT Service] ✓ loop_start() completed for {ep['type']} broker")
                    logger.info(f"[MQTT Service] loop_start() completed for {ep['type']} broker")
                except Exception as conn_err:
                    print(f"[MQTT Service] ✗ Error during connect/loop_start for {ep['type']} broker: {conn_err}")
                    logger.error(f"[MQTT Service] Error during connect/loop_start for {ep['type']} broker: {conn_err}", exc_info=True)
                    raise

                # Save clients for later stop / status
                if self.client is None:
                    self.client = client
                self.clients.append(client)

            logger.info("MQTT client(s) loop started")
            
            # Start periodic device status check
            self._schedule_status_check()
            logger.info(f"Device status check scheduled (interval: {self._status_check_interval}s, timeout: {self._device_timeout_seconds}s)")
        except ConnectionRefusedError:
            error_msg = "Connection refused"
            if self._config and self._config.mode == "builtin":
                error_msg += ". Built-in broker may not be running."
            else:
                error_msg += f". Please check if MQTT broker is running at {self.broker_host}:{self.broker_port}"
            logger.error(error_msg)
            self.is_connected = False
            self.recent_errors.append(
                {
                    "time": time.time(),
                    "type": "connection_refused",
                    "code": None,
                    "message": error_msg,
                }
            )
        except Exception as e:
            logger.error(f"Failed to connect: {e}", exc_info=True)
            self.is_connected = False
            self.recent_errors.append(
                {
                    "time": time.time(),
                    "type": "connection_error",
                    "code": None,
                    "message": str(e),
                }
            )
    
    def stop(self):
        """Stop MQTT client"""
        logger.info("Stopping MQTT client(s)...")
        try:
            # Stop periodic status check timer
            if self._status_check_timer:
                self._status_check_timer.cancel()
                self._status_check_timer = None
            
            # Stop all managed clients
            for client in self.clients or ([] if self.client is None else [self.client]):
                try:
                    client.loop_stop()
                    client.disconnect()
                except Exception as e:
                    logger.error(f"Error stopping MQTT client: {e}")
            self.clients = []
            self.client = None
            self.is_connected = False
            logger.info("MQTT client(s) stopped")
        except Exception as e:
            logger.error(f"Error stopping MQTT clients: {e}")

    def reload_and_reconnect(self):
        """Reload configuration from DB and reconnect to broker."""
        self.stop()
        self.start()


# Global MQTT service instance
mqtt_service = MQTTService()

