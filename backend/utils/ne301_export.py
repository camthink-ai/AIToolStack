"""
NE301 模型导出工具
用于生成 NE301 设备可用的模型包
"""
import json
import logging
import subprocess
import shutil
from pathlib import Path
from typing import Dict, Optional, List, Tuple
import os

try:
    import tensorflow as tf
    TENSORFLOW_AVAILABLE = True
except ImportError:
    TENSORFLOW_AVAILABLE = False

logger = logging.getLogger(__name__)


def _convert_to_json_serializable(obj):
    """
    递归地将字典、列表中的 NumPy 类型转换为 Python 原生类型
    确保对象可以被 JSON 序列化
    兼容 NumPy 1.x 和 2.x
    """
    import numpy as np
    
    if isinstance(obj, dict):
        return {key: _convert_to_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_convert_to_json_serializable(item) for item in obj]
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    
    # 使用更安全的方式检查 NumPy 类型（兼容 NumPy 1.x 和 2.x）
    # 首先检查是否是 NumPy 基础类型（不访问可能不存在的属性）
    if isinstance(obj, np.integer):
        try:
            return int(obj.item())
        except (ValueError, OverflowError, AttributeError):
            return int(obj)
    elif isinstance(obj, np.floating):
        try:
            return float(obj.item())
        except (ValueError, OverflowError, AttributeError):
            return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    
    # 如果 isinstance 检查失败，尝试通过类型名称判断
    obj_type_name = type(obj).__name__
    if 'int' in obj_type_name.lower() and hasattr(obj, 'item'):
        try:
            return int(obj.item())
        except (ValueError, OverflowError, AttributeError):
            try:
                return int(obj)
            except (ValueError, OverflowError):
                return obj
    elif 'float' in obj_type_name.lower() and hasattr(obj, 'item'):
        try:
            return float(obj.item())
        except (ValueError, OverflowError, AttributeError):
            try:
                return float(obj)
            except (ValueError, OverflowError):
                return obj
    elif 'bool' in obj_type_name.lower():
        return bool(obj)
    else:
        return obj


def extract_tflite_quantization_params(tflite_path: Path) -> Tuple[Optional[float], Optional[int], Optional[Tuple[int, int, int]]]:
    """
    从 TFLite 模型中提取量化参数和输出尺寸
    
    Returns:
        (output_scale, output_zero_point, output_shape) 元组
        如果无法提取，返回 (None, None, None)
        所有返回值都转换为 Python 原生类型（可 JSON 序列化）
    """
    if not TENSORFLOW_AVAILABLE:
        logger.warning("TensorFlow not available, cannot extract quantization parameters from TFLite model")
        return None, None, None
    
    try:
        # 加载 TFLite 模型
        interpreter = tf.lite.Interpreter(model_path=str(tflite_path))
        interpreter.allocate_tensors()
        
        # 获取输出张量详情
        output_details = interpreter.get_output_details()[0]  # 假设只有一个输出
        output_shape = output_details['shape']  # 例如 [1, 84, 1344]
        
        # 转换 output_shape 为 Python 原生类型（处理 NumPy int64/int32）
        if output_shape is not None:
            output_shape = tuple(int(x) for x in output_shape)
        
        # 提取量化参数
        if 'quantization_parameters' in output_details:
            quant_params = output_details['quantization_parameters']
            
            # 提取 scale 和 zero_point，并转换为 Python 原生类型
            if quant_params.get('scales') and len(quant_params['scales']) > 0:
                scale_val = quant_params['scales'][0]
                # 转换 NumPy float 类型为 Python float
                output_scale = float(scale_val) if scale_val is not None else None
            else:
                output_scale = None
            
            if quant_params.get('zero_points') and len(quant_params['zero_points']) > 0:
                zp_val = quant_params['zero_points'][0]
                # 转换 NumPy int 类型为 Python int
                output_zero_point = int(zp_val) if zp_val is not None else None
            else:
                output_zero_point = None
            
            logger.info(f"Extracted from TFLite model: scale={output_scale} (type: {type(output_scale)}), zero_point={output_zero_point} (type: {type(output_zero_point)}), shape={output_shape}")
            return output_scale, output_zero_point, output_shape
        else:
            logger.warning("TFLite model output does not have quantization parameters")
            return None, None, output_shape
    except Exception as e:
        logger.warning(f"Failed to extract quantization parameters from TFLite model: {e}", exc_info=True)
        return None, None, None


