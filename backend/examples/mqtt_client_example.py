"""MQTT 客户端示例：模拟 IoT 设备上传图像"""
import paho.mqtt.client as mqtt
import base64
import json
import uuid
import time
from pathlib import Path
from PIL import Image as PILImage
import io


def image_to_base64(image_path: str) -> str:
    """将图像文件转换为 Base64 字符串"""
    with open(image_path, 'rb') as f:
        image_data = f.read()
        base64_str = base64.b64encode(image_data).decode('utf-8')
        return base64_str


def upload_image(mqtt_client: mqtt.Client, project_id: str, device_id: str, image_path: str):
    """上传图像到标注系统"""
    # 读取图像
    if not Path(image_path).exists():
        print(f"Error: Image file not found: {image_path}")
        return
    
    # 获取图像格式
    image_format = Path(image_path).suffix[1:].lower()  # 移除点号
    if image_format not in ['jpg', 'jpeg', 'png', 'bmp']:
        print(f"Error: Unsupported image format: {image_format}")
        return
    
    # 转换为 Base64
    base64_data = image_to_base64(image_path)
    
    # 构建消息载荷
    payload = {
        "req_id": str(uuid.uuid4()),
        "device_id": device_id,
        "timestamp": int(time.time()),
        "image": {
            "filename": Path(image_path).name,
            "format": image_format,
            "encoding": "base64",
            "data": base64_data
        },
        "metadata": {
            "trigger_source": "example_script",
            "location": "test_location"
        }
    }
    
    # 发布到 MQTT Topic
    topic = f"annotator/upload/{project_id}"
    message = json.dumps(payload)
    
    print(f"Uploading image to topic: {topic}")
    print(f"Image: {Path(image_path).name}")
    print(f"Size: {len(base64_data)} bytes (base64)")
    
    result = mqtt_client.publish(topic, message, qos=1)
    
    if result.rc == mqtt.MQTT_ERR_SUCCESS:
        print("✓ Image uploaded successfully")
    else:
        print(f"✗ Failed to upload image: {result.rc}")


def on_connect(client, userdata, flags, rc):
    """连接回调"""
    if rc == 0:
        print("✓ Connected to MQTT broker")
        
        # 订阅响应主题
        device_id = userdata.get('device_id', 'test_device')
        response_topic = f"annotator/response/{device_id}"
        client.subscribe(response_topic)
        print(f"✓ Subscribed to response topic: {response_topic}")
    else:
        print(f"✗ Connection failed with code {rc}")


def on_message(client, userdata, msg):
    """消息接收回调"""
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        print(f"\n[Response] {payload.get('status', 'unknown')}")
        print(f"  Message: {payload.get('message', '')}")
        if payload.get('status') == 'error':
            print(f"  Code: {payload.get('code', '')}")
    except Exception as e:
        print(f"Error parsing response: {e}")


def main():
    # 配置
    MQTT_BROKER = "localhost"
    MQTT_PORT = 1883
    PROJECT_ID = "your_project_id_here"  # 替换为实际的项目 ID
    DEVICE_ID = "camera_01_line_A"
    
    # 创建 MQTT 客户端
    client = mqtt.Client(client_id=f"test_client_{uuid.uuid4().hex[:8]}")
    client.user_data_set({'device_id': DEVICE_ID})
    
    # 设置回调
    client.on_connect = on_connect
    client.on_message = on_message
    
    # 连接到 Broker
    print(f"Connecting to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}...")
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_start()
        
        # 等待连接
        time.sleep(2)
        
        # 上传示例图像（替换为实际图像路径）
        image_path = "test_image.jpg"  # 替换为实际图像路径
        
        if Path(image_path).exists():
            upload_image(client, PROJECT_ID, DEVICE_ID, image_path)
            
            # 等待响应
            time.sleep(5)
        else:
            print(f"\nWarning: Test image not found at {image_path}")
            print("Please update the image_path variable with a valid image file.")
            print("\nExample usage:")
            print("  upload_image(client, PROJECT_ID, DEVICE_ID, '/path/to/image.jpg')")
        
        # 保持连接
        print("\nPress Ctrl+C to exit...")
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\nExiting...")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
