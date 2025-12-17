import { useState, useCallback } from 'react';
import { AlertType } from '../ui/Alert';

interface AlertState {
  open: boolean;
  title?: string;
  message: string;
  type: AlertType;
  confirmText?: string;
  onConfirm?: () => void;
}

export const useAlert = () => {
  const [alertState, setAlertState] = useState<AlertState>({
    open: false,
    message: '',
    type: 'info',
  });

  const showAlert = useCallback((
    message: string,
    type: AlertType = 'info',
    title?: string,
    confirmText?: string,
    onConfirm?: () => void
  ) => {
    setAlertState({
      open: true,
      message,
      type,
      title,
      confirmText,
      onConfirm,
    });
  }, []);

  const showSuccess = useCallback((message: string, title?: string) => {
    showAlert(message, 'success', title);
  }, [showAlert]);

  const showError = useCallback((message: string, title?: string) => {
    showAlert(message, 'error', title);
  }, [showAlert]);

  const showWarning = useCallback((message: string, title?: string) => {
    showAlert(message, 'warning', title);
  }, [showAlert]);

  const showInfo = useCallback((message: string, title?: string) => {
    showAlert(message, 'info', title);
  }, [showAlert]);

  const closeAlert = useCallback(() => {
    setAlertState(prev => ({ ...prev, open: false }));
  }, []);

  return {
    alertState,
    showAlert,
    showSuccess,
    showError,
    showWarning,
    showInfo,
    closeAlert,
  };
};
