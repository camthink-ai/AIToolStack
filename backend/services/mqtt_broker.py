"""内置 MQTT Broker 服务"""
import asyncio
import logging
import threading
import socket
from typing import Optional
from backend.config import settings, get_local_ip

logger = logging.getLogger(__name__)


class BuiltinMQTTBroker:
    """内置 MQTT Broker（使用 aMQTT）"""
    
    def __init__(self):
        self.broker = None
        self.is_running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
    
    def _run_broker(self):
        """在独立线程中运行 Broker"""
        try:
            from amqtt.broker import Broker
            
            # 创建新的事件循环
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            
            async def start_broker():
                # 创建 Broker 配置（aMQTT 格式）
                config = {
                    'listeners': {
                        'default': {
                            'type': 'tcp',
                            'bind': '0.0.0.0',  # 绑定所有接口，允许容器内外连接
                            'port': settings.MQTT_BUILTIN_PORT,
                            'max-connections': 100,  # 增加最大连接数
                        },
                    },
                    'sys_interval': 10,
                    'auth': {
                        'allow-anonymous': True,  # 允许匿名连接
                    },
                    'keepalive-timeout': 300,  # 增加keepalive超时时间（秒）
                }
                
                # 创建并启动 Broker
                self.broker = Broker(config)
                await self.broker.start()
                self.is_running = True
                
                local_ip = get_local_ip()
                logger.info(f"[MQTT Broker] Built-in MQTT Broker started on port {settings.MQTT_BUILTIN_PORT}")
                print(f"[MQTT Broker] Built-in MQTT Broker is ready at {local_ip}:{settings.MQTT_BUILTIN_PORT}")
                
                # 保持运行
                try:
                    while self.is_running:
                        await asyncio.sleep(1)
                except asyncio.CancelledError:
                    pass
                finally:
                    if self.broker:
                        try:
                            await self.broker.shutdown()
                        except:
                            pass
            
            # 运行异步函数
            self._loop.run_until_complete(start_broker())
            
        except ImportError as e:
            logger.error(f"[MQTT Broker] aMQTT library not installed. Please install: pip install amqtt. Error: {e}")
            self.is_running = False
        except Exception as e:
            logger.error(f"[MQTT Broker] Failed to start built-in broker: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self.is_running = False
    
    def start(self):
        """启动内置 MQTT Broker（同步方法）"""
        if self.is_running:
            logger.warning("[MQTT Broker] Broker is already running")
            return
        
        try:
            # 在独立线程中启动 Broker
            self._thread = threading.Thread(target=self._run_broker, daemon=True)
            self._thread.start()
            
            # 等待一下确保 Broker 启动
            import time
            time.sleep(0.5)
            
            if not self.is_running:
                raise RuntimeError("Failed to start built-in MQTT Broker")
                
        except Exception as e:
            logger.error(f"[MQTT Broker] Error starting broker thread: {e}")
            self.is_running = False
            raise
    
    def stop(self):
        """停止内置 MQTT Broker"""
        if not self.is_running:
            return
        
        try:
            self.is_running = False
            
            if self._loop and self._loop.is_running():
                # 停止事件循环
                self._loop.call_soon_threadsafe(self._loop.stop)
            
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3)
            
            logger.info("[MQTT Broker] Built-in MQTT Broker stopped")
        except Exception as e:
            logger.error(f"[MQTT Broker] Error stopping broker: {e}")
    
    def get_broker_address(self) -> str:
        """获取 Broker 地址"""
        local_ip = get_local_ip()
        return f"{local_ip}:{settings.MQTT_BUILTIN_PORT}"


# 全局内置 Broker 实例
builtin_mqtt_broker = BuiltinMQTTBroker()
