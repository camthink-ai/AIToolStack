import React from 'react';
import { useTranslation } from 'react-i18next';
import { 
  IoHardwareChip, 
  IoCloudUpload, 
  IoCreate, 
  IoFitness, 
  IoCube, 
  IoRocket,
  IoArrowForward
} from 'react-icons/io5';
import './Dashboard.css';

// Icon component wrapper
const Icon: React.FC<{ component: React.ComponentType<any>; className?: string }> = ({ 
  component: Component, 
  className 
}) => {
  return <Component className={className} />;
};

interface DashboardProps {
  onStartProject?: () => void;
}

export const Dashboard: React.FC<DashboardProps> = ({ onStartProject }) => {
  const { t } = useTranslation();

  const workflowSteps = [
    {
      id: 'step1',
      icon: IoHardwareChip,
      titleKey: 'dashboard.workflow.step1.title',
      descriptionKey: 'dashboard.workflow.step1.description'
    },
    {
      id: 'step2',
      icon: IoCloudUpload,
      titleKey: 'dashboard.workflow.step2.title',
      descriptionKey: 'dashboard.workflow.step2.description'
    },
    {
      id: 'step3',
      icon: IoCreate,
      titleKey: 'dashboard.workflow.step3.title',
      descriptionKey: 'dashboard.workflow.step3.description'
    },
    {
      id: 'step4',
      icon: IoFitness,
      titleKey: 'dashboard.workflow.step4.title',
      descriptionKey: 'dashboard.workflow.step4.description'
    },
    {
      id: 'step5',
      icon: IoCube,
      titleKey: 'dashboard.workflow.step5.title',
      descriptionKey: 'dashboard.workflow.step5.description'
    },
    {
      id: 'step6',
      icon: IoRocket,
      titleKey: 'dashboard.workflow.step6.title',
      descriptionKey: 'dashboard.workflow.step6.description'
    }
  ];


  return (
    <div className="dashboard">
      {/* 整合的工作流程部分 */}
      <div className="dashboard-section workflow-section">
        <div className="workflow-header">
          <div className="workflow-header-left">
            <h1 className="workflow-main-title">
              {t('dashboard.title', 'AI模型项目工作流')}
            </h1>
            <p className="workflow-main-subtitle">
              {t('dashboard.subtitle', '与NE301设备无缝对接，从数据采集到模型部署的完整解决方案')}
            </p>
          </div>
          <div className="workflow-header-buttons">
            {onStartProject && (
              <a
                className="dashboard-device-btn"
                onClick={onStartProject}
                style={{
                  background: 'var(--primary-color)',
                  color: 'var(--text-white)',
                  borderColor: 'var(--primary-color)'
                }}
              >
                {t('dashboard.startProject', '开始创建项目')}
              </a>
            )}
            <a 
              href="https://www.camthink.ai/product/neoeyes-301/" 
              target="_blank" 
              rel="noopener noreferrer"
              className="dashboard-device-btn"
            >
              {t('dashboard.getDevice', '获取NeoEyes NE301 AI Camera 设备')}
            </a>
          </div>
        </div>
        <div className="workflow-steps-header">
          <h2 className="workflow-steps-title">{t('dashboard.workflow.title', '完整工作流程')}</h2>
          <p className="workflow-steps-description">
            {t('dashboard.workflow.description', '从设备采集到模型部署的六个关键步骤')}
          </p>
        </div>
        <div className="workflow-container">
          {workflowSteps.map((step, index) => (
            <React.Fragment key={step.id}>
              <div className="workflow-step">
                <div className="workflow-step-icon">
                  <Icon component={step.icon} className="step-icon" />
                  <div className="step-number">{index + 1}</div>
                </div>
                <div className="workflow-step-content">
                  <h3 className="step-title">{t(step.titleKey)}</h3>
                  <p className="step-description">{t(step.descriptionKey)}</p>
                </div>
              </div>
              {index < workflowSteps.length - 1 && (
                <div className="workflow-connector">
                  <Icon component={IoArrowForward} className="connector-arrow" />
                </div>
              )}
            </React.Fragment>
          ))}
        </div>
      </div>

      {/* 扩展区域（为未来功能预留） */}
      <div className="dashboard-section extension-section">
        <div className="extension-placeholder">
          <p className="extension-text">
            {t('dashboard.extension', '更多功能即将推出...')}
          </p>
        </div>
      </div>
    </div>
  );
};
