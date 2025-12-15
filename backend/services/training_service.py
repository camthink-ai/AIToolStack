"""Model training service"""
import logging
import threading
import io
import sys
import shutil
import yaml
import json
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime

# Lazy import ultralytics to avoid requiring installation at module load time
try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

logger = logging.getLogger(__name__)

# Database
from backend.models.database import SessionLocal, TrainingRecord, TrainingLog
from backend.config import settings


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
                    # Deduplication: skip if same as last log line (avoid duplicate progress bar updates)
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
        batch: int = 16,
        device: Optional[str] = None,
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
            
            # Persist to database
            print(f"[TrainingService] Persisting training record to database...")
            self._persist_record(training_info)
            print(f"[TrainingService] Training record persisted")
            
            # Start training in background thread (pass training_id to generate unique directory name)
            print(f"[TrainingService] Creating training thread...")
            thread = threading.Thread(
                target=self._run_training,
                args=(training_id, project_id, dataset_path, training_info, model_type, model_size, epochs, imgsz, batch, device,
                      lr0, lrf, optimizer, momentum, weight_decay, patience, workers, val, save_period, amp,
                      hsv_h, hsv_s, hsv_v, degrees, translate, scale, shear, perspective, flipud, fliplr, mosaic, mixup,
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
        print(f"[TrainingService] Training thread started, returning training_info")
        
        return training_info
    
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
        stop_event: Optional[threading.Event] = None,
    ):
        """Run training in background thread"""
        print(f"[TrainingThread-{training_id}] Thread started, beginning training process")
        record_for_db = None
        training_success = False
        stop_requested = False
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
                if workers is not None:
                    train_args['workers'] = workers
                if val is not None:
                    train_args['val'] = val
                if save_period is not None:
                    train_args['save_period'] = save_period
                if amp is not None:
                    train_args['amp'] = amp
                
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
                
                self._add_log(training_id, project_id, "=" * 60)
                self._add_log(training_id, project_id, "Starting training")
                self._add_log(training_id, project_id, "=" * 60)
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
                results = model.train(**train_args)
                # Mark if stop was requested after training exits
                stop_requested = stop_event.is_set() if stop_event else False

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
                                record['error'] = None
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
                    
                    # Clear active training flag
                    if project_id in self.active_trainings and self.active_trainings[project_id] == training_id:
                        del self.active_trainings[project_id]
                # Cleanup stop event reference once training exits
                if training_id in self.stop_events:
                    del self.stop_events[training_id]
                if training_id in self.training_threads:
                    del self.training_threads[training_id]
            
            # Sync to database
            if record_for_db:
                try:
                    self._persist_record(record_for_db)
                    if stop_requested:
                        self._add_log(training_id, project_id, "Training stopped by user")
                        logger.info(f"[Training] Training stopped for project {project_id}, training_id: {training_id}")
                    else:
                        self._add_log(training_id, project_id, "Training completed!")
                        logger.info(f"[Training] Training completed for project {project_id}, training_id: {training_id}")
                except Exception as persist_error:
                    logger.error(f"[Training] Failed to persist completed training record: {persist_error}", exc_info=True)
            
        except KeyboardInterrupt:
            # Handle user interrupt (Ctrl+C)
            error_msg = "Training interrupted by user"
            self._add_log(training_id, project_id, f"Training interrupted by user")
            logger.warning(f"[Training] Training interrupted for project {project_id}, training_id: {training_id}")
            training_success = False
            
        except Exception as e:
            error_msg = str(e)
            try:
                self._add_log(training_id, project_id, f"Training failed: {error_msg}")
            except Exception as log_error:
                logger.error(f"[Training] Failed to add error log: {log_error}")
            
            logger.error(f"[Training] Training failed for project {project_id}, training_id: {training_id}: {e}", exc_info=True)
            training_success = False
            
        finally:
            # Ensure status is updated and active marker is cleared regardless of success or failure
            if not training_success:
                # If training didn't succeed, ensure status is updated to failed (unless stop requested)
                stop_requested = stop_event.is_set() if stop_event else False
                with self.training_lock:
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
                    
                    # Clear active training marker (clear regardless of status)
                    if project_id in self.active_trainings and self.active_trainings[project_id] == training_id:
                        del self.active_trainings[project_id]
                    
                    # Clear thread tracking
                    if training_id in self.training_threads:
                        del self.training_threads[training_id]
                    if training_id in self.stop_events:
                        del self.stop_events[training_id]
                
                # Sync to database
                if record_for_db:
                    try:
                        self._persist_record(record_for_db)
                        if stop_requested:
                            self._add_log(training_id, project_id, "Training stopped by user")
                            logger.info(f"[Training] Updated training status to 'stopped' for project {project_id}, training_id: {training_id}")
                        else:
                            logger.info(f"[Training] Updated training status to 'failed' for project {project_id}, training_id: {training_id}")
                    except Exception as persist_error:
                        logger.error(f"[Training] Failed to persist failed training record: {persist_error}", exc_info=True)
    
    def _add_log(self, training_id: str, project_id: str, message: str):
        """Add log to training record and write to database"""
        # If message already has timestamp, keep as is; otherwise add timestamp
        if not message.startswith('[') or ']' not in message[:20]:
            timestamp = datetime.now().strftime('%H:%M:%S')
            log_entry = f"[{timestamp}] {message}"
        else:
            log_entry = message
        
        # Append to memory (compatible with runtime real-time refresh)
        with self.training_lock:
            if project_id in self.training_records:
                for record in self.training_records[project_id]:
                    if record.get('training_id') == training_id:
                        record.setdefault('logs', []).append(log_entry)
                        if len(record['logs']) > 10000:
                            record['logs'] = record['logs'][-5000:]
                        break
        
        # Write to database log table and update log_count
        session = SessionLocal()
        try:
            session.add(TrainingLog(
                training_id=training_id,
                project_id=project_id,
                timestamp=datetime.utcnow(),
                message=message
            ))
            # Update count
            db_obj = session.query(TrainingRecord).filter(
                TrainingRecord.training_id == training_id,
                TrainingRecord.project_id == project_id
            ).first()
            if db_obj:
                db_obj.log_count = (db_obj.log_count or 0) + 1
            session.commit()
        except Exception as e:
            session.rollback()
            logger.warning(f"[Training] Failed to persist log: {e}")
        finally:
            session.close()
    
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
                    # Thread has ended but status is still running, indicates abnormal termination
                    for record in self.training_records.get(project_id, []):
                        if record.get('training_id') == training_id and record.get('status') == 'running':
                            # Update status to failed
                            record['status'] = 'failed'
                            record['error'] = "Training thread terminated unexpectedly"
                            record['end_time'] = datetime.now().isoformat()
                            try:
                                self._persist_record(record)
                                logger.warning(f"[Training] Detected terminated thread for training {training_id}, marked as failed")
                            except Exception as e:
                                logger.error(f"[Training] Failed to update terminated training status: {e}")
                            # Clear active flag
                            del self.active_trainings[project_id]
                            if training_id in self.training_threads:
                                del self.training_threads[training_id]
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
