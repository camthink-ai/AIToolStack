import React, { useEffect, useState, useCallback } from 'react';
import { IoRefresh, IoCheckmark, IoAdd, IoPencil, IoTrash, IoClose, IoCopyOutline, IoDownloadOutline } from 'react-icons/io5';
import { Switch } from '../ui/Switch';
import { Input } from '../ui/Input';
import { Select, SelectItem } from '../ui/Select';
import { Button } from '../ui/Button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, DialogClose } from '../ui/Dialog';
import { Alert } from '../ui/Alert';
import { useAlert } from '../hooks/useAlert';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { useConfirm } from '../hooks/useConfirm';
import { useTranslation } from 'react-i18next';
import { API_BASE_URL } from '../config';
import './Dashboard.css';
import './SystemSettings.css';

// Icon component wrapper
const Icon: React.FC<{ component: React.ComponentType<any>; className?: string }> = ({ 
  component: Component, 
  className 
}) => {
  return <Component className={className} />;
};

interface MQTTConfig {
  enabled: boolean;
  external_enabled: boolean;

  // Built-in broker configuration
  builtin_protocol: 'mqtt' | 'mqtts';
  builtin_broker_host: string | null;  // Manual override for broker host IP (if null, auto-detect)
  builtin_tcp_port: number | null;
  builtin_tls_port: number | null;
  builtin_allow_anonymous: boolean;
  builtin_username: string | null;
  builtin_password: string | null;
  builtin_max_connections: number;
  builtin_keepalive_timeout: number;
  builtin_qos: number;
  builtin_keepalive: number;
  builtin_tls_enabled: boolean;
  builtin_tls_ca_cert_path: string | null;
  builtin_tls_client_cert_path: string | null;
  builtin_tls_client_key_path: string | null;
  builtin_tls_insecure_skip_verify: boolean;
  builtin_tls_require_client_cert: boolean;  // Whether to require client certificates (mTLS)

  // External broker configuration
  external_protocol: 'mqtt' | 'mqtts';
  external_host: string | null;
  external_port: number | null;
  external_username: string | null;
  external_password: string | null;
  external_qos: number;
  external_keepalive: number;
  external_tls_enabled: boolean;
  external_tls_ca_cert_path: string | null;
  external_tls_client_cert_path: string | null;
  external_tls_client_key_path: string | null;
  external_tls_insecure_skip_verify: boolean;
}

interface MQTTStatus {
  enabled: boolean;
  connected: boolean;
  broker?: string | null;
  upload_topic?: string | null;
  response_topic_prefix?: string | null;
  server_ip?: string | null;
  server_port?: number | null;
  connection_count?: number;
  disconnection_count?: number;
  message_count?: number;
  last_connect_time?: number | null;
  last_disconnect_time?: number | null;
  last_message_time?: number | null;
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

interface DeviceBootstrapInfo {
  enabled: boolean;
  mode: 'builtin' | 'external';
  protocol: 'mqtt' | 'mqtts';
  broker_type: 'builtin' | 'external';
  broker_host: string;
  broker_port: number;
  upload_topic_format: string;
  response_topic_prefix: string;
  server_ip: string;
  server_port: number;
}

interface ExternalBroker {
  id: number;
  name: string;
  enabled: boolean;
  protocol: 'mqtt' | 'mqtts';
  host: string;
  port: number;
  username: string | null;
  password: string | null;
  qos: number;
  keepalive: number;
  tls_enabled: boolean;
  tls_ca_cert_path: string | null;
  tls_client_cert_path: string | null;
  tls_client_key_path: string | null;
  tls_insecure_skip_verify: boolean;
  topic_pattern: string | null;
  connected: boolean | null; // Connection status: true=connected, false=disconnected, null=unknown
  created_at: string;
  updated_at: string;
}

type TabType = 'mqtt' | 'certificates';

// Broker Edit Dialog Component
interface BrokerEditDialogProps {
  broker: ExternalBroker | null;
  onClose: () => void;
  onSave: () => void;
  enabled: boolean;
}

const BrokerEditDialog: React.FC<BrokerEditDialogProps> = ({ broker, onClose, onSave, enabled }) => {
  const { t } = useTranslation();
  const { alertState, showSuccess, showError, closeAlert } = useAlert();
  const [formData, setFormData] = useState<Partial<ExternalBroker>>({
    name: broker?.name || '',
    enabled: broker?.enabled ?? true,
    protocol: broker?.protocol || 'mqtt',
    host: broker?.host || '',
    port: broker?.port || 1883,
    username: broker ? (broker.username || null) : null,
    password: broker ? (broker.password || null) : null,
    qos: broker?.qos || 1,
    keepalive: broker?.keepalive || 120,
    tls_enabled: broker?.tls_enabled || false,
    tls_ca_cert_path: broker?.tls_ca_cert_path || null,
    tls_client_cert_path: broker?.tls_client_cert_path || null,
    tls_client_key_path: broker?.tls_client_key_path || null,
    // tls_insecure_skip_verify is not applicable - AIToolStack as client cannot control this
    // topic_pattern is managed by backend, not user-configurable
  });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const externalCaFileInputRef = React.useRef<HTMLInputElement | null>(null);
  const externalClientCertFileInputRef = React.useRef<HTMLInputElement | null>(null);
  const externalClientKeyFileInputRef = React.useRef<HTMLInputElement | null>(null);

  // Helper function to translate backend error messages
  const translateBackendMessage = (message: string): string => {
    if (!message) return '';
    const messageMap: Record<string, string> = {
      'Connection successful': t('settings.externalBroker.connectionSuccessful'),
      'Connection failed': t('settings.externalBroker.connectionFailed'),
      'Incorrect protocol version': t('settings.externalBroker.incorrectProtocolVersion'),
      'Invalid client identifier': t('settings.externalBroker.invalidClientIdentifier'),
      'Server unavailable': t('settings.externalBroker.serverUnavailable'),
      'Bad username or password': t('settings.externalBroker.badUsernameOrPassword'),
      'Not authorized': t('settings.externalBroker.notAuthorized'),
    };
    // Check for "Connection failed (error code: X)" pattern
    const errorCodeMatch = message.match(/Connection failed \(error code: (\d+)\)/);
    if (errorCodeMatch) {
      return t('settings.externalBroker.connectionFailedWithCode', { code: errorCodeMatch[1] });
    }
    return messageMap[message] || message;
  };

  // Handle file upload for external broker certificates
  const handleUploadExternalTlsFile = async (file: File, kind: 'ca' | 'client_cert' | 'client_key') => {
    const formDataUpload = new FormData();
    formDataUpload.append('file', file);
    
    try {
      const res = await fetch(`${API_BASE_URL}/system/mqtt/tls/upload-external/${kind}`, {
        method: 'POST',
        body: formDataUpload,
      });

      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(error.detail || t('settings.uploadFailed'));
      }

      const data = await res.json();
      const filePath = data.path;

      // Update form data with the uploaded file path
      if (kind === 'ca') {
        setFormData({ ...formData, tls_ca_cert_path: filePath });
      } else if (kind === 'client_cert') {
        setFormData({ ...formData, tls_client_cert_path: filePath });
      } else if (kind === 'client_key') {
        setFormData({ ...formData, tls_client_key_path: filePath });
      }

      const typeMap: Record<string, string> = {
        'ca': t('settings.externalBroker.caCertType'),
        'client_cert': t('settings.externalBroker.clientCertType'),
        'client_key': t('settings.externalBroker.clientKeyType'),
      };
      showSuccess(t('settings.externalBroker.uploadSuccess', { type: typeMap[kind] }));
      setTestResult(null); // Clear test result when certificate changes
    } catch (err: any) {
      console.error('Failed to upload external TLS file:', err);
      showError(`${t('settings.uploadFailed')}: ${err.message || err}`);
    } finally {
      // Clear file input
      if (kind === 'ca' && externalCaFileInputRef.current) {
        externalCaFileInputRef.current.value = '';
      }
      if (kind === 'client_cert' && externalClientCertFileInputRef.current) {
        externalClientCertFileInputRef.current.value = '';
      }
      if (kind === 'client_key' && externalClientKeyFileInputRef.current) {
        externalClientKeyFileInputRef.current.value = '';
      }
    }
  };

  // Reset form data when broker changes
  useEffect(() => {
    setFormData({
      name: broker?.name || '',
      enabled: broker?.enabled ?? true,
      protocol: broker?.protocol || 'mqtt',
      host: broker?.host || '',
      port: broker?.port || 1883,
      username: broker ? (broker.username || null) : null,
      password: broker ? (broker.password || null) : null,
      qos: broker?.qos || 1,
      keepalive: broker?.keepalive || 120,
      tls_enabled: broker?.tls_enabled || false,
      tls_ca_cert_path: broker?.tls_ca_cert_path || null,
      tls_client_cert_path: broker?.tls_client_cert_path || null,
      tls_client_key_path: broker?.tls_client_key_path || null,
      // tls_insecure_skip_verify is not applicable - AIToolStack as client cannot control this
    });
    setTestResult(null); // Clear test result when broker changes
  }, [broker]);

