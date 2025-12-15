import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { API_BASE_URL } from '../config';
import './TrainingPanel.css';
import { IoClose, IoDownload, IoTrash, IoAdd, IoImage } from 'react-icons/io5';

interface TrainingPanelProps {
  projectId: string;
  onClose: () => void;
}

interface TrainingRecord {
  training_id: string;
  status: 'not_started' | 'running' | 'completed' | 'failed' | 'stopped';
  start_time?: string;
  end_time?: string;
  model_type?: string;
  model_size?: string;
  epochs?: number;
  imgsz?: number;
  batch?: number;
  device?: string;
  current_epoch?: number;
  metrics?: {
    best_fitness?: number;
    mAP50?: number;
    'mAP50-95'?: number;
    precision?: number;
    recall?: number;
    box_loss?: number;
    cls_loss?: number;
    dfl_loss?: number;
    val_box_loss?: number;
    val_cls_loss?: number;
    val_dfl_loss?: number;
  };
  error?: string;
  model_path?: string;
  log_count?: number;
}

interface TrainingRequest {
  model_type: string;
  model_size: string;
  epochs: number;
  imgsz: number;
  batch: number;
  device?: string;
  // Learning rate related
  lr0?: number;
  lrf?: number;
  // Optimizer related
  optimizer?: string;
  momentum?: number;
  weight_decay?: number;
  // Training control
  patience?: number;
  workers?: number;
  val?: boolean;
  save_period?: number;
  amp?: boolean;
  // Data augmentation (advanced options)
  hsv_h?: number;
  hsv_s?: number;
  hsv_v?: number;
  degrees?: number;
  translate?: number;
  scale?: number;
  shear?: number;
  perspective?: number;
  flipud?: number;
  fliplr?: number;
  mosaic?: number;
  mixup?: number;
}

