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
  const [activeTab, setActiveTab] = useState<'objects' | 'classes' | 'files'>('classes');
  const [newClassName, setNewClassName] = useState('');
  const [newClassColor, setNewClassColor] = useState('#4a9eff');
  const [isUploading, setIsUploading] = useState(false);
  const [isDeleting, setIsDeleting] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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

    const file = files[0];
    
    // 验证文件类型
    const allowedTypes = ['image/jpeg', 'image/jpg', 'image/png', 'image/bmp', 'image/gif', 'image/webp'];
    if (!allowedTypes.includes(file.type)) {
      alert('不支持的文件格式。请选择 JPG、PNG、BMP、GIF 或 WEBP 格式的图像。');
      return;
    }

    // 验证文件大小（10MB）
    const maxSize = 10 * 1024 * 1024;
    if (file.size > maxSize) {
      alert(`文件太大。最大支持 ${maxSize / 1024 / 1024}MB。`);
      return;
    }

    setIsUploading(true);
    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/images/upload`, {
        method: 'POST',
        body: formData,
      });

      if (response.ok) {
        const result = await response.json();
        if (onImageUpload) {
          onImageUpload();
        }
        // 清空文件输入，允许重复上传同一文件
        if (fileInputRef.current) {
          fileInputRef.current.value = '';
        }
      } else {
        const errorData = await response.json().catch(() => ({ detail: '上传失败' }));
        alert(errorData.detail || '图像上传失败');
      }
    } catch (error) {
      console.error('Failed to upload image:', error);
      alert('上传失败：无法连接到服务器');
    } finally {
      setIsUploading(false);
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
        onCreateClass();
      } else {
        alert('创建类别失败');
      }
    } catch (error) {
      console.error('Failed to create class:', error);
      alert('创建类别失败');
    }
  };

  return (
    <div className="control-panel">
      <div className="panel-tabs">
        <button
          className={`tab-button ${activeTab === 'objects' ? 'active' : ''}`}
          onClick={() => setActiveTab('objects')}
        >
          标注列表
        </button>
        <button
          className={`tab-button ${activeTab === 'classes' ? 'active' : ''}`}
          onClick={() => setActiveTab('classes')}
        >
          类别
        </button>
        <button
          className={`tab-button ${activeTab === 'files' ? 'active' : ''}`}
          onClick={() => setActiveTab('files')}
        >
          文件
        </button>
      </div>

      <div className="panel-content">
        {activeTab === 'objects' && (
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
        )}

        {activeTab === 'classes' && (
          <div className="class-palette">
            <h3>类别 ({classes.length})</h3>
            {classes.length === 0 ? (
              <div className="empty-state">请创建一个类别</div>
            ) : (
              <div className="class-list">
                {classes.map((cls) => (
                  <div
                    key={cls.id}
                    className="class-item"
                    onClick={() => onClassSelect(cls.id)}
                  >
                    <div
                      className="class-color"
                      style={{ backgroundColor: cls.color }}
                    />
                    <span className="class-name">{cls.name}</span>
                    {cls.shortcutKey && (
                      <span className="class-shortcut">{cls.shortcutKey}</span>
                    )}
                  </div>
                ))}
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
        )}

        {activeTab === 'files' && (
          <div className="file-navigator">
            <div className="file-header">
              <h3>图像列表 ({images.length})</h3>
              <button
                onClick={handleUploadClick}
                disabled={isUploading}
                className="btn-upload"
                title="上传图像"
              >
                {isUploading ? '上传中...' : '上传图片'}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/jpg,image/png,image/bmp,image/gif,image/webp"
                onChange={handleFileSelect}
                style={{ display: 'none' }}
                multiple={false}
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
        )}
      </div>
    </div>
  );
};

