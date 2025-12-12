"""配置文件"""
import socket
import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).parent.parent
DATASETS_DIR = BASE_DIR / "datasets"


def get_local_ip() -> str:
    """获取本机 IP 地址（优先返回非容器内部IP）"""
    
    # 回退方法1: 通过连接外部地址获取本机IP（标准方法）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            # 如果获取到的不是容器内部IP或localhost，可以使用
            container_ranges = ['172.17.', '172.18.', '172.19.', '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.']
            if ip != '127.0.0.1' and not any(ip.startswith(prefix) for prefix in container_ranges):
                return ip
        except Exception:
            pass
        finally:
            try:
                s.close()
            except:
                pass
    except Exception:
        pass
    
    # 回退方法2: 使用hostname
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip != '127.0.0.1':
            container_ranges = ['172.17.', '172.18.', '172.19.']
            if not any(ip.startswith(prefix) for prefix in container_ranges):
                return ip
    except Exception:
        pass
    
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
    
    # 如果有请求对象，优先从请求头获取（这反映了客户端实际访问的IP）
    if request:
        # 优先级1: 尝试从 X-Forwarded-Host 获取（反向代理场景，最可靠）
        forwarded_host = request.headers.get("X-Forwarded-Host", "")
        if forwarded_host:
            host_without_port = forwarded_host.split(":")[0]
            if host_without_port not in ["localhost", "127.0.0.1", "0.0.0.0"]:
                return host_without_port
        
        # 优先级2: 尝试从 Host 头获取
        host = request.headers.get("Host", "")
        if host:
            # 移除端口号（如果有）
            host_without_port = host.split(":")[0]
            # 排除 localhost 和 127.0.0.1，但保留其他地址
            if host_without_port not in ["localhost", "127.0.0.1", "0.0.0.0"]:
                return host_without_port
        
        # 优先级3: 尝试从 X-Real-IP 获取（某些反向代理设置）
        real_ip = request.headers.get("X-Real-IP", "")
        if real_ip:
            if real_ip not in ["localhost", "127.0.0.1", "0.0.0.0"]:
                return real_ip
    
    # 方法1: 尝试从环境变量获取（Docker Compose 可能会设置，或手动配置）
    host_ip = os.environ.get("HOST_IP") or os.environ.get("HOSTIP") or os.environ.get("MQTT_BROKER_HOST") or os.environ.get("SERVER_IP")
    if host_ip:
        try:
            socket.inet_aton(host_ip)
            return host_ip
        except:
            pass
    
    # 方法2: 获取本机 IP（已改进，优先返回非容器IP）
    local_ip = get_local_ip()
    
    # 如果是容器内部 IP，尝试获取主机 IP
    container_ip_ranges = ["172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31."]
    is_container_ip = any(local_ip.startswith(prefix) for prefix in container_ip_ranges)
    
    if is_container_ip:
        # 方法2.1: 在 Docker 容器中，尝试获取默认网关（通常是 Docker 主机）
        try:
            import subprocess
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'default via' in line:
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part == 'via' and i + 1 < len(parts):
                                gateway_ip = parts[i + 1]
                                try:
                                    socket.inet_aton(gateway_ip)
                                    # 网关IP通常是Docker主机IP，但我们需要的是服务器对外IP
                                    # 只有在没有其他选择时才使用网关IP
                                except:
                                    pass
        except Exception:
            pass
        
        # 方法2.2: 尝试从 /proc/net/route 获取网关
        try:
            with open("/proc/net/route", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == "00000000":  # default route
                        if len(parts) >= 3:
                            gateway_hex = parts[2]
                            gateway_ip = ".".join([
                                str(int(gateway_hex[i:i+2], 16)) 
                                for i in range(6, -1, -2)
                            ])
                            try:
                                socket.inet_aton(gateway_ip)
                                # 网关IP可能不是对外IP，但至少不是容器IP
                                if not any(gateway_ip.startswith(prefix) for prefix in container_ip_ranges):
                                    # 如果网关IP不是容器IP范围，可以作为备选
                                    # 但这里我们仍然优先使用改进后的get_local_ip返回的IP
                                    pass
                            except:
                                pass
        except Exception:
            pass
    
    # 返回检测到的IP（get_local_ip已经优先返回非容器IP）
    return local_ip


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