  const handleTestConnection = async () => {
    if (!formData.host || !formData.port) {
      showError(t('settings.externalBroker.fillAddressPort'));
      return;
    }

    setTesting(true);
    setTestResult(null);
    try {
      // Prepare test data
      const testData: any = {
        name: formData.name || 'Test',
        enabled: true,
        protocol: formData.protocol || 'mqtt',
        host: formData.host,
        port: formData.port,
        username: formData.username || null,
        password: formData.password || null,
        qos: formData.qos || 1,
        keepalive: formData.keepalive || 120,
        tls_enabled: formData.tls_enabled || false,
        tls_ca_cert_path: formData.tls_ca_cert_path || null,
        tls_client_cert_path: formData.tls_client_cert_path || null,
        tls_client_key_path: formData.tls_client_key_path || null,
        // tls_insecure_skip_verify is not applicable - AIToolStack as client cannot control this
      };

      const res = await fetch(`${API_BASE_URL}/system/mqtt/external-brokers/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(testData),
      });

      const result = await res.json();
      const translatedMessage = result.message ? translateBackendMessage(result.message) : '';
      setTestResult({
        ...result,
        message: translatedMessage || result.message,
      });
      
      if (result.success) {
        showSuccess(translatedMessage || t('settings.externalBroker.testSuccess'));
      } else {
        showError(translatedMessage || t('settings.externalBroker.testFailed', { error: '' }));
      }
    } catch (e: any) {
      console.error('Failed to test connection:', e);
      setTestResult({
        success: false,
        message: `${t('settings.testFailed')}: ${e.message || e}`,
      });
      showError(`${t('settings.testFailed')}: ${e.message || e}`);
    } finally {
      setTesting(false);
    }
  };

  const handleSubmit = async () => {
    if (!formData.name || !formData.host || !formData.port) {
      showError(t('settings.externalBroker.fillRequired'));
      return;
    }

    // For new brokers, require successful connection test
    if (!broker) {
      if (!testResult) {
        showError(t('settings.externalBroker.testBeforeSave'));
        return;
      }
      if (!testResult.success) {
        showError(t('settings.externalBroker.testFailedBeforeSave'));
        return;
      }
    }

    setSaving(true);
    try {
      const url = broker
        ? `${API_BASE_URL}/system/mqtt/external-brokers/${broker.id}`
        : `${API_BASE_URL}/system/mqtt/external-brokers`;
      const method = broker ? 'PUT' : 'POST';

      // Prepare data: remove empty username/password, topic_pattern (managed by backend), and tls_insecure_skip_verify (not applicable for external brokers)
      const submitData: any = { ...formData };
      if (!submitData.username) delete submitData.username;
      if (!submitData.password) delete submitData.password;
      delete submitData.topic_pattern; // Always use backend default
      delete submitData.tls_insecure_skip_verify; // Not applicable - AIToolStack as client cannot control this

      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(submitData),
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }

      showSuccess(broker ? t('settings.externalBroker.updateSuccess') : t('settings.externalBroker.addSuccess'));
      onSave();
    } catch (e: any) {
      console.error('Failed to save broker:', e);
      showError(t('settings.externalBroker.saveFailed', { error: e.message || e }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={true} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="config-modal">
        <DialogHeader className="config-modal-header">
          <DialogTitle>{broker ? t('settings.externalBroker.editBroker') : t('settings.externalBroker.addBrokerTitle')}</DialogTitle>
          <DialogClose className="close-btn" onClick={onClose}>
            <Icon component={IoClose} />
          </DialogClose>
        </DialogHeader>
        <DialogBody className="config-modal-content">
          <div className="settings-form">
            <div className="settings-form-grid">
              <div className="settings-form-item">
                <label className="settings-form-label">{t('settings.externalBroker.nameRequired')}</label>
                <Input
                  value={formData.name || ''}
                  placeholder={t('settings.externalBroker.namePlaceholder')}
                  onChange={(e) => {
                    setFormData({ ...formData, name: e.target.value });
                    setTestResult(null); // Clear test result when form changes
                  }}
                  disabled={!enabled || saving || testing}
                />
              </div>

              <div className="settings-form-item">
                <label className="settings-form-label">{t('settings.protocol')}</label>
                <Select
                  value={formData.protocol || 'mqtt'}
                  onValueChange={(v) => setFormData({ ...formData, protocol: v as 'mqtt' | 'mqtts' })}
                  disabled={!enabled || saving || testing}
                >
                  <SelectItem value="mqtt">MQTT</SelectItem>
                  <SelectItem value="mqtts">MQTTS</SelectItem>
                </Select>
              </div>

              <div className="settings-form-item">
                <label className="settings-form-label">{t('settings.externalBroker.brokerAddressRequired')}</label>
                <Input
                  value={formData.host || ''}
                  placeholder={t('settings.externalBroker.brokerAddressPlaceholder')}
                  onChange={(e) => {
                    setFormData({ ...formData, host: e.target.value });
                    setTestResult(null); // Clear test result when form changes
                  }}
                  disabled={!enabled || saving || testing}
                />
              </div>

              <div className="settings-form-item">
                <label className="settings-form-label">{t('settings.externalBroker.portRequired')}</label>
                <Input
                  type="number"
                  value={formData.port || ''}
                  placeholder={formData.protocol === 'mqtts' ? '8883' : '1883'}
                  onChange={(e) => {
                    setFormData({
                      ...formData,
                      port: e.target.value ? parseInt(e.target.value, 10) : undefined,
                    });
                    setTestResult(null); // Clear test result when form changes
                  }}
                  disabled={!enabled || saving || testing}
                />
              </div>

              <div className="settings-form-item">
                <label className="settings-form-label">{t('settings.externalBroker.username')}</label>
                <Input
                  value={formData.username || ''}
                  placeholder={t('settings.externalBroker.usernamePlaceholder')}
                  onChange={(e) =>
                    setFormData({ ...formData, username: e.target.value || null })
                  }
                  disabled={!enabled || saving || testing}
                />
              </div>

              <div className="settings-form-item">
                <label className="settings-form-label">{t('settings.externalBroker.password')}</label>
                <Input
                  type="password"
                  value={formData.password || ''}
                  placeholder={t('settings.externalBroker.passwordPlaceholder')}
                  autoComplete="off"
                  onChange={(e) =>
                    setFormData({ ...formData, password: e.target.value || null })
                  }
                  disabled={!enabled || saving || testing}
                />
              </div>

              <div className="settings-form-item">
                <label className="settings-form-label">QoS</label>
                <Select
                  value={String(formData.qos || 1)}
                  onValueChange={(v) => setFormData({ ...formData, qos: parseInt(v, 10) })}
                  disabled={!enabled || saving || testing}
                >
                  <SelectItem value="0">0</SelectItem>
                  <SelectItem value="1">1</SelectItem>
                  <SelectItem value="2">2</SelectItem>
                </Select>
              </div>

              <div className="settings-form-item">
                <label className="settings-form-label">Keepalive</label>
                <Input
                  type="number"
                  value={formData.keepalive || ''}
                  placeholder="120"
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      keepalive: e.target.value ? parseInt(e.target.value, 10) : undefined,
                    })
                  }
                  disabled={!enabled || saving || testing}
                />
              </div>

              {formData.protocol === 'mqtts' && (
                <>
                  <div className="settings-form-item">
                    <label className="settings-form-label">{t('settings.builtinBroker.enableTls')}</label>
                    <Switch
                      checked={formData.tls_enabled || false}
                      onCheckedChange={(v) => setFormData({ ...formData, tls_enabled: v })}
                      disabled={!enabled || saving || testing}
                    />
                  </div>

                  {formData.tls_enabled && (
                    <>
                      <div className="settings-form-item">
                        <label className="settings-form-label">{t('settings.externalBroker.caCert')}</label>
                        <div className="settings-form-control">
                          <div className="input-with-action">
                            <Input
                              value={formData.tls_ca_cert_path || ''}
                              placeholder={t('settings.externalBroker.caCertPlaceholder')}
                              onChange={(e) =>
                                setFormData({ ...formData, tls_ca_cert_path: e.target.value || null })
                              }
                              disabled={!enabled || saving || testing}
                            />
                            <input
                              ref={externalCaFileInputRef}
                              type="file"
                              accept=".crt,.pem"
                              style={{ display: 'none' }}
                              onChange={(e) => {
                                const file = e.target.files?.[0];
                                if (file) {
                                  void handleUploadExternalTlsFile(file, 'ca');
                                }
                              }}
                            />
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              disabled={!enabled || saving || testing}
                              onClick={() => externalCaFileInputRef.current?.click()}
                            >
{t('settings.externalBroker.upload')}
                            </Button>
                          </div>
                        </div>
                      </div>

                      <div className="settings-form-item">
                        <label className="settings-form-label">{t('settings.externalBroker.clientCert')}</label>
                        <div className="settings-form-control">
                          <div className="input-with-action">
                            <Input
                              value={formData.tls_client_cert_path || ''}
                              placeholder={t('settings.externalBroker.clientCertPlaceholder')}
                              onChange={(e) =>
                                setFormData({
                                  ...formData,
                                  tls_client_cert_path: e.target.value || null,
                                })
                              }
                              disabled={!enabled || saving || testing}
                            />
                            <input
                              ref={externalClientCertFileInputRef}
                              type="file"
                              accept=".crt,.pem"
                              style={{ display: 'none' }}
                              onChange={(e) => {
                                const file = e.target.files?.[0];
                                if (file) {
                                  void handleUploadExternalTlsFile(file, 'client_cert');
                                }
                              }}
                            />
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              disabled={!enabled || saving || testing}
                              onClick={() => externalClientCertFileInputRef.current?.click()}
                            >
{t('settings.externalBroker.upload')}
                            </Button>
                          </div>
                        </div>
                      </div>

                      <div className="settings-form-item">
                        <label className="settings-form-label">{t('settings.externalBroker.clientKey')}</label>
                        <div className="settings-form-control">
                          <div className="input-with-action">
                            <Input
                              value={formData.tls_client_key_path || ''}
                              placeholder={t('settings.externalBroker.clientKeyPlaceholder')}
                              onChange={(e) =>
                                setFormData({
                                  ...formData,
                                  tls_client_key_path: e.target.value || null,
                                })
                              }
                              disabled={!enabled || saving || testing}
                            />
                            <input
                              ref={externalClientKeyFileInputRef}
                              type="file"
                              accept=".key,.pem"
                              style={{ display: 'none' }}
                              onChange={(e) => {
                                const file = e.target.files?.[0];
                                if (file) {
                                  void handleUploadExternalTlsFile(file, 'client_key');
                                }
                              }}
                            />
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              disabled={!enabled || saving || testing}
                              onClick={() => externalClientKeyFileInputRef.current?.click()}
                            >
{t('settings.externalBroker.upload')}
                            </Button>
                          </div>
                        </div>
                      </div>

                    </>
                  )}
                </>
              )}
            </div>

            {/* Test Result Display */}
            {testResult && (
              <div
                style={{
                  marginTop: '16px',
                  padding: '12px',
                  borderRadius: '4px',
                  backgroundColor: testResult.success
                    ? 'var(--success-light, #e8f5e9)'
                    : 'var(--error-light, #ffebee)',
                  border: `1px solid ${testResult.success ? 'var(--success-color, #4caf50)' : 'var(--error-color, #f44336)'}`,
                  color: testResult.success
                    ? 'var(--success-color, #2e7d32)'
                    : 'var(--error-color, #c62828)',
                  fontSize: '13px',
                }}
              >
                <strong>{testResult.success ? t('settings.externalBroker.testSuccess') : t('settings.externalBroker.testFailed', { error: '' })}</strong>
                {testResult.message && (
                  <div style={{ marginTop: '4px', fontSize: '12px' }}>{translateBackendMessage(testResult.message)}</div>
                )}
              </div>
            )}
          </div>
        </DialogBody>
        <DialogFooter className="config-modal-footer">
          <Button
            type="button"
            variant="secondary"
            onClick={handleTestConnection}
            disabled={testing || saving || !enabled || !formData.host || !formData.port}
          >
            {testing ? t('settings.externalBroker.testing') : t('settings.externalBroker.testConnection')}
          </Button>
          <Button type="button" variant="secondary" onClick={onClose} disabled={saving || testing}>
{t('settings.externalBroker.cancel')}
          </Button>
          <Button
            type="button"
            variant="primary"
            onClick={handleSubmit}
            disabled={saving || testing || !enabled || (!broker && (!testResult || !testResult.success))}
            title={!broker && (!testResult || !testResult.success) ? t('settings.externalBroker.testFirst') : ''}
          >
            <Icon component={IoCheckmark} /> {saving ? t('settings.externalBroker.saving') : t('settings.externalBroker.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
      <Alert
        open={alertState.open}
        onOpenChange={closeAlert}
        title={alertState.title}
        message={alertState.message}
        type={alertState.type}
      />
    </Dialog>
  );
};

// Generate Certificate Dialog Component
interface GenerateCertDialogProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
  forAIToolStack: boolean;
  enabled: boolean;
  showSuccess: (message: string) => void;
  showError: (message: string) => void;
}

const GenerateCertDialog: React.FC<GenerateCertDialogProps> = ({
  open,
  onClose,
  onSuccess,
  forAIToolStack,
  enabled,
  showSuccess,
  showError,
}) => {
  const { t } = useTranslation();
  const [commonName, setCommonName] = useState(forAIToolStack ? 'mqtt-client' : 'device-1');
  const [days, setDays] = useState('3650');
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    if (open) {
      setCommonName(forAIToolStack ? 'mqtt-client' : 'device-1');
      setDays('3650');
    }
  }, [open, forAIToolStack]);

  const handleSubmit = async () => {
    if (!commonName.trim()) {
      showError(t('settings.certificate.commonNameRequired'));
      return;
    }

    const daysNum = parseInt(days, 10);
    if (isNaN(daysNum) || daysNum <= 0) {
      showError(t('settings.certificate.validityDaysRequired'));
      return;
    }

    setGenerating(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/system/mqtt/tls/generate-client-cert?common_name=${encodeURIComponent(commonName.trim())}&days=${encodeURIComponent(daysNum)}&for_aitoolstack=${forAIToolStack}`,
        { method: 'POST' }
      );

      if (response.ok) {
        const data = await response.json();
        if (forAIToolStack) {
          showSuccess(t('settings.certificate.generateSuccessAIToolStack', { cn: commonName }));
          onSuccess();
          onClose();
        } else {
          showSuccess(t('settings.certificate.generateSuccessDevice', { cn: commonName }));
          // Trigger refresh event for DeviceCertificatesList component
          window.dispatchEvent(new CustomEvent('device-cert-generated'));
          onSuccess();
          onClose();
        }
      } else {
        const error = await response.json();
        showError(error.detail || t('settings.certificate.generateFailed', { type: forAIToolStack ? t('settings.certificate.aitoolstack') : t('settings.certificate.device') }));
      }
    } catch (err) {
      showError(t('settings.certificate.generateFailedError', { error: err instanceof Error ? err.message : String(err) }));
    } finally {
      setGenerating(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && !generating && onClose()}>
      <DialogContent className="config-modal">
        <DialogHeader className="config-modal-header">
          <DialogTitle>
            {forAIToolStack ? t('settings.certificate.generateAIToolStackTitle') : t('settings.certificate.generateTitle')}
          </DialogTitle>
          <DialogClose className="close-btn" onClick={onClose} disabled={generating}>
            <Icon component={IoClose} />
          </DialogClose>
        </DialogHeader>
        <DialogBody className="config-modal-content">
          <div className="settings-form">
            <div className="settings-form-grid">
              <div className="settings-form-item">
                <label className="settings-form-label">{t('settings.certificate.commonName')}</label>
                <Input
                  value={commonName}
                  placeholder={forAIToolStack ? t('settings.certificate.commonNamePlaceholderAIToolStack') : t('settings.certificate.commonNamePlaceholderDevice')}
                  onChange={(e) => setCommonName(e.target.value)}
                  disabled={!enabled || generating}
                />
                <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
                  {forAIToolStack
                    ? t('settings.certificate.commonNameDescAIToolStack')
                    : t('settings.certificate.commonNameDescDevice')}
                </div>
              </div>

              <div className="settings-form-item">
                <label className="settings-form-label">{t('settings.certificate.validityDays')}</label>
                <Input
                  type="number"
                  value={days}
                  placeholder={t('settings.certificate.validityDaysPlaceholder')}
                  onChange={(e) => setDays(e.target.value)}
                  disabled={!enabled || generating}
                />
                <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
                  {t('settings.certificate.validityDaysDesc')}
                </div>
              </div>
            </div>

            <div style={{ marginTop: '16px', padding: '12px', backgroundColor: 'var(--bg-secondary)', borderRadius: '4px', fontSize: '13px', color: 'var(--text-secondary)' }}>
              <strong>{t('settings.description')}</strong>
              <ul style={{ margin: '8px 0', paddingLeft: '20px' }}>
                {forAIToolStack ? (
                  <>
                    <li>{t('settings.certificate.descriptionAIToolStack')}</li>
                    <li>{t('settings.certificate.descriptionAIToolStack2')}</li>
                  </>
                ) : (
                  <>
                    <li>{t('settings.certificate.descriptionDevice')}</li>
                    <li>{t('settings.certificate.descriptionDevice2')}</li>
                    <li>{t('settings.certificate.descriptionDevice3')}</li>
                  </>
                )}
              </ul>
            </div>
          </div>
        </DialogBody>
        <DialogFooter className="config-modal-footer">
          <Button
            type="button"
            variant="secondary"
            onClick={onClose}
            disabled={generating}
          >
{t('settings.externalBroker.cancel')}
          </Button>
          <Button
            type="button"
            variant="primary"
            onClick={handleSubmit}
            disabled={!enabled || generating || !commonName.trim() || !days}
          >
            {generating ? t('settings.certificate.generating') : t('settings.certificate.generate')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

// DeviceCertificatesList component - moved outside to prevent re-creation on parent re-render
interface DeviceCertificatesListProps {
  enabled: boolean;
  showSuccess: (msg: string) => void;
  showError: (msg: string) => void;
}

const DeviceCertificatesList: React.FC<DeviceCertificatesListProps> = React.memo(({ enabled, showSuccess, showError }) => {
  const { t } = useTranslation();
  const { confirmState, showConfirm, closeConfirm } = useConfirm();
  const [deviceCerts, setDeviceCerts] = React.useState<Array<{
    common_name: string;
    cert_path: string;
    key_path: string | null;
    cert_exists: boolean;
    key_exists: boolean;
    created_at: string | null;
  }>>([]);
  const [loading, setLoading] = React.useState(true);

  const loadDeviceCerts = React.useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_BASE_URL}/system/mqtt/tls/device-certificates`);
      if (res.ok) {
        const data = await res.json();
        setDeviceCerts(data.devices || []);
      }
    } catch (e) {
      console.error('Failed to load device certificates:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    // Load data when component mounts
    loadDeviceCerts();
    // Listen for custom event to refresh list when certificate is generated
    const handleRefresh = () => {
      loadDeviceCerts();
    };
    // Listen for custom event to refresh when switching to certificates tab
    const handleTabSwitch = () => {
      loadDeviceCerts();
    };
    window.addEventListener('device-cert-generated', handleRefresh);
    window.addEventListener('refresh-certificate-list', handleTabSwitch);
    return () => {
      window.removeEventListener('device-cert-generated', handleRefresh);
      window.removeEventListener('refresh-certificate-list', handleTabSwitch);
    };
  }, [loadDeviceCerts]);

  const handleDelete = async (cn: string) => {
    showConfirm(
      t('settings.builtinBroker.deleteCertConfirm', { name: cn }),
      async () => {
        try {
          const res = await fetch(
            `${API_BASE_URL}/system/mqtt/tls/device-certificate/${encodeURIComponent(cn)}`,
            { method: 'DELETE' }
          );
          if (res.ok) {
            showSuccess(t('settings.builtinBroker.deleteCertSuccess', { name: cn }));
            await loadDeviceCerts();
          } else {
            const error = await res.json();
            showError(error.detail || t('settings.builtinBroker.deleteCertFailed'));
          }
        } catch (err) {
          showError(t('settings.builtinBroker.deleteCertFailed') + ': ' + (err instanceof Error ? err.message : String(err)));
        }
      },
      {
        variant: 'danger',
      }
    );
  };

  if (loading) {
    return <div style={{ padding: '12px', color: 'var(--text-secondary)' }}>{t('settings.loading')}</div>;
  }

  if (deviceCerts.length === 0) {
    return (
      <div style={{ padding: '12px', color: 'var(--text-secondary)', fontSize: '13px' }}>
        {t('settings.builtinBroker.noCertificates')}
      </div>
    );
  }

  return (
    <div className="settings-broker-list">
      {deviceCerts.map((device) => (
        <div key={device.common_name} className="settings-broker-item">
          <div className="settings-broker-header">
            <div className="settings-broker-info">
              <div className="settings-broker-name-row">
                <h4 className="settings-broker-name">{device.common_name}</h4>
                <span className="settings-broker-status settings-broker-status-connected">
                  {device.cert_exists && device.key_exists ? t('settings.builtinBroker.certificate') + ' ' + t('common.success') : t('settings.builtinBroker.certificate') + ' ' + t('common.error')}
                </span>
              </div>
              <span className="settings-broker-address">
                {t('settings.builtinBroker.certificate')}: {device.cert_path.split('/').pop()}
                {device.key_path && ` | ${t('settings.builtinBroker.privateKey')}: ${device.key_path.split('/').pop()}`}
              </span>
              {device.created_at && (
                <span style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px', display: 'block' }}>
                  {t('settings.createdAt')}: {new Date(device.created_at).toLocaleString()}
                </span>
              )}
            </div>
            <div className="settings-broker-actions">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  window.open(`${API_BASE_URL}/system/mqtt/tls/device-cert/${encodeURIComponent(device.common_name)}`, '_blank');
                }}
                disabled={!device.cert_exists}
              >
                {t('settings.builtinBroker.downloadCert')}
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  window.open(`${API_BASE_URL}/system/mqtt/tls/device-key/${encodeURIComponent(device.common_name)}`, '_blank');
                }}
                disabled={!device.key_exists}
              >
                {t('settings.builtinBroker.downloadKey')}
              </Button>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => handleDelete(device.common_name)}
                disabled={!enabled}
              >
                <Icon component={IoTrash} /> {t('settings.builtinBroker.deleteCert')}
              </Button>
            </div>
          </div>
        </div>
      ))}
      <ConfirmDialog
        open={confirmState.open}
        onOpenChange={(open) => {
          if (!open) {
            closeConfirm();
          }
        }}
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
});

DeviceCertificatesList.displayName = 'DeviceCertificatesList';

export const SystemSettings: React.FC = () => {
  const { t } = useTranslation();
  const [config, setConfig] = useState<MQTTConfig | null>(null);
  const [status, setStatus] = useState<MQTTStatus | null>(null);
  const [bootstrap, setBootstrap] = useState<DeviceBootstrapInfo | null>(null);
  const [externalBrokers, setExternalBrokers] = useState<ExternalBroker[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editingBroker, setEditingBroker] = useState<ExternalBroker | null>(null);
  const [showBrokerDialog, setShowBrokerDialog] = useState(false);
  const [showGenerateCertDialog, setShowGenerateCertDialog] = useState(false);
  const { alertState, showSuccess, showError, closeAlert } = useAlert();
  const { confirmState, showConfirm, closeConfirm } = useConfirm();
  const [showBuiltinPassword, setShowBuiltinPassword] = useState(false);
  const caFileInputRef = React.useRef<HTMLInputElement | null>(null);
  const [copiedPassword, setCopiedPassword] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabType>('mqtt');

  const copyToClipboard = useCallback((text: string, type: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedPassword(type);
      showSuccess(t('settings.copied'));
      setTimeout(() => setCopiedPassword(null), 2000);
    }).catch((err) => {
      console.error('Failed to copy:', err);
      showError(t('settings.copyFailed'));
    });
  }, [showSuccess, showError, t]);

  const downloadCertificate = useCallback((certType: 'ca' | 'client-cert' | 'client-key', filename?: string) => {
    const url = `${API_BASE_URL}/system/mqtt/tls/${certType}${filename ? `/${encodeURIComponent(filename)}` : ''}`;
    window.open(url, '_blank');
  }, []);

  useEffect(() => {
    void refreshAll();
  }, []);

  // Refresh certificate list when switching to certificates tab
  useEffect(() => {
    if (activeTab === 'certificates') {
      // Dispatch event to trigger certificate list refresh
      window.dispatchEvent(new CustomEvent('refresh-certificate-list'));
    }
  }, [activeTab]);

  const refreshAll = async () => {
    setLoading(true);
    try {
      await Promise.all([
        loadConfig(),
        loadStatus(),
        loadBootstrap(),
        loadExternalBrokers(),
      ]);
    } finally {
      setLoading(false);
    }
  };

  const loadExternalBrokers = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/system/mqtt/external-brokers`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setExternalBrokers(data as ExternalBroker[]);
    } catch (e: any) {
      console.error('Failed to load external brokers:', e);
      showError(t('settings.loadBrokerListFailed'));
    }
  };

  // Auto-refresh broker connection status periodically
  useEffect(() => {
    if (config?.enabled) {
      const interval = setInterval(() => {
        loadExternalBrokers();
        loadStatus(); // Also refresh MQTT status
      }, 5000); // Refresh every 5 seconds
      return () => clearInterval(interval);
    }
  }, [config?.enabled]);

  const loadConfig = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/system/mqtt/config`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setConfig(data as MQTTConfig);
    } catch (e: any) {
      console.error('Failed to load MQTT config:', e);
      showError(t('settings.loadConfigFailed'));
    }
  };

  const loadStatus = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/mqtt/status`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setStatus(data as MQTTStatus);
    } catch (e) {
      console.error('Failed to load MQTT status:', e);
    }
  };

