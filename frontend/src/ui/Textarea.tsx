import React from 'react';

type TextareaSize = 'sm' | 'md' | 'lg';

const cn = (...args: Array<string | false | null | undefined>) =>
  args.filter(Boolean).join(' ');

const sizeClass: Record<TextareaSize, string> = {
  sm: 'input-sm',
  md: 'input-md',
  lg: 'input-lg',
};

type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement> & {
  size?: TextareaSize;
};

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ size = 'md', className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn('input-base', sizeClass[size], className)}
      {...props}
    />
  )
);

Textarea.displayName = 'Textarea';

