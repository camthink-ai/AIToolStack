import React, { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { IoLanguage, IoChevronDown } from 'react-icons/io5';
import './LanguageSwitcher.css';
import { Button } from '../ui/Button';

export const LanguageSwitcher: React.FC = () => {
  const { i18n } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const languages = [
    { value: 'zh', label: '中文' },
    { value: 'en', label: 'English' },
  ];

  const currentLanguage = i18n.language;
  const currentLabel = languages.find(lang => lang.value === currentLanguage)?.label || '中文';

  const handleLanguageChange = (lng: string) => {
    i18n.changeLanguage(lng);
    localStorage.setItem('language', lng);
    setIsOpen(false);
  };

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => {
        document.removeEventListener('mousedown', handleClickOutside);
      };
    }
  }, [isOpen]);

  return (
    <div className="language-switcher" ref={dropdownRef}>
      <div className="language-dropdown">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="language-btn"
          onClick={() => setIsOpen(!isOpen)}
        >
          <IoLanguage />
          <span className="language-label">{currentLabel}</span>
          <IoChevronDown className={`language-chevron ${isOpen ? 'open' : ''}`} />
        </Button>
        {isOpen && (
          <div className="language-dropdown-menu">
            {languages.map((lang) => (
              <Button
                key={lang.value}
                type="button"
                variant="ghost"
                size="sm"
                className={`language-dropdown-item ${currentLanguage === lang.value ? 'active' : ''}`}
                onClick={() => handleLanguageChange(lang.value)}
              >
                {lang.label}
              </Button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
