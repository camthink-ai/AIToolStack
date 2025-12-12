"""
Dataset import utilities for COCO and YOLO formats
"""
import json
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from PIL import Image
import logging

logger = logging.getLogger(__name__)


def generate_color(index: int) -> str:
    """Generate a color for a class based on index"""
    colors = [
        "#FF6B6B", "#4ECDC4", "#45B7D1", "#FFA07A", "#98D8C8",
        "#F7DC6F", "#BB8FCE", "#85C1E2", "#F8B739", "#82E0AA",
        "#F1948A", "#85C1E9", "#F7DC6F", "#BB8FCE", "#52BE80",
        "#EC7063", "#5DADE2", "#F4D03F", "#AF7AC5", "#76D7C4"
    ]
    return colors[index % len(colors)]


class DatasetImporter:
    """Base class for dataset importers"""
    
    @staticmethod
    def import_dataset(project_id: str, dataset_path: Path, format_type: str) -> Dict:
        """
        Import dataset from a file or directory
        
        Args:
            project_id: Project ID (not used currently, kept for compatibility)
            dataset_path: Path to dataset file or directory
            format_type: Dataset format ('coco', 'yolo', 'images')
            
        Returns:
            Dict with import statistics
        """
        if format_type == 'coco':
            return COCOImporter.import_dataset(project_id, dataset_path)
        elif format_type == 'yolo':
            return YOLOImporter.import_dataset(project_id, dataset_path)
        elif format_type == 'images':
            # This is handled by the upload endpoint
            return {"message": "Use image upload endpoint for images"}
        else:
            raise ValueError(f"Unsupported dataset format: {format_type}")


class COCOImporter:
    """COCO format importer"""
    
    @staticmethod
    def import_dataset(project_id: str, dataset_path: Path) -> Dict:
        """
        Import COCO format dataset
        
        COCO format structure:
        {
            "images": [{"id": int, "file_name": str, "width": int, "height": int, ...}, ...],
            "annotations": [{"id": int, "image_id": int, "category_id": int, "bbox": [x, y, w, h], "area": float, ...}, ...],
            "categories": [{"id": int, "name": str, ...}, ...]
        }
        
        Returns:
            Dict with import statistics
        """
        try:
            # Read COCO JSON file
            if dataset_path.is_file():
                coco_data = json.loads(dataset_path.read_text())
                images_dir = dataset_path.parent
            else:
                # If it's a directory, look for annotations file
                annotations_file = dataset_path / "annotations.json"
                if not annotations_file.exists():
                    annotations_file = dataset_path / "instances_default.json"
                if not annotations_file.exists():
                    raise FileNotFoundError("COCO annotations file not found (expected annotations.json or instances_default.json)")
                coco_data = json.loads(annotations_file.read_text())
                images_dir = dataset_path / "images"
                if not images_dir.exists():
                    images_dir = dataset_path
            
            images = coco_data.get("images", [])
            annotations = coco_data.get("annotations", [])
            categories = coco_data.get("categories", [])
            
            # Build mappings
            image_id_to_info = {img["id"]: img for img in images}
            category_id_to_info = {cat["id"]: cat for cat in categories}
            image_filename_to_id = {img["file_name"]: img["id"] for img in images}
            
            # Group annotations by image_id
            annotations_by_image = {}
            for ann in annotations:
                image_id = ann["image_id"]
                if image_id not in annotations_by_image:
                    annotations_by_image[image_id] = []
                annotations_by_image[image_id].append(ann)
            
            # Prepare result
            result = {
                "images": [],
                "categories": [],
                "annotations_by_image": {},
                "images_dir": str(images_dir)
            }
            
            # Process categories
            for cat in categories:
                result["categories"].append({
                    "id": cat["id"],
                    "name": cat["name"],
                    "supercategory": cat.get("supercategory", "")
                })
            
            # Process images and annotations
            for img in images:
                img_info = {
                    "id": img["id"],
                    "file_name": img["file_name"],
                    "width": img["width"],
                    "height": img["height"],
                    "annotations": []
                }
                
                # Get annotations for this image
                if img["id"] in annotations_by_image:
                    for ann in annotations_by_image[img["id"]]:
                        category_id = ann["category_id"]
                        category = category_id_to_info.get(category_id)
                        if not category:
                            continue
                        
                        # Convert COCO bbox [x, y, width, height] to our format [x_min, y_min, x_max, y_max]
                        bbox = ann.get("bbox", [])
                        if len(bbox) == 4:
                            x, y, w, h = bbox
                            annotation_data = {
                                "x_min": float(x),
                                "y_min": float(y),
                                "x_max": float(x + w),
                                "y_max": float(y + h)
                            }
                            img_info["annotations"].append({
                                "category_id": category_id,
                                "category_name": category["name"],
                                "data": annotation_data,
                                "type": "bbox"
                            })
                
                result["images"].append(img_info)
            
            return result
            
        except Exception as e:
            logger.error(f"[Dataset Import] Failed to parse COCO format: {e}", exc_info=True)
            raise ValueError(f"Failed to parse COCO format: {str(e)}")


