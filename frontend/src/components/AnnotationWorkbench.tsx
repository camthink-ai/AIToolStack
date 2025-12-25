import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { ToolsBar } from './ToolsBar';
import { AnnotationCanvas } from './AnnotationCanvas';
import { ControlPanel } from './ControlPanel';
import { ShortcutHelper } from './ShortcutHelper';
import { MQTTGuide } from './MQTTGuide';
import { TrainingPanel } from './TrainingPanel';
import { DatasetImportModal } from './DatasetImportModal';
import { DataSourceManager } from './DataSourceManager';
import { useWebSocket } from '../hooks/useWebSocket';
import { API_BASE_URL } from '../config';
import { IoArrowBack, IoDownload, IoChevronDown, IoCloudUpload, IoRocket, IoLink } from 'react-icons/io5';
import './AnnotationWorkbench.css';
import { Button } from '../ui/Button';
import { Alert } from '../ui/Alert';
import { useAlert } from '../hooks/useAlert';

// Icon component wrapper to resolve TypeScript type issues
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
  onOpenTraining?: (projectId: string, trainingId?: string) => void;
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
  onBack,
  onOpenTraining
}) => {
  const { t } = useTranslation();
  const { alertState, showError, showSuccess, closeAlert } = useAlert();
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
  const [showTrainingPanel, setShowTrainingPanel] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [showDataSourceModal, setShowDataSourceModal] = useState(false);
  const exportMenuRef = React.useRef<HTMLDivElement>(null);
  const exportMenuButtonRef = React.useRef<HTMLButtonElement>(null);
  const exportMenuDropdownRef = React.useRef<HTMLDivElement>(null);
  const [exportMenuPosition, setExportMenuPosition] = useState<{ top: number; left: number; width?: number } | null>(null);
  const annotationCacheRef = React.useRef<Record<number, Annotation[]>>({});
  const annotationsAbortRef = React.useRef<AbortController | null>(null);
  const imageListRefreshTimeoutRef = React.useRef<number | null>(null);
  const previousImageIdRef = React.useRef<number | null>(null); // Track current image ID to detect changes

  // Calculate dropdown menu position and close when clicking outside
  useEffect(() => {
    const updateMenuPosition = () => {
      if (exportMenuButtonRef.current && showExportMenu) {
        const rect = exportMenuButtonRef.current.getBoundingClientRect();
        setExportMenuPosition({
          top: rect.bottom + 4, // 4px spacing
          left: rect.left, // Align with button left edge
          width: rect.width, // Match button width
        });
      } else {
        setExportMenuPosition(null);
      }
    };

    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      // Check if click is outside button, dropdown container, and the portal menu
      if (
        exportMenuButtonRef.current && 
        !exportMenuButtonRef.current.contains(target) &&
        exportMenuRef.current &&
        !exportMenuRef.current.contains(target) &&
        exportMenuDropdownRef.current &&
        !exportMenuDropdownRef.current.contains(target)
      ) {
        setShowExportMenu(false);
      }
    };

    if (showExportMenu) {
      updateMenuPosition();
      window.addEventListener('resize', updateMenuPosition);
      window.addEventListener('scroll', updateMenuPosition, true);
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      window.removeEventListener('resize', updateMenuPosition);
      window.removeEventListener('scroll', updateMenuPosition, true);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [showExportMenu]);

  // Load image list with retry mechanism
  const fetchImages = useCallback(async (retryCount = 0): Promise<void> => {
    const maxRetries = 3;
    const retryDelay = 500; // ms
    
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${project.id}/images`);
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const data = await response.json();
      
      // Update images list
      setImages(prevImages => {
        const previousLength = prevImages.length;
        
        // If new images are added, log to console
        if (data.length > previousLength) {
          console.log(`[Image List] New image added. Total: ${data.length} (was ${previousLength})`);
        }
        
        return data;
      });
      
      // Selection logic with image ID tracking:
      // 1) On first load or after clearing, select first image
      // 2) If current index exceeds new length (added/deleted), clamp to last image
      // 3) If image added via WebSocket notification, keep current selection (don't auto-switch)
      // 4) Check if current index's image ID changed - if so, we need to reload annotations
      setCurrentImageIndex(prevIndex => {
        if (data.length === 0) {
          previousImageIdRef.current = null;
          return -1;
        }
        if (prevIndex === -1) {
          previousImageIdRef.current = data[0]?.id ?? null;
          return 0;
        }
        if (prevIndex >= data.length) {
          const newIndex = data.length - 1;
          previousImageIdRef.current = data[newIndex]?.id ?? null;
          return newIndex;
        }
        
        // Check if image ID at current index has changed
        const currentImageId = data[prevIndex]?.id;
        const trackedPreviousImageId = previousImageIdRef.current;
        
        // If image ID changed, we need to clear annotations and reload
        if (currentImageId !== trackedPreviousImageId && currentImageId !== undefined && trackedPreviousImageId !== null) {
          console.log(`[Image List] Image ID changed at index ${prevIndex}: ${trackedPreviousImageId} -> ${currentImageId}`);
          // Clear annotations immediately to prevent showing wrong annotations
          setAnnotations([]);
          setHistory([[]]);
          setHistoryIndex(0);
          setSelectedAnnotationId(null);
        }
        
        // Update tracked image ID
        if (currentImageId !== undefined) {
          previousImageIdRef.current = currentImageId;
        }
        
        // If new images are added (length increased) but current image is selected, keep current selection
        // This prevents users from being interrupted by new images
        return prevIndex;
      });
    } catch (error) {
      console.error(`[Image List] Failed to fetch images (attempt ${retryCount + 1}/${maxRetries + 1}):`, error);
      
      // Retry on failure
      if (retryCount < maxRetries) {
        console.log(`[Image List] Retrying in ${retryDelay}ms...`);
        await new Promise(resolve => setTimeout(resolve, retryDelay));
        return fetchImages(retryCount + 1);
      } else {
        console.error('[Image List] Max retries reached, giving up');
      }
    }
  }, [project.id]);

  // Load class list
  const fetchClasses = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${project.id}/classes`);
      const data = await response.json();
      setClasses(data);
    } catch (error) {
      console.error('Failed to fetch classes:', error);
    }
  }, [project.id]);

  // Load annotations for current image
  const fetchAnnotations = useCallback(async () => {
    if (currentImageIndex < 0 || !images[currentImageIndex]) {
      // If no valid image, clear annotations
      setAnnotations([]);
      setHistory([[]]);
      setHistoryIndex(0);
      previousImageIdRef.current = null;
      return;
    }

    const imageId = images[currentImageIndex].id;
    
    // Check if we're switching to a different image (image ID changed)
    const previousImageId = previousImageIdRef.current;
    const imageChanged = previousImageId !== imageId && previousImageId !== null;
    
    // If image changed, clear annotations immediately to prevent showing wrong annotations
    if (imageChanged) {
      setAnnotations([]);
      setHistory([[]]);
      setHistoryIndex(0);
      setSelectedAnnotationId(null);
    }
    
    // Update tracked image ID
    previousImageIdRef.current = imageId;
    
    // Try to use cache to improve switching speed (only if image ID hasn't changed)
    const cached = annotationCacheRef.current[imageId];
    if (cached && !imageChanged) {
      // Use cache only if we're still on the same image (optimistic display)
      setAnnotations(cached);
      setHistory([cached]);
      setHistoryIndex(0);
      setSelectedAnnotationId(null);
    } else if (!cached) {
      // No cache and image changed, clear annotations to show loading state
      setAnnotations([]);
      setHistory([[]]);
      setHistoryIndex(0);
      setSelectedAnnotationId(null);
    }

    // Cancel previous request
    if (annotationsAbortRef.current) {
      annotationsAbortRef.current.abort();
    }
    const controller = new AbortController();
    annotationsAbortRef.current = controller;
    
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${project.id}/images/${imageId}`, {
        signal: controller.signal
      });
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();

      // Verify we're still on the same image (image ID might have changed during fetch)
      if (images[currentImageIndex]?.id !== imageId) {
        console.log(`[Annotations] Image changed during fetch, ignoring results for image ${imageId}`);
        return;
      }

      // Normalize annotation fields, ensure classId exists
      const annotationsList = (data.annotations || []).map((ann: any) => ({
        ...ann,
        classId: ann.classId ?? ann.class_id ?? ann.classid ?? null
      }));

      // Update annotation list
      setAnnotations(annotationsList);
      annotationCacheRef.current[imageId] = annotationsList;
      
      // Clear selection (because image was switched)
      setSelectedAnnotationId(null);
      
      // Reset history to new image's annotation history
      setHistory([annotationsList]);
      setHistoryIndex(0);
      
      // Update tracked image ID to match
      previousImageIdRef.current = imageId;

      // Prefetch annotations of neighbor images in background to speed up navigation
      const neighborIndices = [currentImageIndex - 1, currentImageIndex + 1];
      neighborIndices.forEach((idx) => {
        const neighborImage = images[idx];
        if (!neighborImage) return;
        const neighborId = neighborImage.id;
        if (annotationCacheRef.current[neighborId]) return;

        // Fire and forget, do not override current annotations
        fetch(`${API_BASE_URL}/projects/${project.id}/images/${neighborId}`)
          .then(res => {
            if (!res.ok) return null;
            return res.json();
          })
          .then(neighborData => {
            if (!neighborData) return;
            const neighborAnnotations = (neighborData.annotations || []).map((ann: any) => ({
              ...ann,
              classId: ann.classId ?? ann.class_id ?? ann.classid ?? null
            }));
            annotationCacheRef.current[neighborId] = neighborAnnotations;
          })
          .catch(err => {
            console.error('Failed to prefetch neighbor annotations:', err);
          });
      });
    } catch (error: any) {
      if (error?.name === 'AbortError') {
        return;
      }
      console.error('Failed to fetch annotations:', error);
      setAnnotations([]);
      setHistory([[]]);
      setHistoryIndex(0);
    }
  }, [currentImageIndex, images, project.id]);

  // WebSocket connection
  useWebSocket(project.id, useCallback((message: any) => {
    console.log('[WebSocket] Received message:', message);
    
    const scheduleRefreshImages = (immediate = false) => {
      // Clear any pending refresh
      if (imageListRefreshTimeoutRef.current !== null) {
        window.clearTimeout(imageListRefreshTimeoutRef.current);
        imageListRefreshTimeoutRef.current = null;
      }
      
      const refreshDelay = immediate ? 0 : 300; // Increased delay to ensure DB commit completes
      
      imageListRefreshTimeoutRef.current = window.setTimeout(() => {
        fetchImages()
          .then(() => {
            console.log('[WebSocket] Image list refreshed successfully');
          })
          .catch(error => {
            console.error('[WebSocket] Failed to refresh image list:', error);
            // Try once more after a short delay if first attempt fails
            setTimeout(() => {
              fetchImages().catch(err => {
                console.error('[WebSocket] Retry refresh also failed:', err);
              });
            }, 1000);
          })
          .finally(() => {
            imageListRefreshTimeoutRef.current = null;
          });
      }, refreshDelay);
    };

    if (message.type === 'new_image') {
      console.log('[WebSocket] New image notification received:', message);
      // When new image is added, clear annotations to prevent showing old image's annotations
      // The annotations will be reloaded after image list refresh if current image is still selected
      setAnnotations([]);
      setHistory([[]]);
      setHistoryIndex(0);
      setSelectedAnnotationId(null);
      // Schedule refresh with a small delay to ensure database commit is complete
      // For new images, we want to refresh even if user is currently viewing an image
      scheduleRefreshImages(false);
    } else if (message.type === 'image_deleted') {
      console.log('[WebSocket] Image deleted notification received:', message);
      const deletedImageId = message.image_id;
      // Clear cache for deleted image
      if (annotationCacheRef.current[deletedImageId]) {
        delete annotationCacheRef.current[deletedImageId];
      }
      // Refresh image list - this will adjust currentImageIndex if needed
      scheduleRefreshImages(false);
    } else if (message.type === 'image_status_updated') {
      // Image status updated (annotation create/delete causes status change)
      console.log('[WebSocket] Image status updated:', message);
      // Debounced refresh image list to update status display
      scheduleRefreshImages();
    } else if (message.type === 'annotation_deleted') {
      // Annotation deleted, refresh annotation list and image list
      const deletedImageId = message.image_id;
      // Refresh image list (status may change from LABELED to UNLABELED)
      fetchImages();
      // If deleted annotation belongs to current image, refresh annotation list
      setImages(currentImages => {
        const currentImageId = currentImages[currentImageIndex]?.id;
        if (deletedImageId === currentImageId) {
          fetchAnnotations();
        }
        return currentImages;
      });
    }
  }, [fetchImages, fetchAnnotations, currentImageIndex]));

  useEffect(() => {
    fetchImages();
    fetchClasses();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id]);

  // Periodic refresh as backup in case WebSocket messages are missed
  // This ensures images are refreshed even if WebSocket connection has issues
  useEffect(() => {
    // Only poll if project is active and we have a valid project ID
    if (!project.id) return;

    const pollInterval = 10000; // Poll every 10 seconds as backup
    const intervalId = setInterval(() => {
      // Silently refresh image list to catch any missed updates
      fetchImages().catch(error => {
        console.error('[Image List] Periodic refresh failed:', error);
      });
    }, pollInterval);

    return () => {
      clearInterval(intervalId);
    };
  }, [project.id, fetchImages]);

  useEffect(() => {
    // Only re-load annotations when the current image index changes,
    // avoid repeated loading when image list status updates
    // Also reload when images array changes (new images added/deleted)
    fetchAnnotations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentImageIndex, images]);

  // Preload neighbor images to make switching smoother
  useEffect(() => {
    if (currentImageIndex < 0 || images.length === 0) return;

    const preloadImage = (idx: number) => {
      const imgInfo = images[idx];
      if (!imgInfo) return;

      let imagePath = imgInfo.path;
      if (!imagePath.includes('raw/')) {
        imagePath = `raw/${imagePath}`;
      } else if (imagePath.startsWith(project.id + '/')) {
        const rawIndex = imagePath.indexOf('raw/');
        if (rawIndex !== -1) {
          imagePath = imagePath.substring(rawIndex);
        }
      }
      const imageUrl = imgInfo.path.startsWith('http')
        ? imgInfo.path
        : `${API_BASE_URL}/images/${project.id}/${imagePath}`;

      const img = new Image();
      img.src = imageUrl;
    };

    // Preload previous and next images (if exist)
    const neighbors = [currentImageIndex - 1, currentImageIndex + 1];
    neighbors.forEach(preloadImage);
  }, [currentImageIndex, images, project.id]);

  // Sync cache (when user operates on current image, ensure cache is also updated)
  useEffect(() => {
    const imageId = images[currentImageIndex]?.id;
    if (imageId) {
      annotationCacheRef.current[imageId] = annotations;
    }
  }, [annotations, currentImageIndex, images]);

  // Save annotations
  const saveAnnotations = useCallback(async () => {
    if (currentImageIndex < 0 || !images[currentImageIndex]) return;

    // Batch save logic can be implemented here
    // Currently Canvas component saves directly on create/update
  }, [currentImageIndex, images]);

  // Switch image
  const handleImageChange = useCallback(async (delta: number) => {
    const newIndex = currentImageIndex + delta;
    if (newIndex >= 0 && newIndex < images.length) {
      // Save current annotations
      await saveAnnotations();
      
      // Immediately clear annotations and selection state to avoid showing old annotations
      setAnnotations([]);
      setSelectedAnnotationId(null);
      
      // Switch image index, this will trigger useEffect to load new annotations
      setCurrentImageIndex(newIndex);
    }
  }, [currentImageIndex, images.length, saveAnnotations]);

  // Add annotations to history
  const addToHistory = useCallback((newAnnotations: Annotation[]) => {
    setHistory(prevHistory => {
      const newHistory = prevHistory.slice(0, historyIndex + 1);
      newHistory.push([...newAnnotations]);
      setHistoryIndex(newHistory.length - 1);
      return newHistory;
    });
  }, [historyIndex]);

  // Undo
  const handleUndo = useCallback(() => {
    if (historyIndex > 0) {
      const newIndex = historyIndex - 1;
      setHistoryIndex(newIndex);
      setAnnotations([...history[newIndex]]);
    }
  }, [historyIndex, history]);

  // Redo
  const handleRedo = useCallback(() => {
    if (historyIndex < history.length - 1) {
      const newIndex = historyIndex + 1;
      setHistoryIndex(newIndex);
      setAnnotations([...history[newIndex]]);
    }
  }, [historyIndex, history]);

  // Delete annotation
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
        // Remove annotation from local state
        setAnnotations(prevAnnotations => {
          const newAnnotations = prevAnnotations.filter(ann => ann.id !== annotationId);
          addToHistory(newAnnotations);
          return newAnnotations;
        });
        
        // If deleted annotation is currently selected, clear selection
        if (selectedAnnotationId === annotationId) {
          setSelectedAnnotationId(null);
        }
        
        // Re-fetch annotation list to ensure sync (optional, but safer)
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
        showError(`Delete failed: ${errorData.detail || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Failed to delete annotation:', error);
      showError('Error occurred while deleting annotation, please check network connection');
    }
  }, [selectedAnnotationId, currentImageIndex, images, project.id, addToHistory]);

  // Keyboard shortcut handling
  useEffect(() => {
    const handleKeyPress = (e: KeyboardEvent) => {
      // Prevent triggering in input fields
      if ((e.target as HTMLElement).tagName === 'INPUT' || 
          (e.target as HTMLElement).tagName === 'TEXTAREA') {
        return;
      }

      // Number keys to switch classes (1-9)
      if (e.key >= '1' && e.key <= '9' && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
        const keyIndex = parseInt(e.key) - 1; // Convert 1-9 to 0-8
        if (keyIndex < classes.length) {
          e.preventDefault();
          setSelectedClassId(classes[keyIndex].id);
        }
      }

      // Tool switching
      if (e.key === 'r' || e.key === 'R') {
        setCurrentTool('bbox');
      } else if (e.key === 'p' || e.key === 'P') {
        setCurrentTool('polygon');
      } else if (e.key === 'v' || e.key === 'V') {
        setCurrentTool('select');
      }

      // Navigation
      if ((e.key === 'a' || e.key === 'ArrowLeft') && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        handleImageChange(-1);
      } else if ((e.key === 'd' || e.key === 'ArrowRight') && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        handleImageChange(1);
      }

      // Show/hide annotations
      if (e.key === 'h' || e.key === 'H') {
        setShowAnnotations(!showAnnotations);
      }

      // Delete
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedAnnotationId) {
        e.preventDefault();
        handleDeleteAnnotation(selectedAnnotationId);
      }

      // Undo/redo
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        handleUndo();
      } else if ((e.ctrlKey || e.metaKey) && (e.key === 'Z' || (e.key === 'z' && e.shiftKey))) {
        e.preventDefault();
        handleRedo();
      }

      // Save
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        saveAnnotations();
      }
    };

    window.addEventListener('keydown', handleKeyPress);
    return () => window.removeEventListener('keydown', handleKeyPress);
  }, [currentImageIndex, images, selectedAnnotationId, showAnnotations, history, historyIndex, classes, handleDeleteAnnotation, handleImageChange, handleRedo, handleUndo, saveAnnotations]);

  const currentImage = currentImageIndex >= 0 ? images[currentImageIndex] : null;

  // Export dataset
  const handleExportDataset = async (exportType: 'yolo' | 'zip') => {
    setIsExporting(true);
    setShowExportMenu(false);
    
    try {
      if (exportType === 'yolo') {
        // First call export API to generate YOLO dataset
        const exportResponse = await fetch(`${API_BASE_URL}/projects/${project.id}/export/yolo`, {
          method: 'POST',
        });
        
        if (!exportResponse.ok) {
          const errorData = await exportResponse.json().catch(() => ({ detail: t('common.exportFailed', 'Export failed') }));
          throw new Error(errorData.detail || t('common.exportFailed', 'Export failed'));
        }
        
        // Wait a bit to ensure file is generated
        await new Promise(resolve => setTimeout(resolve, 500));
        
        // Download YOLO dataset zip
        const downloadResponse = await fetch(`${API_BASE_URL}/projects/${project.id}/export/yolo/download`);
        if (!downloadResponse.ok) {
          throw new Error('Download failed');
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
        showSuccess(t('annotation.exportYOLOSuccess', 'YOLO 数据集导出成功'));
      } else if (exportType === 'zip') {
        // Export dataset zip package
        const downloadResponse = await fetch(`${API_BASE_URL}/projects/${project.id}/export/zip`);
        if (!downloadResponse.ok) {
          const errorData = await downloadResponse.json().catch(() => ({ detail: t('common.exportFailed', 'Export failed') }));
          throw new Error(errorData.detail || t('common.exportFailed', 'Export failed'));
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
        showSuccess(t('annotation.exportZipSuccess', '数据集导出成功'));
      }
    } catch (error: any) {
      showError(`${t('common.exportFailed', 'Export failed')}: ${error.message}`);
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="annotation-workbench">
      <div className="workbench-header">
        <div className="header-left">
          <Button onClick={onBack} variant="secondary" size="sm" className="btn-back">
            <Icon component={IoArrowBack} /> {t('annotation.backToProjects')}
          </Button>
          <h2>{project.name}</h2>
          <span className="workbench-subtitle">{t('annotation.subtitle', '图像标注工作台')}</span>
        </div>
        <div className="header-right">
          <div className="header-info">
            {currentImage && (
              <div className="image-info-column">
                <span className="image-filename">{currentImage.filename}</span>
                <span className="image-resolution">
                  {currentImage.width} × {currentImage.height}
                </span>
              </div>
            )}
          </div>
          
          <MQTTGuide projectId={project.id} projectName={project.name} />
          
          <Button
            variant="primary"
            size="sm"
            className="btn-export"
            onClick={() => {
              if (onOpenTraining) {
                onOpenTraining(project.id);
              } else {
                setShowTrainingPanel(true);
              }
            }}
          >
            <Icon component={IoRocket} />
            <span>{t('annotation.trainModel')}</span>
          </Button>
          
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setShowDataSourceModal(true)}
          >
            <Icon component={IoLink} />
            <span>{t('project.dataSource.addSource', '添加数据源')}</span>
          </Button>
          
          <Button
            variant="secondary"
            size="sm"
            className="btn-export"
            onClick={() => setShowImportModal(true)}
          >
            <Icon component={IoCloudUpload} />
            <span>{t('annotation.importDataset', '导入数据集')}</span>
          </Button>
          
          <div className="export-dropdown" ref={exportMenuRef}>
            <Button
              ref={exportMenuButtonRef}
              variant="secondary"
              size="sm"
              className="btn-export"
              onClick={() => setShowExportMenu(!showExportMenu)}
              disabled={isExporting}
              aria-expanded={showExportMenu}
            >
              <Icon component={IoDownload} />
              <span>{isExporting ? t('annotation.exporting') : t('annotation.exportDataset')}</span>
              <Icon component={IoChevronDown} />
            </Button>
            
            {showExportMenu && exportMenuPosition && createPortal(
              <div 
                ref={exportMenuDropdownRef}
                className="dropdown-menu"
                style={{
                  top: `${exportMenuPosition.top}px`,
                  left: `${exportMenuPosition.left}px`,
                  width: exportMenuPosition.width ? `${exportMenuPosition.width}px` : 'auto',
                }}
                onClick={(e) => {
                  // Prevent event from bubbling to document, which would trigger handleClickOutside
                  e.stopPropagation();
                }}
              >
                <Button
                  variant="ghost"
                  size="sm"
                  className="dropdown-item"
                  onClick={() => handleExportDataset('yolo')}
                  disabled={isExporting}
                >
                  {t('annotation.exportYOLO')}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="dropdown-item"
                  onClick={() => handleExportDataset('zip')}
                  disabled={isExporting}
                >
                  {t('annotation.exportZIP')}
                </Button>
              </div>,
              document.body
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
            <div className="no-image">{t('annotation.noImages')}</div>
          )}
          <ShortcutHelper />
        </div>

        <ControlPanel
          annotations={annotations}
          classes={classes}
          images={images}
          currentImageIndex={currentImageIndex}
          selectedAnnotationId={selectedAnnotationId}
          selectedClassId={selectedClassId}
          onImageSelect={(index) => {
            saveAnnotations();
            setCurrentImageIndex(index);
            setSelectedAnnotationId(null);
          }}
          onAnnotationSelect={setSelectedAnnotationId}
          onAnnotationVisibilityChange={(id, visible) => {
            // Can implement individual annotation show/hide here
          }}
          onAnnotationDelete={handleDeleteAnnotation}
          onClassSelect={(classId) => {
            setSelectedClassId(classId);
            // If annotation is selected, update annotation's class
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
      
      {showTrainingPanel && (
        <TrainingPanel
          projectId={project.id}
          onClose={() => setShowTrainingPanel(false)}
        />
      )}
      
      <DatasetImportModal
        isOpen={showImportModal}
        onClose={() => setShowImportModal(false)}
        defaultProjectId={project.id}
        onImportComplete={() => {
          setShowImportModal(false);
          fetchImages();
          fetchClasses();
        }}
      />

      <DataSourceManager
        projectId={project.id}
        isOpen={showDataSourceModal}
        onClose={() => setShowDataSourceModal(false)}
        onUpdate={() => {
          // Refresh image list when devices are bound/unbound
          fetchImages();
        }}
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

