import React, { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { API_BASE_URL } from '../config';
import { IoClose } from 'react-icons/io5';
import './DatasetImportModal.css';

interface DatasetImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImportComplete?: () => void;
  defaultProjectId?: string; // 默认选中的项目ID
}

interface UploadProgress {
  current: number;
  total: number;
  currentFileName: string;
}

export const DatasetImportModal: React.FC<DatasetImportModalProps> = ({
  isOpen,
  onClose,
  onImportComplete,
  defaultProjectId
}) => {
  const { t } = useTranslation();
  const [selectedProjectId, setSelectedProjectId] = useState<string>(defaultProjectId || '');
  const [importFormat, setImportFormat] = useState<'coco' | 'yolo' | 'project_zip'>('project_zip');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setSelectedProjectId(defaultProjectId || '');
    } else {
      // 重置状态
      setSelectedProjectId(defaultProjectId || '');
      setImportFormat('project_zip');
      setUploadProgress(null);
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  }, [isOpen, defaultProjectId]);

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    if (!selectedProjectId) {
      alert(t('annotation.import.selectProjectFirst', '请先选择项目'));
      return;
    }

    // Handle dataset format import (COCO / YOLO / Project ZIP)
    if (files.length !== 1) {
      alert(t('annotation.import.singleFileRequired', '请选择一个数据集文件'));
      return;
    }

    const file = files[0];
    const formData = new FormData();
    formData.append('file', file);

    setIsUploading(true);
    setUploadProgress({ current: 1, total: 1, currentFileName: file.name });

    try {
      const response = await fetch(
        `${API_BASE_URL}/projects/${selectedProjectId}/dataset/import?format_type=${importFormat}`,
        {
          method: 'POST',
          body: formData,
        }
      );

      if (response.ok) {
        const result = await response.json();
        const message = t('annotation.import.importSuccess', {
          images: result.images_imported,
          annotations: result.annotations_imported,
          classes: result.classes_created,
          defaultValue: `导入成功：${result.images_imported} 张图片，${result.annotations_imported} 个标注，${result.classes_created} 个类别`
        });
        
        if (result.errors && result.errors.length > 0) {
          alert(`${message}\n\n${t('common.errorDetails', '失败详情')}：\n${result.errors.slice(0, 5).join('\n')}`);
        } else {
          alert(message);
        }
        
        if (onImportComplete) {
          onImportComplete();
        }
        onClose();
      } else {
        const errorData = await response.json().catch(() => ({ detail: t('annotation.import.importFailed', '导入失败') }));
        alert(`${t('annotation.import.importFailed', '导入失败')}: ${errorData.detail || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Failed to import dataset:', error);
      alert(`${t('annotation.import.importFailed', '导入失败')}: ${t('common.connectionError', '无法连接到服务器')}`);
    } finally {
      setUploadProgress(null);
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleUploadClick = () => {
    if (!selectedProjectId) {
      alert(t('annotation.import.selectProjectFirst', '请先选择项目'));
      return;
    }
    fileInputRef.current?.click();
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content dataset-import-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{t('annotation.import.title', '导入数据集')}</h2>
          <button className="modal-close" onClick={onClose}>
            <IoClose />
          </button>
        </div>

        <div className="modal-body dataset-import-body">
          <div className="form-group">
            <label>{t('annotation.import.selectFormat', '选择导入格式')}</label>
            <select
              className="dataset-import-select"
              value={importFormat}
              onChange={(e) => setImportFormat(e.target.value as 'coco' | 'yolo' | 'project_zip')}
              disabled={isUploading}
            >
              <option value="coco">{t('annotation.import.formatCOCO', 'COCO 格式 (JSON)')}</option>
              <option value="yolo">{t('annotation.import.formatYOLO', 'YOLO 格式 (ZIP/目录)')}</option>
              <option value="project_zip">{t('annotation.import.formatProject', '项目导出 ZIP')}</option>
            </select>
            <p className="dataset-import-hint">
              {importFormat === 'coco' && t('annotation.import.hintCOCO', '上传 COCO 格式的 JSON 文件（包含 images、annotations、categories）')}
              {importFormat === 'yolo' && t('annotation.import.hintYOLO', '上传 YOLO 格式的 ZIP 文件（包含 images/ 与 labels/）')}
              {importFormat === 'project_zip' && t('annotation.import.hintProject', '上传本项目导出的 ZIP 包（包含 images/ 与 annotations/ JSON）')}
            </p>
          </div>

          <div className="form-group">
            <div className="dataset-import-upload-area">
              <input
                ref={fileInputRef}
                type="file"
                multiple={false}
                accept={
                  importFormat === 'coco'
                    ? '.json'
                    : '.zip'
                }
                onChange={handleFileSelect}
                className="dataset-import-file-input"
                disabled={!selectedProjectId || isUploading}
              />
              <button
                className="btn-primary dataset-import-upload-btn"
                onClick={handleUploadClick}
                disabled={!selectedProjectId || isUploading}
              >
                {t('annotation.import.selectFileButton', '选择文件')}
              </button>
            </div>
          </div>

          {isUploading && uploadProgress && (
            <div className="dataset-import-progress">
              <div className="dataset-import-progress-bar">
                <div
                  className="dataset-import-progress-fill"
                  style={{ width: `${(uploadProgress.current / uploadProgress.total) * 100}%` }}
                />
              </div>
              <div className="dataset-import-progress-text">
                {t('dashboard.import.uploading', { 
                  current: uploadProgress.current, 
                  total: uploadProgress.total,
                  fileName: uploadProgress.currentFileName,
                  defaultValue: `上传中 ${uploadProgress.current}/${uploadProgress.total}: ${uploadProgress.currentFileName}`
                })}
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button
            className="btn-secondary"
            onClick={onClose}
            disabled={isUploading}
          >
            {t('common.cancel', '取消')}
          </button>
        </div>
      </div>
    </div>
  );
};