def generate_ne301_json_config(
    tflite_path: Path,
    model_name: str,
    input_size: int,
    num_classes: int,
    class_names: List[str],
    output_scale: Optional[float] = None,
    output_zero_point: Optional[int] = None,
    confidence_threshold: float = 0.25,
    iou_threshold: float = 0.45,
    max_detections: int = 300,
    total_boxes: Optional[int] = None,
    output_shape: Optional[Tuple[int, int, int]] = None,
) -> Dict:
    """
    生成 NE301 JSON 配置文件
    
    Args:
        tflite_path: TFLite 模型文件路径
        model_name: 模型名称（不含扩展名）
        input_size: 模型输入尺寸（如 256）
        num_classes: 类别数量
        class_names: 类别名称列表
        output_scale: 输出量化 scale（如果为 None，会尝试从 TFLite 模型提取）
        output_zero_point: 输出量化 zero_point（如果为 None，会尝试从 TFLite 模型提取）
        confidence_threshold: 置信度阈值
        iou_threshold: IoU 阈值
        max_detections: 最大检测数量
        total_boxes: 总框数（如果为 None，会从 output_shape 或根据 input_size 估算）
        output_shape: 模型输出形状 (batch, height, width)，如果为 None 会尝试从 TFLite 模型提取
    
    Returns:
        JSON 配置字典
    """
    # 尝试从 TFLite 模型提取量化参数和输出尺寸
    if output_scale is None or output_zero_point is None or output_shape is None:
        extracted_scale, extracted_zero_point, extracted_shape = extract_tflite_quantization_params(tflite_path)
        if extracted_scale is not None:
            output_scale = extracted_scale
        if extracted_zero_point is not None:
            output_zero_point = extracted_zero_point
        if extracted_shape is not None:
            output_shape = extracted_shape
    
    # 使用默认值（如果无法提取）
    if output_scale is None:
        output_scale = 0.003921568859368563  # 默认 uint8->int8 scale (1/255)
    if output_zero_point is None:
        output_zero_point = -128  # 默认 int8 zero_point
    
    # 从 output_shape 提取输出高度和宽度
    if output_shape is not None:
        # output_shape 格式: (batch, height, width) 例如 (1, 84, 1344)
        output_height = output_shape[1] if len(output_shape) > 1 else (4 + num_classes)
        output_width = output_shape[2] if len(output_shape) > 2 else None
        if output_width is not None and total_boxes is None:
            total_boxes = output_width
    else:
        output_height = 4 + num_classes  # 默认值：4 (bbox) + num_classes
    
    # 计算 YOLOv8 输出尺寸
    # YOLOv8 256x256: 输出为 (1, 84, 1344) 或类似
    # 84 = 4 (bbox) + 80 (classes)
    # 1344 = 3 scales * (32*32 + 16*16 + 8*8) = 3 * 448 = 1344
    if total_boxes is None:
        # 根据 input_size 估算
        # YOLOv8 在不同输入尺寸下的网格数不同
        if input_size == 256:
            total_boxes = 1344  # 3 * (32*32 + 16*16 + 8*8) = 3 * 448
        elif input_size == 320:
            total_boxes = 2100  # 3 * (40*40 + 20*20 + 10*10) = 3 * 700
        elif input_size == 416:
            total_boxes = 3549  # 3 * (52*52 + 26*26 + 13*13) = 3 * 1183
        elif input_size == 640:
            total_boxes = 8400  # 3 * (80*80 + 40*40 + 20*20) = 3 * 2800
        else:
            # 默认估算
            scale = input_size // 8
            total_boxes = 3 * (scale * scale + (scale // 2) ** 2 + (scale // 4) ** 2)
    
    # 如果从模型提取的输出高度与计算的不同，使用提取的值
    # 但确保至少是 4 + num_classes（bbox + classes）
    if output_height < 4 + num_classes:
        output_height = 4 + num_classes
        logger.warning(f"Output height {output_height} is less than expected (4 + {num_classes}), using calculated value")
    
    config = {
        "version": "1.0.0",
        "model_info": {
            "name": model_name,
            "version": "1.0.0",
            "description": f"YOLOv8 model for object detection (Int8 quantized, {input_size}x{input_size})",
            "type": "OBJECT_DETECTION",
            "framework": "TFLITE",
            "author": "NeoEyesTool"
        },
        "input_spec": {
            "width": input_size,
            "height": input_size,
            "channels": 3,
            "data_type": "uint8",
            "color_format": "RGB888_YUV444_1",
            "normalization": {
                "enabled": True,
                "mean": [0.0, 0.0, 0.0],
                "std": [255.0, 255.0, 255.0]
            }
        },
        "output_spec": {
            "num_outputs": 1,
            "outputs": [
                {
                    "name": "output0",
                    "batch": 1,
                    "height": output_height,
                    "width": total_boxes,
                    "channels": 1,
                    "data_type": "int8",
                    "scale": output_scale,
                    "zero_point": output_zero_point
                }
            ]
        },
        "memory": {
            "exec_memory_pool": 874512384,
            "exec_memory_size": 1835008,
            "ext_memory_pool": 2415919104,
            "ext_memory_size": 301056,
            "alignment_requirement": 32
        },
        "postprocess_type": "pp_od_yolo_v8_ui",  # uint8 input, int8 output (推荐)
        "postprocess_params": {
            "num_classes": num_classes,
            "class_names": class_names,
            "confidence_threshold": confidence_threshold,
            "iou_threshold": iou_threshold,
            "max_detections": max_detections,
            "total_boxes": total_boxes,
            "raw_output_scale": output_scale,
            "raw_output_zero_point": output_zero_point
        },
        "runtime": {
            "execution": {
                "mode": "SYNC",
                "priority": 5,
                "timeout_ms": 5000
            },
            "memory_management": {
                "cache_policy": "WRITE_BACK",
                "memory_pool_size": 2097152,
                "garbage_collection": False
            },
            "debugging": {
                "log_level": "INFO",
                "performance_monitoring": True,
                "memory_profiling": False
            }
        }
    }
    
    # 确保所有值都是 JSON 可序列化的（转换 NumPy 类型）
    config = _convert_to_json_serializable(config)
    
    return config


def copy_model_to_ne301_project(
    tflite_path: Path,
    json_config: Dict,
    ne301_project_path: Path,
    model_name: str
) -> Tuple[Path, Path]:
    """
    将模型文件和 JSON 配置复制到 NE301 项目目录
    
    Args:
        tflite_path: TFLite 模型文件路径
        json_config: JSON 配置字典
        ne301_project_path: NE301 项目根目录
        model_name: 模型名称（不含扩展名）
    
    Returns:
        (tflite_dest_path, json_dest_path) 元组
    """
    # 确保 Model/weights 目录存在
    weights_dir = ne301_project_path / "Model" / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)
    
    # 复制 TFLite 文件
    tflite_dest = weights_dir / f"{model_name}.tflite"
    shutil.copy2(tflite_path, tflite_dest)
    logger.info(f"Copied TFLite model to {tflite_dest}")
    
    # 保存 JSON 配置（确保所有值都是可序列化的）
    json_dest = weights_dir / f"{model_name}.json"
    # 再次确保配置是可序列化的（双重保险）
    json_config_clean = _convert_to_json_serializable(json_config)
    with open(json_dest, "w", encoding="utf-8") as f:
        json.dump(json_config_clean, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved JSON config to {json_dest}")
    
    return tflite_dest, json_dest


def build_ne301_model(
    ne301_project_path: Path,
    model_name: str,
    docker_image: str = "camthink/ne301-dev:latest",
    use_docker: bool = True
) -> Optional[Path]:
    """
    使用 NE301 开发环境编译模型
    
    Args:
        ne301_project_path: NE301 项目根目录
        model_name: 模型名称（用于更新 Model/Makefile 中的 MODEL_NAME）
        docker_image: Docker 镜像名称
        use_docker: 是否使用 Docker（否则需要本地有 NE301 开发环境）
    
    Returns:
        编译生成的模型包路径 (build/ne301_Model.bin)，失败返回 None
    """
    ne301_project_path = Path(ne301_project_path).resolve()
    
    # 检查 NE301 项目目录
    if not (ne301_project_path / "Model").exists():
        raise FileNotFoundError(f"NE301 项目目录不存在或缺少 Model 目录: {ne301_project_path}")
    
    if not (ne301_project_path / "Makefile").exists():
        raise FileNotFoundError(f"NE301 项目根目录缺少 Makefile: {ne301_project_path}")
    
    # 验证模型文件是否存在（在更新 Makefile 之前）
    weights_dir = ne301_project_path / "Model" / "weights"
    expected_tflite = weights_dir / f"{model_name}.tflite"
    expected_json = weights_dir / f"{model_name}.json"
    
    if not expected_tflite.exists():
        raise FileNotFoundError(
            f"模型文件不存在: {expected_tflite}\n"
            f"请确保在执行 build_ne301_model 之前已调用 copy_model_to_ne301_project()"
        )
    if not expected_json.exists():
        raise FileNotFoundError(
            f"JSON 配置文件不存在: {expected_json}\n"
            f"请确保在执行 build_ne301_model 之前已调用 copy_model_to_ne301_project()"
        )
    logger.info(f"验证模型文件存在: {expected_tflite}, {expected_json}")
    
    # 更新 Model/Makefile 中的 MODEL_NAME
    model_makefile = ne301_project_path / "Model" / "Makefile"
    if model_makefile.exists():
        try:
            content = model_makefile.read_text(encoding="utf-8")
            # 更新 MODEL_NAME
            lines = content.split("\n")
            updated = False
            for i, line in enumerate(lines):
                # 匹配 MODEL_NAME = ... 或 MODEL_NAME=...（允许空格）
                if line.strip().startswith("MODEL_NAME") and "=" in line:
                    # 提取注释（如果有）
                    comment = ""
                    if "#" in line:
                        comment_part = line.split("#", 1)[1]
                        comment = f"  # {comment_part.strip()}"
                    lines[i] = f"MODEL_NAME = {model_name}{comment}"
                    updated = True
                    logger.info(f"Found and updating MODEL_NAME at line {i+1}: {line.strip()} -> MODEL_NAME = {model_name}")
                    break
            
            if not updated:
                # 如果没有找到，在 Model files 部分后面添加
                # 查找 "Model files" 注释行之后的位置
                insert_pos = 0
                for i, line in enumerate(lines):
                    if "Model files" in line or "# Model files" in line.lower():
                        # 找到下一个空行或相关行之后插入
                        for j in range(i+1, len(lines)):
                            if lines[j].strip().startswith("MODEL_NAME"):
                                insert_pos = j
                                break
                        if insert_pos == 0:
                            insert_pos = i + 1
                        break
                
                if insert_pos > 0:
                    lines.insert(insert_pos, f"MODEL_NAME = {model_name}")
                else:
                    # 如果找不到合适位置，在文件开头添加
                    lines.insert(0, f"MODEL_NAME = {model_name}")
                logger.info(f"Added MODEL_NAME at line {insert_pos+1}")
            
            # 写入文件
            updated_content = "\n".join(lines)
            model_makefile.write_text(updated_content, encoding="utf-8")
            
            # 验证更新是否成功
            verify_content = model_makefile.read_text(encoding="utf-8")
            if f"MODEL_NAME = {model_name}" in verify_content or f"MODEL_NAME={model_name}" in verify_content:
                logger.info(f"Successfully updated MODEL_NAME to '{model_name}' in {model_makefile}")
            else:
                logger.warning(f"Updated Makefile but verification failed. Please check {model_makefile}")
                # 再次尝试读取，显示实际内容以便调试
                actual_lines = verify_content.split('\n')
                for i, line in enumerate(actual_lines[:30], 1):  # 只显示前30行
                    if 'MODEL_NAME' in line:
                        logger.warning(f"  Line {i}: {line}")
                
        except Exception as e:
            logger.error(f"Failed to update Model/Makefile: {e}", exc_info=True)
            raise
    else:
        logger.error(f"Model/Makefile 不存在: {model_makefile}")
        raise FileNotFoundError(f"Model/Makefile 不存在: {model_makefile}")
    
    if use_docker:
        # 使用 Docker 编译
        return _build_with_docker(ne301_project_path, docker_image, model_name)
    else:
        # 本地编译（需要安装 NE301 开发环境）
        return _build_local(ne301_project_path)


def _build_with_docker(
    ne301_project_path: Path,
    docker_image: str = "camthink/ne301-dev:latest",
    model_name: Optional[str] = None
) -> Optional[Path]:
    """使用 Docker 容器编译模型"""
    ne301_project_path = Path(ne301_project_path).resolve()
    
    # 如果 model_name 未提供，尝试从 Makefile 读取
    if model_name is None:
        model_makefile = ne301_project_path / "Model" / "Makefile"
        if model_makefile.exists():
            try:
                content = model_makefile.read_text(encoding="utf-8")
                for line in content.split("\n"):
                    if line.strip().startswith("MODEL_NAME") and "=" in line:
                        # 提取 MODEL_NAME 的值
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            model_name = parts[1].strip().split("#")[0].strip()  # 移除注释
                            logger.info(f"Read MODEL_NAME from Makefile: {model_name}")
                            break
            except Exception as e:
                logger.warning(f"Failed to read MODEL_NAME from Makefile: {e}")
    
    # 如果仍然没有 model_name，使用通配符
    model_name_pattern = model_name if model_name else "*"
    
    # 检测系统架构
    import platform
    machine = platform.machine().lower()
    is_arm64 = machine in ('arm64', 'aarch64')
    
    # 检查 Docker 镜像是否存在
    check_cmd = ["docker", "images", "-q", docker_image]
    result = subprocess.run(check_cmd, capture_output=True, text=True)
    if not result.stdout.strip():
        logger.warning(f"Docker 镜像 {docker_image} 不存在，尝试拉取...")
        pull_cmd = ["docker", "pull"]
        if is_arm64:
            # ARM64 架构需要拉取 AMD64 镜像（使用 --platform）
            pull_cmd.extend(["--platform", "linux/amd64"])
        pull_cmd.append(docker_image)
        pull_result = subprocess.run(pull_cmd, capture_output=True, text=True)
        if pull_result.returncode != 0:
            raise RuntimeError(f"无法拉取 Docker 镜像 {docker_image}: {pull_result.stderr}")
    
    # 构建 Docker 命令
    # 问题：在 Docker-in-Docker 场景中，容器内的路径无法直接挂载到另一个容器
    # 解决方案：使用 Docker volume 或者检查是否有主机路径映射
    docker_cmd = [
        "docker", "run", "--rm",
    ]
    
    # 如果是 ARM64 架构，添加平台参数
    if is_arm64:
        docker_cmd.extend(["--platform", "linux/amd64"])
    
    # 检查是否在容器内运行
    is_in_container = Path("/.dockerenv").exists() or os.environ.get("container") == "docker"
    
    if is_in_container:
        # 在容器内运行 Docker-in-Docker
        # Docker Desktop 的限制：容器内路径无法直接挂载到另一个容器
        # 解决方案：获取对应的主机路径
        
        logger.info(f"[NE301] 检测到在容器内运行，开始自动检测主机路径...")
        
        # 检查是否有工作空间挂载（/workspace/ne301）
        workspace_path = Path("/workspace/ne301")
        mount_path = None
        
        logger.info(f"[NE301] 检查容器内路径: {workspace_path} (exists: {workspace_path.exists()}, is_dir: {workspace_path.is_dir() if workspace_path.exists() else 'N/A'})")
        
        def validate_mount_path(path, strict=True):
            """验证挂载路径是否有效
            Args:
                path: 要验证的路径
                strict: 如果为 True，检查路径是否存在；如果为 False，只检查格式
            """
            if not path:
                return False
            try:
                p = Path(path)
                if strict:
                    # 严格模式：检查路径是否存在，且包含 Model 目录（NE301 项目的特征）
                    return p.exists() and (p / "Model").exists()
                else:
                    # 宽松模式：只检查路径格式（主机路径在容器内无法验证）
                    return len(str(path)) > 0 and "/" in str(path)
            except Exception:
                return False
        
        if workspace_path.exists() and workspace_path.is_dir():
            # 优先通过 Docker inspect 获取主机路径（最可靠）
            all_mounts = None
            try:
                # 通过环境变量获取容器名，或使用默认名
                # 优先使用 CONTAINER_NAME，然后是 HOSTNAME，最后是默认值
                container_names = [
                    os.environ.get("CONTAINER_NAME"),
                    os.environ.get("HOSTNAME"),
                    "neoeyestool"
                ]
                # 过滤掉 None 值
                container_names = [name for name in container_names if name]
                
                logger.info(f"[NE301] 尝试通过 Docker inspect 获取主机路径，容器名候选: {container_names}")
                
                for name in container_names:
                    if not name:
                        continue
                    try:
                        inspect_cmd = ["docker", "inspect", name, "--format", "{{json .Mounts}}"]
                        logger.debug(f"[NE301] 执行命令: {' '.join(inspect_cmd)}")
                        result = subprocess.run(inspect_cmd, capture_output=True, text=True, timeout=5)
                        logger.debug(f"[NE301] Docker inspect 返回码: {result.returncode}")
                        if result.returncode == 0:
                            all_mounts = json.loads(result.stdout)
                            logger.info(f"[NE301] 找到 {len(all_mounts)} 个挂载点")
                            
                            # 输出所有挂载点信息（用于调试）
                            logger.info(f"[NE301] 所有挂载点信息：")
                            for i, mount in enumerate(all_mounts):
                                logger.info(f"[NE301]   #{i+1}: {mount.get('Source')} -> {mount.get('Destination')}")
                            
                            # 首先查找 /workspace/ne301 挂载点
                            for mount in all_mounts:
                                dest = mount.get("Destination")
                                src = mount.get("Source")
                                logger.debug(f"[NE301] 检查挂载点: {src} -> {dest}")
                                if dest == "/workspace/ne301":
                                    candidate_path = src
                                    logger.info(f"[NE301] 找到匹配的挂载点: {candidate_path} -> /workspace/ne301")
                                    # 在容器内验证主机路径时，使用宽松模式（只检查格式，不检查存在性）
                                    if validate_mount_path(candidate_path, strict=False):
                                        mount_path = candidate_path
                                        logger.info(f"[NE301] ✓ 从 Docker inspect 找到有效主机路径: {mount_path}")
                                        logger.info(f"[NE301]   注意：主机路径在容器内无法直接验证存在性，将直接使用")
                                        break
                            if mount_path:
                                break
                        else:
                            logger.warning(f"[NE301] Docker inspect {name} 失败: returncode={result.returncode}, stderr={result.stderr[:200]}")
                    except json.JSONDecodeError as e:
                        logger.warning(f"[NE301] Docker inspect {name} JSON 解析失败: {e}, stdout={result.stdout[:200] if 'result' in locals() else 'N/A'}")
                        continue
                    except Exception as e:
                        logger.warning(f"[NE301] Docker inspect {name} 执行异常: {type(e).__name__}: {e}")
                        continue
            except Exception as e:
                logger.warning(f"[NE301] Docker inspect 过程异常: {type(e).__name__}: {e}")
            
            # 如果未找到 ne301 挂载点，尝试通过其他挂载点推断项目根目录
            if not mount_path:
                if all_mounts:
                    logger.info(f"[NE301] 未找到直接的 /workspace/ne301 挂载点，尝试通过其他挂载点推断...")
                    try:
                        # 尝试通过 datasets 挂载点推断项目根目录（最可靠的方法）
                        # docker-compose.yml 中: ./datasets:/app/datasets 和 ./ne301:/workspace/ne301
                        # 如果找到 ./datasets 的主机路径，可以推断出 ./ne301 的路径
                        datasets_host_path = None
                        for mount in all_mounts:
                            if mount.get("Destination") == "/app/datasets":
                                datasets_host_path = mount.get("Source")
                                logger.info(f"[NE301] 找到 datasets 挂载点: {datasets_host_path} -> /app/datasets")
                                break
                        
                        if datasets_host_path:
                            # datasets 路径是项目根目录下的 datasets 目录
                            # 如果 datasets_host_path = /path/to/project/datasets
                            # 那么 ne301_host_path 应该是 /path/to/project/ne301
                            try:
                                datasets_path = Path(datasets_host_path)
                                # 验证 datasets 路径格式是否正确（目录名应该是 datasets）
                                # 注意：在容器内无法验证主机路径是否存在，所以只检查格式
                                if datasets_path.name == "datasets":
                                    inferred_ne301_path = datasets_path.parent / "ne301"
                                    # 在容器内验证推断的主机路径时，使用宽松模式（只检查格式）
                                    if validate_mount_path(str(inferred_ne301_path), strict=False):
                                        mount_path = str(inferred_ne301_path)
                                        logger.info(f"[NE301] ✓ 通过 datasets 挂载点推断出主机路径: {mount_path}")
                                        logger.info(f"[NE301]   项目根目录: {datasets_path.parent}")
                                        logger.info(f"[NE301]   推断的 ne301 路径: {inferred_ne301_path}")
                                        logger.info(f"[NE301]   注意：路径推断基于 docker-compose.yml 的挂载配置")
                                        logger.info(f"[NE301]   注意：主机路径在容器内无法直接验证存在性，将直接使用")
                                    else:
                                        logger.warning(f"[NE301] 推断的路径格式无效: {inferred_ne301_path}")
                                else:
                                    logger.warning(f"[NE301] datasets 路径格式异常（期望目录名为 'datasets'，实际为 '{datasets_path.name}'）")
                                    logger.warning(f"[NE301] datasets 完整路径: {datasets_host_path}")
                            except Exception as e:
                                logger.warning(f"[NE301] 解析 datasets 路径失败: {type(e).__name__}: {e}")
                        else:
                            logger.warning(f"[NE301] 未找到 /app/datasets 挂载点，无法通过 datasets 推断 ne301 路径")
                    except Exception as e:
                        logger.warning(f"[NE301] 通过挂载点推断路径失败: {type(e).__name__}: {e}")
                else:
                    logger.warning(f"[NE301] Docker inspect 未能获取挂载点信息（all_mounts 为 None），无法推断路径")
            
            # 如果 Docker inspect 失败，尝试从 /proc/mounts 获取（但需要验证）
            if not mount_path:
                try:
                    with open("/proc/mounts", "r") as f:
                        for line in f:
                            parts = line.split()
                            if len(parts) >= 2:
                                host_path = parts[0]  # 主机路径
                                container_path = parts[1]  # 容器路径
                                if container_path == "/workspace/ne301" or container_path == str(workspace_path):
                                    # 跳过明显错误的路径（Docker Desktop 的虚拟路径）
                                    if "/run/host" in host_path or "/tmp" in host_path or not host_path.startswith("/"):
                                        logger.debug(f"[NE301] 跳过可疑路径: {host_path}")
                                        continue
                                    # 验证路径格式（使用宽松模式，因为主机路径在容器内无法验证存在性）
                                    if validate_mount_path(host_path, strict=False):
                                        mount_path = host_path
                                        logger.info(f"[NE301] 从 /proc/mounts 找到有效主机路径: {mount_path}")
                                        logger.info(f"[NE301]   注意：主机路径在容器内无法直接验证存在性，将直接使用")
                                        break
                except Exception as e:
                    logger.warning(f"[NE301] 无法读取 /proc/mounts: {e}")
        
        # 如果仍然无法获取主机路径，尝试最后的备选方案
        if not mount_path:
            # 最后的备选方案：检查环境变量（仅作为兜底方案，不推荐手动设置）
            env_path = os.environ.get("NE301_HOST_PATH")
            if env_path:
                env_path = env_path.strip()
                if env_path:
                    # 验证环境变量路径格式（宽松模式）
                    if validate_mount_path(env_path, strict=False):
                        mount_path = env_path
                        logger.warning(f"[NE301] ⚠️ 使用环境变量 NE301_HOST_PATH 作为备选方案: {mount_path}")
                        logger.warning(f"[NE301] 建议：检查为什么自动检测失败，环境变量仅应作为临时解决方案")
                    else:
                        logger.error(f"[NE301] 环境变量 NE301_HOST_PATH 格式无效: {env_path}")
            
            if not mount_path:
                # 最终回退：使用容器路径（会失败，但错误信息会提示用户）
                mount_path = str(workspace_path.resolve()) if workspace_path.exists() else str(ne301_project_path.resolve())
                logger.error(f"[NE301] ✗ 无法自动获取主机路径！")
                logger.error(f"[NE301] 已尝试的方法：")
                logger.error(f"[NE301]   1. Docker inspect（查找 /workspace/ne301 挂载点）")
                logger.error(f"[NE301]   2. 通过 datasets 挂载点推断（从 /app/datasets 推断项目根目录）")
                logger.error(f"[NE301]   3. /proc/mounts 解析")
                logger.error(f"[NE301]   4. 环境变量 NE301_HOST_PATH（当前值: {os.environ.get('NE301_HOST_PATH', '未设置')}）")
                logger.error(f"[NE301]")
                logger.error(f"[NE301] 当前回退路径（可能不正确）: {mount_path}")
                logger.error(f"[NE301] 这将导致 Docker-in-Docker 挂载失败")
                logger.error(f"[NE301]")
                logger.error(f"[NE301] 临时解决方案：")
                logger.error(f"[NE301]   在 docker-compose.yml 中设置环境变量 NE301_HOST_PATH")
                logger.error(f"[NE301]   例如: - NE301_HOST_PATH=/path/to/project/ne301")
                logger.error(f"[NE301]")
                logger.error(f"[NE301] 请检查：")
                logger.error(f"[NE301]   1. docker-compose.yml 中是否配置了 ./ne301:/workspace/ne301 挂载")
                logger.error(f"[NE301]   2. docker-compose.yml 中是否配置了 ./datasets:/app/datasets 挂载")
                logger.error(f"[NE301]   3. Docker socket 权限是否正常（/var/run/docker.sock）")
                logger.error(f"[NE301]   4. CONTAINER_NAME 环境变量是否正确（当前: {os.environ.get('CONTAINER_NAME', '未设置')}）")
        
        # 挂载主机路径到容器的 /workspace/ne301
        if mount_path:
            logger.info(f"[NE301] ✓ 最终使用的挂载路径: {mount_path}")
            logger.info(f"[NE301] 将使用此路径挂载到 ne301-dev 容器: -v {mount_path}:/workspace/ne301")
            logger.info(f"[NE301] 注意：在容器内无法验证主机路径是否存在，这取决于 Docker 挂载的实际配置")
        else:
            logger.warning(f"[NE301] ⚠️ 未找到有效的主机路径，将使用容器内路径（可能导致 Docker-in-Docker 挂载失败）")
        docker_cmd.extend([
            "-v", f"{mount_path}:/workspace/ne301",
            "-w", "/workspace/ne301",  # 工作目录设为项目根目录
        ])
        
        # 在执行 Docker 命令前，验证文件是否存在于容器内（因为文件刚被复制到容器内的挂载点）
        # 文件复制到容器内的 /workspace/ne301，应该会立即同步到主机的挂载点
        # 但如果挂载的主机路径不同，可能需要等待同步或使用不同的策略
        if model_name:
            container_tflite = ne301_project_path / "Model" / "weights" / f"{model_name}.tflite"
            container_json = ne301_project_path / "Model" / "weights" / f"{model_name}.json"
            if not container_tflite.exists() or not container_json.exists():
                logger.error(f"[NE301] 错误：容器内路径中的模型文件不存在")
                logger.error(f"[NE301] 容器内路径: {container_tflite} (exists: {container_tflite.exists()})")
                logger.error(f"[NE301] 容器内路径: {container_json} (exists: {container_json.exists()})")
                logger.error(f"[NE301] 请确保在调用 build_ne301_model 之前已调用 copy_model_to_ne301_project")
                raise FileNotFoundError(
                    f"模型文件不存在于容器内路径: {container_tflite} 或 {container_json}"
                )
            logger.info(f"[NE301] 验证通过：文件存在于容器内路径")
            
            # 如果找到了主机路径，尝试检查主机路径的文件是否存在
            # 由于 bind mount，容器内路径和主机路径应该指向同一位置
            # 但如果文件不在主机路径，可能是文件系统同步延迟或路径不匹配
            # 注意：主机路径在容器内可能无法直接访问，所以这里只做尝试性验证
            if mount_path:
                # 验证路径格式（宽松模式，因为主机路径在容器内无法验证存在性）
                if not validate_mount_path(mount_path, strict=False):
                    logger.warning(f"[NE301] 警告：解析的主机路径格式无效: {mount_path}")
                    logger.warning(f"[NE301] 将继续尝试使用此路径进行 Docker 挂载")
                else:
                    logger.info(f"[NE301] 主机路径格式验证通过: {mount_path}")
                
                # 尝试访问主机路径（可能失败，但不影响 Docker 挂载）
                host_tflite = Path(mount_path) / "Model" / "weights" / f"{model_name}.tflite"
                host_json = Path(mount_path) / "Model" / "weights" / f"{model_name}.json"
                
                # 尝试确保主机路径目录存在（可能在容器内无法访问，但不影响 Docker 挂载）
                host_weights_dir = Path(mount_path) / "Model" / "weights"
                try:
                    if not host_weights_dir.exists():
                        logger.debug(f"[NE301] 主机路径目录在容器内不可见: {host_weights_dir}（这是正常的）")
                        # 尝试创建（可能失败，但不影响 Docker 挂载，因为文件已在容器内存在）
                        try:
                            host_weights_dir.mkdir(parents=True, exist_ok=True)
                            logger.debug(f"[NE301] 已创建主机路径目录（如果可访问）")
                        except Exception as e:
                            logger.debug(f"[NE301] 无法在容器内创建主机路径目录（这是正常的）: {e}")
                    else:
                        logger.debug(f"[NE301] 主机路径目录在容器内可见: {host_weights_dir}")
                except Exception as e:
                    logger.debug(f"[NE301] 无法访问主机路径目录（这是正常的，容器内可能无法直接访问主机路径）: {e}")
                
                # 尝试检查主机路径的文件是否存在（可能在容器内无法访问，这是正常的）
                # 由于 bind mount，容器内路径和主机路径应该指向同一位置
                # 如果文件不在主机路径可见，可能是 Docker Desktop 的文件系统隔离导致的
                try:
                    host_files_exist = host_tflite.exists() and host_json.exists()
                    logger.debug(f"[NE301] 主机路径文件检查: TFLite={host_tflite.exists()}, JSON={host_json.exists()}")
                except Exception as e:
                    logger.debug(f"[NE301] 无法在容器内访问主机路径文件（这是正常的）: {e}")
                    host_files_exist = False
                
                if not host_files_exist:
                    logger.info(f"[NE301] 主机路径文件在容器内不可见（这是正常的），将依赖 bind mount 和 Docker 挂载")
                    logger.info(f"[NE301]   容器内路径: {container_tflite} (exists: {container_tflite.exists()})")
                    logger.info(f"[NE301]   容器内路径: {container_json} (exists: {container_json.exists()})")
                    logger.info(f"[NE301]   主机路径（推断）: {mount_path}")
                    logger.info(f"[NE301]   注意：文件已存在于容器内的挂载点，Docker 会将它们挂载到 ne301-dev 容器")
                else:
                    logger.info(f"[NE301] ✓ 主机路径文件在容器内可见: {mount_path}")
                    logger.info(f"[NE301]   - {host_tflite}")
                    logger.info(f"[NE301]   - {host_json}")
            elif mount_path:
                # 路径已解析，但可能格式验证失败（不应该发生，因为已使用宽松模式）
                logger.warning(f"[NE301] 警告：路径格式验证失败: {mount_path}")
                logger.warning(f"[NE301] 将继续尝试使用此路径进行 Docker 挂载")
            else:
                # 路径解析失败
                logger.warning(f"[NE301] 警告：无法解析主机路径，将使用容器内路径（可能失败）")
    else:
        # 不在容器内：直接使用本地路径
        docker_cmd.extend([
            "-v", f"{ne301_project_path}:/workspace/ne301",
            "-w", "/workspace/ne301",  # 工作目录设为项目根目录
        ])
        
        # 不在容器内时，也验证文件存在
        if model_name:
            local_tflite = ne301_project_path / "Model" / "weights" / f"{model_name}.tflite"
            local_json = ne301_project_path / "Model" / "weights" / f"{model_name}.json"
            if not local_tflite.exists() or not local_json.exists():
                raise FileNotFoundError(
                    f"模型文件不存在: {local_tflite} 或 {local_json}"
                )
            logger.info(f"[NE301] 验证通过：文件存在于本地路径")
    
    # make model 需要在项目根目录执行
    # 使用 bash -c 来执行命令，容器的入口脚本会在执行命令时提供必要的环境变量
    # 先验证文件存在，然后再执行编译
    if model_name:
        # 改进验证命令：分别检查 .tflite 和 .json 文件，并列出目录内容用于调试
        verify_cmd = (
            f"cd /workspace/ne301 && "
            f"echo '[DEBUG] ========================================' && "
            f"echo '[DEBUG] NE301 Docker Build Debug Info' && "
            f"echo '[DEBUG] ========================================' && "
            f"echo '[DEBUG] Current directory:' && pwd && "
            f"echo '[DEBUG] Model name: {model_name}' && "
            f"echo '[DEBUG] Expected files:' && "
            f"echo '[DEBUG]   - Model/weights/{model_name}.tflite' && "
            f"echo '[DEBUG]   - Model/weights/{model_name}.json' && "
            f"echo '[DEBUG] Listing Model/weights/ directory contents:' && "
            f"ls -la Model/weights/ 2>&1 || echo '[WARN] Model/weights/ directory does not exist' && "
            f"echo '[DEBUG] Checking for specific files:' && "
            f"([ -f Model/weights/{model_name}.tflite ] && echo '[OK] TFLite file found' || echo '[ERROR] TFLite file NOT found') && "
            f"([ -f Model/weights/{model_name}.json ] && echo '[OK] JSON file found' || echo '[ERROR] JSON file NOT found') && "
            f"if [ ! -f Model/weights/{model_name}.tflite ] || [ ! -f Model/weights/{model_name}.json ]; then "
            f"  echo '[ERROR] Model files missing! Aborting build.' && "
            f"  exit 1; "
            f"fi && "
            f"echo '[DEBUG] All files found, proceeding with build...' && "
            f"make model"
        )
        debug_info = f"Expected model files in container: /workspace/ne301/Model/weights/{model_name}.tflite and .json"
    else:
        verify_cmd = (
            f"cd /workspace/ne301 && "
            f"echo '[DEBUG] Checking for any TFLite files...' && "
            f"ls -la Model/weights/*.tflite Model/weights/*.json 2>&1 && "
            f"if ! ls Model/weights/*.tflite 2>/dev/null || ! ls Model/weights/*.json 2>/dev/null; then "
            f"  echo '[ERROR] No model files found!' && "
            f"  echo '[DEBUG] Listing Model/weights/ directory:' && "
            f"  ls -la Model/weights/ 2>&1 && "
            f"  exit 1; "
            f"fi && "
            f"echo '[DEBUG] Model files found, proceeding with build...' && "
            f"make model"
        )
        debug_info = "Expected model files in container: /workspace/ne301/Model/weights/*.tflite and *.json"
    
    docker_cmd.extend([
        docker_image,
        "bash", "-c", verify_cmd
    ])
    
    logger.info(f"Running Docker build command: {' '.join(docker_cmd[:10])}...")  # 只显示前10个参数，避免日志过长
    logger.info(f"[NE301] Docker 挂载路径: {mount_path if is_in_container else ne301_project_path}")
    logger.info(f"[NE301] 模型名称: {model_name}")
    logger.info(debug_info)
    
    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=600  # 10 分钟超时
        )
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout
            
            # 过滤掉入口脚本的帮助信息，只提取真正的错误
            error_lines = error_msg.split('\n')
            real_errors = []
            skip_help = False
            for line in error_lines:
                # 如果遇到 "No rule to make target" 或 "Error" 或 "Stop" 等关键字，说明是真实错误
                if any(keyword in line for keyword in ["No rule to make target", "Error", "Stop", "make:", "***"]):
                    skip_help = True
                if skip_help or not any(keyword in line for keyword in ["=========================================", "Available Commands", "make model", "✓"]):
                    if line.strip() and not line.startswith('\x1b'):  # 排除 ANSI 颜色代码行和空行
                        real_errors.append(line)
            
            error_msg_clean = '\n'.join(real_errors) if real_errors else error_msg
            
            # 如果是挂载路径问题，提供更友好的错误信息
            if "Mounts denied" in error_msg or "not shared from the host" in error_msg:
                logger.error("Docker 挂载路径失败（Docker-in-Docker 限制）")
                logger.error("建议解决方案：")
                logger.error("1. 在 docker-compose.yml 中挂载 ne301 目录到主机")
                logger.error("2. 或配置 Docker Desktop 文件共享包含容器路径")
                logger.error(f"3. 或使用主机路径: 取消注释 docker-compose.yml 中的 ./ne301:/app/ne301")
            
            logger.error(f"Docker build failed: {error_msg_clean}")
            logger.error(f"Full stdout: {result.stdout[:1000]}...")  # 只显示前1000字符
            raise RuntimeError(f"NE301 模型编译失败: {error_msg_clean}")
        
        logger.info("Docker build completed successfully")
        
        # 检查生成的文件（可能在项目根目录的 build 目录或 Model/build 目录）
        possible_paths = [
            ne301_project_path / "build" / "ne301_Model.bin",
            ne301_project_path / "Model" / "build" / "ne301_Model.bin",
            ne301_project_path / "build" / "Model.bin",
        ]
        
        model_bin = None
        for path in possible_paths:
            if path.exists():
                model_bin = path
                logger.info(f"Model package generated: {model_bin}")
                break
        
        if not model_bin:
            logger.warning(f"编译完成但未找到模型包，检查了以下路径:")
            for path in possible_paths:
                logger.warning(f"  - {path} (exists: {path.exists()})")
            # 列出 build 目录内容以便调试
            build_dirs = [
                ne301_project_path / "build",
                ne301_project_path / "Model" / "build",
            ]
            for build_dir in build_dirs:
                if build_dir.exists():
                    logger.info(f"Build directory contents ({build_dir}):")
                    try:
                        for item in build_dir.iterdir():
                            logger.info(f"  - {item.name} ({'file' if item.is_file() else 'dir'})")
                    except Exception as e:
                        logger.warning(f"  Error listing contents: {e}")
            return None
        
        return model_bin
            
    except subprocess.TimeoutExpired:
        logger.error("Docker build 超时")
        raise RuntimeError("NE301 模型编译超时（超过 10 分钟）")
    except Exception as e:
        logger.error(f"Docker build 异常: {e}")
        raise


def _build_local(ne301_project_path: Path) -> Optional[Path]:
    """本地编译（需要安装 NE301 开发环境）"""
    ne301_project_path = Path(ne301_project_path).resolve()
    
    # 在项目目录执行 make model
    cmd = ["make", "model"]
    
    logger.info(f"Running local build: {' '.join(cmd)} in {ne301_project_path}")
    
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ne301_project_path),
            capture_output=True,
            text=True,
            timeout=600
        )
        
        if result.returncode != 0:
            logger.error(f"Local build failed: {result.stderr}")
            raise RuntimeError(f"NE301 模型编译失败: {result.stderr}")
        
        # 检查生成的文件
        model_bin = ne301_project_path / "build" / "ne301_Model.bin"
        if model_bin.exists():
            return model_bin
        else:
            logger.warning(f"编译完成但未找到模型包: {model_bin}")
            return None
            
    except FileNotFoundError:
        raise RuntimeError("本地未找到 make 命令，请安装 NE301 开发环境或使用 Docker 模式")
    except subprocess.TimeoutExpired:
        raise RuntimeError("NE301 模型编译超时（超过 10 分钟）")
    except Exception as e:
        logger.error(f"Local build 异常: {e}")
        raise
