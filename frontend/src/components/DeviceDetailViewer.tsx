import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '../ui/Button';
import { IoClose } from 'react-icons/io5';
import { API_BASE_URL } from '../config';
import './SystemSettings.css';
import './TrainingPanel.css';

interface DeviceDetailViewerProps {
  device: {
    id: string;
    name: string | null;
    type: string | null;
  } | null;
  isOpen: boolean;
  onClose: () => void;
}

interface DeviceReport {
  id: number;
  device_id: string;
  report_data: string;
  created_at: string;
}

interface ImageData {
  src: string;
  format?: string;
  size?: number;
  error?: boolean;
}

// Recursively extract image data from any JSON structure
const extractImages = (obj: any, path: string = ''): ImageData[] => {
  const images: ImageData[] = [];
  
  if (!obj || typeof obj !== 'object') {
    return images;
  }
  
  // Check common image field names
  const imageFields = ['image', 'image_data', 'imageData', 'photo', 'picture', 'img'];
  
  for (const [key, value] of Object.entries(obj)) {
    const currentPath = path ? `${path}.${key}` : key;
    
    if (imageFields.includes(key.toLowerCase()) && typeof value === 'string') {
      // Try to parse base64 image
      try {
        let imageSrc = value;
        // Handle data:image/jpeg;base64,... format
        if (value.startsWith('data:image')) {
          imageSrc = value;
        } else if (value.length > 100) {
          // Assume it's base64 encoded
          imageSrc = `data:image/jpeg;base64,${value}`;
        }
        images.push({
          src: imageSrc,
          format: value.match(/data:image\/(\w+)/)?.[1] || 'jpeg',
        });
      } catch (e) {
        // Ignore parse errors
      }
    } else if (typeof value === 'object' && value !== null) {
      // Recursively search in nested objects
      images.push(...extractImages(value, currentPath));
    }
  }
  
  return images;
};

// Generic JSON value renderer
const renderJsonValue = (value: any, depth: number = 0): React.ReactNode => {
  if (value === null || value === undefined) {
    return <span style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}>null</span>;
  }
  
  if (typeof value === 'boolean') {
    return <span style={{ color: value ? '#4caf50' : '#f44336' }}>{String(value)}</span>;
  }
  
  if (typeof value === 'number') {
    return <span style={{ color: '#2196f3' }}>{value}</span>;
  }
  
  if (typeof value === 'string') {
    // Check if it looks like a timestamp
    if (/^\d{13}$/.test(value) || /^\d{10}$/.test(value)) {
      try {
        const date = new Date(parseInt(value));
        return (
          <span>
            <span style={{ color: 'var(--text-secondary)' }}>{value}</span>
            {' '}
            <span style={{ color: 'var(--text-primary)', fontSize: '12px' }}>
              ({date.toLocaleString()})
            </span>
          </span>
        );
      } catch (e) {
        // Not a valid timestamp
      }
    }
    return <span style={{ wordBreak: 'break-word' }}>"{value}"</span>;
  }
  
  if (Array.isArray(value)) {
    if (depth > 3) {
      return <span style={{ color: 'var(--text-secondary)' }}>[Array({value.length})]</span>;
    }
    return (
      <ul style={{ margin: '4px 0', paddingLeft: '20px', listStyle: 'none' }}>
        {value.slice(0, 10).map((item, idx) => (
          <li key={idx} style={{ margin: '2px 0' }}>
            {renderJsonValue(item, depth + 1)}
          </li>
        ))}
        {value.length > 10 && (
          <li style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}>
            ... and {value.length - 10} more
          </li>
        )}
      </ul>
    );
  }
  
  if (typeof value === 'object') {
    if (depth > 3) {
      return <span style={{ color: 'var(--text-secondary)' }}>[Object]</span>;
    }
    return (
      <div style={{ marginLeft: '16px', borderLeft: '2px solid var(--border-color)', paddingLeft: '8px' }}>
        {Object.entries(value).map(([k, v]) => (
          <div key={k} style={{ margin: '4px 0' }}>
            <strong style={{ color: 'var(--primary-color)' }}>{k}:</strong>{' '}
            {renderJsonValue(v, depth + 1)}
          </div>
        ))}
      </div>
    );
  }
  
  return <span>{String(value)}</span>;
};

