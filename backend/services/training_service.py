"""Model training service"""
import logging
import threading
import io
import os
import sys
import time
import shutil
import yaml
import json
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime

# IMPORTANT: Clear sys.argv to prevent Ultralytics from parsing uvicorn arguments
# This must be done BEFORE importing ultralytics
# The issue: when running under uvicorn, sys.argv contains uvicorn's CLI arguments
# (e.g., ['backend.main:app', '--host', '0.0.0.0', '--port', '8000'])
# Ultralytics tries to parse these as YOLO CLI arguments and fails
if len(sys.argv) > 1 and any('backend.main:app' in str(arg) or arg.startswith('--') for arg in sys.argv):
    # Save original argv for reference
    _original_argv = sys.argv.copy()
    # Clear to empty list (YOLO will use default empty argv)
    sys.argv = [sys.argv[0]]  # Keep only the program name

# Lazy import ultralytics to avoid requiring installation at module load time
try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

logger = logging.getLogger(__name__)

# Database
from backend.models.database import SessionLocal, TrainingRecord, TrainingLog
from backend.config import settings


class TrainingStallMonitor:
    def __init__(self, log_fn, on_stall_stop, base_timeout=120.0, max_stalls=3,
                 slow_ratio=5.0, warmup_ratio=5.0, slow_warn_seconds=60.0, time_fn=None):
        self.log_fn = log_fn
        self.on_stall_stop = on_stall_stop
        self.base_timeout = base_timeout
        self.max_stalls = max_stalls
        self.slow_ratio = slow_ratio
        self.warmup_ratio = warmup_ratio
        self.slow_warn_seconds = slow_warn_seconds
        self.time_fn = time_fn or time.monotonic
        self.batches_done = 0
        self.stall_count = 0
        self.stopped_by_stall = False
        self.last_completion = None
        self.last_duration = 0.0
        self.strike_base = None
        self._done = threading.Event()
        self._thread = None
        self._suspended_fn = lambda: False

    @property
    def watchdog_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def timeout(self):
        if self.batches_done < 2:
            return self.base_timeout * self.warmup_ratio
        return max(self.base_timeout, self.slow_ratio * self.last_duration)

    def record_batch(self):
        now = self.time_fn()
        if self.last_completion is not None:
            self.last_duration = now - self.last_completion
            if self.last_duration >= self.slow_warn_seconds:
                self.log_fn(f"WARNING: Slow training: batch {self.batches_done} took {self.last_duration:.0f}s")
        self.batches_done += 1
        self.last_completion = now
        self.strike_base = now
        self.stall_count = 0

    def check(self, suspended=False):
        now = self.time_fn()
        if suspended:
            self.strike_base = now
            return
        if self.strike_base is None or self.stopped_by_stall:
            return
        if now - self.strike_base < self.timeout():
            return
        self.strike_base = now
        self.stall_count += 1
        self.log_fn(
            f"WARNING: No batch completed for {now - self.last_completion:.0f}s "
            f"(stall #{self.stall_count}/{self.max_stalls}, last: batch {self.batches_done - 1})")
        if self.stall_count >= self.max_stalls:
            self.stopped_by_stall = True
            self.log_fn("CRITICAL: No batch progress after repeated stalls, attempting graceful stop")
            self.on_stall_stop()

    def reset(self):
        self.batches_done = 0
        self.stall_count = 0
        self.stopped_by_stall = False
        self.last_completion = None
        self.last_duration = 0.0
        self.strike_base = None

    def start_watchdog(self, check_interval=5.0, suspended_fn=None):
        if suspended_fn is not None:
            self._suspended_fn = suspended_fn
        self._done.clear()
        self._thread = threading.Thread(target=self._watch, args=(check_interval,),
                                        daemon=True, name="TrainingStallWatchdog")
        self._thread.start()

    def stop_watchdog(self):
        self._done.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _watch(self, check_interval):
        while not self._done.wait(check_interval):
            self.check(suspended=self._suspended_fn())


def _setup_pytorch_compatibility():
    """
    Setup PyTorch 2.6+ compatibility (fallback)
    
    Note: ultralytics 8.3.162+ has fixed PyTorch 2.6+ compatibility issues
    Latest version 8.3.229+ has been tested compatible with PyTorch 2.9
    This function is only a fallback, used only if issues persist after upgrade
    """
    try:
        import torch
        if not hasattr(torch.serialization, 'add_safe_globals'):
            return
        
        safe_globals = []
        
        # Add all ultralytics related classes
        try:
            from ultralytics.nn.tasks import (
                DetectionModel, SegmentationModel, 
                PoseModel, ClassificationModel
            )
            safe_globals.extend([DetectionModel, SegmentationModel, PoseModel, ClassificationModel])
        except ImportError:
            pass
        
        # Add all classes from ultralytics.nn.modules
        try:
            from ultralytics.nn import modules as ultralytics_modules
            for attr_name, attr_value in ultralytics_modules.__dict__.items():
                if (not attr_name.startswith('_') and 
                    isinstance(attr_value, type) and
                    hasattr(attr_value, '__module__') and
                    attr_value.__module__ == 'ultralytics.nn.modules'):
                    if attr_value not in safe_globals:
                        safe_globals.append(attr_value)
        except (ImportError, AttributeError):
            pass
        
        # Add PyTorch built-in classes
        try:
            import torch.nn.modules.container
            safe_globals.append(torch.nn.modules.container.Sequential)
        except (ImportError, AttributeError):
            pass
        
        if safe_globals:
            torch.serialization.add_safe_globals(safe_globals)
            logger.debug(f"[Training] Added {len(safe_globals)} classes to torch safe globals (fallback)")
    except ImportError:
        pass


class LogCapture:
    """Capture log output during training"""
    def __init__(self, training_id: str, project_id: str):
        self.training_id = training_id
        self.project_id = project_id
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        self.last_log_line = ''  # For deduplication
        import re
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        
    def write(self, message):
        """Capture standard output, only capture training-related logs"""
        # Write to original output first
        self.original_stdout.write(message)
        
        if message.strip():
            # Clean ANSI escape codes
            cleaned_message = self._strip_ansi_codes(message.rstrip('\n'))
            # Filter out empty lines and lines with only control characters
            if cleaned_message.strip():
                # Filter: only capture training-related logs
                if self._is_training_log(cleaned_message):
                    # Allow all progress updates through (batch numbers change frequently)
                    if any(indicator in cleaned_message for indicator in ['%', 'epoch', 'batch', 'G', 'MB']):
                        # Always allow training progress through without deduplication
                        training_service._add_log(self.training_id, self.project_id, cleaned_message)
                    else:
                        # Deduplication: skip if same as last log line (avoid duplicate other messages)
                        cleaned_stripped = cleaned_message.strip()
                        if cleaned_stripped != self.last_log_line:
                            self.last_log_line = cleaned_stripped
                            training_service._add_log(self.training_id, self.project_id, cleaned_message)
    
    def _is_training_log(self, message: str) -> bool:
        """Determine if message is training-related log"""
        # Convert to lowercase for matching
        msg_lower = message.lower()
        
        # Explicitly ignore HTTP request/response logs
        if message.startswith('[Request]') or message.startswith('[Response]'):
            return False
        if '/api/' in msg_lower or ' /api' in msg_lower:
            return False

        # Training-related keywords
        training_keywords = [
            'epoch', 'train', 'val', 'loss', 'map', 'precision', 'recall',
            'fitness', 'yolo', 'ultralytics', 'class', 'box', 'cls', 'dfl',
            'speed', 'images', 'labels', 'model', 'dataset', 'training',
            'epochs', 'batch', 'imgsz', 'device', 'optimizer', 'lr0',
            'weight', 'classes', 'dataset', 'results', 'epochs', 'patience',
            'best', 'saved', 'results.csv', 'weights/', 'train_batch',
            'val_batch', 'plot', 'predict', 'confusion', 'matrix',
            # Chinese keywords (for compatibility)
            '训练', '验证', '轮次', '批次', '损失', '模型', '数据集',
            # Progress indicators
            'eta', 'time', 'memory', 'gpu', 'cpu'
        ]
        
        # Exclude unrelated log keywords (e.g., logs from other modules)
        exclude_keywords = [
            'mqtt', 'websocket', 'http', 'api', 'route', 'database',
            'sqlite', 'ne301', 'docker', 'mount', 'filesystem',
            'quantization', 'export', 'download', 'upload', 'annotation',
            'request', 'response',
            # FastAPI/Uvicorn related
            'uvicorn', 'started server', 'application startup',
            'info:', 'warning:', 'error:', 'debug:',
            # Exclude pure configuration information
            'config', 'settings', 'environment',
            # MQTT Broker related errors (aMQTT internal exceptions, should not appear in training logs)
            'brokerprotocolhandler', 'unhandled exception', 'reader coro',
            'timeouterror', 'timeout error'
        ]
        
        # Check exclude keywords (if contains exclude keywords and no training keywords, exclude)
        has_exclude = any(keyword in msg_lower for keyword in exclude_keywords)
        has_training = any(keyword in msg_lower for keyword in training_keywords)
        
        # If contains exclude keywords but no training keywords, not a training log
        if has_exclude and not has_training:
            return False
        
        # If contains training keywords, it's a training log
        if has_training:
            return True
        
        # If contains numbers and common training output formats (e.g., progress bars, percentages), might be training log
        import re
        # Match progress bars like "100%|████████████████| 100/100"
        if re.search(r'\d+%|█+|[\d/]+', message):
            # Further check if in training context
            if not has_exclude:
                return True
        
        # Default not capture (strict mode, only capture explicit training logs)
        return False
    
    def _strip_ansi_codes(self, text: str) -> str:
        """Remove ANSI escape codes"""
        # Remove ANSI escape sequences (colors, styles, cursor control, etc.)
        cleaned = self.ansi_escape.sub('', text)
        # Remove common control characters
        cleaned = cleaned.replace('\r', '')
        # Remove lines containing only whitespace
        cleaned = cleaned.strip()
        return cleaned
        
    def flush(self):
        self.original_stdout.flush()
        
    def __enter__(self):
        sys.stdout = self
        sys.stderr = self
        return self
        
    def __exit__(self, *args):
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr


