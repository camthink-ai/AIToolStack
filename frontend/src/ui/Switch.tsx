import React from 'react';

type SwitchProps = {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  label?: React.ReactNode;
};

export const Switch: React.FC<SwitchProps> = ({ checked, onCheckedChange, disabled, label }) => {
  return (
    <label className="toggle-group">
      <span className="toggle-switch">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onCheckedChange(e.target.checked)}
          disabled={disabled}
        />
        <span className="toggle-slider" />
      </span>
      {label && <span>{label}</span>}
    </label>
  );
};

