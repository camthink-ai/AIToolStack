import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { IoChevronDown, IoChevronUp, IoInformationCircleOutline, IoCheckmarkCircle, IoCloseCircle, IoCopyOutline, IoRefresh } from 'react-icons/io5';
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

  useEffect(() => {
    fetchMQTTStatus();
    // Refresh status every 5 seconds
    const interval = setInterval(fetchMQTTStatus, 5000);
    return () => clearInterval(interval);
  }, []);

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
        className="mqtt-guide-header"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="mqtt-guide-header-left">
          <Icon component={IoInformationCircleOutline} />
          <span className="mqtt-guide-title">{t('mqtt.title')}</span>
          {mqttStatus && (
            <span className={`mqtt-status-badge ${mqttStatus.connected ? 'connected' : 'disconnected'}`}>
              {mqttStatus.connected ? (
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

      {isExpanded && (
        <div className="mqtt-guide-content">
          {mqttStatus && !mqttStatus.enabled ? (
            <div className="mqtt-warning">
              <p>{t('mqtt.serviceDisabled')}</p>
            </div>
          ) : (
            <>
              <div className="mqtt-info-section">
                {mqttStatus?.use_builtin ? (
                  <div className="mqtt-notice success">
                    <Icon component={IoCheckmarkCircle} />
                    <p dangerouslySetInnerHTML={{ __html: t('mqtt.usingBuiltin') }} />
                  </div>
                ) : (
                  <div className="mqtt-notice">
                    <Icon component={IoInformationCircleOutline} />
                    <p dangerouslySetInnerHTML={{ __html: t('mqtt.usingExternal') }} />
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
                {mqttStatus?.broker && (
                  <div className="info-item">
                    <span className="info-label">{t('mqtt.mqttBroker')}</span>
                    <div className="info-value-group">
                      <code className="info-value">{mqttStatus.broker}:{mqttStatus.port}</code>
                      {mqttStatus.broker_type && (
                        <span className={`broker-type-badge ${mqttStatus.broker_type}`}>
                          {mqttStatus.broker_type === 'builtin' ? t('mqtt.builtin') : t('mqtt.external')}
                        </span>
                      )}
                    </div>
                  </div>
                )}
                
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
                              {mqttStatus?.use_builtin ? (
                                <p><strong>{t('mqtt.builtinStartFailed')}</strong></p>
                              ) : (
                                <p><strong>{t('mqtt.externalRequired')}</strong></p>
                              )}
                              <div className="code-snippet">
                                <div className="code-snippet-header">{t('mqtt.macOSHomebrew')}</div>
                                <pre><code>{`${t('mqtt.installMosquitto')}
brew install mosquitto

${t('mqtt.startMosquitto')}
brew services start mosquitto

${t('mqtt.runMosquitto')}
mosquitto -c /opt/homebrew/etc/mosquitto/mosquitto.conf

${t('mqtt.verifyBroker')}
mosquitto_sub -h localhost -t test`}</code></pre>
                              </div>
                              <div className="code-snippet">
                                <div className="code-snippet-header">{t('mqtt.dockerEasiest')}</div>
                                <pre><code>{`${t('mqtt.runMosquittoDocker')}
docker run -it -p 1883:1883 -p 9001:9001 \\
  eclipse-mosquitto

${t('mqtt.runMosquittoDockerBg')}
docker run -d --name mosquitto \\
  -p 1883:1883 -p 9001:9001 \\
  eclipse-mosquitto`}</code></pre>
                              </div>
                              <div className="code-snippet">
                                <div className="code-snippet-header">{t('mqtt.linuxUbuntu')}</div>
                                <pre><code>{`${t('mqtt.install')}
sudo apt-get update
sudo apt-get install mosquitto mosquitto-clients

${t('mqtt.startService')}
sudo systemctl start mosquitto
sudo systemctl enable mosquitto

${t('mqtt.checkStatus')}
sudo systemctl status mosquitto`}</code></pre>
                              </div>
                              <div className="code-snippet">
                                <div className="code-snippet-header">{t('mqtt.verifyConnection')}</div>
                                <pre><code>{`${t('mqtt.subscribeTest')}
mosquitto_sub -h localhost -t test

${t('mqtt.publishTest')}
mosquitto_pub -h localhost -t test -m "Hello MQTT"`}</code></pre>
                              </div>
                            </div>
                          ) : (
                            <ul>
                              <li>{t('mqtt.troubleshooting.ensureRunning')}</li>
                              <li>{t('mqtt.troubleshooting.checkAddress')}</li>
                              <li>{t('mqtt.troubleshooting.checkFirewall', { port: mqttStatus?.port || 1883 })}</li>
                              <li>{t('mqtt.troubleshooting.checkDocker')}</li>
                              <li>{t('mqtt.troubleshooting.checkAuth')}</li>
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
                  <li>{t('mqtt.usage.connect')} <code>{mqttStatus?.broker || mqttStatus?.server_ip || 'localhost'}:{mqttStatus?.port || 1883}</code></li>
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
        </div>
      )}
    </div>
  );
};
