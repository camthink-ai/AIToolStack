import React, { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { API_BASE_URL } from '../config';
import { IoClose } from 'react-icons/io5';
import './DatasetImportModal.css';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, DialogClose } from '../ui/Dialog';
import { Button } from '../ui/Button';
import { FormField } from '../ui/FormField';
import { Select, SelectItem } from '../ui/Select';
import { Alert } from '../ui/Alert';
import { useAlert } from '../hooks/useAlert';

interface DatasetImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImportComplete?: () => void;
  defaultProjectId?: string; // Default selected project ID
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
  const { alertState, showSuccess, showError, showWarning, closeAlert } = useAlert();
  const [selectedProjectId, setSelectedProjectId] = useState<string>(defaultProjectId || '');
  const [importFormat, setImportFormat] = useState<'coco' | 'yolo' | 'project_zip'>('project_zip');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setSelectedProjectId(defaultProjectId || '');
    } else {
      // Reset state
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
      showWarning(t('annotation.import.selectProjectFirst', 'Please select a project first'));
      return;
    }

    // Handle dataset format import (COCO / YOLO / Project ZIP)
    if (files.length !== 1) {
      showWarning(t('annotation.import.singleFileRequired', 'Please select a dataset file'));
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
          showWarning(`${message}\n\n${t('common.errorDetails', 'Error details')}:\n${result.errors.slice(0, 5).join('\n')}`);
        } else {
          showSuccess(message);
        }
        
        if (onImportComplete) {
          onImportComplete();
        }
        onClose();
      } else {
        const errorData = await response.json().catch(() => ({ detail: t('annotation.import.importFailed', 'Import failed') }));
        showError(`${t('annotation.import.importFailed', 'Import failed')}: ${errorData.detail || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Failed to import dataset:', error);
      showError(`${t('annotation.import.importFailed', 'Import failed')}: ${t('common.connectionError', 'Unable to connect to server')}`);
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
      showWarning(t('annotation.import.selectProjectFirst', 'Please select a project first'));
      return;
    }
    fileInputRef.current?.click();
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="config-modal dataset-import-modal">
        <DialogHeader className="config-modal-header">
          <DialogTitle asChild>
            <h3>{t('annotation.import.title', '导入数据集')}</h3>
          </DialogTitle>
          <DialogClose className="close-btn" onClick={onClose}>
            <IoClose />
          </DialogClose>
        </DialogHeader>

        <DialogBody className="config-modal-content dataset-import-body">
          <FormField label={t('annotation.import.selectFormat', '选择导入格式')}>
            <Select
              value={importFormat}
              onValueChange={(v) => setImportFormat(v as 'coco' | 'yolo' | 'project_zip')}
              disabled={isUploading}
            >
              <SelectItem value="coco">{t('annotation.import.formatCOCO', 'COCO 格式 (JSON)')}</SelectItem>
              <SelectItem value="yolo">{t('annotation.import.formatYOLO', 'YOLO 格式 (ZIP/目录)')}</SelectItem>
              <SelectItem value="project_zip">{t('annotation.import.formatProject', '项目导出 ZIP')}</SelectItem>
            </Select>
            <p className="dataset-import-hint">
              {importFormat === 'coco' && t('annotation.import.hintCOCO', '上传 COCO 格式的 JSON 文件（包含 images、annotations、categories）')}
              {importFormat === 'yolo' && t('annotation.import.hintYOLO', '上传 YOLO 格式的 ZIP 文件（包含 images/ 与 labels/）')}
              {importFormat === 'project_zip' && t('annotation.import.hintProject', '上传本项目导出的 ZIP 包（包含 images/ 与 annotations/ JSON）')}
            </p>
          </FormField>

          <FormField>
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
              <Button
                className="dataset-import-upload-btn"
                onClick={handleUploadClick}
                disabled={!selectedProjectId || isUploading}
              >
                {t('annotation.import.selectFileButton', '选择文件')}
              </Button>
            </div>
          </FormField>

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
        </DialogBody>

        <DialogFooter className="config-modal-actions">
          <Button
            variant="secondary"
            onClick={onClose}
            disabled={isUploading}
          >
            {t('common.cancel', '取消')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};