import React, { useState, useEffect } from 'react';
import { IoChevronDown, IoChevronUp, IoInformationCircleOutline, IoCheckmarkCircle, IoCloseCircle, IoCopyOutline, IoRefresh } from 'react-icons/io5';
import { API_BASE_URL } from '../config';
import './MQTTGuide.css';

// 图标组件包装器
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
  server_ip?: string;  // 服务器 IP 地址
  server_port?: number;  // 服务器端口
}

interface TestResult {
  success: boolean;
  message: string;
  detail?: string;
  error_code?: number;
}

export const MQTTGuide: React.FC<MQTTGuideProps> = ({ projectId, projectName }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [mqttStatus, setMqttStatus] = useState<MQTTStatus | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  useEffect(() => {
    fetchMQTTStatus();
    // 每 5 秒刷新一次状态
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
      
      // 测试后刷新状态
      setTimeout(() => {
        fetchMQTTStatus();
      }, 1000);
    } catch (error: any) {
      setTestResult({
        success: false,
        message: `测试失败: ${error.message}`
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
          <span className="mqtt-guide-title">MQTT</span>
          {mqttStatus && (
            <span className={`mqtt-status-badge ${mqttStatus.connected ? 'connected' : 'disconnected'}`}>
              {mqttStatus.connected ? (
                <Icon component={IoCheckmarkCircle} />
              ) : mqttStatus.enabled ? (
                <Icon component={IoCloseCircle} />
              ) : (
                <Icon component={IoCloseCircle} />
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
              <p>MQTT 服务已禁用。请在服务器配置中启用 MQTT 服务。</p>
            </div>
          ) : (
            <>
              <div className="mqtt-info-section">
                {mqttStatus?.use_builtin ? (
                  <div className="mqtt-notice success">
                    <Icon component={IoCheckmarkCircle} />
                    <p>正在使用<strong>内置 MQTT Broker</strong>，无需额外配置。您也可以切换到外部 Broker。</p>
                  </div>
                ) : (
                  <div className="mqtt-notice">
                    <Icon component={IoInformationCircleOutline} />
                    <p>当前使用<strong>外部 MQTT Broker</strong>。如需使用内置 Broker，请在服务器配置中启用。</p>
                  </div>
                )}
                <h4>项目信息</h4>
                <div className="info-item">
                  <span className="info-label">服务器地址:</span>
                  <div className="info-value-group">
                    <code className="info-value">
                      {mqttStatus?.server_ip || mqttStatus?.broker || '正在获取...'}
                      {mqttStatus?.server_port ? `:${mqttStatus.server_port}` : (mqttStatus?.port ? `:${mqttStatus.port}` : ':8000')}
                    </code>
                    {(mqttStatus?.server_ip || mqttStatus?.broker) && (
                      <button
                        className="btn-copy"
                        onClick={() => copyToClipboard(
                          `${mqttStatus?.server_ip || mqttStatus?.broker || ''}${mqttStatus?.server_port ? `:${mqttStatus.server_port}` : (mqttStatus?.port ? `:${mqttStatus.port}` : ':8000')}`,
                          'serverIp'
                        )}
                        title="复制"
                      >
                        <Icon component={IoCopyOutline} />
                        {copied === 'serverIp' && <span className="copied-tooltip">已复制</span>}
                      </button>
                    )}
                  </div>
                </div>
                <div className="info-item">
                  <span className="info-label">项目 ID:</span>
                  <div className="info-value-group">
                    <code className="info-value">{projectId}</code>
                    <button
                      className="btn-copy"
                      onClick={() => copyToClipboard(projectId, 'projectId')}
                      title="复制"
                    >
                      <Icon component={IoCopyOutline} />
                      {copied === 'projectId' && <span className="copied-tooltip">已复制</span>}
                    </button>
                  </div>
                </div>
                <div className="info-item">
                  <span className="info-label">MQTT Topic:</span>
                  <div className="info-value-group">
                    <code className="info-value">{mqttTopic}</code>
                    <button
                      className="btn-copy"
                      onClick={() => copyToClipboard(mqttTopic, 'topic')}
                      title="复制"
                    >
                      <Icon component={IoCopyOutline} />
                      {copied === 'topic' && <span className="copied-tooltip">已复制</span>}
                    </button>
                  </div>
                </div>
                {mqttStatus?.broker && (
                  <div className="info-item">
                    <span className="info-label">MQTT Broker:</span>
                    <div className="info-value-group">
                      <code className="info-value">{mqttStatus.broker}:{mqttStatus.port}</code>
                      {mqttStatus.broker_type && (
                        <span className={`broker-type-badge ${mqttStatus.broker_type}`}>
                          {mqttStatus.broker_type === 'builtin' ? '内置' : '外部'}
                        </span>
                      )}
                    </div>
                  </div>
                )}
                
                <div className="mqtt-test-section">
                  <button
                    className="btn-test-mqtt"
                    onClick={testMQTTConnection}
                    disabled={isTesting || !mqttStatus?.enabled}
                  >
                    <Icon component={IoRefresh} />
                    {isTesting ? '测试中...' : '测试连接'}
                  </button>
                  {testResult && (
                    <div className={`test-result ${testResult.success ? 'success' : 'error'}`}>
                      <div className="test-result-message">{testResult.message}</div>
                      {testResult.detail && (
                        <div className="test-result-detail">{testResult.detail}</div>
                      )}
                      {!testResult.success && (
                        <div className="test-result-suggestions">
                          <strong>解决方案：</strong>
                          {testResult.error_code === 61 || testResult.error_code === 111 ? (
                            <div className="suggestion-steps">
                              {mqttStatus?.use_builtin ? (
                                <p><strong>内置 MQTT Broker 启动失败，请检查：</strong></p>
                              ) : (
                                <p><strong>本系统需要外部 MQTT Broker，请安装并启动：</strong></p>
                              )}
                              <div className="code-snippet">
                                <div className="code-snippet-header">macOS (使用 Homebrew) - 推荐</div>
                                <pre><code>{`# 安装 Mosquitto
brew install mosquitto

# 启动 Mosquitto（后台运行）
brew services start mosquitto

# 或前台运行（用于调试）
mosquitto -c /opt/homebrew/etc/mosquitto/mosquitto.conf

# 验证 Broker 是否运行
mosquitto_sub -h localhost -t test`}</code></pre>
                              </div>
                              <div className="code-snippet">
                                <div className="code-snippet-header">使用 Docker - 最简单</div>
                                <pre><code>{`# 运行 Mosquitto（前台运行，按 Ctrl+C 停止）
docker run -it -p 1883:1883 -p 9001:9001 \\
  eclipse-mosquitto

# 或后台运行
docker run -d --name mosquitto \\
  -p 1883:1883 -p 9001:9001 \\
  eclipse-mosquitto`}</code></pre>
                              </div>
                              <div className="code-snippet">
                                <div className="code-snippet-header">Linux (Ubuntu/Debian)</div>
                                <pre><code>{`# 安装
sudo apt-get update
sudo apt-get install mosquitto mosquitto-clients

# 启动服务
sudo systemctl start mosquitto
sudo systemctl enable mosquitto

# 检查状态
sudo systemctl status mosquitto`}</code></pre>
                              </div>
                              <div className="code-snippet">
                                <div className="code-snippet-header">验证连接</div>
                                <pre><code>{`# 在一个终端订阅测试
mosquitto_sub -h localhost -t test

# 在另一个终端发布测试
mosquitto_pub -h localhost -t test -m "Hello MQTT"`}</code></pre>
                              </div>
                            </div>
                          ) : (
                            <ul>
                              <li>确保 MQTT Broker 正在运行（如 Mosquitto、EMQX 等）</li>
                              <li>检查 Broker 地址和端口配置是否正确</li>
                              <li>确认防火墙未阻止端口 {mqttStatus?.port || 1883}</li>
                              <li>如果使用 Docker，确保端口已正确映射</li>
                              <li>检查 MQTT 用户名和密码是否正确（如果配置了认证）</li>
                            </ul>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>

              <div className="mqtt-usage-section">
                <h4>使用说明</h4>
                <ol className="usage-steps">
                              <li>连接到 MQTT Broker: <code>{mqttStatus?.broker || mqttStatus?.server_ip || 'localhost'}:{mqttStatus?.port || 1883}</code></li>
                  <li>发布消息到 Topic: <code>{mqttTopic}</code></li>
                  <li>消息格式为 JSON，包含图像数据（Base64 编码）</li>
                  <li>服务器会自动保存图像到当前项目</li>
                </ol>
              </div>

              <div className="mqtt-example-section">
                <h4>消息格式示例</h4>
                <div className="code-block-container">
                  <div className="code-block-header">
                    <span>JSON 消息格式</span>
                    <button
                      className="btn-copy"
                      onClick={() => copyToClipboard(examplePayload, 'example')}
                      title="复制代码"
                    >
                      <Icon component={IoCopyOutline} />
                      {copied === 'example' && <span className="copied-tooltip">已复制</span>}
                    </button>
                  </div>
                  <pre className="code-block">
                    <code>{examplePayload}</code>
                  </pre>
                </div>
              </div>

              <div className="mqtt-fields-section">
                <h4>字段说明</h4>
                <table className="fields-table">
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>类型</th>
                      <th>说明</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td><code>req_id</code></td>
                      <td>string</td>
                      <td>请求唯一标识（UUID）</td>
                    </tr>
                    <tr>
                      <td><code>device_id</code></td>
                      <td>string</td>
                      <td>设备标识符</td>
                    </tr>
                    <tr>
                      <td><code>timestamp</code></td>
                      <td>number</td>
                      <td>Unix 时间戳（秒）</td>
                    </tr>
                    <tr>
                      <td><code>image.filename</code></td>
                      <td>string</td>
                      <td>图像文件名</td>
                    </tr>
                    <tr>
                      <td><code>image.format</code></td>
                      <td>string</td>
                      <td>图像格式（jpg, png 等）</td>
                    </tr>
                    <tr>
                      <td><code>image.encoding</code></td>
                      <td>string</td>
                      <td>编码方式（base64）</td>
                    </tr>
                    <tr>
                      <td><code>image.data</code></td>
                      <td>string</td>
                      <td>Base64 编码的图像数据</td>
                    </tr>
                    <tr>
                      <td><code>metadata</code></td>
                      <td>object</td>
                      <td>可选的元数据信息</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div className="mqtt-response-section">
                <h4>响应说明</h4>
                <p>服务器会在 Topic <code>annotator/response/{'{device_id}'}</code> 返回响应消息：</p>
                <ul className="response-list">
                  <li><strong>成功响应:</strong> <code>{"{status: 'success', code: 200, message: '...'}"}</code></li>
                  <li><strong>错误响应:</strong> <code>{"{status: 'error', code: 400, message: '...'}"}</code></li>
                </ul>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};
