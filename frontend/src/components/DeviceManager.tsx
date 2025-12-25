import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { IoLink, IoEye, IoTrash, IoChevronBack, IoChevronForward, IoAdd, IoInformationCircle, IoClose, IoSearch, IoCreate } from 'react-icons/io5';
import { Button } from '../ui/Button';
import { Alert } from '../ui/Alert';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { FormField } from '../ui/FormField';
import { Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, DialogClose } from '../ui/Dialog';
import { DeviceDetailViewer } from './DeviceDetailViewer';
import { useAlert } from '../hooks/useAlert';
import { useConfirm } from '../hooks/useConfirm';
import { useDeviceWebSocket } from '../hooks/useDeviceWebSocket';
import { API_BASE_URL } from '../config';
import './Dashboard.css';
import './SystemSettings.css';
import './TrainingPanel.css';

interface DeviceItem {
  id: string;
  name: string | null;
  type: string | null;
  model: string | null;
  serial_number: string | null;
  mac_address: string | null;
  project_ids: string[] | null;  // Changed from project_id to project_ids (array)
  status: string | null;
  last_seen: string | null;
  last_ip: string | null;
  firmware_version: string | null;
  hardware_version: string | null;
  power_supply_type: string | null;
  last_report: string | null;  // Raw JSON payload of last report
  extra_info: string | null;  // JSON string for arbitrary metadata
  // Optional: backend may later include device-specific topics
  uplink_topic?: string | null;
}

interface Project {
  id: string;
  name: string;
  description: string | null;
}

