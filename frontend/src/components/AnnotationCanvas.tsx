import React, { useRef, useEffect, useState, useCallback } from 'react';
import { ToolType, Annotation, ImageInfo, Class } from './AnnotationWorkbench';
import { API_BASE_URL } from '../config';
import { IoWarning } from 'react-icons/io5';
import './AnnotationCanvas.css';

// 图标组件包装器，解决 TypeScript 类型问题
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
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState<Point>({ x: 0, y: 0 });
  const [mousePos, setMousePos] = useState<Point>({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState<Point>({ x: 0, y: 0 });
  
  // 矩形框绘制状态
  const [bboxStart, setBboxStart] = useState<Point | null>(null);
  const [bboxCurrent, setBboxCurrent] = useState<Point | null>(null);
  
  // 多边形绘制状态
  const [polygonPoints, setPolygonPoints] = useState<Point[]>([]);
  const [isDrawingPolygon, setIsDrawingPolygon] = useState(false);
  
  // 关键点绘制状态
  const [keypoints, setKeypoints] = useState<Point[]>([]);
  const [isDrawingKeypoints, setIsDrawingKeypoints] = useState(false);
  
  // 标注编辑状态
  const [isDraggingAnnotation, setIsDraggingAnnotation] = useState(false);
  const [dragStart, setDragStart] = useState<Point | null>(null);
  const [dragOffset, setDragOffset] = useState<Point | null>(null);
  const [draggedAnnotationId, setDraggedAnnotationId] = useState<number | null>(null);
  
  // Bbox调整状态（拖拽角或边）
  const [isResizingBbox, setIsResizingBbox] = useState(false);
  const [resizeHandle, setResizeHandle] = useState<string | null>(null); // 'nw', 'ne', 'sw', 'se', 'n', 's', 'e', 'w'
  
  // Polygon/Keypoint调整状态（拖拽顶点/点）
  const [draggedPointIndex, setDraggedPointIndex] = useState<number | null>(null);

  // Hover 状态，用于改变指针
  const [hoverResizeHandle, setHoverResizeHandle] = useState<string | null>(null);
  const [hoverPointIndex, setHoverPointIndex] = useState<number | null>(null);
  
  const [isSpacePressed, setIsSpacePressed] = useState(false);
  
  // 如果未选中类别且有类别，默认选中第一个
  const effectiveClassId = selectedClassId || (classes.length > 0 ? classes[0].id : null);
  const [imageLoading, setImageLoading] = useState(true);
  const [imageError, setImageError] = useState<string | null>(null);

  // 加载图像
  useEffect(() => {
    setImageLoading(true);
    setImageError(null);
    
    const img = new Image();
    img.crossOrigin = 'anonymous'; // 允许跨域加载
    
    img.onload = () => {
      console.log('[Canvas] Image loaded:', img.width, 'x', img.height);
      imageRef.current = img;
      setImageLoading(false);
      setImageError(null);
      
      // 计算初始缩放和偏移，使图像居中
      if (containerRef.current) {
        const container = containerRef.current;
        const containerWidth = container.clientWidth;
        const containerHeight = container.clientHeight;
        
        const scaleX = containerWidth / img.width;
        const scaleY = containerHeight / img.height;
        const initialScale = Math.min(scaleX, scaleY, 1); // 不放大超过原始尺寸
        
        setScale(initialScale);
        setOffset({
          x: (containerWidth - img.width * initialScale) / 2,
          y: (containerHeight - img.height * initialScale) / 2
        });
      }
      // 使用 setTimeout 确保在下一帧调用 draw，避免依赖循环
      setTimeout(() => {
        if (imageRef.current) {
          draw();
        }
      }, 0);
    };
    
    img.onerror = (error) => {
      console.error('[Canvas] Image load error:', error);
      setImageLoading(false);
      // 构建图像 URL 用于错误提示
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
      setImageError(`图片加载失败: ${imageUrl}`);
    };
    
    // 构建图像 URL
    // image.path 格式应该是 raw/filename
    let imagePath = image.path;
    // 如果路径不包含 raw/，添加它（兼容旧数据）
    if (!imagePath.includes('raw/')) {
      imagePath = `raw/${imagePath}`;
    } else if (imagePath.startsWith(projectId + '/')) {
      // 如果包含 project_id，移除它
      const rawIndex = imagePath.indexOf('raw/');
      if (rawIndex !== -1) {
        imagePath = imagePath.substring(rawIndex);
      }
    }
    // API_BASE_URL 已经是 http://localhost:8000/api
    // 图片服务路径是 /api/images/{project_id}/{image_path}
    const imageUrl = image.path.startsWith('http') 
      ? image.path 
      : `${API_BASE_URL}/images/${projectId}/${imagePath}`;
    
    console.log('[Canvas] Loading image:', imageUrl);
    img.src = imageUrl;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [image, projectId]);

  // 坐标转换：屏幕坐标 -> 图像坐标
  const screenToImage = useCallback((screenX: number, screenY: number): Point => {
    if (!imageRef.current) return { x: 0, y: 0 };
    
    const x = (screenX - offset.x) / scale;
    const y = (screenY - offset.y) / scale;
    
    return {
      x: Math.max(0, Math.min(imageRef.current.width, x)),
      y: Math.max(0, Math.min(imageRef.current.height, y))
    };
  }, [offset, scale]);

  // 坐标转换：图像坐标 -> 屏幕坐标（预留功能）
  // const imageToScreen = useCallback((imageX: number, imageY: number): Point => {
  //   return {
  //     x: imageX * scale + offset.x,
  //     y: imageY * scale + offset.y
  //   };
  // }, [offset, scale]);

  // 获取点击位置在bbox上的哪个区域（用于调整大小）
  const getBboxHandle = useCallback((bbox: { x_min: number; y_min: number; x_max: number; y_max: number }, point: Point): string | null => {
    const x1 = bbox.x_min * scale + offset.x;
    const y1 = bbox.y_min * scale + offset.y;
    const x2 = bbox.x_max * scale + offset.x;
    const y2 = bbox.y_max * scale + offset.y;
    const threshold = 15; // 增加阈值以匹配增大的手柄尺寸
    
    const screenX = point.x * scale + offset.x;
    const screenY = point.y * scale + offset.y;
    
    // 检查四个角
    if (Math.abs(screenX - x1) < threshold && Math.abs(screenY - y1) < threshold) return 'nw';
    if (Math.abs(screenX - x2) < threshold && Math.abs(screenY - y1) < threshold) return 'ne';
    if (Math.abs(screenX - x1) < threshold && Math.abs(screenY - y2) < threshold) return 'sw';
    if (Math.abs(screenX - x2) < threshold && Math.abs(screenY - y2) < threshold) return 'se';
    
    // 检查边
    if (Math.abs(screenX - (x1 + x2) / 2) < threshold && Math.abs(screenY - y1) < threshold) return 'n';
    if (Math.abs(screenX - (x1 + x2) / 2) < threshold && Math.abs(screenY - y2) < threshold) return 's';
    if (Math.abs(screenX - x1) < threshold && Math.abs(screenY - (y1 + y2) / 2) < threshold) return 'w';
    if (Math.abs(screenX - x2) < threshold && Math.abs(screenY - (y1 + y2) / 2) < threshold) return 'e';
    
    return null;
  }, [offset, scale]);

  // 获取点击位置在多边形/关键点的哪个点上
  // 使用图像坐标的距离阈值（像素单位），阈值应该足够大以匹配增大的点尺寸
  const getClickedPointIndex = useCallback((points: Point[], clickPoint: Point, threshold: number = 20): number | null => {
    let minDistance = Infinity;
    let closestIndex: number | null = null;
    
    for (let i = 0; i < points.length; i++) {
      const distance = Math.sqrt(
        Math.pow(clickPoint.x - points[i].x, 2) + Math.pow(clickPoint.y - points[i].y, 2)
      );
      // 找到最近的点
      if (distance < minDistance) {
        minDistance = distance;
        closestIndex = i;
      }
    }
    
    // 如果最近的点在阈值内（图像坐标），返回索引
    if (closestIndex !== null && minDistance < threshold) {
      return closestIndex;
    }
    
    return null;
  }, []);

  // 绘制函数
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    
    if (!imageRef.current) {
      // 如果图片还没加载，只清空画布
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

    // 设置画布尺寸
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;

    // 清空画布
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 绘制图像
    ctx.save();
    ctx.translate(offset.x, offset.y);
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0);
    ctx.restore();

    // 绘制标注
    if (showAnnotations) {
      annotations.forEach((ann) => {
        const classObj = classes.find(c => c.id === ann.classId);
        const color = classObj?.color || '#4a9eff';
        const isSelected = ann.id === selectedAnnotationId;

        ctx.strokeStyle = isSelected ? '#ffff00' : color;
        ctx.lineWidth = isSelected ? 3 : 2;
        ctx.fillStyle = color + '33'; // 半透明填充

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
          
          // 如果选中，绘制调整手柄（增大尺寸）
          if (isSelected && tool === 'select') {
            const handleSize = 12; // 从8增加到12
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
              ctx.fillStyle = isHovered ? '#ffaa00' : '#ffff00'; // hover时使用更深的黄色
              ctx.strokeStyle = '#000000';
              ctx.lineWidth = isHovered ? 3 : 2; // hover时边框更粗
              const size = isHovered ? handleSize + 2 : handleSize; // hover时稍大
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
            
            // 如果选中，绘制顶点手柄（增大尺寸）
            if (isSelected && tool === 'select') {
              points.forEach((point: any, index: number) => {
                const px = (point.x || point[0]) * scale + offset.x;
                const py = (point.y || point[1]) * scale + offset.y;
                const isHovered = hoverPointIndex === index && ann.id === selectedAnnotationId;
                ctx.fillStyle = isHovered ? '#ffaa00' : '#ffff00'; // hover时使用更深的黄色
                ctx.strokeStyle = '#000000';
                ctx.lineWidth = isHovered ? 3 : 2; // hover时边框更粗
                const radius = isHovered ? 11 : 9; // hover时稍大
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
            // 增大选中时的关键点大小
            const radius = isHovered ? 12 : (isSelected && tool === 'select' ? 10 : 5);
            ctx.beginPath();
            ctx.arc(x, y, radius, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
          });
        }
      });
    }

    // 绘制正在绘制的矩形框
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

    // 绘制正在绘制的多边形
    if (polygonPoints.length > 0 && tool === 'polygon' && isDrawingPolygon) {
      // 绘制已确定的边
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
      
      // 绘制预览线：从最后一个点到鼠标位置
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
        
        // 如果点数>=3，绘制回到起点的预览线（闭合预览）
        if (polygonPoints.length >= 3) {
          const firstPoint = polygonPoints[0];
          const firstX = firstPoint.x * scale + offset.x;
          const firstY = firstPoint.y * scale + offset.y;
          ctx.lineTo(firstX, firstY);
        }
        
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // 绘制已放置的点（增大尺寸）
      polygonPoints.forEach((point, index) => {
        const x = point.x * scale + offset.x;
        const y = point.y * scale + offset.y;
        ctx.fillStyle = index === 0 ? '#ffff00' : '#4a9eff'; // 起点用黄色高亮
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2; // 增加边框宽度
        ctx.beginPath();
        ctx.arc(x, y, 7, 0, Math.PI * 2); // 从5增加到7
        ctx.fill();
        ctx.stroke();
      });
      
      // 在鼠标位置显示预览点
      ctx.fillStyle = '#4a9eff';
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(mousePos.x, mousePos.y, 5, 0, Math.PI * 2); // 从4增加到5
      ctx.fill();
      ctx.stroke();
    }

    // 绘制正在绘制的关键点（增大尺寸）
    if (keypoints.length > 0 && tool === 'keypoint') {
      keypoints.forEach((point) => {
        const x = point.x * scale + offset.x;
        const y = point.y * scale + offset.y;
        ctx.fillStyle = '#4a9eff';
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, 7, 0, Math.PI * 2); // 从5增加到7
        ctx.fill();
        ctx.stroke();
      });
      
      // 如果有多个关键点，绘制连接线（可选）
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

    // 绘制十字准星
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

  // 鼠标事件处理
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
      // 拖拽移动标注
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
            // 限制在图像范围内
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
            // 限制在图像范围内
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
            // 限制在图像范围内
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
      // 调整bbox大小
      const imgPoint = screenToImage(x, y);
      const annotation = annotations.find(a => a.id === draggedAnnotationId);
      if (annotation && annotation.type === 'bbox') {
        const data = annotation.data as { x_min: number; y_min: number; x_max: number; y_max: number };
        let newData = { ...data };
        
        if (resizeHandle.includes('w')) newData.x_min = imgPoint.x;
        if (resizeHandle.includes('e')) newData.x_max = imgPoint.x;
        if (resizeHandle.includes('n')) newData.y_min = imgPoint.y;
        if (resizeHandle.includes('s')) newData.y_max = imgPoint.y;
        
        // 确保最小尺寸和边界
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
      // 拖拽多边形顶点或关键点
      const imgPoint = screenToImage(x, y);
      const annotation = annotations.find(a => a.id === draggedAnnotationId);
      if (annotation && (annotation.type === 'polygon' || annotation.type === 'keypoint')) {
        const points = [...(annotation.data.points || [])];
        if (points[draggedPointIndex] !== undefined) {
          // 限制在图像范围内
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
      draw(); // 重绘以显示预览线
    } else if (tool === 'keypoint' && isDrawingKeypoints) {
      draw(); // 重绘关键点
    }

    // Hover 处理（仅在选择模式，用于指针反馈）
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
      // 开始平移
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
      // 检查是否点击了标注
      let clickedAnnotation: Annotation | null = null;
      let clickedHandle: string | null = null;
      let clickedPointIndex: number | null = null;
      
      // 优先检查已选中的标注的编辑点（手柄和顶点）
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
      
      // 如果没有点击到编辑点，检查是否点击了标注本身
      if (!clickedHandle && clickedPointIndex === null) {
        // 按Z顺序从后往前检查（后绘制的在前）
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
              // 检查是否在多边形内
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
            // 检查是否点击在关键点附近（未选中时也可以点击点来选中）
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
      
      // 处理点击 - 优先处理编辑点
      if (clickedHandle && clickedAnnotation) {
        // 开始调整bbox大小
        setIsResizingBbox(true);
        setResizeHandle(clickedHandle);
        setDraggedAnnotationId(clickedAnnotation.id!);
        setIsDraggingAnnotation(false); // 不是移动
        setDraggedPointIndex(null);
        onAnnotationSelect(clickedAnnotation.id!);
      } else if (clickedPointIndex !== null && clickedAnnotation) {
        // 开始拖拽顶点/点（不是整体移动）
        // 立即设置拖拽状态，确保点击后能立即响应鼠标移动
        setDraggedPointIndex(clickedPointIndex);
        setDraggedAnnotationId(clickedAnnotation.id!);
        setIsDraggingAnnotation(false);
        setIsResizingBbox(false);
        setResizeHandle(null);
        // 记录初始点击位置，用于后续拖拽计算
        setDragStart(imgPoint);
        setDragOffset(imgPoint);
        onAnnotationSelect(clickedAnnotation.id!);
      } else if (clickedAnnotation && clickedAnnotation.id) {
        // 选中标注（点击了标注但没点击编辑点）
        const wasAlreadySelected = clickedAnnotation.id === selectedAnnotationId;
        onAnnotationSelect(clickedAnnotation.id);
        
        // 只有在点击中心区域时才准备移动（不是点击手柄或顶点）
        if (wasAlreadySelected && !clickedHandle && clickedPointIndex === null) {
          // 再次点击已选中的标注，准备移动
          setDraggedAnnotationId(clickedAnnotation.id);
          setIsDraggingAnnotation(true);
          setDragStart(imgPoint);
          setDragOffset(imgPoint);
        } else {
          // 新选中，不立即移动
          setIsDraggingAnnotation(false);
          setIsResizingBbox(false);
          setDraggedPointIndex(null);
          setResizeHandle(null);
        }
      } else {
        // 点击空白区域
        exitEditMode(false, true);
      }
    } else if (tool === 'bbox') {
      if (!effectiveClassId) {
        alert('请先选择一个类别');
        return;
      }
      if (!bboxStart) {
        setBboxStart(imgPoint);
        setBboxCurrent(imgPoint);
      }
    } else if (tool === 'polygon') {
      if (!effectiveClassId) {
        alert('请先选择一个类别');
        return;
      }
      if (!isDrawingPolygon) {
        setIsDrawingPolygon(true);
        setPolygonPoints([imgPoint]);
      } else {
        // 检查是否点击了起点（闭合）
        const firstPoint = polygonPoints[0];
        const distance = Math.sqrt(
          Math.pow(imgPoint.x - firstPoint.x, 2) + Math.pow(imgPoint.y - firstPoint.y, 2)
        );
        
        // 使用图像坐标的距离阈值（约15像素，根据缩放调整）
        const threshold = 15;
        if (distance < threshold && polygonPoints.length >= 3) {
          // 闭合多边形，不添加新点，直接完成
          await finishPolygon();
        } else {
          // 添加新点
          setPolygonPoints(prev => [...prev, imgPoint]);
        }
      }
    } else if (tool === 'keypoint') {
      if (!effectiveClassId) {
        alert('请先选择一个类别');
        return;
      }
      // 添加关键点
      setKeypoints(prev => [...prev, imgPoint]);
      if (!isDrawingKeypoints) {
        setIsDrawingKeypoints(true);
      }
    }
  };

  // 退出编辑模式，可选择是否保存、是否取消选中
  const exitEditMode = (clearSelection = false, saveCurrent = true) => {
    if (saveCurrent && draggedAnnotationId) {
      const annotation = annotations.find(a => a.id === draggedAnnotationId);
      if (annotation) {
        // 确保data是对象格式
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

    // 右键取消当前绘制
    if (e.button === 2) {
      if (tool === 'bbox') {
        setBboxStart(null);
        setBboxCurrent(null);
      } else if (tool === 'polygon' && isDrawingPolygon) {
        setPolygonPoints([]);
        setIsDrawingPolygon(false);
      } else if (tool === 'keypoint' && isDrawingKeypoints) {
        // 右键取消关键点绘制（不保存）
        setKeypoints([]);
        setIsDrawingKeypoints(false);
      }
      return;
    }

    // 结束拖拽或调整
    if (isDraggingAnnotation || isResizingBbox || draggedPointIndex !== null) {
      exitEditMode(false, true);
      return;
    }

    if (tool === 'bbox' && bboxStart && bboxCurrent) {
      await finishBbox();
    }
  };

  // 双击：优先完成绘制，其次退出编辑
  const handleDoubleClick = () => {
    if (tool === 'polygon' && isDrawingPolygon && polygonPoints.length >= 3) {
      finishPolygon();
      return;
    }
    if (tool === 'keypoint' && isDrawingKeypoints && keypoints.length > 0) {
      finishKeypoints();
      return;
    }

    // 非绘制状态下，双击退出编辑并清空临时绘制
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

    // 检查是否有效（最小尺寸）
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

    // 保存到服务器
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

  // 完成关键点标注
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

  // 滚轮缩放
  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;

    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
    const newScale = Math.max(0.1, Math.min(5, scale * zoomFactor));

    // 以鼠标位置为中心缩放
    const imgPoint = screenToImage(mouseX, mouseY);
    const newOffset = {
      x: mouseX - imgPoint.x * newScale,
      y: mouseY - imgPoint.y * newScale
    };

    setScale(newScale);
    setOffset(newOffset);
  };

  // 键盘事件
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.code === 'Space') {
        e.preventDefault();
        setIsSpacePressed(true);
      } else if (e.key === 'Escape') {
        // ESC 取消当前绘制或退出编辑模式
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
          // ESC 退出编辑模式：取消选中、停止拖拽/调整
          if (isDraggingAnnotation || isResizingBbox || draggedPointIndex !== null) {
            // 如果正在编辑，取消编辑但不取消选中
            setIsDraggingAnnotation(false);
            setIsResizingBbox(false);
            setDraggedPointIndex(null);
            setResizeHandle(null);
            setDragStart(null);
            setDragOffset(null);
          } else {
            // 如果不在编辑，取消选中
            onAnnotationSelect(null);
            setDraggedAnnotationId(null);
          }
        }
      } else if (e.key === 'Enter' || e.key === ' ') {
        // Enter 完成绘制
        if (tool === 'polygon' && polygonPoints.length >= 3) {
          e.preventDefault();
          finishPolygon();
        } else if (tool === 'keypoint' && keypoints.length > 0) {
          e.preventDefault();
          finishKeypoints();
        }
      } else if (e.key === 'Backspace' || e.key === 'Delete') {
        // Backspace/Delete 删除最后一个点
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
  }, [tool, polygonPoints, isDrawingPolygon, keypoints, isDrawingKeypoints, finishPolygon, finishKeypoints]);

  // 重置绘制状态当切换工具时
  useEffect(() => {
    // 如果正在绘制，取消当前绘制
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
    // 切换工具时取消选中
    if (tool !== 'select') {
      onAnnotationSelect(null);
    }
  }, [tool, onAnnotationSelect]);

  // 获取绘制提示文本
  const getDrawingHint = () => {
    if (tool === 'bbox' && bboxStart) {
      return '拖动鼠标绘制矩形，释放完成';
    } else if (tool === 'polygon' && isDrawingPolygon) {
      return `多边形绘制中 (${polygonPoints.length} 点) - 点击添加点，点击起点/双击/Enter 完成，ESC/右键取消`;
    } else if (tool === 'keypoint' && isDrawingKeypoints) {
      return `关键点绘制中 (${keypoints.length} 点) - 点击添加点，右键/双击完成，ESC/Backspace 删除最后一点`;
    } else if (tool === 'bbox') {
      return '点击并拖动绘制矩形框';
    } else if (tool === 'polygon') {
      return '点击开始绘制多边形';
    } else if (tool === 'keypoint') {
      return '点击添加关键点';
    }
    return null;
  };

  const drawingHint = getDrawingHint();

  // 更新容器鼠标样式
  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;
    
    // 清除所有鼠标样式类
    container.classList.remove(
      'cursor-drawing', 'cursor-editing', 'cursor-moving', 'cursor-resizing',
      'cursor-default',
      'resizing-n', 'resizing-s', 'resizing-w', 'resizing-e', 'resizing-nw',
      'resizing-ne', 'resizing-sw', 'resizing-se'
    );
    
    // 根据当前状态设置鼠标样式
    if (tool === 'select') {
      if (isDraggingAnnotation) {
        container.classList.add('cursor-moving');
      } else if (isResizingBbox && resizeHandle) {
        container.classList.add('cursor-resizing');
        // 根据调整方向设置具体的resize样式
        container.classList.add(`resizing-${resizeHandle}`);
      } else if (draggedPointIndex !== null) {
        container.classList.add('cursor-editing');
      } else if (hoverResizeHandle) {
        // 悬停在调整手柄上
        container.classList.add('cursor-resizing');
        container.classList.add(`resizing-${hoverResizeHandle}`);
      } else if (hoverPointIndex !== null) {
        // 悬停在点上
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
                // 重新触发加载
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
                img.onerror = () => setImageError(`重试失败: ${imageUrl}`);
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
          <strong>{tool === 'bbox' ? '矩形' : tool === 'polygon' ? '多边形' : '关键点'}</strong>: {drawingHint}
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
        onContextMenu={(e) => e.preventDefault()} // 禁用右键菜单
      />
    </div>
  );
};


