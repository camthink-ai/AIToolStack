import React from 'react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from './LanguageSwitcher';
import { IoHome, IoPricetag, IoCube, IoSettings, IoServer } from 'react-icons/io5';
import './TopNavigation.css';

// Icon component wrapper
const Icon: React.FC<{ component: React.ComponentType<any> }> = ({ component: Component }) => {
  return <Component />;
};

type MenuItem = 'dashboard' | 'projects' | 'models' | 'device' | 'settings';

interface TopNavigationProps {
  activeMenu: MenuItem;
  onMenuChange: (menu: MenuItem) => void;
}

export const TopNavigation: React.FC<TopNavigationProps> = ({ activeMenu, onMenuChange }) => {
  const { t } = useTranslation();

  const menuItems: Array<{ key: MenuItem; label: string; icon: React.ReactNode }> = [
    { key: 'dashboard', label: t('nav.dashboard', '工作台'), icon: <Icon component={IoHome} /> },
    { key: 'projects', label: t('nav.projects', '项目管理'), icon: <Icon component={IoPricetag} /> },
    { key: 'models', label: t('nav.models', '模型空间'), icon: <Icon component={IoCube} /> },
    { key: 'device', label: t('nav.devices', '设备管理'), icon: <Icon component={IoServer} /> },
  ];

  return (
    <div className="top-navigation">
      <div className="nav-container">
        <div className="nav-left">
          <div className="nav-logo">
            <span className="logo-text">CamThink AI Tool Stack</span>
          </div>
          <nav className="nav-menu">
            {menuItems.map((item) => (
              <button
                key={item.key}
                className={`nav-menu-item ${activeMenu === item.key ? 'active' : ''}`}
                onClick={() => onMenuChange(item.key)}
              >
                <span className="nav-icon">{item.icon}</span>
                <span className="nav-label">{item.label}</span>
              </button>
            ))}
          </nav>
        </div>
        <div className="nav-right">
          <LanguageSwitcher />
          <button
            className={`nav-settings-btn ${activeMenu === 'settings' ? 'active' : ''}`}
            onClick={() => onMenuChange('settings')}
            title={t('nav.settings', '系统设置')}
          >
            <Icon component={IoSettings} />
          </button>
        </div>
      </div>
    </div>
  );
};
