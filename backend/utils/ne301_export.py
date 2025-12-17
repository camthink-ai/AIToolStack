"""
NE301 model export tool
Used to generate model packages compatible with NE301 devices
"""
import json
import logging
import subprocess
import shutil
import time
import threading
import queue
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
    Recursively convert NumPy types in dictionaries and lists to Python native types
    Ensure objects can be JSON serialized
    Compatible with NumPy 1.x and 2.x
    """
    import numpy as np
    
    if isinstance(obj, dict):
        return {key: _convert_to_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_convert_to_json_serializable(item) for item in obj]
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    
    # Use safer way to check NumPy types (compatible with NumPy 1.x and 2.x)
    # First check if it's a NumPy base type (without accessing potentially non-existent attributes)
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
    
    # If isinstance check fails, try to determine by type name
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
    Extract quantization parameters and output dimensions from TFLite model
    
    Returns:
        Tuple of (output_scale, output_zero_point, output_shape)
        Returns (None, None, None) if extraction fails
        All return values are converted to Python native types (JSON serializable)
    """
    if not TENSORFLOW_AVAILABLE:
        logger.warning("TensorFlow not available, cannot extract quantization parameters from TFLite model")
        return None, None, None
    
    try:
        # Load TFLite model
        interpreter = tf.lite.Interpreter(model_path=str(tflite_path))
        interpreter.allocate_tensors()
        
        # Get output tensor details
        output_details = interpreter.get_output_details()[0]  # Assume only one output
        output_shape = output_details['shape']  # e.g., [1, 84, 1344]
        
        # Convert output_shape to Python native types (handle NumPy int64/int32)
        if output_shape is not None:
            output_shape = tuple(int(x) for x in output_shape)
        
        # Extract quantization parameters
        if 'quantization_parameters' in output_details:
            quant_params = output_details['quantization_parameters']
            
            # Extract scale and zero_point, and convert to Python native types
            if quant_params.get('scales') and len(quant_params['scales']) > 0:
                scale_val = quant_params['scales'][0]
                # Convert NumPy float type to Python float
                output_scale = float(scale_val) if scale_val is not None else None
            else:
                output_scale = None
            
            if quant_params.get('zero_points') and len(quant_params['zero_points']) > 0:
                zp_val = quant_params['zero_points'][0]
                # Convert NumPy int type to Python int
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
    Generate NE301 JSON configuration file
    
    Args:
        tflite_path: TFLite model file path
        model_name: Model name (without extension)
        input_size: Model input size (e.g., 256)
        num_classes: Number of classes
        class_names: List of class names
        output_scale: Output quantization scale (if None, will try to extract from TFLite model)
        output_zero_point: Output quantization zero_point (if None, will try to extract from TFLite model)
        confidence_threshold: Confidence threshold
        iou_threshold: IoU threshold
        max_detections: Maximum number of detections
        total_boxes: Total number of boxes (if None, will estimate from output_shape or based on input_size)
        output_shape: Model output shape (batch, height, width), if None will try to extract from TFLite model
    
    Returns:
        JSON configuration dictionary
    """
    # Try to extract quantization parameters and output dimensions from TFLite model
    if output_scale is None or output_zero_point is None or output_shape is None:
        extracted_scale, extracted_zero_point, extracted_shape = extract_tflite_quantization_params(tflite_path)
        if extracted_scale is not None:
            output_scale = extracted_scale
        if extracted_zero_point is not None:
            output_zero_point = extracted_zero_point
        if extracted_shape is not None:
            output_shape = extracted_shape
    
    # Use default values (if extraction fails)
    if output_scale is None:
        output_scale = 0.003921568859368563  # Default uint8->int8 scale (1/255)
    if output_zero_point is None:
        output_zero_point = -128  # Default int8 zero_point
    
    # Extract output height and width from output_shape
    if output_shape is not None:
        # output_shape format: (batch, height, width) e.g., (1, 84, 1344)
        output_height = output_shape[1] if len(output_shape) > 1 else (4 + num_classes)
        output_width = output_shape[2] if len(output_shape) > 2 else None
        if output_width is not None and total_boxes is None:
            total_boxes = output_width
    else:
        output_height = 4 + num_classes  # Default: 4 (bbox) + num_classes
    
    # Calculate YOLOv8 output dimensions
    # YOLOv8 256x256: output is (1, 84, 1344) or similar
    # 84 = 4 (bbox) + 80 (classes)
    # 1344 = 3 scales * (32*32 + 16*16 + 8*8) = 3 * 448 = 1344
    if total_boxes is None:
        # Estimate based on input_size
        # YOLOv8 has different grid counts at different input sizes
        if input_size == 256:
            total_boxes = 1344  # 3 * (32*32 + 16*16 + 8*8) = 3 * 448
        elif input_size == 320:
            total_boxes = 2100  # 3 * (40*40 + 20*20 + 10*10) = 3 * 700
        elif input_size == 416:
            total_boxes = 3549  # 3 * (52*52 + 26*26 + 13*13) = 3 * 1183
        elif input_size == 640:
            total_boxes = 8400  # 3 * (80*80 + 40*40 + 20*20) = 3 * 2800
        else:
            # Default estimation
            scale = input_size // 8
            total_boxes = 3 * (scale * scale + (scale // 2) ** 2 + (scale // 4) ** 2)
    
    # If extracted output height differs from calculated, use extracted value
    # But ensure it's at least 4 + num_classes (bbox + classes)
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
            "author": "CamThink AI Tool Stack"
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
        "postprocess_type": "pp_od_yolo_v8_ui",  # uint8 input, int8 output (recommended)
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
    
    # Ensure all values are JSON serializable (convert NumPy types)
    config = _convert_to_json_serializable(config)
    
    return config


def copy_model_to_ne301_project(
    tflite_path: Path,
    json_config: Dict,
    ne301_project_path: Path,
    model_name: str
) -> Tuple[Path, Path]:
    """
    Copy model files and JSON config to NE301 project directory
    
    Args:
        tflite_path: TFLite model file path
        json_config: JSON configuration dictionary
        ne301_project_path: NE301 project root directory
        model_name: Model name (without extension)
    
    Returns:
        Tuple of (tflite_dest_path, json_dest_path)
    """
    # Ensure Model/weights directory exists
    weights_dir = ne301_project_path / "Model" / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy TFLite file
    tflite_dest = weights_dir / f"{model_name}.tflite"
    shutil.copy2(tflite_path, tflite_dest)
    logger.info(f"Copied TFLite model to {tflite_dest}")
    
    # Save JSON config (ensure all values are serializable)
    json_dest = weights_dir / f"{model_name}.json"
    # Ensure config is serializable again (double check)
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
    Compile model using NE301 development environment
    
    Args:
        ne301_project_path: NE301 project root directory
        model_name: Model name (for updating MODEL_NAME in Model/Makefile)
        docker_image: Docker image name
        use_docker: Whether to use Docker (otherwise requires local NE301 development environment)
    
    Returns:
        Path to compiled and packaged device update package (build/ne301_Model_v*_pkg.bin), returns original .bin file path if packaging fails, returns None on failure
    """
    ne301_project_path = Path(ne301_project_path).resolve()
    
    # Check NE301 project directory
    if not (ne301_project_path / "Model").exists():
        raise FileNotFoundError(f"NE301 project directory does not exist or Model directory is missing: {ne301_project_path}")
    
    if not (ne301_project_path / "Makefile").exists():
        raise FileNotFoundError(f"NE301 project root directory missing Makefile: {ne301_project_path}")
    
    # Verify model files exist (before updating Makefile)
    weights_dir = ne301_project_path / "Model" / "weights"
    expected_tflite = weights_dir / f"{model_name}.tflite"
    expected_json = weights_dir / f"{model_name}.json"
    
    if not expected_tflite.exists():
        raise FileNotFoundError(
            f"Model file does not exist: {expected_tflite}\n"
            f"Please ensure copy_model_to_ne301_project() is called before build_ne301_model()"
        )
    if not expected_json.exists():
        raise FileNotFoundError(
            f"JSON config file does not exist: {expected_json}\n"
            f"Please ensure copy_model_to_ne301_project() is called before build_ne301_model()"
        )
    logger.info(f"Verifying model files exist: {expected_tflite}, {expected_json}")
    
    # Update MODEL_NAME in Model/Makefile
    model_makefile = ne301_project_path / "Model" / "Makefile"
    if model_makefile.exists():
        try:
            content = model_makefile.read_text(encoding="utf-8")
            # Update MODEL_NAME
            lines = content.split("\n")
            updated = False
            for i, line in enumerate(lines):
                # Match MODEL_NAME = ... or MODEL_NAME=... (allow spaces)
                if line.strip().startswith("MODEL_NAME") and "=" in line:
                    # Extract comment (if any)
                    comment = ""
                    if "#" in line:
                        comment_part = line.split("#", 1)[1]
                        comment = f"  # {comment_part.strip()}"
                    lines[i] = f"MODEL_NAME = {model_name}{comment}"
                    updated = True
                    logger.info(f"Found and updating MODEL_NAME at line {i+1}: {line.strip()} -> MODEL_NAME = {model_name}")
                    break
            
            if not updated:
                # If not found, add after Model files section
                # Find position after "Model files" comment line
                insert_pos = 0
                for i, line in enumerate(lines):
                    if "Model files" in line or "# Model files" in line.lower():
                        # Find next empty line or related line and insert after
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
                    # If no suitable position found, add at file beginning
                    lines.insert(0, f"MODEL_NAME = {model_name}")
                logger.info(f"Added MODEL_NAME at line {insert_pos+1}")
            
            # Write file
            updated_content = "\n".join(lines)
            model_makefile.write_text(updated_content, encoding="utf-8")
            
            # Verify update succeeded
            verify_content = model_makefile.read_text(encoding="utf-8")
            if f"MODEL_NAME = {model_name}" in verify_content or f"MODEL_NAME={model_name}" in verify_content:
                logger.info(f"Successfully updated MODEL_NAME to '{model_name}' in {model_makefile}")
            else:
                logger.warning(f"Updated Makefile but verification failed. Please check {model_makefile}")
                # Try reading again, show actual content for debugging
                actual_lines = verify_content.split('\n')
                for i, line in enumerate(actual_lines[:30], 1):  # Only show first 30 lines
                    if 'MODEL_NAME' in line:
                        logger.warning(f"  Line {i}: {line}")
                
        except Exception as e:
            logger.error(f"Failed to update Model/Makefile: {e}", exc_info=True)
            raise
    else:
        logger.error(f"Model/Makefile does not exist: {model_makefile}")
        raise FileNotFoundError(f"Model/Makefile does not exist: {model_makefile}")
    
    if use_docker:
        # Use Docker to compile
        return _build_with_docker(ne301_project_path, docker_image, model_name)
    else:
        # Local compilation (requires NE301 development environment installed)
        return _build_local(ne301_project_path)


