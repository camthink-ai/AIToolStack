import React, { useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Annotation, ImageInfo, Class } from './AnnotationWorkbench';
import { API_BASE_URL } from '../config';
import { IoTrash } from 'react-icons/io5';
import './ControlPanel.css';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { FormField } from '../ui/FormField';
import { Alert } from '../ui/Alert';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { useAlert } from '../hooks/useAlert';
import { useConfirm } from '../hooks/useConfirm';

// Icon component wrapper to resolve TypeScript type issues
const Icon: React.FC<{ component: React.ComponentType<any> }> = ({ component: Component }) => {
  return <Component />;
};

interface ControlPanelProps {
  annotations: Annotation[];
  classes: Class[];
  images: ImageInfo[];
  currentImageIndex: number;
  selectedAnnotationId: number | null;
  selectedClassId: number | null;
  onImageSelect: (index: number) => void;
  onAnnotationSelect: (id: number | null) => void;
  onAnnotationVisibilityChange: (id: number, visible: boolean) => void;
  onAnnotationDelete?: (id: number) => void;
  onClassSelect: (classId: number) => void;
  projectId: string;
  onCreateClass: () => void;
  onImageUpload?: () => void;
  onImageDelete?: () => void;
}

export const ControlPanel: React.FC<ControlPanelProps> = ({
  annotations,
  classes,
  images,
  currentImageIndex,
  selectedAnnotationId,
  selectedClassId,
  onImageSelect,
  onAnnotationSelect,
  onAnnotationVisibilityChange,
  onAnnotationDelete,
  onClassSelect,
  projectId,
  onCreateClass,
  onImageUpload,
  onImageDelete
}) => {
  const { t } = useTranslation();
  const { alertState, showSuccess, showError, showWarning, closeAlert } = useAlert();
  const { confirmState, showConfirm, closeConfirm } = useConfirm();
  const [newClassName, setNewClassName] = useState('');
  const [newClassColor, setNewClassColor] = useState('#EB814F');
  const [isUploading, setIsUploading] = useState(false);
  const [isDeleting, setIsDeleting] = useState<number | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{
    current: number;
    total: number;
    currentFileName: string;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Generate random color (100 colors)
  const generateRandomColor = (): string => {
    const colors = [
      // Red series
      '#FF6B6B', '#E74C3C', '#C0392B', '#FF4757', '#FF3838',
      '#FF6348', '#FF5733', '#FF4444', '#FF1744', '#D32F2F',
      // Orange series
      '#FFA07A', '#F39C12', '#E67E22', '#D35400', '#FF8C00',
      '#FF7F50', '#FF6B35', '#FF8C42', '#FF9500', '#FF6F00',
      // Yellow series
      '#F7DC6F', '#F1C40F', '#F39C12', '#FFD700', '#FFC107',
      '#FFEB3B', '#FFD54F', '#FFCA28', '#FFC300', '#FFD700',
      // Green series
      '#52BE80', '#1ABC9C', '#16A085', '#27AE60', '#2ECC71',
      '#4CAF50', '#8BC34A', '#66BB6A', '#81C784', '#A5D6A7',
      // Cyan/teal series
      '#EB814F', '#d46a2f', '#f59a6b', '#ffb891', '#ffc9a8',
      '#00ACC1', '#0097A7', '#00838F', '#26C6DA', '#4DD0E1',
      // Blue series
      '#0A0D16', '#1a1d26', '#2a2d36', '#3a3d46', '#4a4d56',
      '#EB814F', '#d46a2f', '#c55a1f', '#f59a6b', '#ffb891',
      // Purple series
      '#9B59B6', '#8E44AD', '#BB8FCE', '#7B1FA2', '#6A1B9A',
      '#9C27B0', '#8E24AA', '#AB47BC', '#BA68C8', '#CE93D8',
      // Pink series
      '#E91E63', '#C2185B', '#F06292', '#EC407A', '#F48FB1',
      '#F8BBD0', '#FF4081', '#E91E63', '#AD1457', '#880E4F',
      // Brown series
      '#8D6E63', '#6D4C41', '#5D4037', '#795548', '#A1887F',
      '#BCAAA4', '#D7CCC8', '#8B4513', '#A0522D', '#CD853F',
      // Gray series
      '#7F8C8D', '#34495E', '#2C3E50', '#95A5A6', '#BDC3C7',
      '#78909C', '#607D8B', '#546E7A', '#455A64', '#37474F',
      // Dark series
      '#2C3E50', '#34495E', '#1A1A1A', '#212121', '#263238',
      '#37474F', '#455A64', '#546E7A', '#607D8B', '#78909C',
      // Light series
      '#F5F5F5', '#FAFAFA', '#FFFFFF', '#E0E0E0', '#BDBDBD',
      '#9E9E9E', '#757575', '#616161', '#424242', '#212121',
      // Special color series
      '#00E676', '#00C853', '#76FF03', '#C6FF00', '#FFEA00',
      '#FFC400', '#FF9100', '#FF3D00', '#D50000', '#C51162',
      '#EB814F', '#d46a2f', '#0A0D16', '#1a1d26', '#f59a6b',
      '#00B8D4', '#00BFA5', '#00C853', '#64DD17', '#AEEA00',
      '#FFD600', '#FFAB00', '#FF6D00', '#DD2C00', '#D50000'
    ];
    return colors[Math.floor(Math.random() * colors.length)];
  };

  const handleDeleteImage = async (imageId: number, event: React.MouseEvent) => {
    event.stopPropagation(); // Prevent triggering image selection

    showConfirm(
      t('annotation.deleteImageConfirm'),
      async () => {
        setIsDeleting(imageId);
        try {
          const response = await fetch(`${API_BASE_URL}/projects/${projectId}/images/${imageId}`, {
            method: 'DELETE'
          });
          
          if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || t('annotation.deleteFailed'));
          }
          
          // Notify parent component to refresh image list
          if (onImageDelete) {
            onImageDelete();
          }
        } catch (error: any) {
          showError(`${t('annotation.deleteFailed')}: ${error.message}`);
        } finally {
          setIsDeleting(null);
        }
      },
      {
        variant: 'danger',
      }
    );
  };

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    const fileArray = Array.from(files);
    
    // Validate all files
    const allowedTypes = ['image/jpeg', 'image/jpg', 'image/png', 'image/bmp', 'image/gif', 'image/webp'];
    const maxSize = 10 * 1024 * 1024; // 10MB
    
    const invalidFiles: string[] = [];
    const validFiles: File[] = [];
    
    fileArray.forEach(file => {
    if (!allowedTypes.includes(file.type)) {
        invalidFiles.push(`${file.name}（${t('annotation.fileTypeNotSupported')}）`);
      } else if (file.size > maxSize) {
        invalidFiles.push(`${file.name}（${t('annotation.fileTooLarge', { size: maxSize / 1024 / 1024 })}）`);
      } else {
        validFiles.push(file);
      }
    });

    // Show invalid file warnings
    if (invalidFiles.length > 0) {
      showWarning(`${t('annotation.invalidFiles')}：\n${invalidFiles.join('\n')}\n\n${t('annotation.skipInvalidFiles')}`);
    }

    if (validFiles.length === 0) {
      // Clear file input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      return;
    }

    setIsUploading(true);
    setUploadProgress({ current: 0, total: validFiles.length, currentFileName: '' });

    let successCount = 0;
    let failCount = 0;
    const errors: string[] = [];

    // Upload files one by one
    for (let i = 0; i < validFiles.length; i++) {
      const file = validFiles[i];
      setUploadProgress({
        current: i + 1,
        total: validFiles.length,
        currentFileName: file.name
      });

    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/images/upload`, {
        method: 'POST',
        body: formData,
      });

      if (response.ok) {
          successCount++;
        } else {
          failCount++;
          const errorData = await response.json().catch(() => ({ detail: 'Upload failed' }));
          errors.push(`${file.name}: ${errorData.detail || 'Upload failed'}`);
        }
      } catch (error) {
        failCount++;
        errors.push(`${file.name}: Unable to connect to server`);
        console.error(`Failed to upload ${file.name}:`, error);
        }
    }

        // Clear file input to allow re-uploading same file
        if (fileInputRef.current) {
          fileInputRef.current.value = '';
        }

    // Refresh image list
    if (successCount > 0 && onImageUpload) {
      onImageUpload();
      }

    // Show upload results
    setUploadProgress(null);
      setIsUploading(false);

    if (successCount > 0 && failCount === 0) {
      // All successful
      showSuccess(t('annotation.uploadSuccess', { count: successCount }));
    } else if (successCount > 0 && failCount > 0) {
      // Partially successful
      showWarning(`${t('annotation.uploadPartial', { success: successCount, fail: failCount })}\n\n${t('common.errorDetails', 'Error details')}:\n${errors.join('\n')}`);
    } else {
      // All failed
      showError(`${t('annotation.uploadAllFailed', { count: failCount })}\n${errors.join('\n')}`);
    }
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleCreateClass = async () => {
    if (!newClassName.trim()) {
      showWarning(t('annotation.classNameRequired'));
      return;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/classes`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name: newClassName,
          color: newClassColor,
        }),
      });

      if (response.ok) {
        setNewClassName('');
        setNewClassColor(generateRandomColor()); // Reset to random color
        onCreateClass();
      } else {
        showError(t('annotation.createClassFailed'));
      }
    } catch (error) {
      console.error('Failed to create class:', error);
      showError(t('annotation.createClassFailed'));
    }
  };

  const handleDeleteClass = async (classId: number, event: React.MouseEvent) => {
    event.stopPropagation(); // Prevent triggering class selection
    
    const classToDelete = classes.find(c => c.id === classId);
    if (!classToDelete) return;

    showConfirm(
      t('annotation.deleteClassConfirm', { name: classToDelete.name }),
      async () => {
        try {
          const response = await fetch(`${API_BASE_URL}/projects/${projectId}/classes/${classId}`, {
            method: 'DELETE'
          });
          
          if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || t('annotation.deleteFailed'));
          }
          
          onCreateClass(); // Refresh class list
        } catch (error: any) {
          showError(`${t('annotation.deleteClassFailed')}: ${error.message}`);
        }
      },
      {
        variant: 'danger',
      }
    );
  };

  return (
    <div className="control-panel">
      <div className="panel-content">
        {/* 左列：类别管理和标注列表 */}
        <div className="panel-left-column">
          {/* 类别管理 */}
          <div className="class-palette">
            <h3>{t('annotation.classes')} ({classes.length})</h3>
            {classes.length === 0 ? (
              <div className="empty-state">{t('annotation.noClasses')}</div>
            ) : (
              <div className="class-list">
                {classes.map((cls, index) => {
                  // Automatically assign shortcut keys to first 9 classes (if not set)
                  const shortcutKey = cls.shortcutKey || (index < 9 ? String(index + 1) : null);
                  return (
                    <div
                      key={cls.id}
                      className={`class-item ${selectedClassId === cls.id ? 'selected' : ''}`}
                      onClick={() => onClassSelect(cls.id)}
                    >
                      <div
                        className="class-color"
                        style={{ backgroundColor: cls.color }}
                      />
                      <span className="class-name">{cls.name}</span>
                      {shortcutKey && (
                        <span className="class-shortcut">{shortcutKey}</span>
                      )}
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="class-delete-btn"
                        onClick={(e) => handleDeleteClass(cls.id, e)}
                        title={t('annotation.deleteClass')}
                      >
                        <Icon component={IoTrash} />
                      </Button>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="create-class">
              <h4>{t('annotation.createClass')}</h4>
              <FormField >
                <Input
                  type="text"
                  placeholder={t('annotation.className')}
                  value={newClassName}
                  onChange={(e) => setNewClassName(e.target.value)}
                  className="class-input"
                />
              </FormField>
              <div className="color-input-group">
                <Input
                  type="color"
                  value={newClassColor}
                  onChange={(e) => setNewClassColor(e.target.value)}
                  className="color-picker"
                />
                <Input
                  type="text"
                  value={newClassColor}
                  onChange={(e) => setNewClassColor(e.target.value)}
                  className="color-text"
                />
              </div>
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={handleCreateClass}
                className="btn-create-class"
              >
                {t('common.create', '创建')}
              </Button>
            </div>
          </div>

          {/* 标注列表 */}
          <div className="object-list">
            <h3>{t('annotation.annotations')} ({annotations.length})</h3>
            {annotations.length === 0 ? (
              <div className="empty-state">{t('annotation.noData')}</div>
            ) : (
              <div className="annotation-items">
                {annotations.map((ann) => {
                  const classObj = classes.find(c => c.id === ann.classId);
                  return (
                    <div
                      key={ann.id}
                      className={`annotation-item ${selectedAnnotationId === ann.id ? 'selected' : ''}`}
                    >
                      <div
                        className="annotation-content"
                        onClick={() => onAnnotationSelect(ann.id || null)}
                      >
                        <div
                          className="annotation-color"
                          style={{ backgroundColor: classObj?.color || '#888' }}
                        />
                        <div className="annotation-info">
                          <div className="annotation-class">{classObj?.name || t('annotation.unknownClass')}</div>
                          <div className="annotation-type">{ann.type}</div>
                        </div>
                      </div>
                      {onAnnotationDelete && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="annotation-delete-btn"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (ann.id) {
                              showConfirm(
                                t('annotation.deleteConfirm', '确定要删除这个标注吗？'),
                                () => {
                                  onAnnotationDelete(ann.id!);
                                },
                                {
                                  variant: 'danger',
                                }
                              );
                            }
                          }}
                          title={t('annotation.delete')}
                        >
                          <Icon component={IoTrash} />
                        </Button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* 右列：图像文件 */}
        <div className="panel-right-column">
          <div className="file-navigator">
            <div className="file-header">
              <div style={{ flex: 1 }}>
              <h3>{t('annotation.images')} ({images.length})</h3>
                {uploadProgress && (
                  <div className="upload-progress-info">
                    {t('annotation.uploading')}: {uploadProgress.currentFileName} ({uploadProgress.current}/{uploadProgress.total})
                  </div>
                )}
              </div>
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={handleUploadClick}
                disabled={isUploading}
                className="btn-upload"
                title={t('annotation.uploadImages')}
              >
                {isUploading && uploadProgress ? (
                  `${t('annotation.uploading')} (${uploadProgress.current}/${uploadProgress.total})...`
                ) : isUploading ? (
                  t('annotation.uploading')
                ) : (
                  t('annotation.uploadImages')
                )}
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/jpg,image/png,image/bmp,image/gif,image/webp"
                onChange={handleFileSelect}
                style={{ display: 'none' }}
                multiple={true}
              />
            </div>
            <div className="file-list">
              {images.map((img, index) => {
                const isLabeled = img.status === 'LABELED';
                const isCurrent = index === currentImageIndex;
                const isDeletingThis = isDeleting === img.id;
                
                return (
                  <div
                    key={img.id}
                    className={`file-item ${isCurrent ? 'current' : ''}`}
                    onClick={() => onImageSelect(index)}
                  >
                    <div className="file-status">
                      <div className={`status-dot ${isLabeled ? 'labeled' : 'unlabeled'}`} />
                    </div>
                    <div className="file-info">
                      <div className="file-name">{img.filename}</div>
                      <div className="file-meta">
                        {img.width} × {img.height}
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="file-delete-btn"
                      onClick={(e) => handleDeleteImage(img.id, e)}
                      disabled={isDeletingThis}
                      title={t('annotation.deleteImage')}
                    >
                      {isDeletingThis ? '...' : <Icon component={IoTrash} />}
                    </Button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

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

      {/* Confirm Dialog */}
      <ConfirmDialog
        open={confirmState.open}
        onOpenChange={closeConfirm}
        title={confirmState.title}
        message={confirmState.message}
        confirmText={confirmState.confirmText}
        cancelText={confirmState.cancelText}
        onConfirm={confirmState.onConfirm || (() => {})}
        onCancel={confirmState.onCancel}
        variant={confirmState.variant}
      />
    </div>
  );
};

