"""YOLO 格式导出工具"""
import json
import shutil
import yaml
import random
from typing import List, Dict, Tuple
from pathlib import Path


class YOLOExporter:
    """YOLO 格式导出器"""
    
    @staticmethod
    def normalize_bbox(x_min: float, y_min: float, x_max: float, y_max: float,
                      img_width: int, img_height: int) -> Tuple[float, float, float, float]:
        """
        将边界框坐标转换为 YOLO 格式（归一化的中心点坐标和宽高）
        
        Args:
            x_min, y_min, x_max, y_max: 边界框绝对坐标
            img_width, img_height: 图像尺寸
            
        Returns:
            (center_x, center_y, width, height) 归一化坐标 (0~1)
        """
        # 计算绝对宽高
        box_w = x_max - x_min
        box_h = y_max - y_min
        
        # 计算绝对中心点
        center_x = x_min + (box_w / 2)
        center_y = y_min + (box_h / 2)
        
        # 归一化（保留6位小数）
        yolo_x = round(center_x / img_width, 6)
        yolo_y = round(center_y / img_height, 6)
        yolo_w = round(box_w / img_width, 6)
        yolo_h = round(box_h / img_height, 6)
        
        return yolo_x, yolo_y, yolo_w, yolo_h
    
    @staticmethod
    def normalize_points(points: List[List[float]], img_width: int, img_height: int) -> List[float]:
        """
        将多边形/关键点坐标归一化
        
        Args:
            points: [[x, y], ...] 或 [[x, y, index], ...]
            img_width, img_height: 图像尺寸
            
        Returns:
            一维数组 [x1, y1, x2, y2, ...] 归一化坐标
        """
        normalized = []
        for point in points:
            x = point[0] if isinstance(point, list) else point['x']
            y = point[1] if isinstance(point, list) else point['y']
            normalized.append(round(x / img_width, 6))
            normalized.append(round(y / img_height, 6))
        return normalized
    
    @staticmethod
    def export_annotation(annotation: Dict, class_id: int, img_width: int, img_height: int) -> str:
        """
        导出单个标注为 YOLO 格式字符串
        
        Args:
            annotation: 标注数据字典
            class_id: 类别 ID
            img_width, img_height: 图像尺寸
            
        Returns:
            YOLO 格式行: "class_id x y w h" 或 "class_id x1 y1 x2 y2 ..."
        """
        ann_type = annotation.get('type')
        data = annotation.get('data')
        
        if isinstance(data, str):
            data = json.loads(data)
        
        if ann_type == 'bbox':
            x_min = data['x_min']
            y_min = data['y_min']
            x_max = data['x_max']
            y_max = data['y_max']
            
            yolo_x, yolo_y, yolo_w, yolo_h = YOLOExporter.normalize_bbox(
                x_min, y_min, x_max, y_max, img_width, img_height
            )
            
            return f"{class_id} {yolo_x} {yolo_y} {yolo_w} {yolo_h}"
        
        elif ann_type in ['polygon', 'keypoint']:
            points = data.get('points', [])
            normalized_points = YOLOExporter.normalize_points(points, img_width, img_height)
            points_str = ' '.join(str(p) for p in normalized_points)
            
            return f"{class_id} {points_str}"
        
        else:
            raise ValueError(f"Unsupported annotation type: {ann_type}")
    
    @staticmethod
    def export_image(image_id: int, annotations: List[Dict], class_map: Dict[str, int],
                    img_width: int, img_height: int) -> List[str]:
        """
        导出单张图像的所有标注
        
        Args:
            image_id: 图像 ID
            annotations: 标注列表
            class_map: 类别名称到类别 ID 的映射
            img_width, img_height: 图像尺寸
            
        Returns:
            YOLO 格式行列表
        """
        lines = []
        
        for ann in annotations:
            class_name = ann.get('class_name')
            class_id = class_map.get(class_name, -1)
            
            if class_id < 0:
                continue
            
            try:
                line = YOLOExporter.export_annotation(ann, class_id, img_width, img_height)
                lines.append(line)
            except Exception as e:
                print(f"Error exporting annotation {ann.get('id')}: {e}")
                continue
        
        return lines
    
    @staticmethod
    def export_project(project_data: Dict, output_dir: Path, datasets_root: Path):
        """
        导出整个项目的 YOLO 格式数据（符合 Ultralytics 官方格式）
        
        Args:
            project_data: 项目数据字典，包含 images, annotations, classes
            output_dir: 输出目录
            datasets_root: 数据集根目录，用于解析图像路径
        """
        # 清理旧的导出目录，确保干净的目录结构
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建 Ultralytics 标准目录结构
        images_train_dir = output_dir / "images" / "train"
        images_val_dir = output_dir / "images" / "val"
        labels_train_dir = output_dir / "labels" / "train"
        labels_val_dir = output_dir / "labels" / "val"
        
        images_train_dir.mkdir(parents=True, exist_ok=True)
        images_val_dir.mkdir(parents=True, exist_ok=True)
        labels_train_dir.mkdir(parents=True, exist_ok=True)
        labels_val_dir.mkdir(parents=True, exist_ok=True)
        
        # 构建类别映射
        classes = project_data.get('classes', [])
        class_map = {cls['name']: idx for idx, cls in enumerate(classes)}
        class_names = [cls['name'] for cls in classes]
        
        # 获取所有有效图像（存在文件的图像）
        images = project_data.get('images', [])
        valid_images = []
        
        for image in images:
            src_path = datasets_root / project_data['id'] / image['path']
            if src_path.exists():
                valid_images.append(image)
        
        # 确定拆分比例：默认 8:2，如果图像数量少于10张则使用 1:1
        total_images = len(valid_images)
        if total_images < 10:
            train_ratio = 0.5  # 1:1
        else:
            train_ratio = 0.8  # 8:2
        
        # 计算训练集和验证集的数量
        train_count = max(1, int(total_images * train_ratio))
        val_count = total_images - train_count
        
        # 随机打乱图像顺序（使用固定种子以确保可重复性）
        random.seed(42)  # 固定种子，确保每次导出结果一致
        shuffled_images = valid_images.copy()
        random.shuffle(shuffled_images)
        
        # 拆分图像
        train_images = shuffled_images[:train_count]
        val_images = shuffled_images[train_count:]
        
        # 导出训练集图像和标注
        train_copied = 0
        for image in train_images:
            img_filename = Path(image['filename'])
            img_stem = img_filename.stem
            img_width = image['width']
            img_height = image['height']
            
            # 复制图像文件
            src_path = datasets_root / project_data['id'] / image['path']
            dst_path = images_train_dir / image['filename']
            shutil.copy2(src_path, dst_path)
            train_copied += 1
            
            # 导出标注
            annotations = image.get('annotations', [])
            if annotations:
                label_lines = YOLOExporter.export_image(
                    image['id'], annotations, class_map, img_width, img_height
                )
                
                if label_lines:
                    label_file = labels_train_dir / f"{img_stem}.txt"
                    with open(label_file, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(label_lines))
        
        # 导出验证集图像和标注
        val_copied = 0
        for image in val_images:
            img_filename = Path(image['filename'])
            img_stem = img_filename.stem
            img_width = image['width']
            img_height = image['height']
            
            # 复制图像文件
            src_path = datasets_root / project_data['id'] / image['path']
            dst_path = images_val_dir / image['filename']
            shutil.copy2(src_path, dst_path)
            val_copied += 1
            
            # 导出标注
            annotations = image.get('annotations', [])
            if annotations:
                label_lines = YOLOExporter.export_image(
                    image['id'], annotations, class_map, img_width, img_height
                )
                
                if label_lines:
                    label_file = labels_val_dir / f"{img_stem}.txt"
                    with open(label_file, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(label_lines))
        
        # 创建 data.yaml 配置文件（Ultralytics 标准格式）
        data_yaml = {
            'path': str(output_dir.absolute()),  # 数据集根路径
            'train': 'images/train',  # 训练图像相对路径
            'val': 'images/val',  # 验证集路径
            'nc': len(classes),  # 类别数量
            'names': class_names  # 类别名称列表
        }
        
        yaml_file = output_dir / "data.yaml"
        with open(yaml_file, 'w', encoding='utf-8') as f:
            yaml.dump(data_yaml, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        
        # 创建类别名称文件（兼容旧格式）
        names_file = output_dir / "classes.txt"
        with open(names_file, 'w', encoding='utf-8') as f:
            for cls_name in class_names:
                f.write(f"{cls_name}\n")
        
        return {
            'images_count': total_images,
            'train_count': train_copied,
            'val_count': val_copied,
            'classes_count': len(classes)
        }

