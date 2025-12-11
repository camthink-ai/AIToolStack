"""配置文件"""
import socket
import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).parent.parent
DATASETS_DIR = BASE_DIR / "datasets"


def get_local_ip() -> str:
    """获取本机 IP 地址"""
    try:
        # 连接到一个远程地址（不需要实际连接）
        # 这样可以获取本机用于连接外网的 IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        try:
            # 不需要真正连接，只是获取本机 IP
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        except Exception:
            # 如果失败，尝试获取 localhost
            ip = socket.gethostbyname(socket.gethostname())
        finally:
            s.close()
        return ip
    except Exception:
        # 如果都失败，返回 localhost
        return "127.0.0.1"


def get_mqtt_broker_host(request=None) -> str:
    """
    获取 MQTT Broker 对外显示的地址
    优先使用配置的 MQTT_BROKER_HOST，如果没有则尝试从请求头获取客户端访问的主机
    """
    # 如果配置了 MQTT_BROKER_HOST，直接使用
    if settings.MQTT_BROKER_HOST:
        return settings.MQTT_BROKER_HOST
    
    # 如果有请求对象，尝试从请求头获取主机信息
    if request:
        # 尝试从 Host 头获取
        host = request.headers.get("Host", "")
        if host:
            # 移除端口号（如果有）
            host_without_port = host.split(":")[0]
            # 排除 localhost 和 127.0.0.1，但保留其他地址
            if host_without_port not in ["localhost", "127.0.0.1", "0.0.0.0"]:
                # 验证是否是 IP 地址格式，如果是域名也可以返回
                return host_without_port
        
        # 尝试从 X-Forwarded-Host 获取（反向代理场景）
        forwarded_host = request.headers.get("X-Forwarded-Host", "")
        if forwarded_host:
            host_without_port = forwarded_host.split(":")[0]
            if host_without_port not in ["localhost", "127.0.0.1", "0.0.0.0"]:
                return host_without_port
        
        # 尝试从 X-Real-IP 或 X-Forwarded-For 获取（虽然这些是客户端 IP，但在某些场景下可能有用）
        # 实际上我们需要的是服务器的主机 IP，不是客户端 IP
        # 但从客户端 IP 可以推断出服务器在同一网络
    
    # 尝试获取本机 IP（在 Docker 中可能获取到容器 IP）
    container_ip = get_local_ip()
    
    # 如果是 Docker 容器内部 IP（通常以 172.17, 172.18 等开头），尝试获取主机 IP
    if container_ip.startswith("172.17.") or container_ip.startswith("172.18.") or container_ip.startswith("172.19."):
        # 方法1: 在 Docker 容器中，尝试获取默认网关（通常是 Docker 主机）
        try:
            import subprocess
            # 尝试使用 ip 命令获取默认网关
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                # 解析网关 IP
                for line in result.stdout.split('\n'):
                    if 'default via' in line:
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part == 'via' and i + 1 < len(parts):
                                gateway_ip = parts[i + 1]
                                # 验证是有效的 IP 地址
                                try:
                                    socket.inet_aton(gateway_ip)
                                    # 网关 IP 通常是主机 IP，但我们需要的是对外的 IP
                                    # 先保存网关 IP 作为备选
                                except:
                                    pass
        except Exception:
            pass
        
        # 方法2: 尝试从 /proc/net/route 获取网关
        try:
            with open("/proc/net/route", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == "00000000":  # default route
                        if len(parts) >= 3:
                            gateway_hex = parts[2]
                            # 转换为 IP 地址
                            gateway_ip = ".".join([
                                str(int(gateway_hex[i:i+2], 16)) 
                                for i in range(6, -1, -2)
                            ])
                            try:
                                socket.inet_aton(gateway_ip)
                                if not gateway_ip.startswith("172.17."):
                                    return gateway_ip
                            except:
                                pass
        except Exception:
            pass
        
        # 方法3: 尝试从环境变量获取（Docker Compose 可能会设置）
        host_ip = os.environ.get("HOST_IP") or os.environ.get("HOSTIP") or os.environ.get("MQTT_BROKER_HOST")
        if host_ip:
            try:
                socket.inet_aton(host_ip)
                return host_ip
            except:
                pass
        
        # 方法4: 尝试通过连接外部地址获取本机用于外出的 IP
        # 但这在容器内可能还是获取到容器 IP
        try:
            # 获取用于连接到外部网络的本机 IP
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_socket.settimeout(1)
            try:
                # 尝试连接到一个外部地址来获取本机 IP
                test_socket.connect(("8.8.8.8", 80))
                test_ip = test_socket.getsockname()[0]
                test_socket.close()
                # 如果获取到的不是容器内部 IP，可以使用
                if not (test_ip.startswith("172.17.") or test_ip.startswith("172.18.") or test_ip.startswith("172.19.")):
                    return test_ip
            except:
                test_socket.close()
        except:
            pass
    
    # 如果都没有获取到，返回检测到的 IP（即使是容器 IP）
    return container_ip


class Settings(BaseSettings):
    """应用配置"""
    
    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    
    # MQTT 配置
    MQTT_ENABLED: bool = True  # 是否启用 MQTT 服务
    MQTT_USE_BUILTIN_BROKER: bool = True  # 是否使用内置 Broker（默认使用内置）
    MQTT_BROKER: str = ""  # 外部 Broker 地址（当 MQTT_USE_BUILTIN_BROKER=False 时使用，为空则自动使用本机 IP）
    MQTT_BROKER_HOST: str = ""  # MQTT Broker 对外显示的地址（用于 Docker 环境，为空则自动检测）
    MQTT_PORT: int = 1883  # MQTT 端口
    MQTT_BUILTIN_PORT: int = 1883  # 内置 Broker 端口
    MQTT_USERNAME: str = ""  # 外部 Broker 认证（内置 Broker 暂不支持认证）
    MQTT_PASSWORD: str = ""
    MQTT_UPLOAD_TOPIC: str = "annotator/upload/+"
    MQTT_RESPONSE_TOPIC_PREFIX: str = "annotator/response"
    MQTT_QOS: int = 1
    
    # 数据库配置
    DATABASE_URL: str = f"sqlite:///{BASE_DIR}/data/annotator.db"
    
    # 文件存储配置
    DATASETS_ROOT: Path = DATASETS_DIR
    MAX_IMAGE_SIZE_MB: int = 10
    
    # NE301 模型编译配置
    NE301_PROJECT_PATH: str = ""  # NE301 项目路径（为空则使用默认路径）
    NE301_USE_DOCKER: bool = True  # 是否使用 Docker 编译（默认 True）
    NE301_DOCKER_IMAGE: str = "camthink/ne301-dev:latest"  # Docker 镜像名称
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

# 如果 MQTT_BROKER 为空，使用本机 IP
if not settings.MQTT_BROKER:
    settings.MQTT_BROKER = get_local_ip()

# 确保必要目录存在
settings.DATASETS_ROOT.mkdir(parents=True, exist_ok=True)
(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)