export const TrainingPanel: React.FC<TrainingPanelProps> = ({ projectId, onClose }) => {
  const { t, i18n } = useTranslation();
  const [trainingRecords, setTrainingRecords] = useState<TrainingRecord[]>([]);
  const [selectedTrainingId, setSelectedTrainingId] = useState<string | null>(null);
  const [trainingLogs, setTrainingLogs] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [showTestModal, setShowTestModal] = useState(false);
  const [showQuantModal, setShowQuantModal] = useState(false);
  const [testImage, setTestImage] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<any>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [testConf, setTestConf] = useState(0.25);
  const [testIou, setTestIou] = useState(0.45);
  const [quantImgSz, setQuantImgSz] = useState(256);
  const [quantInt8, setQuantInt8] = useState(true);
  const [quantFraction, setQuantFraction] = useState(0.2);
  const [quantNe301, setQuantNe301] = useState(true);
  const [quantResult, setQuantResult] = useState<any>(null);
  const [isQuanting, setIsQuanting] = useState(false);
  const [quantProgress, setQuantProgress] = useState<string>('');
  const [quantStartTime, setQuantStartTime] = useState<number | null>(null);
  const [quantElapsedTime, setQuantElapsedTime] = useState<number>(0);
  const quantTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const quantTimerRef = useRef<NodeJS.Timeout | null>(null);
  const [trainingConfig, setTrainingConfig] = useState<TrainingRequest>({
    model_type: 'yolov8',
    model_size: 'n',
    epochs: 100,
    imgsz: 640,
    batch: 16,
    device: undefined,
    lr0: undefined,
    lrf: undefined,
    optimizer: undefined,
    momentum: undefined,
    weight_decay: undefined,
    patience: undefined,
    workers: undefined,
    val: true,
    save_period: undefined,
    amp: true,
    hsv_h: undefined,
    hsv_s: undefined,
    hsv_v: undefined,
    degrees: undefined,
    translate: undefined,
    scale: undefined,
    shear: undefined,
    perspective: undefined,
    flipud: undefined,
    fliplr: undefined,
    mosaic: undefined,
    mixup: undefined,
  });
  const [epochsInput, setEpochsInput] = useState('');
  const [imgszInput, setImgszInput] = useState('');
  const [batchInput, setBatchInput] = useState('');

  const logsEndRef = useRef<HTMLDivElement>(null);
  const recordsIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const logsIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const currentStatus = trainingRecords.find(r => r.training_id === selectedTrainingId) || 
                       (trainingRecords.length > 0 ? trainingRecords[0] : null);

  // Function to fetch training record list
  const fetchRecords = useCallback(async () => {
    if (!projectId) return;

    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train/records`);
      const data = await response.json();
      setTrainingRecords(data);
      
      // If no training is selected, select the latest (first one)
      setSelectedTrainingId(prev => {
        if (!prev && data.length > 0) {
          return data[0].training_id;
        }
        return prev;
      });
      
      // Check if there is a running training
      const hasRunningTraining = data.some((r: TrainingRecord) => r.status === 'running');
      
      // If there is a running training and polling is not set yet, set polling
      if (hasRunningTraining && !recordsIntervalRef.current) {
        recordsIntervalRef.current = setInterval(() => {
          fetchRecords();
        }, 5000);
      } else if (!hasRunningTraining && recordsIntervalRef.current) {
        // If there is no running training, clear polling
        clearInterval(recordsIntervalRef.current);
        recordsIntervalRef.current = null;
      }
    } catch (error) {
      console.error('Failed to fetch training records:', error);
    }
  }, [projectId]);

  // Fetch training record list
  useEffect(() => {
    if (!projectId) return;

    // Clear previous polling
    if (recordsIntervalRef.current) {
      clearInterval(recordsIntervalRef.current);
      recordsIntervalRef.current = null;
    }

    // First fetch
    fetchRecords();

    return () => {
      if (recordsIntervalRef.current) {
        clearInterval(recordsIntervalRef.current);
        recordsIntervalRef.current = null;
      }
    };
  }, [projectId, fetchRecords]);

  // Fetch logs for selected training
  useEffect(() => {
    if (!selectedTrainingId) {
      setTrainingLogs([]);
      // Clear previous polling
      if (logsIntervalRef.current) {
        clearInterval(logsIntervalRef.current);
        logsIntervalRef.current = null;
      }
      // Reset test and quantization related state
      setTestImage(null);
      setTestResults(null);
      setQuantResult(null);
      return;
    }
    
    // When switching training records, reset test and quantization related state
    setTestImage(null);
    setTestResults(null);
    setQuantResult(null);

    const fetchLogs = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train/${selectedTrainingId}/logs`);
        const data = await response.json();
        setTrainingLogs(data.logs || []);
        
        // Also fetch training status to determine if polling should continue
        const statusResponse = await fetch(`${API_BASE_URL}/projects/${projectId}/train/status?training_id=${selectedTrainingId}`);
        const statusData = await statusResponse.json();
        const isRunning = statusData?.status === 'running';
        
        // If training is running and polling is not set yet, set polling
        if (isRunning && !logsIntervalRef.current) {
          logsIntervalRef.current = setInterval(fetchLogs, 2000);
        } else if (!isRunning && logsIntervalRef.current) {
          // If training is completed/failed, clear polling
          clearInterval(logsIntervalRef.current);
          logsIntervalRef.current = null;
        }
      } catch (error) {
        console.error('Failed to fetch training logs:', error);
      }
    };

    // Clear previous polling
    if (logsIntervalRef.current) {
      clearInterval(logsIntervalRef.current);
      logsIntervalRef.current = null;
    }

    // First fetch
    fetchLogs();

    return () => {
      if (logsIntervalRef.current) {
        clearInterval(logsIntervalRef.current);
        logsIntervalRef.current = null;
      }
    };
  }, [projectId, selectedTrainingId]);

  // Cleanup quantization timers
  useEffect(() => {
    return () => {
      if (quantTimeoutRef.current) {
        clearTimeout(quantTimeoutRef.current);
        quantTimeoutRef.current = null;
      }
      if (quantTimerRef.current) {
        clearInterval(quantTimerRef.current);
        quantTimerRef.current = null;
      }
    };
  }, []);

  // Real-time update quantization elapsed time
  useEffect(() => {
    if (isQuanting && quantStartTime) {
      // Update immediately once
      setQuantElapsedTime(Math.floor((Date.now() - quantStartTime) / 1000));
      
      // Update every second
      quantTimerRef.current = setInterval(() => {
        setQuantElapsedTime(Math.floor((Date.now() - quantStartTime) / 1000));
      }, 1000);
      
      return () => {
        if (quantTimerRef.current) {
          clearInterval(quantTimerRef.current);
          quantTimerRef.current = null;
        }
      };
    } else {
      // When quantization stops, clear timer
      if (quantTimerRef.current) {
        clearInterval(quantTimerRef.current);
        quantTimerRef.current = null;
      }
      // Keep final time, don't reset
    }
  }, [isQuanting, quantStartTime]);

  // Auto scroll to bottom
  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [trainingLogs]);

  const handleStartTraining = async () => {
    setIsLoading(true);
    try {
      // Add timeout to prevent hanging (increased to 5 minutes for dataset export)
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 300000); // 5 minute timeout for dataset export
      
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(trainingConfig),
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        let errorMessage = 'Failed to start training';
        try {
          const error = await response.json();
          errorMessage = error.detail || errorMessage;
        } catch (e) {
          errorMessage = `HTTP ${response.status}: ${response.statusText}`;
        }
        throw new Error(errorMessage);
      }

      const data = await response.json();
      // After training starts, immediately refresh training record list
      setShowConfigModal(false);
      // Reset configuration to initial state
      setEpochsInput('');
      setImgszInput('');
      setBatchInput('');
      setTrainingConfig({
        model_type: 'yolov8',
        model_size: 'n',
        epochs: 100,
        imgsz: 640,
        batch: 16,
        device: undefined,
        // Learning rate related
        lr0: undefined,
        lrf: undefined,
        // Optimizer related
        optimizer: undefined,
        momentum: undefined,
        weight_decay: undefined,
        // Training control
        patience: undefined,
        workers: undefined,
        val: undefined,
        save_period: undefined,
        amp: undefined,
        // Data augmentation (advanced options)
        hsv_h: undefined,
        hsv_s: undefined,
        hsv_v: undefined,
        degrees: undefined,
        translate: undefined,
        scale: undefined,
        shear: undefined,
        perspective: undefined,
        flipud: undefined,
        fliplr: undefined,
        mosaic: undefined,
        mixup: undefined,
      });
      
      // Immediately refresh training record list to display newly created record
      await fetchRecords();
      
      // If training ID is returned, select it
      if (data.training_id) {
        setSelectedTrainingId(data.training_id);
      }
    } catch (error: any) {
      console.error('Training start error:', error);
      if (error.name === 'AbortError') {
        alert(`${t('training.startTrainingFailed')}: Request timeout. Please check if the server is responding.`);
      } else {
        alert(`${t('training.startTrainingFailed')}: ${error.message || 'Unknown error'}`);
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleStopTraining = async () => {
    if (!window.confirm(t('training.confirmStop'))) {
      return;
    }

    setIsLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train/stop${selectedTrainingId ? `?training_id=${selectedTrainingId}` : ''}`, {
        method: 'POST',
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || t('training.stopTrainingFailed'));
      }
      // After stopping, refresh records and logs
      await fetchRecords();
    } catch (error: any) {
      alert(`${t('training.stopTrainingFailed')}: ${error.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteRecord = async (trainingId: string) => {
    if (!window.confirm(t('training.confirmDelete'))) {
      return;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train?training_id=${trainingId}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || t('training.deleteFailed'));
      }

      // If deleted record is currently selected, switch to another record
      if (selectedTrainingId === trainingId) {
        const remaining = trainingRecords.filter(r => r.training_id !== trainingId);
        setSelectedTrainingId(remaining.length > 0 ? remaining[0].training_id : null);
      }
      
      // Refresh training record list
      await fetchRecords();
    } catch (error: any) {
      alert(`${t('training.deleteFailed')}: ${error.message}`);
    }
  };

  const handleExportModel = async () => {
    if (!currentStatus?.model_path) {
      alert(t('training.modelFileNotExists'));
      return;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train/${selectedTrainingId}/export`);
      if (!response.ok) {
        throw new Error(t('training.exportModelFailed'));
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `yolov8${currentStatus.model_size}_${selectedTrainingId}.pt`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error: any) {
      alert(`${t('training.exportModelFailed')}: ${error.message}`);
    }
  };

  const handleTestModel = () => {
    if (!currentStatus?.model_path) {
      alert(t('training.modelFileNotExists'));
      return;
    }
    setShowTestModal(true);
    setTestImage(null);
    setTestResults(null);
    setTestConf(0.25);
    setTestIou(0.45);
  };

  const handleQuantModel = () => {
    if (!currentStatus?.model_path) {
      alert(t('training.modelFileNotExists'));
      return;
    }
    setShowQuantModal(true);
    setQuantImgSz(256);
    setQuantInt8(true);
    setQuantFraction(0.2);
    setQuantNe301(true);
    setQuantResult(null);
  };

  const handleDownloadExportFile = async (fileType: string) => {
    if (!selectedTrainingId) return;

    try {
      const response = await fetch(
        `${API_BASE_URL}/projects/${projectId}/train/${selectedTrainingId}/export/tflite/download?file_type=${fileType}`
      );
      
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: t('training.downloadFailed') }));
        throw new Error(error.detail || t('training.downloadFailed'));
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      
      // Get filename from response header, or use default name
      const contentDisposition = response.headers.get('content-disposition');
      let filename = `${fileType}_${selectedTrainingId}`;
      if (contentDisposition) {
        const filenameMatch = contentDisposition.match(/filename="?(.+?)"?$/);
        if (filenameMatch) {
          filename = filenameMatch[1];
        }
      } else {
        // Set default extension based on file type
        const extensions: { [key: string]: string } = {
          'tflite': '.tflite',
          'ne301_tflite': '.tflite',
          'ne301_json': '.json',
          'ne301_model_bin': '.bin'
        };
        filename += extensions[fileType] || '';
      }
      
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error: any) {
      alert(`${t('training.downloadFailed')}: ${error.message}`);
    }
  };

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Preview image
    const reader = new FileReader();
    reader.onload = (event) => {
      setTestImage(event.target?.result as string);
    };
    reader.readAsDataURL(file);
  };

  const handleRunTest = async () => {
    if (!testImage || !selectedTrainingId) {
      console.warn('[Test] Missing testImage or selectedTrainingId:', { testImage: !!testImage, selectedTrainingId });
      return;
    }

    setIsTesting(true);
    try {
      // Convert base64 to File object
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

      const url = `${API_BASE_URL}/projects/${projectId}/train/${selectedTrainingId}/test?conf=${testConf}&iou=${testIou}`;
      console.log('[Test] Sending request to:', url, { conf: testConf, iou: testIou, fileSize: file.size });

      const response = await fetch(url, {
          method: 'POST',
          body: formData,
      });
      
      console.log('[Test] Response status:', response.status, response.statusText);

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || t('training.test.failed'));
      }

      const data = await response.json();
      console.log('[Test] Response data:', data);
      setTestResults(data);
    } catch (error: any) {
      console.error('[Test] Error:', error);
      const errorMessage = error.message || 'Unknown error';
      alert(`${t('training.test.failed')}: ${errorMessage}`);
      // Even if error occurs, reset state to allow retry
      setTestResults(null);
    } finally {
      setIsTesting(false);
    }
  };

  const isTraining = currentStatus?.status === 'running';
  const isCompleted = currentStatus?.status === 'completed';
  const isFailed = currentStatus?.status === 'failed';

  return (
    <div className="training-panel-overlay">
      <div className="training-panel-fullscreen">
        <div className="training-panel-header">
          <h2>{t('training.title')}</h2>
          <button className="close-btn" onClick={onClose}>
            <IoClose />
          </button>
        </div>

        <div className="training-panel-body">
          {/* 左侧：训练记录列表和配置 */}
          <div className="training-panel-left">
            {/* 训练记录列表 */}
            <div className="training-records-section">
              <div className="records-header">
                <button 
                  className="btn-new-training"
                  onClick={() => setShowConfigModal(true)}
                  disabled={isLoading}
                >
                  <IoAdd /> {t('training.newTraining')}
                </button>
              </div>
              <div className="training-records-list">
                {trainingRecords.length === 0 ? (
                  <div className="empty-records">{t('training.noTrainingSelected')}</div>
                ) : (
                  trainingRecords.map((record) => (
                    <div
                      key={record.training_id}
                      className={`training-record-item ${selectedTrainingId === record.training_id ? 'active' : ''}`}
                      onClick={() => setSelectedTrainingId(record.training_id)}
                    >
                      <div className="record-header">
                        <span className="record-time">
                          {record.start_time ? new Date(record.start_time).toLocaleString(i18n.language === 'zh' ? 'zh-CN' : 'en-US') : t('common.unknown', '未知时间')}
                        </span>
                        <span className={`record-status status-${record.status}`}>
                          {record.status === 'running' && t('training.statusRunning')}
                          {record.status === 'completed' && t('training.statusCompleted')}
                          {record.status === 'failed' && t('training.statusFailed')}
                          {record.status === 'stopped' && t('training.statusStopped')}
                        </span>
                      </div>
                      <div className="record-info">
                        <span>模型: yolov8{record.model_size}</span>
                        {record.status === 'completed' && record.metrics && record.metrics.mAP50 !== undefined && (
                          <span className="record-metric">
                            mAP50: {(record.metrics.mAP50 * 100).toFixed(1)}%
                          </span>
                        )}
                      </div>
                      {selectedTrainingId === record.training_id && (
                        <button
                          className="record-delete-btn"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteRecord(record.training_id);
                          }}
                        >
                          <IoTrash />
                        </button>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>

          </div>

          {/* 右侧：训练状态和日志 */}
          <div className="training-panel-right">
            {currentStatus ? (
              <>
                {/* 训练状态 */}
                <div className="training-status-section">
                  <div className="status-header">
                    <h3>{t('training.trainingInfo')}</h3>
                    {isCompleted && currentStatus.model_path && (
                      <div>
                        <button className="btn-export-model" onClick={handleExportModel}>
                          <IoDownload /> {t('training.exportModel')}
                        </button>
                        <button className="btn-export-model" onClick={handleTestModel}>
                          <IoImage /> {t('training.testModel')}
                        </button>
                      <button className="btn-export-model" onClick={handleQuantModel}>
                        <IoDownload /> {t('training.quantizeModel')}
                      </button>
                      </div>
                    )}
                  </div>
                  
                  <div className="status-info">
                    <div className="status-item">
                      <span className="status-label">{t('training.status')}:</span>
                      <span className={`status-value status-${currentStatus.status}`}>
                        {currentStatus.status === 'running' && t('training.statusRunning')}
                        {currentStatus.status === 'completed' && t('training.statusCompleted')}
                        {currentStatus.status === 'failed' && t('training.statusFailed')}
                        {currentStatus.status === 'stopped' && t('training.statusStopped')}
                      </span>
                    </div>

                    {currentStatus.start_time && (
                      <div className="status-item">
                        <span className="status-label">{t('training.startTime')}:</span>
                        <span className="status-value">
                          {new Date(currentStatus.start_time).toLocaleString(i18n.language === 'zh' ? 'zh-CN' : 'en-US')}
                        </span>
                      </div>
                    )}

                    {currentStatus.end_time && (
                      <div className="status-item">
                        <span className="status-label">{t('training.endTime')}:</span>
                        <span className="status-value">
                          {new Date(currentStatus.end_time).toLocaleString(i18n.language === 'zh' ? 'zh-CN' : 'en-US')}
                        </span>
                      </div>
                    )}

                    {currentStatus.model_size && (
                      <div className="status-item">
                        <span className="status-label">{t('training.modelType')}:</span>
                        <span className="status-value">{currentStatus.model_type || 'yolov8'}{currentStatus.model_size}</span>
                      </div>
                    )}

                    {currentStatus.epochs !== undefined && (
                      <div className="status-item">
                        <span className="status-label">{t('training.epochs')}:</span>
                        <span className="status-value">{currentStatus.epochs}</span>
                      </div>
                    )}

                    {currentStatus.batch !== undefined && (
                      <div className="status-item">
                        <span className="status-label">{t('training.batchSize')}:</span>
                        <span className="status-value">{currentStatus.batch}</span>
                      </div>
                    )}

                    {currentStatus.imgsz !== undefined && (
                      <div className="status-item">
                        <span className="status-label">{t('training.imageSize')}:</span>
                        <span className="status-value">{currentStatus.imgsz}</span>
                      </div>
                    )}

                    {currentStatus.device && (
                      <div className="status-item">
                        <span className="status-label">{t('training.device')}:</span>
                        <span className="status-value">{currentStatus.device}</span>
                      </div>
                    )}

                    {isFailed && currentStatus.error && (
                      <div className="error-message">
                        <strong>{t('common.error')}:</strong> {currentStatus.error}
                      </div>
                    )}

                    {isCompleted && currentStatus.model_path && (
                      <div className="model-path">
                        <strong>{t('training.modelPath')}:</strong> {currentStatus.model_path}
                      </div>
                    )}

                    {/* 训练性能指标 */}
                    {isCompleted && currentStatus.metrics && (
                      <div className="training-metrics">
                        <h4 className="metrics-title">{t('training.finalMetrics')}</h4>
                        <div className="metrics-grid">
                          {currentStatus.metrics.mAP50 !== undefined && (
                            <div className="metric-item">
                              <span className="metric-label">{t('training.metrics.mAP50')}:</span>
                              <span className="metric-value">{(currentStatus.metrics.mAP50 * 100).toFixed(2)}%</span>
                            </div>
                          )}
                          {currentStatus.metrics['mAP50-95'] !== undefined && (
                            <div className="metric-item">
                              <span className="metric-label">{t('training.metrics.mAP50-95')}:</span>
                              <span className="metric-value">{(currentStatus.metrics['mAP50-95'] * 100).toFixed(2)}%</span>
                            </div>
                          )}
                          {currentStatus.metrics.precision !== undefined && (
                            <div className="metric-item">
                              <span className="metric-label">{t('training.metrics.precision')}:</span>
                              <span className="metric-value">{(currentStatus.metrics.precision * 100).toFixed(2)}%</span>
                            </div>
                          )}
                          {currentStatus.metrics.recall !== undefined && (
                            <div className="metric-item">
                              <span className="metric-label">{t('training.metrics.recall')}:</span>
                              <span className="metric-value">{(currentStatus.metrics.recall * 100).toFixed(2)}%</span>
                            </div>
                          )}
                          {/* {currentStatus.metrics.best_fitness !== undefined && (
                            <div className="metric-item">
                              <span className="metric-label">最佳适应度 (Fitness):</span>
                              <span className="metric-value">{currentStatus.metrics.best_fitness.toFixed(4)}</span>
                            </div>
                          )} */}
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="training-actions">
                    {isTraining && (
                      <button className="btn-stop-training" onClick={handleStopTraining}>
                        {t('training.stopTraining')}
                      </button>
                    )}
                  </div>
                </div>

                {/* 训练日志 */}
                <div className="training-logs-section">
                  <h3>{t('training.trainingLogs')}</h3>
                  <div className="logs-container">
                    {trainingLogs.length === 0 ? (
                      <div className="empty-logs">{t('training.noLogs')}</div>
                    ) : (
                      trainingLogs.map((log, index) => (
                        <div key={index} className="log-line">{log}</div>
                      ))
                    )}
                    <div ref={logsEndRef} />
                  </div>
                </div>
              </>
            ) : (
              <div className="no-training-selected">
                <p>{t('training.noTrainingSelected')}</p>
              </div>
            )}
          </div>
        </div>

        {/* 训练配置弹窗 */}
        {showConfigModal && (
          <div className="config-modal-overlay" onClick={() => !isLoading && setShowConfigModal(false)}>
            <div className="config-modal" onClick={(e) => e.stopPropagation()}>
              <div className="config-modal-header">
                <h3>{t('training.newTrainingTask')}</h3>
                <button 
                  className="close-btn" 
                  onClick={() => setShowConfigModal(false)}
                  disabled={isLoading}
                >
                  <IoClose />
                </button>
              </div>
              
              <div className="config-modal-content">
                <div className="form-container grid-layout">
                <div className="form-item config-item">
                  <label className="required">{t('training.modelType')}</label>
                  <select
                    value={trainingConfig.model_type}
                    onChange={(e) => setTrainingConfig({ ...trainingConfig, model_type: e.target.value })}
                    disabled={isLoading}
                  >
                    <option value="yolov8">{t('training.modelTypeOptions.yolov8')}</option>
                    {/* <option value="yolov11">{t('training.modelTypeOptions.yolov11')}</option>
                    <option value="yolov12">{t('training.modelTypeOptions.yolov12')}</option> */}
                  </select>
                </div>

                <div className="form-item config-item">
                  <label className="required">{t('training.modelSize')}</label>
                  <select
                    value={trainingConfig.model_size}
                    onChange={(e) => setTrainingConfig({ ...trainingConfig, model_size: e.target.value })}
                    disabled={isLoading}
                  >
                    <option value="n">{t('training.modelSizeOptions.n')}</option>
                    <option value="s">{t('training.modelSizeOptions.s')}</option>
                    <option value="m">{t('training.modelSizeOptions.m')}</option>
                    <option value="l">{t('training.modelSizeOptions.l')}</option>
                    <option value="x">{t('training.modelSizeOptions.x')}</option>
                  </select>
                </div>

                <div className="form-item config-item">
                  <label className="required">{t('training.epochsLabel')}</label>
                  <input
                    type="number"
                    min="1"
                    max="1000"
                    value={epochsInput === '' ? trainingConfig.epochs : epochsInput}
                    onChange={(e) => {
                      const value = e.target.value;
                      setEpochsInput(value);
                      if (value !== '' && value !== '-') {
                        const numValue = parseInt(value, 10);
                        if (!isNaN(numValue) && numValue >= 1 && numValue <= 1000) {
                          setTrainingConfig({ ...trainingConfig, epochs: numValue });
                        }
                      }
                    }}
                    onBlur={(e) => {
                      const value = e.target.value;
                      const numValue = parseInt(value, 10);
                      if (value === '' || isNaN(numValue) || numValue < 1) {
                        setTrainingConfig({ ...trainingConfig, epochs: 100 });
                        setEpochsInput('');
                      } else if (numValue > 1000) {
                        setTrainingConfig({ ...trainingConfig, epochs: 1000 });
                        setEpochsInput('');
                      } else {
                        setEpochsInput('');
                      }
                    }}
                    onFocus={(e) => {
                      setEpochsInput(trainingConfig.epochs.toString());
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="form-item config-item">
                  <label className="required">{t('training.imageSizeLabel')}</label>
                  <input
                    type="number"
                    min="320"
                    max="1280"
                    step="32"
                    value={imgszInput === '' ? trainingConfig.imgsz : imgszInput}
                    onChange={(e) => {
                      const value = e.target.value;
                      setImgszInput(value);
                      if (value !== '' && value !== '-') {
                        const numValue = parseInt(value, 10);
                        if (!isNaN(numValue) && numValue >= 320 && numValue <= 1280) {
                          setTrainingConfig({ ...trainingConfig, imgsz: numValue });
                        }
                      }
                    }}
                    onBlur={(e) => {
                      const value = e.target.value;
                      const numValue = parseInt(value, 10);
                      if (value === '' || isNaN(numValue) || numValue < 320) {
                        setTrainingConfig({ ...trainingConfig, imgsz: 640 });
                        setImgszInput('');
                      } else if (numValue > 1280) {
                        setTrainingConfig({ ...trainingConfig, imgsz: 1280 });
                        setImgszInput('');
                      } else {
                        setImgszInput('');
                      }
                    }}
                    onFocus={(e) => {
                      setImgszInput(trainingConfig.imgsz.toString());
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="form-item config-item">
                  <label className="required">{t('training.batchSizeLabel')}</label>
                  <input
                    type="number"
                    min="1"
                    max="64"
                    value={batchInput === '' ? trainingConfig.batch : batchInput}
                    onChange={(e) => {
                      const value = e.target.value;
                      setBatchInput(value);
                      if (value !== '' && value !== '-') {
                        const numValue = parseInt(value, 10);
                        if (!isNaN(numValue) && numValue >= 1 && numValue <= 64) {
                          setTrainingConfig({ ...trainingConfig, batch: numValue });
                        }
                      }
                    }}
                    onBlur={(e) => {
                      const value = e.target.value;
                      const numValue = parseInt(value, 10);
                      if (value === '' || isNaN(numValue) || numValue < 1) {
                        setTrainingConfig({ ...trainingConfig, batch: 16 });
                        setBatchInput('');
                      } else if (numValue > 64) {
                        setTrainingConfig({ ...trainingConfig, batch: 64 });
                        setBatchInput('');
                      } else {
                        setBatchInput('');
                      }
                    }}
                    onFocus={(e) => {
                      setBatchInput(trainingConfig.batch.toString());
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="form-item config-item">
                  <label className="optional" data-optional-text={t('training.optional')}>{t('training.deviceLabel')}</label>
                  <input
                    type="text"
                    placeholder={t('training.devicePlaceholder', '留空自动选择 (cpu/cuda/0/1/mps...)')}
                    value={trainingConfig.device || ''}
                    onChange={(e) => setTrainingConfig({ ...trainingConfig, device: e.target.value || undefined })}
                    disabled={isLoading}
                  />
                </div>

                {/* 学习率参数 */}
                <div className="form-section-divider config-section-divider">
                  <span>{t('training.learningRate.title')}</span>
                </div>

                <div className="form-item config-item">
                  <label className="optional" data-optional-text={t('training.optional')}>{t('training.learningRate.lr0')}</label>
                  <input
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    max="1"
                    placeholder={t('training.learningRate.placeholder')}
                    value={trainingConfig.lr0 ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setTrainingConfig({ ...trainingConfig, lr0: undefined });
                      } else {
                        const numValue = parseFloat(value);
                        if (!isNaN(numValue) && numValue >= 0.0001 && numValue <= 1) {
                          setTrainingConfig({ ...trainingConfig, lr0: numValue });
                        }
                      }
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="form-item config-item">
                  <label className="optional" data-optional-text={t('training.optional')}>{t('training.learningRate.lrf')}</label>
                  <input
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    max="1"
                    placeholder={t('training.learningRate.placeholder')}
                    value={trainingConfig.lrf ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setTrainingConfig({ ...trainingConfig, lrf: undefined });
                      } else {
                        const numValue = parseFloat(value);
                        if (!isNaN(numValue) && numValue >= 0.0001 && numValue <= 1) {
                          setTrainingConfig({ ...trainingConfig, lrf: numValue });
                        }
                      }
                    }}
                    disabled={isLoading}
                  />
                </div>

                {/* 优化器参数 */}
                <div className="form-section-divider config-section-divider">
                  <span>{t('training.optimizer.title')}</span>
                </div>

                <div className="form-item config-item">
                  <label className="optional" data-optional-text={t('training.optional')}>{t('training.optimizer.label')}</label>
                  <select
                    value={trainingConfig.optimizer || ''}
                    onChange={(e) => setTrainingConfig({ ...trainingConfig, optimizer: e.target.value || undefined })}
                    disabled={isLoading}
                  >
                    <option value="">{t('training.optimizer.default')}</option>
                    <option value="SGD">SGD</option>
                    <option value="Adam">Adam</option>
                    <option value="AdamW">AdamW</option>
                    <option value="RMSProp">RMSProp</option>
                  </select>
                </div>

                <div className="form-item config-item">
                  <label className="optional" data-optional-text={t('training.optional')}>{t('training.optimizer.momentum')}</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    max="1"
                    placeholder={t('training.learningRate.placeholder')}
                    value={trainingConfig.momentum ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setTrainingConfig({ ...trainingConfig, momentum: undefined });
                      } else {
                        const numValue = parseFloat(value);
                        if (!isNaN(numValue) && numValue >= 0 && numValue <= 1) {
                          setTrainingConfig({ ...trainingConfig, momentum: numValue });
                        }
                      }
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="form-item config-item">
                  <label className="optional" data-optional-text={t('training.optional')}>{t('training.optimizer.weightDecay')}</label>
                  <input
                    type="number"
                    step="0.0001"
                    min="0"
                    max="0.01"
                    placeholder={t('training.learningRate.placeholder')}
                    value={trainingConfig.weight_decay ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      setTrainingConfig({ ...trainingConfig, weight_decay: value === '' ? undefined : parseFloat(value) });
                    }}
                    disabled={isLoading}
                  />
                </div>

                {/* 训练控制参数 */}
                <div className="form-section-divider config-section-divider">
                  <span>{t('training.control.title')}</span>
                </div>

                <div className="form-item config-item">
                  <label className="optional" data-optional-text={t('training.optional')}>{t('training.control.patience')}</label>
                  <input
                    type="number"
                    min="0"
                    max="1000"
                    placeholder={t('training.control.patiencePlaceholder')}
                    value={trainingConfig.patience ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setTrainingConfig({ ...trainingConfig, patience: undefined });
                      } else {
                        const numValue = parseInt(value, 10);
                        if (!isNaN(numValue) && numValue >= 0 && numValue <= 1000) {
                          setTrainingConfig({ ...trainingConfig, patience: numValue });
                        }
                      }
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="form-item config-item">
                  <label className="optional" data-optional-text={t('training.optional')}>{t('training.control.workers')}</label>
                  <input
                    type="number"
                    min="0"
                    max="16"
                    placeholder={t('training.learningRate.placeholder')}
                    value={trainingConfig.workers ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setTrainingConfig({ ...trainingConfig, workers: undefined });
                      } else {
                        const numValue = parseInt(value, 10);
                        if (!isNaN(numValue) && numValue >= 0 && numValue <= 16) {
                          setTrainingConfig({ ...trainingConfig, workers: numValue });
                        }
                      }
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="form-item config-item checkbox-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={trainingConfig.val !== false}
                      onChange={(e) => setTrainingConfig({ ...trainingConfig, val: e.target.checked || undefined })}
                      disabled={isLoading}
                    />
                    <span>{t('training.control.validation')}</span>
                  </label>
                </div>

                <div className="form-item config-item checkbox-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={trainingConfig.amp !== false}
                      onChange={(e) => setTrainingConfig({ ...trainingConfig, amp: e.target.checked || undefined })}
                      disabled={isLoading}
                    />
                    <span>{t('training.control.amp')}</span>
                  </label>
                </div>

                <div className="form-item config-item">
                  <label className="optional" data-optional-text={t('training.optional')}>{t('training.control.savePeriod')}</label>
                  <input
                    type="number"
                    min="-1"
                    max="100"
                    placeholder={t('training.control.savePeriodPlaceholder')}
                    value={trainingConfig.save_period ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setTrainingConfig({ ...trainingConfig, save_period: undefined });
                      } else {
                        const numValue = parseInt(value, 10);
                        if (!isNaN(numValue) && numValue >= -1 && numValue <= 100) {
                          setTrainingConfig({ ...trainingConfig, save_period: numValue });
                        }
                      }
                    }}
                    disabled={isLoading}
                  />
                </div>

                {/* 高级选项 - 数据增强 */}
                <div className="form-section-divider config-section-divider">
                  <span>{t('training.advanced.title')}</span>
                </div>

                <>
                    <div className="form-item config-item">
                      <label className="optional" data-optional-text={t('training.optional')}>{t('training.advanced.hsvH')}</label>
                      <input
                        type="number"
                        step="0.001"
                        min="0"
                        max="0.1"
                        placeholder={`${t('training.advanced.default')}: 0.015`}
                        value={trainingConfig.hsv_h ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === '') {
                            setTrainingConfig({ ...trainingConfig, hsv_h: undefined });
                          } else {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue) && numValue >= 0 && numValue <= 0.1) {
                              setTrainingConfig({ ...trainingConfig, hsv_h: numValue });
                            }
                          }
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="form-item config-item">
                      <label className="optional" data-optional-text={t('training.optional')}>{t('training.advanced.hsvS')}</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder={`${t('training.advanced.default')}: 0.7`}
                        value={trainingConfig.hsv_s ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === '') {
                            setTrainingConfig({ ...trainingConfig, hsv_s: undefined });
                          } else {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue) && numValue >= 0 && numValue <= 1) {
                              setTrainingConfig({ ...trainingConfig, hsv_s: numValue });
                            }
                          }
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="form-item config-item">
                      <label className="optional" data-optional-text={t('training.optional')}>{t('training.advanced.hsvV')}</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder={`${t('training.advanced.default')}: 0.4`}
                        value={trainingConfig.hsv_v ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === '') {
                            setTrainingConfig({ ...trainingConfig, hsv_v: undefined });
                          } else {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue) && numValue >= 0 && numValue <= 1) {
                              setTrainingConfig({ ...trainingConfig, hsv_v: numValue });
                            }
                          }
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="form-item config-item">
                      <label className="optional" data-optional-text={t('training.optional')}>{t('training.advanced.degrees')}</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="45"
                        placeholder={`${t('training.advanced.default')}: 0.0`}
                        value={trainingConfig.degrees ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === '') {
                            setTrainingConfig({ ...trainingConfig, degrees: undefined });
                          } else {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue) && numValue >= 0 && numValue <= 45) {
                              setTrainingConfig({ ...trainingConfig, degrees: numValue });
                            }
                          }
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="form-item config-item">
                      <label className="optional" data-optional-text={t('training.optional')}>{t('training.advanced.translate')}</label>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        max="0.5"
                        placeholder={`${t('training.advanced.default')}: 0.1`}
                        value={trainingConfig.translate ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === '') {
                            setTrainingConfig({ ...trainingConfig, translate: undefined });
                          } else {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue) && numValue >= 0 && numValue <= 0.5) {
                              setTrainingConfig({ ...trainingConfig, translate: numValue });
                            }
                          }
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="form-item config-item">
                      <label className="optional" data-optional-text={t('training.optional')}>{t('training.advanced.scale')}</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder={`${t('training.advanced.default')}: 0.5`}
                        value={trainingConfig.scale ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === '') {
                            setTrainingConfig({ ...trainingConfig, scale: undefined });
                          } else {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue) && numValue >= 0 && numValue <= 1) {
                              setTrainingConfig({ ...trainingConfig, scale: numValue });
                            }
                          }
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="form-item config-item">
                      <label className="optional" data-optional-text={t('training.optional')}>{t('training.advanced.shear')}</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="10"
                        placeholder={`${t('training.advanced.default')}: 0.0`}
                        value={trainingConfig.shear ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === '') {
                            setTrainingConfig({ ...trainingConfig, shear: undefined });
                          } else {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue) && numValue >= 0 && numValue <= 10) {
                              setTrainingConfig({ ...trainingConfig, shear: numValue });
                            }
                          }
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="form-item config-item">
                      <label>{t('training.advanced.perspective')}</label>
                      <input
                        type="number"
                        step="0.001"
                        min="0"
                        max="0.01"
                        placeholder={`${t('training.advanced.default')}: 0.0`}
                        value={trainingConfig.perspective ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === '') {
                            setTrainingConfig({ ...trainingConfig, perspective: undefined });
                          } else {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue) && numValue >= 0 && numValue <= 0.01) {
                              setTrainingConfig({ ...trainingConfig, perspective: numValue });
                            }
                          }
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="form-item config-item">
                      <label className="optional" data-optional-text={t('training.optional')}>{t('training.advanced.flipud')}</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder={`${t('training.advanced.default')}: 0.0`}
                        value={trainingConfig.flipud ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === '') {
                            setTrainingConfig({ ...trainingConfig, flipud: undefined });
                          } else {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue) && numValue >= 0 && numValue <= 1) {
                              setTrainingConfig({ ...trainingConfig, flipud: numValue });
                            }
                          }
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="form-item config-item">
                      <label className="optional" data-optional-text={t('training.optional')}>{t('training.advanced.fliplr')}</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder={`${t('training.advanced.default')}: 0.5`}
                        value={trainingConfig.fliplr ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === '') {
                            setTrainingConfig({ ...trainingConfig, fliplr: undefined });
                          } else {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue) && numValue >= 0 && numValue <= 1) {
                              setTrainingConfig({ ...trainingConfig, fliplr: numValue });
                            }
                          }
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="form-item config-item">
                      <label className="optional" data-optional-text={t('training.optional')}>{t('training.advanced.mosaic')}</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder={`${t('training.advanced.default')}: 1.0`}
                        value={trainingConfig.mosaic ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === '') {
                            setTrainingConfig({ ...trainingConfig, mosaic: undefined });
                          } else {
                            const numValue = parseFloat(value);
                            if (!isNaN(numValue) && numValue >= 0 && numValue <= 1) {
                              setTrainingConfig({ ...trainingConfig, mosaic: numValue });
                            }
                          }
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="form-item config-item">
                      <label className="optional" data-optional-text={t('training.optional')}>{t('training.advanced.mixup')}</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder={`${t('training.advanced.default')}: 0.0`}
                        value={trainingConfig.mixup ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, mixup: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>
                </>
                </div>
              </div>

              <div className="config-modal-actions">
                {/* <button
                  className="btn-cancel"
                  onClick={() => setShowConfigModal(false)}
                  disabled={isLoading}
                >
                  取消
                </button> */}
                <button
                  className="btn-start-training"
                  onClick={handleStartTraining}
                  disabled={isLoading}
                >
                  {isLoading ? t('common.loading') : t('training.startTraining')}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 模型测试弹窗 */}
        {showTestModal && (
          <div className="config-modal-overlay" onClick={() => !isTesting && setShowTestModal(false)}>
            <div className="config-modal test-modal" onClick={(e) => e.stopPropagation()}>
              <div className="config-modal-header">
                <h3>{t('training.test.title')}</h3>
                <button 
                  className="close-btn" 
                  onClick={() => setShowTestModal(false)}
                  disabled={isTesting}
                >
                  <IoClose />
                </button>
              </div>
              
              <div className="config-modal-content">
                <div className="test-modal-body">
                  <div className="test-left">
                    <div className="test-upload-section">
                      <label className="test-upload-label">
                        <input
                          type="file"
                          accept="image/*"
                          onChange={handleImageUpload}
                          disabled={isTesting}
                          style={{ display: 'none' }}
                        />
                        <div className="test-upload-area">
                          {testImage ? (
                            <img src={testImage} alt={t('training.test.preview')} className="test-preview-image" />
                          ) : (
                            <div className="test-upload-placeholder">
                              <IoImage size={48} />
                              <p>{t('training.test.uploadPlaceholder')}</p>
                            </div>
                          )}
                        </div>
                      </label>
                    </div>

                    <div className="config-item">
                      <label>{t('training.test.confThreshold')}</label>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        max="1"
                        value={testConf}
                        onChange={(e) => {
                          const v = parseFloat(e.target.value);
                          if (!isNaN(v)) setTestConf(Math.min(1, Math.max(0, v)));
                        }}
                        disabled={isTesting}
                      />
                    </div>

                    <div className="config-item">
                      <label>{t('training.test.iouThreshold', 'IoU 阈值 (iou)')}</label>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        max="1"
                        value={testIou}
                        onChange={(e) => {
                          const v = parseFloat(e.target.value);
                          if (!isNaN(v)) setTestIou(Math.min(1, Math.max(0, v)));
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
                            <img src={testResults.annotated_image} alt={t('training.test.detectionResult')} />
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
                                    <span className="detection-confidence">{t('training.test.confidence')}: {(det.confidence * 100).toFixed(2)}%</span>
                                    <span className="detection-bbox">
                                      [{det.bbox.x1.toFixed(0)}, {det.bbox.y1.toFixed(0)}, {det.bbox.x2.toFixed(0)}, {det.bbox.y2.toFixed(0)}]
                                    </span>
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <div className="detection-list-container">
                                <div className="detection-item no-detections">
                                  <span>{t('training.test.noDetections', '未检测到任何目标')}</span>
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
              </div>

              <div className="config-modal-actions">
                <button
                  className="btn-start-training"
                  onClick={handleRunTest}
                  disabled={!testImage || isTesting}
                >
                  {isTesting ? t('training.test.testing', '检测中...') : t('training.test.start', '开始检测')}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 量化导出弹窗 */}
        {showQuantModal && (
          <div className="config-modal-overlay" onClick={() => {
            if (isQuanting) {
              if (!window.confirm(t('quantization.confirmClose'))) {
                return;
              }
            }
            // Cleanup timers and state
            if (quantTimeoutRef.current) {
              clearTimeout(quantTimeoutRef.current);
              quantTimeoutRef.current = null;
            }
            setShowQuantModal(false);
            setQuantProgress('');
            setQuantStartTime(null);
            setQuantElapsedTime(0);
          }}>
            <div className="config-modal quant-modal" onClick={(e) => e.stopPropagation()}>
              <div className="config-modal-header">
                <h3>{t('quantization.title')}</h3>
                <button 
                  className="close-btn" 
                  onClick={() => {
                    if (isQuanting) {
                      if (!window.confirm(t('quantization.confirmClose'))) {
                        return;
                      }
                    }
                    // Cleanup timers and state
                    if (quantTimeoutRef.current) {
                      clearTimeout(quantTimeoutRef.current);
                      quantTimeoutRef.current = null;
                    }
                    setShowQuantModal(false);
                    setQuantProgress('');
                    setQuantStartTime(null);
                    setQuantElapsedTime(0);
                  }}
                  title={isQuanting ? t('training.quantization.closeWindowHint') : t('training.quantization.closeWindow')}
                >
                  <IoClose />
                </button>
              </div>
              
              <div className="config-modal-content">
                {/* Form: only show when quantization hasn't started and no result */}
                {!isQuanting && !quantResult && (
                  <>
                <div className="config-item">
                      <label>{t('quantization.inputSize')}</label>
                  <input
                    type="number"
                    min="32"
                    max="2048"
                    step="32"
                    value={quantImgSz}
                    onChange={(e) => setQuantImgSz(Math.max(32, Math.min(2048, parseInt(e.target.value || '0', 10))))}
                  />
                </div>

                <div className="config-item checkbox-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={quantInt8}
                      onChange={(e) => setQuantInt8(e.target.checked)}
                    />
                        <span>{t('quantization.useInt8')}</span>
                  </label>
                </div>

                <div className="config-item checkbox-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={quantNe301}
                      onChange={(e) => setQuantNe301(e.target.checked)}
                    />
                        <span>{t('quantization.ne301Device')}</span>
                  </label>
                </div>

                <div className="config-item">
                      <label>{t('quantization.calibFraction')}</label>
                  <input
                    type="number"
                    step="0.05"
                    min="0"
                    max="1"
                    value={quantFraction}
                    onChange={(e) => {
                      const v = parseFloat(e.target.value);
                      if (!isNaN(v)) setQuantFraction(Math.min(1, Math.max(0, v)));
                    }}
                  />
                </div>
                  </>
                )}

                {/* Waiting process: show when quantization is in progress */}
                {isQuanting && (
                  <div className="quant-progress-section">
                    <div className="quant-progress-header">
                      <div className="quant-progress-spinner"></div>
                      <span className="quant-progress-text">{t('quantization.inProgress')}</span>
                    </div>
                    {quantProgress && (
                      <div className="quant-progress-message">
                        {quantProgress}
                      </div>
                    )}
                    {quantStartTime && (
                      <div className="quant-progress-time">
                        {t('quantization.elapsedTime')}: {quantElapsedTime} {t('quantization.seconds')}
                      </div>
                    )}
                    <div className="quant-progress-hint">
                      <p>{t('quantization.stepsDesc', '量化过程可能需要几分钟时间，包括：')}</p>
                      <ul>
                        <li>{t('quantization.steps.tflite')}</li>
                        {quantNe301 && (
                          <>
                            <li>{t('quantization.steps.json')}</li>
                            <li>{t('quantization.steps.compile')}</li>
                          </>
                        )}
                      </ul>
                    </div>
                  </div>
                )}

                {/* Result: show when quantization is completed */}
                {quantResult && !isQuanting && (
                  <div className="quant-result">
                    <div className="quant-result-message">
                      <strong>{t('quantization.result')}:</strong> {quantResult.message || t('common.success')}
                    </div>
                    {quantResult.params && (
                      <div className="quant-result-params">
                        {t('quantization.params')}: imgsz={quantResult.params.imgsz}, int8={String(quantResult.params.int8)}, fraction={quantResult.params.fraction}
                      </div>
                    )}
                    <div className="quant-files-list">
                      {quantResult.tflite_path && (
                        <div className="quant-file-item">
                          <div className="quant-file-info">
                            <div className="quant-file-label">{t('quantization.files.tflite')}</div>
                            <div className="quant-file-name">{quantResult.tflite_path.split('/').pop()}</div>
                          </div>
                          <button
                            className="btn-download-file"
                            onClick={() => handleDownloadExportFile('tflite')}
                          >
                            <IoDownload /> {t('quantization.download')}
                          </button>
                        </div>
                      )}
                      {quantResult.ne301_tflite && (
                        <div className="quant-file-item">
                          <div className="quant-file-info">
                            <div className="quant-file-label">{t('quantization.files.ne301Tflite')}</div>
                            <div className="quant-file-name">{quantResult.ne301_tflite.split('/').pop()}</div>
                          </div>
                          <button
                            className="btn-download-file"
                            onClick={() => handleDownloadExportFile('ne301_tflite')}
                          >
                            <IoDownload /> {t('quantization.download')}
                          </button>
                        </div>
                      )}
                      {quantResult.ne301_json && (
                        <div className="quant-file-item">
                          <div className="quant-file-info">
                            <div className="quant-file-label">{t('quantization.files.ne301Json')}</div>
                            <div className="quant-file-name">{quantResult.ne301_json.split('/').pop()}</div>
                          </div>
                          <button
                            className="btn-download-file"
                            onClick={() => handleDownloadExportFile('ne301_json')}
                          >
                            <IoDownload /> {t('quantization.download')}
                          </button>
                        </div>
                      )}
                      {quantResult.ne301_model_bin && (
                        <div className="quant-file-item model-package">
                          <div className="quant-file-info">
                            <div className="quant-file-label">{t('quantization.files.ne301Bin')}</div>
                            <div className="quant-file-name">{quantResult.ne301_model_bin.split('/').pop()}</div>
                          </div>
                          <button
                            className="btn-download-file model-package"
                            onClick={() => handleDownloadExportFile('ne301_model_bin')}
                          >
                            <IoDownload /> {t('training.quantization.downloadPackage')}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>

              <div className="config-modal-actions">
                {/* 按钮：只在未开始量化且无结果时显示 */}
                {!isQuanting && !quantResult && (
                <button
                  className="btn-start-training"
                  onClick={async () => {
                    if (!selectedTrainingId) return;
                    setIsQuanting(true);
                    setQuantResult(null);
                    setQuantProgress(t('quantization.initializing'));
                    const startTime = Date.now();
                    setQuantStartTime(startTime);
                    setQuantElapsedTime(0);
                    
                    // Clear previous timeout timer
                    if (quantTimeoutRef.current) {
                      clearTimeout(quantTimeoutRef.current);
                    }
                    
                    // Set timed updates for progress hints
                    const progressSteps = [
                      { delay: 3000, message: t('quantization.loadingModel') },
                      { delay: 8000, message: t('quantization.quantizing') },
                    ];
                    
                    if (quantNe301) {
                      progressSteps.push(
                        { delay: 20000, message: t('quantization.generatingJson') },
                        { delay: 40000, message: t('quantization.compiling') }
                      );
                    }
                    
                    progressSteps.forEach(({ delay, message }) => {
                      quantTimeoutRef.current = setTimeout(() => {
                        setQuantProgress(message);
                      }, delay);
                    });
                    
                    try {
                      const response = await fetch(
                        `${API_BASE_URL}/projects/${projectId}/train/${selectedTrainingId}/export/tflite?imgsz=${quantImgSz}&int8=${quantInt8}&fraction=${quantFraction}&ne301=${quantNe301}`,
                        { method: 'POST' }
                      );
                      
                      // Clear all progress hint timers
                      if (quantTimeoutRef.current) {
                        clearTimeout(quantTimeoutRef.current);
                        quantTimeoutRef.current = null;
                      }
                      
                      if (!response.ok) {
                        const err = await response.json();
                        throw new Error(err.detail || t('quantization.failed'));
                      }
                      
                      const data = await response.json();
                      setQuantResult(data);
                      
                      // Show success message, but don't use alert (more user-friendly)
                      const finalElapsedTime = quantElapsedTime || (quantStartTime ? Math.floor((Date.now() - quantStartTime) / 1000) : 0);
                      const minutes = Math.floor(finalElapsedTime / 60);
                      const seconds = finalElapsedTime % 60;
                      const timeStr = minutes > 0 ? `${minutes}${t('quantization.minutes', '分')}${seconds}${t('quantization.seconds')}` : `${seconds}${t('quantization.seconds')}`;
                      setQuantProgress(`${t('quantization.success')}: ${timeStr}`);
                      
                      // Immediately clear progress hint, show result directly
                      setTimeout(() => {
                        setQuantProgress('');
                      }, 100);
                    } catch (error: any) {
                      // Clear progress hint timer
                      if (quantTimeoutRef.current) {
                        clearTimeout(quantTimeoutRef.current);
                        quantTimeoutRef.current = null;
                      }
                      
                      const errorMessage = error.message || 'Unknown error';
                      setQuantProgress(`✗ ${t('quantization.failed')}: ${errorMessage}`);
                      
                      // Show alert after 5 seconds, let user see error message in progress hint first
                      setTimeout(() => {
                        alert(`${t('quantization.failed')}: ${errorMessage}`);
                      }, 500);
                    } finally {
                      setIsQuanting(false);
                      setQuantStartTime(null);
                      // Don't clear quantElapsedTime, keep final time display
                    }
                  }}
                >
                  {t('training.quantization.startQuantize')}
                </button>
                )}
                
                {/* Action buttons after completion */}
                {quantResult && !isQuanting && (
                  <button
                    className="btn-start-training"
                    onClick={() => {
                      setQuantResult(null);
                      setQuantProgress('');
                      setQuantElapsedTime(0);
                      setQuantStartTime(null);
                    }}
                  >
                    {t('quantization.reQuantize')}
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
