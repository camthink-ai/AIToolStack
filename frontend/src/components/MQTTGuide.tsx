import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { IoChevronDown, IoChevronUp, IoInformationCircleOutline, IoCheckmarkCircle, IoCloseCircle, IoCopyOutline, IoRefresh, IoOpenOutline } from 'react-icons/io5';
import { API_BASE_URL } from '../config';
import './MQTTGuide.css';
import { Button } from '../ui/Button';

// Icon component wrapper
const Icon: React.FC<{ component: React.ComponentType<any> }> = ({ component: Component }) => {
  return <Component />;
};

interface MQTTGuideProps {
  projectId: string;
  projectName: string;
}

interface MQTTStatus {
  enabled: boolean;
  use_builtin?: boolean;
  broker_type?: 'builtin' | 'external';
  connected: boolean;
  broker?: string;
  port?: number;
  topic?: string;
  server_ip?: string;  // Server IP address
  server_port?: number;  // Server port
  builtin?: {
    enabled: boolean;
    host: string | null;
    port: number | null;
    protocol: 'mqtt' | 'mqtts';
    connected: boolean;
  };
  external?: {
    enabled: boolean;
    configured: boolean;
    host: string | null;
    port: number | null;
    protocol: 'mqtt' | 'mqtts';
    connected: boolean;
  };
}

interface TestResult {
  success: boolean;
  message: string;
  detail?: string;
  error_code?: number;
}

