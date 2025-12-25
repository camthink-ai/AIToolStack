import React, { useRef, useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { ToolType, Annotation, ImageInfo, Class } from './AnnotationWorkbench';
import { API_BASE_URL } from '../config';
import { IoWarning } from 'react-icons/io5';
import './AnnotationCanvas.css';
import { Alert } from '../ui/Alert';
import { useAlert } from '../hooks/useAlert';

// Icon component wrapper to resolve TypeScript type issues
const Icon: React.FC<{ component: React.ComponentType<any> }> = ({ component: Component }) => {
  return <Component />;
};

interface AnnotationCanvasProps {
  image: ImageInfo;
  annotations: Annotation[];
  tool: ToolType;
  classes: Class[];
  selectedAnnotationId: number | null;
  selectedClassId: number | null;
  showAnnotations: boolean;
  onAnnotationCreate: (annotation: Annotation) => void;
  onAnnotationUpdate: (id: number, updates: Partial<Annotation>) => void;
  onAnnotationSelect: (id: number | null) => void;
  projectId: string;
}

interface Point {
  x: number;
  y: number;
}

export const AnnotationCanvas: React.FC<AnnotationCanvasProps> = ({
  image,
  annotations,
  tool,
  classes,
  selectedAnnotationId,
  selectedClassId,
  showAnnotations,
  onAnnotationCreate,
  onAnnotationUpdate,
  onAnnotationSelect,
  projectId
}) => {
  const { t } = useTranslation();
  const { alertState, showWarning, closeAlert } = useAlert();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState<Point>({ x: 0, y: 0 });
  const [mousePos, setMousePos] = useState<Point>({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState<Point>({ x: 0, y: 0 });
  
  // Bounding box drawing state
  const [bboxStart, setBboxStart] = useState<Point | null>(null);
  const [bboxCurrent, setBboxCurrent] = useState<Point | null>(null);
  
  // Polygon drawing state
  const [polygonPoints, setPolygonPoints] = useState<Point[]>([]);
  const [isDrawingPolygon, setIsDrawingPolygon] = useState(false);
  
  // Keypoint drawing state
  const [keypoints, setKeypoints] = useState<Point[]>([]);
  const [isDrawingKeypoints, setIsDrawingKeypoints] = useState(false);
  
  // Annotation editing state
  const [isDraggingAnnotation, setIsDraggingAnnotation] = useState(false);
  const [dragStart, setDragStart] = useState<Point | null>(null);
  const [dragOffset, setDragOffset] = useState<Point | null>(null);
  const [draggedAnnotationId, setDraggedAnnotationId] = useState<number | null>(null);
  
  // Bbox resize state (dragging corner or edge)
  const [isResizingBbox, setIsResizingBbox] = useState(false);
  const [resizeHandle, setResizeHandle] = useState<string | null>(null); // 'nw', 'ne', 'sw', 'se', 'n', 's', 'e', 'w'
  
  // Polygon/Keypoint adjust state (dragging vertex/point)
  const [draggedPointIndex, setDraggedPointIndex] = useState<number | null>(null);

  // Hover state for changing cursor
  const [hoverResizeHandle, setHoverResizeHandle] = useState<string | null>(null);
  const [hoverPointIndex, setHoverPointIndex] = useState<number | null>(null);
  
  const [isSpacePressed, setIsSpacePressed] = useState(false);
  
  // If no class selected and classes exist, default to first class
  const effectiveClassId = selectedClassId || (classes.length > 0 ? classes[0].id : null);
  const [imageLoading, setImageLoading] = useState(true);
  const [imageError, setImageError] = useState<string | null>(null);

  // Load image
  useEffect(() => {
    setImageLoading(true);
    setImageError(null);
    
    const img = new Image();
    img.crossOrigin = 'anonymous'; // Allow cross-origin loading
    
    img.onload = () => {
      console.log('[Canvas] Image loaded:', img.width, 'x', img.height);
      imageRef.current = img;
      setImageLoading(false);
      setImageError(null);
      
      // Calculate initial scale and offset to center image with padding
      if (containerRef.current) {
        const container = containerRef.current;
        const containerWidth = container.clientWidth;
        const containerHeight = container.clientHeight;
        
        // Add padding: 60px on each side for better visual spacing
        const padding = 60;
        const availableWidth = containerWidth - padding * 2;
        const availableHeight = containerHeight - padding * 2;
        
        const scaleX = availableWidth / img.width;
        const scaleY = availableHeight / img.height;
        // 为了保证不同尺寸图片在 UI 上占用的宽度基本一致，这里允许适度放大图片以适配容器
        // 不再强制限制 initialScale <= 1
        const initialScale = Math.min(scaleX, scaleY);
        
        setScale(initialScale);
        setOffset({
          x: (containerWidth - img.width * initialScale) / 2,
          y: (containerHeight - img.height * initialScale) / 2
        });
      }
      // Use setTimeout to ensure draw is called in next frame, avoid dependency cycle
      setTimeout(() => {
        if (imageRef.current) {
          draw();
        }
      }, 0);
    };
    
    img.onerror = (error) => {
      console.error('[Canvas] Image load error:', error);
      setImageLoading(false);
      // Build image URL for error message
      let imagePath = image.path;
      if (!imagePath.includes('raw/')) {
        imagePath = `raw/${imagePath}`;
      } else if (imagePath.startsWith(projectId + '/')) {
        const rawIndex = imagePath.indexOf('raw/');
        if (rawIndex !== -1) {
          imagePath = imagePath.substring(rawIndex);
        }
      }
      const imageUrl = image.path.startsWith('http') 
        ? image.path 
        : `${API_BASE_URL.replace('/api', '')}/images/${projectId}/${imagePath}`;
      setImageError(`Image load failed: ${imageUrl}`);
    };
    
    // Build image URL
    // image.path format should be raw/filename
    let imagePath = image.path;
    // If path doesn't contain raw/, add it (for compatibility with old data)
    if (!imagePath.includes('raw/')) {
      imagePath = `raw/${imagePath}`;
    } else if (imagePath.startsWith(projectId + '/')) {
      // If contains project_id, remove it
      const rawIndex = imagePath.indexOf('raw/');
      if (rawIndex !== -1) {
        imagePath = imagePath.substring(rawIndex);
      }
    }
    // API_BASE_URL is already http://localhost:8000/api
    // Image service path is /api/images/{project_id}/{image_path}
    const imageUrl = image.path.startsWith('http') 
      ? image.path 
      : `${API_BASE_URL}/images/${projectId}/${imagePath}`;
    
    console.log('[Canvas] Loading image:', imageUrl);
    img.src = imageUrl;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [image, projectId]);

  // Coordinate conversion: screen coordinates -> image coordinates
  const screenToImage = useCallback((screenX: number, screenY: number): Point => {
    if (!imageRef.current) return { x: 0, y: 0 };
    
    const x = (screenX - offset.x) / scale;
    const y = (screenY - offset.y) / scale;
    
    return {
      x: Math.max(0, Math.min(imageRef.current.width, x)),
      y: Math.max(0, Math.min(imageRef.current.height, y))
    };
  }, [offset, scale]);

  // Coordinate conversion: image coordinates -> screen coordinates (reserved feature)
  // const imageToScreen = useCallback((imageX: number, imageY: number): Point => {
  //   return {
  //     x: imageX * scale + offset.x,
  //     y: imageY * scale + offset.y
  //   };
  // }, [offset, scale]);

  // Get which region of bbox the click position is on (for resizing)
  const getBboxHandle = useCallback((bbox: { x_min: number; y_min: number; x_max: number; y_max: number }, point: Point): string | null => {
    const x1 = bbox.x_min * scale + offset.x;
    const y1 = bbox.y_min * scale + offset.y;
    const x2 = bbox.x_max * scale + offset.x;
    const y2 = bbox.y_max * scale + offset.y;
    const threshold = 15; // Increase threshold to match enlarged handle size
    
    const screenX = point.x * scale + offset.x;
    const screenY = point.y * scale + offset.y;
    
    // Check four corners
    if (Math.abs(screenX - x1) < threshold && Math.abs(screenY - y1) < threshold) return 'nw';
    if (Math.abs(screenX - x2) < threshold && Math.abs(screenY - y1) < threshold) return 'ne';
    if (Math.abs(screenX - x1) < threshold && Math.abs(screenY - y2) < threshold) return 'sw';
    if (Math.abs(screenX - x2) < threshold && Math.abs(screenY - y2) < threshold) return 'se';
    
    // Check edges
    if (Math.abs(screenX - (x1 + x2) / 2) < threshold && Math.abs(screenY - y1) < threshold) return 'n';
    if (Math.abs(screenX - (x1 + x2) / 2) < threshold && Math.abs(screenY - y2) < threshold) return 's';
    if (Math.abs(screenX - x1) < threshold && Math.abs(screenY - (y1 + y2) / 2) < threshold) return 'w';
    if (Math.abs(screenX - x2) < threshold && Math.abs(screenY - (y1 + y2) / 2) < threshold) return 'e';
    
    return null;
  }, [offset, scale]);

  // Get which point of polygon/keypoints the click position is on
  // Use image coordinate distance threshold (in pixels), threshold should be large enough to match enlarged point size
  const getClickedPointIndex = useCallback((points: Point[], clickPoint: Point, threshold: number = 20): number | null => {
    let minDistance = Infinity;
    let closestIndex: number | null = null;
    
    for (let i = 0; i < points.length; i++) {
      const distance = Math.sqrt(
        Math.pow(clickPoint.x - points[i].x, 2) + Math.pow(clickPoint.y - points[i].y, 2)
      );
      // Find closest point
      if (distance < minDistance) {
        minDistance = distance;
        closestIndex = i;
      }
    }
    
    // If closest point is within threshold (image coordinates), return index
    if (closestIndex !== null && minDistance < threshold) {
      return closestIndex;
    }
    
    return null;
  }, []);

  // Draw function
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    
    if (!imageRef.current) {
      // If image not loaded yet, only clear canvas
      const ctx = canvas.getContext('2d');
      if (!ctx || !containerRef.current) return;
      canvas.width = containerRef.current.clientWidth;
      canvas.height = containerRef.current.clientHeight;
      ctx.fillStyle = '#1a1a1a';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      return;
    }

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const img = imageRef.current;
    const container = containerRef.current;
    if (!container) return;

    // Set canvas size
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;

    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw image
    ctx.save();
    ctx.translate(offset.x, offset.y);
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0);
    ctx.restore();

    // Draw annotations
    if (showAnnotations) {
      annotations.forEach((ann) => {
        const classObj = classes.find(c => c.id === ann.classId);
        const color = classObj?.color || '#EB814F';
        const isSelected = ann.id === selectedAnnotationId;

        ctx.strokeStyle = isSelected ? '#ffff00' : color;
        ctx.lineWidth = isSelected ? 3 : 2;
        ctx.fillStyle = color + '33'; // Semi-transparent fill

        if (ann.type === 'bbox') {
          const data = ann.data;
          const x1 = data.x_min * scale + offset.x;
          const y1 = data.y_min * scale + offset.y;
          const x2 = data.x_max * scale + offset.x;
          const y2 = data.y_max * scale + offset.y;
          const width = x2 - x1;
          const height = y2 - y1;
          
          ctx.fillRect(x1, y1, width, height);
          ctx.strokeRect(x1, y1, width, height);
          
          // If selected, draw resize handles (increased size)
          if (isSelected && tool === 'select') {
            const handleSize = 12; // Increased from 8 to 12
            const handles = [
              { x: x1, y: y1, type: 'nw' },
              { x: x2, y: y1, type: 'ne' },
              { x: x1, y: y2, type: 'sw' },
              { x: x2, y: y2, type: 'se' },
              { x: (x1 + x2) / 2, y: y1, type: 'n' },
              { x: (x1 + x2) / 2, y: y2, type: 's' },
              { x: x1, y: (y1 + y2) / 2, type: 'w' },
              { x: x2, y: (y1 + y2) / 2, type: 'e' }
            ];
            
            handles.forEach(handle => {
              const isHovered = hoverResizeHandle === handle.type && ann.id === selectedAnnotationId;
              ctx.fillStyle = isHovered ? '#ffaa00' : '#ffff00'; // Use deeper yellow on hover
              ctx.strokeStyle = '#000000';
              ctx.lineWidth = isHovered ? 3 : 2; // Thicker border on hover
              const size = isHovered ? handleSize + 2 : handleSize; // Slightly larger on hover
              ctx.fillRect(handle.x - size / 2, handle.y - size / 2, size, size);
              ctx.strokeRect(handle.x - size / 2, handle.y - size / 2, size, size);
            });
          }
        } else if (ann.type === 'polygon') {
          const points = ann.data.points || [];
          if (points.length >= 3) {
            ctx.beginPath();
            ctx.moveTo(points[0].x * scale + offset.x, points[0].y * scale + offset.y);
            for (let i = 1; i < points.length; i++) {
              ctx.lineTo(points[i].x * scale + offset.x, points[i].y * scale + offset.y);
            }
            ctx.closePath();
            ctx.fill();
            ctx.stroke();
            
            // If selected, draw vertex handles (increased size)
            if (isSelected && tool === 'select') {
              points.forEach((point: any, index: number) => {
                const px = (point.x || point[0]) * scale + offset.x;
                const py = (point.y || point[1]) * scale + offset.y;
                const isHovered = hoverPointIndex === index && ann.id === selectedAnnotationId;
                ctx.fillStyle = isHovered ? '#ffaa00' : '#ffff00'; // Use deeper yellow on hover
                ctx.strokeStyle = '#000000';
                ctx.lineWidth = isHovered ? 3 : 2; // Thicker border on hover
                const radius = isHovered ? 11 : 9; // Slightly larger on hover
                ctx.beginPath();
                ctx.arc(px, py, radius, 0, Math.PI * 2);
                ctx.fill();
                ctx.stroke();
              });
            }
          }
        } else if (ann.type === 'keypoint') {
          const points = ann.data.points || [];
          points.forEach((point: any, index: number) => {
            const x = (point.x || point[0]) * scale + offset.x;
            const y = (point.y || point[1]) * scale + offset.y;
            
            const isHovered = hoverPointIndex === index && ann.id === selectedAnnotationId && tool === 'select';
            ctx.fillStyle = isHovered ? '#ffaa00' : (isSelected ? '#ffff00' : color);
            ctx.strokeStyle = isSelected ? '#000000' : '#ffffff';
            ctx.lineWidth = isHovered ? 3 : (isSelected ? 2 : 1);
            // Increase keypoint size when selected
            const radius = isHovered ? 12 : (isSelected && tool === 'select' ? 10 : 5);
            ctx.beginPath();
            ctx.arc(x, y, radius, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
          });
        }
      });
    }

    // Draw bounding box being drawn
    if (bboxStart && bboxCurrent && tool === 'bbox') {
      ctx.strokeStyle = '#4a9eff';
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 5]);
      const x1 = bboxStart.x * scale + offset.x;
      const y1 = bboxStart.y * scale + offset.y;
      const x2 = bboxCurrent.x * scale + offset.x;
      const y2 = bboxCurrent.y * scale + offset.y;
      ctx.strokeRect(
        Math.min(x1, x2),
        Math.min(y1, y2),
        Math.abs(x2 - x1),
        Math.abs(y2 - y1)
      );
      ctx.setLineDash([]);
    }

    // Draw polygon being drawn
    if (polygonPoints.length > 0 && tool === 'polygon' && isDrawingPolygon) {
      // Draw confirmed edges
      if (polygonPoints.length > 1) {
        ctx.strokeStyle = '#4a9eff';
        ctx.lineWidth = 2;
        ctx.setLineDash([]);
        ctx.beginPath();
        polygonPoints.forEach((point, index) => {
          const x = point.x * scale + offset.x;
          const y = point.y * scale + offset.y;
          if (index === 0) {
            ctx.moveTo(x, y);
          } else {
            ctx.lineTo(x, y);
          }
        });
        ctx.stroke();
      }
      
      // Draw preview line: from last point to mouse position
      if (polygonPoints.length > 0) {
        const lastPoint = polygonPoints[polygonPoints.length - 1];
        const lastX = lastPoint.x * scale + offset.x;
        const lastY = lastPoint.y * scale + offset.y;
        
        ctx.strokeStyle = '#4a9eff';
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 5]);
        ctx.beginPath();
        ctx.moveTo(lastX, lastY);
        ctx.lineTo(mousePos.x, mousePos.y);
        
        // If points >= 3, draw preview line back to start point (close preview)
        if (polygonPoints.length >= 3) {
          const firstPoint = polygonPoints[0];
          const firstX = firstPoint.x * scale + offset.x;
          const firstY = firstPoint.y * scale + offset.y;
          ctx.lineTo(firstX, firstY);
        }
        
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Draw placed points (increased size)
      polygonPoints.forEach((point, index) => {
        const x = point.x * scale + offset.x;
        const y = point.y * scale + offset.y;
        ctx.fillStyle = index === 0 ? '#ffff00' : '#EB814F'; // Highlight start point with yellow
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2; // Increase border width
        ctx.beginPath();
        ctx.arc(x, y, 7, 0, Math.PI * 2); // Increased from 5 to 7
        ctx.fill();
        ctx.stroke();
      });
      
      // Show preview point at mouse position
      ctx.fillStyle = '#EB814F';
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(mousePos.x, mousePos.y, 5, 0, Math.PI * 2); // Increased from 4 to 5
      ctx.fill();
      ctx.stroke();
    }

    // Draw keypoints being drawn (increased size)
    if (keypoints.length > 0 && tool === 'keypoint') {
      keypoints.forEach((point) => {
        const x = point.x * scale + offset.x;
        const y = point.y * scale + offset.y;
        ctx.fillStyle = '#EB814F';
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, 7, 0, Math.PI * 2); // Increased from 5 to 7
        ctx.fill();
        ctx.stroke();
      });
      
      // If multiple keypoints, draw connection lines (optional)
      if (keypoints.length > 1 && isDrawingKeypoints) {
        ctx.strokeStyle = '#4a9eff';
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        keypoints.forEach((point, index) => {
          const x = point.x * scale + offset.x;
          const y = point.y * scale + offset.y;
          if (index === 0) {
            ctx.moveTo(x, y);
          } else {
            ctx.lineTo(x, y);
          }
        });
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // Draw crosshair
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(mousePos.x - 20, mousePos.y);
    ctx.lineTo(mousePos.x + 20, mousePos.y);
    ctx.moveTo(mousePos.x, mousePos.y - 20);
    ctx.lineTo(mousePos.x, mousePos.y + 20);
    ctx.stroke();
    ctx.setLineDash([]);
  }, [
    annotations,
    tool,
    classes,
    selectedAnnotationId,
    showAnnotations,
    scale,
    offset,
    mousePos,
    bboxStart,
    bboxCurrent,
    polygonPoints,
    isDrawingPolygon,
    keypoints,
    isDrawingKeypoints,
    hoverResizeHandle,
    hoverPointIndex
  ]);

  useEffect(() => {
    draw();
  }, [draw]);

  // Mouse event handlers
  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;

    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    setMousePos({ x, y });

    if (isPanning) {
      const dx = x - panStart.x;
      const dy = y - panStart.y;
      setOffset(prev => ({
        x: prev.x + dx,
        y: prev.y + dy
      }));
      setPanStart({ x, y });
    } else if (isDraggingAnnotation && draggedAnnotationId) {
      // Drag and move annotation
      const imgPoint = screenToImage(x, y);
      if (dragOffset && dragStart) {
        const dx = imgPoint.x - dragStart.x;
        const dy = imgPoint.y - dragStart.y;
        
        const annotation = annotations.find(a => a.id === draggedAnnotationId);
        if (annotation) {
          if (annotation.type === 'bbox') {
            const data = annotation.data as { x_min: number; y_min: number; x_max: number; y_max: number };
            const newData = {
              x_min: data.x_min + dx,
              y_min: data.y_min + dy,
              x_max: data.x_max + dx,
              y_max: data.y_max + dy
            };
            // Limit within image bounds
            if (imageRef.current) {
              newData.x_min = Math.max(0, Math.min(imageRef.current.width, newData.x_min));
              newData.y_min = Math.max(0, Math.min(imageRef.current.height, newData.y_min));
              newData.x_max = Math.max(0, Math.min(imageRef.current.width, newData.x_max));
              newData.y_max = Math.max(0, Math.min(imageRef.current.height, newData.y_max));
            }
            onAnnotationUpdate(draggedAnnotationId, { data: newData });
          } else if (annotation.type === 'polygon') {
            const points = (annotation.data.points || []).map((p: any) => ({
              x: (p.x || p[0]) + dx,
              y: (p.y || p[1]) + dy
            }));
            // Limit within image bounds
            if (imageRef.current) {
              points.forEach((p: Point) => {
                p.x = Math.max(0, Math.min(imageRef.current!.width, p.x));
                p.y = Math.max(0, Math.min(imageRef.current!.height, p.y));
              });
            }
            onAnnotationUpdate(draggedAnnotationId, { data: { points } });
          } else if (annotation.type === 'keypoint') {
            const points = (annotation.data.points || []).map((p: any) => ({
              x: (p.x || p[0]) + dx,
              y: (p.y || p[1]) + dy
            }));
            // Limit within image bounds
            if (imageRef.current) {
              points.forEach((p: Point) => {
                p.x = Math.max(0, Math.min(imageRef.current!.width, p.x));
                p.y = Math.max(0, Math.min(imageRef.current!.height, p.y));
              });
            }
            onAnnotationUpdate(draggedAnnotationId, { data: { points } });
          }
        }
        setDragStart(imgPoint);
      }
    } else if (isResizingBbox && draggedAnnotationId && resizeHandle) {
      // Resize bbox
      const imgPoint = screenToImage(x, y);
      const annotation = annotations.find(a => a.id === draggedAnnotationId);
      if (annotation && annotation.type === 'bbox') {
        const data = annotation.data as { x_min: number; y_min: number; x_max: number; y_max: number };
        let newData = { ...data };
        
        if (resizeHandle.includes('w')) newData.x_min = imgPoint.x;
        if (resizeHandle.includes('e')) newData.x_max = imgPoint.x;
        if (resizeHandle.includes('n')) newData.y_min = imgPoint.y;
        if (resizeHandle.includes('s')) newData.y_max = imgPoint.y;
        
        // Ensure minimum size and bounds
        if (imageRef.current) {
          newData.x_min = Math.max(0, Math.min(imageRef.current.width, newData.x_min));
          newData.y_min = Math.max(0, Math.min(imageRef.current.height, newData.y_min));
          newData.x_max = Math.max(0, Math.min(imageRef.current.width, newData.x_max));
          newData.y_max = Math.max(0, Math.min(imageRef.current.height, newData.y_max));
          
          if (newData.x_max <= newData.x_min) newData.x_max = newData.x_min + 10;
          if (newData.y_max <= newData.y_min) newData.y_max = newData.y_min + 10;
        }
        
        onAnnotationUpdate(draggedAnnotationId, { data: newData });
      }
    } else if (draggedPointIndex !== null && draggedAnnotationId) {
      // Drag polygon vertex or keypoint
      const imgPoint = screenToImage(x, y);
      const annotation = annotations.find(a => a.id === draggedAnnotationId);
      if (annotation && (annotation.type === 'polygon' || annotation.type === 'keypoint')) {
        const points = [...(annotation.data.points || [])];
        if (points[draggedPointIndex] !== undefined) {
          // Limit within image bounds
          if (imageRef.current) {
            imgPoint.x = Math.max(0, Math.min(imageRef.current.width, imgPoint.x));
            imgPoint.y = Math.max(0, Math.min(imageRef.current.height, imgPoint.y));
          }
          points[draggedPointIndex] = imgPoint;
          onAnnotationUpdate(draggedAnnotationId, { data: { points } });
        }
      }
    } else if (bboxStart && tool === 'bbox') {
      const imgPoint = screenToImage(x, y);
      setBboxCurrent(imgPoint);
    } else if (tool === 'polygon' && polygonPoints.length > 0) {
      draw(); // Redraw to show preview line
    } else if (tool === 'keypoint' && isDrawingKeypoints) {
      draw(); // Redraw keypoints
    }

    // Hover handling (only in select mode, for cursor feedback)
    if (tool === 'select' && !isPanning && !isDraggingAnnotation && !isResizingBbox && draggedPointIndex === null) {
      const imgPoint = screenToImage(x, y);
      let hoverHandle: string | null = null;
      let hoverPoint: number | null = null;
      const selectedAnn = annotations.find(a => a.id === selectedAnnotationId);
      if (selectedAnn) {
        if (selectedAnn.type === 'bbox') {
          const data = selectedAnn.data as { x_min: number; y_min: number; x_max: number; y_max: number };
          hoverHandle = getBboxHandle(data, imgPoint);
        } else if (selectedAnn.type === 'polygon') {
          const points = (selectedAnn.data.points || []).map((p: any) => ({
            x: p.x || p[0],
            y: p.y || p[1]
          }));
          hoverPoint = getClickedPointIndex(points, imgPoint);
        } else if (selectedAnn.type === 'keypoint') {
          const points = (selectedAnn.data.points || []).map((p: any) => ({
            x: p.x || p[0],
            y: p.y || p[1]
          }));
          hoverPoint = getClickedPointIndex(points, imgPoint);
        }
      }
      setHoverResizeHandle(hoverHandle);
      setHoverPointIndex(hoverPoint);
    } else {
      setHoverResizeHandle(null);
      setHoverPointIndex(null);
    }
  };

  const handleMouseDown = async (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (isSpacePressed || e.button === 1) {
      // Start panning
      setIsPanning(true);
      setPanStart({ x: e.clientX, y: e.clientY });
      return;
    }

    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;

    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const imgPoint = screenToImage(x, y);

    if (tool === 'select') {
      // Check if clicked on annotation
      let clickedAnnotation: Annotation | null = null;
      let clickedHandle: string | null = null;
      let clickedPointIndex: number | null = null;
      
      // Prioritize checking edit points (handles and vertices) of selected annotation
      if (selectedAnnotationId) {
        const selectedAnn = annotations.find(a => a.id === selectedAnnotationId);
        if (selectedAnn) {
          if (selectedAnn.type === 'bbox') {
            const data = selectedAnn.data as { x_min: number; y_min: number; x_max: number; y_max: number };
            clickedHandle = getBboxHandle(data, imgPoint);
            if (clickedHandle) {
              clickedAnnotation = selectedAnn;
            }
          } else if (selectedAnn.type === 'polygon') {
            const points = (selectedAnn.data.points || []).map((p: any) => ({
              x: p.x || p[0],
              y: p.y || p[1]
            }));
            clickedPointIndex = getClickedPointIndex(points, imgPoint);
            if (clickedPointIndex !== null) {
              clickedAnnotation = selectedAnn;
            }
          } else if (selectedAnn.type === 'keypoint') {
            const points = (selectedAnn.data.points || []).map((p: any) => ({
              x: p.x || p[0],
              y: p.y || p[1]
            }));
            clickedPointIndex = getClickedPointIndex(points, imgPoint);
            if (clickedPointIndex !== null) {
              clickedAnnotation = selectedAnn;
            }
          }
        }
      }
      
      // If didn't click on edit point, check if clicked on annotation itself
      if (!clickedHandle && clickedPointIndex === null) {
        // Check from back to front in Z-order (later drawn ones are on top)
        for (let i = annotations.length - 1; i >= 0; i--) {
          const ann = annotations[i];
          
          if (ann.type === 'bbox') {
            const data = ann.data as { x_min: number; y_min: number; x_max: number; y_max: number };
            const isInside = imgPoint.x >= data.x_min && imgPoint.x <= data.x_max &&
                            imgPoint.y >= data.y_min && imgPoint.y <= data.y_max;
            
            if (isInside) {
              clickedAnnotation = ann;
              break;
            }
          } else if (ann.type === 'polygon') {
            const points = ann.data.points || [];
            if (points.length >= 3) {
              // Check if inside polygon
              let inside = false;
              for (let j = 0, k = points.length - 1; j < points.length; k = j++) {
                const p1 = points[j];
                const p2 = points[k];
                const xi = p1.x || p1[0];
                const yi = p1.y || p1[1];
                const xj = p2.x || p2[0];
                const yj = p2.y || p2[1];
                const intersect = ((yi > imgPoint.y) !== (yj > imgPoint.y)) &&
                  (imgPoint.x < (xj - xi) * (imgPoint.y - yi) / (yj - yi) + xi);
                if (intersect) inside = !inside;
              }
              
              if (inside) {
                clickedAnnotation = ann;
                break;
              }
            }
          } else if (ann.type === 'keypoint') {
            const points = ann.data.points || [];
            // Check if clicked near keypoint (can click point to select even when not selected)
            const pointIndex = getClickedPointIndex(points.map((p: any) => ({
              x: p.x || p[0],
              y: p.y || p[1]
            })), imgPoint);
            
            if (pointIndex !== null) {
              clickedAnnotation = ann;
              clickedPointIndex = pointIndex;
              break;
            }
          }
        }
      }
      
      // Handle click - prioritize edit points
      if (clickedHandle && clickedAnnotation) {
        // Start resizing bbox
        setIsResizingBbox(true);
        setResizeHandle(clickedHandle);
        setDraggedAnnotationId(clickedAnnotation.id!);
        setIsDraggingAnnotation(false); // Not moving
        setDraggedPointIndex(null);
        onAnnotationSelect(clickedAnnotation.id!);
      } else if (clickedPointIndex !== null && clickedAnnotation) {
        // Start dragging vertex/point (not moving entire annotation)
        // Set drag state immediately to ensure immediate response to mouse movement after click
        setDraggedPointIndex(clickedPointIndex);
        setDraggedAnnotationId(clickedAnnotation.id!);
        setIsDraggingAnnotation(false);
        setIsResizingBbox(false);
        setResizeHandle(null);
        // Record initial click position for subsequent drag calculations
        setDragStart(imgPoint);
        setDragOffset(imgPoint);
        onAnnotationSelect(clickedAnnotation.id!);
      } else if (clickedAnnotation && clickedAnnotation.id) {
        // Select annotation (clicked annotation but not edit point)
        const wasAlreadySelected = clickedAnnotation.id === selectedAnnotationId;
        onAnnotationSelect(clickedAnnotation.id);
        
        // Only prepare to move when clicking center area (not clicking handle or vertex)
        if (wasAlreadySelected && !clickedHandle && clickedPointIndex === null) {
          // Click again on already selected annotation, prepare to move
          setDraggedAnnotationId(clickedAnnotation.id);
          setIsDraggingAnnotation(true);
          setDragStart(imgPoint);
          setDragOffset(imgPoint);
        } else {
          // Newly selected, don't move immediately
          setIsDraggingAnnotation(false);
          setIsResizingBbox(false);
          setDraggedPointIndex(null);
          setResizeHandle(null);
        }
      } else {
        // Clicked blank area
        exitEditMode(false, true);
      }
    } else if (tool === 'bbox') {
      if (!effectiveClassId) {
        showWarning(t('annotation.selectClassFirst', '请先选择类别'));
        return;
      }
      if (!bboxStart) {
        setBboxStart(imgPoint);
        setBboxCurrent(imgPoint);
      }
    } else if (tool === 'polygon') {
      if (!effectiveClassId) {
        showWarning(t('annotation.selectClassFirst', '请先选择类别'));
        return;
      }
      if (!isDrawingPolygon) {
        setIsDrawingPolygon(true);
        setPolygonPoints([imgPoint]);
      } else {
        // Check if clicked on starting point (close polygon)
        const firstPoint = polygonPoints[0];
        const distance = Math.sqrt(
          Math.pow(imgPoint.x - firstPoint.x, 2) + Math.pow(imgPoint.y - firstPoint.y, 2)
        );
        
        // Use distance threshold in image coordinates (approximately 15 pixels, adjusted for zoom)
        const threshold = 15;
        if (distance < threshold && polygonPoints.length >= 3) {
          // Close polygon, don't add new point, finish directly
          await finishPolygon();
        } else {
          // Add new point
          setPolygonPoints(prev => [...prev, imgPoint]);
        }
      }
    } else if (tool === 'keypoint') {
      if (!effectiveClassId) {
        showWarning(t('annotation.selectClassFirst', '请先选择类别'));
        return;
      }
      // Add keypoint
      setKeypoints(prev => [...prev, imgPoint]);
      if (!isDrawingKeypoints) {
        setIsDrawingKeypoints(true);
      }
    }
  };

  // Exit edit mode, optionally save and/or clear selection
  const exitEditMode = (clearSelection = false, saveCurrent = true) => {
    if (saveCurrent && draggedAnnotationId) {
      const annotation = annotations.find(a => a.id === draggedAnnotationId);
      if (annotation) {
        // Ensure data is object format
        const dataObj = typeof annotation.data === 'string'
          ? JSON.parse(annotation.data)
          : annotation.data;

        fetch(`${API_BASE_URL}/annotations/${draggedAnnotationId}`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            data: dataObj,
            class_id: annotation.classId
          }),
        }).catch(error => {
          console.error('Failed to save annotation:', error);
        });
      }
    }

    setIsDraggingAnnotation(false);
    setIsResizingBbox(false);
    setDraggedPointIndex(null);
    setResizeHandle(null);
    setDragStart(null);
    setDragOffset(null);
    if (clearSelection) {
      onAnnotationSelect(null);
      setDraggedAnnotationId(null);
    }
  };

  const handleMouseUp = async (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (isPanning) {
      setIsPanning(false);
      return;
    }

    // Right-click to cancel current drawing
    if (e.button === 2) {
      if (tool === 'bbox') {
        setBboxStart(null);
        setBboxCurrent(null);
      } else if (tool === 'polygon' && isDrawingPolygon) {
        setPolygonPoints([]);
        setIsDrawingPolygon(false);
      } else if (tool === 'keypoint' && isDrawingKeypoints) {
        // Right-click to cancel keypoint drawing (don't save)
        setKeypoints([]);
        setIsDrawingKeypoints(false);
      }
      return;
    }

    // End dragging or resizing
    if (isDraggingAnnotation || isResizingBbox || draggedPointIndex !== null) {
      exitEditMode(false, true);
      return;
    }

    if (tool === 'bbox' && bboxStart && bboxCurrent) {
      await finishBbox();
    }
  };

  // Double-click: prioritize finishing drawing, then exit edit mode
  const handleDoubleClick = () => {
    if (tool === 'polygon' && isDrawingPolygon && polygonPoints.length >= 3) {
      finishPolygon();
      return;
    }
    if (tool === 'keypoint' && isDrawingKeypoints && keypoints.length > 0) {
      finishKeypoints();
      return;
    }

    // In non-drawing state, double-click to exit edit mode and clear temporary drawing
    exitEditMode(true, true);
    setBboxStart(null);
    setBboxCurrent(null);
    setPolygonPoints([]);
    setIsDrawingPolygon(false);
    setKeypoints([]);
    setIsDrawingKeypoints(false);
  };

  const finishBbox = async () => {
    if (!bboxStart || !bboxCurrent || !effectiveClassId) {
      setBboxStart(null);
      setBboxCurrent(null);
      return;
    }

    const x_min = Math.min(bboxStart.x, bboxCurrent.x);
    const y_min = Math.min(bboxStart.y, bboxCurrent.y);
    const x_max = Math.max(bboxStart.x, bboxCurrent.x);
    const y_max = Math.max(bboxStart.y, bboxCurrent.y);

    // Check if valid (minimum size)
    if (Math.abs(x_max - x_min) < 5 || Math.abs(y_max - y_min) < 5) {
      setBboxStart(null);
      setBboxCurrent(null);
      return;
    }

    const annotation: Annotation = {
      type: 'bbox',
      data: { x_min, y_min, x_max, y_max },
      classId: effectiveClassId
    };

    // Save to server
    try {
      const response = await fetch(`${API_BASE_URL}/images/${image.id}/annotations`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          type: 'bbox',
          data: { x_min, y_min, x_max, y_max },
          class_id: effectiveClassId
        }),
      });

      if (response.ok) {
        const saved = await response.json();
        annotation.id = saved.id;
        onAnnotationCreate(annotation);
      }
    } catch (error) {
      console.error('Failed to save annotation:', error);
    }

    setBboxStart(null);
    setBboxCurrent(null);
  };

  const finishPolygon = useCallback(async () => {
    if (polygonPoints.length < 3 || !effectiveClassId) {
      setPolygonPoints([]);
      setIsDrawingPolygon(false);
      return;
    }

    const annotation: Annotation = {
      type: 'polygon',
      data: { points: polygonPoints },
      classId: effectiveClassId
    };

    try {
      const response = await fetch(`${API_BASE_URL}/images/${image.id}/annotations`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          type: 'polygon',
          data: { points: polygonPoints },
          class_id: effectiveClassId
        }),
      });

      if (response.ok) {
        const saved = await response.json();
        annotation.id = saved.id;
        onAnnotationCreate(annotation);
      }
    } catch (error) {
      console.error('Failed to save polygon:', error);
    }

    setPolygonPoints([]);
    setIsDrawingPolygon(false);
  }, [polygonPoints, effectiveClassId, image.id, onAnnotationCreate]);

  // Finish keypoint annotation
  const finishKeypoints = useCallback(async () => {
    if (keypoints.length === 0 || !effectiveClassId) {
      setKeypoints([]);
      setIsDrawingKeypoints(false);
      return;
    }

    const annotation: Annotation = {
      type: 'keypoint',
      data: { points: keypoints },
      classId: effectiveClassId
    };

    try {
      const response = await fetch(`${API_BASE_URL}/images/${image.id}/annotations`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          type: 'keypoint',
          data: { points: keypoints },
          class_id: effectiveClassId
        }),
      });

      if (response.ok) {
        const saved = await response.json();
        annotation.id = saved.id;
        onAnnotationCreate(annotation);
      }
    } catch (error) {
      console.error('Failed to save keypoints:', error);
    }

    setKeypoints([]);
    setIsDrawingKeypoints(false);
  }, [keypoints, effectiveClassId, image.id, onAnnotationCreate]);

  // Mouse wheel zoom
  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;

    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
    const newScale = Math.max(0.1, Math.min(5, scale * zoomFactor));

    // Zoom centered on mouse position
    const imgPoint = screenToImage(mouseX, mouseY);
    const newOffset = {
      x: mouseX - imgPoint.x * newScale,
      y: mouseY - imgPoint.y * newScale
    };

    setScale(newScale);
    setOffset(newOffset);
  };

  // Keyboard events
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.code === 'Space') {
        e.preventDefault();
        setIsSpacePressed(true);
      } else if (e.key === 'Escape') {
        // ESC cancel current drawing or exit edit mode
        if (tool === 'bbox') {
          setBboxStart(null);
          setBboxCurrent(null);
        } else if (tool === 'polygon' && isDrawingPolygon) {
          if (polygonPoints.length > 0) {
            setPolygonPoints(prev => prev.slice(0, -1));
          } else {
            setPolygonPoints([]);
            setIsDrawingPolygon(false);
          }
        } else if (tool === 'keypoint' && isDrawingKeypoints) {
          if (keypoints.length > 0) {
            setKeypoints(prev => prev.slice(0, -1));
          } else {
            setKeypoints([]);
            setIsDrawingKeypoints(false);
          }
        } else if (tool === 'select') {
          // ESC exit edit mode: clear selection, stop dragging/resizing
          if (isDraggingAnnotation || isResizingBbox || draggedPointIndex !== null) {
            // If editing, cancel editing but don't clear selection
            setIsDraggingAnnotation(false);
            setIsResizingBbox(false);
            setDraggedPointIndex(null);
            setResizeHandle(null);
            setDragStart(null);
            setDragOffset(null);
          } else {
            // If not editing, clear selection
            onAnnotationSelect(null);
            setDraggedAnnotationId(null);
          }
        }
      } else if (e.key === 'Enter' || e.key === ' ') {
        // Enter to finish drawing
        if (tool === 'polygon' && polygonPoints.length >= 3) {
          e.preventDefault();
          finishPolygon();
        } else if (tool === 'keypoint' && keypoints.length > 0) {
          e.preventDefault();
          finishKeypoints();
        }
      } else if (e.key === 'Backspace' || e.key === 'Delete') {
        // Backspace/Delete to delete last point
        if (tool === 'polygon' && polygonPoints.length > 0) {
          e.preventDefault();
          setPolygonPoints(prev => prev.slice(0, -1));
        } else if (tool === 'keypoint' && keypoints.length > 0) {
          e.preventDefault();
          setKeypoints(prev => prev.slice(0, -1));
        }
      }
    };

    const handleKeyUp = (e: KeyboardEvent) => {
      if (e.code === 'Space') {
        setIsSpacePressed(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('keyup', handleKeyUp);

    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('keyup', handleKeyUp);
    };
  }, [tool, polygonPoints, isDrawingPolygon, keypoints, isDrawingKeypoints, finishPolygon, finishKeypoints, onAnnotationSelect]);

  // Reset drawing state when switching tools
  useEffect(() => {
    // If currently drawing, cancel current drawing
    if (tool !== 'bbox') {
      setBboxStart(null);
      setBboxCurrent(null);
    }
    if (tool !== 'polygon') {
      setPolygonPoints([]);
      setIsDrawingPolygon(false);
    }
    if (tool !== 'keypoint') {
      setKeypoints([]);
      setIsDrawingKeypoints(false);
    }
    // Clear selection when switching tools
    if (tool !== 'select') {
      onAnnotationSelect(null);
    }
  }, [tool, onAnnotationSelect]);

  // Get drawing hint text
  const getDrawingHint = () => {
    if (tool === 'bbox' && bboxStart) {
      return t('annotation.hint.bboxDrawing');
    } else if (tool === 'polygon' && isDrawingPolygon) {
      return t('annotation.hint.polygonDrawing', { count: polygonPoints.length });
    } else if (tool === 'keypoint' && isDrawingKeypoints) {
      return t('annotation.hint.keypointDrawing', { count: keypoints.length });
    } else if (tool === 'bbox') {
      return t('annotation.hint.bboxStart');
    } else if (tool === 'polygon') {
      return t('annotation.hint.polygonStart');
    } else if (tool === 'keypoint') {
      return t('annotation.hint.keypointStart');
    }
    return null;
  };

  const drawingHint = getDrawingHint();

  // Update container mouse cursor style
  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    
    // Clear all mouse cursor style classes
    container.classList.remove(
      'cursor-drawing', 'cursor-editing', 'cursor-moving', 'cursor-resizing',
      'cursor-default',
      'resizing-n', 'resizing-s', 'resizing-w', 'resizing-e', 'resizing-nw',
      'resizing-ne', 'resizing-sw', 'resizing-se'
    );
    
    // Set mouse cursor style based on current state
    if (tool === 'select') {
      if (isDraggingAnnotation) {
        container.classList.add('cursor-moving');
      } else if (isResizingBbox && resizeHandle) {
        container.classList.add('cursor-resizing');
        // Set specific resize style based on resize direction
        container.classList.add(`resizing-${resizeHandle}`);
      } else if (draggedPointIndex !== null) {
        container.classList.add('cursor-editing');
      } else if (hoverResizeHandle) {
        // Hovering over resize handle
        container.classList.add('cursor-resizing');
        container.classList.add(`resizing-${hoverResizeHandle}`);
      } else if (hoverPointIndex !== null) {
        // Hovering over point
        container.classList.add('cursor-editing');
      } else if (selectedAnnotationId) {
        container.classList.add('cursor-editing');
      } else {
        container.classList.add('cursor-default');
      }
    } else if (tool === 'bbox' || tool === 'polygon' || tool === 'keypoint') {
      container.classList.add('cursor-drawing');
    }
  }, [tool, isDraggingAnnotation, isResizingBbox, resizeHandle, draggedPointIndex, selectedAnnotationId, hoverResizeHandle, hoverPointIndex]);

  return (
    <div className="annotation-canvas-container" ref={containerRef}>
      {imageLoading && (
        <div className="image-loading-overlay">
          <div className="loading-spinner">加载中...</div>
        </div>
      )}
      {imageError && (
        <div className="image-error-overlay">
          <div className="error-message">
            <div className="error-icon"><Icon component={IoWarning} /></div>
            <div>{imageError}</div>
            <button 
              onClick={() => {
                setImageLoading(true);
                setImageError(null);
                // Retry loading
                const img = new Image();
                let imagePath = image.path;
                if (!imagePath.includes('raw/')) {
                  imagePath = `raw/${imagePath}`;
                } else if (imagePath.startsWith(projectId + '/')) {
                  const rawIndex = imagePath.indexOf('raw/');
                  if (rawIndex !== -1) {
                    imagePath = imagePath.substring(rawIndex);
                  }
                }
                const imageUrl = image.path.startsWith('http') 
                  ? image.path 
                  : `${API_BASE_URL.replace('/api', '')}/images/${projectId}/${imagePath}`;
                img.onload = () => {
                  imageRef.current = img;
                  setImageLoading(false);
                  draw();
                };
                img.onerror = () => setImageError(`Retry failed: ${imageUrl}`);
                img.src = imageUrl;
              }}
              className="retry-button"
            >
              重试
            </button>
          </div>
        </div>
      )}
      {drawingHint && !imageLoading && !imageError && (
        <div className="drawing-hint">
          <strong>
            {tool === 'bbox' ? t('annotation.toolRectangle') : 
             tool === 'polygon' ? t('annotation.toolPolygon') : 
             t('annotation.toolKeypoint')}
          </strong>: {drawingHint}
        </div>
      )}
      <canvas
        ref={canvasRef}
        className="annotation-canvas"
        onMouseMove={handleMouseMove}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onDoubleClick={handleDoubleClick}
        onWheel={handleWheel}
        onContextMenu={(e) => e.preventDefault()} // Disable right-click context menu
      />
      
      {/* Alert Dialog */}
      <Alert
        open={alertState.open}
        onOpenChange={closeAlert}
        title={alertState.title}
        message={alertState.message}
        type={alertState.type}
        confirmText={alertState.confirmText || t('common.confirm', '确定')}
        onConfirm={alertState.onConfirm}
      />
    </div>
  );
};


