import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { IoAdd, IoLink, IoUnlink, IoClose, IoRefresh } from 'react-icons/io5';
import { Button } from '../ui/Button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, DialogClose } from '../ui/Dialog';
import { Select, SelectItem } from '../ui/Select';
import { Alert } from '../ui/Alert';
import { useAlert } from '../hooks/useAlert';
import { API_BASE_URL } from '../config';
import './SystemSettings.css';
import './TrainingPanel.css';

interface Device {
  id: string;
  name: string | null;
  type: string | null;
  status: string | null;
  project_ids: string[] | null;
}

interface DataSourceManagerProps {
  projectId: string;
  isOpen: boolean;
  onClose: () => void;
  onUpdate?: () => void;
}

export const DataSourceManager: React.FC<DataSourceManagerProps> = ({
  projectId,
  isOpen,
  onClose,
  onUpdate,
}) => {
  const { t } = useTranslation();
  const { alertState, showSuccess, showError, closeAlert } = useAlert();

  const [devices, setDevices] = useState<Device[]>([]);
  const [boundDevices, setBoundDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(false);
  const [showBindDialog, setShowBindDialog] = useState(false);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>('');

  const loadDevices = useCallback(async () => {
    setLoading(true);
    try {
      const [allDevicesRes, boundDevicesRes] = await Promise.all([
        fetch(`${API_BASE_URL}/devices`),
        fetch(`${API_BASE_URL}/projects/${projectId}/devices`),
      ]);

      if (!allDevicesRes.ok) throw new Error(`Failed to load devices: ${allDevicesRes.status}`);
      if (!boundDevicesRes.ok) throw new Error(`Failed to load bound devices: ${boundDevicesRes.status}`);

      const allDevices = (await allDevicesRes.json()) as Device[];
      const bound = (await boundDevicesRes.json()) as Device[];
      
      setDevices(allDevices);
      setBoundDevices(bound);
    } catch (e: any) {
      console.error('Failed to load devices:', e);
      showError(t('project.dataSource.loadFailed', '数据源加载失败'));
    } finally {
      setLoading(false);
    }
  }, [projectId, showError, t]);

  useEffect(() => {
    if (isOpen) {
      void loadDevices();
    }
  }, [isOpen, loadDevices]);

  const handleBindDevice = useCallback(async () => {
    if (!selectedDeviceId) {
      showError(t('project.dataSource.selectDevice', '请选择设备'));
      return;
    }

    try {
      const res = await fetch(
        `${API_BASE_URL}/devices/${encodeURIComponent(selectedDeviceId)}/bind-project`,
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

      showSuccess(t('project.dataSource.bindSuccess', '设备绑定成功'));
      setShowBindDialog(false);
      setSelectedDeviceId('');
      await loadDevices();
      if (onUpdate) {
        onUpdate();
      }
    } catch (e: any) {
      console.error('Failed to bind device:', e);
      showError(
        t('project.dataSource.bindFailed', {
          error: e?.message || String(e),
        }) || `绑定失败: ${e?.message || String(e)}`
      );
    }
  }, [selectedDeviceId, projectId, showSuccess, showError, t, loadDevices, onUpdate]);

  const handleUnbindDevice = useCallback(
    async (deviceId: string) => {
      try {
        const res = await fetch(
          `${API_BASE_URL}/devices/${encodeURIComponent(deviceId)}/unbind-project`,
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

        showSuccess(t('project.dataSource.unbindSuccess', '设备解绑成功'));
        await loadDevices();
        if (onUpdate) {
          onUpdate();
        }
      } catch (e: any) {
        console.error('Failed to unbind device:', e);
        showError(
          t('project.dataSource.unbindFailed', {
            error: e?.message || String(e),
          }) || `解绑失败: ${e?.message || String(e)}`
        );
      }
    },
    [projectId, showSuccess, showError, t, loadDevices, onUpdate]
  );

  const getStatusLabel = (status: string | null): string => {
    if (!status) return t('settings.deviceAccess.devices.status.unknown');
    const statusLower = status.toLowerCase();
    if (statusLower === 'online') return t('settings.deviceAccess.devices.status.online');
    if (statusLower === 'offline') return t('settings.deviceAccess.devices.status.offline');
    return t('settings.deviceAccess.devices.status.unknown');
  };

  const getStatusClass = (status: string | null): string => {
    if (!status) return 'device-status-unknown';
    const statusLower = status.toLowerCase();
    if (statusLower === 'online') return 'device-status-online';
    if (statusLower === 'offline') return 'device-status-offline';
    return 'device-status-unknown';
  };

  // Get available devices (not yet bound to this project)
  const availableDevices = devices.filter(
    (d) => !boundDevices.some((bd) => bd.id === d.id)
  );

  return (
    <>
      <Dialog open={isOpen} onOpenChange={onClose}>
        <DialogContent className="config-modal" style={{ width: '90vw', maxWidth: '800px', maxHeight: '90vh' }}>
          <DialogHeader className="config-modal-header">
            <DialogTitle>{t('project.dataSource.title', '数据源管理')}</DialogTitle>
            <DialogClose className="close-btn" />
          </DialogHeader>
          <DialogBody className="config-modal-content">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
              {/* Bound Devices List */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                  <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>
                    {t('project.dataSource.boundDevices', '已绑定的设备')} ({boundDevices.length})
                  </h3>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <Button
                      type="button"
                      variant="primary"
                      size="sm"
                      onClick={() => setShowBindDialog(true)}
                    >
                      <IoAdd style={{ marginRight: '4px' }} />
                      {t('project.dataSource.bindDevice', '绑定设备')}
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={loadDevices}
                      disabled={loading}
                    >
                      <IoRefresh />
                    </Button>
                  </div>
                </div>

                {loading ? (
                  <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-secondary)' }}>
                    {t('common.loading', '加载中...')}
                  </div>
                ) : boundDevices.length === 0 ? (
                  <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-secondary)' }}>
                    {t('project.dataSource.noBoundDevices', '暂无绑定的设备')}
                  </div>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table className="settings-table" style={{ width: '100%' }}>
                      <thead>
                        <tr>
                          <th style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                            {t('settings.deviceAccess.devices.columns.id')}
                          </th>
                          <th style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                            {t('settings.deviceAccess.devices.columns.name')}
                          </th>
                          <th style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                            {t('settings.deviceAccess.devices.columns.type')}
                          </th>
                          <th style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                            {t('settings.deviceAccess.devices.columns.status')}
                          </th>
                          <th style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                            {t('settings.deviceAccess.devices.columns.actions', '操作')}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {boundDevices.map((device) => (
                          <tr key={device.id}>
                            <td style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                              <code className="settings-code-inline">{device.id}</code>
                            </td>
                            <td style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                              {device.name || '-'}
                            </td>
                            <td style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                              {device.type || '-'}
                            </td>
                            <td style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                              <span className={`device-status-badge ${getStatusClass(device.status)}`}>
                                {getStatusLabel(device.status)}
                              </span>
                            </td>
                            <td style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                className="action-btn"
                                onClick={() => handleUnbindDevice(device.id)}
                                title={t('project.dataSource.unbind', '解绑')}
                              >
                                <IoUnlink />
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          </DialogBody>
          <DialogFooter className="config-modal-actions">
            <DialogClose asChild>
              <Button type="button" variant="secondary" onClick={onClose}>
                {t('common.close', '关闭')}
              </Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bind Device Dialog */}
      <Dialog open={showBindDialog} onOpenChange={setShowBindDialog}>
        <DialogContent className="config-modal">
          <DialogHeader className="config-modal-header">
            <DialogTitle>{t('project.dataSource.bindDevice', '绑定设备')}</DialogTitle>
            <DialogClose className="close-btn" />
          </DialogHeader>
          <DialogBody className="config-modal-content">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <label style={{ fontSize: '14px', fontWeight: 500 }}>
                {t('settings.deviceAccess.devices.columns.name', '设备')}
              </label>
              <Select
                value={selectedDeviceId}
                onValueChange={setSelectedDeviceId}
                placeholder={t('project.dataSource.selectDevice', '请选择设备')}
              >
                {availableDevices.map((device) => (
                  <SelectItem key={device.id} value={device.id}>
                    {device.name || device.id} ({device.type || '-'})
                  </SelectItem>
                ))}
              </Select>
              {availableDevices.length === 0 && (
                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>
                  {t('project.dataSource.noAvailableDevices', '没有可用的设备，所有设备已绑定到此项目')}
                </p>
              )}
            </div>
          </DialogBody>
          <DialogFooter className="config-modal-actions">
            <DialogClose asChild>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setShowBindDialog(false);
                  setSelectedDeviceId('');
                }}
              >
                {t('common.cancel', '取消')}
              </Button>
            </DialogClose>
            <Button
              type="button"
              variant="primary"
              onClick={handleBindDevice}
              disabled={!selectedDeviceId || availableDevices.length === 0}
            >
              {t('common.confirm', '确认')}
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
    </>
  );
};