export const MQTTGuide: React.FC<MQTTGuideProps> = ({ projectId, projectName }) => {
  const { t } = useTranslation();
  const [isExpanded, setIsExpanded] = useState(false);
  const [mqttStatus, setMqttStatus] = useState<MQTTStatus | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const guideHeaderRef = useRef<HTMLDivElement>(null);
  const [contentPosition, setContentPosition] = useState<{ top: number; right: number } | null>(null);

  useEffect(() => {
    fetchMQTTStatus();
    // Refresh status every 5 seconds
    const interval = setInterval(fetchMQTTStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  // Calculate content position when expanded
  useEffect(() => {
    const updateContentPosition = () => {
      if (guideHeaderRef.current && isExpanded) {
        const rect = guideHeaderRef.current.getBoundingClientRect();
        setContentPosition({
          top: rect.bottom + 4, // 4px spacing
          right: window.innerWidth - rect.right,
        });
      } else {
        setContentPosition(null);
      }
    };

    if (isExpanded) {
      updateContentPosition();
      window.addEventListener('resize', updateContentPosition);
      window.addEventListener('scroll', updateContentPosition, true);
    }

    return () => {
      window.removeEventListener('resize', updateContentPosition);
      window.removeEventListener('scroll', updateContentPosition, true);
    };
  }, [isExpanded]);

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(id);
      setTimeout(() => setCopied(null), 2000);
    });
  };

  const testMQTTConnection = async () => {
    setIsTesting(true);
    setTestResult(null);
    
    try {
      const response = await fetch(`${API_BASE_URL}/mqtt/test`, {
        method: 'POST',
      });
      
      const data = await response.json();
      setTestResult({
        success: data.success,
        message: data.message,
        detail: data.detail,
        error_code: data.error_code
      });
      
      // Refresh status after test
      setTimeout(() => {
        fetchMQTTStatus();
      }, 1000);
    } catch (error: any) {
      setTestResult({
        success: false,
        message: t('mqtt.testFailed', { error: error.message })
      });
    } finally {
      setIsTesting(false);
    }
  };

  const fetchMQTTStatus = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/mqtt/status`);
      if (response.ok) {
        const data = await response.json();
        setMqttStatus(data);
      }
    } catch (error) {
      console.error('Failed to fetch MQTT status:', error);
    }
  };

  const mqttTopic = `annotator/upload/${projectId}`;
  const examplePayload = JSON.stringify({
    req_id: "550e8400-e29b-41d4-a716-446655440000",
    device_id: "camera_01",
    timestamp: Math.floor(Date.now() / 1000),
    image: {
      filename: "image_001.jpg",
      format: "jpg",
      encoding: "base64",
      data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    },
    metadata: {
      trigger_source: "sensor_A",
      location: "factory_floor_1"
    }
  }, null, 2);

  return (
    <div className="mqtt-guide">
      <div
        ref={guideHeaderRef}
        className="mqtt-guide-header"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="mqtt-guide-header-left">
          <Icon component={IoInformationCircleOutline} />
          <span className="mqtt-guide-title">{t('mqtt.title')}</span>
          {mqttStatus && (
            <span className={`mqtt-status-badge ${(mqttStatus.builtin?.connected || mqttStatus.external?.connected) ? 'connected' : 'disconnected'}`}>
              {(mqttStatus.builtin?.connected || mqttStatus.external?.connected) ? (
                <>
                  <Icon component={IoCheckmarkCircle} />
                  {t('mqtt.connected')}
                </>
              ) : mqttStatus.enabled ? (
                <>
                  <Icon component={IoCloseCircle} />
                  {t('mqtt.disconnected')}
                </>
              ) : (
                <>
                  <Icon component={IoCloseCircle} />
                  {t('mqtt.disconnected')}
                </>
              )}
            </span>
          )}
        </div>
        <Icon component={isExpanded ? IoChevronUp : IoChevronDown} />
      </div>

      {isExpanded && contentPosition && createPortal(
        <div 
          className="mqtt-guide-content"
          style={{
            top: `${contentPosition.top}px`,
            right: `${contentPosition.right}px`,
          }}
        >
          {mqttStatus && !mqttStatus.enabled ? (
            <div className="mqtt-warning">
              <p>{t('mqtt.serviceDisabled')}</p>
            </div>
          ) : (
            <>
              <div className="mqtt-info-section">
                {mqttStatus?.builtin?.enabled && mqttStatus?.builtin?.connected ? (
                  <div className="mqtt-notice success">
                    <Icon component={IoCheckmarkCircle} />
                    <p dangerouslySetInnerHTML={{ __html: t('mqtt.usingBuiltin') }} />
                  </div>
                ) : mqttStatus?.external?.enabled && mqttStatus?.external?.connected ? (
                  <div className="mqtt-notice">
                    <Icon component={IoInformationCircleOutline} />
                    <p dangerouslySetInnerHTML={{ __html: t('mqtt.usingExternal') }} />
                  </div>
                ) : (
                  <div className="mqtt-notice warning">
                    <Icon component={IoInformationCircleOutline} />
                    <p dangerouslySetInnerHTML={{ __html: t('mqtt.brokerNotConfigured') }} />
                  </div>
                )}
                <h4>{t('mqtt.projectInfo')}</h4>
                <div className="info-item">
                  <span className="info-label">{t('mqtt.serverAddress')}</span>
                  <div className="info-value-group">
                    <code className="info-value">
                      {mqttStatus?.server_ip || mqttStatus?.broker || t('mqtt.getting')}
                      {mqttStatus?.server_port ? `:${mqttStatus.server_port}` : (mqttStatus?.port ? `:${mqttStatus.port}` : ':8000')}
                    </code>
                    {(mqttStatus?.server_ip || mqttStatus?.broker) && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                        className="btn-copy"
                        onClick={() => copyToClipboard(
                          `${mqttStatus?.server_ip || mqttStatus?.broker || ''}${mqttStatus?.server_port ? `:${mqttStatus.server_port}` : (mqttStatus?.port ? `:${mqttStatus.port}` : ':8000')}`,
                          'serverIp'
                        )}
                        title={t('mqtt.copy')}
                      >
                        <Icon component={IoCopyOutline} />
                        {copied === 'serverIp' && <span className="copied-tooltip">{t('mqtt.copied')}</span>}
                    </Button>
                    )}
                  </div>
                </div>
                <div className="info-item">
                  <span className="info-label">{t('mqtt.projectId')}</span>
                  <div className="info-value-group">
                    <code className="info-value">{projectId}</code>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="btn-copy"
                      onClick={() => copyToClipboard(projectId, 'projectId')}
                      title={t('mqtt.copy')}
                    >
                      <Icon component={IoCopyOutline} />
                      {copied === 'projectId' && <span className="copied-tooltip">{t('mqtt.copied')}</span>}
                    </Button>
                  </div>
                </div>
                <div className="info-item">
                  <span className="info-label">{t('mqtt.mqttTopic')}</span>
                  <div className="info-value-group">
                    <code className="info-value">{mqttTopic}</code>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="btn-copy"
                      onClick={() => copyToClipboard(mqttTopic, 'topic')}
                      title={t('mqtt.copy')}
                    >
                      <Icon component={IoCopyOutline} />
                      {copied === 'topic' && <span className="copied-tooltip">{t('mqtt.copied')}</span>}
                    </Button>
                  </div>
                </div>
                {(mqttStatus?.builtin?.enabled || mqttStatus?.external?.enabled) && (
                  <div className="info-item">
                    <span className="info-label">{t('mqtt.mqttBroker')}</span>
                    <div className="info-value-group">
                      {mqttStatus?.builtin?.enabled && (
                        <div style={{ marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                          <code className="info-value" style={{ flex: '0 1 auto', wordBreak: 'break-all' }}>
                            {mqttStatus.builtin.protocol}://{mqttStatus.builtin.host || mqttStatus?.server_ip || 'localhost'}:{mqttStatus.builtin.port || 1883}
                          </code>
                          {mqttStatus.builtin.connected && (
                            <span className="mqtt-status-badge connected" style={{ flexShrink: 0 }}>
                              {t('mqtt.connected')}
                            </span>
                          )}
                        </div>
                      )}
                      {mqttStatus?.external?.enabled && mqttStatus?.external?.configured && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                          <code className="info-value" style={{ flex: '0 1 auto', wordBreak: 'break-all' }}>
                            {mqttStatus.external.protocol}://{mqttStatus.external.host || mqttStatus?.server_ip || 'localhost'}:{mqttStatus.external.port || 1883}
                          </code>
                          <span className={`broker-type-badge external`} style={{ flexShrink: 0 }}>
                            {t('mqtt.external')}
                          </span>
                          {mqttStatus.external.connected && (
                            <span className="mqtt-status-badge connected" style={{ flexShrink: 0 }}>
                              {t('mqtt.connected')}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
                <div className="info-item" style={{ marginTop: '16px', paddingTop: '16px', borderTop: '1px solid var(--border-color)' }}>
                  <div className="mqtt-notice info" style={{ marginBottom: '8px', display: 'flex', alignItems: 'flex-start', gap: '8px' }}>
                    <Icon component={IoInformationCircleOutline} />
                    <div style={{ flex: 1 }}>
                      <p dangerouslySetInnerHTML={{ __html: t('mqtt.configHint') }} style={{ margin: 0, marginBottom: '8px' }} />
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => {
                          // Dispatch custom event to navigate to settings
                          window.dispatchEvent(new CustomEvent('navigate-to-settings'));
                        }}
                        style={{ marginTop: '4px' }}
                      >
                        <Icon component={IoOpenOutline} />
                        {t('mqtt.openSystemSettings')}
                      </Button>
                    </div>
                  </div>
                </div>
                <div className="info-item">
                  <div className="mqtt-notice info" style={{ marginBottom: '8px' }}>
                    <Icon component={IoInformationCircleOutline} />
                    <p dangerouslySetInnerHTML={{ __html: t('mqtt.deviceAccessHint', { server: `${mqttStatus?.server_ip || 'localhost'}:${mqttStatus?.server_port || 8000}` }) }} />
                  </div>
                </div>
                
                <div className="mqtt-test-section">
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    className="btn-test-mqtt"
                    onClick={testMQTTConnection}
                    disabled={isTesting || !mqttStatus?.enabled}
                  >
                    <Icon component={IoRefresh} />
                    {isTesting ? t('mqtt.testing') : t('mqtt.testConnection')}
                  </Button>
                  {testResult && (
                    <div className={`test-result ${testResult.success ? 'success' : 'error'}`}>
                      <div className="test-result-message">{testResult.message}</div>
                      {testResult.detail && (
                        <div className="test-result-detail">{testResult.detail}</div>
                      )}
                      {!testResult.success && (
                        <div className="test-result-suggestions">
                          <strong>{t('mqtt.solution')}</strong>
                          {testResult.error_code === 61 || testResult.error_code === 111 ? (
                            <div className="suggestion-steps">
                              <p><strong>{t('mqtt.brokerConnectionFailed')}</strong></p>
                              <p>{t('mqtt.configInSystemSettings')}</p>
                              {mqttStatus?.builtin?.enabled ? (
                                <div className="code-snippet">
                                  <div className="code-snippet-header">{t('mqtt.builtinBrokerTroubleshooting')}</div>
                                  <ul style={{ margin: '8px 0', paddingLeft: '20px' }}>
                                    <li>{t('mqtt.troubleshooting.checkBuiltinEnabled')}</li>
                                    <li>{t('mqtt.troubleshooting.checkBuiltinPort', { port: mqttStatus?.builtin?.port || 1883 })}</li>
                                    <li>{t('mqtt.troubleshooting.checkBuiltinTLS')}</li>
                                    <li>{t('mqtt.troubleshooting.checkBuiltinAuth')}</li>
                                  </ul>
                                </div>
                              ) : (
                                <div className="code-snippet">
                                  <div className="code-snippet-header">{t('mqtt.externalBrokerTroubleshooting')}</div>
                                  <ul style={{ margin: '8px 0', paddingLeft: '20px' }}>
                                    <li>{t('mqtt.troubleshooting.checkExternalConfigured')}</li>
                                    <li>{t('mqtt.troubleshooting.checkExternalAddress')}</li>
                                    <li>{t('mqtt.troubleshooting.checkExternalPort', { port: mqttStatus?.external?.port || 1883 })}</li>
                                    <li>{t('mqtt.troubleshooting.checkExternalAuth')}</li>
                                    <li>{t('mqtt.troubleshooting.checkExternalTLS')}</li>
                                  </ul>
                                </div>
                              )}
                            </div>
                          ) : (
                            <ul>
                              <li>{t('mqtt.troubleshooting.ensureRunning')}</li>
                              <li>{t('mqtt.troubleshooting.checkSystemSettings')}</li>
                              <li>{t('mqtt.troubleshooting.checkAddress')}</li>
                              <li>{t('mqtt.troubleshooting.checkFirewall', { port: mqttStatus?.builtin?.port || mqttStatus?.external?.port || 1883 })}</li>
                              <li>{t('mqtt.troubleshooting.checkAuth')}</li>
                              <li>{t('mqtt.troubleshooting.checkTLS')}</li>
                            </ul>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>

              <div className="mqtt-usage-section">
                <h4>{t('mqtt.usage.title')}</h4>
                <ol className="usage-steps">
                  <li>{t('mqtt.usage.getBootstrap')} <code>{mqttStatus?.server_ip || 'localhost'}:{mqttStatus?.server_port || 8000}/api/device/bootstrap</code></li>
                  <li>{t('mqtt.usage.connect')} <code>{mqttStatus?.builtin?.enabled ? `${mqttStatus.builtin.protocol}://${mqttStatus.builtin.host || mqttStatus?.server_ip || 'localhost'}:${mqttStatus.builtin.port || 1883}` : (mqttStatus?.external?.enabled ? `${mqttStatus.external.protocol}://${mqttStatus.external.host || mqttStatus?.server_ip || 'localhost'}:${mqttStatus.external.port || 1883}` : `${mqttStatus?.server_ip || 'localhost'}:1883`)}</code></li>
                  <li>{t('mqtt.usage.publish')} <code>{mqttTopic}</code></li>
                  <li>{t('mqtt.usage.format')}</li>
                  <li>{t('mqtt.usage.autoSave')}</li>
                </ol>
              </div>

              <div className="mqtt-example-section">
                <h4>{t('mqtt.example.title')}</h4>
                <div className="code-block-container">
                  <div className="code-block-header">
                    <span>{t('mqtt.example.jsonFormat')}</span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="btn-copy"
                      onClick={() => copyToClipboard(examplePayload, 'example')}
                      title={t('mqtt.copyCode')}
                    >
                      <Icon component={IoCopyOutline} />
                      {copied === 'example' && <span className="copied-tooltip">{t('mqtt.copied')}</span>}
                    </Button>
                  </div>
                  <pre className="code-block">
                    <code>{examplePayload}</code>
                  </pre>
                </div>
              </div>

              <div className="mqtt-fields-section">
                <h4>{t('mqtt.fields.title')}</h4>
                <table className="fields-table">
                  <thead>
                    <tr>
                      <th>{t('mqtt.fields.field')}</th>
                      <th>{t('mqtt.fields.type')}</th>
                      <th>{t('mqtt.fields.description')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td><code>req_id</code></td>
                      <td>string</td>
                      <td>{t('mqtt.fields.reqId')}</td>
                    </tr>
                    <tr>
                      <td><code>device_id</code></td>
                      <td>string</td>
                      <td>{t('mqtt.fields.deviceId')}</td>
                    </tr>
                    <tr>
                      <td><code>timestamp</code></td>
                      <td>number</td>
                      <td>{t('mqtt.fields.timestamp')}</td>
                    </tr>
                    <tr>
                      <td><code>image.filename</code></td>
                      <td>string</td>
                      <td>{t('mqtt.fields.filename')}</td>
                    </tr>
                    <tr>
                      <td><code>image.format</code></td>
                      <td>string</td>
                      <td>{t('mqtt.fields.format')}</td>
                    </tr>
                    <tr>
                      <td><code>image.encoding</code></td>
                      <td>string</td>
                      <td>{t('mqtt.fields.encoding')}</td>
                    </tr>
                    <tr>
                      <td><code>image.data</code></td>
                      <td>string</td>
                      <td>{t('mqtt.fields.data')}</td>
                    </tr>
                    <tr>
                      <td><code>metadata</code></td>
                      <td>object</td>
                      <td>{t('mqtt.fields.metadata')}</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div className="mqtt-response-section">
                <h4>{t('mqtt.response.title')}</h4>
                <p>{t('mqtt.response.description')} <code>annotator/response/{'{device_id}'}</code> {t('mqtt.response.returnResponse')}</p>
                <ul className="response-list">
                  <li><strong>{t('mqtt.response.success')}</strong> <code>{"{status: 'success', code: 200, message: '...'}"}</code></li>
                  <li><strong>{t('mqtt.response.error')}</strong> <code>{"{status: 'error', code: 400, message: '...'}"}</code></li>
                </ul>
              </div>
            </>
          )}
        </div>,
        document.body
      )}
    </div>
  );
};
