import React from 'react';
import { useTranslation } from 'react-i18next';
import { ToolType } from './AnnotationWorkbench';
import { IoHandLeftOutline, IoSquareOutline, IoShapesOutline, IoLocationOutline } from 'react-icons/io5';
import './ToolsBar.css';

interface ToolsBarProps {
  currentTool: ToolType;
  onToolChange: (tool: ToolType) => void;
}

// Icon component wrapper to resolve TypeScript type issues
const Icon: React.FC<{ component: React.ComponentType<any> }> = ({ component: Component }) => {
  return <Component />;
};

export const ToolsBar: React.FC<ToolsBarProps> = ({ currentTool, onToolChange }) => {
  const { t } = useTranslation();
  const tools: Array<{ type: ToolType; label: string; icon: React.ReactNode; shortcut: string }> = [
    { type: 'select', label: t('annotation.toolSelect'), icon: <Icon component={IoHandLeftOutline} />, shortcut: 'V' },
    { type: 'bbox', label: t('annotation.toolRectangle'), icon: <Icon component={IoSquareOutline} />, shortcut: 'R' },
    { type: 'polygon', label: t('annotation.toolPolygon'), icon: <Icon component={IoShapesOutline} />, shortcut: 'P' },
    { type: 'keypoint', label: t('annotation.toolKeypoint'), icon: <Icon component={IoLocationOutline} />, shortcut: 'K' },
  ];

  return (
    <div className="tools-bar">
      {tools.map((tool) => (
        <button
          key={tool.type}
          className={`tool-button ${currentTool === tool.type ? 'active' : ''}`}
          onClick={() => onToolChange(tool.type)}
          title={`${tool.label} (${tool.shortcut})`}
        >
          <span className="tool-icon">{tool.icon}</span>
          <span className="tool-label">{tool.label}</span>
          {/* <span className="tool-shortcut">{tool.shortcut}</span> */}
        </button>
      ))}
    </div>
  );
};