interface MQTTStatus {
  enabled: boolean;
  server_ip?: string | null;
  server_port?: number | null;
  broker?: string | null;
  port?: number | null;
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

interface MQTTConfigSummary {
  builtin_allow_anonymous?: boolean | null; // Whether anonymous access is allowed
  builtin_username?: string | null;
  builtin_password?: string | null;
  builtin_tls_enabled?: boolean | null;
  builtin_tls_ca_cert_path?: string | null;
  builtin_tls_client_cert_path?: string | null;
  builtin_tls_client_key_path?: string | null;
  builtin_tls_require_client_cert?: boolean | null; // mTLS enabled
  external_username?: string | null;
  external_password?: string | null;
  external_tls_enabled?: boolean | null;
  external_tls_ca_cert_path?: string | null;
  external_tls_client_cert_path?: string | null;
  external_tls_client_key_path?: string | null;
}

export const DeviceManager: React.FC = () => {
  const { t, i18n } = useTranslation();
  const { alertState, showSuccess, showError, closeAlert } = useAlert();
  const { confirmState, showConfirm, closeConfirm } = useConfirm();

  const [devices, setDevices] = useState<DeviceItem[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingDevices, setLoadingDevices] = useState(false);
  const [bindingDevice, setBindingDevice] = useState<DeviceItem | null>(null);
  const [selectedProjectIds, setSelectedProjectIds] = useState<Set<string>>(new Set());
  const [detailDevice, setDetailDevice] = useState<DeviceItem | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 10;
  // Filter states
  const [filterDeviceName, setFilterDeviceName] = useState<string>('');
  // Edit device name dialog state
  const [editingDevice, setEditingDevice] = useState<DeviceItem | null>(null);
  const [editingDeviceNameValue, setEditingDeviceNameValue] = useState<string>('');
  const [mqttStatus, setMqttStatus] = useState<MQTTStatus | null>(null);
  const [externalBrokers, setExternalBrokers] = useState<any[]>([]);
  const [mqttConfig, setMqttConfig] = useState<MQTTConfigSummary | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [connectionInfoDevice, setConnectionInfoDevice] = useState<DeviceItem | null>(null);
  // Helper function to generate 6-digit random code
  const generateRandomCode = useCallback(() => {
    return Math.floor(100000 + Math.random() * 900000).toString();
  }, []);

  const [showCreateDevice, setShowCreateDevice] = useState(false);
  const [newDevice, setNewDevice] = useState({
    // Default values to simplify user input
    name: `Camera-${generateRandomCode()}`,
    type: 'NE301',
    selectedBroker: 'builtin', // Default to builtin broker
    mqttProtocol: 'mqtt', // 'mqtt' or 'mqtts' for builtin broker
  });
  const [creatingDevice, setCreatingDevice] = useState(false);
  const [createdDevice, setCreatedDevice] = useState<DeviceItem | null>(null);
  const [createdDeviceBroker, setCreatedDeviceBroker] = useState<any>(null);

  useEffect(() => {
    void refreshAll();
  }, []);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    try {
      await Promise.all([loadDevices(), loadProjects()]);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadProjects = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/projects`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProjects(data as Project[]);
    } catch (e) {
      console.error('Failed to load projects:', e);
    }
  }, []);

  const loadMQTTStatus = useCallback(async () => {
    try {
      const [statusRes, brokersRes, configRes] = await Promise.all([
        fetch(`${API_BASE_URL}/mqtt/status`),
        fetch(`${API_BASE_URL}/system/mqtt/external-brokers`),
        fetch(`${API_BASE_URL}/system/mqtt/config`)
      ]);
      if (statusRes.ok) {
        const statusData = await statusRes.json();
        setMqttStatus(statusData as MQTTStatus);
      }
      if (brokersRes.ok) {
        const brokersData = await brokersRes.json();
        setExternalBrokers(brokersData || []);
      }
      if (configRes.ok) {
        const configData = await configRes.json();
        setMqttConfig({
          builtin_allow_anonymous: configData.builtin_allow_anonymous,
          builtin_username: configData.builtin_username,
          builtin_password: configData.builtin_password,
          builtin_tls_enabled: configData.builtin_tls_enabled,
          builtin_tls_ca_cert_path: configData.builtin_tls_ca_cert_path,
          builtin_tls_client_cert_path: configData.builtin_tls_client_cert_path,
          builtin_tls_client_key_path: configData.builtin_tls_client_key_path,
          builtin_tls_require_client_cert: configData.builtin_tls_require_client_cert,
          external_username: configData.external_username,
          external_password: configData.external_password,
          external_tls_enabled: configData.external_tls_enabled,
          external_tls_ca_cert_path: configData.external_tls_ca_cert_path,
          external_tls_client_cert_path: configData.external_tls_client_cert_path,
          external_tls_client_key_path: configData.external_tls_client_key_path,
        });
      }
    } catch (e) {
      console.error('Failed to load MQTT status:', e);
    }
  }, []);

  // Load MQTT status when create device dialog opens
  useEffect(() => {
    if (showCreateDevice) {
      void loadMQTTStatus();
    }
  }, [showCreateDevice, loadMQTTStatus]);

  const loadDevices = useCallback(async () => {
    try {
      setLoadingDevices(true);
      const res = await fetch(`${API_BASE_URL}/devices`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const devicesData = data as DeviceItem[];
      setDevices(devicesData);
      // If binding dialog is open, update the binding device with latest data
      if (bindingDevice) {
        const updatedDevice = devicesData.find(d => d.id === bindingDevice.id);
        if (updatedDevice) {
          setBindingDevice(updatedDevice);
          // Update selected project IDs to match the updated device
          const boundProjectIds = updatedDevice.project_ids && updatedDevice.project_ids.length > 0 
            ? new Set(updatedDevice.project_ids) 
            : new Set<string>();
          setSelectedProjectIds(boundProjectIds);
        }
      }
    } catch (e) {
      console.error('Failed to load devices:', e);
      showError(t('settings.deviceAccess.devices.loadFailed', '设备列表加载失败'));
    } finally {
      setLoadingDevices(false);
    }
  }, [showError, t, bindingDevice]);

  // Listen for device updates via WebSocket
  useDeviceWebSocket(
    useCallback((message: any) => {
      console.log('[DeviceManager] Received device update:', message);
      if (message.type === 'device_update') {
        // Refresh device list when device is updated
        // Use a small delay to ensure database commit has completed
        setTimeout(() => {
          loadDevices().catch((error) => {
            console.error('[DeviceManager] Failed to refresh device list:', error);
          });
        }, 300);
      }
    }, [loadDevices])
  );

  // Filter logic
  const filteredDevices = useMemo(() => {
    let filtered = devices;
    
    // Filter by device name (case-insensitive search)
    if (filterDeviceName.trim()) {
      const searchTerm = filterDeviceName.trim().toLowerCase();
      filtered = filtered.filter(d => 
        (d.name || '').toLowerCase().includes(searchTerm) ||
        (d.id || '').toLowerCase().includes(searchTerm)
      );
    }
    
    return filtered;
  }, [devices, filterDeviceName]);

  // Pagination logic (based on filtered devices)
  const pagedDevices = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filteredDevices.slice(start, start + pageSize);
  }, [filteredDevices, page, pageSize]);

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil((filteredDevices?.length || 0) / pageSize)),
    [filteredDevices, pageSize]
  );

  // Reset page when filters change
  useEffect(() => {
    setPage(1);
  }, [filterDeviceName]);

  const copyToClipboard = useCallback((text: string, key: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(null), 2000);
    });
  }, []);

  const formatTimeAgo = useCallback((dateString: string | null): string => {
    if (!dateString) return t('settings.deviceAccess.devices.lastSeenNever');
    try {
      const date = new Date(dateString);
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffSecs = Math.floor(diffMs / 1000);
      const diffMins = Math.floor(diffSecs / 60);
      const diffHours = Math.floor(diffMins / 60);
      const diffDays = Math.floor(diffHours / 24);

      if (diffSecs < 60) {
        return t('settings.deviceAccess.devices.lastSeenAgo', { time: `${diffSecs}${t('common.timeSeconds', '秒')}` });
      } else if (diffMins < 60) {
        return t('settings.deviceAccess.devices.lastSeenAgo', { time: `${diffMins}${t('common.timeMinutes', '分钟')}` });
      } else if (diffHours < 24) {
        return t('settings.deviceAccess.devices.lastSeenAgo', { time: `${diffHours}${t('common.timeHours', '小时')}` });
      } else if (diffDays < 7) {
        return t('settings.deviceAccess.devices.lastSeenAgo', { time: `${diffDays}${t('common.timeDays', '天')}` });
      } else {
        // For longer periods, show formatted date
        const locale = i18n.language === 'zh' ? 'zh-CN' : 'en-US';
        return date.toLocaleDateString(locale, {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit'
        });
      }
    } catch {
      return dateString;
    }
  }, [t, i18n]);

  const getStatusLabel = useCallback((status: string | null): string => {
    if (!status) return t('settings.deviceAccess.devices.status.unknown');
    const statusLower = status.toLowerCase();
    if (statusLower === 'online') return t('settings.deviceAccess.devices.status.online');
    if (statusLower === 'offline') return t('settings.deviceAccess.devices.status.offline');
    return t('settings.deviceAccess.devices.status.unknown');
  }, [t]);

  const getStatusClass = useCallback((status: string | null): string => {
    if (!status) return 'device-status-unknown';
    const statusLower = status.toLowerCase();
    if (statusLower === 'online') return 'device-status-online';
    if (statusLower === 'offline') return 'device-status-offline';
    return 'device-status-unknown';
  }, []);

  const handleOpenBindDialog = useCallback((device: DeviceItem) => {
    // Find the latest device info from the devices list to ensure we have fresh data
    const latestDevice = devices.find(d => d.id === device.id) || device;
    setBindingDevice(latestDevice);
    // Initialize with currently bound projects
    const boundProjectIds = latestDevice.project_ids && latestDevice.project_ids.length > 0 
      ? new Set(latestDevice.project_ids) 
      : new Set<string>();
    setSelectedProjectIds(boundProjectIds);
  }, [devices]);

  const handleBindProjects = useCallback(async () => {
    if (!bindingDevice) return;

    const currentProjectIds = bindingDevice.project_ids ? new Set(bindingDevice.project_ids) : new Set<string>();
    const selectedSet = selectedProjectIds;
    
    // Find projects to add and remove
    const toAdd = Array.from(selectedSet).filter(id => !currentProjectIds.has(id));
    const toRemove = Array.from(currentProjectIds).filter(id => !selectedSet.has(id));

    try {
      // Bind new projects
      for (const projectId of toAdd) {
        const res = await fetch(
          `${API_BASE_URL}/devices/${encodeURIComponent(bindingDevice.id)}/bind-project`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: projectId }),
          }
        );
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || `HTTP ${res.status}`);
        }
      }

      // Unbind removed projects
      for (const projectId of toRemove) {
        const res = await fetch(
          `${API_BASE_URL}/devices/${encodeURIComponent(bindingDevice.id)}/unbind-project`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: projectId }),
          }
        );
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || `HTTP ${res.status}`);
        }
      }

      // Refresh device list to get updated project bindings
      await loadDevices();
      
      // Close dialog and reset state
      setBindingDevice(null);
      setSelectedProjectIds(new Set());
      
      showSuccess(
        toAdd.length > 0 || toRemove.length > 0
          ? t('settings.deviceAccess.devices.bindSuccess', '项目绑定更新成功')
          : t('settings.deviceAccess.devices.noChange', '未发生变化')
      );
    } catch (e: any) {
      console.error('Failed to bind/unbind projects:', e);
      showError(
        t('settings.deviceAccess.devices.bindFailed', {
          error: e?.message || String(e),
        })
      );
    }
  }, [bindingDevice, selectedProjectIds, showSuccess, showError, t, loadDevices]);

  const handleToggleProject = useCallback((projectId: string) => {
    setSelectedProjectIds((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(projectId)) {
        newSet.delete(projectId);
      } else {
        newSet.add(projectId);
      }
      return newSet;
    });
  }, []);

  // Helper function to get project name by ID
  const getProjectName = useCallback((projectId: string): string => {
    const project = projects.find(p => p.id === projectId);
    return project ? (project.name || project.id) : projectId;
  }, [projects]);

  const handleUpdateDeviceName = useCallback(async (deviceId: string, newName: string) => {
    try {
      const res = await fetch(`${API_BASE_URL}/devices/${encodeURIComponent(deviceId)}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name: newName.trim() || null }),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({ detail: 'Failed to update device name' }));
        throw new Error(errorData.detail || 'Failed to update device name');
      }

      await loadDevices();
      showSuccess(t('device.update.nameSuccess', '设备名称更新成功'));
    } catch (e: any) {
      showError(
        t('device.update.nameFailed', '设备名称更新失败：{{error}}', {
          error: e?.message || String(e),
        })
      );
    }
  }, [showSuccess, showError, t, loadDevices]);

  const handleOpenEditNameDialog = useCallback((device: DeviceItem) => {
    setEditingDevice(device);
    setEditingDeviceNameValue(device.name || '');
  }, []);

  const handleCloseEditNameDialog = useCallback(() => {
    setEditingDevice(null);
    setEditingDeviceNameValue('');
  }, []);

  const handleSaveEditName = useCallback(async () => {
    if (!editingDevice) return;
    await handleUpdateDeviceName(editingDevice.id, editingDeviceNameValue);
    handleCloseEditNameDialog();
  }, [editingDevice, editingDeviceNameValue, handleUpdateDeviceName, handleCloseEditNameDialog]);

  const handleDeleteDevice = useCallback((device: DeviceItem) => {
    showConfirm(
      t('device.delete.confirmMessage', '确定要删除设备「{{name}}」吗？此操作不可恢复，将删除所有相关的上报数据。', { name: device.name || device.id }),
      async () => {
        try {
          const res = await fetch(
            `${API_BASE_URL}/devices/${encodeURIComponent(device.id)}`,
            {
              method: 'DELETE',
            }
          );
          if (!res.ok) {
            const text = await res.text();
            throw new Error(text || `HTTP ${res.status}`);
          }
          showSuccess(t('device.delete.success', '设备删除成功'));
          await loadDevices();
        } catch (e: any) {
          console.error('Failed to delete device:', e);
          showError(
            t('device.delete.failed', '设备删除失败：{{error}}', {
              error: e?.message || String(e),
            })
          );
        }
      },
      {
        title: t('device.delete.confirmTitle', '删除设备'),
        variant: 'danger',
      }
    );
  }, [showConfirm, showSuccess, showError, t, loadDevices]);

  const projectOptions = useMemo(() => {
    return projects.sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));
  }, [projects]);

  const handleCreateDevice = useCallback(async () => {
    // Basic validation: required fields
    if (!newDevice.name.trim()) {
      showError(t('device.create.nameRequired', '请填写设备名称'));
      return;
    }
    if (!newDevice.type) {
      showError(t('device.create.typeRequired', '请选择设备类型'));
      return;
    }

    setCreatingDevice(true);
    try {
      // Load MQTT status and config before creating device
      await loadMQTTStatus();
      
      // Determine broker info before creating device
      let brokerInfoForSave: any = null;
      if (newDevice.selectedBroker === 'builtin' && mqttStatus?.builtin?.enabled) {
        const selectedProtocol = newDevice.mqttProtocol || 'mqtt';
        const isTLS = selectedProtocol === 'mqtts';
        const isMTLS = isTLS && mqttConfig?.builtin_tls_require_client_cert === true;
        // Use correct port based on selected protocol: MQTT -> 1883, MQTTS -> 8883
        const port = isTLS ? 8883 : 1883;
        // Only include username/password if anonymous access is disabled
        const allowAnonymous = mqttConfig?.builtin_allow_anonymous !== false; // Default to true if not set
        brokerInfoForSave = {
          type: 'builtin',
          name: t('device.broker.builtin', '内置 Broker'),
          host: mqttStatus.builtin.host || mqttStatus.server_ip || 'localhost',
          port: port,
          protocol: selectedProtocol,
          username: allowAnonymous ? null : (mqttConfig?.builtin_username || null),
          password: allowAnonymous ? null : (mqttConfig?.builtin_password || null),
          tls_enabled: isTLS,
          mtls_enabled: isMTLS,
          device_cert_common_name: isMTLS ? null : null, // Will be set after device creation
        };
      } else if (newDevice.selectedBroker && newDevice.selectedBroker !== 'builtin' && externalBrokers.length > 0) {
        // Convert selectedBroker to number for comparison (select value is string, broker.id is number)
        const selectedBrokerId = typeof newDevice.selectedBroker === 'string' 
          ? parseInt(newDevice.selectedBroker, 10) 
          : newDevice.selectedBroker;
        const broker = externalBrokers.find((b: any) => b.id === selectedBrokerId);
        if (broker && broker.enabled) {
          // Use external broker's own configuration
          brokerInfoForSave = {
            type: 'external',
            name: broker.name || t('device.broker.external', '外部 Broker'),
            host: broker.host || '',
            port: broker.port || 1883,
            protocol: broker.protocol || 'mqtt',
            username: broker.username || null,
            password: broker.password || null,
            tls_enabled: broker.tls_enabled || false,
            tls_ca_cert_path: broker.tls_ca_cert_path || null,
            tls_client_cert_path: broker.tls_client_cert_path || null,
            tls_client_key_path: broker.tls_client_key_path || null,
            broker_id: broker.id, // Save broker ID for later retrieval
          };
        }
      }

      const payload: any = {};
      if (newDevice.name) payload.name = newDevice.name;
      if (newDevice.type) payload.type = newDevice.type;
      if (brokerInfoForSave) {
        payload.extra_info = JSON.stringify({ broker: brokerInfoForSave });
      }

      const res = await fetch(`${API_BASE_URL}/devices`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }

      const data = await res.json();
      const created: DeviceItem = {
        ...data,
        uplink_topic: data.uplink_topic || `device/${data.id}/uplink`,
      };
      
      // Determine selected broker info
      let brokerInfo: any = null;
      let deviceCertCommonName: string | null = null;
      
      if (newDevice.selectedBroker === 'builtin' && mqttStatus?.builtin?.enabled) {
        // Use user-selected protocol for builtin broker
        const selectedProtocol = newDevice.mqttProtocol || 'mqtt';
        const isTLS = selectedProtocol === 'mqtts';
        const isMTLS = isTLS && mqttConfig?.builtin_tls_require_client_cert === true;
        
        // Debug logging for certificate generation
        console.log('[DeviceManager] Certificate generation check:', {
          selectedProtocol,
          isTLS,
          builtin_tls_require_client_cert: mqttConfig?.builtin_tls_require_client_cert,
          isMTLS,
          deviceId: created.id,
        });
        
        // If mTLS is enabled, generate client certificate for this device
        if (isMTLS) {
          try {
            const deviceId = created.id; // Use device ID as CN
            console.log('[DeviceManager] Generating client certificate for device:', deviceId);
            const certRes = await fetch(
              `${API_BASE_URL}/system/mqtt/tls/generate-client-cert?common_name=${encodeURIComponent(deviceId)}&days=3650&for_aitoolstack=false`,
              { method: 'POST' }
            );
            if (!certRes.ok) {
              const errorData = await certRes.json().catch(() => ({ detail: 'Failed to generate certificate' }));
              console.error('[DeviceManager] Failed to generate device certificate:', errorData.detail);
              showError(t('device.create.certGenFailed', '客户端证书生成失败：{{error}}', { error: errorData.detail || 'Unknown error' }));
              // Don't set deviceCertCommonName if certificate generation failed
              deviceCertCommonName = null;
            } else {
              const certData = await certRes.json().catch(() => null);
              console.log('[DeviceManager] Client certificate generated successfully:', certData);
              // Only set deviceCertCommonName if certificate was successfully generated
              deviceCertCommonName = deviceId;
              // Trigger refresh event for DeviceCertificatesList component
              window.dispatchEvent(new CustomEvent('device-cert-generated'));
            }
          } catch (certError) {
            console.error('[DeviceManager] Error generating device certificate:', certError);
            showError(t('device.create.certGenError', '生成客户端证书时发生错误：{{error}}', { error: certError instanceof Error ? certError.message : String(certError) }));
            // Don't set deviceCertCommonName if certificate generation failed
            deviceCertCommonName = null;
          }
        } else {
          console.log('[DeviceManager] Certificate generation skipped:', {
            reason: !isTLS ? 'MQTTS not selected' : 'mTLS not enabled',
            isTLS,
            builtin_tls_require_client_cert: mqttConfig?.builtin_tls_require_client_cert,
          });
        }
        
        // Use correct port based on selected protocol: MQTT -> 1883, MQTTS -> 8883
        const port = isTLS ? 8883 : 1883;
        // Only include username/password if anonymous access is disabled
        const allowAnonymous = mqttConfig?.builtin_allow_anonymous !== false; // Default to true if not set
        brokerInfo = {
          type: 'builtin',
          name: t('device.broker.builtin', '内置 Broker'),
          host: mqttStatus.builtin.host || mqttStatus.server_ip || 'localhost',
          port: port,
          protocol: selectedProtocol, // Use user-selected protocol
          username: allowAnonymous ? null : (mqttConfig?.builtin_username || null),
          password: allowAnonymous ? null : (mqttConfig?.builtin_password || null),
          tls_enabled: isTLS,
          mtls_enabled: isMTLS,
          ca_cert_path: mqttConfig?.builtin_tls_ca_cert_path || null,
          device_cert_common_name: deviceCertCommonName,
        };
        
        // Update created device's extra_info with certificate info if certificate was generated
        if (deviceCertCommonName && created.extra_info) {
          try {
            const extraInfo = JSON.parse(created.extra_info);
            if (extraInfo.broker) {
              extraInfo.broker.device_cert_common_name = deviceCertCommonName;
              extraInfo.broker.port = port; // Update port based on protocol
              created.extra_info = JSON.stringify(extraInfo);
            }
          } catch (e) {
            console.error('Failed to update extra_info with certificate:', e);
          }
        }
      } else if (newDevice.selectedBroker && newDevice.selectedBroker !== 'builtin' && externalBrokers.length > 0) {
        // Convert selectedBroker to number for comparison (select value is string, broker.id is number)
        const selectedBrokerId = typeof newDevice.selectedBroker === 'string' 
          ? parseInt(newDevice.selectedBroker, 10) 
          : newDevice.selectedBroker;
        const broker = externalBrokers.find((b: any) => b.id === selectedBrokerId);
        if (broker && broker.enabled) {
          // Use external broker's own configuration
          brokerInfo = {
            type: 'external',
            name: broker.name || t('device.broker.external', '外部 Broker'),
            host: broker.host || '',
            port: broker.port || 1883,
            protocol: broker.protocol || 'mqtt',
            username: broker.username || null,
            password: broker.password || null,
            tls_enabled: broker.tls_enabled || false,
            tls_ca_cert_path: broker.tls_ca_cert_path || null,
            tls_client_cert_path: broker.tls_client_cert_path || null,
            tls_client_key_path: broker.tls_client_key_path || null,
            broker_id: broker.id, // Save broker ID for later retrieval
          };
        }
      }
      
      // If no broker selected or not found, use builtin as default
      // But only if user didn't explicitly select an external broker
      if (!brokerInfo && (!newDevice.selectedBroker || newDevice.selectedBroker === 'builtin') && mqttStatus?.builtin?.enabled) {
        const selectedProtocol = newDevice.mqttProtocol || 'mqtt';
        const isTLS = selectedProtocol === 'mqtts';
        const isMTLS = isTLS && mqttConfig?.builtin_tls_require_client_cert === true;
        
        // If mTLS is enabled, generate client certificate for this device
        if (isMTLS) {
          try {
            const deviceId = created.id;
            const certRes = await fetch(
              `${API_BASE_URL}/system/mqtt/tls/generate-client-cert?common_name=${encodeURIComponent(deviceId)}&days=3650&for_aitoolstack=false`,
              { method: 'POST' }
            );
            if (!certRes.ok) {
              const errorData = await certRes.json().catch(() => ({ detail: 'Failed to generate certificate' }));
              console.warn('Failed to generate device certificate:', errorData.detail);
              // Don't set deviceCertCommonName if certificate generation failed
              deviceCertCommonName = null;
            } else {
              // Only set deviceCertCommonName if certificate was successfully generated
              deviceCertCommonName = deviceId;
              // Trigger refresh event for DeviceCertificatesList component
              window.dispatchEvent(new CustomEvent('device-cert-generated'));
            }
          } catch (certError) {
            console.error('Error generating device certificate:', certError);
            // Don't set deviceCertCommonName if certificate generation failed
            deviceCertCommonName = null;
          }
        }
        
        // Use correct port based on selected protocol: MQTT -> 1883, MQTTS -> 8883
        const port = isTLS ? 8883 : 1883;
        // Only include username/password if anonymous access is disabled
        const allowAnonymous = mqttConfig?.builtin_allow_anonymous !== false; // Default to true if not set
        brokerInfo = {
          type: 'builtin',
          name: t('device.broker.builtin', '内置 Broker'),
          host: mqttStatus.builtin.host || mqttStatus.server_ip || 'localhost',
          port: port,
          protocol: selectedProtocol,
          username: allowAnonymous ? null : (mqttConfig?.builtin_username || null),
          password: allowAnonymous ? null : (mqttConfig?.builtin_password || null),
          tls_enabled: isTLS,
          mtls_enabled: isMTLS,
          ca_cert_path: mqttConfig?.builtin_tls_ca_cert_path || null,
          device_cert_common_name: deviceCertCommonName,
        };
        
        // Update created device's extra_info with certificate info if certificate was generated
        if (deviceCertCommonName && created.extra_info) {
          try {
            const extraInfo = JSON.parse(created.extra_info);
            if (extraInfo.broker) {
              extraInfo.broker.device_cert_common_name = deviceCertCommonName;
              extraInfo.broker.port = port; // Update port based on protocol
              created.extra_info = JSON.stringify(extraInfo);
            }
          } catch (e) {
            console.error('Failed to update extra_info with certificate:', e);
          }
        }
      }
      
      // Update created device with extra_info if broker info was saved
      // brokerInfo already contains device_cert_common_name if certificate was generated
      if (brokerInfo) {
        // If extra_info already exists, merge broker info instead of overwriting
        let extraInfo: any = {};
        if (created.extra_info) {
          try {
            extraInfo = JSON.parse(created.extra_info);
          } catch (e) {
            console.warn('Failed to parse existing extra_info, creating new:', e);
            extraInfo = {};
          }
        }
        
        // Merge broker info, ensuring device_cert_common_name is preserved
        // brokerInfo already has the correct device_cert_common_name from certificate generation
        extraInfo.broker = {
          ...extraInfo.broker, // Preserve any existing broker fields
          ...brokerInfo, // Override with current brokerInfo (includes device_cert_common_name)
        };
        
        created.extra_info = JSON.stringify(extraInfo);
      }
      
      // Debug log to verify brokerInfo contains device_cert_common_name
      console.log('[DeviceManager] Setting createdDeviceBroker:', {
        mtls_enabled: brokerInfo?.mtls_enabled,
        device_cert_common_name: brokerInfo?.device_cert_common_name,
        tls_enabled: brokerInfo?.tls_enabled,
        type: brokerInfo?.type,
        protocol: brokerInfo?.protocol,
      });
      
      setCreatedDevice(created);
      setCreatedDeviceBroker(brokerInfo);
      setShowCreateDevice(false);
      // Reset form with new random default name
      setNewDevice({
        name: `Camera-${generateRandomCode()}`,
        type: 'NE301',
        selectedBroker: 'builtin',
        mqttProtocol: 'mqtt',
      });
      
      // Refresh device list
      await loadDevices();
      
      showSuccess(t('device.create.success', '设备创建成功'));
    } catch (e: any) {
      console.error('Failed to create device:', e);
      showError(
        t('device.create.failed', '设备创建失败：{{error}}', {
          error: e?.message || String(e),
        })
      );
    } finally {
      setCreatingDevice(false);
    }
  }, [newDevice, showSuccess, showError, t, loadDevices, loadMQTTStatus, mqttStatus, mqttConfig, externalBrokers]);

  return (
    <div className="project-selector">
      <div className="project-selector-content">
        <section className="project-list-section">
          <div className="section-header">
            <div>
              <h2>{t('nav.devices', '设备')}</h2>
            </div>
            <div className="header-actions">
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={() => setShowCreateDevice(true)}
              >
                <IoAdd style={{ marginRight: '4px' }} />
                {t('device.create.button', '添加设备')}
              </Button>
            </div>
          </div>

        <div className="training-content">
          {/* Filter section */}
          <div style={{ 
            display: 'flex', 
            gap: '12px', 
            marginBottom: '16px', 
            alignItems: 'center',
            flexWrap: 'wrap'
          }}>
            <div style={{ 
              position: 'relative', 
              width: '300px' 
            }}>
              <input
                type="text"
                placeholder={t('device.filter.namePlaceholder', '搜索设备名称或ID...')}
                value={filterDeviceName}
                onChange={(e) => setFilterDeviceName(e.target.value)}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  paddingLeft: '36px',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '14px',
                  background: 'var(--bg-primary)',
                  color: 'var(--text-primary)',
                }}
              />
              <IoSearch style={{ 
                position: 'absolute',
                left: '12px',
                top: '50%',
                transform: 'translateY(-50%)',
                color: 'var(--text-secondary)',
                pointerEvents: 'none',
                fontSize: '16px'
              }} />
            </div>
            {filterDeviceName.trim() && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  setFilterDeviceName('');
                }}
              >
                {t('device.filter.clear', '清除筛选')}
              </Button>
            )}
          </div>

          <div className="training-list">
            {loadingDevices ? (
              <div className="training-empty">
                <p className="training-empty-desc">{t('common.loading', '加载中...')}</p>
              </div>
            ) : filteredDevices.length === 0 ? (
              <div className="training-empty">
                <p className="training-empty-desc">
                  {filterDeviceName.trim() 
                    ? t('device.filter.noResults', '没有找到匹配的设备')
                    : t('settings.deviceAccess.devices.empty')}
                </p>
              </div>
            ) : (
              <table className="training-table model-table device-table">
                <thead>
                  <tr>
                    <th className="col-id">{t('settings.deviceAccess.devices.columns.id')}</th>
                    <th className="col-name">{t('settings.deviceAccess.devices.columns.name')}</th>
                    <th className="col-type">{t('settings.deviceAccess.devices.columns.type')}</th>
                    <th className="col-status">{t('settings.deviceAccess.devices.columns.status')}</th>
                    <th className="col-firmware">{t('settings.deviceAccess.devices.columns.firmware')}</th>
                    <th className="col-mac">{t('settings.deviceAccess.devices.columns.mac')}</th>
                    <th className="col-lastSeen">{t('settings.deviceAccess.devices.columns.lastSeen')}</th>
                    <th className="col-project">{t('settings.deviceAccess.devices.columns.project')}</th>
                    <th className="col-actions">{t('settings.deviceAccess.devices.columns.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {pagedDevices.map((d) => (
                    <tr key={d.id}>
                      <td className="col-id">
                        <code>{d.id}</code>
                      </td>
                      <td className="col-name">
                        <span>{d.name || '-'}</span>
                      </td>
                      <td className="col-type">{d.type || '-'}</td>
                      <td className="col-status">
                        <span className={`device-status-badge ${getStatusClass(d.status)}`}>
                          {getStatusLabel(d.status)}
                        </span>
                      </td>
                      <td className="col-firmware">{d.firmware_version || '-'}</td>
                      <td className="col-mac">
                        {d.mac_address ? (
                          <code>{d.mac_address}</code>
                        ) : (
                          '-'
                        )}
                      </td>
                      <td className="col-lastSeen" style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {formatTimeAgo(d.last_seen)}
                      </td>
                      <td className="col-project">
                        {d.project_ids && d.project_ids.length > 0 ? (
                          <div className="project-badges-container">
                            {d.project_ids.slice(0, 3).map((pid) => (
                              <span key={pid} className="project-badge">
                                {getProjectName(pid)}
                              </span>
                            ))}
                            {d.project_ids.length > 3 && (
                              <span className="project-badge project-badge-more">
                                +{d.project_ids.length - 3}
                              </span>
                            )}
                          </div>
                        ) : (
                          <span className="text-secondary-sm">
                            {t('settings.deviceAccess.devices.unbound')}
                          </span>
                        )}
                      </td>
                      <td className="col-actions">
                        <div className="actions-cell">
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            className="action-btn"
                            onClick={() => setDetailDevice(d)}
                            title={t('device.detail.view', '查看详情')}
                          >
                            <IoEye />
                          </Button>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            className="action-btn"
                            onClick={() => {
                              setConnectionInfoDevice(d);
                              void loadMQTTStatus();
                            }}
                            title={t('device.access.viewConnectionInfo', '查看连接信息')}
                          >
                            <IoInformationCircle />
                          </Button>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            className="action-btn"
                            onClick={() => handleOpenEditNameDialog(d)}
                            title={t('device.update.editName', '编辑设备名称')}
                          >
                            <IoCreate />
                          </Button>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            className="action-btn"
                            onClick={() => handleOpenBindDialog(d)}
                            title={t('settings.deviceAccess.devices.bindAction')}
                          >
                            <IoLink />
                          </Button>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            className="action-btn"
                            onClick={() => handleDeleteDevice(d)}
                            title={t('device.delete.action', '删除设备')}
                          >
                            <IoTrash />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {filteredDevices.length > 0 && (
              <div className="table-pagination">
                <div className="pagination-info">
                  <span>
                    {((page - 1) * pageSize + 1).toString()} - {Math.min(page * pageSize, filteredDevices.length)} / {filteredDevices.length}
                    {filteredDevices.length !== devices.length && (
                      <span style={{ color: 'var(--text-secondary)', marginLeft: '8px' }}>
                        ({t('device.filter.total', '共 {{total}} 个设备', { total: devices.length })})
                      </span>
                    )}
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
        </div>
        </section>
      </div>

      {/* Bind Project Dialog */}
      <Dialog
        open={bindingDevice !== null}
        onOpenChange={(open) => {
          if (!open) {
            setBindingDevice(null);
            setSelectedProjectIds(new Set());
          }
        }}
      >
        <DialogContent className="config-modal">
          <DialogHeader className="config-modal-header">
            <DialogTitle asChild>
              <h3>
                {t('settings.deviceAccess.devices.bindAction')} - {bindingDevice?.name || bindingDevice?.id}
              </h3>
            </DialogTitle>
            <DialogClose className="close-btn">
              <IoClose />
            </DialogClose>
          </DialogHeader>
          <DialogBody className="config-modal-content">
            <div className="ui-form-stack">
              <FormField
                label={t('settings.deviceAccess.devices.columns.project', '绑定项目')}
              >
                <div style={{ marginBottom: '8px', fontSize: '13px', color: 'var(--text-secondary)' }}>
                  {t('settings.deviceAccess.devices.bindDescription', '可以选择多个项目，设备上报的图像数据将推送到所有绑定的项目')}
                </div>
                <div style={{ 
                  display: 'flex', 
                  flexDirection: 'column', 
                  gap: '8px',
                  maxHeight: '300px',
                  overflowY: 'auto',
                  padding: '8px',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--bg-primary)'
                }}>
                  {projectOptions.length === 0 ? (
                    <div style={{ padding: '16px', textAlign: 'center', color: 'var(--text-secondary)' }}>
                      {t('settings.deviceAccess.devices.noProjects', '暂无项目')}
                    </div>
                  ) : (
                    projectOptions.map((p) => (
                      <label
                        key={p.id}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                          padding: '8px 12px',
                          borderRadius: 'var(--radius-sm)',
                          cursor: 'pointer',
                          transition: 'background-color 0.2s',
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.backgroundColor = 'var(--bg-secondary)';
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.backgroundColor = 'transparent';
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={selectedProjectIds.has(p.id)}
                          onChange={() => handleToggleProject(p.id)}
                          style={{
                            width: '16px',
                            height: '16px',
                            cursor: 'pointer',
                            accentColor: 'var(--primary-color)',
                          }}
                        />
                        <div style={{ flex: 1 }}>
                          <div style={{ fontWeight: 500, fontSize: '14px' }}>
                            {p.name || p.id}
                          </div>
                          {p.description && (
                            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                              {p.description}
                            </div>
                          )}
                        </div>
                      </label>
                    ))
                  )}
                </div>
                {selectedProjectIds.size > 0 && (
                  <div style={{ 
                    marginTop: '8px', 
                    fontSize: '12px', 
                    color: 'var(--text-secondary)' 
                  }}>
                    {t('settings.deviceAccess.devices.selectedCount', '已选择 {{count}} 个项目', { count: selectedProjectIds.size })}
                  </div>
                )}
              </FormField>
            </div>
          </DialogBody>
          <DialogFooter className="config-modal-actions">
            <DialogClose asChild>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setBindingDevice(null);
                  setSelectedProjectIds(new Set());
                }}
              >
                {t('common.cancel', '取消')}
              </Button>
            </DialogClose>
            <Button
              type="button"
              variant="primary"
              onClick={handleBindProjects}
            >
              {t('common.save', '保存')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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

      {/* Create Device Dialog */}
      <Dialog
        open={showCreateDevice}
        onOpenChange={(open) => {
          if (!open) {
            setShowCreateDevice(false);
            setNewDevice({
              name: `Camera-${generateRandomCode()}`,
              type: 'NE301',
              selectedBroker: 'builtin',
              mqttProtocol: 'mqtt',
            });
          }
        }}
      >
        <DialogContent className="config-modal">
          <DialogHeader className="config-modal-header">
            <DialogTitle asChild>
              <h3>{t('device.create.title', '添加设备')}</h3>
            </DialogTitle>
            <DialogClose className="close-btn">
              <IoClose />
            </DialogClose>
          </DialogHeader>
          <DialogBody className="config-modal-content">
            <div className="ui-form-stack">
              <FormField label={t('device.create.name', '设备名称')} required>
                <input
                  type="text"
                  value={newDevice.name}
                  onChange={(e) => setNewDevice({ ...newDevice, name: e.target.value })}
                  placeholder={t('device.create.namePlaceholder', '请输入设备名称，例如「NE301 摄像头 01」')}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    border: '1px solid var(--border-color)',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '14px',
                  }}
                />
              </FormField>
              <FormField label={t('device.create.type', '设备类型')} required>
                <select
                  value={newDevice.type}
                  onChange={(e) => setNewDevice({ ...newDevice, type: e.target.value })}
                  className="device-form-select"
                >
                  <option value="NE101">NE101</option>
                  <option value="NE301">NE301</option>
                  <option value="Other">{t('device.create.typeOther', '其他')}</option>
                </select>
              </FormField>
              <FormField label={t('device.create.broker', '选择 MQTT Broker')}>
                <select
                  value={newDevice.selectedBroker}
                  onChange={(e) => setNewDevice({ ...newDevice, selectedBroker: e.target.value })}
                  className="device-form-select"
                >
                  {mqttStatus?.builtin?.enabled && (
                    <option value="builtin">
                      {t('device.broker.builtin', '内置 Broker')}
                    </option>
                  )}
                  {externalBrokers.filter((b: any) => b.enabled).map((broker: any) => (
                    <option key={broker.id} value={broker.id}>
                      {broker.name || t('device.broker.external', '外部 Broker')} ({broker.host}:{broker.port || 1883})
                    </option>
                  ))}
                </select>
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                  {t('device.create.brokerHint', '选择设备将连接的 MQTT Broker，用于生成连接配置信息')}
                </div>
              </FormField>

              {newDevice.selectedBroker === 'builtin' && (
                <FormField label={t('device.create.protocol', '连接协议')}>
                  <select
                    value={newDevice.mqttProtocol}
                    onChange={(e) => setNewDevice({ ...newDevice, mqttProtocol: e.target.value })}
                    className="device-form-select"
                  >
                    <option value="mqtt">{t('device.create.protocolMqtt', 'MQTT (未加密)')}</option>
                    <option value="mqtts">{t('device.create.protocolMqtts', 'MQTTS (TLS/SSL 加密)')}</option>
                  </select>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                    {t('device.create.protocolHint', '选择设备连接时使用的协议，MQTTS 提供加密连接')}
                  </div>
                </FormField>
              )}
            </div>
          </DialogBody>
          <DialogFooter className="config-modal-actions">
            <DialogClose asChild>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setShowCreateDevice(false);
                  setNewDevice({
                    name: `camera${generateRandomCode()}`,
                    type: 'NE301',
                    selectedBroker: 'builtin',
                    mqttProtocol: 'mqtt',
                  });
                }}
              >
                {t('common.cancel', '取消')}
              </Button>
            </DialogClose>
            <Button
              type="button"
              variant="primary"
              onClick={handleCreateDevice}
              disabled={creatingDevice}
            >
              {creatingDevice ? t('device.create.creating', '创建中...') : t('common.create', '创建')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Device Connection Info Dialog (shown after creation) */}
      <Dialog
        open={createdDevice !== null}
        onOpenChange={(open) => {
          if (!open) {
            setCreatedDevice(null);
          }
        }}
      >
        <DialogContent className="config-modal" style={{ maxWidth: '600px' }}>
          <DialogHeader className="config-modal-header">
            <DialogTitle>{t('device.connectionInfo.title', '设备连接信息')}</DialogTitle>
            <DialogClose className="close-btn">
              <IoClose />
            </DialogClose>
          </DialogHeader>
          <DialogBody className="config-modal-content">
            {createdDevice && (
              <div className="ui-form-stack">
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: 0 }}>
                  {t('device.connectionInfo.description', '请将以下信息配置到设备中，设备上报数据后将自动匹配到此设备。')}
                </p>
                
                <FormField label={t('device.connectionInfo.deviceId', '设备ID')}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <code style={{
                      flex: 1,
                      padding: '8px 12px',
                      background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '13px',
                      fontFamily: 'var(--font-family-mono)',
                    }}>
                      {createdDevice.id}
                    </code>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => copyToClipboard(createdDevice.id, 'created_device_id')}
                    >
                      {copiedKey === 'created_device_id'
                        ? t('device.connectionInfo.copied', '已复制')
                        : t('device.connectionInfo.copy', '复制')}
                    </Button>
                  </div>
                </FormField>

                <FormField label={t('device.connectionInfo.uplinkTopic', '上行 Topic（设备 → 平台）')}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <code style={{
                      flex: 1,
                      padding: '8px 12px',
                      background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '13px',
                      fontFamily: 'var(--font-family-mono)',
                    }}>
                      {createdDevice.uplink_topic || `device/${createdDevice.id}/uplink`}
                    </code>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => copyToClipboard(createdDevice.uplink_topic || `device/${createdDevice.id}/uplink`, 'created_uplink_topic')}
                    >
                      {copiedKey === 'created_uplink_topic'
                        ? t('device.connectionInfo.copied', '已复制')
                        : t('device.connectionInfo.copy', '复制')}
                    </Button>
                  </div>
                </FormField>

                <FormField label={t('device.connectionInfo.downlinkTopic', '下行 Topic（平台 → 设备）')}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <code style={{
                      flex: 1,
                      padding: '8px 12px',
                      background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-color)',
                      borderRadius: 'var(--radius-sm)',
                      fontSize: '13px',
                      fontFamily: 'var(--font-family-mono)',
                    }}>
                      device/{createdDevice.id}/downlink
                    </code>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => copyToClipboard(`device/${createdDevice.id}/downlink`, 'created_downlink_topic')}
                    >
                      {copiedKey === 'created_downlink_topic'
                        ? t('device.connectionInfo.copied', '已复制')
                        : t('device.connectionInfo.copy', '复制')}
                    </Button>
                  </div>
                </FormField>

                {createdDeviceBroker && (
                  <>
                    <div style={{ 
                      marginTop: 'var(--spacing-lg)', 
                      paddingTop: 'var(--spacing-lg)', 
                      borderTop: '1px solid var(--border-color)' 
                    }}>
                      <h4 style={{ 
                        margin: '0 0 var(--spacing-md) 0', 
                        fontSize: '14px', 
                        fontWeight: 600,
                        color: 'var(--text-primary)'
                      }}>
                        {t('device.connectionInfo.brokerConfig', 'MQTT Broker 配置')}
                      </h4>
                    </div>

                    <FormField label={t('device.connectionInfo.brokerName', 'Broker 名称')}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <code style={{
                          flex: 1,
                          padding: '8px 12px',
                          background: 'var(--bg-secondary)',
                          border: '1px solid var(--border-color)',
                          borderRadius: 'var(--radius-sm)',
                          fontSize: '13px',
                          fontFamily: 'var(--font-family-mono)',
                        }}>
                          {createdDeviceBroker.name}
                        </code>
                      </div>
                    </FormField>

                    <FormField label={t('device.connectionInfo.brokerAddress', 'Broker 地址')}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <code style={{
                          flex: 1,
                          padding: '8px 12px',
                          background: 'var(--bg-secondary)',
                          border: '1px solid var(--border-color)',
                          borderRadius: 'var(--radius-sm)',
                          fontSize: '13px',
                          fontFamily: 'var(--font-family-mono)',
                        }}>
                          {createdDeviceBroker.protocol}://{createdDeviceBroker.host}:{createdDeviceBroker.port}
                        </code>
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => copyToClipboard(`${createdDeviceBroker.protocol}://${createdDeviceBroker.host}:${createdDeviceBroker.port}`, 'created_broker_address')}
                        >
                          {copiedKey === 'created_broker_address'
                            ? t('device.connectionInfo.copied', '已复制')
                            : t('device.connectionInfo.copy', '复制')}
                        </Button>
                      </div>
                    </FormField>

                    {createdDeviceBroker.username && (
                      <FormField label={t('device.connectionInfo.username', '用户名')}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <code style={{
                            flex: 1,
                            padding: '8px 12px',
                            background: 'var(--bg-secondary)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '13px',
                            fontFamily: 'var(--font-family-mono)',
                          }}>
                            {createdDeviceBroker.username}
                          </code>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            onClick={() => copyToClipboard(createdDeviceBroker.username, 'created_username')}
                          >
                            {copiedKey === 'created_username'
                              ? t('device.connectionInfo.copied', '已复制')
                              : t('device.connectionInfo.copy', '复制')}
                          </Button>
                        </div>
                      </FormField>
                    )}

                    {createdDeviceBroker.password && (
                      <FormField label={t('device.connectionInfo.password', '密码')}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <code style={{
                            flex: 1,
                            padding: '8px 12px',
                            background: 'var(--bg-secondary)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '13px',
                            fontFamily: 'var(--font-family-mono)',
                          }}>
                            {'*'.repeat(createdDeviceBroker.password.length)}
                          </code>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            onClick={() => copyToClipboard(createdDeviceBroker.password, 'created_password')}
                          >
                            {copiedKey === 'created_password'
                              ? t('device.connectionInfo.copied', '已复制')
                              : t('device.connectionInfo.copy', '复制')}
                          </Button>
                        </div>
                      </FormField>
                    )}

                    {/* Only show TLS/SSL info if TLS is enabled (MQTTS) */}
                    {/* Do not show TLS/SSL info for MQTT (non-encrypted) connections */}
                    {createdDeviceBroker.tls_enabled === true && (
                      <FormField label={t('device.connectionInfo.tls', 'TLS/SSL')}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <code style={{
                            flex: 1,
                            padding: '8px 12px',
                            background: 'var(--bg-secondary)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '13px',
                            fontFamily: 'var(--font-family-mono)',
                          }}>
                            {t('device.connectionInfo.tlsEnabled', '已启用 (MQTTS)')}
                          </code>
                        </div>
                      </FormField>
                    )}

                    {/* Certificate downloads for MQTTS */}
                    {/* CA certificate is always needed for MQTTS (one-way TLS or mTLS) */}
                    {createdDeviceBroker.tls_enabled && createdDeviceBroker.type === 'builtin' && (
                      <>
                        <FormField label={t('device.connectionInfo.caCert', 'CA 证书')}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <code style={{
                              flex: 1,
                              padding: '8px 12px',
                              background: 'var(--bg-secondary)',
                              border: '1px solid var(--border-color)',
                              borderRadius: 'var(--radius-sm)',
                              fontSize: '13px',
                              fontFamily: 'var(--font-family-mono)',
                            }}>
                              {t('device.connectionInfo.caCertRequired', '需要 CA 证书进行服务器验证')}
                            </code>
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              onClick={() => {
                                window.open(`${API_BASE_URL}/system/mqtt/tls/ca`, '_blank');
                              }}
                            >
                              {t('device.connectionInfo.download', '下载')}
                            </Button>
                          </div>
                        </FormField>

                        {/* mTLS client certificate and key */}
                        {createdDeviceBroker.mtls_enabled && createdDeviceBroker.device_cert_common_name && (
                          <>
                            <FormField label={t('device.connectionInfo.clientCert', '客户端证书')}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <code style={{
                                  flex: 1,
                                  padding: '8px 12px',
                                  background: 'var(--bg-secondary)',
                                  border: '1px solid var(--border-color)',
                                  borderRadius: 'var(--radius-sm)',
                                  fontSize: '13px',
                                  fontFamily: 'var(--font-family-mono)',
                                }}>
                                  {t('device.connectionInfo.clientCertGenerated', '已为设备生成客户端证书')}
                                </code>
                                <Button
                                  type="button"
                                  variant="secondary"
                                  size="sm"
                                  onClick={() => {
                                    window.open(`${API_BASE_URL}/system/mqtt/tls/device-cert/${encodeURIComponent(createdDeviceBroker.device_cert_common_name)}`, '_blank');
                                  }}
                                >
                                  {t('device.connectionInfo.download', '下载')}
                                </Button>
                              </div>
                            </FormField>

                            <FormField label={t('device.connectionInfo.clientKey', '客户端私钥')}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <code style={{
                                  flex: 1,
                                  padding: '8px 12px',
                                  background: 'var(--bg-secondary)',
                                  border: '1px solid var(--border-color)',
                                  borderRadius: 'var(--radius-sm)',
                                  fontSize: '13px',
                                  fontFamily: 'var(--font-family-mono)',
                                }}>
                                  {t('device.connectionInfo.clientKeyGenerated', '已为设备生成客户端私钥')}
                                </code>
                                <Button
                                  type="button"
                                  variant="secondary"
                                  size="sm"
                                  onClick={() => {
                                    window.open(`${API_BASE_URL}/system/mqtt/tls/device-key/${encodeURIComponent(createdDeviceBroker.device_cert_common_name)}`, '_blank');
                                  }}
                                >
                                  {t('device.connectionInfo.download', '下载')}
                                </Button>
                              </div>
                            </FormField>
                          </>
                        )}
                      </>
                    )}

                    {/* External broker TLS certificate information */}
                    {createdDeviceBroker.tls_enabled === true && createdDeviceBroker.type === 'external' && (
                      <>
                        {createdDeviceBroker.tls_ca_cert_path && (() => {
                          // Extract filename from path (e.g., /mosquitto/config/certs/external/ca-1766456884.crt -> ca-1766456884)
                          const pathParts = createdDeviceBroker.tls_ca_cert_path.split('/');
                          const filename = pathParts[pathParts.length - 1] || '';
                          const filenameWithoutExt = filename.replace(/\.(crt|pem)$/i, '');
                          return (
                            <FormField label={t('device.connectionInfo.caCert', 'CA 证书')}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <code style={{
                                  flex: 1,
                                  padding: '8px 12px',
                                  background: 'var(--bg-secondary)',
                                  border: '1px solid var(--border-color)',
                                  borderRadius: 'var(--radius-sm)',
                                  fontSize: '13px',
                                  fontFamily: 'var(--font-family-mono)',
                                }}>
                                  {createdDeviceBroker.tls_ca_cert_path}
                                </code>
                                <Button
                                  type="button"
                                  variant="secondary"
                                  size="sm"
                                  onClick={() => {
                                    window.open(`${API_BASE_URL}/system/mqtt/tls/external/ca/${encodeURIComponent(filenameWithoutExt)}`, '_blank');
                                  }}
                                >
                                  {t('device.connectionInfo.download', '下载')}
                                </Button>
                              </div>
                            </FormField>
                          );
                        })()}

                        {createdDeviceBroker.tls_client_cert_path && (
                          <FormField label={t('device.connectionInfo.clientCert', '客户端证书')}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <code style={{
                                flex: 1,
                                padding: '8px 12px',
                                background: 'var(--bg-secondary)',
                                border: '1px solid var(--border-color)',
                                borderRadius: 'var(--radius-sm)',
                                fontSize: '13px',
                                fontFamily: 'var(--font-family-mono)',
                              }}>
                                {createdDeviceBroker.tls_client_cert_path}
                              </code>
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                onClick={() => copyToClipboard(createdDeviceBroker.tls_client_cert_path, 'external_client_cert')}
                              >
                                {copiedKey === 'external_client_cert'
                                  ? t('device.connectionInfo.copied', '已复制')
                                  : t('device.connectionInfo.copy', '复制')}
                              </Button>
                            </div>
                          </FormField>
                        )}

                        {createdDeviceBroker.tls_client_key_path && (
                          <FormField label={t('device.connectionInfo.clientKey', '客户端私钥')}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <code style={{
                                flex: 1,
                                padding: '8px 12px',
                                background: 'var(--bg-secondary)',
                                border: '1px solid var(--border-color)',
                                borderRadius: 'var(--radius-sm)',
                                fontSize: '13px',
                                fontFamily: 'var(--font-family-mono)',
                              }}>
                                {createdDeviceBroker.tls_client_key_path}
                              </code>
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                onClick={() => copyToClipboard(createdDeviceBroker.tls_client_key_path, 'external_client_key')}
                              >
                                {copiedKey === 'external_client_key'
                                  ? t('device.connectionInfo.copied', '已复制')
                                  : t('device.connectionInfo.copy', '复制')}
                              </Button>
                            </div>
                          </FormField>
                        )}
                      </>
                    )}
                  </>
                )}
              </div>
            )}
          </DialogBody>
          <DialogFooter className="config-modal-actions">
            <DialogClose asChild>
              <Button type="button" variant="primary">
                {t('common.close', '关闭')}
              </Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DeviceDetailViewer
        device={detailDevice}
        isOpen={detailDevice !== null}
        onClose={() => setDetailDevice(null)}
      />

      {/* Device Connection Info Dialog */}
      <Dialog
        open={connectionInfoDevice !== null}
        onOpenChange={(open) => {
          if (!open) {
            setConnectionInfoDevice(null);
          }
        }}
      >
        <DialogContent className="config-modal" style={{ maxWidth: '600px' }}>
          <DialogHeader className="config-modal-header">
            <DialogTitle>{t('device.connectionInfo.title', '设备连接信息')}</DialogTitle>
            <DialogClose className="close-btn">
              <IoClose />
            </DialogClose>
          </DialogHeader>
          <DialogBody className="config-modal-content">
            {connectionInfoDevice && (() => {
              // Try to read broker info from device's extra_info (saved during creation)
              let brokerInfo: any = null;
              
              if (connectionInfoDevice.extra_info) {
                try {
                  const extraInfo = JSON.parse(connectionInfoDevice.extra_info);
                  if (extraInfo.broker) {
                    brokerInfo = { ...extraInfo.broker }; // Create a copy to avoid mutations
                    // Debug log to verify device_cert_common_name is read correctly
                    console.log('[DeviceManager] Reading brokerInfo from extra_info:', {
                      mtls_enabled: brokerInfo.mtls_enabled,
                      device_cert_common_name: brokerInfo.device_cert_common_name,
                      tls_enabled: brokerInfo.tls_enabled,
                      type: brokerInfo.type,
                      protocol: brokerInfo.protocol,
                    });
                    // Don't auto-set device_cert_common_name - only use the value saved during creation
                    // If certificate generation failed, device_cert_common_name will be null
                  }
                } catch (e) {
                  console.error('Failed to parse device extra_info:', e);
                }
              }
              
              // Fallback: if no saved broker info, use current MQTT status (for backward compatibility)
              // Note: Built-in broker supports both MQTT (port 1883) and MQTTS (port 8883) simultaneously
              // The saved broker info contains the protocol and port selected during device creation
              // Also, if brokerInfo exists but device_cert_common_name is missing and mTLS is enabled,
              // try to use device ID as device_cert_common_name (for backward compatibility)
              if (brokerInfo && brokerInfo.type === 'builtin' && brokerInfo.mtls_enabled && !brokerInfo.device_cert_common_name) {
                // Try to use device ID as device_cert_common_name if certificate might exist
                brokerInfo.device_cert_common_name = connectionInfoDevice.id;
                console.log('[DeviceManager] Using device ID as device_cert_common_name for backward compatibility:', connectionInfoDevice.id);
              }
              
              if (!brokerInfo) {
                if (mqttStatus?.builtin?.enabled) {
                  // For backward compatibility, use broker's configured protocol
                  // But note: broker can support both protocols simultaneously
                  const protocol = mqttStatus.builtin?.protocol || 'mqtt';
                  const isTLS = protocol === 'mqtts';
                  const isMTLS = isTLS && mqttConfig?.builtin_tls_require_client_cert === true;
                  // Use correct port based on protocol: MQTT -> 1883, MQTTS -> 8883
                  const port = isTLS ? 8883 : 1883;
                  // Only include username/password if anonymous access is disabled
                  const allowAnonymous = mqttConfig?.builtin_allow_anonymous !== false; // Default to true if not set
                  brokerInfo = {
                    type: 'builtin',
                    name: t('device.broker.builtin', '内置 Broker'),
                    host: mqttStatus.builtin.host || mqttStatus.server_ip || 'localhost',
                    port: port,
                    protocol: protocol,
                    username: allowAnonymous ? null : (mqttConfig?.builtin_username || null),
                    password: allowAnonymous ? null : (mqttConfig?.builtin_password || null),
                    tls_enabled: isTLS,
                    mtls_enabled: isMTLS,
                    device_cert_common_name: null, // Don't auto-set - only use value from saved extra_info
                  };
                } else if (externalBrokers && externalBrokers.length > 0) {
                  // Try to find broker by saved broker_id first, then fallback to first enabled broker
                  let broker: any = null;
                  if (brokerInfo && brokerInfo.broker_id) {
                    broker = externalBrokers.find((b: any) => b.id === brokerInfo.broker_id);
                  }
                  if (!broker) {
                    broker = externalBrokers.find((b: any) => b.enabled);
                  }
                  if (broker) {
                    // Use external broker's own configuration
                    brokerInfo = {
                      type: 'external',
                      name: broker.name || t('device.broker.external', '外部 Broker'),
                      host: broker.host || '',
                      port: broker.port || 1883,
                      protocol: broker.protocol || 'mqtt',
                      username: broker.username || null,
                      password: broker.password || null,
                      tls_enabled: broker.tls_enabled || false,
                      tls_ca_cert_path: broker.tls_ca_cert_path || null,
                      tls_client_cert_path: broker.tls_client_cert_path || null,
                      tls_client_key_path: broker.tls_client_key_path || null,
                      broker_id: broker.id,
                    };
                  }
                }
              }

              // Final debug log before rendering
              if (brokerInfo) {
                console.log('[DeviceManager] Final brokerInfo before rendering:', {
                  mtls_enabled: brokerInfo.mtls_enabled,
                  device_cert_common_name: brokerInfo.device_cert_common_name,
                  tls_enabled: brokerInfo.tls_enabled,
                  type: brokerInfo.type,
                  protocol: brokerInfo.protocol,
                  willShowClientCert: brokerInfo.mtls_enabled && brokerInfo.device_cert_common_name,
                });
              }

              return (
                <div className="ui-form-stack">
                  <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: 0 }}>
                    {t('device.connectionInfo.description', '请将以下信息配置到设备中，设备上报数据后将自动匹配到此设备。')}
                  </p>
                  
                  <FormField label={t('device.connectionInfo.deviceId', '设备ID')}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <code style={{
                        flex: 1,
                        padding: '8px 12px',
                        background: 'var(--bg-secondary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 'var(--radius-sm)',
                        fontSize: '13px',
                        fontFamily: 'var(--font-family-mono)',
                      }}>
                        {connectionInfoDevice.id}
                      </code>
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => copyToClipboard(connectionInfoDevice.id, 'device_id')}
                      >
                        {copiedKey === 'device_id'
                          ? t('device.connectionInfo.copied', '已复制')
                          : t('device.connectionInfo.copy', '复制')}
                      </Button>
                    </div>
                  </FormField>

                  <FormField label={t('device.connectionInfo.uplinkTopic', '上行 Topic（设备 → 平台）')}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <code style={{
                        flex: 1,
                        padding: '8px 12px',
                        background: 'var(--bg-secondary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 'var(--radius-sm)',
                        fontSize: '13px',
                        fontFamily: 'var(--font-family-mono)',
                      }}>
                        {connectionInfoDevice.uplink_topic || `device/${connectionInfoDevice.id}/uplink`}
                      </code>
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => copyToClipboard(connectionInfoDevice.uplink_topic || `device/${connectionInfoDevice.id}/uplink`, 'uplink_device')}
                      >
                        {copiedKey === 'uplink_device'
                          ? t('device.connectionInfo.copied', '已复制')
                          : t('device.connectionInfo.copy', '复制')}
                      </Button>
                    </div>
                  </FormField>

                  <FormField label={t('device.connectionInfo.downlinkTopic', '下行 Topic（平台 → 设备）')}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <code style={{
                        flex: 1,
                        padding: '8px 12px',
                        background: 'var(--bg-secondary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 'var(--radius-sm)',
                        fontSize: '13px',
                        fontFamily: 'var(--font-family-mono)',
                      }}>
                        device/{connectionInfoDevice.id}/downlink
                      </code>
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => copyToClipboard(`device/${connectionInfoDevice.id}/downlink`, 'downlink_device')}
                      >
                        {copiedKey === 'downlink_device'
                          ? t('device.connectionInfo.copied', '已复制')
                          : t('device.connectionInfo.copy', '复制')}
                      </Button>
                    </div>
                  </FormField>

                  {brokerInfo && (
                    <>
                      <div style={{ 
                        marginTop: 'var(--spacing-lg)', 
                        paddingTop: 'var(--spacing-lg)', 
                        borderTop: '1px solid var(--border-color)' 
                      }}>
                        <h4 style={{ 
                          margin: '0 0 var(--spacing-md) 0', 
                          fontSize: '14px', 
                          fontWeight: 600,
                          color: 'var(--text-primary)'
                        }}>
                          {t('device.connectionInfo.brokerConfig', 'MQTT Broker 配置')}
                        </h4>
                      </div>

                      <FormField label={t('device.connectionInfo.brokerName', 'Broker 名称')}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <code style={{
                            flex: 1,
                            padding: '8px 12px',
                            background: 'var(--bg-secondary)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '13px',
                            fontFamily: 'var(--font-family-mono)',
                          }}>
                            {brokerInfo.name}
                          </code>
                        </div>
                      </FormField>

                      <FormField label={t('device.connectionInfo.brokerAddress', 'Broker 地址')}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <code style={{
                            flex: 1,
                            padding: '8px 12px',
                            background: 'var(--bg-secondary)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '13px',
                            fontFamily: 'var(--font-family-mono)',
                          }}>
                            {brokerInfo.protocol}://{brokerInfo.host}:{brokerInfo.port}
                          </code>
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            onClick={() => copyToClipboard(`${brokerInfo.protocol}://${brokerInfo.host}:${brokerInfo.port}`, 'broker_address')}
                          >
                            {copiedKey === 'broker_address'
                              ? t('device.connectionInfo.copied', '已复制')
                              : t('device.connectionInfo.copy', '复制')}
                          </Button>
                        </div>
                      </FormField>

                      {brokerInfo.username && (
                        <FormField label={t('device.connectionInfo.username', '用户名')}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <code style={{
                              flex: 1,
                              padding: '8px 12px',
                              background: 'var(--bg-secondary)',
                              border: '1px solid var(--border-color)',
                              borderRadius: 'var(--radius-sm)',
                              fontSize: '13px',
                              fontFamily: 'var(--font-family-mono)',
                            }}>
                              {brokerInfo.username}
                            </code>
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              onClick={() => copyToClipboard(brokerInfo.username, 'broker_username')}
                            >
                              {copiedKey === 'broker_username'
                                ? t('device.connectionInfo.copied', '已复制')
                                : t('device.connectionInfo.copy', '复制')}
                            </Button>
                          </div>
                        </FormField>
                      )}

                      {brokerInfo.password && (
                        <FormField label={t('device.connectionInfo.password', '密码')}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <code style={{
                              flex: 1,
                              padding: '8px 12px',
                              background: 'var(--bg-secondary)',
                              border: '1px solid var(--border-color)',
                              borderRadius: 'var(--radius-sm)',
                              fontSize: '13px',
                              fontFamily: 'var(--font-family-mono)',
                            }}>
                              {'*'.repeat(brokerInfo.password.length)}
                            </code>
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              onClick={() => copyToClipboard(brokerInfo.password, 'broker_password')}
                            >
                              {copiedKey === 'broker_password'
                                ? t('device.connectionInfo.copied', '已复制')
                                : t('device.connectionInfo.copy', '复制')}
                            </Button>
                          </div>
                        </FormField>
                      )}

                      {/* Only show TLS/SSL info if TLS is enabled (MQTTS) */}
                      {/* Do not show TLS/SSL info for MQTT (non-encrypted) connections */}
                      {brokerInfo.tls_enabled === true && (
                        <FormField label={t('device.connectionInfo.tls', 'TLS/SSL')}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <code style={{
                              flex: 1,
                              padding: '8px 12px',
                              background: 'var(--bg-secondary)',
                              border: '1px solid var(--border-color)',
                              borderRadius: 'var(--radius-sm)',
                              fontSize: '13px',
                              fontFamily: 'var(--font-family-mono)',
                            }}>
                              {t('device.connectionInfo.tlsEnabled', '已启用 (MQTTS)')}
                            </code>
                          </div>
                        </FormField>
                      )}

                      {/* Certificate downloads for MQTTS */}
                      {brokerInfo.tls_enabled && brokerInfo.type === 'builtin' && (
                        <>
                          <FormField label={t('device.connectionInfo.caCert', 'CA 证书')}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <code style={{
                                flex: 1,
                                padding: '8px 12px',
                                background: 'var(--bg-secondary)',
                                border: '1px solid var(--border-color)',
                                borderRadius: 'var(--radius-sm)',
                                fontSize: '13px',
                                fontFamily: 'var(--font-family-mono)',
                              }}>
                                {t('device.connectionInfo.caCertRequired', '需要 CA 证书进行服务器验证')}
                              </code>
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                onClick={() => {
                                  window.open(`${API_BASE_URL}/system/mqtt/tls/ca`, '_blank');
                                }}
                              >
                                {t('device.connectionInfo.download', '下载')}
                              </Button>
                            </div>
                          </FormField>

                          {/* mTLS client certificate and key */}
                          {brokerInfo.mtls_enabled && brokerInfo.device_cert_common_name && (
                            <>
                              <FormField label={t('device.connectionInfo.clientCert', '客户端证书')}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                  <code style={{
                                    flex: 1,
                                    padding: '8px 12px',
                                    background: 'var(--bg-secondary)',
                                    border: '1px solid var(--border-color)',
                                    borderRadius: 'var(--radius-sm)',
                                    fontSize: '13px',
                                    fontFamily: 'var(--font-family-mono)',
                                  }}>
                                    {t('device.connectionInfo.clientCertGenerated', '已为设备生成客户端证书')}
                                  </code>
                                  <Button
                                    type="button"
                                    variant="secondary"
                                    size="sm"
                                    onClick={() => {
                                      window.open(`${API_BASE_URL}/system/mqtt/tls/device-cert/${encodeURIComponent(brokerInfo.device_cert_common_name)}`, '_blank');
                                    }}
                                  >
                                    {t('device.connectionInfo.download', '下载')}
                                  </Button>
                                </div>
                              </FormField>

                              <FormField label={t('device.connectionInfo.clientKey', '客户端私钥')}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                  <code style={{
                                    flex: 1,
                                    padding: '8px 12px',
                                    background: 'var(--bg-secondary)',
                                    border: '1px solid var(--border-color)',
                                    borderRadius: 'var(--radius-sm)',
                                    fontSize: '13px',
                                    fontFamily: 'var(--font-family-mono)',
                                  }}>
                                    {t('device.connectionInfo.clientKeyGenerated', '已为设备生成客户端私钥')}
                                  </code>
                                  <Button
                                    type="button"
                                    variant="secondary"
                                    size="sm"
                                    onClick={() => {
                                      window.open(`${API_BASE_URL}/system/mqtt/tls/device-key/${encodeURIComponent(brokerInfo.device_cert_common_name)}`, '_blank');
                                    }}
                                  >
                                    {t('device.connectionInfo.download', '下载')}
                                  </Button>
                                </div>
                              </FormField>
                            </>
                          )}
                        </>
                      )}

                      {/* External broker TLS certificate information */}
                      {brokerInfo.tls_enabled === true && brokerInfo.type === 'external' && (
                        <>
                          {brokerInfo.tls_ca_cert_path && (() => {
                            // Extract filename from path (e.g., /mosquitto/config/certs/external/ca-1766456884.crt -> ca-1766456884)
                            const pathParts = brokerInfo.tls_ca_cert_path.split('/');
                            const filename = pathParts[pathParts.length - 1] || '';
                            const filenameWithoutExt = filename.replace(/\.(crt|pem)$/i, '');
                            return (
                              <FormField label={t('device.connectionInfo.caCert', 'CA 证书')}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                  <code style={{
                                    flex: 1,
                                    padding: '8px 12px',
                                    background: 'var(--bg-secondary)',
                                    border: '1px solid var(--border-color)',
                                    borderRadius: 'var(--radius-sm)',
                                    fontSize: '13px',
                                    fontFamily: 'var(--font-family-mono)',
                                  }}>
                                    {brokerInfo.tls_ca_cert_path}
                                  </code>
                                  <Button
                                    type="button"
                                    variant="secondary"
                                    size="sm"
                                    onClick={() => {
                                      window.open(`${API_BASE_URL}/system/mqtt/tls/external/ca/${encodeURIComponent(filenameWithoutExt)}`, '_blank');
                                    }}
                                  >
                                    {t('device.connectionInfo.download', '下载')}
                                  </Button>
                                </div>
                              </FormField>
                            );
                          })()}

                          {brokerInfo.tls_client_cert_path && (
                            <FormField label={t('device.connectionInfo.clientCert', '客户端证书')}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <code style={{
                                  flex: 1,
                                  padding: '8px 12px',
                                  background: 'var(--bg-secondary)',
                                  border: '1px solid var(--border-color)',
                                  borderRadius: 'var(--radius-sm)',
                                  fontSize: '13px',
                                  fontFamily: 'var(--font-family-mono)',
                                }}>
                                  {brokerInfo.tls_client_cert_path}
                                </code>
                                <Button
                                  type="button"
                                  variant="secondary"
                                  size="sm"
                                  onClick={() => copyToClipboard(brokerInfo.tls_client_cert_path, 'external_client_cert_list')}
                                >
                                  {copiedKey === 'external_client_cert_list'
                                    ? t('device.connectionInfo.copied', '已复制')
                                    : t('device.connectionInfo.copy', '复制')}
                                </Button>
                              </div>
                            </FormField>
                          )}

                          {brokerInfo.tls_client_key_path && (
                            <FormField label={t('device.connectionInfo.clientKey', '客户端私钥')}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <code style={{
                                  flex: 1,
                                  padding: '8px 12px',
                                  background: 'var(--bg-secondary)',
                                  border: '1px solid var(--border-color)',
                                  borderRadius: 'var(--radius-sm)',
                                  fontSize: '13px',
                                  fontFamily: 'var(--font-family-mono)',
                                }}>
                                  {brokerInfo.tls_client_key_path}
                                </code>
                                <Button
                                  type="button"
                                  variant="secondary"
                                  size="sm"
                                  onClick={() => copyToClipboard(brokerInfo.tls_client_key_path, 'external_client_key_list')}
                                >
                                  {copiedKey === 'external_client_key_list'
                                    ? t('device.connectionInfo.copied', '已复制')
                                    : t('device.connectionInfo.copy', '复制')}
                                </Button>
                              </div>
                            </FormField>
                          )}
                        </>
                      )}
                    </>
                  )}
                </div>
              );
            })()}
          </DialogBody>
          <DialogFooter className="config-modal-actions">
            <DialogClose asChild>
              <Button type="button" variant="primary">
                {t('common.close', '关闭')}
              </Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Device Name Dialog */}
      <Dialog
        open={editingDevice !== null}
        onOpenChange={(open) => {
          if (!open) {
            handleCloseEditNameDialog();
          }
        }}
      >
        <DialogContent className="config-modal">
          <DialogHeader className="config-modal-header">
            <DialogTitle asChild>
              <h3>{t('device.update.editName', '编辑设备名称')}</h3>
            </DialogTitle>
            <DialogClose className="close-btn" />
          </DialogHeader>
          <DialogBody className="config-modal-content">
            <div className="ui-form-stack">
              <FormField label={t('device.update.deviceName', '设备名称')}>
                <input
                  type="text"
                  value={editingDeviceNameValue}
                  onChange={(e) => setEditingDeviceNameValue(e.target.value)}
                  placeholder={t('device.update.namePlaceholder', '请输入设备名称')}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      void handleSaveEditName();
                    } else if (e.key === 'Escape') {
                      handleCloseEditNameDialog();
                    }
                  }}
                  autoFocus
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    border: '1px solid var(--border-color)',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '14px',
                    background: 'var(--bg-primary)',
                    color: 'var(--text-primary)',
                  }}
                />
              </FormField>
              {editingDevice && (
                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '8px' }}>
                  {t('device.update.deviceId', '设备ID')}: <code>{editingDevice.id}</code>
                </div>
              )}
            </div>
          </DialogBody>
          <DialogFooter className="config-modal-actions">
            <DialogClose asChild>
              <Button
                type="button"
                variant="secondary"
                onClick={handleCloseEditNameDialog}
              >
                {t('common.cancel', '取消')}
              </Button>
            </DialogClose>
            <Button
              type="button"
              variant="primary"
              onClick={handleSaveEditName}
            >
              {t('common.save', '保存')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};
