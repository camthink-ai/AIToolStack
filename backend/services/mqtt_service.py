"""MQTT 服务：订阅设备上传的图像"""
import json
import base64
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional
import paho.mqtt.client as mqtt
from PIL import Image
import io

from backend.config import settings
from backend.models.database import SessionLocal, Image, Project
from backend.services.websocket_manager import websocket_manager


class MQTTService:
    """MQTT 订阅服务"""
    
    def __init__(self):
        self.client: Optional[mqtt.Client] = None
        self.is_connected = False
    
    def on_connect(self, client, userdata, flags, rc):
        """连接回调"""
        if rc == 0:
            self.is_connected = True
            print(f"[MQTT] Connected to broker at {settings.MQTT_BROKER}:{settings.MQTT_PORT}")
            # 订阅上传主题
            client.subscribe(settings.MQTT_UPLOAD_TOPIC, qos=settings.MQTT_QOS)
            print(f"[MQTT] Subscribed to topic: {settings.MQTT_UPLOAD_TOPIC}")
        else:
            print(f"[MQTT] Connection failed with code {rc}")
    
    def on_disconnect(self, client, userdata, rc):
        """断开连接回调"""
        self.is_connected = False
        print(f"[MQTT] Disconnected from broker")
    
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
        req_id = data.get('req_id', str(uuid.uuid4()))
        device_id = data.get('device_id', 'unknown')
        timestamp = data.get('timestamp', int(datetime.utcnow().timestamp()))
        image_data = data.get('image', {})
        metadata = data.get('metadata', {})
        
        # 校验项目是否存在
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                error_msg = f"Project {project_id} not found"
                print(f"[MQTT] {error_msg}")
                self._send_error_response(req_id, device_id, error_msg)
                return
            
            # 解析图像数据
            filename = image_data.get('filename', f'img_{timestamp}.jpg')
            image_format = image_data.get('format', 'jpg').lower()
            encoding = image_data.get('encoding', 'base64')
            base64_data = image_data.get('data', '')
            
            # 移除可能的 data:image/jpg;base64, 前缀
            if ',' in base64_data:
                base64_data = base64_data.split(',')[-1]
            
            # Base64 解码
            if encoding != 'base64':
                raise ValueError(f"Unsupported encoding: {encoding}")
            
            image_bytes = base64.b64decode(base64_data)
            
            # 校验图像大小
            size_mb = len(image_bytes) / (1024 * 1024)
            if size_mb > settings.MAX_IMAGE_SIZE_MB:
                raise ValueError(f"Image too large: {size_mb:.2f}MB (max: {settings.MAX_IMAGE_SIZE_MB}MB)")
            
            # 打开图像获取尺寸
            img = Image.open(io.BytesIO(image_bytes))
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
        self.client = mqtt.Client(client_id=f"annotator_server_{uuid.uuid4().hex[:8]}")
        
        # 设置回调
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        
        # 设置认证（如果需要）
        if settings.MQTT_USERNAME and settings.MQTT_PASSWORD:
            self.client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
        
        # 连接到 Broker
        try:
            self.client.connect(settings.MQTT_BROKER, settings.MQTT_PORT, keepalive=60)
            self.client.loop_start()
        except Exception as e:
            print(f"[MQTT] Failed to connect: {e}")
    
    def stop(self):
        """停止 MQTT 客户端"""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.is_connected = False


# 全局 MQTT 服务实例
mqtt_service = MQTTService()

