"""
NE301 项目初始化工具
在应用启动时自动下载和初始化 NE301 项目
"""
import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional
from backend.config import settings

NE301_REPO_URL = "https://github.com/camthink-ai/ne301.git"
DEFAULT_NE301_PATH = Path("/app/ne301")


def ensure_ne301_project(ne301_path: Optional[Path] = None) -> Path:
    """
    确保 NE301 项目存在，如果不存在则自动克隆
    
    Args:
        ne301_path: NE301 项目路径，如果为 None 则使用默认路径或配置中的路径
    
    Returns:
        NE301 项目路径
    """
    # 确定目标路径
    if ne301_path is None:
        # 优先使用环境变量或配置中的路径
        env_path = os.environ.get("NE301_PROJECT_PATH")
        if env_path:
            ne301_path = Path(env_path)
        elif hasattr(settings, 'NE301_PROJECT_PATH') and settings.NE301_PROJECT_PATH:
            ne301_path = Path(settings.NE301_PROJECT_PATH)
        else:
            # 使用默认路径
            ne301_path = DEFAULT_NE301_PATH
    
    ne301_path = Path(ne301_path).resolve()
    
    # 检查挂载的主机目录（/workspace/ne301）
    # 在 Docker Compose 中，主机目录会挂载到 /workspace/ne301
    workspace_path = Path("/workspace/ne301")
    if workspace_path.exists() and workspace_path.is_dir():
        # 检查是否为空目录或符号链接
        try:
            if not any(workspace_path.iterdir()):
                # 空目录，克隆项目
                print(f"[NE301] Workspace directory is empty, cloning to {workspace_path}")
                subprocess.run(
                    ["git", "clone", NE301_REPO_URL, str(workspace_path)],
                    check=True,
                    capture_output=True,
                    text=True
                )
                print(f"[NE301] Successfully cloned to workspace: {workspace_path}")
            # 使用工作空间目录
            ne301_path = workspace_path
        except Exception as e:
            print(f"[NE301] Warning: Failed to use workspace directory: {e}")
    
    # 如果目录不存在，则克隆
    if not ne301_path.exists():
        print(f"[NE301] Project directory not found: {ne301_path}")
        print(f"[NE301] Cloning NE301 project from {NE301_REPO_URL}...")
        
        try:
            # 检查 git 是否可用
            subprocess.run(
                ["git", "--version"],
                check=True,
                capture_output=True
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("[NE301] ERROR: git is not available. Cannot clone NE301 project.")
            print("[NE301] Please install git in Dockerfile or mount the project manually.")
            return ne301_path
        
        # 创建父目录
        ne301_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 克隆项目
        try:
            subprocess.run(
                ["git", "clone", NE301_REPO_URL, str(ne301_path)],
                check=True,
                capture_output=True,
                text=True
            )
            print(f"[NE301] Successfully cloned NE301 project to {ne301_path}")
        except subprocess.CalledProcessError as e:
            print(f"[NE301] ERROR: Failed to clone NE301 project: {e}")
            print(f"[NE301] stdout: {e.stdout}")
            print(f"[NE301] stderr: {e.stderr}")
            print(f"[NE301] You can manually clone the project or set NE301_PROJECT_PATH to an existing path.")
            return ne301_path
    
    # 如果目录存在但是空的，尝试克隆
    if ne301_path.exists() and ne301_path.is_dir():
        try:
            if not any(ne301_path.iterdir()):
                print(f"[NE301] Directory exists but is empty, cloning...")
                subprocess.run(
                    ["git", "clone", NE301_REPO_URL, str(ne301_path)],
                    check=True,
                    capture_output=True,
                    text=True
                )
                print(f"[NE301] Successfully cloned to {ne301_path}")
        except Exception as e:
            print(f"[NE301] Warning: Directory check failed: {e}")
    
    # 验证项目结构
    model_dir = ne301_path / "Model"
    makefile = ne301_path / "Makefile"
    
    if not model_dir.exists():
        print(f"[NE301] WARNING: Model directory not found in {ne301_path}")
        return ne301_path
    
    if not makefile.exists():
        print(f"[NE301] WARNING: Makefile not found in {ne301_path}")
        return ne301_path
    
    print(f"[NE301] Project ready at: {ne301_path}")
    return ne301_path


def get_ne301_project_path() -> Path:
    """
    获取 NE301 项目路径（自动初始化如果不存在）
    
    Returns:
        NE301 项目路径
    """
    return ensure_ne301_project()


if __name__ == "__main__":
    # 测试
    path = ensure_ne301_project()
    print(f"NE301 project path: {path}")
