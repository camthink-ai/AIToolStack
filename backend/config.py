"""配置文件"""
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).parent.parent
DATASETS_DIR = BASE_DIR / "datasets"


class Settings(BaseSettings):
    """应用配置"""
    
    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    
    # MQTT 配置
    MQTT_BROKER: str = "localhost"
    MQTT_PORT: int = 1883
    MQTT_USERNAME: str = ""
    MQTT_PASSWORD: str = ""
    MQTT_UPLOAD_TOPIC: str = "annotator/upload/+"
    MQTT_RESPONSE_TOPIC_PREFIX: str = "annotator/response"
    MQTT_QOS: int = 1
    
    # 数据库配置
    DATABASE_URL: str = f"sqlite:///{BASE_DIR}/data/annotator.db"
    
    # 文件存储配置
    DATASETS_ROOT: Path = DATASETS_DIR
    MAX_IMAGE_SIZE_MB: int = 10
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

# 确保必要目录存在
settings.DATASETS_ROOT.mkdir(parents=True, exist_ok=True)
(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)

