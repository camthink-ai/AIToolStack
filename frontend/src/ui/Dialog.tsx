import React from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import './ui.css';

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export const DialogContent = React.forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ children, className, ...props }, ref) => (
  <DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className="ui-dialog-overlay" />
    <DialogPrimitive.Content
      ref={ref}
      className={['ui-dialog-content', className].filter(Boolean).join(' ')}
      {...props}
    >
      {children}
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
DialogContent.displayName = 'DialogContent';

export const DialogHeader: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({
  className,
  ...props
}) => (
  <div className={['ui-dialog-header', className].filter(Boolean).join(' ')} {...props} />
);

export const DialogTitle = React.forwardRef<
  HTMLHeadingElement,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={['ui-dialog-title', className].filter(Boolean).join(' ')}
    {...props}
  />
));
DialogTitle.displayName = 'DialogTitle';

export const DialogBody: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({
  className,
  ...props
}) => (
  <div className={['ui-dialog-body', className].filter(Boolean).join(' ')} {...props} />
);

export const DialogFooter: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({
  className,
  ...props
}) => (
  <div className={['ui-dialog-footer', className].filter(Boolean).join(' ')} {...props} />
);

