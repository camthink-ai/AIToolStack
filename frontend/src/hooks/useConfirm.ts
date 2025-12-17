import { useState, useCallback } from 'react';

interface ConfirmState {
  open: boolean;
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  onConfirm?: () => void;
  onCancel?: () => void;
  variant?: 'danger' | 'warning' | 'info';
}

export const useConfirm = () => {
  const [confirmState, setConfirmState] = useState<ConfirmState>({
    open: false,
    message: '',
    variant: 'warning',
  });

  const showConfirm = useCallback((
    message: string,
    onConfirm: () => void,
    options?: {
      title?: string;
      confirmText?: string;
      cancelText?: string;
      onCancel?: () => void;
      variant?: 'danger' | 'warning' | 'info';
    }
  ) => {
    setConfirmState({
      open: true,
      message,
      onConfirm,
      title: options?.title,
      confirmText: options?.confirmText,
      cancelText: options?.cancelText,
      onCancel: options?.onCancel,
      variant: options?.variant || 'warning',
    });
  }, []);

  const closeConfirm = useCallback(() => {
    setConfirmState(prev => ({ ...prev, open: false }));
  }, []);

  return {
    confirmState,
    showConfirm,
    closeConfirm,
  };
};