def _build_with_docker(
    ne301_project_path: Path,
    docker_image: str = "camthink/ne301-dev:latest",
    model_name: Optional[str] = None
) -> Optional[Path]:
    """Compile model using Docker container"""
    ne301_project_path = Path(ne301_project_path).resolve()
    
    # If model_name not provided, try to read from Makefile
    if model_name is None:
        model_makefile = ne301_project_path / "Model" / "Makefile"
        if model_makefile.exists():
            try:
                content = model_makefile.read_text(encoding="utf-8")
                for line in content.split("\n"):
                    if line.strip().startswith("MODEL_NAME") and "=" in line:
                        # Extract MODEL_NAME value
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            model_name = parts[1].strip().split("#")[0].strip()  # Remove comment
                            logger.info(f"Read MODEL_NAME from Makefile: {model_name}")
                            break
            except Exception as e:
                logger.warning(f"Failed to read MODEL_NAME from Makefile: {e}")
    
    # If still no model_name, use wildcard
    model_name_pattern = model_name if model_name else "*"
    
    # Detect system architecture
    import platform
    machine = platform.machine().lower()
    is_arm64 = machine in ('arm64', 'aarch64')
    
    # Check if Docker image exists
    check_cmd = ["docker", "images", "-q", docker_image]
    result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
    if not result.stdout.strip():
        logger.warning(f"Docker image {docker_image} does not exist, attempting to pull...")
        pull_cmd = ["docker", "pull"]
        if is_arm64:
            # ARM64 architecture needs to pull AMD64 image (using --platform)
            pull_cmd.extend(["--platform", "linux/amd64"])
        pull_cmd.append(docker_image)
        pull_result = subprocess.run(pull_cmd, capture_output=True, text=True, timeout=120)
        if pull_result.returncode != 0:
            raise RuntimeError(f"Failed to pull Docker image {docker_image}: {pull_result.stderr}")
    
    # Build Docker command
    # Problem: In Docker-in-Docker scenario, paths inside container cannot be directly mounted to another container
    # Solution: Use Docker volume or check if there's host path mapping
    docker_cmd = [
        "docker", "run", "--rm",
    ]
    
    # If ARM64 architecture, add platform parameter
    if is_arm64:
        docker_cmd.extend(["--platform", "linux/amd64"])
    
    # Check if running inside container
    is_in_container = Path("/.dockerenv").exists() or os.environ.get("container") == "docker"
    
    if is_in_container:
        # Running Docker-in-Docker inside container
        # Docker Desktop limitation: paths inside container cannot be directly mounted to another container
        # Solution: Get corresponding host path
        
        logger.info(f"[NE301] Detected running inside container, starting automatic host path detection...")
        
        # Check if workspace mount exists (/workspace/ne301)
        workspace_path = Path("/workspace/ne301")
        mount_path = None
        
        logger.info(f"[NE301] Checking container path: {workspace_path} (exists: {workspace_path.exists()}, is_dir: {workspace_path.is_dir() if workspace_path.exists() else 'N/A'})")
        
        def validate_mount_path(path, strict=True):
            """Validate if mount path is valid
            Args:
                path: Path to validate
                strict: If True, check if path exists; if False, only check format
            """
            if not path:
                return False
            try:
                p = Path(path)
                if strict:
                    # Strict mode: check if path exists and contains Model directory (NE301 project feature)
                    return p.exists() and (p / "Model").exists()
                else:
                    # Lenient mode: only check path format (host path cannot be verified inside container)
                    return len(str(path)) > 0 and "/" in str(path)
            except Exception:
                return False
        
        if workspace_path.exists() and workspace_path.is_dir():
            # Prefer getting host path via Docker inspect (most reliable)
            all_mounts = None
            try:
                # Get container name from environment variable, or use default name
                # Prefer CONTAINER_NAME, then HOSTNAME, finally default value
                container_names = [
                    os.environ.get("CONTAINER_NAME"),
                    os.environ.get("HOSTNAME"),
                    "aitoolstack"
                ]
                # Filter out None values
                container_names = [name for name in container_names if name]
                
                logger.info(f"[NE301] Attempting to get host path via Docker inspect, container name candidates: {container_names}")
                
                for name in container_names:
                    if not name:
                        continue
                    try:
                        inspect_cmd = ["docker", "inspect", name, "--format", "{{json .Mounts}}"]
                        logger.debug(f"[NE301] Executing command: {' '.join(inspect_cmd)}")
                        result = subprocess.run(inspect_cmd, capture_output=True, text=True, timeout=5)
                        logger.debug(f"[NE301] Docker inspect return code: {result.returncode}")
                        if result.returncode == 0:
                            all_mounts = json.loads(result.stdout)
                            logger.info(f"[NE301] Found {len(all_mounts)} mount points")
                            
                            # Output all mount point information (for debugging)
                            logger.info(f"[NE301] All mount point information:")
                            for i, mount in enumerate(all_mounts):
                                logger.info(f"[NE301]   #{i+1}: {mount.get('Source')} -> {mount.get('Destination')}")
                            
                            # First search for /workspace/ne301 mount point
                            for mount in all_mounts:
                                dest = mount.get("Destination")
                                src = mount.get("Source")
                                logger.debug(f"[NE301] Checking mount point: {src} -> {dest}")
                                if dest == "/workspace/ne301":
                                    candidate_path = src
                                    logger.info(f"[NE301] Found matching mount point: {candidate_path} -> /workspace/ne301")
                                    # When validating host path inside container, use lenient mode (only check format, not existence)
                                    if validate_mount_path(candidate_path, strict=False):
                                        mount_path = candidate_path
                                        logger.info(f"[NE301] ✓ Found valid host path from Docker inspect: {mount_path}")
                                        logger.info(f"[NE301]   Note: Host path cannot be directly verified inside container, will use it directly")
                                        break
                            if mount_path:
                                break
                        else:
                            logger.warning(f"[NE301] Docker inspect {name} failed: returncode={result.returncode}, stderr={result.stderr[:200]}")
                    except json.JSONDecodeError as e:
                        logger.warning(f"[NE301] Docker inspect {name} JSON parse failed: {e}, stdout={result.stdout[:200] if 'result' in locals() else 'N/A'}")
                        continue
                    except Exception as e:
                        logger.warning(f"[NE301] Docker inspect {name} execution exception: {type(e).__name__}: {e}")
                        continue
            except Exception as e:
                logger.warning(f"[NE301] Docker inspect process exception: {type(e).__name__}: {e}")
            
            # If ne301 mount point not found, try to infer project root directory from other mount points
            if not mount_path:
                if all_mounts:
                    logger.info(f"[NE301] Direct /workspace/ne301 mount point not found, attempting to infer from other mount points...")
                    try:
                        # Try to infer project root directory from datasets mount point (most reliable method)
                        # In docker-compose.yml: ./datasets:/app/datasets and ./ne301:/workspace/ne301
                        # If ./datasets host path is found, can infer ./ne301 path
                        datasets_host_path = None
                        for mount in all_mounts:
                            if mount.get("Destination") == "/app/datasets":
                                datasets_host_path = mount.get("Source")
                                logger.info(f"[NE301] Found datasets mount point: {datasets_host_path} -> /app/datasets")
                                break
                        
                        if datasets_host_path:
                            # datasets path is the datasets directory under project root directory
                            # If datasets_host_path = /path/to/project/datasets
                            # Then ne301_host_path should be /path/to/project/ne301
                            try:
                                datasets_path = Path(datasets_host_path)
                                # Verify datasets path format is correct (directory name should be datasets)
                                # Note: Cannot verify host path existence inside container, so only check format
                                if datasets_path.name == "datasets":
                                    inferred_ne301_path = datasets_path.parent / "ne301"
                                    # When validating inferred host path inside container, use lenient mode (only check format)
                                    if validate_mount_path(str(inferred_ne301_path), strict=False):
                                        mount_path = str(inferred_ne301_path)
                                        logger.info(f"[NE301] ✓ Inferred host path from datasets mount point: {mount_path}")
                                        logger.info(f"[NE301]   Project root directory: {datasets_path.parent}")
                                        logger.info(f"[NE301]   Inferred ne301 path: {inferred_ne301_path}")
                                        logger.info(f"[NE301]   Note: Path inference based on docker-compose.yml mount configuration")
                                        logger.info(f"[NE301]   Note: Host path cannot be directly verified inside container, will use it directly")
                                    else:
                                        logger.warning(f"[NE301] Inferred path format invalid: {inferred_ne301_path}")
                                else:
                                    logger.warning(f"[NE301] datasets path format abnormal (expected directory name 'datasets', actual '{datasets_path.name}')")
                                    logger.warning(f"[NE301] datasets full path: {datasets_host_path}")
                            except Exception as e:
                                logger.warning(f"[NE301] Failed to parse datasets path: {type(e).__name__}: {e}")
                        else:
                            logger.warning(f"[NE301] /app/datasets mount point not found, cannot infer ne301 path from datasets")
                    except Exception as e:
                        logger.warning(f"[NE301] Failed to infer path from mount points: {type(e).__name__}: {e}")
                else:
                    logger.warning(f"[NE301] Docker inspect failed to get mount point information (all_mounts is None), cannot infer path")
            
            # If Docker inspect fails, try to get from /proc/mounts (but needs verification)
            if not mount_path:
                try:
                    with open("/proc/mounts", "r") as f:
                        for line in f:
                            parts = line.split()
                            if len(parts) >= 2:
                                host_path = parts[0]  # Host path
                                container_path = parts[1]  # Container path
                                if container_path == "/workspace/ne301" or container_path == str(workspace_path):
                                    # Skip obviously wrong paths (Docker Desktop virtual paths)
                                    if "/run/host" in host_path or "/tmp" in host_path or not host_path.startswith("/"):
                                        logger.debug(f"[NE301] Skipping suspicious path: {host_path}")
                                        continue
                                    # Verify path format (use lenient mode, because host path cannot be verified inside container)
                                    if validate_mount_path(host_path, strict=False):
                                        mount_path = host_path
                                        logger.info(f"[NE301] Found valid host path from /proc/mounts: {mount_path}")
                                        logger.info(f"[NE301]   Note: Host path cannot be directly verified inside container, will use it directly")
                                        break
                except Exception as e:
                    logger.warning(f"[NE301] Failed to read /proc/mounts: {e}")
        
        # If still cannot get host path, try final fallback
        if not mount_path:
            # Final fallback: check environment variable (only as last resort, not recommended to set manually)
            env_path = os.environ.get("NE301_HOST_PATH")
            if env_path:
                env_path = env_path.strip()
                if env_path:
                    # Verify environment variable path format (lenient mode)
                    if validate_mount_path(env_path, strict=False):
                        mount_path = env_path
                        logger.warning(f"[NE301] ⚠️ Using environment variable NE301_HOST_PATH as fallback: {mount_path}")
                        logger.warning(f"[NE301] Recommendation: Check why auto-detection failed, environment variable should only be used as a temporary solution")
                    else:
                        logger.error(f"[NE301] Environment variable NE301_HOST_PATH format invalid: {env_path}")
            
            if not mount_path:
                # Final fallback: use container path (will fail, but error message will guide user)
                mount_path = str(workspace_path.resolve()) if workspace_path.exists() else str(ne301_project_path.resolve())
                logger.error(f"[NE301] ✗ Failed to automatically get host path!")
                logger.error(f"[NE301] Attempted methods:")
                logger.error(f"[NE301]   1. Docker inspect (search for /workspace/ne301 mount point)")
                logger.error(f"[NE301]   2. Infer from datasets mount point (infer project root from /app/datasets)")
                logger.error(f"[NE301]   3. /proc/mounts parsing")
                logger.error(f"[NE301]   4. Environment variable NE301_HOST_PATH (current value: {os.environ.get('NE301_HOST_PATH', 'not set')})")
                logger.error(f"[NE301]")
                logger.error(f"[NE301] Current fallback path (may be incorrect): {mount_path}")
                logger.error(f"[NE301] This will cause Docker-in-Docker mount to fail")
                logger.error(f"[NE301]")
                logger.error(f"[NE301] Temporary solution:")
                logger.error(f"[NE301]   Set environment variable NE301_HOST_PATH in docker-compose.yml")
                logger.error(f"[NE301]   Example: - NE301_HOST_PATH=/path/to/project/ne301")
                logger.error(f"[NE301]")
                logger.error(f"[NE301] Please check:")
                logger.error(f"[NE301]   1. Is ./ne301:/workspace/ne301 mount configured in docker-compose.yml")
                logger.error(f"[NE301]   2. Is ./datasets:/app/datasets mount configured in docker-compose.yml")
                logger.error(f"[NE301]   3. Is Docker socket permission normal (/var/run/docker.sock)")
                logger.error(f"[NE301]   4. Is CONTAINER_NAME environment variable correct (current: {os.environ.get('CONTAINER_NAME', 'not set')})")
        
        # Mount host path to container's /workspace/ne301
        if mount_path:
            logger.info(f"[NE301] ✓ Final mount path to use: {mount_path}")
            logger.info(f"[NE301] Will mount this path to ne301-dev container: -v {mount_path}:/workspace/ne301")
            logger.info(f"[NE301] Note: Cannot verify if host path exists inside container, depends on actual Docker mount configuration")
        else:
            logger.warning(f"[NE301] ⚠️ No valid host path found, will use container path (may cause Docker-in-Docker mount to fail)")
        docker_cmd.extend([
            "-v", f"{mount_path}:/workspace/ne301",
            "-w", "/workspace/ne301",  # Set working directory to project root
        ])
        
        # Before executing Docker command, verify files exist in container (because files were just copied to container mount point)
        # Files copied to container's /workspace/ne301 should immediately sync to host mount point
        # But if mounted host path is different, may need to wait for sync or use different strategy
        if model_name:
            container_tflite = ne301_project_path / "Model" / "weights" / f"{model_name}.tflite"
            container_json = ne301_project_path / "Model" / "weights" / f"{model_name}.json"
            if not container_tflite.exists() or not container_json.exists():
                logger.error(f"[NE301] Error: Model files do not exist in container path")
                logger.error(f"[NE301] Container path: {container_tflite} (exists: {container_tflite.exists()})")
                logger.error(f"[NE301] Container path: {container_json} (exists: {container_json.exists()})")
                logger.error(f"[NE301] Please ensure copy_model_to_ne301_project is called before build_ne301_model")
                raise FileNotFoundError(
                    f"Model files do not exist in container path: {container_tflite} or {container_json}"
                )
            logger.info(f"[NE301] Validation passed: Files exist in container path")
            
            # If host path found, try to check if files exist at host path
            # Due to bind mount, container path and host path should point to same location
            # But if files not at host path, may be filesystem sync delay or path mismatch
            # Note: Host path may not be directly accessible inside container, so only do tentative verification
            if mount_path:
                # Verify path format (lenient mode, because host path cannot be verified inside container)
                if not validate_mount_path(mount_path, strict=False):
                    logger.warning(f"[NE301] Warning: Parsed host path format invalid: {mount_path}")
                    logger.warning(f"[NE301] Will continue to try using this path for Docker mount")
                else:
                    logger.info(f"[NE301] Host path format validation passed: {mount_path}")
                
                # Try to access host path (may fail, but doesn't affect Docker mount)
                host_tflite = Path(mount_path) / "Model" / "weights" / f"{model_name}.tflite"
                host_json = Path(mount_path) / "Model" / "weights" / f"{model_name}.json"
                
                # Try to ensure host path directory exists (may not be accessible in container, but doesn't affect Docker mount)
                host_weights_dir = Path(mount_path) / "Model" / "weights"
                try:
                    if not host_weights_dir.exists():
                        logger.debug(f"[NE301] Host path directory not visible in container: {host_weights_dir} (this is normal)")
                        # Try to create (may fail, but doesn't affect Docker mount, because files already exist in container)
                        try:
                            host_weights_dir.mkdir(parents=True, exist_ok=True)
                            logger.debug(f"[NE301] Created host path directory (if accessible)")
                        except Exception as e:
                            logger.debug(f"[NE301] Cannot create host path directory in container (this is normal): {e}")
                    else:
                        logger.debug(f"[NE301] Host path directory visible in container: {host_weights_dir}")
                except Exception as e:
                    logger.debug(f"[NE301] Cannot access host path directory (this is normal, container may not directly access host path): {e}")
                
                # Try to check if files exist at host path (may not be accessible in container, this is normal)
                # Due to bind mount, container path and host path should point to same location
                # If files not visible at host path, may be due to Docker Desktop filesystem isolation
                try:
                    host_files_exist = host_tflite.exists() and host_json.exists()
                    logger.debug(f"[NE301] Host path file check: TFLite={host_tflite.exists()}, JSON={host_json.exists()}")
                except Exception as e:
                    logger.debug(f"[NE301] Cannot access host path files in container (this is normal): {e}")
                    host_files_exist = False
                
                if not host_files_exist:
                    logger.info(f"[NE301] Host path files not visible in container (this is normal), will rely on bind mount and Docker mount")
                    logger.info(f"[NE301]   Container path: {container_tflite} (exists: {container_tflite.exists()})")
                    logger.info(f"[NE301]   Container path: {container_json} (exists: {container_json.exists()})")
                    logger.info(f"[NE301]   Host path (inferred): {mount_path}")
                    logger.info(f"[NE301]   Note: Files already exist in container mount point, Docker will mount them to ne301-dev container")
                else:
                    logger.info(f"[NE301] ✓ Host path files visible in container: {mount_path}")
                    logger.info(f"[NE301]   - {host_tflite}")
                    logger.info(f"[NE301]   - {host_json}")
            elif mount_path:
                # Path parsed, but format validation may have failed (shouldn't happen, because lenient mode used)
                logger.warning(f"[NE301] Warning: Path format validation failed: {mount_path}")
                logger.warning(f"[NE301] Will continue to try using this path for Docker mount")
            else:
                # Path parsing failed
                logger.warning(f"[NE301] Warning: Failed to parse host path, will use container path (may fail)")
    else:
        # Not in container: use local path directly
        docker_cmd.extend([
            "-v", f"{ne301_project_path}:/workspace/ne301",
            "-w", "/workspace/ne301",  # Set working directory to project root
        ])
        
        # When not in container, also verify files exist
        if model_name:
            local_tflite = ne301_project_path / "Model" / "weights" / f"{model_name}.tflite"
            local_json = ne301_project_path / "Model" / "weights" / f"{model_name}.json"
            if not local_tflite.exists() or not local_json.exists():
                raise FileNotFoundError(
                    f"Model files do not exist: {local_tflite} or {local_json}"
                )
            logger.info(f"[NE301] Validation passed: Files exist in local path")
    
    # make model needs to be executed in project root directory
    # Use bash -c to execute command, container entry script will provide necessary environment variables when executing command
    # Verify files exist first, then execute compilation
    if model_name:
        # Improved verification command: separately check .tflite and .json files, and list directory contents for debugging
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
            f"make model && "
            f"echo '[NE301] Model build completed, creating update package...' && "
            f"make pkg-model && "
            f"echo '[NE301] Package created successfully'"
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
            f"make model && "
            f"echo '[NE301] Model build completed, creating update package...' && "
            f"make pkg-model && "
            f"echo '[NE301] Package created successfully'"
        )
        debug_info = "Expected model files in container: /workspace/ne301/Model/weights/*.tflite and *.json"
    
    docker_cmd.extend([
        docker_image,
        "bash", "-c", verify_cmd
    ])
    
    logger.info(f"Running Docker build command: {' '.join(docker_cmd[:10])}...")  # Only show first 10 parameters to avoid log being too long
    logger.info(f"[NE301] Docker mount path: {mount_path if is_in_container else ne301_project_path}")
    logger.info(f"[NE301] Model name: {model_name}")
    logger.info(debug_info)
    
    try:
        # Real-time output logs while capturing output
        result = subprocess.Popen(
            docker_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Real-time output and collect logs
        output_lines = []
        logger.info("[NE301] Starting compilation, real-time logs:")
        print("[NE301] Starting compilation, real-time logs:")
        
        # Use thread or direct read, set timeout
        output_queue = queue.Queue()
        def read_output():
            try:
                for line in result.stdout:
                    output_queue.put(line)
            except Exception as e:
                output_queue.put(f"[Error reading output] {e}")
            finally:
                output_queue.put(None)  # End marker
        
        reader_thread = threading.Thread(target=read_output, daemon=True)
        reader_thread.start()
        
        # Read output and print in real-time
        timeout = 600  # 10 minute timeout
        start_time = time.time()
        while True:
            try:
                # Check if timeout
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    result.terminate()
                    result.wait()
                    raise subprocess.TimeoutExpired(docker_cmd, timeout)
                
                # Non-blocking queue read
                try:
                    line = output_queue.get(timeout=1.0)
                    if line is None:
                        break
                    line = line.rstrip('\n\r')
                    if line.strip():
                        # Print to console in real-time
                        logger.info(f"[NE301 Build] {line}")
                        print(f"[NE301 Build] {line}")
                        output_lines.append(line)
                except queue.Empty:
                    # Check if process has ended
                    if result.poll() is not None:
                        # Process ended, read remaining output
                        remaining = result.stdout.read()
                        if remaining:
                            for line in remaining.splitlines():
                                line = line.rstrip('\n\r')
                                if line.strip():
                                    logger.info(f"[NE301 Build] {line}")
                                    print(f"[NE301 Build] {line}")
                                    output_lines.append(line)
                        break
                    continue
            except Exception as e:
                logger.error(f"[NE301] Error reading output: {e}")
                break
        
        # Wait for process to complete
        return_code = result.wait()
        
        # Build complete output
        full_output = '\n'.join(output_lines)
        
        # Create a subprocess.CompletedProcess-like object
        class CompletedProcess:
            def __init__(self, returncode, stdout, stderr=None):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr
        
        result = CompletedProcess(return_code, full_output, None)
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout
            
            # Filter out entry script help information, only extract real errors
            error_lines = error_msg.split('\n')
            real_errors = []
            skip_help = False
            for line in error_lines:
                # If encountering keywords like "No rule to make target" or "Error" or "Stop", it's a real error
                if any(keyword in line for keyword in ["No rule to make target", "Error", "Stop", "make:", "***"]):
                    skip_help = True
                if skip_help or not any(keyword in line for keyword in ["=========================================", "Available Commands", "make model", "✓"]):
                    if line.strip() and not line.startswith('\x1b'):  # Exclude ANSI color code lines and empty lines
                        real_errors.append(line)
            
            error_msg_clean = '\n'.join(real_errors) if real_errors else error_msg
            
            # If it's a mount path issue, provide more friendly error message
            if "Mounts denied" in error_msg or "not shared from the host" in error_msg:
                logger.error("Docker mount path failed (Docker-in-Docker limitation)")
                logger.error("Recommended solutions:")
                logger.error("1. Mount ne301 directory to host in docker-compose.yml")
                logger.error("2. Or configure Docker Desktop file sharing to include container path")
                logger.error(f"3. Or use host path: uncomment ./ne301:/app/ne301 in docker-compose.yml")
            
            logger.error(f"Docker build failed: {error_msg_clean}")
            logger.error(f"Full stdout: {result.stdout[:1000]}...")  # Only show first 1000 characters
            raise RuntimeError(f"NE301 model compilation failed: {error_msg_clean}")
        
        logger.info("Docker build and package completed successfully")
        print("[NE301] Build and package completed successfully")
        
        # First search for packaged files (device update package, format: *_v*_pkg.bin)
        build_dir = ne301_project_path / "build"
        pkg_files = []
        if build_dir.exists():
            # Find all _pkg.bin files (might be ne301_Model_v*_pkg.bin)
            pkg_files = list(build_dir.glob("*_pkg.bin"))
            # Sort by modification time, newest first
            pkg_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        
        # Prefer returning packaged files (device update package)
        model_bin = None
        if pkg_files:
            model_bin = pkg_files[0]
            logger.info(f"[NE301] Update package generated: {model_bin}")
            print(f"[NE301] Update package generated: {model_bin}")
        else:
            # If packaged file not found, try to find original .bin file
            logger.warning("[NE301] Update package not found, checking for raw .bin file...")
            print("[NE301] Update package not found, checking for raw .bin file...")
            possible_paths = [
                ne301_project_path / "build" / "ne301_Model.bin",
                ne301_project_path / "Model" / "build" / "ne301_Model.bin",
                ne301_project_path / "build" / "Model.bin",
            ]
            
            for path in possible_paths:
                if path.exists():
                    model_bin = path
                    logger.warning(f"[NE301] Found raw .bin file (packaging may have failed): {model_bin}")
                    print(f"[NE301] Found raw .bin file (packaging may have failed): {model_bin}")
                    break
        
        if not model_bin:
            logger.error("[NE301] Compilation completed but model package file not found")
            print("[NE301] Compilation completed but model package file not found")
            logger.error("[NE301] Checked the following paths:")
            print("[NE301] Checked the following paths:")
            # List build directory contents for debugging
            build_dirs = [
                ne301_project_path / "build",
                ne301_project_path / "Model" / "build",
            ]
            for build_dir in build_dirs:
                if build_dir.exists():
                    logger.error(f"[NE301] Build directory contents ({build_dir}):")
                    print(f"[NE301] Build directory contents ({build_dir}):")
                    try:
                        for item in build_dir.iterdir():
                            logger.error(f"[NE301]   - {item.name} ({'file' if item.is_file() else 'dir'})")
                            print(f"[NE301]   - {item.name} ({'file' if item.is_file() else 'dir'})")
                    except Exception as e:
                        logger.warning(f"[NE301]   Error listing contents: {e}")
            return None
        
        return model_bin
            
    except subprocess.TimeoutError:
        logger.error("[NE301] Docker build timeout")
        print("[NE301] Docker build timeout")
        raise RuntimeError("NE301 model compilation timeout (exceeded 10 minutes)")
    except Exception as e:
        logger.error(f"Docker build exception: {e}")
        raise


def _build_local(ne301_project_path: Path) -> Optional[Path]:
    """Local compilation (requires NE301 development environment installed)"""
    ne301_project_path = Path(ne301_project_path).resolve()
    
    # Execute make model in project directory, then make pkg-model
    logger.info(f"Running local build and package in {ne301_project_path}")
    print(f"[NE301] Running local build and package in {ne301_project_path}")

    try:
        # First execute make model
        cmd_build = ["make", "model"]
        logger.info(f"[NE301] Step 1/2: Building model...")
        print(f"[NE301] Step 1/2: Building model...")
        result_build = subprocess.run(
            cmd_build,
            cwd=str(ne301_project_path),
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )

        if result_build.returncode != 0:
            logger.error(f"[NE301] Model build failed: {result_build.stderr or result_build.stdout}")
            print(f"[NE301] Model build failed: {result_build.stderr or result_build.stdout}")
            raise RuntimeError(f"NE301 model compilation failed: {result_build.stderr or result_build.stdout}")

        # Then execute make pkg-model
        cmd_pkg = ["make", "pkg-model"]
        logger.info(f"[NE301] Step 2/2: Creating update package...")
        print(f"[NE301] Step 2/2: Creating update package...")
        result_pkg = subprocess.run(
            cmd_pkg,
            cwd=str(ne301_project_path),
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout (packaging should be fast)
        )

        if result_pkg.returncode != 0:
            logger.warning(f"[NE301] Package creation failed: {result_pkg.stderr or result_pkg.stdout}")
            print(f"[NE301] Package creation failed: {result_pkg.stderr or result_pkg.stdout}")
            logger.warning("[NE301] Will try to find raw .bin file instead")
            print("[NE301] Will try to find raw .bin file instead")

        # Prefer searching for packaged files (device update package)
        build_dir = ne301_project_path / "build"
        pkg_files = []
        if build_dir.exists():
            # Find all _pkg.bin files
            pkg_files = list(build_dir.glob("*_pkg.bin"))
            # Sort by modification time, newest first
            pkg_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        
        if pkg_files:
            model_bin = pkg_files[0]
            logger.info(f"[NE301] Update package generated: {model_bin}")
            print(f"[NE301] Update package generated: {model_bin}")
            return model_bin
        
        # If packaged file not found, try to find original .bin file
        model_bin = ne301_project_path / "build" / "ne301_Model.bin"
        if model_bin.exists():
            logger.warning(f"[NE301] Found raw .bin file (packaging may have failed): {model_bin}")
            print(f"[NE301] Found raw .bin file (packaging may have failed): {model_bin}")
            return model_bin
        else:
            logger.error(f"[NE301] Compilation completed but model package file not found")
            print(f"[NE301] Compilation completed but model package file not found")
            return None
            
    except FileNotFoundError:
        raise RuntimeError("make command not found locally, please install NE301 development environment or use Docker mode")
    except subprocess.TimeoutExpired:
        raise RuntimeError("NE301 model compilation timeout (exceeded 10 minutes)")
    except Exception as e:
        logger.error(f"Local build exception: {e}")
        raise