  const loadBootstrap = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/device/bootstrap`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setBootstrap(data as DeviceBootstrapInfo);
    } catch (e) {
      console.error('Failed to load device bootstrap info:', e);
    }
  };

  const handleConfigChange = (field: keyof MQTTConfig, value: any) => {
    setConfig((prev) => {
      if (!prev) return null;
      return { ...prev, [field]: value };
    });
  };

  const handleUploadCaCert = async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch(`${API_BASE_URL}/system/mqtt/tls/upload/ca`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      showSuccess(t('settings.uploadCaCertSuccess'));
      // 重新加载配置以获取最新的 CA 路径
      await loadConfig();
    } catch (e: any) {
      console.error('Failed to upload CA cert:', e);
      showError(`${t('settings.uploadCaCertFailed')}: ${e.message || e}`);
    } finally {
      if (caFileInputRef.current) {
        caFileInputRef.current.value = '';
      }
    }
  };


  const parseNumber = (value: string, fallback: number): number => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };


  const handleBrokerToggle = async (brokerId: number, enabled: boolean) => {
    try {
      const broker = externalBrokers.find(b => b.id === brokerId);
      if (!broker) return;

      const res = await fetch(`${API_BASE_URL}/system/mqtt/external-brokers/${brokerId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadExternalBrokers();
    } catch (e: any) {
      console.error('Failed to toggle broker:', e);
      showError(`${t('settings.operationFailed')}: ${e.message || e}`);
    }
  };

  const handleDeleteBroker = async (brokerId: number) => {
    showConfirm(
      t('settings.deleteBrokerConfirm'),
      async () => {
        try {
          const res = await fetch(`${API_BASE_URL}/system/mqtt/external-brokers/${brokerId}`, {
            method: 'DELETE',
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          await loadExternalBrokers();
          showSuccess(t('settings.deleteBrokerSuccess'));
        } catch (e: any) {
          console.error('Failed to delete broker:', e);
          showError(`${t('settings.deleteBrokerFailed')}: ${e.message || e}`);
        }
      },
      {
        variant: 'danger',
      }
    );
  };

  const handleSave = async () => {
    if (!config) return;
    
    // Validate that username/password are provided when anonymous is disabled
    if (!config.builtin_allow_anonymous) {
      if (!config.builtin_username || !config.builtin_password) {
        showError(t('settings.usernamePasswordRequired'));
        return;
      }
    }
    
    setSaving(true);
    try {
      // Only send fields that are in MQTTConfigUpdate model
      const updateData: any = {
        enabled: config.enabled,
        external_enabled: config.external_enabled,
        // Built-in broker fields
        builtin_protocol: config.builtin_protocol,
        // Ports are fixed: 1883 for MQTT, 8883 for MQTTS
        builtin_tcp_port: 1883,
        builtin_tls_port: 8883,
        builtin_allow_anonymous: config.builtin_allow_anonymous,
        builtin_username: config.builtin_username,
        builtin_password: config.builtin_password,
        builtin_max_connections: config.builtin_max_connections,
        builtin_keepalive_timeout: config.builtin_keepalive_timeout,
        // builtin_qos and builtin_keepalive are client-side settings, not user-configurable
        builtin_tls_enabled: config.builtin_tls_enabled,
        builtin_tls_ca_cert_path: config.builtin_tls_ca_cert_path || null,
        builtin_tls_client_cert_path: config.builtin_tls_client_cert_path || null,
        builtin_tls_client_key_path: config.builtin_tls_client_key_path || null,
        builtin_tls_insecure_skip_verify: config.builtin_tls_insecure_skip_verify,
        builtin_tls_require_client_cert: config.builtin_tls_require_client_cert,
        // External broker fields
        external_protocol: config.external_protocol,
        external_host: config.external_host || null,
        external_port: config.external_port || null,
        external_username: config.external_username || null,
        external_password: config.external_password || null,
        external_qos: config.external_qos,
        external_keepalive: config.external_keepalive,
        external_tls_enabled: config.external_tls_enabled,
        external_tls_ca_cert_path: config.external_tls_ca_cert_path || null,
        external_tls_client_cert_path: config.external_tls_client_cert_path || null,
        external_tls_client_key_path: config.external_tls_client_key_path || null,
        external_tls_insecure_skip_verify: config.external_tls_insecure_skip_verify,
      };

      const res = await fetch(`${API_BASE_URL}/system/mqtt/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updateData),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      const data = (await res.json()) as MQTTConfig;
      setConfig(data);
      await Promise.all([loadStatus(), loadBootstrap()]);
      showSuccess(t('settings.configSaveSuccess'));
      
      // Wait a bit for connection to establish, then refresh status
      setTimeout(async () => {
        await Promise.all([loadStatus(), loadBootstrap()]);
      }, 2000);
    } catch (e: any) {
      console.error('Failed to save MQTT config:', e);
      showError(`${t('settings.configSaveFailedError')}: ${e.message || e}`);
    } finally {
      setSaving(false);
    }
  };

  const renderStatusBadge = () => {
    if (!status) return null;
    if (!status.enabled) {
      return <span className="settings-status-badge settings-status-badge-disabled">{t('settings.disabled')}</span>;
    }
    // Check builtin broker connection status specifically
    const builtinConnected = status.builtin?.connected ?? status.connected;
    if (builtinConnected) {
      return <span className="settings-status-badge settings-status-badge-connected">{t('settings.connected')}</span>;
    }
    return <span className="settings-status-badge settings-status-badge-disconnected">{t('settings.disconnected')}</span>;
  };

  const renderMQTTConfigTab = () => {
    if (!config) {
      return (
        <div className="settings-card">
          <div className="settings-card-body">
            <div className="settings-loading">
              <p>{t('common.loading')}</p>
            </div>
          </div>
        </div>
      );
    }

    const cfg = config as MQTTConfig;

    return (
      <>
        {/* 内置MQTT Broker管理卡片 */}
          <div className="settings-card">
            <div className="settings-card-header">
              <div className="settings-card-header-main">
                <div className="settings-card-title-row">
                  <h3 className="settings-card-title">{t('settings.builtinBroker.title')}</h3>
                {renderStatusBadge()}
              </div>
            </div>
            <div className="settings-card-actions">
              <div className="settings-card-buttons">
                <Button
                  type="button"
                  variant="primary"
                  size="md"
                  onClick={handleSave}
                  disabled={saving || !config}
                >
                  <Icon component={IoCheckmark} /> {saving ? t('settings.savingConfig') : t('settings.saveConfig')}
                </Button>
              </div>
            </div>
          </div>

          <div className="settings-card-body">
            {/* Broker 协议配置 */}
            <div className="settings-group">
              <h4 style={{ marginBottom: '12px', fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                {t('settings.builtinBroker.basicConfig')}
              </h4>
              <div className="settings-form-grid">
                <div className="settings-form-item">
                  <label className="settings-form-label">{t('settings.builtinBroker.protocol')}</label>
                  <div className="settings-form-control">
                    <Select
                      value={cfg.builtin_protocol}
                      onValueChange={(v) => handleConfigChange('builtin_protocol', v as MQTTConfig['builtin_protocol'])}
                      disabled={!cfg.enabled}
                    >
                      <SelectItem value="mqtt">MQTT ({t('settings.builtinBroker.tcpPort')} 1883)</SelectItem>
                      <SelectItem value="mqtts">MQTTS ({t('settings.builtinBroker.tlsPort')} 8883)</SelectItem>
                    </Select>
                    <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
                      {t('settings.protocolDesc')}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Broker 基本配置 */}
            <div className="settings-group">
              <div className="settings-form-grid">
                <div className="settings-form-item">
                  <label className="settings-form-label">{t('settings.builtinBroker.brokerAddress')}</label>
                  <div className="settings-form-control">
                    <div 
                      className="input-base input-md"
                      style={{ 
                        fontSize: 'var(--font-size-base)', 
                        color: 'var(--text-primary)', 
                        backgroundColor: 'var(--bg-secondary)', 
                        border: '1px solid var(--border-color)',
                        display: 'flex',
                        alignItems: 'center',
                        cursor: 'not-allowed'
                      }}
                    >
                      {status?.builtin?.host || status?.server_ip || t('settings.detecting')}
                    </div>
                    <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
                      {t('settings.autoDetect')}
                    </div>
                  </div>
                </div>

                <div className="settings-form-item">
                  <label className="settings-form-label">{t('settings.builtinBroker.tcpPort')}</label>
                  <div className="settings-form-control">
                    <Input
                      type="number"
                      value="1883"
                      readOnly
                      disabled={true}
                      style={{ backgroundColor: '#f5f5f5', cursor: 'not-allowed' }}
                    />
                    <span style={{ marginLeft: '8px', color: '#666', fontSize: '12px' }}>{t('settings.fixedPort')}</span>
                  </div>
                </div>

                {cfg.builtin_protocol === 'mqtts' && (
                  <div className="settings-form-item">
                    <label className="settings-form-label">{t('settings.builtinBroker.tlsPort')}</label>
                    <div className="settings-form-control">
                      <Input
                        type="number"
                        value="8883"
                        readOnly
                        disabled={true}
                        style={{ backgroundColor: '#f5f5f5', cursor: 'not-allowed' }}
                      />
                      <span style={{ marginLeft: '8px', color: '#666', fontSize: '12px' }}>{t('settings.fixedPort')}</span>
                    </div>
                  </div>
                )}

                <div className="settings-form-item">
                  <label className="settings-form-label">{t('settings.builtinBroker.maxConnections')}</label>
                  <div className="settings-form-control">
                    <Input
                      type="number"
                      value={cfg.builtin_max_connections}
                      onChange={(e) =>
                        handleConfigChange(
                          'builtin_max_connections',
                          parseNumber(e.target.value, 100),
                        )
                      }
                      disabled={!cfg.enabled}
                    />
                  </div>
                </div>

                <div className="settings-form-item">
                  <label className="settings-form-label">{t('settings.builtinBroker.keepaliveTimeout')}</label>
                  <div className="settings-form-control">
                    <Input
                      type="number"
                      value={cfg.builtin_keepalive_timeout}
                      placeholder="300"
                      onChange={(e) =>
                        handleConfigChange(
                          'builtin_keepalive_timeout',
                          parseNumber(e.target.value, 300),
                        )
                      }
                      disabled={!cfg.enabled}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* 认证配置 - 允许匿名连接、用户名、密码 */}
            <div className="settings-group">
              <h4 style={{ marginBottom: '12px', fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                {t('settings.builtinBroker.authConfig')}
              </h4>
              <div className="settings-form-grid">
                <div className="settings-form-item">
                  <label className="settings-form-label">{t('settings.builtinBroker.allowAnonymous')}</label>
                  <div className="settings-form-control">
                    <Switch
                      checked={cfg.builtin_allow_anonymous}
                      onCheckedChange={(v) => handleConfigChange('builtin_allow_anonymous', v)}
                      disabled={!cfg.enabled}
                    />
                  </div>
                </div>

                {!cfg.builtin_allow_anonymous && (
                  <>
                    <div className="settings-form-item">
                      <label className="settings-form-label">{t('settings.builtinBroker.username')} *</label>
                      <div className="settings-form-control">
                        <Input
                          value={cfg.builtin_username || ''}
                          placeholder={t('settings.builtinBroker.username')}
                          onChange={(e) =>
                            handleConfigChange('builtin_username', e.target.value || null)
                          }
                          disabled={!cfg.enabled}
                        />
                      </div>
                    </div>

                    <div className="settings-form-item">
                      <label className="settings-form-label">{t('settings.builtinBroker.password')} *</label>
                      <div className="settings-form-control">
                        <div className="input-with-action">
                          <Input
                            type={showBuiltinPassword ? 'text' : 'password'}
                            value={cfg.builtin_password || ''}
                            placeholder={t('settings.builtinBroker.password')}
                            onChange={(e) =>
                              handleConfigChange('builtin_password', e.target.value || null)
                            }
                            disabled={!cfg.enabled}
                          />
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            onClick={() => setShowBuiltinPassword((v) => !v)}
                            disabled={!cfg.enabled}
                          >
                            {showBuiltinPassword ? t('settings.hide') : t('settings.show')}
                          </Button>
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* TLS 配置 - 仅在 MQTTS 协议时显示 */}
            {cfg.builtin_protocol === 'mqtts' && (
              <div className="settings-group">
                <h4 style={{ marginBottom: '12px', fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                {t('settings.builtinBroker.tlsConfig')}
                </h4>
                <div className="settings-form-grid">
                  <div className="settings-form-item">
                    <label className="settings-form-label">{t('settings.builtinBroker.enableTls')}</label>
                    <div className="settings-form-control">
                      <Switch
                        checked={cfg.builtin_tls_enabled}
                        onCheckedChange={(v) => handleConfigChange('builtin_tls_enabled', v)}
                        disabled={!cfg.enabled}
                      />
                    </div>
                  </div>

                  {cfg.builtin_tls_enabled && (
                    <>
                      <div className="settings-form-item">
                        <label className="settings-form-label">{t('settings.builtinBroker.requireClientCert')}</label>
                        <div className="settings-form-control">
                          <Switch
                            checked={cfg.builtin_tls_require_client_cert}
                            onCheckedChange={(v) => handleConfigChange('builtin_tls_require_client_cert', v)}
                            disabled={!cfg.enabled}
                          />
                          <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
                            {cfg.builtin_tls_require_client_cert
                              ? t('settings.builtinBroker.requireClientCertDesc')
                              : t('settings.builtinBroker.requireClientCertDescOff')}
                          </div>
                        </div>
                      </div>

                      <div className="settings-form-item">
                        <label className="settings-form-label">{t('settings.builtinBroker.caCert')}</label>
                        <div className="settings-form-control">
                          <div className="input-with-action">
                            <Input
                              value={cfg.builtin_tls_ca_cert_path || '/mosquitto/config/certs/ca.crt'}
                              placeholder="/mosquitto/config/certs/ca.crt"
                              onChange={(e) =>
                                handleConfigChange('builtin_tls_ca_cert_path', e.target.value || null)
                              }
                              disabled={!cfg.enabled}
                            />
                            <input
                              ref={caFileInputRef}
                              type="file"
                              accept=".crt,.pem"
                              style={{ display: 'none' }}
                              onChange={(e) => {
                                const file = e.target.files?.[0];
                                if (file) {
                                  void handleUploadCaCert(file);
                                }
                              }}
                            />
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              disabled={!cfg.enabled}
                              onClick={() => caFileInputRef.current?.click()}
                            >
{t('settings.externalBroker.upload')} CA
                            </Button>
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              onClick={() => {
                                window.open(`${API_BASE_URL}/system/mqtt/tls/ca`, '_blank');
                              }}
                            >
                              {t('settings.downloadCa')}
                            </Button>
                          </div>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              </div>
            )}

            {/* 客户端连接状态（只读显示） */}
            <div className="settings-group" style={{ marginTop: '24px', paddingTop: '24px', borderTop: '1px solid var(--border-color)' }}>
              <h4 style={{ marginBottom: '12px', fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                {t('settings.builtinBroker.clientConnection')}
              </h4>
              <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                {t('settings.builtinBroker.clientConnectionDesc')}
              </div>
              <div className="settings-form-grid">
                <div className="settings-form-item">
                  <label className="settings-form-label">{t('settings.connectionProtocol')}</label>
                  <div className="settings-form-control">
                    <div 
                      className="input-base input-md"
                      style={{ 
                        fontSize: 'var(--font-size-base)', 
                        color: 'var(--text-primary)', 
                        backgroundColor: 'var(--bg-secondary)', 
                        border: '1px solid var(--border-color)',
                        display: 'flex',
                        alignItems: 'center',
                        cursor: 'not-allowed'
                      }}
                    >
                      {cfg.builtin_protocol === 'mqtts' ? `MQTTS (${t('settings.autoMatchProtocol')})` : `MQTT (${t('settings.autoMatchProtocol')})`}
                    </div>
                    <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
                      {t('settings.autoMatchProtocol')}
                    </div>
                  </div>
                </div>
                <div className="settings-form-item">
                  <label className="settings-form-label">{t('settings.connectionPort')}</label>
                  <div className="settings-form-control">
                    <div 
                      className="input-base input-md"
                      style={{ 
                        fontSize: 'var(--font-size-base)', 
                        color: 'var(--text-primary)', 
                        backgroundColor: 'var(--bg-secondary)', 
                        border: '1px solid var(--border-color)',
                        display: 'flex',
                        alignItems: 'center',
                        cursor: 'not-allowed'
                      }}
                    >
                      {cfg.builtin_protocol === 'mqtts' ? `8883 (${t('settings.autoMatchPort')})` : `1883 (${t('settings.autoMatchPort')})`}
                    </div>
                    <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
                      {t('settings.autoMatchPort')}
                    </div>
                  </div>
                </div>
                <div className="settings-form-item">
                  <label className="settings-form-label">QoS</label>
                  <div className="settings-form-control">
                    <Input
                      type="number"
                      value={cfg.builtin_qos}
                      onChange={(e) =>
                        handleConfigChange(
                          'builtin_qos',
                          parseNumber(e.target.value, 1),
                        )
                      }
                      disabled={!cfg.enabled}
                    />
                    <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
                      {t('settings.clientQos')}
                    </div>
                  </div>
                </div>
                <div className="settings-form-item">
                  <label className="settings-form-label">Keepalive</label>
                  <div className="settings-form-control">
                    <Input
                      type="number"
                      value={cfg.builtin_keepalive}
                      onChange={(e) =>
                        handleConfigChange(
                          'builtin_keepalive',
                          parseNumber(e.target.value, 120),
                        )
                      }
                      disabled={!cfg.enabled}
                    />
                    <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
                      {t('settings.clientKeepalive')}
                    </div>
                  </div>
                </div>
              </div>
            </div>

          </div>
        </div>

        {/* 外部 MQTT Broker管理 卡片 */}
        <div className="settings-card">
          <div className="settings-card-header">
            <div className="settings-card-header-main">
              <div className="settings-card-title-row">
                <h3 className="settings-card-title">{t('settings.externalBroker.title')}</h3>
              </div>
              <p className="settings-card-subtitle" style={{ marginTop: '8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                {t('settings.externalBroker.subtitle')}
              </p>
            </div>
            <div className="settings-card-actions">
              <div className="settings-card-buttons">
                <Button
                  type="button"
                  variant="primary"
                  size="md"
                  onClick={() => {
                    setEditingBroker(null);
                    setShowBrokerDialog(true);
                  }}
                  disabled={!cfg.enabled}
                >
                  <Icon component={IoAdd} /> {t('settings.externalBroker.addBroker')}
                </Button>
              </div>
            </div>
          </div>

          <div className="settings-card-body">
            {externalBrokers.length === 0 ? (
              <div className="settings-loading">
                <p>{t('settings.externalBroker.noBrokers')}</p>
              </div>
            ) : (
              <div>
                <div style={{ marginBottom: '12px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                  {t('settings.configuredBrokers', { total: externalBrokers.length, enabled: externalBrokers.filter(b => b.enabled).length })}
                </div>
                <div className="settings-broker-list">
                  {externalBrokers.map((broker) => (
                  <div key={broker.id} className="settings-broker-item">
                    <div className="settings-broker-header">
                      <div className="settings-broker-info">
                        <div className="settings-broker-name-row">
                          <h4 className="settings-broker-name">{broker.name}</h4>
                          {broker.enabled && (
                            <span
                              className={`settings-broker-status ${
                                broker.connected === true
                                  ? 'settings-broker-status-connected'
                                  : broker.connected === false
                                  ? 'settings-broker-status-disconnected'
                                  : 'settings-broker-status-unknown'
                              }`}
                            >
                              {broker.connected === true
                                ? t('settings.connected')
                                : broker.connected === false
                                ? t('settings.disconnected')
                                : t('settings.connecting')}
                            </span>
                          )}
                          {!broker.enabled && (
                            <span className="settings-broker-status settings-broker-status-disabled">
                              {t('settings.disabled')}
                            </span>
                          )}
                        </div>
                        <span className="settings-broker-address">
                          {broker.protocol.toUpperCase()}://{broker.host}:{broker.port}
                        </span>
                      </div>
                      <div className="settings-broker-actions">
                        <Switch
                          checked={broker.enabled}
                          onCheckedChange={(v) => handleBrokerToggle(broker.id, v)}
                          disabled={!cfg.enabled}
                        />
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => {
                            setEditingBroker(broker);
                            setShowBrokerDialog(true);
                          }}
                          disabled={!cfg.enabled}
                          title={t('settings.edit')}
                          aria-label={t('settings.edit')}
                        >
                          <Icon component={IoPencil} />
                        </Button>
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => handleDeleteBroker(broker.id)}
                          disabled={!cfg.enabled}
                          title={t('settings.delete')}
                          aria-label={t('settings.delete')}
                        >
                          <Icon component={IoTrash} />
                        </Button>
                      </div>
                    </div>
                  </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Broker 编辑对话框 */}
        {showBrokerDialog && (
          <BrokerEditDialog
            broker={editingBroker}
            onClose={() => {
              setShowBrokerDialog(false);
              setEditingBroker(null);
            }}
            onSave={async () => {
              await loadExternalBrokers();
              setShowBrokerDialog(false);
              setEditingBroker(null);
            }}
            enabled={cfg.enabled}
          />
        )}

      </>
    );
  };

  const renderCertificatesTab = () => {
    if (!config) {
      return (
        <div className="settings-card">
          <div className="settings-card-body">
            <div className="settings-loading">
              <p>{t('common.loading')}</p>
            </div>
          </div>
        </div>
      );
    }

    const cfg = config;
    // Check if TLS is enabled (for MQTTS support)
    // Note: Built-in broker can support both MQTT and MQTTS simultaneously
    // builtin_protocol determines which protocol AIToolStack client uses to connect
    // builtin_tls_enabled determines if TLS listener (8883) is enabled
    // Certificate management is independent - certificates are used for MQTTS connections
    const isTLSEnabled = cfg.builtin_tls_enabled && cfg.builtin_tls_ca_cert_path;

    return (
      <>
        {/* 证书管理 */}
        <div className="settings-card">
          <div className="settings-card-header">
            <div>
              <h3 className="settings-card-title">{t('settings.builtinBroker.certManagement')}</h3>
              <p className="settings-card-subtitle">{t('settings.builtinBroker.certManagementDesc')}</p>
            </div>
          </div>
          <div className="settings-card-body">
            {!isTLSEnabled && (
              <div style={{ 
                padding: '12px 16px', 
                marginBottom: '16px',
                background: 'var(--bg-info, #d1ecf1)',
                border: '1px solid var(--border-info, #bee5eb)',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--text-info, #0c5460)',
                fontSize: '13px'
              }}>
                <p style={{ margin: 0 }}>
                  {t('settings.builtinBroker.certManagementInfo', '证书用于 MQTTS 连接。内置 Broker 可以同时支持 MQTT (1883) 和 MQTTS (8883) 连接。如需使用 MQTTS，请在 MQTT 配置中启用 TLS。')}
                </p>
              </div>
            )}
            <div style={{ marginBottom: '16px' }}>
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={() => {
                  setShowGenerateCertDialog(true);
                }}
                disabled={!cfg.enabled || !isTLSEnabled}
              >
                <Icon component={IoAdd} /> {t('settings.builtinBroker.generateCert')}
              </Button>
            </div>
            <DeviceCertificatesList 
              enabled={cfg.enabled} 
              showSuccess={showSuccess} 
              showError={showError} 
            />
          </div>
        </div>

        {/* Generate Certificate Dialog */}
        {isTLSEnabled && (
          <GenerateCertDialog
            open={showGenerateCertDialog}
            onClose={() => setShowGenerateCertDialog(false)}
            onSuccess={() => {
              setShowGenerateCertDialog(false);
              refreshAll();
            }}
            forAIToolStack={false}
            enabled={cfg.enabled}
            showSuccess={showSuccess}
            showError={showError}
          />
        )}
      </>
    );
  };

  const renderDeviceBootstrapTab = () => {
    if (!bootstrap) {
      return (
        <div className="settings-card">
          <div className="settings-card-body">
            <div className="settings-loading">
              <p>{t('settings.deviceAccess.loading')}</p>
            </div>
          </div>
        </div>
      );
    }

    return (
      <div className="settings-card">
        <div className="settings-card-header">
          <div className="settings-card-header-main">
            <div className="settings-card-title-row">
              <h3 className="settings-card-title">{t('settings.deviceAccess.title')}</h3>
            </div>
            <p className="settings-card-subtitle">{t('settings.deviceAccess.subtitle')}</p>
          </div>
          <div className="settings-card-actions">
            <div className="settings-card-buttons">
              <Button
                type="button"
                variant="secondary"
                size="md"
                onClick={refreshAll}
                disabled={loading}
              >
                <Icon component={IoRefresh} /> {loading ? t('settings.refreshing') : t('settings.refresh')}
              </Button>
            </div>
          </div>
        </div>

        <div className="settings-card-body">
          {/* MQTT Broker Information */}
          <h4 className="settings-subsection-title">{t('settings.deviceAccess.mqttBroker.title')}</h4>
          
          {/* Built-in Broker */}
          {config && config.enabled && (
            <div className="settings-broker-connection-info">
              <div className="settings-broker-connection-header">
                <span className="settings-status-badge settings-status-badge-info">
                  {t('settings.deviceAccess.builtinBroker')}
                </span>
                <span className="settings-status-badge settings-status-badge-info">
                  {config.builtin_protocol.toUpperCase()}
                </span>
              </div>
              <div className="settings-broker-connection-content">
                <div className="settings-broker-info-row">
                  <span className="settings-broker-info-label">{t('settings.deviceAccess.mqttBroker.address')}:</span>
                  <code className="settings-code-inline">
                    {config.builtin_protocol}://{config.builtin_broker_host || bootstrap.broker_host}:{config.builtin_protocol === 'mqtts' ? (config.builtin_tls_port || 8883) : (config.builtin_tcp_port || 1883)}
                  </code>
                </div>
                {!config.builtin_allow_anonymous && config.builtin_username && (
                  <>
                    <div className="settings-broker-info-row">
                      <span className="settings-broker-info-label">{t('settings.deviceAccess.mqttBroker.username')}:</span>
                      <code className="settings-code-inline">{config.builtin_username}</code>
                    </div>
                    {config.builtin_password && (
                      <div className="settings-broker-info-row">
                        <span className="settings-broker-info-label">{t('settings.deviceAccess.mqttBroker.password')}:</span>
                        <div className="settings-broker-info-value-with-action">
                          <code className="settings-code-inline">••••••••</code>
                          <button
                            type="button"
                            className="settings-action-btn"
                            onClick={() => copyToClipboard(config.builtin_password || '', 'builtin-password')}
                            title={t('settings.deviceAccess.mqttBroker.copyPassword')}
                          >
                            <Icon component={IoCopyOutline} />
                            {copiedPassword === 'builtin-password' && <span className="settings-action-feedback">{t('settings.copied')}</span>}
                          </button>
                        </div>
                      </div>
                    )}
                  </>
                )}
                {config.builtin_tls_enabled && (
                  <>
                    {config.builtin_tls_ca_cert_path && (
                      <div className="settings-broker-info-row">
                        <span className="settings-broker-info-label">{t('settings.deviceAccess.mqttBroker.caCert')}:</span>
                        <div className="settings-broker-info-value-with-action">
                          <code className="settings-code-inline">{config.builtin_tls_ca_cert_path}</code>
                          <button
                            type="button"
                            className="settings-action-btn"
                            onClick={() => downloadCertificate('ca')}
                            title={t('settings.deviceAccess.mqttBroker.downloadCert')}
                          >
                            <Icon component={IoDownloadOutline} />
                          </button>
                        </div>
                      </div>
                    )}
                    {config.builtin_tls_require_client_cert && (
                      <div className="settings-broker-info-row">
                        <span className="settings-broker-info-label">{t('settings.deviceAccess.mqttBroker.clientCert')}:</span>
                        <div className="settings-broker-info-value-with-action">
                          <code className="settings-code-inline">{t('settings.deviceAccess.mqttBroker.clientCertRequired')}</code>
                          <button
                            type="button"
                            className="settings-action-btn"
                            onClick={() => downloadCertificate('client-cert')}
                            title={t('settings.deviceAccess.mqttBroker.downloadCert')}
                          >
                            <Icon component={IoDownloadOutline} />
                          </button>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          )}

          {/* External Brokers */}
          {externalBrokers.filter(b => b.enabled).map((broker) => (
            <div key={broker.id} className="settings-broker-connection-info" style={{ marginTop: config && config.enabled ? '8px' : '0' }}>
              <div className="settings-broker-connection-header">
                <span className="settings-status-badge settings-status-badge-info">
                  {t('settings.deviceAccess.externalBroker')}: {broker.name}
                </span>
                <span className="settings-status-badge settings-status-badge-info">
                  {broker.protocol.toUpperCase()}
                </span>
              </div>
              <div className="settings-broker-connection-content">
                <div className="settings-broker-info-row">
                  <span className="settings-broker-info-label">{t('settings.deviceAccess.mqttBroker.address')}:</span>
                  <code className="settings-code-inline">
                    {broker.protocol}://{broker.host}:{broker.port}
                  </code>
                </div>
                {broker.username && (
                  <>
                    <div className="settings-broker-info-row">
                      <span className="settings-broker-info-label">{t('settings.deviceAccess.mqttBroker.username')}:</span>
                      <code className="settings-code-inline">{broker.username}</code>
                    </div>
                    {broker.password && (
                      <div className="settings-broker-info-row">
                        <span className="settings-broker-info-label">{t('settings.deviceAccess.mqttBroker.password')}:</span>
                        <div className="settings-broker-info-value-with-action">
                          <code className="settings-code-inline">••••••••</code>
                          <button
                            type="button"
                            className="settings-action-btn"
                            onClick={() => copyToClipboard(broker.password || '', `external-password-${broker.id}`)}
                            title={t('settings.deviceAccess.mqttBroker.copyPassword')}
                          >
                            <Icon component={IoCopyOutline} />
                            {copiedPassword === `external-password-${broker.id}` && <span className="settings-action-feedback">{t('settings.copied')}</span>}
                          </button>
                        </div>
                      </div>
                    )}
                  </>
                )}
                {broker.tls_enabled && (
                  <>
                    {broker.tls_ca_cert_path && (
                      <div className="settings-broker-info-row">
                        <span className="settings-broker-info-label">{t('settings.deviceAccess.mqttBroker.caCert')}:</span>
                        <div className="settings-broker-info-value-with-action">
                          <code className="settings-code-inline">{broker.tls_ca_cert_path}</code>
                          <button
                            type="button"
                            className="settings-action-btn"
                            onClick={() => {
                              const filename = broker.tls_ca_cert_path?.split('/').pop() || '';
                              if (filename.startsWith('ca-')) {
                                // External broker CA cert
                                const name = filename.replace('.crt', '');
                                window.open(`${API_BASE_URL}/system/mqtt/tls/external/ca/${name}`, '_blank');
                              } else {
                                downloadCertificate('ca');
                              }
                            }}
                            title={t('settings.deviceAccess.mqttBroker.downloadCert')}
                          >
                            <Icon component={IoDownloadOutline} />
                          </button>
                        </div>
                      </div>
                    )}
                    {broker.tls_client_cert_path && (
                      <div className="settings-broker-info-row">
                        <span className="settings-broker-info-label">{t('settings.deviceAccess.mqttBroker.clientCert')}:</span>
                        <div className="settings-broker-info-value-with-action">
                          <code className="settings-code-inline">{broker.tls_client_cert_path}</code>
                          <button
                            type="button"
                            className="settings-action-btn"
                            onClick={() => {
                              const filename = broker.tls_client_cert_path?.split('/').pop() || '';
                              if (filename.startsWith('client-cert-')) {
                                // External broker client cert
                                const name = filename.replace('.crt', '');
                                window.open(`${API_BASE_URL}/system/mqtt/tls/external/client-cert/${name}`, '_blank');
                              } else if (filename.startsWith('client-')) {
                                // Device cert (client-{CN}.crt)
                                const cn = filename.replace('client-', '').replace('.crt', '');
                                window.open(`${API_BASE_URL}/system/mqtt/tls/device-cert/${encodeURIComponent(cn)}`, '_blank');
                              } else {
                                downloadCertificate('client-cert');
                              }
                            }}
                            title={t('settings.deviceAccess.mqttBroker.downloadCert')}
                          >
                            <Icon component={IoDownloadOutline} />
                          </button>
                        </div>
                      </div>
                    )}
                    {broker.tls_client_key_path && (
                      <div className="settings-broker-info-row">
                        <span className="settings-broker-info-label">{t('settings.deviceAccess.mqttBroker.clientKey')}:</span>
                        <div className="settings-broker-info-value-with-action">
                          <code className="settings-code-inline">{broker.tls_client_key_path}</code>
                          <button
                            type="button"
                            className="settings-action-btn"
                            onClick={() => {
                              const filename = broker.tls_client_key_path?.split('/').pop() || '';
                              if (filename.startsWith('client-key-')) {
                                // External broker client key
                                const name = filename.replace('.key', '');
                                window.open(`${API_BASE_URL}/system/mqtt/tls/external/client-key/${name}`, '_blank');
                              } else if (filename.startsWith('client-')) {
                                // Device key (client-{CN}.key)
                                const cn = filename.replace('client-', '').replace('.key', '');
                                window.open(`${API_BASE_URL}/system/mqtt/tls/device-key/${encodeURIComponent(cn)}`, '_blank');
                              } else {
                                downloadCertificate('client-key');
                              }
                            }}
                            title={t('settings.deviceAccess.mqttBroker.downloadKey')}
                          >
                            <Icon component={IoDownloadOutline} />
                          </button>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}

          {(!config || !config.enabled) && externalBrokers.filter(b => b.enabled).length === 0 && (
            <div className="settings-info-box">
              <div className="settings-info-icon">ℹ️</div>
              <div className="settings-info-content">
                <p className="settings-info-text">{t('settings.deviceAccess.mqttBroker.noBrokerConfigured')}</p>
              </div>
            </div>
          )}

          <div className="settings-section-divider"></div>

          {/* Topic Information */}
          <h4 className="settings-subsection-title">{t('settings.deviceAccess.topics.title')}</h4>
          
          <div className="settings-device-topics">
            {/* Image Upload Topic */}
            <div className="settings-device-topic-item">
              <div className="settings-device-topic-row">
                <span className="settings-device-topic-label">{t('settings.deviceAccess.topics.topic')}:</span>
                <code className="settings-code-inline">annotator/upload/{'{'}project_id{'}'}</code>
              </div>
              <div className="settings-device-topic-row">
                <span className="settings-device-topic-label">{t('settings.deviceAccess.topics.purpose')}:</span>
                <span>{t('settings.deviceAccess.topics.uploadPurpose')}</span>
              </div>
            </div>
            {/* Device Management Topics */}
            <div className="settings-device-topic-item">
              <div className="settings-device-topic-row">
                <span className="settings-device-topic-label">{t('settings.deviceAccess.topics.topic')}:</span>
                <code className="settings-code-inline">device/{'{'}device_id{'}'}/uplink</code>
              </div>
              <div className="settings-device-topic-row">
                <span className="settings-device-topic-label">{t('settings.deviceAccess.topics.purpose')}:</span>
                <span>{t('settings.deviceAccess.topics.reportPurpose')}</span>
              </div>
            </div>
            <div className="settings-device-topic-item">
              <div className="settings-device-topic-row">
                <span className="settings-device-topic-label">{t('settings.deviceAccess.topics.topic')}:</span>
                <code className="settings-code-inline">device/{'{'}device_id{'}'}/downlink</code>
              </div>
              <div className="settings-device-topic-row">
                <span className="settings-device-topic-label">{t('settings.deviceAccess.topics.purpose')}:</span>
                <span>{t('settings.deviceAccess.topics.commandPurpose')}</span>
              </div>
            </div>
          </div>

        </div>
      </div>
    );
  };

  return (
    <div className="project-selector">
      <div className="project-selector-content">
        <div className="section-header">
          <div>
            <h2>{t('settings.title')}</h2>
            <p className="dashboard-subtitle">{t('settings.subtitle')}</p>
          </div>
        </div>

        {/* Tab navigation */}
        <div className="settings-tabs">
          <button
            className={`settings-tab ${activeTab === 'mqtt' ? 'settings-tab-active' : ''}`}
            onClick={() => setActiveTab('mqtt')}
          >
            <span className="settings-tab-label">{t('settings.tabs.mqtt')}</span>
          </button>
          <button
            className={`settings-tab ${activeTab === 'certificates' ? 'settings-tab-active' : ''}`}
            onClick={() => setActiveTab('certificates')}
          >
            <span className="settings-tab-label">{t('settings.tabs.certificates', '证书管理')}</span>
          </button>
        </div>

        {/* Tab panel */}
        <div className="settings-tab-panel">
          {activeTab === 'mqtt' && renderMQTTConfigTab()}
          {activeTab === 'certificates' && renderCertificatesTab()}
        </div>
      </div>
      <Alert
        open={alertState.open}
        onOpenChange={closeAlert}
        title={alertState.title}
        message={alertState.message}
        type={alertState.type}
      />
      <ConfirmDialog
        open={confirmState.open}
        onOpenChange={(open) => {
          if (!open) {
            closeConfirm();
          }
        }}
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
};
