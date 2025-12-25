import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { API_BASE_URL } from '../config';
import './TrainingPanel.css';
import './ProjectSelector.css';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Textarea } from '../ui/Textarea';
import { FormField } from '../ui/FormField';
import { Select, SelectItem } from '../ui/Select';
import { Alert } from '../ui/Alert';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { useAlert } from '../hooks/useAlert';
import { useConfirm } from '../hooks/useConfirm';
import { IoEye, IoDownload, IoTrash, IoChevronBack, IoChevronForward, IoPlay, IoClose, IoImage, IoFolderOpen, IoCloudUpload, IoFlash, IoAdd, IoRemove } from 'react-icons/io5';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, DialogClose } from '../ui/Dialog';

interface ModelRecord {
  model_id?: number | null;
  training_id?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  name?: string | null;  // User-defined model name
  source: string;
  model_type?: string | null;
  format?: string | null;
  status?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  model_size?: string | null;
  epochs?: number | null;
  imgsz?: number | null;
  batch?: number | null;
  device?: string | null;
  metrics?: {
    mAP50?: number;
    ['mAP50-95']?: number;
    precision?: number;
    recall?: number;
    [key: string]: any;
  } | null;
  error?: string | null;
  model_path?: string | null;
  log_count: number;
  num_classes?: number | null;
  class_names?: string[] | null;
}

interface ModelSpaceProps {
  onOpenTraining: (projectId: string, trainingId?: string) => void;
}

