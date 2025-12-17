import React, { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, DialogClose } from './Dialog';
import { Button } from './Button';
import { IoClose, IoCheckmarkCircle, IoAlertCircle, IoInformationCircle, IoWarning } from 'react-icons/io5';
import './ui.css';

export type AlertType = 'success' | 'error' | 'warning' | 'info';

interface AlertProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  message: string;
  type?: AlertType;
  confirmText?: string;
  onConfirm?: () => void;
}

export const Alert: React.FC<AlertProps> = ({
  open,
  onOpenChange,
  title,
  message,
  type = 'info',
  confirmText,
  onConfirm,
}) => {
  const { t } = useTranslation();
  
  const typeConfig: Record<AlertType, { icon: React.ReactNode; color: string; titleKey: string }> = {
    success: {
      icon: <IoCheckmarkCircle size={20} />,
      color: 'var(--success-color, #10b981)',
      titleKey: 'common.success',
    },
    error: {
      icon: <IoAlertCircle size={24} />,
      color: 'var(--error-color, #ef4444)',
      titleKey: 'common.error',
    },
    warning: {
      icon: <IoWarning size={24} />,
      color: 'var(--warning-color, #f59e0b)',
      titleKey: 'common.warning',
    },
    info: {
      icon: <IoInformationCircle size={24} />,
      color: 'var(--info-color, #3b82f6)',
      titleKey: 'common.info',
    },
  };
  
  const config = typeConfig[type];
  const displayTitle = title || t(config.titleKey);
  const displayConfirmText = confirmText || t('common.confirm', '确定');

  const handleConfirm = () => {
    if (onConfirm) {
      onConfirm();
    }
    onOpenChange(false);
  };

  // Auto close after 3 seconds for success messages (toast style)
  useEffect(() => {
    if (open && type === 'success') {
      const timer = setTimeout(() => {
        onOpenChange(false);
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [open, type, onOpenChange]);

  // Success messages display as toast at the top
  if (type === 'success') {
    return (
      <div className={`alert-toast ${open ? 'alert-toast-open' : ''}`}>
        <div className="alert-toast-content" style={{ borderLeftColor: config.color }}>
          <div className="alert-toast-icon" style={{ color: config.color }}>
            {config.icon}
          </div>
          <div className="alert-toast-message">
            <p style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>{message}</p>
          </div>
          <button 
            className="alert-toast-close" 
            onClick={() => onOpenChange(false)}
            aria-label="Close"
          >
            <IoClose size={18} />
          </button>
        </div>
      </div>
    );
  }

  // Error and warning require confirmation, info doesn't
  const needsConfirmation = type === 'error' || type === 'warning';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="alert-dialog">
        <DialogHeader className="alert-header">
          <div className="alert-icon" style={{ color: config.color }}>
            {config.icon}
          </div>
          <DialogTitle asChild>
            <h3 style={{ color: config.color }}>{displayTitle}</h3>
          </DialogTitle>
          <DialogClose className="close-btn">
            <IoClose />
          </DialogClose>
        </DialogHeader>
        <DialogBody className="alert-body">
          <p style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{message}</p>
        </DialogBody>
        {needsConfirmation && (
          <DialogFooter className="alert-footer">
            <Button variant="primary" onClick={handleConfirm}>
              {displayConfirmText}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
};
