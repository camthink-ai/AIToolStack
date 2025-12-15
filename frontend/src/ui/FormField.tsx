import React from 'react';

interface FormFieldProps {
  label?: React.ReactNode;
  required?: boolean;
  htmlFor?: string;
  error?: string;
  helpText?: React.ReactNode;
  children: React.ReactNode;
}

export const FormField: React.FC<FormFieldProps> = ({
  label,
  required,
  htmlFor,
  error,
  helpText,
  children,
}) => {
  return (
    <div className="form-group">
      {label && (
        <label htmlFor={htmlFor}>
          {label} {required && <span className="required">*</span>}
        </label>
      )}
      {children}
      {helpText && <div className="form-help">{helpText}</div>}
      {error && <div className="form-error">{error}</div>}
    </div>
  );
};