export const ModelSpace: React.FC<ModelSpaceProps> = ({ onOpenTraining }) => {
  const { t } = useTranslation();
  const { alertState, showSuccess, showError, showWarning, showInfo, closeAlert } = useAlert();
  const { confirmState, showConfirm, closeConfirm } = useConfirm();
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 10;
  const [showTestModal, setShowTestModal] = useState(false);
  const [testTarget, setTestTarget] = useState<ModelRecord | null>(null);
  const [testImage, setTestImage] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<any>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [testConf, setTestConf] = useState(0.25);
  const [testConfInput, setTestConfInput] = useState('0.25');
  const [testIou, setTestIou] = useState(0.45);
  const [testIouInput, setTestIouInput] = useState('0.45');
  const [showBrowseModal, setShowBrowseModal] = useState(false);
  const [browseTarget, setBrowseTarget] = useState<ModelRecord | null>(null);
  const [relatedFiles, setRelatedFiles] = useState<{ tflite: any; json: any } | null>(null);
  const [loadingRelated, setLoadingRelated] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadModelName, setUploadModelName] = useState('');
  const [uploadModelType, setUploadModelType] = useState('yolov8n');
  const [uploadInputSize, setUploadInputSize] = useState(640);
  const [uploadInputSizeInput, setUploadInputSizeInput] = useState('640');
  const [uploadNumClasses, setUploadNumClasses] = useState(80);
  const [uploadNumClassesInput, setUploadNumClassesInput] = useState('80');
  const [uploadClassNamesList, setUploadClassNamesList] = useState<string[]>([]);
  const [newClassName, setNewClassName] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [classNamesInputMode, setClassNamesInputMode] = useState<'manual' | 'json'>('manual');
  const [classNamesJsonInput, setClassNamesJsonInput] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [showQuantizeModal, setShowQuantizeModal] = useState(false);
  const [quantizeTarget, setQuantizeTarget] = useState<ModelRecord | null>(null);
  const [isQuantizing, setIsQuantizing] = useState(false);
  const [quantizeInputSize, setQuantizeInputSize] = useState(256);
  const [quantizeInputSizeInput, setQuantizeInputSizeInput] = useState('256');
  const [filterModelType, setFilterModelType] = useState<string>('all');


  const fetchModels = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/models`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setModels(data || []);
      setPage(1);
    } catch (err: any) {
      console.error('Failed to fetch models:', err);
      setError(err.message || 'Failed to fetch models');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);


  const filteredModels = useMemo(() => {
    let result = models;
    
    // Filter by model type
    if (filterModelType !== 'all') {
      result = result.filter(m => {
        const modelType = m.model_type?.toLowerCase() || '';
        return modelType.includes(filterModelType.toLowerCase());
      });
    }
    
    // Sort by time (newest first)
    // Use end_time if available, otherwise use start_time
    result = [...result].sort((a, b) => {
      const timeA = a.end_time || a.start_time || '';
      const timeB = b.end_time || b.start_time || '';
      
      if (!timeA && !timeB) return 0;
      if (!timeA) return 1;  // Put items without time at the end
      if (!timeB) return -1;
      
      // Compare timestamps (newest first = descending)
      return new Date(timeB).getTime() - new Date(timeA).getTime();
    });
    
    return result;
  }, [models, filterModelType]);

  const pagedModels = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filteredModels.slice(start, start + pageSize);
  }, [filteredModels, page, pageSize]);

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil((filteredModels?.length || 0) / pageSize)),
    [filteredModels, pageSize]
  );

  const handleDownload = async (model: ModelRecord) => {
    try {
      let response: Response;
      let filename: string;

      if (model.model_id) {
        // Download from ModelRegistry (quantized models)
        response = await fetch(`${API_BASE_URL}/models/${model.model_id}/download`);
        if (!response.ok) {
          throw new Error(t('training.exportModelFailed', '导出模型失败'));
        }
        // Get filename from Content-Disposition header or use default
        const contentDisposition = response.headers.get('Content-Disposition');
        filename = contentDisposition
          ? contentDisposition.split('filename=')[1]?.replace(/"/g, '') || 'model'
          : `${model.project_name || 'model'}_${model.model_id}.${model.format || 'pt'}`;
      } else if (model.project_id && model.training_id) {
        // Download training-produced model
        response = await fetch(
          `${API_BASE_URL}/projects/${model.project_id}/train/${model.training_id}/export`
        );
        if (!response.ok) {
          throw new Error(t('training.exportModelFailed', '导出模型失败'));
        }
        const contentDisposition = response.headers.get('Content-Disposition');
        filename = contentDisposition
          ? contentDisposition.split('filename=')[1]?.replace(/"/g, '') || 'model'
          : `${model.project_name || 'model'}_${model.training_id}.pt`;
      } else {
        return;
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err: any) {
      console.error('Failed to download model:', err);
      showError(t('training.downloadFailed', '下载失败'));
    }
  };

  const handleDelete = async (model: ModelRecord) => {
    showConfirm(
      t('training.confirmDelete', '确定要删除这条记录吗？'),
      async () => {
        try {
          let response: Response;

          if (model.model_id) {
            // Delete from ModelRegistry (quantized models)
            response = await fetch(`${API_BASE_URL}/models/${model.model_id}`, {
              method: 'DELETE',
            });
            if (!response.ok) {
              throw new Error(t('training.deleteFailed', '删除模型失败'));
            }
            setModels((prev) => prev.filter((m) => m.model_id !== model.model_id));
          } else if (model.project_id && model.training_id) {
            // Delete training record
            const params = new URLSearchParams({ training_id: model.training_id });
            response = await fetch(
              `${API_BASE_URL}/projects/${model.project_id}/train?${params.toString()}`,
              {
                method: 'DELETE',
              }
            );
            if (!response.ok) {
              throw new Error(t('training.deleteFailed', '删除训练记录失败'));
            }
            setModels((prev) =>
              prev.filter(
                (m) =>
                  !(
                    m.project_id === model.project_id &&
                    m.training_id === model.training_id
                  )
              )
            );
          } else {
            return;
          }
        } catch (err: any) {
          console.error('Failed to delete model:', err);
          showError(t('training.deleteFailed', '删除失败'));
        }
      },
      {
        title: t('common.confirm', '确认'),
        variant: 'danger',
      }
    );
  };

  const handleTest = (model: ModelRecord) => {
    if (!model.model_id && !(model.project_id && model.training_id)) {
      showWarning(t('modelSpace.testNotSupported', '该模型不支持测试'));
      return;
    }
    setTestTarget(model);
    setTestImage(null);
    setTestResults(null);
    setTestConf(0.25);
    setTestConfInput('0.25');
    setTestIou(0.45);
    setTestIouInput('0.45');
    setShowTestModal(true);
  };

  const handleTestImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      setTestImage(event.target?.result as string);
    };
    reader.readAsDataURL(file);
  };

  const handleFileSelect = (file: File) => {
    if (!file.type.startsWith('image/')) {
      showWarning(t('training.test.uploadHint', '请上传图片文件'));
      return;
    }

    const reader = new FileReader();
    reader.onload = (event) => {
      setTestImage(event.target?.result as string);
    };
    reader.readAsDataURL(file);
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isTesting) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    if (isTesting) return;

    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      handleFileSelect(files[0]);
    }
  };

  const handleUploadFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    if (!file.name.toLowerCase().endsWith('.pt')) {
      showWarning(t('modelSpace.uploadOnlyPt', '只能上传 .pt 模型文件'));
      return;
    }
    
    setUploadFile(file);
    // Auto-fill model name from filename if empty
    if (!uploadModelName) {
      setUploadModelName(file.name.replace(/\.pt$/i, ''));
    }
  };

  const handleUploadModalOpen = useCallback((open: boolean) => {
    if (open) {
      setShowUploadModal(true);
      // Reset form when opening
      setUploadInputSizeInput('640');
      setUploadNumClassesInput('80');
      setClassNamesInputMode('manual');
      setClassNamesJsonInput('');
    } else {
      setShowUploadModal(false);
      // Reset form when closing
      setUploadFile(null);
      setUploadModelName('');
      setUploadModelType('yolov8n');
      setUploadInputSize(640);
      setUploadInputSizeInput('640');
      setUploadNumClasses(80);
      setUploadNumClassesInput('80');
      setUploadClassNamesList([]);
      setNewClassName('');
      setClassNamesInputMode('manual');
      setClassNamesJsonInput('');
    }
  }, []);

  const handleUpload = async () => {
    if (!uploadFile || !uploadModelName.trim()) {
      showWarning(t('modelSpace.uploadRequired', '请选择模型文件并输入模型名称'));
      return;
    }

    setIsUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', uploadFile);
      formData.append('model_name', uploadModelName.trim());
      formData.append('model_type', uploadModelType);
      formData.append('input_size', uploadInputSize.toString());
      formData.append('num_classes', uploadNumClasses.toString());
      
      if (uploadClassNamesList.length > 0) {
        formData.append('class_names', JSON.stringify(uploadClassNamesList));
      }

      const response = await fetch(`${API_BASE_URL}/models/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || t('modelSpace.uploadFailed', '上传失败'));
      }

      const data = await response.json();
      showSuccess(t('modelSpace.uploadSuccess', '模型上传成功'));
      
      // Reset form
      setUploadFile(null);
      setUploadModelName('');
      setUploadModelType('yolov8n');
      setUploadInputSize(640);
      setUploadNumClasses(80);
      setUploadClassNamesList([]);
      setNewClassName('');
      setShowUploadModal(false);
      
      // Refresh model list
      await fetchModels();
    } catch (err: any) {
      console.error('Failed to upload model:', err);
      showError(err.message || t('modelSpace.uploadFailed', '上传失败'));
    } finally {
      setIsUploading(false);
    }
  };

  const handleQuantize = (model: ModelRecord) => {
    if (!model.model_id || model.format?.toLowerCase() !== 'pt') {
      showWarning(t('modelSpace.quantizeOnlyPt', '只能量化 .pt 模型'));
      return;
    }
    setQuantizeTarget(model);
    // Default quantization input size is always 256
    setQuantizeInputSize(256);
    setQuantizeInputSizeInput('256');
    setShowQuantizeModal(true);
  };

  const handleRunQuantize = async () => {
    if (!quantizeTarget || !quantizeTarget.model_id) {
      return;
    }

    setIsQuantizing(true);
    try {
      const params = new URLSearchParams({
        imgsz: quantizeInputSize.toString(),
        int8: 'true',
        fraction: '0.2',
      });

      const response = await fetch(
        `${API_BASE_URL}/models/${quantizeTarget.model_id}/quantize/ne301?${params.toString()}`,
        {
          method: 'POST',
        }
      );

      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || t('modelSpace.quantizeFailed', '量化失败'));
      }

      const data = await response.json();
      showSuccess(t('modelSpace.quantizeSuccess', '模型量化成功'));
      
      setShowQuantizeModal(false);
      setQuantizeTarget(null);
      setQuantizeInputSize(256);
      setQuantizeInputSizeInput('256');

      // Refresh model list
      await fetchModels();
    } catch (err: any) {
      console.error('Failed to quantize model:', err);
      showError(err.message || t('modelSpace.quantizeFailed', '量化失败'));
    } finally {
      setIsQuantizing(false);
    }
  };

  const handleRunTest = async (): Promise<void> => {
    if (!testImage || !testTarget) {
      return;
    }

    setIsTesting(true);
    try {
      // Convert base64 to File object (same as TrainingPanel)
      const base64Data = testImage.split(',')[1];
      const byteCharacters = atob(base64Data);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
      }
      const byteArray = new Uint8Array(byteNumbers);
      const blob = new Blob([byteArray], { type: 'image/png' });
      const file = new File([blob], 'test_image.png', { type: 'image/png' });

      const formData = new FormData();
      formData.append('file', file);

      let url: string | null = null;
      if (testTarget.model_id) {
        url = `${API_BASE_URL}/models/${testTarget.model_id}/test?conf=${testConf}&iou=${testIou}`;
      } else if (testTarget.project_id && testTarget.training_id) {
        url = `${API_BASE_URL}/projects/${testTarget.project_id}/train/${testTarget.training_id}/test?conf=${testConf}&iou=${testIou}`;
      }

      if (!url) {
        showWarning(t('modelSpace.testNotSupported', '该模型不支持测试'));
        setIsTesting(false);
        return;
      }

      const response = await fetch(url, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || t('modelSpace.testFailed', '测试失败'));
      }

      const data = await response.json();
      setTestResults(data);
    } catch (err: any) {
      console.error('Failed to test model:', err);
      showError(err.message || t('modelSpace.testFailed', '测试失败'));
    } finally {
      setIsTesting(false);
    }
  };

  const formatTime = (time?: string | null) => {
    if (!time) return t('common.unknown', '未知时间');
    try {
      const d = new Date(time);
      if (Number.isNaN(d.getTime())) return time;
      return d.toLocaleString();
    } catch {
      return time;
    }
  };

  const handleBrowse = async (model: ModelRecord) => {
    if (!model.model_id) return;
    
    setBrowseTarget(model);
    setShowBrowseModal(true);
    setRelatedFiles(null);
    setLoadingRelated(true);
    
    try {
      const response = await fetch(`${API_BASE_URL}/models/${model.model_id}/related`);
      if (!response.ok) {
        throw new Error('Failed to fetch related files');
      }
      const data = await response.json();
      setRelatedFiles(data);
    } catch (err: any) {
      console.error('Failed to fetch related files:', err);
      showError(t('modelSpace.browseFailed', '获取关联文件失败'));
    } finally {
      setLoadingRelated(false);
    }
  };

  const handleDownloadRelated = async (fileType: 'tflite' | 'json', fileInfo: any) => {
    if (!fileInfo || !browseTarget?.model_id) return;
    
    try {
      const url = `${API_BASE_URL}/models/${browseTarget.model_id}/related/${fileType}/download`;
      
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error('Download failed');
      }
      
      const blob = await response.blob();
      const contentDisposition = response.headers.get('Content-Disposition');
      const filename = contentDisposition
        ? contentDisposition.split('filename=')[1]?.replace(/"/g, '') || fileInfo.name || 'file'
        : fileInfo.name || 'file';
      
      const url_obj = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url_obj;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url_obj);
      document.body.removeChild(a);
    } catch (err: any) {
      console.error('Failed to download related file:', err);
      showError(t('modelSpace.downloadFailed', '下载失败'));
    }
  };

  const buildModelName = (m: ModelRecord): string => {
    // For externally imported/standalone models, use user-defined name if available
    if ((m.source === 'import' || m.source === 'standalone') && m.name) {
      return m.name;
    }
    
    // For other models (training-produced), generate name from type, format, and timestamp
    // Model Name: yolov8n_20251216-085744.pt
    const baseType = m.model_type || 'model';
    const fmt = m.format || 'pt';
    const tsSource = m.end_time || m.start_time;
    if (!tsSource) {
      return `${baseType}.${fmt}`;
    }
    try {
      const d = new Date(tsSource);
      if (Number.isNaN(d.getTime())) {
        return `${baseType}.${fmt}`;
      }
      const pad = (n: number) => n.toString().padStart(2, '0');
      const ts = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(
        d.getHours()
      )}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
      return `${baseType}_${ts}.${fmt}`;
    } catch {
      return `${baseType}.${fmt}`;
    }
  };

  const buildModelTypeLabel = (m: ModelRecord): string => {
    // Model Type column: yolov8n.pt / yolov8n.tflite / ne301.bin
    const baseType = m.model_type || 'model';
    const fmt = m.format || 'pt';
    return `${baseType}.${fmt}`;
  };

  return (
    <div className="project-selector">
      <div className="project-selector-content">
        <section className="project-list-section">
          <div className="section-header">
            <div>
              <h2>{t('modelSpace.title', '模型空间')}</h2>
            </div>
            <div className="header-actions">
              <Button
                variant="primary"
                size="sm"
                onClick={() => handleUploadModalOpen(true)}
                disabled={loading}
              >
                <IoCloudUpload style={{ marginRight: '4px' }} />
                {t('modelSpace.uploadModelQuantize', '上传模型量化')}
              </Button>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{ minWidth: 80, whiteSpace: 'nowrap', fontSize: '14px', color: 'var(--text-secondary)' }}>
              {t('modelSpace.filterByType', '按类型筛选')}:
            </div>
            <div style={{ minWidth: 120, maxWidth: 160 }}>
              <Select
                value={filterModelType}
                onValueChange={setFilterModelType}
                className="filter-select"
              >
                <SelectItem value="all">{t('modelSpace.filterAll', '全部')}</SelectItem>
                <SelectItem value="yolov8">{t('modelSpace.filterYolov8', 'YOLOv8')}</SelectItem>
                {/* <SelectItem value="yolov11">{t('modelSpace.filterYolov11', 'YOLOv11')}</SelectItem> */}
                <SelectItem value="ne301">{t('modelSpace.filterNe301', 'NE301')}</SelectItem>
              </Select>
            </div>
          </div>
          <div className="training-content">
            <div className="training-list">
              {filteredModels.length === 0 ? (
                <div className="training-empty">
                  <p className="training-empty-desc">
                    {loading
                      ? t('common.loading', '加载中...')
                      : error
                      ? t(
                          'modelSpace.loadFailed',
                          '模型列表加载失败，请使用右上角刷新重试'
                        )
                      : t('modelSpace.empty', '暂无模型记录')}
                  </p>
                </div>
              ) : (
                <table className="training-table model-table">
                  <thead>
                    <tr>
                      <th className="col-project">
                        {t('modelSpace.modelName', '模型名称')}
                      </th>
                      <th className="col-type">
                        {t('modelSpace.modelType', '模型类型')}
                      </th>
                      <th className="col-source">
                        {t('modelSpace.source', '来源')}
                      </th>
                      <th className="col-classes">
                        {t('modelSpace.classes', '类别')}
                      </th>
                      <th className="col-metrics">
                        {t('modelSpace.metrics', '关键指标')}
                      </th>
                      <th className="col-imgsz">
                        {t('modelSpace.inputSize', '输入尺寸')}
                      </th>
                      <th className="col-time">
                        {t('modelSpace.createdTime', '创建时间')}
                      </th>
                      <th className="col-actions">
                        {t('modelSpace.actions', '操作')}
                      </th>
                    </tr>
                  </thead>
                  {filteredModels.length === 0 && models.length > 0 ? (
                    <tbody>
                      <tr>
                        <td colSpan={8} style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)' }}>
                          {t('modelSpace.noFilteredResults', '没有找到符合条件的模型')}
                        </td>
                      </tr>
                    </tbody>
                  ) : (
                    <tbody>
                      {pagedModels.map((m, idx) => {
                  const rowKey =
                    m.model_id != null
                      ? `reg-${m.model_id}`
                      : m.training_id
                      ? `train-${m.training_id}`
                      : `row-${idx}`;
                  return (
                  <tr key={rowKey}>
                    <td className="col-project">
                      <div className="model-main">
                        <div className="model-title">
                          <span className="model-type">{buildModelName(m)}</span>
                        </div>
                        <div className="model-sub">
                          {m.project_name || t('modelSpace.unknownProject', '未关联项目')}
                        </div>
                      </div>
                    </td>
                    <td className="col-type">
                      {buildModelTypeLabel(m)}
                    </td>
                    <td className="col-source">
                      {m.source === 'training'
                        ? t('modelSpace.sourceTraining', '训练产生')
                        : m.source === 'quantization'
                        ? t('modelSpace.sourceQuantization', '量化产生')
                        : t('modelSpace.sourceImport', '外部导入')}
                    </td>
                    <td className="col-classes">
                      {m.class_names && m.class_names.length > 0
                        ? m.class_names.slice(0, 3).join(', ') +
                          (m.class_names.length > 3 ? ` +${m.class_names.length - 3}` : '')
                        : '-'}
                    </td>
                    <td className="col-metrics">
                      {m.metrics ? (
                        <div className="metrics-simple">
                          <div>
                            mAP50:{' '}
                            <strong>{m.metrics.mAP50 != null ? m.metrics.mAP50.toFixed(3) : '-'}</strong>
                          </div>
                          <div>
                            mAP50-95:{' '}
                            <strong>
                              {m.metrics['mAP50-95'] != null
                                ? m.metrics['mAP50-95'].toFixed(3)
                                : '-'}
                            </strong>
                          </div>
                        </div>
                      ) : (
                        '-'
                      )}
                    </td>
                    <td className="col-imgsz">
                      {m.imgsz ? `${m.imgsz}×${m.imgsz}` : '-'}
                    </td>
                    <td className="col-time">
                      {formatTime(m.end_time)}
                    </td>
                    <td className="col-actions">
                      <div className="actions-cell">
                        {/* View detail button (only for training models) */}
                        {m.source === 'training' && m.project_id && m.training_id && (
                          <Button
                            variant="secondary"
                            size="sm"
                            className="action-btn"
                            onClick={() => onOpenTraining(m.project_id!, m.training_id!)}
                            title={t('modelSpace.viewDetail', '训练详情')}
                          >
                            <IoEye />
                          </Button>
                        )}
                        {/* Test button (for all models including NE301 bin via tflite) */}
                        {(m.model_id || (m.project_id && m.training_id)) && (
                          <Button
                            variant="secondary"
                            size="sm"
                            className="action-btn"
                            onClick={() => handleTest(m)}
                            disabled={m.status === 'running'}
                            title={t('modelSpace.test', '测试模型')}
                          >
                            <IoPlay />
                          </Button>
                        )}
                        {/* Browse button (for NE301 bin only) */}
                        {m.model_id && m.format?.toLowerCase() === 'bin' && m.model_type?.toLowerCase() === 'ne301' && (
                          <Button
                            variant="secondary"
                            size="sm"
                            className="action-btn"
                            onClick={() => handleBrowse(m)}
                            title={t('modelSpace.browse', '浏览关联文件')}
                          >
                            <IoFolderOpen />
                          </Button>
                        )}
                        {/* Download button (for all models) */}
                        {(m.model_id || (m.project_id && m.training_id)) && (
                          <Button
                            variant="secondary"
                            size="sm"
                            className="action-btn"
                            onClick={() => handleDownload(m)}
                            disabled={m.status === 'running' || (m.status ? m.status !== 'completed' : false)}
                            title={t('modelSpace.download', '下载模型')}
                          >
                            <IoDownload />
                          </Button>
                        )}
                        {/* Quantize button (for uploaded .pt models only) */}
                        {m.model_id && m.format?.toLowerCase() === 'pt' && (m.source === 'import' || m.source === 'standalone') && (
                          <Button
                            variant="secondary"
                            size="sm"
                            className="action-btn"
                            onClick={() => handleQuantize(m)}
                            disabled={m.status === 'running'}
                            title={t('modelSpace.quantize', '量化为 NE301')}
                          >
                            <IoFlash />
                          </Button>
                        )}
                        {/* Delete button (for all models) */}
                        {(m.model_id || (m.project_id && m.training_id)) && (
                          <Button
                            variant="secondary"
                            size="sm"
                            className="action-btn"
                            onClick={() => handleDelete(m)}
                            title={t('common.delete', '删除')}
                          >
                            <IoTrash />
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                      );
                    })}
                    </tbody>
                  )}
                </table>
              )}
            </div>

            {filteredModels.length > 0 && (
              <div className="table-pagination">
                <div className="pagination-info">
                  <span>
                    {((page - 1) * pageSize + 1).toString()} - {Math.min(page * pageSize, filteredModels.length)} / {filteredModels.length}
                  </span>
                </div>
                <div className="pagination-actions">
                  <Button
                    variant="secondary"
                    size="sm"
                    className="icon-button"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    aria-label={t('common.previous', '上一页')}
                  >
                    <IoChevronBack />
                  </Button>
                  <span className="pagination-page">
                    {page} / {totalPages}
                  </span>
                  <Button
                    variant="secondary"
                    size="sm"
                    className="icon-button"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
                    aria-label={t('common.next', '下一页')}
                  >
                    <IoChevronForward />
                  </Button>
                </div>
              </div>
            )}
          </div>
        </section>
      </div>

      {/* 模型测试弹窗（参照训练列表样式） */}
      <Dialog open={showTestModal} onOpenChange={(open) => !isTesting && setShowTestModal(open)}>
        <DialogContent className="config-modal test-modal">
          <DialogHeader className="config-modal-header">
            <DialogTitle asChild>
              <h3>{t('training.test.title')}</h3>
            </DialogTitle>
            <DialogClose
              className="close-btn"
              onClick={() => setShowTestModal(false)}
              disabled={isTesting}
            >
              <IoClose />
            </DialogClose>
          </DialogHeader>

          <DialogBody className="config-modal-content">
            <div className="test-modal-body">
              <div className="test-left">
                <div className="test-upload-section">
                  <input
                    type="file"
                    accept="image/*"
                    onChange={handleTestImageUpload}
                    disabled={isTesting}
                    style={{ display: 'none' }}
                    id={`test-image-input-${testTarget?.model_id || testTarget?.training_id || 'default'}`}
                  />
                  <div
                    className={`test-upload-area ${isDragging ? 'dragging' : ''}`}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                    onClick={() => {
                      if (!isTesting && !testImage) {
                        const input = document.getElementById(`test-image-input-${testTarget?.model_id || testTarget?.training_id || 'default'}`) as HTMLInputElement;
                        input?.click();
                      }
                    }}
                    style={{ cursor: isTesting ? 'not-allowed' : testImage ? 'default' : 'pointer' }}
                  >
                    {testImage ? (
                      <img
                        src={testImage}
                        alt={t('training.test.preview')}
                        className="test-preview-image"
                      />
                    ) : (
                      <div className="test-upload-placeholder">
                        <IoImage size={48} />
                        <p>{t('training.test.uploadPlaceholder')}</p>
                      </div>
                    )}
                  </div>
                </div>

                <div className="config-item">
                  <label>{t('training.test.confThreshold')}</label>
                  <Input
                    type="number"
                    step={0.01}
                    min={0}
                    max={1}
                    value={testConfInput}
                    onChange={(e) => {
                      const value = e.target.value;
                      setTestConfInput(value);
                      if (value !== '') {
                        const v = parseFloat(value);
                        if (!Number.isNaN(v)) {
                          setTestConf(Math.min(1, Math.max(0, v)));
                        }
                      }
                    }}
                    onBlur={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setTestConfInput('0.25');
                        setTestConf(0.25);
                      } else {
                        const numValue = parseFloat(value);
                        if (Number.isNaN(numValue) || numValue < 0) {
                          setTestConfInput('0');
                          setTestConf(0);
                        } else if (numValue > 1) {
                          setTestConfInput('1');
                          setTestConf(1);
                        } else {
                          setTestConfInput(numValue.toString());
                          setTestConf(numValue);
                        }
                      }
                    }}
                    disabled={isTesting}
                  />
                </div>

                <div className="config-item">
                  <label>{t('training.test.iouThreshold', 'IoU 阈值 (iou)')}</label>
                  <Input
                    type="number"
                    step={0.01}
                    min={0}
                    max={1}
                    value={testIouInput}
                    onChange={(e) => {
                      const value = e.target.value;
                      setTestIouInput(value);
                      if (value !== '') {
                        const v = parseFloat(value);
                        if (!Number.isNaN(v)) {
                          setTestIou(Math.min(1, Math.max(0, v)));
                        }
                      }
                    }}
                    onBlur={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setTestIouInput('0.45');
                        setTestIou(0.45);
                      } else {
                        const numValue = parseFloat(value);
                        if (Number.isNaN(numValue) || numValue < 0) {
                          setTestIouInput('0');
                          setTestIou(0);
                        } else if (numValue > 1) {
                          setTestIouInput('1');
                          setTestIou(1);
                        } else {
                          setTestIouInput(numValue.toString());
                          setTestIou(numValue);
                        }
                      }
                    }}
                    disabled={isTesting}
                  />
                </div>
              </div>

              <div className="test-right">
                {testResults ? (
                  <>
                    {testResults.annotated_image && (
                      <div className="test-result-image">
                        <img
                          src={testResults.annotated_image}
                          alt={t('training.test.detectionResult')}
                        />
                      </div>
                    )}
                    <div className="test-detections-list">
                      <div className="config-item">
                        <label>{t('training.test.details')}</label>
                        {testResults.detections && testResults.detections.length > 0 ? (
                          <div className="detection-list-container">
                            {testResults.detections.map((det: any, index: number) => (
                              <div key={index} className="detection-item">
                                <span className="detection-class">{det.class_name}</span>
                                <span className="detection-confidence">
                                  {t('training.test.confidence')}:{' '}
                                  {(det.confidence * 100).toFixed(2)}%
                                </span>
                                <span className="detection-bbox">
                                  [
                                  {det.bbox.x1.toFixed(0)}, {det.bbox.y1.toFixed(0)},{' '}
                                  {det.bbox.x2.toFixed(0)}, {det.bbox.y2.toFixed(0)}]
                                </span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="detection-list-container">
                            <div className="detection-item no-detections">
                              <span>
                                {t('training.test.noDetections', '未检测到任何目标')}
                              </span>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="test-results-section placeholder">
                    <h4>{t('training.test.results')}</h4>
                    <div className="test-results-info">
                      <p>{t('training.test.uploadHint')}</p>
                    </div>
                  </div>
                )}
              </div>
            </div>

            <DialogFooter className="config-modal-actions">
              <Button
                className="btn-start-training"
                onClick={handleRunTest}
                disabled={!testImage || isTesting}
              >
                {isTesting
                  ? t('training.test.testing', '检测中...')
                  : t('training.test.start', '开始检测')}
              </Button>
            </DialogFooter>
          </DialogBody>
        </DialogContent>
      </Dialog>

      {/* 浏览关联文件弹窗（NE301 bin） */}
      <Dialog open={showBrowseModal} onOpenChange={setShowBrowseModal}>
        <DialogContent className="config-modal browse-modal">
          <DialogHeader className="config-modal-header">
            <DialogTitle asChild>
              <h3>{t('modelSpace.browseTitle', '浏览关联文件')}</h3>
            </DialogTitle>
            <DialogClose
              className="close-btn"
              onClick={() => setShowBrowseModal(false)}
            >
              <IoClose />
            </DialogClose>
          </DialogHeader>

          <DialogBody className="config-modal-content">
            {loadingRelated ? (
              <div style={{ padding: '40px', textAlign: 'center' }}>
                {t('common.loading', '加载中...')}
              </div>
            ) : relatedFiles ? (
              <div className="browse-files-list">
                {relatedFiles.tflite && (
                  <div className="browse-file-item">
                    <div className="browse-file-info">
                      <strong>{t('modelSpace.tfliteFile', 'TFLite 文件')}</strong>
                      <span className="browse-file-name">
                        {relatedFiles.tflite.name || relatedFiles.tflite.path.split('/').pop() || relatedFiles.tflite.path.split('\\').pop()}
                      </span>
                    </div>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => handleDownloadRelated('tflite', relatedFiles.tflite)}
                      title={t('modelSpace.download', '下载模型')}
                    >
                      <IoDownload /> {t('common.download', '下载')}
                    </Button>
                  </div>
                )}
                {relatedFiles.json && (
                  <div className="browse-file-item">
                    <div className="browse-file-info">
                      <strong>{t('modelSpace.jsonFile', 'JSON 配置文件')}</strong>
                      <span className="browse-file-name">
                        {relatedFiles.json.name || relatedFiles.json.path.split('/').pop() || relatedFiles.json.path.split('\\').pop()}
                      </span>
                    </div>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => handleDownloadRelated('json', relatedFiles.json)}
                      title={t('modelSpace.download', '下载模型')}
                    >
                      <IoDownload /> {t('common.download', '下载')}
                    </Button>
                  </div>
                )}
                {!relatedFiles.tflite && !relatedFiles.json && (
                  <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-light)' }}>
                    {t('modelSpace.noRelatedFiles', '未找到关联文件')}
                  </div>
                )}
              </div>
            ) : (
              <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-light)' }}>
                {t('modelSpace.loadFailed', '加载失败')}
              </div>
            )}
          </DialogBody>

          <DialogFooter className="config-modal-actions">
            <Button
              variant="secondary"
              onClick={() => setShowBrowseModal(false)}
            >
              {t('common.close', '关闭')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 上传模型弹窗 */}
      <Dialog open={showUploadModal} onOpenChange={(open) => !isUploading && handleUploadModalOpen(open)}>
        <DialogContent className="config-modal upload-modal">
          <DialogHeader className="config-modal-header">
            <DialogTitle asChild>
              <h3>{t('modelSpace.uploadModel', '上传模型')}</h3>
            </DialogTitle>
            <DialogClose
              className="close-btn"
              onClick={() => handleUploadModalOpen(false)}
              disabled={isUploading}
            >
              <IoClose />
            </DialogClose>
          </DialogHeader>

          <DialogBody className="config-modal-content">
            <FormField
              label={t('modelSpace.uploadFile', '模型文件')}
              required
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".pt"
                onChange={handleUploadFileSelect}
                disabled={isUploading}
                style={{ display: 'none' }}
              />
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isUploading}
                  style={{ width: '100%' }}
                >
                  {uploadFile 
                    ? t('modelSpace.changeFile', '更换文件')
                    : t('modelSpace.selectFile', '选择文件')}
                </Button>
                {uploadFile && (
                  <p style={{ marginTop: '0', color: 'var(--text-secondary)', fontSize: '14px' }}>
                    {t('modelSpace.selectedFile', '已选择')}: {uploadFile.name}
                  </p>
                )}
              </div>
            </FormField>

            <FormField
              label={t('modelSpace.modelName', '模型名称')}
              required
            >
              <Input
                type="text"
                value={uploadModelName}
                onChange={(e) => setUploadModelName(e.target.value)}
                disabled={isUploading}
                placeholder={t('modelSpace.modelNamePlaceholder', '请输入模型名称')}
              />
            </FormField>

            <FormField
              label={t('modelSpace.modelType', '模型类型')}
            >
              <Select
                value={uploadModelType}
                onValueChange={setUploadModelType}
                disabled={isUploading}
              >
                <SelectItem value="yolov8n">YOLOv8n</SelectItem>
              </Select>
            </FormField>

            <FormField
              label={t('modelSpace.inputSize', '输入尺寸')}
            >
              <Input
                type="number"
                value={uploadInputSizeInput}
                onChange={(e) => {
                  const value = e.target.value;
                  setUploadInputSizeInput(value);
                  if (value !== '') {
                    const v = parseInt(value);
                    if (!Number.isNaN(v) && v >= 32 && v <= 2048) {
                      setUploadInputSize(v);
                    }
                  }
                }}
                onBlur={(e) => {
                  const value = e.target.value;
                  if (value === '') {
                    setUploadInputSizeInput('640');
                    setUploadInputSize(640);
                  } else {
                    const numValue = parseInt(value);
                    if (Number.isNaN(numValue) || numValue < 32) {
                      setUploadInputSizeInput('32');
                      setUploadInputSize(32);
                    } else if (numValue > 2048) {
                      setUploadInputSizeInput('2048');
                      setUploadInputSize(2048);
                    } else {
                      setUploadInputSizeInput(numValue.toString());
                      setUploadInputSize(numValue);
                    }
                  }
                }}
                disabled={isUploading}
                min={32}
                max={2048}
              />
            </FormField>

            <FormField
              label={t('modelSpace.numClasses', '类别数量')}
            >
              <Input
                type="number"
                value={uploadNumClassesInput}
                onChange={(e) => {
                  const value = e.target.value;
                  setUploadNumClassesInput(value);
                  if (value !== '') {
                    const v = parseInt(value);
                    if (!Number.isNaN(v) && v >= 1) {
                      setUploadNumClasses(v);
                    }
                  }
                }}
                onBlur={(e) => {
                  const value = e.target.value;
                  if (value === '') {
                    setUploadNumClassesInput('80');
                    setUploadNumClasses(80);
                  } else {
                    const numValue = parseInt(value);
                    if (Number.isNaN(numValue) || numValue < 1) {
                      setUploadNumClassesInput('1');
                      setUploadNumClasses(1);
                    } else {
                      setUploadNumClassesInput(numValue.toString());
                      setUploadNumClasses(numValue);
                    }
                  }
                }}
                disabled={isUploading}
                min={1}
              />
            </FormField>

            <FormField
              label={t('modelSpace.classNames', '类别名称')}
              helpText={t('modelSpace.classNamesHelp', '可手动添加类别名称，用于模型测试和部署。顺序按照类索引顺序添加。')}
            >
              <div style={{ marginBottom: '12px' }}>
                <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
                  <Button
                    type="button"
                    variant={classNamesInputMode === 'manual' ? 'primary' : 'secondary'}
                    size="sm"
                    onClick={() => {
                      setClassNamesInputMode('manual');
                      // Sync JSON input when switching to manual mode
                      if (uploadClassNamesList.length > 0) {
                        setClassNamesJsonInput(JSON.stringify(uploadClassNamesList, null, 2));
                      }
                    }}
                    disabled={isUploading}
                  >
                    {t('modelSpace.manualInput', '手动添加')}
                  </Button>
                  <Button
                    type="button"
                    variant={classNamesInputMode === 'json' ? 'primary' : 'secondary'}
                    size="sm"
                    onClick={() => {
                      setClassNamesInputMode('json');
                      // Sync JSON input when switching to JSON mode
                      if (uploadClassNamesList.length > 0) {
                        setClassNamesJsonInput(JSON.stringify(uploadClassNamesList, null, 2));
                      } else {
                        setClassNamesJsonInput('');
                      }
                    }}
                    disabled={isUploading}
                  >
                    {t('modelSpace.jsonInput', 'JSON 数组')}
                  </Button>
                </div>

                {classNamesInputMode === 'manual' ? (
                  <>
                    <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
                      <Input
                        type="text"
                        value={newClassName}
                        onChange={(e) => setNewClassName(e.target.value)}
                        disabled={isUploading}
                        placeholder={t('modelSpace.addClassName', '输入类别名称')}
                        onKeyPress={(e) => {
                          if (e.key === 'Enter' && newClassName.trim()) {
                            e.preventDefault();
                            if (!uploadClassNamesList.includes(newClassName.trim())) {
                              const newList = [...uploadClassNamesList, newClassName.trim()];
                              setUploadClassNamesList(newList);
                              setClassNamesJsonInput(JSON.stringify(newList, null, 2));
                              setNewClassName('');
                            }
                          }
                        }}
                      />
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => {
                          if (newClassName.trim() && !uploadClassNamesList.includes(newClassName.trim())) {
                            const newList = [...uploadClassNamesList, newClassName.trim()];
                            setUploadClassNamesList(newList);
                            setClassNamesJsonInput(JSON.stringify(newList, null, 2));
                            setNewClassName('');
                          }
                        }}
                        disabled={isUploading || !newClassName.trim()}
                      >
                        <IoAdd />
                      </Button>
                    </div>
                    {uploadClassNamesList.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginTop: '8px' }}>
                        {uploadClassNamesList.map((name, index) => (
                          <div
                            key={index}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: '6px',
                              padding: '6px 12px',
                              background: 'var(--bg-secondary)',
                              borderRadius: '6px',
                              fontSize: '14px',
                              border: '1px solid var(--border-color)',
                            }}
                          >
                            <span style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>{index}:</span>
                            <span>{name}</span>
                            <button
                              type="button"
                              onClick={() => {
                                const newList = uploadClassNamesList.filter((_, i) => i !== index);
                                setUploadClassNamesList(newList);
                                setClassNamesJsonInput(JSON.stringify(newList, null, 2));
                              }}
                              disabled={isUploading}
                              style={{
                                background: 'none',
                                border: 'none',
                                color: 'var(--text-secondary)',
                                cursor: 'pointer',
                                padding: '0',
                                display: 'flex',
                                alignItems: 'center',
                                marginLeft: '4px',
                              }}
                              title={t('common.delete', '删除')}
                            >
                              <IoRemove size={16} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <div>
                    <Textarea
                      value={classNamesJsonInput}
                      onChange={(e) => {
                        const value = e.target.value;
                        setClassNamesJsonInput(value);
                        // Try to parse JSON and update class names list
                        try {
                          if (value.trim()) {
                            const parsed = JSON.parse(value);
                            if (Array.isArray(parsed)) {
                              setUploadClassNamesList(parsed.map(item => String(item)));
                            }
                          } else {
                            setUploadClassNamesList([]);
                          }
                        } catch {
                          // Invalid JSON, keep current list
                        }
                      }}
                      disabled={isUploading}
                      placeholder={t('modelSpace.jsonInputPlaceholder', '例如: ["class1", "class2", "class3"]')}
                      rows={4}
                      style={{ fontFamily: 'monospace', fontSize: '14px' }}
                    />
                    <p style={{ marginTop: '8px', color: 'var(--text-secondary)', fontSize: '12px' }}>
                      {t('modelSpace.jsonInputHint', '输入 JSON 数组格式的类别名称，例如：["person", "car", "bicycle"]')}
                    </p>
                  </div>
                )}
              </div>
            </FormField>
          </DialogBody>

          <DialogFooter className="config-modal-actions">
            <Button
              variant="secondary"
              onClick={() => {
                handleUploadModalOpen(false);
              }}
              disabled={isUploading}
            >
              {t('common.cancel', '取消')}
            </Button>
            <Button
              variant="primary"
              onClick={handleUpload}
              disabled={isUploading || !uploadFile || !uploadModelName.trim()}
            >
              {isUploading
                ? t('common.uploading', '上传中...')
                : t('common.upload', '上传')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 量化模型弹窗 */}
      <Dialog open={showQuantizeModal} onOpenChange={(open) => !isQuantizing && setShowQuantizeModal(open)}>
        <DialogContent className="config-modal quantize-modal">
          <DialogHeader className="config-modal-header">
            <DialogTitle asChild>
              <h3>{t('modelSpace.quantizeTitle', '量化为 NE301')}</h3>
            </DialogTitle>
            <DialogClose
              className="close-btn"
              onClick={() => {
                setShowQuantizeModal(false);
                setQuantizeTarget(null);
                setQuantizeInputSize(256);
                setQuantizeInputSizeInput('256');
              }}
              disabled={isQuantizing}
            >
              <IoClose />
            </DialogClose>
          </DialogHeader>

          <DialogBody className="config-modal-content">
            {quantizeTarget && (
              <div>
                <FormField
                  label={t('modelSpace.modelName', '模型名称')}
                >
                  <div style={{ 
                    padding: '12px', 
                    background: 'var(--bg-secondary)', 
                    borderRadius: '6px',
                    border: '1px solid var(--border-color)',
                    fontSize: '14px',
                    color: 'var(--text-primary)',
                    fontWeight: '500'
                  }}>
                    {buildModelName(quantizeTarget)}
                  </div>
                </FormField>

                <FormField
                  label={t('modelSpace.inputSize', '输入尺寸')}
                  helpText={t('modelSpace.quantizeInputSizeHelp', '设置量化时的输入图像尺寸（256-640）')}
                >
                  <Input
                    type="number"
                    value={quantizeInputSizeInput}
                    onChange={(e) => {
                      const value = e.target.value;
                      setQuantizeInputSizeInput(value);
                      if (value !== '') {
                        const v = parseInt(value);
                        if (!Number.isNaN(v) && v >= 256 && v <= 640) {
                          setQuantizeInputSize(v);
                        }
                      }
                    }}
                    onBlur={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setQuantizeInputSizeInput('256');
                        setQuantizeInputSize(256);
                      } else {
                        const numValue = parseInt(value);
                        if (Number.isNaN(numValue) || numValue < 256) {
                          setQuantizeInputSizeInput('256');
                          setQuantizeInputSize(256);
                        } else if (numValue > 640) {
                          setQuantizeInputSizeInput('640');
                          setQuantizeInputSize(640);
                        } else {
                          setQuantizeInputSizeInput(numValue.toString());
                          setQuantizeInputSize(numValue);
                        }
                      }
                    }}
                    disabled={isQuantizing}
                    min={256}
                    max={640}
                  />
                </FormField>

                <div style={{ 
                  marginTop: '16px', 
                  padding: '12px', 
                  background: 'var(--bg-secondary)', 
                  borderRadius: '6px',
                  border: '1px solid var(--border-color)'
                }}>
                  <div style={{ 
                    display: 'flex', 
                    alignItems: 'center', 
                    gap: '8px', 
                    marginBottom: '8px',
                    fontSize: '14px',
                    fontWeight: '500',
                    color: 'var(--text-primary)'
                  }}>
                    <IoFlash style={{ color: 'var(--primary-color)' }} />
                    <span>{t('modelSpace.quantizationType', '量化类型')}: int8</span>
                  </div>
                  <p style={{ 
                    margin: '0', 
                    color: 'var(--text-secondary)', 
                    fontSize: '13px',
                    lineHeight: '1.5'
                  }}>
                    {t('modelSpace.quantizeNote', '注意：量化过程可能需要几分钟时间，请耐心等待。')}
                  </p>
                </div>
              </div>
            )}
          </DialogBody>

          <DialogFooter className="config-modal-actions">
            <Button
              variant="secondary"
              onClick={() => {
                setShowQuantizeModal(false);
                setQuantizeTarget(null);
                setQuantizeInputSize(256);
                setQuantizeInputSizeInput('256');
              }}
              disabled={isQuantizing}
            >
              {t('common.cancel', '取消')}
            </Button>
            <Button
              variant="primary"
              onClick={handleRunQuantize}
              disabled={isQuantizing || !quantizeTarget}
            >
              {isQuantizing
                ? t('modelSpace.quantizing', '量化中...')
                : t('modelSpace.startQuantize', '开始量化')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
}


