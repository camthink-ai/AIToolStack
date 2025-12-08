import React, { useState, useEffect, useCallback } from 'react';
import { ToolsBar } from './ToolsBar';
import { AnnotationCanvas } from './AnnotationCanvas';
import { ControlPanel } from './ControlPanel';
import { ShortcutHelper } from './ShortcutHelper';
import { useWebSocket } from '../hooks/useWebSocket';
import { API_BASE_URL } from '../config';
import { IoArrowBack, IoDownload, IoChevronDown } from 'react-icons/io5';
import './AnnotationWorkbench.css';

// 图标组件包装器，解决 TypeScript 类型问题
const Icon: React.FC<{ component: React.ComponentType<any> }> = ({ component: Component }) => {
  return <Component />;
};

interface Project {
  id: string;
  name: string;
  description: string;
}

interface AnnotationWorkbenchProps {
  project: Project;
  onBack: () => void;
}

export type ToolType = 'select' | 'bbox' | 'polygon' | 'keypoint';

export interface Annotation {
  id?: number;
  type: 'bbox' | 'polygon' | 'keypoint';
  data: any;
  classId: number;
  className?: string;
  classColor?: string;
}

export interface ImageInfo {
  id: number;
  filename: string;
  path: string;
  width: number;
  height: number;
  status: string;
}

export interface Class {
  id: number;
  name: string;
  color: string;
  shortcutKey?: string;
}

