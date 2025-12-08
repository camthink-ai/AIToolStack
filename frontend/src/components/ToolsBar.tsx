import React from 'react';
import { ToolType } from './AnnotationWorkbench';
import { IoHandLeftOutline, IoSquareOutline, IoShapesOutline, IoLocationOutline } from 'react-icons/io5';
import './ToolsBar.css';

interface ToolsBarProps {
  currentTool: ToolType;
  onToolChange: (tool: ToolType) => void;
}

// 图标组件包装器，解决 TypeScript 类型问题
const Icon: React.FC<{ component: React.ComponentType<any> }> = ({ component: Component }) => {
  return <Component />;
};

export const ToolsBar: React.FC<ToolsBarProps> = ({ currentTool, onToolChange }) => {
  const tools: Array<{ type: ToolType; label: string; icon: React.ReactNode; shortcut: string }> = [
    { type: 'select', label: '选择', icon: <Icon component={IoHandLeftOutline} />, shortcut: 'V' },
    { type: 'bbox', label: '矩形框', icon: <Icon component={IoSquareOutline} />, shortcut: 'R' },
    { type: 'polygon', label: '多边形', icon: <Icon component={IoShapesOutline} />, shortcut: 'P' },
    { type: 'keypoint', label: '关键点', icon: <Icon component={IoLocationOutline} />, shortcut: 'K' },
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
          <span className="tool-shortcut">{tool.shortcut}</span>
        </button>
      ))}
    </div>
  );
};

