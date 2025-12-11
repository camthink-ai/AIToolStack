import React, { useState, useRef } from 'react';
import { Annotation, ImageInfo, Class } from './AnnotationWorkbench';
import { API_BASE_URL } from '../config';
import { IoTrash } from 'react-icons/io5';
import './ControlPanel.css';

// 图标组件包装器，解决 TypeScript 类型问题
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
  const [newClassName, setNewClassName] = useState('');
  const [newClassColor, setNewClassColor] = useState('#4a9eff');
  const [isUploading, setIsUploading] = useState(false);
  const [isDeleting, setIsDeleting] = useState<number | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{
    current: number;
    total: number;
    currentFileName: string;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 生成随机颜色（100种颜色）
  const generateRandomColor = (): string => {
    const colors = [
      // 红色系
      '#FF6B6B', '#E74C3C', '#C0392B', '#FF4757', '#FF3838',
      '#FF6348', '#FF5733', '#FF4444', '#FF1744', '#D32F2F',
      // 橙色系
      '#FFA07A', '#F39C12', '#E67E22', '#D35400', '#FF8C00',
      '#FF7F50', '#FF6B35', '#FF8C42', '#FF9500', '#FF6F00',
      // 黄色系
      '#F7DC6F', '#F1C40F', '#F39C12', '#FFD700', '#FFC107',
      '#FFEB3B', '#FFD54F', '#FFCA28', '#FFC300', '#FFD700',
      // 绿色系
      '#52BE80', '#1ABC9C', '#16A085', '#27AE60', '#2ECC71',
      '#4CAF50', '#8BC34A', '#66BB6A', '#81C784', '#A5D6A7',
      // 青色/蓝绿色系
      '#4ECDC4', '#45B7D1', '#1ABC9C', '#16A085', '#00BCD4',
      '#00ACC1', '#0097A7', '#00838F', '#26C6DA', '#4DD0E1',
      // 蓝色系
      '#3498DB', '#2980B9', '#45B7D1', '#5DADE2', '#85C1E2',
      '#2196F3', '#1976D2', '#0D47A1', '#42A5F5', '#64B5F6',
      // 紫色系
      '#9B59B6', '#8E44AD', '#BB8FCE', '#7B1FA2', '#6A1B9A',
      '#9C27B0', '#8E24AA', '#AB47BC', '#BA68C8', '#CE93D8',
      // 粉色系
      '#E91E63', '#C2185B', '#F06292', '#EC407A', '#F48FB1',
      '#F8BBD0', '#FF4081', '#E91E63', '#AD1457', '#880E4F',
      // 棕色系
      '#8D6E63', '#6D4C41', '#5D4037', '#795548', '#A1887F',
      '#BCAAA4', '#D7CCC8', '#8B4513', '#A0522D', '#CD853F',
      // 灰色系
      '#7F8C8D', '#34495E', '#2C3E50', '#95A5A6', '#BDC3C7',
      '#78909C', '#607D8B', '#546E7A', '#455A64', '#37474F',
      // 深色系
      '#2C3E50', '#34495E', '#1A1A1A', '#212121', '#263238',
      '#37474F', '#455A64', '#546E7A', '#607D8B', '#78909C',
      // 亮色系
      '#F5F5F5', '#FAFAFA', '#FFFFFF', '#E0E0E0', '#BDBDBD',
      '#9E9E9E', '#757575', '#616161', '#424242', '#212121',
      // 特殊色系
      '#00E676', '#00C853', '#76FF03', '#C6FF00', '#FFEA00',
      '#FFC400', '#FF9100', '#FF3D00', '#D50000', '#C51162',
      '#AA00FF', '#6200EA', '#304FFE', '#2962FF', '#0091EA',
      '#00B8D4', '#00BFA5', '#00C853', '#64DD17', '#AEEA00',
      '#FFD600', '#FFAB00', '#FF6D00', '#DD2C00', '#D50000'
    ];
    return colors[Math.floor(Math.random() * colors.length)];
  };

  const handleDeleteImage = async (imageId: number, event: React.MouseEvent) => {
    event.stopPropagation(); // 阻止触发图片选择
    
    if (!window.confirm('确定要删除这张图片吗？删除后无法恢复。')) {
      return;
    }
    
    setIsDeleting(imageId);
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/images/${imageId}`, {
        method: 'DELETE'
      });
      
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '删除失败');
      }
      
      // 通知父组件刷新图片列表
      if (onImageDelete) {
        onImageDelete();
      }
    } catch (error: any) {
      alert(`删除失败: ${error.message}`);
    } finally {
      setIsDeleting(null);
    }
  };

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    const fileArray = Array.from(files);
    
    // 验证所有文件
    const allowedTypes = ['image/jpeg', 'image/jpg', 'image/png', 'image/bmp', 'image/gif', 'image/webp'];
    const maxSize = 10 * 1024 * 1024; // 10MB
    
    const invalidFiles: string[] = [];
    const validFiles: File[] = [];
    
    fileArray.forEach(file => {
    if (!allowedTypes.includes(file.type)) {
        invalidFiles.push(`${file.name}（格式不支持）`);
      } else if (file.size > maxSize) {
        invalidFiles.push(`${file.name}（超过 ${maxSize / 1024 / 1024}MB）`);
      } else {
        validFiles.push(file);
      }
    });

    // 显示无效文件提示
    if (invalidFiles.length > 0) {
      alert(`以下文件无法上传：\n${invalidFiles.join('\n')}\n\n将跳过这些文件，继续上传其他有效文件。`);
    }

    if (validFiles.length === 0) {
      // 清空文件输入
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

    // 逐个上传文件
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
          const errorData = await response.json().catch(() => ({ detail: '上传失败' }));
          errors.push(`${file.name}: ${errorData.detail || '上传失败'}`);
        }
      } catch (error) {
        failCount++;
        errors.push(`${file.name}: 无法连接到服务器`);
        console.error(`Failed to upload ${file.name}:`, error);
        }
    }

        // 清空文件输入，允许重复上传同一文件
        if (fileInputRef.current) {
          fileInputRef.current.value = '';
        }

    // 刷新图片列表
    if (successCount > 0 && onImageUpload) {
      onImageUpload();
      }

    // 显示上传结果
    setUploadProgress(null);
      setIsUploading(false);

    if (successCount > 0 && failCount === 0) {
      // 全部成功
      alert(`成功上传 ${successCount} 张图片`);
    } else if (successCount > 0 && failCount > 0) {
      // 部分成功
      alert(`上传完成：成功 ${successCount} 张，失败 ${failCount} 张\n\n失败详情：\n${errors.join('\n')}`);
    } else {
      // 全部失败
      alert(`上传失败（${failCount} 张）：\n${errors.join('\n')}`);
    }
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleCreateClass = async () => {
    if (!newClassName.trim()) {
      alert('请输入类别名称');
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
        setNewClassColor(generateRandomColor()); // 重置为随机颜色
        onCreateClass();
      } else {
        alert('创建类别失败');
      }
    } catch (error) {
      console.error('Failed to create class:', error);
      alert('创建类别失败');
    }
  };

  const handleDeleteClass = async (classId: number, event: React.MouseEvent) => {
    event.stopPropagation(); // 阻止触发类别选择
    
    const classToDelete = classes.find(c => c.id === classId);
    if (!classToDelete) return;
    
    if (!window.confirm(`确定要删除类别"${classToDelete.name}"吗？如果该类别已被使用，将无法删除。`)) {
      return;
    }
    
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/classes/${classId}`, {
        method: 'DELETE'
      });
      
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '删除失败');
      }
      
      onCreateClass(); // 刷新类别列表
    } catch (error: any) {
      alert(`删除类别失败: ${error.message}`);
    }
  };

  return (
    <div className="control-panel">
      <div className="panel-content">
        {/* 左列：类别管理和标注列表 */}
        <div className="panel-left-column">
          {/* 类别管理 */}
          <div className="class-palette">
            <h3>标注类别 ({classes.length})</h3>
            {classes.length === 0 ? (
              <div className="empty-state">请创建一个类别</div>
            ) : (
              <div className="class-list">
                {classes.map((cls, index) => {
                  // 自动为前9个类别分配快捷键（如果没有设置）
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
                      <button
                        className="class-delete-btn"
                        onClick={(e) => handleDeleteClass(cls.id, e)}
                        title="删除类别"
                      >
                        <Icon component={IoTrash} />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="create-class">
              <h4>创建新类别</h4>
              <input
                type="text"
                placeholder="类别名称"
                value={newClassName}
                onChange={(e) => setNewClassName(e.target.value)}
                className="class-input"
              />
              <div className="color-input-group">
                <input
                  type="color"
                  value={newClassColor}
                  onChange={(e) => setNewClassColor(e.target.value)}
                  className="color-picker"
                />
                <input
                  type="text"
                  value={newClassColor}
                  onChange={(e) => setNewClassColor(e.target.value)}
                  className="color-text"
                />
              </div>
              <button onClick={handleCreateClass} className="btn-create-class">
                创建
              </button>
            </div>
          </div>

          {/* 标注列表 */}
          <div className="object-list">
            <h3>标注列表 ({annotations.length})</h3>
            {annotations.length === 0 ? (
              <div className="empty-state">暂无标注</div>
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
                          <div className="annotation-class">{classObj?.name || '未知'}</div>
                          <div className="annotation-type">{ann.type}</div>
                        </div>
                      </div>
                      {onAnnotationDelete && (
                        <button
                          className="annotation-delete-btn"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (ann.id && window.confirm('确定要删除这个标注吗？')) {
                              onAnnotationDelete(ann.id);
                            }
                          }}
                          title="删除标注"
                        >
                          <Icon component={IoTrash} />
                        </button>
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
              <h3>图像列表 ({images.length})</h3>
                {uploadProgress && (
                  <div className="upload-progress-info">
                    正在上传: {uploadProgress.currentFileName} ({uploadProgress.current}/{uploadProgress.total})
                  </div>
                )}
              </div>
              <button
                onClick={handleUploadClick}
                disabled={isUploading}
                className="btn-upload"
                title="上传图像（支持批量选择）"
              >
                {isUploading && uploadProgress ? (
                  `上传中 (${uploadProgress.current}/${uploadProgress.total})...`
                ) : isUploading ? (
                  '上传中...'
                ) : (
                  '上传图片'
                )}
              </button>
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
                    <button
                      className="file-delete-btn"
                      onClick={(e) => handleDeleteImage(img.id, e)}
                      disabled={isDeletingThis}
                      title="删除图片"
                    >
                      {isDeletingThis ? '...' : <Icon component={IoTrash} />}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

