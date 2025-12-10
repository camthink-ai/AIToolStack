import React, { useState, useEffect, useRef, useCallback } from 'react';
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
  model_size: string;
  epochs: number;
  imgsz: number;
  batch: number;
  device?: string;
  // 学习率相关
  lr0?: number;
  lrf?: number;
  // 优化器相关
  optimizer?: string;
  momentum?: number;
  weight_decay?: number;
  // 训练控制
  patience?: number;
  workers?: number;
  val?: boolean;
  save_period?: number;
  amp?: boolean;
  // 数据增强（高级选项）
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
  const [trainingConfig, setTrainingConfig] = useState<TrainingRequest>({
    model_size: 'n',
    epochs: 100,
    imgsz: 640,
    batch: 16,
    device: undefined,
    // 学习率相关
    lr0: undefined,
    lrf: undefined,
    // 优化器相关
    optimizer: undefined,
    momentum: undefined,
    weight_decay: undefined,
    // 训练控制
    patience: undefined,
    workers: undefined,
    val: undefined,
    save_period: undefined,
    amp: undefined,
    // 数据增强（高级选项）
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
  const [showAdvanced, setShowAdvanced] = useState(false);
  
  const logsEndRef = useRef<HTMLDivElement>(null);
  const recordsIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const logsIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const currentStatus = trainingRecords.find(r => r.training_id === selectedTrainingId) || 
                       (trainingRecords.length > 0 ? trainingRecords[0] : null);

  // 获取训练记录列表的函数
  const fetchRecords = useCallback(async () => {
    if (!projectId) return;

    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train/records`);
      const data = await response.json();
      setTrainingRecords(data);
      
      // 如果没有选中的训练，选择最新的（第一个）
      setSelectedTrainingId(prev => {
        if (!prev && data.length > 0) {
          return data[0].training_id;
        }
        return prev;
      });
      
      // 检查是否有正在运行的训练
      const hasRunningTraining = data.some((r: TrainingRecord) => r.status === 'running');
      
      // 如果有正在运行的训练，且还没有设置轮询，则设置轮询
      if (hasRunningTraining && !recordsIntervalRef.current) {
        recordsIntervalRef.current = setInterval(() => {
          fetchRecords();
        }, 5000);
      } else if (!hasRunningTraining && recordsIntervalRef.current) {
        // 如果没有正在运行的训练，清除轮询
        clearInterval(recordsIntervalRef.current);
        recordsIntervalRef.current = null;
      }
    } catch (error) {
      console.error('Failed to fetch training records:', error);
    }
  }, [projectId]);

  // 获取训练记录列表
  useEffect(() => {
    if (!projectId) return;

    // 清除之前的轮询
    if (recordsIntervalRef.current) {
      clearInterval(recordsIntervalRef.current);
      recordsIntervalRef.current = null;
    }

    // 首次获取
    fetchRecords();

    return () => {
      if (recordsIntervalRef.current) {
        clearInterval(recordsIntervalRef.current);
        recordsIntervalRef.current = null;
      }
    };
  }, [projectId, fetchRecords]);

  // 获取选中训练的日志
  useEffect(() => {
    if (!selectedTrainingId) {
      setTrainingLogs([]);
      // 清除之前的轮询
      if (logsIntervalRef.current) {
        clearInterval(logsIntervalRef.current);
        logsIntervalRef.current = null;
      }
      // 重置测试和量化相关状态
      setTestImage(null);
      setTestResults(null);
      setQuantResult(null);
      return;
    }
    
    // 切换训练记录时，重置测试和量化相关状态
    setTestImage(null);
    setTestResults(null);
    setQuantResult(null);

    const fetchLogs = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train/${selectedTrainingId}/logs`);
        const data = await response.json();
        setTrainingLogs(data.logs || []);
        
        // 同时获取训练状态来判断是否需要继续轮询
        const statusResponse = await fetch(`${API_BASE_URL}/projects/${projectId}/train/status?training_id=${selectedTrainingId}`);
        const statusData = await statusResponse.json();
        const isRunning = statusData?.status === 'running';
        
        // 如果训练中，且还没有设置轮询，则设置轮询
        if (isRunning && !logsIntervalRef.current) {
          logsIntervalRef.current = setInterval(fetchLogs, 2000);
        } else if (!isRunning && logsIntervalRef.current) {
          // 如果训练已完成/失败，清除轮询
          clearInterval(logsIntervalRef.current);
          logsIntervalRef.current = null;
        }
      } catch (error) {
        console.error('Failed to fetch training logs:', error);
      }
    };

    // 清除之前的轮询
    if (logsIntervalRef.current) {
      clearInterval(logsIntervalRef.current);
      logsIntervalRef.current = null;
    }

    // 首次获取
    fetchLogs();

    return () => {
      if (logsIntervalRef.current) {
        clearInterval(logsIntervalRef.current);
        logsIntervalRef.current = null;
      }
    };
  }, [projectId, selectedTrainingId]);

  // 自动滚动到底部
  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [trainingLogs]);

  const handleStartTraining = async () => {
    setIsLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(trainingConfig),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '启动训练失败');
      }

      const data = await response.json();
      // 训练启动后，立即刷新训练记录列表
      setShowConfigModal(false);
      // 重置配置
      setTrainingConfig({
        model_size: 'n',
        epochs: 100,
        imgsz: 640,
        batch: 16,
        device: undefined
      });
      
      // 立即刷新训练记录列表，以便显示新创建的记录
      await fetchRecords();
      
      // 如果返回了训练ID，选中它
      if (data.training_id) {
        setSelectedTrainingId(data.training_id);
      }
    } catch (error: any) {
      alert(`启动训练失败: ${error.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleStopTraining = async () => {
    if (!window.confirm('确定要停止训练吗？')) {
      return;
    }

    setIsLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train/stop${selectedTrainingId ? `?training_id=${selectedTrainingId}` : ''}`, {
        method: 'POST',
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '停止训练失败');
      }
      // 停止后刷新记录与日志
      await fetchRecords();
    } catch (error: any) {
      alert(`停止训练失败: ${error.message}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteRecord = async (trainingId: string) => {
    if (!window.confirm('确定要删除这条训练记录吗？')) {
      return;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train?training_id=${trainingId}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '删除训练记录失败');
      }

      // 如果删除的是当前选中的记录，切换到其他记录
      if (selectedTrainingId === trainingId) {
        const remaining = trainingRecords.filter(r => r.training_id !== trainingId);
        setSelectedTrainingId(remaining.length > 0 ? remaining[0].training_id : null);
      }
      
      // 刷新训练记录列表
      await fetchRecords();
    } catch (error: any) {
      alert(`删除训练记录失败: ${error.message}`);
    }
  };

  const handleExportModel = async () => {
    if (!currentStatus?.model_path) {
      alert('模型文件不存在');
      return;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/projects/${projectId}/train/${selectedTrainingId}/export`);
      if (!response.ok) {
        throw new Error('导出模型失败');
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
      alert(`导出模型失败: ${error.message}`);
    }
  };

  const handleTestModel = () => {
    if (!currentStatus?.model_path) {
      alert('模型文件不存在');
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
      alert('模型文件不存在');
      return;
    }
    setShowQuantModal(true);
    setQuantImgSz(256);
    setQuantInt8(true);
    setQuantFraction(0.2);
    setQuantNe301(true);
    setQuantResult(null);
  };

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // 预览图片
    const reader = new FileReader();
    reader.onload = (event) => {
      setTestImage(event.target?.result as string);
    };
    reader.readAsDataURL(file);
  };

  const handleRunTest = async () => {
    if (!testImage || !selectedTrainingId) return;

    setIsTesting(true);
    try {
      // 将base64转换为File对象
      const base64Data = testImage.split(',')[1];
      const byteCharacters = atob(base64Data);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
      }
      const byteArray = new Uint8Array(byteNumbers);
      const blob = new Blob([byteArray]);
      const file = new File([blob], 'test_image.png', { type: 'image/png' });

      const formData = new FormData();
      formData.append('file', file);
      formData.append('conf', String(testConf));
      formData.append('iou', String(testIou));

      const response = await fetch(
        `${API_BASE_URL}/projects/${projectId}/train/${selectedTrainingId}/test?conf=${testConf}&iou=${testIou}`,
        {
          method: 'POST',
          body: formData,
        }
      );

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '模型测试失败');
      }

      const data = await response.json();
      setTestResults(data);
    } catch (error: any) {
      alert(`模型测试失败: ${error.message}`);
    } finally {
      setIsTesting(false);
    }
  };

  const isTraining = currentStatus?.status === 'running';
  const isCompleted = currentStatus?.status === 'completed';
  const isFailed = currentStatus?.status === 'failed';

  return (
    <div className="training-panel-overlay" onClick={onClose}>
      <div className="training-panel-fullscreen" onClick={(e) => e.stopPropagation()}>
        <div className="training-panel-header">
          <h2>模型训练管理</h2>
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
                <h3>训练记录</h3>
                <button 
                  className="btn-new-training"
                  onClick={() => setShowConfigModal(true)}
                  disabled={isLoading}
                >
                  <IoAdd /> 新建训练
                </button>
              </div>
              <div className="training-records-list">
                {trainingRecords.length === 0 ? (
                  <div className="empty-records">暂无训练记录</div>
                ) : (
                  trainingRecords.map((record) => (
                    <div
                      key={record.training_id}
                      className={`training-record-item ${selectedTrainingId === record.training_id ? 'active' : ''}`}
                      onClick={() => setSelectedTrainingId(record.training_id)}
                    >
                      <div className="record-header">
                        <span className="record-time">
                          {record.start_time ? new Date(record.start_time).toLocaleString('zh-CN') : '未知时间'}
                        </span>
                        <span className={`record-status status-${record.status}`}>
                          {record.status === 'running' && '训练中'}
                          {record.status === 'completed' && '已完成'}
                          {record.status === 'failed' && '失败'}
                          {record.status === 'stopped' && '已停止'}
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
                    <h3>训练状态</h3>
                    {isCompleted && currentStatus.model_path && (
                      <div>
                        <button className="btn-export-model" onClick={handleExportModel}>
                          <IoDownload /> 导出模型
                        </button>
                        <button className="btn-test-model" onClick={handleTestModel}>
                          <IoImage /> 测试模型
                        </button>
                      <button className="btn-quant-model" onClick={handleQuantModel}>
                        <IoDownload /> 量化(TFLite)
                      </button>
                      </div>
                    )}
                  </div>
                  
                  <div className="status-info">
                    <div className="status-item">
                      <span className="status-label">状态:</span>
                      <span className={`status-value status-${currentStatus.status}`}>
                        {currentStatus.status === 'running' && '训练中'}
                        {currentStatus.status === 'completed' && '已完成'}
                        {currentStatus.status === 'failed' && '失败'}
                        {currentStatus.status === 'stopped' && '已停止'}
                      </span>
                    </div>

                    {currentStatus.start_time && (
                      <div className="status-item">
                        <span className="status-label">开始时间:</span>
                        <span className="status-value">
                          {new Date(currentStatus.start_time).toLocaleString('zh-CN')}
                        </span>
                      </div>
                    )}

                    {currentStatus.end_time && (
                      <div className="status-item">
                        <span className="status-label">结束时间:</span>
                        <span className="status-value">
                          {new Date(currentStatus.end_time).toLocaleString('zh-CN')}
                        </span>
                      </div>
                    )}

                    {currentStatus.model_size && (
                      <div className="status-item">
                        <span className="status-label">模型大小:</span>
                        <span className="status-value">yolov8{currentStatus.model_size}</span>
                      </div>
                    )}

                    {currentStatus.epochs !== undefined && (
                      <div className="status-item">
                        <span className="status-label">训练轮数:</span>
                        <span className="status-value">{currentStatus.epochs}</span>
                      </div>
                    )}

                    {currentStatus.batch !== undefined && (
                      <div className="status-item">
                        <span className="status-label">批次大小:</span>
                        <span className="status-value">{currentStatus.batch}</span>
                      </div>
                    )}

                    {currentStatus.imgsz !== undefined && (
                      <div className="status-item">
                        <span className="status-label">图像尺寸:</span>
                        <span className="status-value">{currentStatus.imgsz}</span>
                      </div>
                    )}

                    {currentStatus.device && (
                      <div className="status-item">
                        <span className="status-label">设备:</span>
                        <span className="status-value">{currentStatus.device}</span>
                      </div>
                    )}

                    {isFailed && currentStatus.error && (
                      <div className="error-message">
                        <strong>错误:</strong> {currentStatus.error}
                      </div>
                    )}

                    {isCompleted && currentStatus.model_path && (
                      <div className="model-path">
                        <strong>模型路径:</strong> {currentStatus.model_path}
                      </div>
                    )}

                    {/* 训练性能指标 */}
                    {isCompleted && currentStatus.metrics && (
                      <div className="training-metrics">
                        <h4 className="metrics-title">最终验证性能指标</h4>
                        <div className="metrics-grid">
                          {currentStatus.metrics.mAP50 !== undefined && (
                            <div className="metric-item">
                              <span className="metric-label">mAP50:</span>
                              <span className="metric-value">{(currentStatus.metrics.mAP50 * 100).toFixed(2)}%</span>
                            </div>
                          )}
                          {currentStatus.metrics['mAP50-95'] !== undefined && (
                            <div className="metric-item">
                              <span className="metric-label">mAP50-95:</span>
                              <span className="metric-value">{(currentStatus.metrics['mAP50-95'] * 100).toFixed(2)}%</span>
                            </div>
                          )}
                          {currentStatus.metrics.precision !== undefined && (
                            <div className="metric-item">
                              <span className="metric-label">精确率 (Precision):</span>
                              <span className="metric-value">{(currentStatus.metrics.precision * 100).toFixed(2)}%</span>
                            </div>
                          )}
                          {currentStatus.metrics.recall !== undefined && (
                            <div className="metric-item">
                              <span className="metric-label">召回率 (Recall):</span>
                              <span className="metric-value">{(currentStatus.metrics.recall * 100).toFixed(2)}%</span>
                            </div>
                          )}
                          {currentStatus.metrics.best_fitness !== undefined && (
                            <div className="metric-item">
                              <span className="metric-label">最佳适应度 (Fitness):</span>
                              <span className="metric-value">{currentStatus.metrics.best_fitness.toFixed(4)}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="training-actions">
                    {isTraining && (
                      <button className="btn-stop-training" onClick={handleStopTraining}>
                        停止训练
                      </button>
                    )}
                  </div>
                </div>

                {/* 训练日志 */}
                <div className="training-logs-section">
                  <h3>训练日志</h3>
                  <div className="logs-container">
                    {trainingLogs.length === 0 ? (
                      <div className="empty-logs">暂无日志</div>
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
                <p>请选择一个训练记录或开始新的训练</p>
              </div>
            )}
          </div>
        </div>

        {/* 训练配置弹窗 */}
        {showConfigModal && (
          <div className="config-modal-overlay" onClick={() => !isLoading && setShowConfigModal(false)}>
            <div className="config-modal" onClick={(e) => e.stopPropagation()}>
              <div className="config-modal-header">
                <h3>新建训练任务</h3>
                <button 
                  className="close-btn" 
                  onClick={() => setShowConfigModal(false)}
                  disabled={isLoading}
                >
                  <IoClose />
                </button>
              </div>
              
              <div className="config-modal-content">
                <div className="config-item">
                  <label>模型大小</label>
                  <select
                    value={trainingConfig.model_size}
                    onChange={(e) => setTrainingConfig({ ...trainingConfig, model_size: e.target.value })}
                    disabled={isLoading}
                  >
                    <option value="n">Nano (最快，最小)</option>
                    <option value="s">Small (小)</option>
                    <option value="m">Medium (中等)</option>
                    <option value="l">Large (大)</option>
                    <option value="x">XLarge (最大，最准确)</option>
                  </select>
                </div>

                <div className="config-item">
                  <label>训练轮数 (Epochs)</label>
                  <input
                    type="number"
                    min="1"
                    max="1000"
                    value={trainingConfig.epochs}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setTrainingConfig({ ...trainingConfig, epochs: 0 });
                      } else {
                        const numValue = parseInt(value, 10);
                        if (!isNaN(numValue) && numValue >= 1) {
                          setTrainingConfig({ ...trainingConfig, epochs: numValue });
                        }
                      }
                    }}
                    onBlur={(e) => {
                      const value = parseInt(e.target.value, 10);
                      if (isNaN(value) || value < 1) {
                        setTrainingConfig({ ...trainingConfig, epochs: 100 });
                      }
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="config-item">
                  <label>图像尺寸 (Image Size)</label>
                  <input
                    type="number"
                    min="320"
                    max="1280"
                    step="32"
                    value={trainingConfig.imgsz}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setTrainingConfig({ ...trainingConfig, imgsz: 0 });
                      } else {
                        const numValue = parseInt(value, 10);
                        if (!isNaN(numValue) && numValue >= 320) {
                          setTrainingConfig({ ...trainingConfig, imgsz: numValue });
                        }
                      }
                    }}
                    onBlur={(e) => {
                      const value = parseInt(e.target.value, 10);
                      if (isNaN(value) || value < 320) {
                        setTrainingConfig({ ...trainingConfig, imgsz: 640 });
                      }
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="config-item">
                  <label>批次大小 (Batch Size)</label>
                  <input
                    type="number"
                    min="1"
                    max="64"
                    value={trainingConfig.batch}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === '') {
                        setTrainingConfig({ ...trainingConfig, batch: 0 });
                      } else {
                        const numValue = parseInt(value, 10);
                        if (!isNaN(numValue) && numValue >= 1) {
                          setTrainingConfig({ ...trainingConfig, batch: numValue });
                        }
                      }
                    }}
                    onBlur={(e) => {
                      const value = parseInt(e.target.value, 10);
                      if (isNaN(value) || value < 1) {
                        setTrainingConfig({ ...trainingConfig, batch: 16 });
                      }
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="config-item">
                  <label>设备 (Device)</label>
                  <input
                    type="text"
                    placeholder="留空自动选择 (cpu/cuda/0/1/mps...)"
                    value={trainingConfig.device || ''}
                    onChange={(e) => setTrainingConfig({ ...trainingConfig, device: e.target.value || undefined })}
                    disabled={isLoading}
                  />
                </div>

                {/* 学习率参数 */}
                <div className="config-section-divider">
                  <span>学习率参数</span>
                </div>

                <div className="config-item">
                  <label>初始学习率 (lr0)</label>
                  <input
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    max="1"
                    placeholder="留空使用默认值"
                    value={trainingConfig.lr0 ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      setTrainingConfig({ ...trainingConfig, lr0: value === '' ? undefined : parseFloat(value) });
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="config-item">
                  <label>最终学习率 (lrf)</label>
                  <input
                    type="number"
                    step="0.0001"
                    min="0.0001"
                    max="1"
                    placeholder="留空使用默认值"
                    value={trainingConfig.lrf ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      setTrainingConfig({ ...trainingConfig, lrf: value === '' ? undefined : parseFloat(value) });
                    }}
                    disabled={isLoading}
                  />
                </div>

                {/* 优化器参数 */}
                <div className="config-section-divider">
                  <span>优化器参数</span>
                </div>

                <div className="config-item">
                  <label>优化器 (Optimizer)</label>
                  <select
                    value={trainingConfig.optimizer || ''}
                    onChange={(e) => setTrainingConfig({ ...trainingConfig, optimizer: e.target.value || undefined })}
                    disabled={isLoading}
                  >
                    <option value="">使用默认 (auto)</option>
                    <option value="SGD">SGD</option>
                    <option value="Adam">Adam</option>
                    <option value="AdamW">AdamW</option>
                    <option value="RMSProp">RMSProp</option>
                  </select>
                </div>

                <div className="config-item">
                  <label>动量 (Momentum)</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    max="1"
                    placeholder="留空使用默认值"
                    value={trainingConfig.momentum ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      setTrainingConfig({ ...trainingConfig, momentum: value === '' ? undefined : parseFloat(value) });
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="config-item">
                  <label>权重衰减 (Weight Decay)</label>
                  <input
                    type="number"
                    step="0.0001"
                    min="0"
                    max="0.01"
                    placeholder="留空使用默认值"
                    value={trainingConfig.weight_decay ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      setTrainingConfig({ ...trainingConfig, weight_decay: value === '' ? undefined : parseFloat(value) });
                    }}
                    disabled={isLoading}
                  />
                </div>

                {/* 训练控制参数 */}
                <div className="config-section-divider">
                  <span>训练控制</span>
                </div>

                <div className="config-item">
                  <label>早停耐心值 (Patience)</label>
                  <input
                    type="number"
                    min="0"
                    max="1000"
                    placeholder="留空使用默认值 (100)"
                    value={trainingConfig.patience ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      setTrainingConfig({ ...trainingConfig, patience: value === '' ? undefined : parseInt(value, 10) });
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="config-item">
                  <label>数据加载线程数 (Workers)</label>
                  <input
                    type="number"
                    min="0"
                    max="16"
                    placeholder="留空使用默认值"
                    value={trainingConfig.workers ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      setTrainingConfig({ ...trainingConfig, workers: value === '' ? undefined : parseInt(value, 10) });
                    }}
                    disabled={isLoading}
                  />
                </div>

                <div className="config-item checkbox-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={trainingConfig.val !== false}
                      onChange={(e) => setTrainingConfig({ ...trainingConfig, val: e.target.checked || undefined })}
                      disabled={isLoading}
                    />
                    <span>进行验证 (Validation)</span>
                  </label>
                </div>

                <div className="config-item checkbox-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={trainingConfig.amp !== false}
                      onChange={(e) => setTrainingConfig({ ...trainingConfig, amp: e.target.checked || undefined })}
                      disabled={isLoading}
                    />
                    <span>混合精度训练 (AMP)</span>
                  </label>
                </div>

                <div className="config-item">
                  <label>保存周期 (Save Period)</label>
                  <input
                    type="number"
                    min="-1"
                    max="100"
                    placeholder="留空使用默认值 (-1=不保存中间模型)"
                    value={trainingConfig.save_period ?? ''}
                    onChange={(e) => {
                      const value = e.target.value;
                      setTrainingConfig({ ...trainingConfig, save_period: value === '' ? undefined : parseInt(value, 10) });
                    }}
                    disabled={isLoading}
                  />
                </div>

                {/* 高级选项 - 数据增强 */}
                <div className="config-section-divider">
                  <button
                    type="button"
                    className="advanced-toggle"
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    disabled={isLoading}
                  >
                    <span>{showAdvanced ? '▼' : '▶'}</span>
                    <span>高级选项 - 数据增强</span>
                  </button>
                </div>

                {showAdvanced && (
                  <>
                    <div className="config-item">
                      <label>HSV 色调增强 (hsv_h)</label>
                      <input
                        type="number"
                        step="0.001"
                        min="0"
                        max="0.1"
                        placeholder="默认: 0.015"
                        value={trainingConfig.hsv_h ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, hsv_h: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="config-item">
                      <label>HSV 饱和度增强 (hsv_s)</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder="默认: 0.7"
                        value={trainingConfig.hsv_s ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, hsv_s: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="config-item">
                      <label>HSV 明度增强 (hsv_v)</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder="默认: 0.4"
                        value={trainingConfig.hsv_v ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, hsv_v: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="config-item">
                      <label>旋转角度 (degrees)</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="45"
                        placeholder="默认: 0.0"
                        value={trainingConfig.degrees ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, degrees: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="config-item">
                      <label>平移 (translate)</label>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        max="0.5"
                        placeholder="默认: 0.1"
                        value={trainingConfig.translate ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, translate: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="config-item">
                      <label>缩放 (scale)</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder="默认: 0.5"
                        value={trainingConfig.scale ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, scale: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="config-item">
                      <label>剪切 (shear)</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="10"
                        placeholder="默认: 0.0"
                        value={trainingConfig.shear ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, shear: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="config-item">
                      <label>透视变换 (perspective)</label>
                      <input
                        type="number"
                        step="0.001"
                        min="0"
                        max="0.01"
                        placeholder="默认: 0.0"
                        value={trainingConfig.perspective ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, perspective: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="config-item">
                      <label>上下翻转概率 (flipud)</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder="默认: 0.0"
                        value={trainingConfig.flipud ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, flipud: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="config-item">
                      <label>左右翻转概率 (fliplr)</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder="默认: 0.5"
                        value={trainingConfig.fliplr ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, fliplr: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="config-item">
                      <label>Mosaic 增强概率 (mosaic)</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder="默认: 1.0"
                        value={trainingConfig.mosaic ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, mosaic: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>

                    <div className="config-item">
                      <label>Mixup 增强概率 (mixup)</label>
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        placeholder="默认: 0.0"
                        value={trainingConfig.mixup ?? ''}
                        onChange={(e) => {
                          const value = e.target.value;
                          setTrainingConfig({ ...trainingConfig, mixup: value === '' ? undefined : parseFloat(value) });
                        }}
                        disabled={isLoading}
                      />
                    </div>
                  </>
                )}
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
                  {isLoading ? '启动中...' : '开始训练'}
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
                <h3>模型测试</h3>
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
                            <img src={testImage} alt="预览" className="test-preview-image" />
                          ) : (
                            <div className="test-upload-placeholder">
                              <IoImage size={48} />
                              <p>点击或拖拽图片到此处上传</p>
                            </div>
                          )}
                        </div>
                      </label>
                    </div>

                    <div className="config-item">
                      <label>置信度阈值 (conf)</label>
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
                      <label>IoU 阈值 (iou)</label>
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
                            <img src={testResults.annotated_image} alt="检测结果" />
                          </div>
                        )}
                        {testResults.detections && testResults.detections.length > 0 && (
                          <div className="test-detections-list">
                            <div className="config-item">
                              <label>检测详情</label>

                              <div className="detection-list-container">
                                {testResults.detections.map((det: any, index: number) => (
                                  <div key={index} className="detection-item">
                                    <span className="detection-class">{det.class_name}</span>
                                    <span className="detection-confidence">置信度: {(det.confidence * 100).toFixed(2)}%</span>
                                    <span className="detection-bbox">
                                      [{det.bbox.x1.toFixed(0)}, {det.bbox.y1.toFixed(0)}, {det.bbox.x2.toFixed(0)}, {det.bbox.y2.toFixed(0)}]
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          </div>
                        )}
                      </>
                    ) : (
                      <div className="test-results-section placeholder">
                        <h4>检测结果</h4>
                        <div className="test-results-info">
                          <p>请上传图片并点击“开始检测”</p>
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
                  {isTesting ? '检测中...' : '开始检测'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 量化导出弹窗 */}
        {showQuantModal && (
          <div className="config-modal-overlay" onClick={() => !isQuanting && setShowQuantModal(false)}>
            <div className="config-modal quant-modal" onClick={(e) => e.stopPropagation()}>
              <div className="config-modal-header">
                <h3>模型量化导出 (TFLite)</h3>
                <button 
                  className="close-btn" 
                  onClick={() => setShowQuantModal(false)}
                  disabled={isQuanting}
                >
                  <IoClose />
                </button>
              </div>
              
              <div className="config-modal-content">
                <div className="config-item">
                  <label>输入尺寸 (imgsz)</label>
                  <input
                    type="number"
                    min="32"
                    max="2048"
                    step="32"
                    value={quantImgSz}
                    onChange={(e) => setQuantImgSz(Math.max(32, Math.min(2048, parseInt(e.target.value || '0', 10))))}
                    disabled={isQuanting}
                  />
                </div>

                <div className="config-item checkbox-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={quantInt8}
                      onChange={(e) => setQuantInt8(e.target.checked)}
                      disabled={isQuanting}
                    />
                    <span>使用 int8 量化</span>
                  </label>
                </div>

                <div className="config-item checkbox-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={quantNe301}
                      onChange={(e) => setQuantNe301(e.target.checked)}
                      disabled={isQuanting}
                    />
                    <span>量化为 NE301 设备模型</span>
                  </label>
                </div>

                <div className="config-item">
                  <label>校准数据占比 (fraction)</label>
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
                    disabled={isQuanting}
                  />
                </div>

                {quantResult && (
                  <div className="quant-result">
                    <div><strong>导出结果:</strong> {quantResult.message || '成功'}</div>
                    {quantResult.tflite_path && <div>文件: {quantResult.tflite_path}</div>}
                    {quantResult.params && (
                      <div>参数: imgsz={quantResult.params.imgsz}, int8={String(quantResult.params.int8)}, fraction={quantResult.params.fraction}</div>
                    )}
                  </div>
                )}
              </div>

              <div className="config-modal-actions">
                <button
                  className="btn-start-training"
                  onClick={async () => {
                    if (!selectedTrainingId) return;
                    setIsQuanting(true);
                    setQuantResult(null);
                    try {
                      const response = await fetch(
                        `${API_BASE_URL}/projects/${projectId}/train/${selectedTrainingId}/export/tflite?imgsz=${quantImgSz}&int8=${quantInt8}&fraction=${quantFraction}&ne301=${quantNe301}`,
                        { method: 'POST' }
                      );
                      if (!response.ok) {
                        const err = await response.json();
                        throw new Error(err.detail || '量化导出失败');
                      }
                      const data = await response.json();
                      setQuantResult(data);
                      alert('量化导出成功');
                    } catch (error: any) {
                      alert(`量化导出失败: ${error.message}`);
                    } finally {
                      setIsQuanting(false);
                    }
                  }}
                  disabled={isQuanting}
                >
                  {isQuanting ? '量化中...' : '开始量化'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
