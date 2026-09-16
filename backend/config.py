"""Configuration file"""
import socket
import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).parent.parent
DATASETS_DIR = BASE_DIR / "datasets"


def get_local_ip() -> str:
    """Get local IP address (prefer returning non-container internal IP)
    
    Improved detection for Docker environments:
    1. Check MQTT_BROKER_HOST environment variable first (highest priority, avoids warnings)
    2. Try host.docker.internal (Docker Desktop / some Docker versions)
    3. Try to get IP from network interfaces using system commands (prefer non-container IPs)
    4. Try to get host IP from Docker network information
    5. Fallback to connecting to external address
    6. Fallback to hostname resolution
    """
    import subprocess
    import re
    
    container_ranges = ['172.17.', '172.18.', '172.19.', '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.']
    # Docker Desktop virtual network ranges (macOS/Windows)
    # These are virtual network IPs created by Docker Desktop, not the actual host IP
    docker_desktop_ranges = ['192.168.65.', '192.168.49.', '192.168.39.', '10.0.2.']
    
    # Priority 0: Check MQTT_BROKER_HOST environment variable first (if set, use it directly to avoid warnings)
    env_ip = os.environ.get("MQTT_BROKER_HOST") or os.environ.get("HOST_IP") or os.environ.get("HOSTIP")
    if env_ip:
        try:
            socket.inet_aton(env_ip)
            # Verify it's not a container IP or Docker Desktop virtual IP
            if (env_ip != '127.0.0.1' and 
                not any(env_ip.startswith(prefix) for prefix in container_ranges) and
                not any(env_ip.startswith(prefix) for prefix in docker_desktop_ranges)):
                return env_ip
        except:
            # Invalid IP format, continue with other methods
            pass
    
    # Method 0: Try host.docker.internal (available in Docker Desktop and some Docker versions)
    # This is a special DNS name that resolves to the host IP
    # Note: In Docker Desktop, this might return the virtual network IP, so we'll filter it
    if any(os.path.exists(p) for p in ['/.dockerenv', '/proc/1/cgroup']):
        try:
            host_ip = socket.gethostbyname('host.docker.internal')
            if (host_ip and host_ip != '127.0.0.1' and 
                not any(host_ip.startswith(prefix) for prefix in container_ranges) and
                not any(host_ip.startswith(prefix) for prefix in docker_desktop_ranges)):
                return host_ip
        except (socket.gaierror, OSError):
            # host.docker.internal not available, continue with other methods
            pass
    
    # Method 1: Try to get IP from network interfaces using 'ip' command (Linux/Docker)
    try:
        result = subprocess.run(
            ['ip', 'addr', 'show'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            # Parse output to find non-container IPs
            # Prefer interfaces like eth0, en0, etc.
            preferred_interfaces = ['eth0', 'en0', 'en1', 'wlan0', 'wlan1']
            lines = result.stdout.split('\n')
            current_interface = None
            
            # First pass: look for preferred interfaces
            for idx, line in enumerate(lines):
                # Check for interface name
                if_match = re.match(r'^\d+:\s+(\w+):', line)
                if if_match:
                    current_interface = if_match.group(1)
                    if current_interface in preferred_interfaces:
                        # Look for inet address in next few lines
                        for next_line in lines[idx:idx+10]:
                            inet_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)/', next_line)
                            if inet_match:
                                ip = inet_match.group(1)
                                if (ip != '127.0.0.1' and 
                                    not any(ip.startswith(prefix) for prefix in container_ranges) and
                                    not any(ip.startswith(prefix) for prefix in docker_desktop_ranges)):
                                    return ip
            
            # Second pass: look for any non-container IP
            current_interface = None
            for line in lines:
                if_match = re.match(r'^\d+:\s+(\w+):', line)
                if if_match:
                    current_interface = if_match.group(1)
                    # Skip loopback and Docker interfaces
                    if current_interface.startswith('lo') or current_interface.startswith('docker'):
                        continue
                
                inet_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)/', line)
                if inet_match:
                    ip = inet_match.group(1)
                    if (ip != '127.0.0.1' and 
                        not any(ip.startswith(prefix) for prefix in container_ranges) and
                        not any(ip.startswith(prefix) for prefix in docker_desktop_ranges)):
                        return ip
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        # 'ip' command not available or failed, try other methods
        pass
    
    # Method 2: Try to get IP from 'ifconfig' command (macOS/Linux fallback)
    try:
        result = subprocess.run(
            ['ifconfig'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            # Parse ifconfig output
            preferred_interfaces = ['eth0', 'en0', 'en1', 'wlan0', 'wlan1']
            lines = result.stdout.split('\n')
            current_interface = None
            
            # First pass: look for preferred interfaces
            for idx, line in enumerate(lines):
                if_match = re.match(r'^(\w+):', line)
                if if_match:
                    current_interface = if_match.group(1)
                    if current_interface in preferred_interfaces:
                        # Look for inet address in this section
                        for next_line in lines[idx:idx+10]:
                            inet_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', next_line)
                            if inet_match:
                                ip = inet_match.group(1)
                                if (ip != '127.0.0.1' and 
                                    not any(ip.startswith(prefix) for prefix in container_ranges) and
                                    not any(ip.startswith(prefix) for prefix in docker_desktop_ranges)):
                                    return ip
            
            # Second pass: look for any non-container IP
            current_interface = None
            for line in lines:
                if_match = re.match(r'^(\w+):', line)
                if if_match:
                    current_interface = if_match.group(1)
                    if current_interface.startswith('lo') or current_interface.startswith('docker'):
                        continue
                
                inet_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', line)
                if inet_match:
                    ip = inet_match.group(1)
                    if (ip != '127.0.0.1' and 
                        not any(ip.startswith(prefix) for prefix in container_ranges) and
                        not any(ip.startswith(prefix) for prefix in docker_desktop_ranges)):
                        return ip
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        # 'ifconfig' command not available or failed, try other methods
        pass
    
    # Method 3: Get local IP by connecting to external address (standard method)
    detected_container_ip = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            # If obtained IP is not container internal IP, Docker Desktop virtual IP, or localhost, can use it
            if (ip != '127.0.0.1' and 
                not any(ip.startswith(prefix) for prefix in container_ranges) and
                not any(ip.startswith(prefix) for prefix in docker_desktop_ranges)):
                return ip
            # Store container IP for later use (if we're in Docker)
            if any(ip.startswith(prefix) for prefix in container_ranges):
                detected_container_ip = ip
        except Exception:
            pass
        finally:
            try:
                s.close()
            except:
                pass
    except Exception:
        pass
    
    # Method 4: Use hostname
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip != '127.0.0.1':
            if (not any(ip.startswith(prefix) for prefix in container_ranges) and
                not any(ip.startswith(prefix) for prefix in docker_desktop_ranges)):
                return ip
            # Store container IP for later use
            if not detected_container_ip and any(ip.startswith(prefix) for prefix in container_ranges):
                detected_container_ip = ip
    except Exception:
        pass
    
    # Method 5: If we detected a container IP, try to get host IP via Docker gateway
    # In Docker, the default gateway is usually the Docker host IP
    if detected_container_ip or any(os.path.exists(p) for p in ['/.dockerenv', '/proc/1/cgroup']):
        # We're likely in a Docker container, try to get host IP via gateway
        try:
            import subprocess
            # Try to get default gateway IP
            result = subprocess.run(
                ['ip', 'route', 'show', 'default'],
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
                                    # Gateway IP is usually Docker host IP
                                    # But we need to verify it's not a container IP or Docker Desktop virtual IP
                                    if (gateway_ip != '127.0.0.1' and 
                                        not any(gateway_ip.startswith(prefix) for prefix in container_ranges) and
                                        not any(gateway_ip.startswith(prefix) for prefix in docker_desktop_ranges)):
                                        return gateway_ip
                                except:
                                    pass
        except Exception:
            pass
        
        # Alternative: Try to get gateway from /proc/net/route
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
                                # Gateway IP might be Docker host IP
                                # But exclude Docker Desktop virtual network IPs
                                if (gateway_ip != '127.0.0.1' and 
                                    not any(gateway_ip.startswith(prefix) for prefix in container_ranges) and
                                    not any(gateway_ip.startswith(prefix) for prefix in docker_desktop_ranges)):
                                    return gateway_ip
                            except:
                                pass
        except Exception:
            pass
        
        # Method 6: Try to get host IP from Docker network information using docker inspect
        # This requires access to docker socket, which is available in docker-compose.yml
        try:
            # Get container name from environment variable or try to detect
            container_name = os.environ.get("CONTAINER_NAME", "camthink-aitoolstack")
            result = subprocess.run(
                ['docker', 'inspect', '--format', '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}', container_name],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0 and result.stdout.strip():
                gateway_ip = result.stdout.strip()
                try:
                    socket.inet_aton(gateway_ip)
                    # Exclude Docker Desktop virtual network IPs
                    if (gateway_ip != '127.0.0.1' and 
                        not any(gateway_ip.startswith(prefix) for prefix in container_ranges) and
                        not any(gateway_ip.startswith(prefix) for prefix in docker_desktop_ranges)):
                        return gateway_ip
                except:
                    pass
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            # Docker command not available or failed, continue
            pass
        
        # Method 7: Try to get host IP by connecting to host network from container
        # In some Docker setups, we can access host services via special IPs
        # Try common Docker host access patterns
        try:
            # Try to connect to host via gateway and get the IP that host sees
            # This is a heuristic: try to get the IP that would be used to reach the host
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            try:
                # Connect to a known external address to determine our route
                s.connect(('8.8.8.8', 80))
                local_ip = s.getsockname()[0]
                s.close()
                
                # If we got a container IP, try to infer host IP
                # Common pattern: if container is 172.17.0.x, host might be 172.17.0.1
                # But we want the actual host's LAN IP, not the Docker bridge IP
                # So we'll try to get the IP from the host's perspective
                if any(local_ip.startswith(prefix) for prefix in container_ranges):
                    # We're on a Docker network, try to get host IP from network info
                    # Get network name from container
                    container_name = os.environ.get("CONTAINER_NAME", "camthink-aitoolstack")
                    try:
                        # Try to get network gateway which is often the host IP on the Docker network
                        result = subprocess.run(
                            ['docker', 'inspect', '--format', '{{range $key, $value := .NetworkSettings.Networks}}{{$value.Gateway}}{{end}}', container_name],
                            capture_output=True,
                            text=True,
                            timeout=2
                        )
                        if result.returncode == 0:
                            gateway_ip = result.stdout.strip()
                            if gateway_ip:
                                try:
                                    socket.inet_aton(gateway_ip)
                                    # Gateway might be Docker bridge IP, but let's check if it's not in container ranges
                                    # Actually, gateway is usually in container ranges, so we need another approach
                                    # Try to get the host's actual IP by checking what IP the host would use
                                    # This is tricky - we might need to use a different method
                                except:
                                    pass
                    except:
                        pass
            except:
                pass
            finally:
                try:
                    s.close()
                except:
                    pass
        except Exception:
            pass
        
        # Method 8: Try to read host IP from /etc/hosts (some Docker setups add host IP there)
        try:
            with open("/etc/hosts", "r") as f:
                for line in f:
                    # Look for host.docker.internal or hostname entries
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        ip = parts[0]
                        hostnames = parts[1:]
                        # Check if this is a host IP entry (not localhost or container IP)
                        try:
                            socket.inet_aton(ip)
                            # Exclude Docker Desktop virtual network IPs
                            if (ip != '127.0.0.1' and 
                                not any(ip.startswith(prefix) for prefix in container_ranges) and
                                not any(ip.startswith(prefix) for prefix in docker_desktop_ranges) and
                                ('host.docker.internal' in hostnames or 
                                 any('host' in h.lower() for h in hostnames))):
                                return ip
                        except:
                            pass
        except Exception:
            pass
        
        # Method 9: For Docker Desktop on macOS/Windows, try to get actual host IP
        # by attempting to connect to host services and inferring the IP
        # In Docker Desktop, the host's actual network IP is not directly visible
        # We can try to get it by checking what IP the host would use for external connections
        try:
            # Try to get the IP that would be used to reach the host from outside
            # This is a heuristic: try common patterns
            # First, try to see if we can get host IP from Docker network bridge
            container_name = os.environ.get("CONTAINER_NAME", "camthink-aitoolstack")
            try:
                # Get all network interfaces from the host perspective
                # In Docker Desktop, we can try to infer host IP from network configuration
                result = subprocess.run(
                    ['docker', 'exec', container_name, 'sh', '-c', 'ip route | grep default'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                # Actually, we're already inside the container, so we can't exec into ourselves
                # Instead, let's try a different approach: check if we're in Docker Desktop
                # and try to get host IP via special methods
                
                # In Docker Desktop, we can try to connect to a service on the host
                # to determine the host's actual IP on the LAN
                # But this requires the host to have a service running, which is not guaranteed
                
                # Alternative: Try to parse network information more carefully
                # Look for IPs that are NOT in Docker ranges but are reachable
                pass
            except Exception:
                pass
        except Exception:
            pass
    
    # If all fail, try one more method: check if we can get host IP from Docker network
    # In Docker Desktop, we can try to get the host's actual IP by checking network configuration
    if any(os.path.exists(p) for p in ['/.dockerenv', '/proc/1/cgroup']):
        # We're in Docker, try additional methods
        try:
            # Method 10: Try to get host IP from Docker network bridge information
            # In some Docker setups, we can infer host IP from network configuration
            import subprocess
            # Try to get the IP that the host would use to reach the container
            # This is a heuristic: if we can determine the host's network interface IP
            # by checking what IP the host would use to connect to us
            
            # Try to get host IP by checking Docker network gateway
            # The gateway is usually the Docker host IP on the bridge network
            # But we want the host's actual LAN IP, not the bridge IP
            # So we'll try to get it from the host's perspective if possible
            
            # For Docker Desktop on macOS/Windows, the actual host IP is not directly accessible
            # We need to rely on environment variable or manual configuration
            # But we can try to get it from the host system if possible
            
            # Check if there's a way to get host IP from the host system
            # In Docker Desktop, we can try to use host.docker.internal
            # But that resolves to Docker Desktop's virtual IP, not the actual WiFi IP
            
        except Exception:
            pass
        
        # If we still don't have an IP and MQTT_BROKER_HOST is not set, log a warning
        # (If MQTT_BROKER_HOST was set, we would have returned it at the beginning of the function)
        if not os.environ.get("MQTT_BROKER_HOST"):
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                "[IP Detection] Running in Docker container but could not detect host IP. "
                "Please set MQTT_BROKER_HOST environment variable in docker-compose.yml "
                "or configure builtin_broker_host in system settings. "
                "In Docker Desktop, the actual host WiFi IP (e.g., 192.168.110.106) cannot be auto-detected."
            )
    return "127.0.0.1"


def get_mqtt_broker_host(request=None) -> str:
    """
    Get MQTT Broker address displayed externally
    Priority:
    1. Environment variable MQTT_BROKER_HOST (deployment-time configuration, highest priority)
    2. Manual override from database (builtin_broker_host) - user manual configuration
    3. Request headers (X-Forwarded-Host, Host, X-Real-IP) - only if not Docker gateway IP
    4. Auto-detect from local IP
    """
    # Priority 1: If MQTT_BROKER_HOST environment variable is configured, use it directly (deployment-time config)
    if settings.MQTT_BROKER_HOST:
        return settings.MQTT_BROKER_HOST
    
    # Priority 2: Check manual override from database (user manual configuration in UI)
    try:
        from backend.services.mqtt_config_service import mqtt_config_service
        cfg = mqtt_config_service.load_config()
        if cfg.builtin_broker_host and cfg.builtin_broker_host.strip():
            # Manual override is set in database, use it
            return cfg.builtin_broker_host.strip()
    except Exception:
        # If config service is not available, continue with other methods
        pass
    
    # Priority 3: If request object exists, try getting from request headers
    # But exclude Docker gateway IPs (172.17.x.x, 172.18.x.x, etc.) to avoid incorrect detection
    container_ip_ranges = ["172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31."]
    
    if request:
        # Priority 3.1: Try to get from X-Forwarded-Host (reverse proxy scenario, most reliable)
        forwarded_host = request.headers.get("X-Forwarded-Host", "")
        if forwarded_host:
            host_without_port = forwarded_host.split(":")[0]
            # Exclude localhost, container IPs, and Docker gateway IPs
            if (host_without_port not in ["localhost", "127.0.0.1", "0.0.0.0"] and 
                not any(host_without_port.startswith(prefix) for prefix in container_ip_ranges)):
                return host_without_port
        
        # Priority 3.2: Try to get from Host header
        host = request.headers.get("Host", "")
        if host:
            # Remove port number (if exists)
            host_without_port = host.split(":")[0]
            # Exclude localhost, container IPs, and Docker gateway IPs
            if (host_without_port not in ["localhost", "127.0.0.1", "0.0.0.0"] and 
                not any(host_without_port.startswith(prefix) for prefix in container_ip_ranges)):
                return host_without_port
        
        # Priority 3.3: Try to get from X-Real-IP (some reverse proxy settings)
        real_ip = request.headers.get("X-Real-IP", "")
        if real_ip:
            # Exclude localhost, container IPs, and Docker gateway IPs
            if (real_ip not in ["localhost", "127.0.0.1", "0.0.0.0"] and 
                not any(real_ip.startswith(prefix) for prefix in container_ip_ranges)):
                return real_ip
    
    # Priority 4: Try to get from environment variables (Docker Compose might set, or manual config)
    host_ip = os.environ.get("HOST_IP") or os.environ.get("HOSTIP") or os.environ.get("MQTT_BROKER_HOST") or os.environ.get("SERVER_IP")
    if host_ip:
        try:
            socket.inet_aton(host_ip)
            return host_ip
        except:
            pass
    
    # Priority 5: Auto-detect from local IP (improved detection for Docker environments)
    local_ip = get_local_ip()
    
    # Return detected IP (get_local_ip already prefers returning non-container IP)
    return local_ip


class Settings(BaseSettings):
    """Application configuration"""

    # Server configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    # DEBUG enables uvicorn --reload, DEBUG-level logs and the public
    # /docs + /openapi.json API map. Must stay False in production.
    DEBUG: bool = False

    # ========== API security ==========
    # When enabled, every /api and /ws request must present a valid key
    # (X-API-Key header, "Authorization: Bearer <key>", or ?api_key= for
    # <img> tags and WebSocket connections). /health and frontend static
    # files stay public. Generate a key with: openssl rand -hex 32
    # Comma-separate multiple keys to grant several clients access.
    API_AUTH_ENABLED: bool = False
    API_KEY: str = ""  # e.g. "a1b2c3..." or "key1,key2"
    # Comma-separated list of origins allowed by CORS. Leave empty to use the
    # development defaults; in production set it to the exact UI origin(s).
    CORS_ORIGINS: str = ""
    # Simple per-IP request rate limit applied to /api endpoints (0 = off).
    # Generous by design: behind Docker bridge/NAT many clients can share one
    # source IP, and image-heavy UI flows issue many requests.
    RATE_LIMIT_PER_MINUTE: int = 600

    # Upload size limits (DoS protection)
    MAX_ZIP_UNCOMPRESSED_MB: int = 8192  # Zip-bomb cap for extracted archives
    MAX_MODEL_UPLOAD_MB: int = 2048      # .pt model files
    MAX_DATASET_UPLOAD_MB: int = 4096    # Dataset import archives
    MAX_CALIBRATION_UPLOAD_MB: int = 1024  # Calibration image archives
    MAX_TLS_FILE_MB: int = 2             # PEM certificates/keys

    # MQTT configuration
    MQTT_ENABLED: bool = True  # Whether to enable MQTT service
    MQTT_USE_BUILTIN_BROKER: bool = True  # Whether to use built-in Broker (default use built-in)
    MQTT_BROKER: str = ""  # External Broker address (used when MQTT_USE_BUILTIN_BROKER=False, empty means auto use local IP)
    MQTT_BROKER_HOST: str = ""  # MQTT Broker external address (for Docker environment, empty means auto detect)
    MQTT_PORT: int = 1883  # MQTT port
    MQTT_BUILTIN_PORT: int = 1883  # Built-in Broker port
    MQTT_USERNAME: str = ""  # External Broker authentication (built-in Broker doesn't support auth yet)
    MQTT_PASSWORD: str = ""
    MQTT_UPLOAD_TOPIC: str = "annotator/upload/+"
    MQTT_RESPONSE_TOPIC_PREFIX: str = "annotator/response"
    MQTT_QOS: int = 1
    
    # Database configuration
    DATABASE_URL: str = f"sqlite:///{BASE_DIR}/data/annotator.db"
    
    # File storage configuration
    DATASETS_ROOT: Path = DATASETS_DIR
    MAX_IMAGE_SIZE_MB: int = 10
    
    # NE301 model compilation configuration
    NE301_PROJECT_PATH: str = ""  # NE301 project path (empty means use default path)
    NE301_USE_DOCKER: bool = True  # Whether to use Docker for compilation (default True)
    NE301_DOCKER_IMAGE: str = "camthink/ne301-dev:latest"  # Docker image name

    # NE301 auto-update configuration
    # Auto-pulling and executing code from an external repo at every startup
    # is a supply-chain risk; keep this disabled in production and update the
    # NE301 project deliberately (pinned commit) instead.
    NE301_AUTO_UPDATE: bool = False
    NE301_UPDATE_TIMEOUT: int = 30  # Update operation timeout (seconds)
    NE301_UPDATE_STASH_CHANGES: bool = False  # Whether to auto-stash local changes

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

# If MQTT_BROKER is empty, use local IP
if not settings.MQTT_BROKER:
    settings.MQTT_BROKER = get_local_ip()

# Ensure necessary directories exist
settings.DATASETS_ROOT.mkdir(parents=True, exist_ok=True)
(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)

