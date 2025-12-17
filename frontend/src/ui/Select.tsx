import React from 'react';
import * as RadixSelect from '@radix-ui/react-select';
import { IoChevronDown } from 'react-icons/io5';
import './ui.css';

type SelectProps = {
  value: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  children: React.ReactNode;
  placeholder?: string;
  className?: string;
};

export const Select: React.FC<SelectProps> = ({
  value,
  onValueChange,
  disabled,
  children,
  placeholder,
  className,
}) => {
  return (
    <RadixSelect.Root value={value} onValueChange={onValueChange} disabled={disabled}>
      <RadixSelect.Trigger className={['input-base select-trigger', className].filter(Boolean).join(' ')}>
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon className="select-icon">
          <IoChevronDown size={16} />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content className="select-content" position="popper">
          <RadixSelect.Viewport className="select-viewport">{children}</RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
};

export const SelectItem: React.FC<React.ComponentPropsWithoutRef<typeof RadixSelect.Item>> = ({
  children,
  className,
  ...props
}) => {
  return (
    <RadixSelect.Item
      className={['select-item', className].filter(Boolean).join(' ')}
      {...props}
    >
      <RadixSelect.ItemText>{children}</RadixSelect.ItemText>
    </RadixSelect.Item>
  );
};

