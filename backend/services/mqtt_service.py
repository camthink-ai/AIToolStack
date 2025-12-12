"""MQTT 服务：订阅设备上传的图像"""
import json
import base64
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional
import paho.mqtt.client as mqtt
from PIL import Image as PILImage
import io

from backend.config import settings
from backend.models.database import SessionLocal, Image, Project
from backend.services.websocket_manager import websocket_manager
from backend.services.mqtt_broker import builtin_mqtt_broker


class MQTTService:
    """MQTT 订阅服务"""
    
    def __init__(self):
        self.client: Optional[mqtt.Client] = None
        self.is_connected = False
        self.broker_host = ""  # 保存当前连接的broker地址
        self.broker_port = 0
    
    def on_connect(self, client, userdata, flags, rc):
        """连接回调"""
        if rc == 0:
            self.is_connected = True
            print(f"[MQTT] Connected to broker at {self.broker_host}:{self.broker_port}")
            # 订阅上传主题
            client.subscribe(settings.MQTT_UPLOAD_TOPIC, qos=settings.MQTT_QOS)
            print(f"[MQTT] Subscribed to topic: {settings.MQTT_UPLOAD_TOPIC}")
        else:
            print(f"[MQTT] Connection failed with code {rc}")
    
    def on_disconnect(self, client, userdata, rc):
        """断开连接回调"""
        self.is_connected = False
        if rc != 0:
            print(f"[MQTT] Disconnected from broker unexpectedly (rc={rc})")
        else:
            print(f"[MQTT] Disconnected from broker")
        
        # 如果非正常断开（rc != 0），paho-mqtt会自动尝试重连
        # 我们不需要手动处理重连逻辑
    
    def on_message(self, client, userdata, msg):
        """消息接收回调"""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            # 解析 Topic 获取 project_id
            # topic 格式: annotator/upload/{project_id}
            parts = topic.split('/')
            if len(parts) < 3:
                print(f"[MQTT] Invalid topic format: {topic}")
                return
            
            project_id = parts[2]
            
            # 解析 JSON 载荷
            data = json.loads(payload)
            
            # 处理图像上传
            self._handle_image_upload(project_id, data, topic)
            
        except json.JSONDecodeError as e:
            print(f"[MQTT] JSON decode error: {e}")
            self._send_error_response(data.get('req_id', ''), data.get('device_id', ''), 
                                     "Invalid JSON format")
        except Exception as e:
            print(f"[MQTT] Error processing message: {e}")
            self._send_error_response(data.get('req_id', ''), data.get('device_id', ''), 
                                     str(e))
    
    def _handle_image_upload(self, project_id: str, data: dict, topic: str):
        """处理图像上传"""
        # 适配新的数据结构
        # 支持两种格式：
        # 1. 新格式: { "image_data": "...", "encoding": "...", "metadata": {...} }
        # 2. 旧格式: { "req_id": "...", "device_id": "...", "image": {...} }
        
        # 尝试新格式
        if 'image_data' in data:
            # 新格式
            req_id = data.get('req_id', str(uuid.uuid4()))
            device_id = data.get('device_id', topic.split('/')[-1] if '/' in topic else 'unknown')
            metadata = data.get('metadata', {})
            encoding = data.get('encoding', 'base64')
            base64_data = data.get('image_data', '')
            
            # 从 metadata 中提取信息
            image_id = metadata.get('image_id', f'img_{int(datetime.utcnow().timestamp())}')
            timestamp = metadata.get('timestamp', int(datetime.utcnow().timestamp()))
            image_format = metadata.get('format', 'jpeg').lower()
            # 如果 metadata 中有尺寸信息，优先使用
            metadata_width = metadata.get('width')
            metadata_height = metadata.get('height')
            
            # 生成文件名
            if image_format in ['jpeg', 'jpg']:
                filename = f'{image_id}.jpg'
            elif image_format == 'png':
                filename = f'{image_id}.png'
            else:
                filename = f'{image_id}.{image_format}'
        else:
            # 旧格式（向后兼容）
            req_id = data.get('req_id', str(uuid.uuid4()))
            device_id = data.get('device_id', 'unknown')
            timestamp = data.get('timestamp', int(datetime.utcnow().timestamp()))
            image_data = data.get('image', {})
            metadata = data.get('metadata', {})
            
            filename = image_data.get('filename', f'img_{timestamp}.jpg')
            image_format = image_data.get('format', 'jpg').lower()
            encoding = image_data.get('encoding', 'base64')
            base64_data = image_data.get('data', '')
            metadata_width = None
            metadata_height = None
        
        # 校验项目是否存在
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                error_msg = f"Project {project_id} not found"
                print(f"[MQTT] {error_msg}")
                self._send_error_response(req_id, device_id, error_msg)
                return
            
            # 处理 base64 数据，移除可能的 data URI 前缀
            # 支持格式：
            # 1. data:image/jpeg;base64,xxxxx
            # 2. data:image/png;base64,xxxxx
            # 3. data:image/jpg;base64,xxxxx
            # 4. 纯 base64 字符串
            if base64_data.startswith('data:'):
                # 包含 data URI 前缀，提取 base64 部分
                if ',' in base64_data:
                    base64_data = base64_data.split(',')[-1]
                else:
                    # 如果格式异常，尝试移除 data: 前缀
                    base64_data = base64_data.replace('data:', '').split(';')[-1]
            elif ',' in base64_data:
                # 可能包含其他分隔符
                base64_data = base64_data.split(',')[-1]
            
            # 清理可能的空白字符
            base64_data = base64_data.strip()
            
            # Base64 解码
            if encoding != 'base64':
                raise ValueError(f"Unsupported encoding: {encoding}")
            
            try:
                image_bytes = base64.b64decode(base64_data)
            except Exception as e:
                raise ValueError(f"Failed to decode base64 data: {str(e)}")
            
            # 校验图像大小
            size_mb = len(image_bytes) / (1024 * 1024)
            if size_mb > settings.MAX_IMAGE_SIZE_MB:
                raise ValueError(f"Image too large: {size_mb:.2f}MB (max: {settings.MAX_IMAGE_SIZE_MB}MB)")
            
            # 获取图像尺寸
            if metadata_width and metadata_height:
                # 使用 metadata 中的尺寸信息
                img_width = metadata_width
                img_height = metadata_height
            else:
                # 打开图像获取尺寸
                img = PILImage.open(io.BytesIO(image_bytes))
                img_width, img_height = img.size
            
            # 生成存储路径
            project_dir = settings.DATASETS_ROOT / project_id / "raw"
            project_dir.mkdir(parents=True, exist_ok=True)
            
            # 处理文件名冲突
            file_path = project_dir / filename
            if file_path.exists():
                stem = file_path.stem
                suffix = file_path.suffix
                timestamp_suffix = int(datetime.utcnow().timestamp())
                filename = f"{stem}_{timestamp_suffix}{suffix}"
                file_path = project_dir / filename
            
            # 保存图像
            file_path.write_bytes(image_bytes)
            
            # 生成相对路径（仅包含 raw/filename，不包含 project_id）
            relative_path = f"raw/{filename}"
            
            # 存入数据库
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
            db.commit()
            db.refresh(db_image)
            
            print(f"[MQTT] Image saved: {filename} ({img_width}x{img_height}) to project {project_id}")
            
            # 发送成功响应
            self._send_success_response(req_id, device_id, project_id)
            
            # 通过 WebSocket 通知前端
            websocket_manager.broadcast_project_update(project_id, {
                "type": "new_image",
                "image_id": db_image.id,
                "filename": filename,
                "path": relative_path,
                "width": img_width,
                "height": img_height
            })
            
        except Exception as e:
            db.rollback()
            error_msg = f"Failed to save image: {str(e)}"
            print(f"[MQTT] {error_msg}")
            self._send_error_response(req_id, device_id, error_msg)
        finally:
            db.close()
    
    def _send_success_response(self, req_id: str, device_id: str, project_id: str):
        """发送成功响应"""
        if not device_id or device_id == 'unknown':
            return
        
        response_topic = f"{settings.MQTT_RESPONSE_TOPIC_PREFIX}/{device_id}"
        response = {
            "req_id": req_id,
            "status": "success",
            "code": 200,
            "message": f"Image saved to project {project_id}",
            "server_time": int(datetime.utcnow().timestamp())
        }
        
        self.client.publish(response_topic, json.dumps(response), qos=settings.MQTT_QOS)
    
    def _send_error_response(self, req_id: str, device_id: str, error_message: str):
        """发送错误响应"""
        if not device_id or device_id == 'unknown':
            return
        
        response_topic = f"{settings.MQTT_RESPONSE_TOPIC_PREFIX}/{device_id}"
        response = {
            "req_id": req_id,
            "status": "error",
            "code": 400,
            "message": error_message,
            "server_time": int(datetime.utcnow().timestamp())
        }
        
        self.client.publish(response_topic, json.dumps(response), qos=settings.MQTT_QOS)
    
    def start(self):
        """启动 MQTT 客户端"""
        if not settings.MQTT_ENABLED:
            print("[MQTT] MQTT service is disabled in configuration")
            return
        
        try:
            # 明确指定使用 MQTT 3.1.1 协议（aMQTT broker 不支持 MQTT 5.0）
            # protocol=mqtt.MQTTv311 表示使用 MQTT 3.1.1
            # 设置clean_session=True确保连接是干净的
            self.client = mqtt.Client(
                client_id=f"annotator_server_{uuid.uuid4().hex[:8]}",
                protocol=mqtt.MQTTv311,
                clean_session=True
            )
            
            # 设置连接超时和重试参数
            self.client.reconnect_delay_set(min_delay=1, max_delay=120)
            
            # 设置回调
            self.client.on_connect = self.on_connect
            self.client.on_disconnect = self.on_disconnect
            self.client.on_message = self.on_message
            
            # 确定要连接的 Broker 地址
            if settings.MQTT_USE_BUILTIN_BROKER:
                # 内置 Broker 绑定在 0.0.0.0，客户端在同一容器内应该连接 localhost
                # 这样更可靠，避免容器内部IP导致连接问题
                self.broker_host = "127.0.0.1"  # 在容器内使用 localhost 连接
                self.broker_port = settings.MQTT_BUILTIN_PORT
                print(f"[MQTT] Using built-in MQTT Broker at {self.broker_host}:{self.broker_port}")
            else:
                self.broker_host = settings.MQTT_BROKER
                self.broker_port = settings.MQTT_PORT
                print(f"[MQTT] Connecting to external MQTT Broker at {self.broker_host}:{self.broker_port}")
                # 外部 Broker 才需要认证
                if settings.MQTT_USERNAME and settings.MQTT_PASSWORD:
                    self.client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
            
            # 连接到 Broker
            # 增加keepalive时间，减少超时断开的问题
            self.client.connect(self.broker_host, self.broker_port, keepalive=120)
            self.client.loop_start()
        except ConnectionRefusedError:
            if settings.MQTT_USE_BUILTIN_BROKER:
                print(f"[MQTT] Connection refused. Built-in broker may not be running.")
            else:
                print(f"[MQTT] Connection refused. Please check if MQTT broker is running at {settings.MQTT_BROKER}:{settings.MQTT_PORT}")
            self.is_connected = False
        except Exception as e:
            print(f"[MQTT] Failed to connect: {e}")
            self.is_connected = False
    
    def stop(self):
        """停止 MQTT 客户端"""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.is_connected = False


# 全局 MQTT 服务实例
mqtt_service = MQTTService()

