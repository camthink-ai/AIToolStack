import React from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, DialogClose } from './Dialog';
import { Button } from './Button';
import { IoClose, IoAlertCircle } from 'react-icons/io5';
import './ui.css';

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  onConfirm: () => void;
  onCancel?: () => void;
  variant?: 'danger' | 'warning' | 'info';
  disabled?: boolean;
}

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  onOpenChange,
  title,
  message,
  confirmText,
  cancelText,
  onConfirm,
  onCancel,
  variant = 'warning',
  disabled = false,
}) => {
  const { t } = useTranslation();

  const handleConfirm = () => {
    onConfirm();
    onOpenChange(false);
  };

  const handleCancel = () => {
    if (onCancel) {
      onCancel();
    }
    onOpenChange(false);
  };

  const variantColor = variant === 'danger' 
    ? 'var(--error-color, #ef4444)' 
    : variant === 'warning'
    ? 'var(--warning-color, #f59e0b)'
    : 'var(--info-color, #3b82f6)';

  const displayTitle = title || t('common.confirm', '确认');
  const displayConfirmText = confirmText || t('common.confirm', '确认');
  const displayCancelText = cancelText || t('common.cancel', '取消');

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="confirm-dialog">
        <DialogHeader className="confirm-header">
          <div className="confirm-icon" style={{ color: '#000' }}>
            <IoAlertCircle size={24} />
          </div>
          <DialogTitle asChild>
            <h3 style={{ color: '#000' }}>{displayTitle}</h3>
          </DialogTitle>
          <DialogClose className="close-btn">
            <IoClose />
          </DialogClose>
        </DialogHeader>
        <DialogBody className="confirm-body">
          <p style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{message}</p>
        </DialogBody>
        <DialogFooter className="confirm-footer">
          <Button variant="secondary" onClick={handleCancel} disabled={disabled}>
            {displayCancelText}
          </Button>
          <Button 
            variant="primary" 
            onClick={handleConfirm}
            disabled={disabled}
          >
            {displayConfirmText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
