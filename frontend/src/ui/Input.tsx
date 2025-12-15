import React from 'react';

type InputSize = 'sm' | 'md' | 'lg';

const cn = (...args: Array<string | false | null | undefined>) =>
  args.filter(Boolean).join(' ');

const sizeClass: Record<InputSize, string> = {
  sm: 'input-sm',
  md: 'input-md',
  lg: 'input-lg',
};

type InputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> & {
  size?: InputSize;
};

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ size = 'md', className, ...props }, ref) => {
    const resolvedSize: InputSize = size ?? 'md';
    return (
      <input
        ref={ref}
        className={cn('input-base', sizeClass[resolvedSize], className)}
        {...props}
      />
    );
  }
);

Input.displayName = 'Input';