class YOLOImporter:
    """YOLO format importer"""
    
    @staticmethod
    def import_dataset(project_id: str, dataset_path: Path) -> Dict:
        """
        Import YOLO format dataset
        
        YOLO format structure:
        - images/ (directory with images)
        - labels/ (directory with .txt files, one per image)
        - classes.txt (optional, list of class names)
        
        Label file format (per line):
        class_id center_x center_y width height (normalized coordinates 0-1)
        
        Returns:
            Dict with import statistics
        """
        try:
            # Handle ZIP file
            if dataset_path.suffix == '.zip':
                return YOLOImporter._import_from_zip(project_id, dataset_path)
            
            # Handle directory
            images_dir = dataset_path / "images"
            if not images_dir.exists():
                # Try train/val/test structure
                for split in ["train", "val", "test"]:
                    potential_images_dir = dataset_path / split / "images"
                    if potential_images_dir.exists():
                        images_dir = potential_images_dir
                        break
                
                if not images_dir.exists():
                    # Assume images are in root
                    images_dir = dataset_path
            
            labels_dir = dataset_path / "labels"
            if not labels_dir.exists():
                # Try train/val/test structure
                for split in ["train", "val", "test"]:
                    potential_labels_dir = dataset_path / split / "labels"
                    if potential_labels_dir.exists():
                        labels_dir = potential_labels_dir
                        break
            
            # Read classes file
            classes_file = dataset_path / "classes.txt"
            if not classes_file.exists():
                classes_file = dataset_path / "names.txt"
            if not classes_file.exists():
                classes_file = dataset_path.parent / "classes.txt"
            
            classes = []
            if classes_file.exists():
                classes = [line.strip() for line in classes_file.read_text().splitlines() if line.strip()]
            
            # Get all images
            image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
            image_files = [f for f in images_dir.iterdir() if f.suffix.lower() in image_extensions]
            
            result = {
                "images": [],
                "categories": [],
                "labels_dir": str(labels_dir) if labels_dir.exists() else None,
                "images_dir": str(images_dir)
            }
            
            # Create categories from classes file
            for idx, class_name in enumerate(classes):
                result["categories"].append({
                    "id": idx,
                    "name": class_name
                })
            
            # Process images
            for img_file in image_files:
                # Get image dimensions
                try:
                    img = Image.open(img_file)
                    width, height = img.size
                except Exception as e:
                    logger.warning(f"[Dataset Import] Failed to read image {img_file}: {e}")
                    continue
                
                img_info = {
                    "file_name": img_file.name,
                    "width": width,
                    "height": height,
                    "annotations": []
                }
                
                # Read corresponding label file
                if labels_dir and labels_dir.exists():
                    label_file = labels_dir / f"{img_file.stem}.txt"
                    if label_file.exists():
                        label_lines = label_file.read_text().splitlines()
                        for line in label_lines:
                            parts = line.strip().split()
                            if len(parts) >= 5:
                                try:
                                    class_id = int(parts[0])
                                    center_x = float(parts[1])
                                    center_y = float(parts[2])
                                    w = float(parts[3])
                                    h = float(parts[4])
                                    
                                    # Convert YOLO format (normalized center, width, height) to our format (x_min, y_min, x_max, y_max)
                                    x_min = (center_x - w / 2) * width
                                    y_min = (center_y - h / 2) * height
                                    x_max = (center_x + w / 2) * width
                                    y_max = (center_y + h / 2) * height
                                    
                                    # Ensure class exists
                                    if class_id >= len(classes):
                                        # Auto-create class name if not in classes.txt
                                        while len(classes) <= class_id:
                                            classes.append(f"class_{len(classes)}")
                                        result["categories"] = [
                                            {"id": idx, "name": name}
                                            for idx, name in enumerate(classes)
                                        ]
                                    
                                    class_name = classes[class_id] if class_id < len(classes) else f"class_{class_id}"
                                    annotation_data = {
                                        "x_min": float(x_min),
                                        "y_min": float(y_min),
                                        "x_max": float(x_max),
                                        "y_max": float(y_max)
                                    }
                                    img_info["annotations"].append({
                                        "category_id": class_id,
                                        "category_name": class_name,
                                        "data": annotation_data,
                                        "type": "bbox"
                                    })
                                except (ValueError, IndexError) as e:
                                    logger.warning(f"[Dataset Import] Failed to parse label line in {label_file}: {line} - {e}")
                
                result["images"].append(img_info)
            
            return result
            
        except Exception as e:
            logger.error(f"[Dataset Import] Failed to parse YOLO format: {e}", exc_info=True)
            raise ValueError(f"Failed to parse YOLO format: {str(e)}")
    
    @staticmethod
    def _import_from_zip(project_id: str, zip_path: Path) -> Dict:
        """Extract and import YOLO dataset from ZIP file"""
        import tempfile
        import shutil
        
        temp_dir = Path(tempfile.mkdtemp())
        try:
            # Extract ZIP
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            
            # Find dataset root (might be in a subdirectory)
            dataset_root = temp_dir
            if len(list(temp_dir.iterdir())) == 1:
                subdir = next(temp_dir.iterdir())
                if subdir.is_dir():
                    dataset_root = subdir
            
            # Import from extracted directory
            return YOLOImporter.import_dataset(project_id, dataset_root)
        finally:
            # Cleanup
            shutil.rmtree(temp_dir, ignore_errors=True)