// Flatten JSON object for table display
const flattenJson = (obj: any, prefix: string = '', result: Record<string, any> = {}): Record<string, any> => {
  if (obj === null || obj === undefined) {
    result[prefix] = null;
    return result;
  }
  
  if (typeof obj !== 'object') {
    result[prefix] = obj;
    return result;
  }
  
  if (Array.isArray(obj)) {
    result[prefix] = obj;
    return result;
  }
  
  for (const [key, value] of Object.entries(obj)) {
    const newKey = prefix ? `${prefix}.${key}` : key;
    
    if (value === null || value === undefined) {
      result[newKey] = null;
    } else if (typeof value === 'object' && !Array.isArray(value)) {
      // Recursively flatten nested objects
      flattenJson(value, newKey, result);
    } else {
      result[newKey] = value;
    }
  }
  
  return result;
};

export const DeviceDetailViewer: React.FC<DeviceDetailViewerProps> = ({
  device,
  isOpen,
  onClose,
}) => {
  const { t } = useTranslation();
  const [reports, setReports] = useState<DeviceReport[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedReport, setSelectedReport] = useState<DeviceReport | null>(null);

  const loadReports = useCallback(async () => {
    if (!device) return;
    
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE_URL}/devices/${encodeURIComponent(device.id)}/reports?limit=100`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setReports(data as DeviceReport[]);
      // Auto-select the most recent report
      if (data.length > 0) {
        setSelectedReport(data[0]);
      }
    } catch (e) {
      console.error('Failed to load device reports:', e);
    } finally {
      setLoading(false);
    }
  }, [device]);

  useEffect(() => {
    if (isOpen && device) {
      void loadReports();
    } else {
      setReports([]);
      setSelectedReport(null);
    }
  }, [isOpen, device, loadReports]);

  const parsedData = useMemo(() => {
    if (!selectedReport) return null;
    
    try {
      return JSON.parse(selectedReport.report_data);
    } catch (e) {
      console.error('Failed to parse report data:', e);
      return null;
    }
  }, [selectedReport]);

  const flattenedData = useMemo(() => {
    if (!parsedData) return {};
    return flattenJson(parsedData);
  }, [parsedData]);

  const images = useMemo(() => {
    if (!parsedData) return [];
    return extractImages(parsedData);
  }, [parsedData]);

  const formatDate = useCallback((dateString: string) => {
    try {
      const date = new Date(dateString);
      return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    } catch {
      return dateString;
    }
  }, []);


  if (!isOpen || !device) {
    return null;
  }

  return (
    <div className="training-panel-overlay">
      <div className="training-panel-fullscreen">
        <div className="training-panel-header">
          <h2>
            {t('device.detail.title', '设备详情')} - {device.name || device.id}
          </h2>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="close-btn"
            onClick={onClose}
          >
            <IoClose />
          </Button>
        </div>

        <div className="training-panel-body">
            {/* Left sidebar: Report history list */}
            <div className="training-panel-left">
              <div className="training-records-section">
                <div className="records-header" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '4px' }}>
                  <div style={{ fontWeight: 600, fontSize: '14px', color: 'var(--text-primary)' }}>
                    {t('device.detail.history', '上报历史')}
                  </div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    {reports.length} {t('device.detail.records', '条记录')}
                  </div>
                </div>
                <div className="training-records-list">
                  {loading ? (
                    <div className="training-empty">
                      <p className="training-empty-desc">{t('common.loading', '加载中...')}</p>
                    </div>
                  ) : reports.length === 0 ? (
                    <div className="training-empty">
                      <p className="training-empty-desc">
                        {t('device.detail.noHistory', '暂无历史记录')}
                      </p>
                    </div>
                  ) : (
                    reports.map((report) => (
                      <div
                        key={report.id}
                        className={`training-record-item ${selectedReport?.id === report.id ? 'active' : ''}`}
                        onClick={() => setSelectedReport(report)}
                      >
                        <div className="record-header">
                          <span className="record-time">#{report.id}</span>
                        </div>
                        <div className="record-info">
                          <span>{formatDate(report.created_at)}</span>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>

            {/* Right content: Report details */}
            <div className="training-panel-right" style={{ padding: 'var(--spacing-xl)', overflowY: 'auto' }}>
              {!selectedReport ? (
                <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-secondary)' }}>
                  {t('device.detail.selectReport', '请选择一个上报记录')}
                </div>
              ) : !parsedData ? (
                <div style={{ padding: '40px', textAlign: 'center', color: 'var(--error-color)' }}>
                  {t('device.detail.parseError', '数据解析失败')}
                </div>
              ) : (
                <div className="ui-form-stack">
                  {/* Images section */}
                  {images.length > 0 && (
                    <div style={{ marginBottom: 'var(--spacing-2xl)' }}>
                      <h4 style={{
                        margin: '0 0 var(--spacing-md) 0',
                        fontSize: 'var(--font-size-lg)',
                        fontWeight: 600,
                        color: 'var(--text-primary)',
                        borderBottom: '1px solid var(--border-color)',
                        paddingBottom: 'var(--spacing-sm)'
                      }}>
                        {t('device.detail.images', '图片')} ({images.length})
                      </h4>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px' }}>
                        {images.map((img, idx) => (
                          <div key={idx} style={{
                            border: '1px solid var(--border-color)',
                            borderRadius: 'var(--radius-sm)',
                            overflow: 'hidden',
                            backgroundColor: 'var(--bg-secondary)',
                            width: '100%',
                            maxWidth: '100%',
                          }}>
                            <div
                              style={{
                                position: 'relative',
                                width: '100%',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                backgroundColor: '#000',
                                padding: '12px',
                              }}
                            >
                              {img.error ? (
                                <div style={{ color: 'var(--error-color)', padding: '20px', textAlign: 'center' }}>
                                  {t('device.detail.imageError', '图片加载失败')}
                                </div>
                              ) : (
                                <img
                                  src={img.src}
                                  alt={`Image ${idx + 1}`}
                                  style={{
                                    maxWidth: '100%',
                                    maxHeight: '350px',
                                    objectFit: 'contain',
                                  }}
                                  onError={(e) => {
                                    img.error = true;
                                    e.currentTarget.style.display = 'none';
                                  }}
                                  onLoad={() => {
                                    img.error = false;
                                  }}
                                />
                              )}
                            </div>
                            <div style={{ padding: '8px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                              {t('device.detail.image', '图片')} {idx + 1}
                              {img.format && ` (${img.format})`}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Data fields section */}
                  <div style={{ marginBottom: 'var(--spacing-2xl)' }}>
                    <h4 style={{
                      margin: '0 0 var(--spacing-md) 0',
                      fontSize: 'var(--font-size-lg)',
                      fontWeight: 600,
                      color: 'var(--text-primary)',
                      borderBottom: '1px solid var(--border-color)',
                      paddingBottom: 'var(--spacing-sm)'
                    }}>
                      {t('device.detail.dataFields', '数据字段')}
                    </h4>
                    <div style={{
                      border: '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-md)',
                      overflow: 'hidden',
                      backgroundColor: 'var(--bg-primary)',
                    }}>
                      <table className="settings-table" style={{ width: '100%', margin: 0 }}>
                        <tbody>
                          {Object.entries(flattenedData).map(([key, value]) => {
                            // Skip image fields as they're shown separately
                            if (['image', 'image_data', 'imageData', 'photo', 'picture', 'img'].some(f => key.toLowerCase().includes(f.toLowerCase()))) {
                              return null;
                            }
                            return (
                              <tr key={key}>
                                <td style={{
                                  width: '30%',
                                  padding: '10px 12px',
                                  fontWeight: 500,
                                  color: 'var(--text-primary)',
                                  verticalAlign: 'top',
                                  borderBottom: '1px solid var(--border-color)',
                                  backgroundColor: 'var(--bg-secondary)',
                                }}>
                                  {key}
                                </td>
                                <td style={{
                                  padding: '10px 12px',
                                  color: 'var(--text-secondary)',
                                  borderBottom: '1px solid var(--border-color)',
                                  wordBreak: 'break-word',
                                }}>
                                  {renderJsonValue(value)}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* Raw JSON section */}
                  <div style={{ marginTop: 'var(--spacing-2xl)' }}>
                    <h4 style={{
                      margin: '0 0 var(--spacing-md) 0',
                      fontSize: 'var(--font-size-lg)',
                      fontWeight: 600,
                      color: 'var(--text-primary)',
                      borderBottom: '1px solid var(--border-color)',
                      paddingBottom: 'var(--spacing-sm)'
                    }}>
                      {t('device.detail.rawJson', '原始JSON数据')}
                    </h4>
                    <pre style={{
                      padding: 'var(--spacing-md)',
                      background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-md)',
                      fontSize: 'var(--font-size-xs)',
                      overflowX: 'auto',
                      maxHeight: '400px',
                      overflowY: 'auto',
                      fontFamily: 'var(--font-family-mono)',
                      lineHeight: '1.5',
                      margin: 0,
                    }}>
                      {JSON.stringify(parsedData, null, 2)}
                    </pre>
                  </div>
                </div>
              )}
            </div>
        </div>
      </div>
    </div>
  );
};