class TrainingService:
    """Training service manager"""
    
    def __init__(self):
        # Support multiple training records: {project_id: [training_record1, training_record2, ...]}
        self.training_records: Dict[str, List[Dict]] = {}
        # Currently active trainings: {project_id: training_id}
        self.active_trainings: Dict[str, str] = {}
        # Training thread tracking: {training_id: thread}
        self.training_threads: Dict[str, threading.Thread] = {}
        # Stop signals for graceful interruption: {training_id: Event}
        self.stop_events: Dict[str, threading.Event] = {}
        # Pause control: {training_id: Condition}
        self.pause_conditions: Dict[str, threading.Condition] = {}
        # Pause state flags: {training_id: bool}
        self.paused_states: Dict[str, bool] = {}
        self.training_lock = threading.RLock()  # Use RLock to allow re-entrant locking
    
    # ========= Database related utility methods =========
    def _persist_record(self, record: Dict):
        """Write training record to database (insert or update)"""
        session = SessionLocal()
        try:
            db_obj = session.query(TrainingRecord).filter(
                TrainingRecord.training_id == record['training_id']
            ).first()
            if not db_obj:
                db_obj = TrainingRecord(
                    training_id=record['training_id'],
                    project_id=record['project_id'],
                )
                session.add(db_obj)
            
            # Sync fields
            db_obj.status = record.get('status')
            db_obj.start_time = datetime.fromisoformat(record['start_time']) if record.get('start_time') else None
            db_obj.end_time = datetime.fromisoformat(record['end_time']) if record.get('end_time') else None
            db_obj.model_size = record.get('model_size')
            db_obj.epochs = record.get('epochs')
            db_obj.imgsz = record.get('imgsz')
            db_obj.batch = record.get('batch')
            db_obj.device = record.get('device')
            metrics = record.get('metrics') or {}
            db_obj.metrics = json.dumps(metrics, ensure_ascii=False)
            db_obj.error = record.get('error')
            db_obj.model_path = record.get('model_path')
            db_obj.log_count = len(record.get('logs', []))
            session.commit()
        except Exception as e:
            session.rollback()
            logger.warning(f"[Training] Failed to persist training record {record.get('training_id')}: {e}")
        finally:
            session.close()

    def _update_db_fields(self, training_id: str, project_id: str, **fields):
        """Update specified fields of database training record"""
        session = SessionLocal()
        try:
            db_obj = session.query(TrainingRecord).filter(
                TrainingRecord.training_id == training_id,
                TrainingRecord.project_id == project_id
            ).first()
            if db_obj:
                for k, v in fields.items():
                    setattr(db_obj, k, v)
                session.commit()
        except Exception as e:
            session.rollback()
            logger.warning(f"[Training] Failed to update DB training record {training_id}: {e}")
        finally:
            session.close()

    def _get_log_count(self, project_id: str, training_id: str) -> int:
        """Get log count from memory"""
        with self.training_lock:
            if project_id in self.training_records:
                for record in self.training_records[project_id]:
                    if record.get('training_id') == training_id:
                        return len(record.get('logs', []))
        return 0

    def _get_db_logs(self, project_id: str, training_id: str, limit: int = 1000) -> List[str]:
        """Get logs from database (ascending by time, max limit entries)"""
        session = SessionLocal()
        try:
            rows = session.query(TrainingLog).filter(
                TrainingLog.project_id == project_id,
                TrainingLog.training_id == training_id
            ).order_by(TrainingLog.timestamp.asc()).limit(limit).all()
            return [f"[{r.timestamp.strftime('%H:%M:%S')}] {r.message}" if r.timestamp else r.message for r in rows]
        finally:
            session.close()
    
    def start_training(
        self,
        project_id: str,
        dataset_path: Path,
        model_type: str = 'yolov8',
        model_size: str = 'n',
        epochs: int = 100,
        imgsz: int = 640,
        batch: int = 16,  # Default: safe for both CPU and GPU
        device: Optional[str] = None,
        lr0: Optional[float] = None,
        lrf: Optional[float] = None,
        optimizer: Optional[str] = None,
        momentum: Optional[float] = None,
        weight_decay: Optional[float] = None,
        patience: Optional[int] = None,
        workers: Optional[int] = None,  # 优化: 将在下面自动设置
        val: Optional[bool] = None,
        save_period: Optional[int] = None,
        amp: Optional[bool] = None,
        hsv_h: Optional[float] = None,
        hsv_s: Optional[float] = None,
        hsv_v: Optional[float] = None,
        degrees: Optional[float] = None,
        translate: Optional[float] = None,
        scale: Optional[float] = None,
        shear: Optional[float] = None,
        perspective: Optional[float] = None,
        flipud: Optional[float] = None,
        fliplr: Optional[float] = None,
        mosaic: Optional[float] = None,
        mixup: Optional[float] = None,
        cache: Optional[str] = None,
    ) -> Dict:
        """
        Start training task
        
        Args:
            project_id: Project ID
            dataset_path: Dataset path (contains data.yaml)
            model_type: Model type ('yolov8', 'yolov11', 'yolov12', etc.)
            model_size: Model size ('n', 's', 'm', 'l', 'x')
            epochs: Number of training epochs
            imgsz: Image size
            batch: Batch size
            device: Device ('cpu', 'cuda', '0', '1', 'mps', ...)
            
        Returns:
            Training information dictionary
        """
        print(f"[TrainingService] start_training called for project {project_id}")
        print(f"[TrainingService] Dataset path: {dataset_path}")
        with self.training_lock:
            if project_id in self.active_trainings:
                error_msg = f"Training already in progress for project {project_id}"
                print(f"[TrainingService] ERROR: {error_msg}")
                raise ValueError(error_msg)
            
            # Check if dataset exists
            data_yaml = dataset_path / "data.yaml"
            print(f"[TrainingService] Checking data.yaml at {data_yaml}")
            if not data_yaml.exists():
                error_msg = f"data.yaml not found at {data_yaml}"
                print(f"[TrainingService] ERROR: {error_msg}")
                raise FileNotFoundError(error_msg)
            print(f"[TrainingService] data.yaml found")
            
            # Generate training record ID
            training_id = f"{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            print(f"[TrainingService] Generated training_id: {training_id}")
            
            # Create training information
            training_info = {
                'training_id': training_id,
                'project_id': project_id,
                'status': 'running',
                'start_time': datetime.now().isoformat(),
                'model_type': model_type,
                'model_size': model_size,
                'epochs': epochs,
                'imgsz': imgsz,
                'batch': batch,
                'device': device or 'cpu',
                'metrics': {},
                'logs': [],
                'error': None,
                'model_path': None
            }
            
            # Initialize training record list
            if project_id not in self.training_records:
                self.training_records[project_id] = []
            
            # Add to training record list (newest first)
            self.training_records[project_id].insert(0, training_info)
            self.active_trainings[project_id] = training_id
            # Prepare stop event for this training
            stop_event = threading.Event()
            self.stop_events[training_id] = stop_event
            # Prepare pause condition for this training
            pause_condition = threading.Condition()
            self.pause_conditions[training_id] = pause_condition
            self.paused_states[training_id] = False

            # Start training in background thread (pass training_id to generate unique directory name)
            print(f"[TrainingService] Creating training thread...")
            thread = threading.Thread(
                target=self._run_training,
                args=(training_id, project_id, dataset_path, training_info, model_type, model_size, epochs, imgsz, batch, device,
                      lr0, lrf, optimizer, momentum, weight_decay, patience, workers, val, save_period, amp,
                      hsv_h, hsv_s, hsv_v, degrees, translate, scale, shear, perspective, flipud, fliplr, mosaic, mixup, cache,
                      stop_event),
                daemon=True,
                name=f"TrainingThread-{training_id}"
            )
            # Save thread reference for tracking (already inside lock, so no need to acquire again)
            print(f"[TrainingService] Saving thread reference (already holding lock)...")
            self.training_threads[training_id] = thread
            print(f"[TrainingService] Thread reference saved")

        # Release lock before starting thread (thread.start() can be slow)
        print(f"[TrainingService] Lock released, starting training thread...")
        thread.start()

        # Persist to database in background (don't block response)
        print(f"[TrainingService] Scheduling database persist in background...")
        persist_thread = threading.Thread(
            target=self._persist_record,
            args=(training_info,),
            daemon=True,
            name=f"PersistThread-{training_id}"
        )
        persist_thread.start()

        print(f"[TrainingService] Training thread started, returning training_info")

        return training_info

    def _cleanup_training_state(self, training_id: str, project_id: str) -> None:
        """
        Cleanup training state, ensuring active_trainings, threads, and stop events are cleared.
        This is critical for preventing 'Training already in progress' errors.

        Args:
            training_id: Training ID to clean up
            project_id: Project ID associated with the training
        """
        try:
            with self.training_lock:
                # Clean up active_trainings if this training was marked as active
                # We check the training_id to avoid cleaning up a newer training
                if project_id in self.active_trainings and self.active_trainings[project_id] == training_id:
                    del self.active_trainings[project_id]
                    logger.info(f"[Training] Cleanup: Cleared active training for project {project_id}, training_id: {training_id}")

                # Clean up thread tracking
                if training_id in self.training_threads:
                    del self.training_threads[training_id]
                    logger.debug(f"[Training] Cleanup: Removed thread tracking for {training_id}")

                # Clean up stop events
                if training_id in self.stop_events:
                    del self.stop_events[training_id]
                    logger.debug(f"[Training] Cleanup: Removed stop event for {training_id}")

                # Clean up pause conditions
                if training_id in self.pause_conditions:
                    del self.pause_conditions[training_id]
                    logger.debug(f"[Training] Cleanup: Removed pause condition for {training_id}")

                # Clean up paused states
                if training_id in self.paused_states:
                    del self.paused_states[training_id]
                    logger.debug(f"[Training] Cleanup: Removed paused state for {training_id}")
        except Exception as cleanup_error:
            # Log but don't raise - cleanup errors shouldn't prevent other cleanup
            logger.error(f"[Training] Error during cleanup for training {training_id}: {cleanup_error}", exc_info=True)

    def _run_training(
        self,
        training_id: str,
        project_id: str,
        dataset_path: Path,
        training_info: Dict,
        model_type: str,
        model_size: str,
        epochs: int,
        imgsz: int,
        batch: int,
        device: Optional[str],
        lr0: Optional[float] = None,
        lrf: Optional[float] = None,
        optimizer: Optional[str] = None,
        momentum: Optional[float] = None,
        weight_decay: Optional[float] = None,
        patience: Optional[int] = None,
        workers: Optional[int] = None,
        val: Optional[bool] = None,
        save_period: Optional[int] = None,
        amp: Optional[bool] = None,
        hsv_h: Optional[float] = None,
        hsv_s: Optional[float] = None,
        hsv_v: Optional[float] = None,
        degrees: Optional[float] = None,
        translate: Optional[float] = None,
        scale: Optional[float] = None,
        shear: Optional[float] = None,
        perspective: Optional[float] = None,
        flipud: Optional[float] = None,
        fliplr: Optional[float] = None,
        mosaic: Optional[float] = None,
        mixup: Optional[float] = None,
        cache: Optional[str] = None,
        stop_event: Optional[threading.Event] = None,
        checkpoint_path: Optional[Path] = None,
        original_record: Optional[Dict] = None,
    ):
        """Run training in background thread

        Args:
            checkpoint_path: If provided, resume training from this checkpoint
            original_record: Original training record (used when resuming)
        """
        print(f"[TrainingThread-{training_id}] Thread started, beginning training process")

        time.sleep(0.1)

        record_for_db = None
        training_success = False
        stop_requested = False
        model = None  # Keep model reference for cleanup
        # Use shared stop event if not provided (backward safety)
        stop_event = stop_event or self.stop_events.get(training_id)

        try:
            if YOLO is None:
                raise ImportError("ultralytics library is not installed. Please install it with: pip install ultralytics")
            
            # Setup PyTorch 2.6+ compatibility (before loading model)
            _setup_pytorch_compatibility()
            
            # Read data.yaml to get class information
            data_yaml_path = dataset_path / "data.yaml"
            class_names = []
            num_classes = 0
            try:
                with open(data_yaml_path, 'r', encoding='utf-8') as f:
                    data_config = yaml.safe_load(f)
                    class_names = data_config.get('names', [])
                    num_classes = data_config.get('nc', 0)
            except Exception as e:
                self._add_log(training_id, project_id, f"Warning: Unable to read class information from data.yaml: {str(e)}")
            
            # Use log capture
            with LogCapture(training_id, project_id):
                # Add initial logs - training parameter information
                self._add_log(training_id, project_id, "=" * 60)
                self._add_log(training_id, project_id, "Training task started")
                self._add_log(training_id, project_id, "=" * 60)
                self._add_log(training_id, project_id, f"Model Type: {model_type}")
                self._add_log(training_id, project_id, f"Model: {model_type}{model_size}.pt")
                self._add_log(training_id, project_id, f"Epochs: {epochs}")
                self._add_log(training_id, project_id, f"Batch Size: {batch}")
                self._add_log(training_id, project_id, f"Image Size: {imgsz}")
                self._add_log(training_id, project_id, f"Device: {device or 'auto'}")
                self._add_log(training_id, project_id, f"Dataset path: {dataset_path}")
                self._add_log(training_id, project_id, "-" * 60)
                
                # Add class information
                if class_names:
                    self._add_log(training_id, project_id, f"Number of classes: {num_classes}")
                    self._add_log(training_id, project_id, "Class list:")
                    for idx, cls_name in enumerate(class_names):
                        self._add_log(training_id, project_id, f"  [{idx}] {cls_name}")
                else:
                    self._add_log(training_id, project_id, f"Number of classes: {num_classes}")
                self._add_log(training_id, project_id, "-" * 60)
                
                # Load pretrained model (ultralytics will handle download automatically)
                # Use model name (e.g., 'yolov8n', 'yolov11n') instead of filename,
                # so ultralytics will automatically download weights from GitHub
                model_name_str = f'{model_type}{model_size}'
                model_file_name = f'{model_type}{model_size}.pt'

                # If checkpoint_path is provided, resume from checkpoint
                if checkpoint_path and checkpoint_path.exists():
                    self._add_log(training_id, project_id, f"Resuming from checkpoint: {checkpoint_path}")
                    try:
                        model = YOLO(str(checkpoint_path))
                        self._add_log(training_id, project_id, "Checkpoint loaded successfully")
                        # For resumed training, model_type and model_size come from the checkpoint
                        # We don't need to re-download
                    except Exception as e:
                        error_msg = f"Failed to load checkpoint: {str(e)}"
                        self._add_log(training_id, project_id, error_msg)
                        raise FileNotFoundError(error_msg) from e
                else:
                    self._add_log(training_id, project_id, f"Loading pretrained model: {model_file_name}")

                    try:
                        # Use model name string, ultralytics will automatically download weights
                        model = YOLO(model_name_str)
                        self._add_log(training_id, project_id, "Model loaded successfully")
                    except Exception as e:
                        error_msg = str(e)
                        # If network-related error, provide friendly message
                        if any(keyword in error_msg.lower() for keyword in ['download', 'connection', 'ssl', 'url', 'network', 'timeout']):
                            download_url = f"https://github.com/ultralytics/assets/releases/download/v8.1.0/{model_file_name}"
                            home_dir = Path.home()
                            raise ConnectionError(
                                f"Unable to download pretrained model {model_file_name}.\n\n"
                                f"Solutions:\n"
                                f"1. Check network connection\n"
                                f"2. Manual download: {download_url}\n"
                                f"3. Place in one of the following locations:\n"
                                f"   - {Path.cwd() / model_file_name}\n"
                                f"   - {home_dir / '.ultralytics' / 'weights' / model_file_name}\n"
                                f"   - {home_dir / '.cache' / 'ultralytics' / model_file_name}\n"
                                f"4. Then use file path to load model\n"
                            ) from e
                        # If file not found error, provide same message (might be auto-download failure)
                        if 'FileNotFoundError' in str(type(e).__name__) or 'No such file' in error_msg:
                            download_url = f"https://github.com/ultralytics/assets/releases/download/v8.1.0/{model_file_name}"
                            home_dir = Path.home()
                            raise FileNotFoundError(
                                f"Pretrained model {model_file_name} not found and auto-download failed.\n\n"
                                f"Solutions:\n"
                                f"1. Check network connection\n"
                                f"2. Manual download: {download_url}\n"
                                f"3. Place in one of the following locations:\n"
                                f"   - {Path.cwd() / model_file_name}\n"
                                f"   - {home_dir / '.ultralytics' / 'weights' / model_file_name}\n"
                                f"   - {home_dir / '.cache' / 'ultralytics' / model_file_name}\n"
                            ) from e
                        raise
                
                if workers is None:
                    cpu_count = os.cpu_count() or 8

                    if device and 'cuda' in str(device).lower():
                        workers = min(cpu_count, 8)
                        self._add_log(training_id, project_id,
                            f"GPU training: configured {workers} workers (cores: {cpu_count})")
                    else:
                        self._add_log(training_id, project_id,
                            "CPU training: dataloader workers managed by ultralytics (0 on CPU)")

                # Prepare training parameters (following ultralytics documentation)
                # Use training_id to generate unique training directory name, avoid overwriting multiple trainings
                train_args = {
                    'data': str(dataset_path / "data.yaml"),
                    'epochs': epochs,
                    'imgsz': imgsz,
                    'batch': batch,
                    'device': device,
                    'project': str(dataset_path.parent),
                    'name': f'train_{training_id}',  # Use training_id to ensure each training has unique directory
                    'exist_ok': False,  # Changed to False, as each training should use new directory
                    # Note: 'timeout' is NOT a valid YOLO parameter, removed
                }
                
                # Add optional parameters (if provided)
                if lr0 is not None:
                    train_args['lr0'] = lr0
                if lrf is not None:
                    train_args['lrf'] = lrf
                if optimizer is not None:
                    train_args['optimizer'] = optimizer
                if momentum is not None:
                    train_args['momentum'] = momentum
                if weight_decay is not None:
                    train_args['weight_decay'] = weight_decay
                if patience is not None:
                    train_args['patience'] = patience
                    self._add_log(training_id, project_id, f"Early stopping patience: {patience} epochs")
                else:
                    self._add_log(training_id, project_id, "Early stopping patience: 100 (default)")
                if workers is not None:
                    train_args['workers'] = workers
                    if hasattr(model, "add_callback") and (not device or 'cpu' in str(device).lower()):
                        def _restore_cpu_workers(trainer):
                            trainer.args.workers = workers
                        model.add_callback("on_pretrain_routine_start", _restore_cpu_workers)
                        self._add_log(training_id, project_id,
                            f"CPU training: preserving explicit {workers} dataloader workers")
                if val is not None:
                    train_args['val'] = val
                    self._add_log(training_id, project_id, f"Validation: {'enabled' if val else 'disabled'}")
                if save_period is not None:
                    train_args['save_period'] = save_period
                if amp is not None:
                    train_args['amp'] = amp
                else:
                    # Disable AMP for CPU by default (not well supported)
                    if not device or 'cpu' in str(device).lower():
                        train_args['amp'] = False
                
                # Data augmentation parameters (if provided, otherwise use defaults)
                augment_info = {
                    "hsv_h": hsv_h if hsv_h is not None else 0.015,
                    "hsv_s": hsv_s if hsv_s is not None else 0.7,
                    "hsv_v": hsv_v if hsv_v is not None else 0.4,
                    "degrees": degrees if degrees is not None else 0.0,
                    "translate": translate if translate is not None else 0.1,
                    "scale": scale if scale is not None else 0.5,
                    "shear": shear if shear is not None else 0.0,
                    "perspective": perspective if perspective is not None else 0.0,
                    "flipud": flipud if flipud is not None else 0.0,
                    "fliplr": fliplr if fliplr is not None else 0.5,
                    "mosaic": mosaic if mosaic is not None else 1.0,
                    "mixup": mixup if mixup is not None else 0.0,
                }
                
                # Add data augmentation parameters to training arguments
                train_args.update(augment_info)

                # Handle cache parameter
                if cache is not None:
                    if cache.lower() in ('ram', 'disk'):
                        train_args['cache'] = cache.lower()
                        self._add_log(training_id, project_id, f"Dataset caching: {cache.lower()}")
                    else:
                        self._add_log(training_id, project_id,
                            f"Warning: Invalid cache value '{cache}', using default (no cache)")
                else:
                    self._add_log(training_id, project_id, "Dataset caching: disabled (default)")

                # CPU-specific optimizations
                if not device or 'cpu' in str(device).lower():
                    # Ensure AMP is disabled (not well supported on CPU)
                    train_args['amp'] = False
                    # Note: batch defaults to 16, which is safe for CPU
                    # For GPU training, consider increasing to 32-64 for better performance
                # Inject stop callbacks for graceful cancellation (use model.add_callback to avoid unsupported args)
                # Inject stop callbacks for graceful cancellation (use model.add_callback to avoid unsupported args)
                if stop_event and hasattr(model, "add_callback"):
                    stop_logged = {'flag': False}

                    def _handle_stop(trainer):
                        # Called inside training loop; stop ASAP (sets all known stop flags)
                        if stop_event.is_set():
                            # Different UL versions check different flags; set all to be safe
                            setattr(trainer, "stop", True)
                            setattr(trainer, "stop_training", True)
                            setattr(trainer, "stop_epoch", True)
                            if not stop_logged['flag']:
                                stop_logged['flag'] = True
                                self._add_log(training_id, project_id, "Stop requested, stopping after current batch...")

                    try:
                        model.add_callback("on_train_batch_end", _handle_stop)
                        model.add_callback("on_train_epoch_end", _handle_stop)
                    except Exception:
                        # Best-effort: if callback registration fails, continue without graceful stop
                        pass

                # Inject pause callbacks for pause/continue functionality
                pause_cond = self.pause_conditions.get(training_id)
                if pause_cond and hasattr(model, "add_callback"):
                    pause_logged = {'flag': False}

                    def _handle_pause(trainer):
                        # Called inside training loop; check if paused and wait
                        is_paused = self.paused_states.get(training_id, False)
                        if is_paused:
                            with pause_cond:
                                if not pause_logged['flag']:
                                    pause_logged['flag'] = True
                                    self._add_log(training_id, project_id, "Training paused...")
                                    # Update status to paused
                                    with self.training_lock:
                                        for record in self.training_records.get(project_id, []):
                                            if record.get('training_id') == training_id:
                                                record['status'] = 'paused'
                                                self._update_db_fields(training_id, project_id, status='paused')
                                                break
                                # Wait for continue signal, check every second for stop signal
                                while self.paused_states.get(training_id, False):
                                    pause_cond.wait(timeout=1.0)
                                    if stop_event and stop_event.is_set():
                                        break
                                pause_logged['flag'] = False
                                self._add_log(training_id, project_id, "Training resumed")
                                # Update status back to running
                                with self.training_lock:
                                    for record in self.training_records.get(project_id, []):
                                        if record.get('training_id') == training_id:
                                            record['status'] = 'running'
                                            self._update_db_fields(training_id, project_id, status='running')
                                            break

                    try:
                        model.add_callback("on_train_batch_end", _handle_pause)
                        model.add_callback("on_train_epoch_end", _handle_pause)
                    except Exception:
                        # Best-effort: if callback registration fails, continue without pause support
                        pass

                monitor = None
                trainer_ref = {}

                def _request_stall_stop():
                    trainer = trainer_ref.get('obj')
                    if trainer is not None:
                        trainer.stop = True
                        trainer.stop_training = True

                if hasattr(model, "add_callback"):
                    monitor = TrainingStallMonitor(
                        log_fn=lambda message: self._add_log(training_id, project_id, message),
                        on_stall_stop=_request_stall_stop,
                    )

                    def _capture_and_record(trainer):
                        trainer_ref['obj'] = trainer
                        monitor.record_batch()

                    try:
                        model.add_callback("on_train_batch_end", _capture_and_record)
                        monitor.start_watchdog(
                            suspended_fn=lambda: self.paused_states.get(training_id, False))
                    except Exception:
                        monitor.stop_watchdog()
                        monitor = None

                # Log training mode clearly
                if not device or 'cpu' in str(device).lower():
                    self._add_log(training_id, project_id,
                        f"Training mode: CPU ONLY ({os.cpu_count() or 8} cores detected)")
                    self._add_log(training_id, project_id,
                        "Note: CPU training is slower than GPU. Consider using GPU for faster training.")
                else:
                    try:
                        import torch
                        if torch.cuda.is_available():
                            gpu_name = torch.cuda.get_device_name(0)
                            self._add_log(training_id, project_id, f"Training mode: GPU ({gpu_name})")
                    except Exception:
                        self._add_log(training_id, project_id, "Training mode: GPU")

                self._add_log(training_id, project_id, "=" * 60)
                self._add_log(training_id, project_id, "Starting training")
                self._add_log(training_id, project_id, "=" * 60)

                # Diagnostic logging before training
                self._add_log(training_id, project_id, "=== Training Configuration ===")
                self._add_log(training_id, project_id, f"Epochs: {epochs}")
                patience_value = patience if patience else 100
                self._add_log(training_id, project_id, f"Patience: {patience_value}")
                self._add_log(training_id, project_id, f"Device: {device if device else 'auto'}")
                self._add_log(training_id, project_id, f"Batch: {batch}")
                self._add_log(training_id, project_id, "===================================")

                self._add_log(training_id, project_id, f"Training parameters:")
                self._add_log(training_id, project_id, f"  data: {train_args['data']}")
                self._add_log(training_id, project_id, f"  epochs: {epochs}")
                self._add_log(training_id, project_id, f"  imgsz: {imgsz}")
                self._add_log(training_id, project_id, f"  batch: {batch}")
                self._add_log(training_id, project_id, f"  device: {device or 'auto'}")
                self._add_log(training_id, project_id, f"  project: {train_args['project']}")
                self._add_log(training_id, project_id, f"  name: {train_args['name']}")
                self._add_log(training_id, project_id, f"  augment (default): {augment_info}")
                self._add_log(training_id, project_id, "-" * 60)
                
                # Run training (ultralytics will handle all details automatically)
                # verbose=True ensures detailed training information output
                train_args['verbose'] = True

                is_cpu_training = not device or 'cpu' in str(device).lower()
                max_retries = 2 if is_cpu_training else 0

                try:
                    for attempt in range(max_retries + 1):
                        try:
                            if attempt > 0:
                                if monitor is not None:
                                    monitor.reset()
                                self._add_log(training_id, project_id,
                                    f"Retrying training (attempt {attempt + 1}/{max_retries + 1})...")
                                time.sleep(5)

                            results = model.train(**train_args)

                            if monitor is not None and monitor.stopped_by_stall:
                                if attempt < max_retries:
                                    self._add_log(training_id, project_id,
                                        f"Training stalled on attempt {attempt + 1}, retrying...")
                                    continue
                                break

                            self._add_log(training_id, project_id, "=== Training Completed ===")
                            try:
                                if hasattr(results, 'results_dict'):
                                    metrics = results.results_dict
                                    self._add_log(training_id, project_id, f"Final Fitness: {metrics.get('metrics/fitness(B)', 'N/A')}")
                                    self._add_log(training_id, project_id, f"Final mAP50: {metrics.get('metrics/mAP50(B)', 'N/A')}")
                                if hasattr(results, 'epochs'):
                                    self._add_log(training_id, project_id, f"Epochs Run: {results.epochs}")
                            except Exception as e:
                                self._add_log(training_id, project_id, f"Warning: Could not extract diagnostics: {e}")
                            self._add_log(training_id, project_id, "=============================")

                            break

                        except Exception as e:
                            if attempt < max_retries:
                                self._add_log(training_id, project_id,
                                    f"Training attempt {attempt + 1} failed: {str(e)}")
                            else:
                                raise
                finally:
                    if monitor is not None:
                        monitor.stop_watchdog()

                stall_stopped = monitor is not None and monitor.stopped_by_stall
                stop_requested = stall_stopped or (stop_event.is_set() if stop_event else False)
                
            # Training completed (normal or stopped)
            training_success = True
            results_obj = results  # Save results object for later use
                
            # Training completed, update status
            with self.training_lock:
                # Find corresponding training record
                if project_id in self.training_records:
                    for record in self.training_records[project_id]:
                        if record.get('training_id') == training_id:
                            if stop_requested:
                                record['status'] = 'stopped'
                                record['end_time'] = datetime.now().isoformat()
                                record['error'] = 'auto-stopped: repeated batch stalls' if stall_stopped else None
                                record_for_db = record
                            else:
                                record['status'] = 'completed'
                                record['end_time'] = datetime.now().isoformat()
                                
                                # Extract more training metrics
                                results_dict = results_obj.results_dict
                                record['metrics'] = {
                                    'best_fitness': float(results_dict.get('metrics/fitness(B)', 0)),
                                    'mAP50': float(results_dict.get('metrics/mAP50(B)', 0)),
                                    'mAP50-95': float(results_dict.get('metrics/mAP50-95(B)', 0)),
                                    'precision': float(results_dict.get('metrics/precision(B)', 0)),
                                    'recall': float(results_dict.get('metrics/recall(B)', 0)),
                                    'box_loss': float(results_dict.get('train/box_loss', 0)),
                                    'cls_loss': float(results_dict.get('train/cls_loss', 0)),
                                    'dfl_loss': float(results_dict.get('train/dfl_loss', 0)),
                                    'val_box_loss': float(results_dict.get('val/box_loss', 0)),
                                    'val_cls_loss': float(results_dict.get('val/cls_loss', 0)),
                                    'val_dfl_loss': float(results_dict.get('val/dfl_loss', 0)),
                                }
                                # Get saved model path
                                model_path = results_obj.save_dir / 'weights' / 'best.pt'
                                if model_path.exists():
                                    record['model_path'] = str(model_path)
                                record_for_db = record
                            break

                    # Clear active training flag and cleanup
                    self._cleanup_training_state(training_id, project_id)

            # Sync to database
            if record_for_db:
                try:
                    self._persist_record(record_for_db)
                    if stop_requested:
                        outcome = "Training auto-stopped: no batch progress" if stall_stopped else "Training stopped by user"
                        self._add_log(training_id, project_id, outcome)
                        logger.info(f"[Training] Training stopped for project {project_id}, training_id: {training_id}")
                    else:
                        self._add_log(training_id, project_id, "Training completed!")
                        logger.info(f"[Training] Training completed for project {project_id}, training_id: {training_id}")
                except Exception as persist_error:
                    logger.error(f"[Training] Failed to persist completed training record: {persist_error}", exc_info=True)
            
        except KeyboardInterrupt:
            # Handle user interrupt (Ctrl+C)
            error_msg = "Training interrupted by user"
            try:
                self._add_log(training_id, project_id, f"Training interrupted by user")
            except Exception as log_error:
                logger.error(f"[Training] Failed to add error log: {log_error}")
            logger.warning(f"[Training] Training interrupted for project {project_id}, training_id: {training_id}")
            training_success = False
            
        except MemoryError as e:
            # Handle out-of-memory errors specifically
            error_msg = (
                "Training failed due to insufficient memory (OOM). "
                "The system killed the training process.\n\n"
                "Suggestions:\n"
                "1. Reduce batch size (current: {})\n"
                "2. Reduce image size (current: {})\n"
                "3. Use a smaller model size (e.g., 'n' instead of 's'/'m'/'l'/'x')\n"
                "4. Close other applications to free memory\n"
                "5. If using GPU, reduce batch size or use CPU with smaller batch size"
            ).format(batch, imgsz)
            try:
                self._add_log(training_id, project_id, f"Training failed: Out of memory (OOM)")
                self._add_log(training_id, project_id, "Suggestions:")
                self._add_log(training_id, project_id, f"  - Reduce batch size from {batch} to a smaller value")
                self._add_log(training_id, project_id, f"  - Reduce image size from {imgsz} to a smaller value")
                self._add_log(training_id, project_id, f"  - Use a smaller model (e.g., 'n' size)")
            except Exception as log_error:
                logger.error(f"[Training] Failed to add error log: {log_error}")
            logger.error(f"[Training] Training failed due to OOM for project {project_id}, training_id: {training_id}: {e}", exc_info=True)
            training_success = False
            
        except RuntimeError as e:
            # Handle PyTorch/CUDA runtime errors, which often include memory errors
            error_msg_str = str(e).lower()
            if any(keyword in error_msg_str for keyword in ['out of memory', 'oom', 'cuda out of memory', 'killed', 'memory']):
                error_msg = (
                    "Training failed due to insufficient memory (OOM). "
                    "CUDA/GPU or system memory exhausted.\n\n"
                    "Suggestions:\n"
                    "1. Reduce batch size (current: {})\n"
                    "2. Reduce image size (current: {})\n"
                    "3. Use a smaller model size (e.g., 'n' instead of 's'/'m'/'l'/'x')\n"
                    "4. Close other applications to free memory\n"
                    "5. If using GPU, reduce batch size or use CPU with smaller batch size"
                ).format(batch, imgsz)
                try:
                    self._add_log(training_id, project_id, f"Training failed: Out of memory (OOM)")
                    self._add_log(training_id, project_id, "Suggestions:")
                    self._add_log(training_id, project_id, f"  - Reduce batch size from {batch} to a smaller value")
                    self._add_log(training_id, project_id, f"  - Reduce image size from {imgsz} to a smaller value")
                    self._add_log(training_id, project_id, f"  - Use a smaller model (e.g., 'n' size)")
                except Exception as log_error:
                    logger.error(f"[Training] Failed to add error log: {log_error}")
            else:
                # Other runtime errors
                error_msg = str(e)
                try:
                    self._add_log(training_id, project_id, f"Training failed: {error_msg}")
                except Exception as log_error:
                    logger.error(f"[Training] Failed to add error log: {log_error}")
            logger.error(f"[Training] Training failed (RuntimeError) for project {project_id}, training_id: {training_id}: {e}", exc_info=True)
            training_success = False
            
        except Exception as e:
            error_msg = str(e)
            # Check if error message indicates OOM/killed
            error_msg_lower = error_msg.lower()
            if any(keyword in error_msg_lower for keyword in ['killed', 'out of memory', 'oom', 'memory']):
                error_msg = (
                    "Training failed due to insufficient memory. "
                    "The process was killed by the system.\n\n"
                    "Suggestions:\n"
                    "1. Reduce batch size (current: {})\n"
                    "2. Reduce image size (current: {})\n"
                    "3. Use a smaller model size\n"
                    "4. Close other applications to free memory"
                ).format(batch, imgsz)
                try:
                    self._add_log(training_id, project_id, f"Training failed: Out of memory (process killed)")
                    self._add_log(training_id, project_id, f"Original error: {str(e)}")
                    self._add_log(training_id, project_id, "Please reduce batch size or image size and try again")
                except Exception as log_error:
                    logger.error(f"[Training] Failed to add error log: {log_error}")
            else:
                try:
                    self._add_log(training_id, project_id, f"Training failed: {error_msg}")
                except Exception as log_error:
                    logger.error(f"[Training] Failed to add error log: {log_error}")
            
            logger.error(f"[Training] Training failed for project {project_id}, training_id: {training_id}: {e}", exc_info=True)
            training_success = False
            
        finally:
            # Cleanup resources (GPU/CUDA memory, etc.)
            try:
                # Clear CUDA cache if available
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                        logger.debug(f"[Training] CUDA cache cleared for training {training_id}")
                except Exception:
                    pass  # CUDA not available or already cleaned up
                
                # Delete model reference to free memory
                if model is not None:
                    del model
                    import gc
                    gc.collect()
                    logger.debug(f"[Training] Model object deleted and garbage collected for training {training_id}")
            except Exception as cleanup_error:
                logger.warning(f"[Training] Error during resource cleanup for training {training_id}: {cleanup_error}")
            
            # Ensure status is updated and active marker is cleared regardless of success or failure
            # Always clean up thread tracking and active markers, even if training succeeded
            # (success case was handled earlier, but we still need to ensure cleanup)
            with self.training_lock:
                # If training didn't succeed, ensure status is updated to failed (unless stop requested)
                if not training_success:
                    stop_requested = stop_event.is_set() if stop_event else False
                    record_for_db = None
                    # Find corresponding training record
                    if project_id in self.training_records:
                        for record in self.training_records[project_id]:
                            if record.get('training_id') == training_id:
                                # Only update if still running/stopping
                                if record.get('status') in ('running', 'stopping'):
                                    if stop_requested:
                                        record['status'] = 'stopped'
                                        record['error'] = None
                                    else:
                                        record['status'] = 'failed'
                                        record['error'] = error_msg if 'error_msg' in locals() else "Training failed unexpectedly"
                                        record['end_time'] = datetime.now().isoformat()
                                    record_for_db = record
                                break
                
                    # Sync to database
                    if record_for_db:
                        try:
                            self._persist_record(record_for_db)
                            if stop_requested:
                                try:
                                    self._add_log(training_id, project_id, "Training stopped by user")
                                except Exception:
                                    pass
                                logger.info(f"[Training] Updated training status to 'stopped' for project {project_id}, training_id: {training_id}")
                            else:
                                logger.info(f"[Training] Updated training status to 'failed' for project {project_id}, training_id: {training_id}")
                        except Exception as persist_error:
                            logger.error(f"[Training] Failed to persist failed training record: {persist_error}", exc_info=True)
                
                # Always clear active training marker and thread tracking (even if training succeeded)
                # This ensures clean state for next training
                self._cleanup_training_state(training_id, project_id)

    def _add_log(self, training_id: str, project_id: str, message: str):
        """Add log to training record and write to database"""
        # If message already has timestamp, keep as is; otherwise add timestamp
        if not message.startswith('[') or ']' not in message[:20]:
            timestamp = datetime.now().strftime('%H:%M:%S')
            log_entry = f"[{timestamp}] {message}"
        else:
            log_entry = message

        # Append to memory first (lightweight operation)
        with self.training_lock:
            if project_id in self.training_records:
                for record in self.training_records[project_id]:
                    if record.get('training_id') == training_id:
                        record.setdefault('logs', []).append(log_entry)
                        if len(record['logs']) > 10000:
                            record['logs'] = record['logs'][-5000:]
                        break

        # Write to TrainingLog table asynchronously to avoid blocking
        def _persist_single_log():
            session = SessionLocal()
            try:
                # Check if log already exists (avoid duplicates)
                existing = session.query(TrainingLog).filter(
                    TrainingLog.training_id == training_id,
                    TrainingLog.message == message,
                    TrainingLog.timestamp >= datetime.utcnow().replace(second=0, microsecond=0)
                ).first()
                if not existing:
                    session.add(TrainingLog(
                        training_id=training_id,
                        project_id=project_id,
                        timestamp=datetime.utcnow(),
                        message=message
                    ))

                    # Update log count
                    db_obj = session.query(TrainingRecord).filter(
                        TrainingRecord.training_id == training_id,
                        TrainingRecord.project_id == project_id
                    ).first()
                    if db_obj:
                        with self.training_lock:
                            if project_id in self.training_records:
                                for r in self.training_records[project_id]:
                                    if r.get('training_id') == training_id:
                                        db_obj.log_count = len(r.get('logs', []))
                                        break

                    session.commit()
            except Exception as e:
                session.rollback()
                logger.warning(f"[Training] Failed to persist log: {e}")
            finally:
                session.close()

        # Run in background thread (don't block)
        import threading
        thread = threading.Thread(target=_persist_single_log, daemon=True)
        thread.start()
    
    def _db_record_to_dict(self, db_obj: TrainingRecord) -> Dict:
        """Convert database object to dictionary"""
        metrics = {}
        if db_obj.metrics:
            try:
                metrics = json.loads(db_obj.metrics)
            except Exception:
                metrics = {}
        # Get model_type from memory if available, otherwise default to 'yolov8' for backward compatibility
        model_type = 'yolov8'
        if db_obj.project_id in self.training_records:
            for record in self.training_records[db_obj.project_id]:
                if record.get('training_id') == db_obj.training_id:
                    model_type = record.get('model_type', 'yolov8')
                    break
        return {
            'training_id': db_obj.training_id,
            'project_id': db_obj.project_id,
            'status': db_obj.status,
            'start_time': db_obj.start_time.isoformat() if db_obj.start_time else None,
            'end_time': db_obj.end_time.isoformat() if db_obj.end_time else None,
            'model_type': model_type,
            'model_size': db_obj.model_size,
            'epochs': db_obj.epochs,
            'imgsz': db_obj.imgsz,
            'batch': db_obj.batch,
            'device': db_obj.device,
            'metrics': metrics,
            'error': db_obj.error,
            'model_path': db_obj.model_path,
            'log_count': db_obj.log_count,
        }

    def get_training_records(self, project_id: str) -> List[Dict]:
        """Get all training records for project (database + memory log count)"""
        session = SessionLocal()
        try:
            db_records = session.query(TrainingRecord).filter(
                TrainingRecord.project_id == project_id
            ).order_by(TrainingRecord.start_time.desc()).all()
            results = []
            for db_obj in db_records:
                data = self._db_record_to_dict(db_obj)
                # Use log count from memory (if there are running logs)
                mem_log_count = self._get_log_count(project_id, db_obj.training_id)
                if mem_log_count:
                    data['log_count'] = mem_log_count
                results.append(data)
            return results
        finally:
            session.close()
    
    def get_training_record(self, project_id: str, training_id: str) -> Optional[Dict]:
        """Get specified training record (database + memory log count)"""
        session = SessionLocal()
        try:
            db_obj = session.query(TrainingRecord).filter(
                TrainingRecord.project_id == project_id,
                TrainingRecord.training_id == training_id
            ).first()
            if not db_obj:
                return None
            data = self._db_record_to_dict(db_obj)
            mem_log_count = self._get_log_count(project_id, training_id)
            if mem_log_count:
                data['log_count'] = mem_log_count
            # Logs read from database (frontend needs log content)
            data['logs'] = self._get_db_logs(project_id, training_id, limit=2000)
            return data
        finally:
            session.close()
    
    def get_training_status(self, project_id: str) -> Optional[Dict]:
        """Get current active training status (prefer memory active, then database latest)"""
        with self.training_lock:
            if project_id in self.active_trainings:
                training_id = self.active_trainings[project_id]
                # Check if thread is still running
                thread = self.training_threads.get(training_id)
                if thread is not None and not thread.is_alive():
                    # Thread has ended but status is still running, indicates abnormal termination (likely OOM kill)
                    for record in self.training_records.get(project_id, []):
                        if record.get('training_id') == training_id and record.get('status') == 'running':
                            # Update status to failed with helpful error message
                            batch = record.get('batch', 'unknown')
                            imgsz = record.get('imgsz', 'unknown')
                            record['status'] = 'failed'
                            record['error'] = (
                                "Training process was terminated unexpectedly (likely due to insufficient memory).\n\n"
                                "Suggestions:\n"
                                f"1. Reduce batch size (current: {batch})\n"
                                f"2. Reduce image size (current: {imgsz})\n"
                                "3. Use a smaller model size (e.g., 'n' instead of 's'/'m'/'l'/'x')\n"
                                "4. Close other applications to free memory"
                            )
                            record['end_time'] = datetime.now().isoformat()
                            try:
                                self._persist_record(record)
                                # Try to add log, but don't fail if it doesn't work
                                try:
                                    self._add_log(training_id, project_id, "Training terminated unexpectedly (likely OOM)")
                                except Exception:
                                    pass
                                logger.warning(f"[Training] Detected terminated thread for training {training_id}, marked as failed (likely OOM)")
                            except Exception as e:
                                logger.error(f"[Training] Failed to update terminated training status: {e}")
                            # Clear active flag and thread tracking using dedicated cleanup method
                            self._cleanup_training_state(training_id, project_id)
                            break
                
                # Return record from memory to ensure logs and status are real-time
                for record in self.training_records.get(project_id, []):
                    if record.get('training_id') == training_id:
                        return record
        # If no active training, get latest from database
        session = SessionLocal()
        try:
            db_obj = session.query(TrainingRecord).filter(
                TrainingRecord.project_id == project_id
            ).order_by(TrainingRecord.start_time.desc()).first()
            if db_obj:
                data = self._db_record_to_dict(db_obj)
                # Supplement logs (from database)
                data['logs'] = self._get_db_logs(project_id, db_obj.training_id, limit=2000)
                return data
            return None
        finally:
            session.close()
    
    def stop_training(self, project_id: str, training_id: Optional[str] = None) -> bool:
        """
        Stop training:
        - If training_id is provided, try to stop that record;
        - Otherwise stop current active training.
        Implements graceful stop by setting a stop flag checked by training thread.
        """
        with self.training_lock:
            target_id = training_id or self.active_trainings.get(project_id)
            if not target_id:
                return False
            stop_event = self.stop_events.get(target_id)
            if not stop_event:
                return False
            stop_event.set()

            # Update status to stopped immediately for UI visibility
            if project_id in self.training_records:
                for record in self.training_records[project_id]:
                    if record.get('training_id') == target_id and record.get('status') in ('running', 'stopping'):
                        record['status'] = 'stopped'
                        record['end_time'] = datetime.now().isoformat()
                        try:
                            self._persist_record(record)
                        except Exception:
                            pass
                        break
            # Remove active marker so status API falls back to DB/latest
            if project_id in self.active_trainings and self.active_trainings[project_id] == target_id:
                del self.active_trainings[project_id]
            # Add a log for visibility
            try:
                self._add_log(target_id, project_id, "Stop requested, training marked as stopped.")
            except Exception:
                pass
        
        # Launch a watcher thread to force-stop if graceful stop fails
        def _force_stop_watcher():
            thread = self.training_threads.get(target_id)
            if thread is None:
                return
            thread.join(timeout=5)
            if thread.is_alive():
                try:
                    import ctypes
                    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread.ident), ctypes.py_object(SystemExit))
                    if res == 0:
                        logger.warning(f"[Training] Force stop failed (no thread) for {target_id}")
                    elif res > 1:
                        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread.ident), None)
                        logger.warning(f"[Training] Force stop rollback for {target_id}")
                    else:
                        logger.info(f"[Training] Force stop signal sent for {target_id}")
                except Exception as e:
                    logger.error(f"[Training] Force stop error for {target_id}: {e}")
            # Ensure status and bookkeeping updated
            with self.training_lock:
                record_for_db = None
                if project_id in self.training_records:
                    for record in self.training_records[project_id]:
                        if record.get('training_id') == target_id:
                            record['status'] = 'stopped'
                            record['error'] = None
                            record['end_time'] = datetime.now().isoformat()
                            record_for_db = record
                            break
                if project_id in self.active_trainings and self.active_trainings[project_id] == target_id:
                    del self.active_trainings[project_id]
                if target_id in self.training_threads:
                    del self.training_threads[target_id]
                if target_id in self.stop_events:
                    del self.stop_events[target_id]
                if record_for_db:
                    try:
                        self._persist_record(record_for_db)
                        self._add_log(target_id, project_id, "Training stopped by user (force)")
                    except Exception:
                        pass
        try:
            watcher = threading.Thread(target=_force_stop_watcher, daemon=True)
            watcher.start()
        except Exception as e:
            logger.error(f"[Training] Failed to start force-stop watcher: {e}")
        return True

    def pause_training(self, project_id: str, training_id: Optional[str] = None) -> bool:
        """
        Pause a running training

        Args:
            project_id: Project ID
            training_id: Training ID to pause (optional, uses active training if not specified)

        Returns:
            True if pause was successful, False otherwise
        """
        with self.training_lock:
            # Use provided training_id or get active training
            target_id = training_id or self.active_trainings.get(project_id)
            if not target_id:
                logger.warning(f"[Training] Pause failed: no training found for project {project_id}")
                return False

            # Find the training record
            for record in self.training_records.get(project_id, []):
                if record['training_id'] == target_id:
                    # Only allow pausing running trainings
                    if record['status'] != 'running':
                        logger.warning(f"[Training] Pause failed: training {target_id} is not running (status: {record['status']})")
                        return False

                    # Set pause flag
                    self.paused_states[target_id] = True
                    record['status'] = 'paused'
                    self._update_db_fields(target_id, project_id, status='paused')
                    self._add_log(target_id, project_id, "Training paused")
                    logger.info(f"[Training] Training {target_id} paused")
                    return True

            logger.warning(f"[Training] Pause failed: training {target_id} not found in records")
            return False

    def continue_training(self, project_id: str, training_id: Optional[str] = None) -> bool:
        """
        Continue a paused training

        Args:
            project_id: Project ID
            training_id: Training ID to continue (optional, uses first paused training if not specified)

        Returns:
            True if continue was successful, False otherwise
        """
        with self.training_lock:
            # Use provided training_id or find first paused training
            target_id = training_id
            if not target_id:
                # Find a paused training for this project
                for record in self.training_records.get(project_id, []):
                    if record['status'] == 'paused':
                        target_id = record['training_id']
                        break

            if not target_id:
                logger.warning(f"[Training] Continue failed: no paused training found for project {project_id}")
                return False

            # Find the training record
            for record in self.training_records.get(project_id, []):
                if record['training_id'] == target_id:
                    # Only allow continuing paused trainings
                    if record['status'] != 'paused':
                        logger.warning(f"[Training] Continue failed: training {target_id} is not paused (status: {record['status']})")
                        return False

                    # Clear pause flag and wake up the training thread
                    pause_cond = self.pause_conditions.get(target_id)
                    if pause_cond:
                        with pause_cond:
                            self.paused_states[target_id] = False
                            pause_cond.notify_all()

                    record['status'] = 'running'
                    self._update_db_fields(target_id, project_id, status='running')
                    self._add_log(target_id, project_id, "Training continued")

                    # Mark as active training
                    self.active_trainings[project_id] = target_id
                    logger.info(f"[Training] Training {target_id} continued")
                    return True

            logger.warning(f"[Training] Continue failed: training {target_id} not found in records")
            return False

    def resume_training(
        self,
        project_id: str,
        training_id: str,
        additional_epochs: Optional[int] = None,
        device: Optional[str] = None,
    ) -> Dict:
        """
        Resume training from a previous training checkpoint

        Args:
            project_id: Project ID
            training_id: Training ID to resume from
            additional_epochs: Additional epochs to train (optional, uses original if not specified)
            device: Device to use for training (optional, uses original if not specified)

        Returns:
            Training information dictionary
        """
        print(f"[TrainingService] resume_training called for project {project_id}, training_id {training_id}")

        # Get the original training record
        original_record = self.get_training_record(project_id, training_id)
        if not original_record:
            error_msg = f"Training record {training_id} not found for project {project_id}"
            print(f"[TrainingService] ERROR: {error_msg}")
            raise ValueError(error_msg)

        # Check if original training is still running
        if original_record.get('status') == 'running':
            error_msg = f"Training {training_id} is still running, cannot resume"
            print(f"[TrainingService] ERROR: {error_msg}")
            raise ValueError(error_msg)

        # Find the last.pt checkpoint file
        model_path = original_record.get('model_path')
        checkpoint_path = None

        if model_path:
            # Try to find checkpoint relative to model_path
            checkpoint_path = Path(model_path).parent.parent / "weights" / "last.pt"
            if not checkpoint_path.exists():
                # Try alternative path
                checkpoint_path = Path(model_path).parent / "last.pt"

        # If still not found, try to construct path from training_id
        if not checkpoint_path or not checkpoint_path.exists():
            # YOLO saves checkpoints in train_<training_id>/weights/last.pt
            # The training directory is in the datasets folder
            from backend.config import settings
            datasets_root = getattr(settings, 'DATASETS_ROOT', '/app/datasets')
            possible_paths = [
                Path(datasets_root) / project_id / f"train_{training_id}" / "weights" / "last.pt",
                Path(datasets_root) / project_id / f"train_{training_id}" / "weights" / "last.pt",
            ]

            for path in possible_paths:
                if path.exists():
                    checkpoint_path = path
                    print(f"[TrainingService] Found checkpoint at: {checkpoint_path}")
                    break

        if not checkpoint_path or not checkpoint_path.exists():
            error_msg = f"Checkpoint file not found. Training may not have saved any checkpoints yet."
            print(f"[TrainingService] ERROR: {error_msg}")
            raise FileNotFoundError(error_msg)

        print(f"[TrainingService] Using checkpoint at: {checkpoint_path}")

        # Generate new training ID for the resumed training
        new_training_id = f"{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"[TrainingService] Generated new training_id: {new_training_id}")

        with self.training_lock:
            if project_id in self.active_trainings:
                error_msg = f"Training already in progress for project {project_id}"
                print(f"[TrainingService] ERROR: {error_msg}")
                raise ValueError(error_msg)

            # Create stop event for this training
            stop_event = threading.Event()
            self.stop_events[new_training_id] = stop_event

            # Mark as active training
            self.active_trainings[project_id] = new_training_id

            # Get original training parameters
            original_epochs = original_record.get('epochs', 100)
            epochs_to_use = additional_epochs if additional_epochs is not None else original_epochs
            device_to_use = device if device is not None else original_record.get('device')

            # Create training information
            training_info = {
                'training_id': new_training_id,
                'project_id': project_id,
                'status': 'running',
                'start_time': datetime.now().isoformat(),
                'model_type': original_record.get('model_type', 'yolov8'),
                'model_size': original_record.get('model_size', 'n'),
                'epochs': epochs_to_use,
                'imgsz': original_record.get('imgsz', 640),
                'batch': original_record.get('batch', 16),
                'device': device_to_use,
                'resumed_from': training_id,  # Track which training was resumed
            }

            # Add to training records
            if project_id not in self.training_records:
                self.training_records[project_id] = []
            self.training_records[project_id].append(training_info)

            # Persist to database
            try:
                self._persist_record(training_info)
                self._add_log(new_training_id, project_id,
                    f"Resuming training from {training_id}")
                self._add_log(new_training_id, project_id,
                    f"Checkpoint: {checkpoint_path}")
                self._add_log(new_training_id, project_id,
                    f"Additional epochs: {epochs_to_use}")
            except Exception as e:
                logger.error(f"[Training] Failed to persist resume training record: {e}")

            # Get dataset path from original training
            dataset_path = Path(model_path).parent.parent.parent / "yolo_export"
            if not dataset_path.exists():
                dataset_path = settings.DATASETS_ROOT / project_id / "yolo_export"

            print(f"[TrainingService] Using dataset path: {dataset_path}")

        # Start training in background thread
        def _run_resumed_training():
            try:
                self._run_training(
                    training_id=new_training_id,
                    project_id=project_id,
                    dataset_path=dataset_path,
                    training_info=training_info,
                    model_type=original_record.get('model_type', 'yolov8'),
                    model_size=original_record.get('model_size', 'n'),
                    epochs=epochs_to_use,
                    imgsz=original_record.get('imgsz', 640),
                    batch=original_record.get('batch', 16),
                    device=device_to_use,
                    stop_event=stop_event,
                    checkpoint_path=checkpoint_path,
                    original_record=original_record,
                )
            except Exception as e:
                logger.error(f"[Training] Resumed training failed: {e}", exc_info=True)
                with self.training_lock:
                    if project_id in self.training_records:
                        for record in self.training_records[project_id]:
                            if record.get('training_id') == new_training_id:
                                record['status'] = 'failed'
                                record['error'] = str(e)
                                record['end_time'] = datetime.now().isoformat()
                                try:
                                    self._persist_record(record)
                                    self._add_log(new_training_id, project_id, f"Training failed: {str(e)}")
                                except Exception:
                                    pass
                                break
                    if project_id in self.active_trainings and self.active_trainings[project_id] == new_training_id:
                        del self.active_trainings[project_id]
                    if new_training_id in self.stop_events:
                        del self.stop_events[new_training_id]

        thread = threading.Thread(target=_run_resumed_training, daemon=True)
        self.training_threads[new_training_id] = thread
        thread.start()

        print(f"[TrainingService] Resumed training started with new ID: {new_training_id}")

        return {
            'training_id': new_training_id,
            'resumed_from': training_id,
            'status': 'running',
        }

    def clear_training(self, project_id: str, training_id: Optional[str] = None):
        """Clear training record and delete model files"""
        # To avoid deletion failure due to memory loss, prioritize reading records to delete from database
        def _delete_model_file(model_path: Optional[str]):
            if not model_path:
                return
            try:
                model_file = Path(model_path)
                if model_file.exists():
                    model_file.unlink()
                    logger.info(f"[Training] Deleted model file: {model_path}")
                train_dir = model_file.parent.parent if model_file.parent else None
                if train_dir and train_dir.exists() and train_dir.name.startswith('train_'):
                    shutil.rmtree(train_dir, ignore_errors=True)
                    logger.info(f"[Training] Deleted training directory: {train_dir}")
            except Exception as e:
                logger.warning(f"[Training] Failed to delete model file {model_path}: {e}")

        session = SessionLocal()
        try:
            if training_id:
                # Get record from DB (priority, prevent memory loss)
                db_obj = session.query(TrainingRecord).filter(
                    TrainingRecord.training_id == training_id,
                    TrainingRecord.project_id == project_id
                ).first()
                model_path = db_obj.model_path if db_obj else None

                # Delete model file
                _delete_model_file(model_path)

                # Delete DB record
                session.query(TrainingRecord).filter(
                    TrainingRecord.training_id == training_id,
                    TrainingRecord.project_id == project_id
                ).delete()
                session.query(TrainingLog).filter(
                    TrainingLog.training_id == training_id,
                    TrainingLog.project_id == project_id
                ).delete()
                session.commit()

                # Sync memory
                with self.training_lock:
                    if project_id in self.training_records:
                        self.training_records[project_id] = [
                            r for r in self.training_records[project_id]
                            if r.get('training_id') != training_id
                        ]
                        if not self.training_records[project_id]:
                            del self.training_records[project_id]
                    if project_id in self.active_trainings and self.active_trainings[project_id] == training_id:
                        del self.active_trainings[project_id]
            else:
                # Clear all records for project
                db_objs = session.query(TrainingRecord).filter(
                    TrainingRecord.project_id == project_id
                ).all()
                for db_obj in db_objs:
                    _delete_model_file(db_obj.model_path)
                session.query(TrainingRecord).filter(
                    TrainingRecord.project_id == project_id
                ).delete()
                session.query(TrainingLog).filter(
                    TrainingLog.project_id == project_id
                ).delete()
                session.commit()

                with self.training_lock:
                    if project_id in self.training_records:
                        del self.training_records[project_id]
                    if project_id in self.active_trainings:
                        del self.active_trainings[project_id]
        finally:
            session.close()


# Global training service instance
training_service = TrainingService()