export const AnnotationWorkbench: React.FC<AnnotationWorkbenchProps> = ({
  project,
  onBack
}) => {
  const [currentTool, setCurrentTool] = useState<ToolType>('select');
  const [images, setImages] = useState<ImageInfo[]>([]);
  const [currentImageIndex, setCurrentImageIndex] = useState<number>(-1);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [classes, setClasses] = useState<Class[]>([]);
  const [selectedAnnotationId, setSelectedAnnotationId] = useState<number | null>(null);
  const [selectedClassId, setSelectedClassId] = useState<number | null>(null);
  const [showAnnotations, setShowAnnotations] = useState<boolean>(true);
  const [history, setHistory] = useState<Annotation[][]>([]);
  const [historyIndex, setHistoryIndex] = useState<number>(-1);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const exportMenuRef = React.useRef<HTMLDivElement>(null);

  // 点击外部关闭下拉菜单
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(event.target as Node)) {
        setShowExportMenu(false);
      }
    };

    if (showExportMenu) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [showExportMenu]);

  // 加载图像列表
  const fetchImages = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${project.id}/images`);
      const data = await response.json();
      setImages(data);
      
      // 选择逻辑：
      // 1) 初次加载或清空后，选中第一张
      // 2) 如果当前索引超出新长度（新增/删除），钳制到最后一张
      setCurrentImageIndex(prevIndex => {
        if (data.length === 0) return -1;
        if (prevIndex === -1) return 0;
        if (prevIndex >= data.length) return data.length - 1;
        return prevIndex;
      });
    } catch (error) {
      console.error('Failed to fetch images:', error);
    }
  }, [project.id, currentImageIndex]);

  // 加载类别列表
  const fetchClasses = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${project.id}/classes`);
      const data = await response.json();
      setClasses(data);
    } catch (error) {
      console.error('Failed to fetch classes:', error);
    }
  }, [project.id]);

  // 加载当前图像的标注
  const fetchAnnotations = useCallback(async () => {
    if (currentImageIndex < 0 || !images[currentImageIndex]) {
      // 如果没有有效图像，清空标注
      setAnnotations([]);
      setHistory([[]]);
      setHistoryIndex(0);
      return;
    }

    const imageId = images[currentImageIndex].id;
    
    // 立即清空旧标注，避免显示上一个图像的标注
    setAnnotations([]);
    
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${project.id}/images/${imageId}`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();

      // 统一标注字段，确保 classId 存在
      const annotationsList = (data.annotations || []).map((ann: any) => ({
        ...ann,
        classId: ann.classId ?? ann.class_id ?? ann.classid ?? null
      }));

      // 更新标注列表
      setAnnotations(annotationsList);
      
      // 取消选中（因为切换了图像）
      setSelectedAnnotationId(null);
      
      // 重置历史记录为新图像的标注历史
      setHistory([annotationsList]);
      setHistoryIndex(0);
    } catch (error) {
      console.error('Failed to fetch annotations:', error);
      setAnnotations([]);
      setHistory([[]]);
      setHistoryIndex(0);
    }
  }, [currentImageIndex, images, project.id]);

  // WebSocket 连接
  useWebSocket(project.id, useCallback((message: any) => {
    if (message.type === 'new_image') {
      // 刷新图像列表
      fetchImages();
      // 如果之前没有选中任何图像，等待 fetch 后会自动选中第一张
    } else if (message.type === 'annotation_deleted') {
      // 标注被删除，刷新标注列表
      const deletedImageId = message.image_id;
      const currentImageId = images[currentImageIndex]?.id;
      if (deletedImageId === currentImageId) {
        // 如果删除的是当前图像的标注，刷新标注列表
        fetchAnnotations();
      }
    }
  }, [fetchImages, fetchAnnotations, images, currentImageIndex]));

  useEffect(() => {
    fetchImages();
    fetchClasses();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  useEffect(() => {
    fetchAnnotations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentImageIndex, images]);

  // 保存标注
  const saveAnnotations = useCallback(async () => {
    if (currentImageIndex < 0 || !images[currentImageIndex]) return;

    // 这里可以实现批量保存逻辑
    // 目前由 Canvas 组件在创建/更新时直接保存
  }, [currentImageIndex, images]);

  // 切换图像
  const handleImageChange = useCallback(async (delta: number) => {
    const newIndex = currentImageIndex + delta;
    if (newIndex >= 0 && newIndex < images.length) {
      // 保存当前标注
      await saveAnnotations();
      
      // 立即清空标注和选中状态，避免显示旧标注
      setAnnotations([]);
      setSelectedAnnotationId(null);
      
      // 切换图像索引，这会触发 useEffect 加载新标注
      setCurrentImageIndex(newIndex);
    }
  }, [currentImageIndex, images.length, saveAnnotations]);

  // 添加标注到历史
  const addToHistory = useCallback((newAnnotations: Annotation[]) => {
    setHistory(prevHistory => {
      const newHistory = prevHistory.slice(0, historyIndex + 1);
      newHistory.push([...newAnnotations]);
      setHistoryIndex(newHistory.length - 1);
      return newHistory;
    });
  }, [historyIndex]);

  // 撤销
  const handleUndo = useCallback(() => {
    if (historyIndex > 0) {
      const newIndex = historyIndex - 1;
      setHistoryIndex(newIndex);
      setAnnotations([...history[newIndex]]);
    }
  }, [historyIndex, history]);

  // 重做
  const handleRedo = useCallback(() => {
    if (historyIndex < history.length - 1) {
      const newIndex = historyIndex + 1;
      setHistoryIndex(newIndex);
      setAnnotations([...history[newIndex]]);
    }
  }, [historyIndex, history]);

  // 删除标注
  const handleDeleteAnnotation = useCallback(async (annotationId: number) => {
    if (!annotationId) {
      console.error('No annotation ID provided for deletion');
      return;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/annotations/${annotationId}`, {
        method: 'DELETE',
      });

      if (response.ok) {
        // 从本地状态中移除标注
        setAnnotations(prevAnnotations => {
          const newAnnotations = prevAnnotations.filter(ann => ann.id !== annotationId);
          addToHistory(newAnnotations);
          return newAnnotations;
        });
        
        // 如果删除的是当前选中的标注，取消选中
        if (selectedAnnotationId === annotationId) {
          setSelectedAnnotationId(null);
        }
        
        // 重新获取标注列表以确保同步（可选，但更安全）
        const imageId = images[currentImageIndex]?.id;
        if (imageId) {
          try {
            const refreshResponse = await fetch(`${API_BASE_URL}/projects/${project.id}/images/${imageId}`);
            const refreshData = await refreshResponse.json();
            setAnnotations(refreshData.annotations || []);
          } catch (refreshError) {
            console.error('Failed to refresh annotations:', refreshError);
          }
        }
      } else {
        const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
        console.error('Failed to delete annotation:', errorData);
        alert(`删除失败: ${errorData.detail || '未知错误'}`);
      }
    } catch (error) {
      console.error('Failed to delete annotation:', error);
      alert('删除标注时发生错误，请检查网络连接');
    }
  }, [selectedAnnotationId, currentImageIndex, images, project.id, addToHistory]);

  // 快捷键处理
  useEffect(() => {
    const handleKeyPress = (e: KeyboardEvent) => {
      // 防止在输入框中触发
      if ((e.target as HTMLElement).tagName === 'INPUT' || 
          (e.target as HTMLElement).tagName === 'TEXTAREA') {
        return;
      }

      // 工具切换
      if (e.key === 'r' || e.key === 'R') {
        setCurrentTool('bbox');
      } else if (e.key === 'p' || e.key === 'P') {
        setCurrentTool('polygon');
      } else if (e.key === 'v' || e.key === 'V') {
        setCurrentTool('select');
      }

      // 导航
      if ((e.key === 'a' || e.key === 'ArrowLeft') && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        handleImageChange(-1);
      } else if ((e.key === 'd' || e.key === 'ArrowRight') && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        handleImageChange(1);
      }

      // 显示/隐藏标注
      if (e.key === 'h' || e.key === 'H') {
        setShowAnnotations(!showAnnotations);
      }

      // 删除
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedAnnotationId) {
        e.preventDefault();
        handleDeleteAnnotation(selectedAnnotationId);
      }

      // 撤销/重做
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        handleUndo();
      } else if ((e.ctrlKey || e.metaKey) && (e.key === 'Z' || (e.key === 'z' && e.shiftKey))) {
        e.preventDefault();
        handleRedo();
      }

      // 保存
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        saveAnnotations();
      }
    };

    window.addEventListener('keydown', handleKeyPress);
    return () => window.removeEventListener('keydown', handleKeyPress);
  }, [currentImageIndex, images, selectedAnnotationId, showAnnotations, history, historyIndex, handleDeleteAnnotation, handleImageChange, handleRedo, handleUndo, saveAnnotations]);

  const currentImage = currentImageIndex >= 0 ? images[currentImageIndex] : null;

  // 导出数据集
  const handleExportDataset = async (exportType: 'yolo' | 'zip') => {
    setIsExporting(true);
    setShowExportMenu(false);
    
    try {
      if (exportType === 'yolo') {
        // 先调用导出 API 生成 YOLO 数据集
        const exportResponse = await fetch(`${API_BASE_URL}/projects/${project.id}/export/yolo`, {
          method: 'POST',
        });
        
        if (!exportResponse.ok) {
          const errorData = await exportResponse.json().catch(() => ({ detail: '导出失败' }));
          throw new Error(errorData.detail || '导出失败');
        }
        
        // 等待一下确保文件已生成
        await new Promise(resolve => setTimeout(resolve, 500));
        
        // 下载 YOLO 数据集 zip
        const downloadResponse = await fetch(`${API_BASE_URL}/projects/${project.id}/export/yolo/download`);
        if (!downloadResponse.ok) {
          throw new Error('下载失败');
        }
        
        const blob = await downloadResponse.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${project.name}_yolo_dataset.zip`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      } else if (exportType === 'zip') {
        // 导出数据集 zip 包
        const downloadResponse = await fetch(`${API_BASE_URL}/projects/${project.id}/export/zip`);
        if (!downloadResponse.ok) {
          const errorData = await downloadResponse.json().catch(() => ({ detail: '导出失败' }));
          throw new Error(errorData.detail || '导出失败');
        }
        
        const blob = await downloadResponse.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${project.name}_dataset.zip`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      }
    } catch (error: any) {
      alert(`导出失败: ${error.message}`);
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="annotation-workbench">
      <ShortcutHelper />
      <div className="workbench-header">
        <div className="header-left">
          <button onClick={onBack} className="btn-back">
            <Icon component={IoArrowBack} /> 返回项目
          </button>
          <h2>{project.name}</h2>
        </div>
        <div className="header-right">
          <div className="header-info">
            {currentImage && (
              <>
                <span className="image-filename">{currentImage.filename}</span>
                <span className="image-resolution">
                  {currentImage.width} × {currentImage.height}
                </span>
              </>
            )}
          </div>
          
          <div className="export-dropdown" ref={exportMenuRef}>
            <button
              className="btn-export"
              onClick={() => setShowExportMenu(!showExportMenu)}
              disabled={isExporting}
            >
              <Icon component={IoDownload} />
              <span>{isExporting ? '导出中...' : '导出数据集'}</span>
              <Icon component={IoChevronDown} />
            </button>
            
            {showExportMenu && (
              <div className="dropdown-menu">
                <button
                  className="dropdown-item"
                  onClick={() => handleExportDataset('yolo')}
                  disabled={isExporting}
                >
                  导出YOLO训练数据集
                </button>
                <button
                  className="dropdown-item"
                  onClick={() => handleExportDataset('zip')}
                  disabled={isExporting}
                >
                  导出标注ZIP 包
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="workbench-content">
        <ToolsBar
          currentTool={currentTool}
          onToolChange={setCurrentTool}
        />

        <div className="canvas-container">
          {currentImage ? (
            <AnnotationCanvas
              image={currentImage}
              annotations={annotations}
              tool={currentTool}
              classes={classes}
              selectedAnnotationId={selectedAnnotationId}
              selectedClassId={selectedClassId}
              showAnnotations={showAnnotations}
              onAnnotationCreate={(ann) => {
                const newAnnotations = [...annotations, ann];
                setAnnotations(newAnnotations);
                addToHistory(newAnnotations);
              }}
              onAnnotationUpdate={(id, updates) => {
                const newAnnotations = annotations.map(ann =>
                  ann.id === id ? { ...ann, ...updates } : ann
                );
                setAnnotations(newAnnotations);
                addToHistory(newAnnotations);
              }}
              onAnnotationSelect={setSelectedAnnotationId}
              projectId={project.id}
            />
          ) : (
            <div className="no-image">暂无图像</div>
          )}
        </div>

        <ControlPanel
          annotations={annotations}
          classes={classes}
          images={images}
          currentImageIndex={currentImageIndex}
          selectedAnnotationId={selectedAnnotationId}
          onImageSelect={(index) => {
            saveAnnotations();
            setCurrentImageIndex(index);
            setSelectedAnnotationId(null);
          }}
          onAnnotationSelect={setSelectedAnnotationId}
          onAnnotationVisibilityChange={(id, visible) => {
            // 可以在这里实现单个标注的显示/隐藏
          }}
          onAnnotationDelete={handleDeleteAnnotation}
          onClassSelect={(classId) => {
            setSelectedClassId(classId);
            // 如果选中了标注，更新标注的类别
            if (selectedAnnotationId) {
              const updatedAnnotations = annotations.map(ann =>
                ann.id === selectedAnnotationId ? { ...ann, classId } : ann
              );
              setAnnotations(updatedAnnotations);
              addToHistory(updatedAnnotations);
            }
          }}
          projectId={project.id}
          onCreateClass={fetchClasses}
          onImageUpload={fetchImages}
          onImageDelete={fetchImages}
        />
      </div>
    </div>
  );
};

