import React, { useState } from 'react';
import { IoChevronDown, IoChevronUp, IoHelpCircleOutline } from 'react-icons/io5';
import './ShortcutHelper.css';

// 图标组件包装器
const Icon: React.FC<{ component: React.ComponentType<any> }> = ({ component: Component }) => {
  return <Component />;
};

interface ShortcutItem {
  key: string;
  description: string;
}

const shortcuts: ShortcutItem[] = [
  { key: 'R', description: '矩形框工具' },
  { key: 'P', description: '多边形工具' },
  { key: 'V', description: '选择/移动工具' },
  { key: 'K', description: '关键点工具' },
  { key: 'A / ←', description: '上一张图像' },
  { key: 'D / →', description: '下一张图像' },
  { key: 'Space + 拖拽', description: '平移画布' },
  { key: 'H', description: '隐藏/显示标注' },
  { key: 'Del / Backspace', description: '删除选中标注' },
  { key: 'Ctrl+Z', description: '撤销' },
  { key: 'Ctrl+Shift+Z', description: '重做' },
  { key: 'Ctrl+S', description: '手动保存' },
  { key: 'Esc', description: '取消当前操作' },
  { key: 'Enter', description: '完成多边形绘制' },
];

export const ShortcutHelper: React.FC = () => {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className={`shortcut-helper ${isExpanded ? 'expanded' : ''}`}>
      <div
        className="shortcut-helper-toggle"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <Icon component={IoHelpCircleOutline} />
        <span className="shortcut-helper-title">快捷键</span>
        <Icon component={isExpanded ? IoChevronDown : IoChevronUp} />
      </div>
      
      {isExpanded && (
        <div className="shortcut-helper-content">
          {shortcuts.map((item, index) => (
            <div key={index} className="shortcut-item">
              <span className="shortcut-key">{item.key}</span>
              <span className="shortcut-description">{item.description}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
