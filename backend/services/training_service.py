"""模型训练服务"""
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

# 延迟导入 ultralytics，避免在模块加载时就必须安装
try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

logger = logging.getLogger(__name__)

# 数据库
from backend.models.database import SessionLocal, TrainingRecord, TrainingLog
from backend.config import settings


def _setup_pytorch_compatibility():
    """
    设置 PyTorch 2.6+ 兼容性（后备方案）
    
    注意：ultralytics 8.3.162+ 版本已经修复了 PyTorch 2.6+ 的兼容性问题
    最新版本 8.3.229+ 已测试与 PyTorch 2.9 兼容
    此函数仅作为后备方案，如果升级后仍有问题才会使用
    """
    try:
        import torch
        if not hasattr(torch.serialization, 'add_safe_globals'):
            return
        
        safe_globals = []
        
        # 添加 ultralytics 相关的所有类
        try:
            from ultralytics.nn.tasks import (
                DetectionModel, SegmentationModel, 
                PoseModel, ClassificationModel
            )
            safe_globals.extend([DetectionModel, SegmentationModel, PoseModel, ClassificationModel])
        except ImportError:
            pass
        
        # 添加 ultralytics.nn.modules 中的所有类
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
        
        # 添加 PyTorch 内置类
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
    """捕获训练过程中的日志输出"""
    def __init__(self, training_id: str, project_id: str):
        self.training_id = training_id
        self.project_id = project_id
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        self.last_log_line = ''  # 用于去重
        import re
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        
    def write(self, message):
        """捕获标准输出"""
        if message.strip():
            # 清理ANSI转义码
            cleaned_message = self._strip_ansi_codes(message.rstrip('\n'))
            # 过滤掉空行和只包含控制字符的行
            if cleaned_message.strip():
                # 去重：如果和上一条日志相同，跳过（避免重复的进度条更新）
                cleaned_stripped = cleaned_message.strip()
                if cleaned_stripped != self.last_log_line:
                    self.last_log_line = cleaned_stripped
                    training_service._add_log(self.training_id, self.project_id, cleaned_message)
        self.original_stdout.write(message)
    
    def _strip_ansi_codes(self, text: str) -> str:
        """移除ANSI转义码"""
        # 移除ANSI转义序列（颜色、样式、光标控制等）
        cleaned = self.ansi_escape.sub('', text)
        # 移除常见的控制字符
        cleaned = cleaned.replace('\r', '')
        # 移除只包含空白字符的行
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
    """训练服务管理器"""
    
    def __init__(self):
        # 支持多个训练记录：{project_id: [training_record1, training_record2, ...]}
        self.training_records: Dict[str, List[Dict]] = {}
        # 当前活动的训练：{project_id: training_id}
        self.active_trainings: Dict[str, str] = {}
        self.training_lock = threading.Lock()
    
    # ========= 数据库相关工具方法 =========
    def _persist_record(self, record: Dict):
        """将训练记录写入数据库（插入或更新）"""
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
            
            # 同步字段
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
        """更新数据库训练记录指定字段"""
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
        """从内存中获取日志数量"""
        with self.training_lock:
            if project_id in self.training_records:
                for record in self.training_records[project_id]:
                    if record.get('training_id') == training_id:
                        return len(record.get('logs', []))
        return 0

    def _get_db_logs(self, project_id: str, training_id: str, limit: int = 1000) -> List[str]:
        """从数据库获取日志（按时间升序，最多 limit 条）"""
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
        启动训练任务
        
        Args:
            project_id: 项目ID
            dataset_path: 数据集路径（包含 data.yaml）
            model_size: 模型大小 ('n', 's', 'm', 'l', 'x')
            epochs: 训练轮数
            imgsz: 图像尺寸
            batch: 批次大小
            device: 设备 ('cpu', 'cuda', '0', '1', 'mps', ...)
            
        Returns:
            训练信息字典
        """
        with self.training_lock:
            if project_id in self.active_trainings:
                raise ValueError(f"Training already in progress for project {project_id}")
            
            # 检查数据集是否存在
            data_yaml = dataset_path / "data.yaml"
            if not data_yaml.exists():
                raise FileNotFoundError(f"data.yaml not found at {data_yaml}")
            
            # 生成训练记录ID
            training_id = f"{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # 创建训练信息
            training_info = {
                'training_id': training_id,
                'project_id': project_id,
                'status': 'running',
                'start_time': datetime.now().isoformat(),
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
            
            # 初始化训练记录列表
            if project_id not in self.training_records:
                self.training_records[project_id] = []
            
            # 添加到训练记录列表（最新的在前面）
            self.training_records[project_id].insert(0, training_info)
            self.active_trainings[project_id] = training_id
            
            # 持久化到数据库
            self._persist_record(training_info)
            
            # 在后台线程中启动训练
            thread = threading.Thread(
                target=self._run_training,
                args=(training_id, project_id, dataset_path, training_info, model_size, epochs, imgsz, batch, device,
                      lr0, lrf, optimizer, momentum, weight_decay, patience, workers, val, save_period, amp,
                      hsv_h, hsv_s, hsv_v, degrees, translate, scale, shear, perspective, flipud, fliplr, mosaic, mixup),
                daemon=True
            )
            thread.start()
            
            return training_info
    
    def _run_training(
        self,
        training_id: str,
        project_id: str,
        dataset_path: Path,
        training_info: Dict,
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
    ):
        """在后台线程中运行训练"""
        try:
            if YOLO is None:
                raise ImportError("ultralytics library is not installed. Please install it with: pip install ultralytics")
            
            # 设置 PyTorch 2.6+ 兼容性（在加载模型前）
            _setup_pytorch_compatibility()
            
            # 读取data.yaml获取类别信息
            data_yaml_path = dataset_path / "data.yaml"
            class_names = []
            num_classes = 0
            try:
                with open(data_yaml_path, 'r', encoding='utf-8') as f:
                    data_config = yaml.safe_load(f)
                    class_names = data_config.get('names', [])
                    num_classes = data_config.get('nc', 0)
            except Exception as e:
                self._add_log(training_id, project_id, f"警告: 无法读取data.yaml中的类别信息: {str(e)}")
            
            # 使用日志捕获
            with LogCapture(training_id, project_id):
                # 添加初始日志 - 训练参数信息
                self._add_log(training_id, project_id, "=" * 60)
                self._add_log(training_id, project_id, "训练任务启动")
                self._add_log(training_id, project_id, "=" * 60)
                self._add_log(training_id, project_id, f"模型: yolov8{model_size}.pt")
                self._add_log(training_id, project_id, f"训练轮数 (Epochs): {epochs}")
                self._add_log(training_id, project_id, f"批次大小 (Batch Size): {batch}")
                self._add_log(training_id, project_id, f"图像尺寸 (Image Size): {imgsz}")
                self._add_log(training_id, project_id, f"设备 (Device): {device or 'auto'}")
                self._add_log(training_id, project_id, f"数据集路径: {dataset_path}")
                self._add_log(training_id, project_id, "-" * 60)
                
                # 添加类别信息
                if class_names:
                    self._add_log(training_id, project_id, f"类别数量 (Classes): {num_classes}")
                    self._add_log(training_id, project_id, "类别列表:")
                    for idx, cls_name in enumerate(class_names):
                        self._add_log(training_id, project_id, f"  [{idx}] {cls_name}")
                else:
                    self._add_log(training_id, project_id, f"类别数量 (Classes): {num_classes}")
                self._add_log(training_id, project_id, "-" * 60)
                
                # 加载预训练模型（ultralytics 会自动处理下载）
                model_name = f'yolov8{model_size}.pt'
                self._add_log(training_id, project_id, f"正在加载预训练模型: {model_name}")
                
                try:
                    model = YOLO(model_name)
                    self._add_log(training_id, project_id, "模型加载成功")
                except Exception as e:
                    error_msg = str(e)
                    # 如果是网络相关错误，提供友好的提示
                    if any(keyword in error_msg.lower() for keyword in ['download', 'connection', 'ssl', 'url', 'network']):
                        download_url = f"https://github.com/ultralytics/assets/releases/download/v8.1.0/{model_name}"
                        home_dir = Path.home()
                        raise ConnectionError(
                            f"无法下载预训练模型 {model_name}。\n\n"
                            f"解决方案：\n"
                            f"1. 手动下载：{download_url}\n"
                            f"2. 放置到以下任一位置：\n"
                            f"   - {Path.cwd() / model_name}\n"
                            f"   - {home_dir / '.ultralytics' / 'weights' / model_name}\n"
                            f"   - {home_dir / '.cache' / 'ultralytics' / model_name}\n"
                        ) from e
                    raise
                
                # 准备训练参数（按照 ultralytics 文档的方式）
                train_args = {
                    'data': str(dataset_path / "data.yaml"),
                    'epochs': epochs,
                    'imgsz': imgsz,
                    'batch': batch,
                    'device': device,
                    'project': str(dataset_path.parent),
                    'name': f'train_{project_id}',
                    'exist_ok': True,
                }
                
                # 添加可选参数（如果提供）
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
                
                # 数据增强参数（如果提供，否则使用默认值）
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
                
                # 将数据增强参数添加到训练参数中
                train_args.update(augment_info)
                
                self._add_log(training_id, project_id, "=" * 60)
                self._add_log(training_id, project_id, "开始训练")
                self._add_log(training_id, project_id, "=" * 60)
                self._add_log(training_id, project_id, f"训练参数:")
                self._add_log(training_id, project_id, f"  data: {train_args['data']}")
                self._add_log(training_id, project_id, f"  epochs: {epochs}")
                self._add_log(training_id, project_id, f"  imgsz: {imgsz}")
                self._add_log(training_id, project_id, f"  batch: {batch}")
                self._add_log(training_id, project_id, f"  device: {device or 'auto'}")
                self._add_log(training_id, project_id, f"  project: {train_args['project']}")
                self._add_log(training_id, project_id, f"  name: {train_args['name']}")
                self._add_log(training_id, project_id, f"  augment (默认): {augment_info}")
                self._add_log(training_id, project_id, "-" * 60)
                
                # 运行训练（ultralytics 会自动处理所有细节）
                # verbose=True 确保输出详细的训练信息
                train_args['verbose'] = True
                results = model.train(**train_args)
                
            # 训练完成，更新状态
            record_for_db = None
            with self.training_lock:
                # 找到对应的训练记录
                if project_id in self.training_records:
                    for record in self.training_records[project_id]:
                        if record.get('training_id') == training_id:
                            record['status'] = 'completed'
                            record['end_time'] = datetime.now().isoformat()
                            
                            # 提取更多训练指标
                            results_dict = results.results_dict
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
                            # 获取保存的模型路径
                            model_path = results.save_dir / 'weights' / 'best.pt'
                            if model_path.exists():
                                record['model_path'] = str(model_path)
                            record_for_db = record
                            break
                    
                    # 清除活动训练标记
                    if project_id in self.active_trainings and self.active_trainings[project_id] == training_id:
                        del self.active_trainings[project_id]
            
            # 同步到数据库
            if record_for_db:
                self._persist_record(record_for_db)
                
                self._add_log(training_id, project_id, "训练完成！")
                logger.info(f"[Training] Training completed for project {project_id}, training_id: {training_id}")
            
        except Exception as e:
            error_msg = str(e)
            self._add_log(training_id, project_id, f"训练失败: {error_msg}")
            logger.error(f"[Training] Training failed for project {project_id}, training_id: {training_id}: {e}", exc_info=True)
            
            record_for_db = None
            with self.training_lock:
                # 找到对应的训练记录
                if project_id in self.training_records:
                    for record in self.training_records[project_id]:
                        if record.get('training_id') == training_id:
                            record['status'] = 'failed'
                            record['error'] = error_msg
                            record['end_time'] = datetime.now().isoformat()
                            record_for_db = record
                            break
                
                # 清除活动训练标记
                if project_id in self.active_trainings and self.active_trainings[project_id] == training_id:
                    del self.active_trainings[project_id]
            
            if record_for_db:
                self._persist_record(record_for_db)
    
    def _add_log(self, training_id: str, project_id: str, message: str):
        """添加日志到训练记录，并写入数据库"""
        # 如果消息已带时间戳，保持原样；否则添加时间戳
        if not message.startswith('[') or ']' not in message[:20]:
            timestamp = datetime.now().strftime('%H:%M:%S')
            log_entry = f"[{timestamp}] {message}"
        else:
            log_entry = message
        
        # 内存追加（兼容运行时实时刷新）
        with self.training_lock:
            if project_id in self.training_records:
                for record in self.training_records[project_id]:
                    if record.get('training_id') == training_id:
                        record.setdefault('logs', []).append(log_entry)
                        if len(record['logs']) > 10000:
                            record['logs'] = record['logs'][-5000:]
                        break
        
        # 写入数据库日志表，并更新 log_count
        session = SessionLocal()
        try:
            session.add(TrainingLog(
                training_id=training_id,
                project_id=project_id,
                timestamp=datetime.utcnow(),
                message=message
            ))
            # 更新计数
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
        """将数据库对象转换为字典"""
        metrics = {}
        if db_obj.metrics:
            try:
                metrics = json.loads(db_obj.metrics)
            except Exception:
                metrics = {}
        return {
            'training_id': db_obj.training_id,
            'project_id': db_obj.project_id,
            'status': db_obj.status,
            'start_time': db_obj.start_time.isoformat() if db_obj.start_time else None,
            'end_time': db_obj.end_time.isoformat() if db_obj.end_time else None,
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
        """获取项目的所有训练记录（数据库 + 内存日志数）"""
        session = SessionLocal()
        try:
            db_records = session.query(TrainingRecord).filter(
                TrainingRecord.project_id == project_id
            ).order_by(TrainingRecord.start_time.desc()).all()
            results = []
            for db_obj in db_records:
                data = self._db_record_to_dict(db_obj)
                # 使用内存中的日志数量（如果有运行中的日志）
                mem_log_count = self._get_log_count(project_id, db_obj.training_id)
                if mem_log_count:
                    data['log_count'] = mem_log_count
                results.append(data)
            return results
        finally:
            session.close()
    
    def get_training_record(self, project_id: str, training_id: str) -> Optional[Dict]:
        """获取指定的训练记录（数据库 + 内存日志数）"""
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
            # 日志从数据库读取（前端需要日志内容）
            data['logs'] = self._get_db_logs(project_id, training_id, limit=2000)
            return data
        finally:
            session.close()
    
    def get_training_status(self, project_id: str) -> Optional[Dict]:
        """获取当前活动的训练状态（优先内存活动，其次数据库最新）"""
        with self.training_lock:
            if project_id in self.active_trainings:
                training_id = self.active_trainings[project_id]
                # 返回内存中的记录以保证日志和状态实时
                for record in self.training_records.get(project_id, []):
                    if record.get('training_id') == training_id:
                        return record
        # 若无活动训练，取数据库最新一条
        session = SessionLocal()
        try:
            db_obj = session.query(TrainingRecord).filter(
                TrainingRecord.project_id == project_id
            ).order_by(TrainingRecord.start_time.desc()).first()
            if db_obj:
                data = self._db_record_to_dict(db_obj)
                # 补充日志（从数据库）
                data['logs'] = self._get_db_logs(project_id, db_obj.training_id, limit=2000)
                return data
            return None
        finally:
            session.close()
    
    def stop_training(self, project_id: str, training_id: Optional[str] = None) -> bool:
        """
        停止训练：
        - 如果传入 training_id，则尝试停止该记录；
        - 否则停止当前活动训练。
        注：目前未实现硬中断，只标记为 stopped。
        """
        with self.training_lock:
            target_id = training_id or self.active_trainings.get(project_id)
            if not target_id:
                return False
            record = self.get_training_record(project_id, target_id)
            if record and record.get('status') == 'running':
                record['status'] = 'stopped'
                record['end_time'] = datetime.now().isoformat()
                # 移除活动标记
                if project_id in self.active_trainings and self.active_trainings[project_id] == target_id:
                    del self.active_trainings[project_id]
                try:
                    self._persist_record(record)
                except Exception:
                    pass
                return True
            # 如果已完成或失败，直接返回 False
            return False
    
    def clear_training(self, project_id: str, training_id: Optional[str] = None):
        """清除训练记录，同时删除模型文件"""
        # 为避免因内存丢失导致删除失败，优先从数据库读取待删记录
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
                # 从 DB 取记录（优先，防止内存缺失）
                db_obj = session.query(TrainingRecord).filter(
                    TrainingRecord.training_id == training_id,
                    TrainingRecord.project_id == project_id
                ).first()
                model_path = db_obj.model_path if db_obj else None

                # 删除模型文件
                _delete_model_file(model_path)

                # 删除 DB 记录
                session.query(TrainingRecord).filter(
                    TrainingRecord.training_id == training_id,
                    TrainingRecord.project_id == project_id
                ).delete()
                session.query(TrainingLog).filter(
                    TrainingLog.training_id == training_id,
                    TrainingLog.project_id == project_id
                ).delete()
                session.commit()

                # 同步内存
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
                # 清除项目全部记录
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


# 全局训练服务实例
training_service = TrainingService()
