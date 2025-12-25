import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { 
  IoFolderOpen,
  IoCube,
  IoHardwareChip,
  IoArrowForward,
  IoCheckmarkCircle,
  IoWarning,
  IoCloseCircle,
  IoFlash,
  IoLogoGithub,
  IoRocket
} from 'react-icons/io5';
import { API_BASE_URL } from '../config';
import './Dashboard.css';

interface DashboardProps {
  onStartProject?: () => void;
}

interface Project {
  id: string;
  name: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
}

interface ProjectStats extends Project {
  totalImages: number;
  labeledImages: number;
  annotationProgress: number; // 0-100
}

interface ModelStats {
  total: number;
  byType: Record<string, number>; // Model type distribution for pie chart
  classNames: Set<string>; // Unique class names for tags
}

interface DeviceStats {
  total: number;
  online: number;
  offline: number;
  unknown: number;
  byType: Record<string, number>;
}

export const Dashboard: React.FC<DashboardProps> = ({ onStartProject }) => {
  const { t } = useTranslation();
  const [projects, setProjects] = useState<ProjectStats[]>([]);
  const [modelStats, setModelStats] = useState<ModelStats>({ total: 0, byType: {}, classNames: new Set() });
  const [deviceStats, setDeviceStats] = useState<DeviceStats>({ total: 0, online: 0, offline: 0, unknown: 0, byType: {} });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboardData();
  }, []);

  const loadDashboardData = async () => {
    setLoading(true);
    try {
      // Fetch all data in parallel
      const [projectsRes, modelsRes, devicesRes] = await Promise.all([
        fetch(`${API_BASE_URL}/projects`),
        fetch(`${API_BASE_URL}/models`),
        fetch(`${API_BASE_URL}/devices`)
      ]);

      const projectsList: Project[] = projectsRes.ok ? await projectsRes.json() : [];
      const modelsList: any[] = modelsRes.ok ? await modelsRes.json() : [];
      const devicesList: any[] = devicesRes.ok ? await devicesRes.json() : [];

      // Calculate project statistics
      const projectsWithStats = await Promise.all(
        projectsList.map(async (project) => {
          try {
            const imagesRes = await fetch(`${API_BASE_URL}/projects/${project.id}/images`);
            const images = imagesRes.ok ? await imagesRes.json() : [];
            const totalImages = images.length;
            const labeledImages = images.filter((img: any) => img.status === 'LABELED').length;
            const annotationProgress = totalImages > 0 ? Math.round((labeledImages / totalImages) * 100) : 0;

            return {
              ...project,
              totalImages,
              labeledImages,
              annotationProgress
            };
          } catch (error) {
            console.error(`Failed to load stats for project ${project.id}:`, error);
            return {
              ...project,
              totalImages: 0,
              labeledImages: 0,
              annotationProgress: 0
            };
          }
        })
      );

      setProjects(projectsWithStats);

      // Calculate model statistics
      // 1. Model type distribution for pie chart
      const byType: Record<string, number> = {};
      // 2. Collect unique class names from all models for tags
      const allClasses = new Set<string>();
      
      modelsList.forEach((model) => {
        // Count model types for pie chart
        const type = model.model_type || 'unknown';
        byType[type] = (byType[type] || 0) + 1;
        
        // Collect class names for tags
        if (model.class_names && Array.isArray(model.class_names)) {
          model.class_names.forEach((className: string) => {
            if (className && className.trim()) {
              allClasses.add(className.trim());
            }
          });
        }
      });

      setModelStats({
        total: modelsList.length,
        byType,
        classNames: allClasses
      });

      // Calculate device statistics
      let online = 0;
      let offline = 0;
      let unknown = 0;
      const deviceByType: Record<string, number> = {};

      devicesList.forEach((device) => {
        const status = device.status?.toLowerCase() || 'unknown';
        if (status === 'online' || status === 'connected') {
          online++;
        } else if (status === 'offline' || status === 'disconnected') {
          offline++;
        } else {
          unknown++;
        }

        // Count by device type
        const deviceType = device.type || 'Other';
        deviceByType[deviceType] = (deviceByType[deviceType] || 0) + 1;
      });

      setDeviceStats({
        total: devicesList.length,
        online,
        offline,
        unknown,
        byType: deviceByType
      });
    } catch (error) {
      console.error('Failed to load dashboard data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleProjectClick = (project: ProjectStats) => {
    window.dispatchEvent(new CustomEvent('open-project', { detail: project }));
  };

  const maxImages = Math.max(...projects.map(p => p.totalImages), 1);

  // Pie chart for types (using SVG) - reusable for both models and devices
  const renderPieChart = (byType: Record<string, number>, total: number) => {
    const entries = Object.entries(byType);
    if (entries.length === 0 || total === 0) return null;

    const colors = ['#E05727', '#0E0808', '#E05727', '#0E0808', '#E05727', '#0E0808'];
    let cumulativePercent = 0;
    const radius = 30;
    const circumference = 2 * Math.PI * radius;

    return (
      <div className="pie-chart-container">
        <svg width="80" height="80" viewBox="0 0 80 80">
          <circle
            cx="40"
            cy="40"
            r={radius}
            fill="none"
            stroke="var(--border-color)"
            strokeWidth="10"
          />
          {entries.map(([type, count], index) => {
            const percent = (count / total) * 100;
            const strokeDasharray = (percent / 100) * circumference;
            const strokeDashoffset = -cumulativePercent * circumference / 100;
            cumulativePercent += percent;

            return (
              <circle
                key={type}
                cx="40"
                cy="40"
                r={radius}
                fill="none"
                stroke={colors[index % colors.length]}
                strokeWidth="10"
                strokeDasharray={`${strokeDasharray} ${circumference}`}
                strokeDashoffset={strokeDashoffset}
                transform="rotate(-90 40 40)"
                className="pie-segment"
              />
            );
          })}
        </svg>
        <div className="pie-chart-legend">
          {entries.slice(0, 4).map(([type, count], index) => (
            <div key={type} className="pie-legend-item">
              <div
                className="pie-legend-color"
                style={{ backgroundColor: colors[index % colors.length] }}
              />
              <span className="pie-legend-label">{type}</span>
              <span className="pie-legend-value">({count})</span>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="dashboard">
      {/* Top Banner - 产品介绍和快捷入口 */}
      <div className="dashboard-banner">
        <div className="banner-content">
          <div className="banner-text">
            <h1 className="banner-title">
              {t('dashboard.banner.title', 'AI模型项目管理平台')}
            </h1>
            <p className="banner-subtitle">
              {t('dashboard.banner.subtitle', '从数据采集、标注、训练到部署的完整工具链，专为NE301边缘AI设备设计')}
            </p>
          </div>
          <div className="banner-actions">
            {onStartProject && (
              <button className="banner-btn-primary" onClick={onStartProject}>
                <IoFolderOpen /> {t('dashboard.banner.createProject', '创建项目')}
              </button>
            )}
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                window.dispatchEvent(new CustomEvent('navigate-to-device'));
              }}
              className="banner-btn-secondary"
            >
              <IoHardwareChip /> {t('dashboard.banner.manageDevice', '设备管理')}
            </a>
            <a
              href="https://github.com/camthink-ai/AIToolStack"
              target="_blank"
              rel="noopener noreferrer"
              className="banner-btn-secondary banner-btn-github"
              title="GitHub Repository"
            >
              <IoLogoGithub />
            </a>
          </div>
        </div>
        <div className="banner-features">
          <div className="feature-item">
            <IoFlash className="feature-icon" />
            <span>{t('dashboard.banner.feature1', '端到端流程自动化')}</span>
          </div>
          <div className="feature-item">
            <IoCheckmarkCircle className="feature-icon" />
            <span>{t('dashboard.banner.feature2', 'NE301设备深度集成')}</span>
          </div>
          <div className="feature-item">
            <IoRocket className="feature-icon" />
            <span>{t('dashboard.banner.feature3', '快速模型部署')}</span>
          </div>
        </div>
      </div>

      {/* Main Content Grid - 三个模块一行显示 */}
      <div className="dashboard-main-grid">
        {/* Projects Horizontal Chart */}
        <div className="dashboard-section projects-chart-section">
          <div className="section-header">
            <h4 className="section-title">{t('dashboard.projects.title', '项目概览')}</h4>
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                window.dispatchEvent(new CustomEvent('navigate-to-projects'));
              }}
              className="section-link"
            >
              {t('common.viewAll', '查看全部')} <IoArrowForward />
            </a>
          </div>
          {loading ? (
            <div className="loading-placeholder">{t('common.loading', '加载中...')}</div>
          ) : projects.length > 0 ? (
            <div className="projects-chart">
              {projects.map((project) => (
                <div
                  key={project.id}
                  className="project-bar-item"
                  onClick={() => handleProjectClick(project)}
                >
                  <div className="project-bar-header">
                    <span className="project-bar-name">{project.name}</span>
                    <span className="project-bar-count">
                      {project.labeledImages} / {project.totalImages}
                    </span>
                  </div>
                  <div className="project-bar-chart">
                    {project.annotationProgress > 0 ? (
                      <div
                        className="project-bar-fill"
                        style={{
                          width: `${project.annotationProgress}%`,
                          backgroundColor: project.annotationProgress === 100
                            ? '#E05727'
                            : '#0E0808'
                        }}
                      />
                    ) : (
                      <div className="project-bar-fill-empty" />
                    )}
                  </div>
                  <div className="project-bar-footer">
                    <span className="project-bar-progress-text">
                      {t('dashboard.projects.annotationProgress', '标注进度')}: {project.annotationProgress}%
                    </span>
                    <IoArrowForward className="project-bar-arrow" />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <p>{t('dashboard.projects.noProjects', '暂无项目')}</p>
              {onStartProject && (
                <button className="empty-state-btn" onClick={onStartProject}>
                  {t('dashboard.banner.createProject', '创建项目')}
                </button>
              )}
            </div>
          )}
        </div>

        {/* Model Statistics */}
        <div className="dashboard-section stats-section">
          <div className="section-header">
            <h4 className="section-title">{t('dashboard.models.title', '模型统计')}</h4>
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                window.dispatchEvent(new CustomEvent('navigate-to-models'));
              }}
              className="section-link"
            >
              {t('common.viewAll', '查看全部')} <IoArrowForward />
            </a>
          </div>
          {loading ? (
            <div className="loading-placeholder">{t('common.loading', '加载中...')}</div>
          ) : modelStats.total > 0 ? (
            <div className="stats-content">
              <div className="stats-number">
                <IoCube className="stats-icon" />
                <div>
                  <div className="stats-value">{modelStats.total}</div>
                  <div className="stats-label">{t('dashboard.models.total', '模型总数')}</div>
                </div>
              </div>
              {Object.keys(modelStats.byType).length > 0 && renderPieChart(modelStats.byType, modelStats.total)}
              {modelStats.classNames.size > 0 && (
                <div className="stats-list">
                  {Array.from(modelStats.classNames).sort().map((className) => (
                    <div key={className} className="stats-list-item">
                      <span className="stats-list-label">{className}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="empty-state">
              <p>{t('dashboard.models.noModels', '暂无模型')}</p>
              <button
                className="empty-state-btn"
                onClick={(e) => {
                  e.preventDefault();
                  window.dispatchEvent(new CustomEvent('navigate-to-models'));
                }}
              >
                {t('dashboard.models.viewModels', '前往模型空间')} <IoArrowForward />
              </button>
            </div>
          )}
        </div>

        {/* Device Statistics */}
        <div className="dashboard-section stats-section">
          <div className="section-header">
            <h4 className="section-title">{t('dashboard.devices.title', '设备统计')}</h4>
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                window.dispatchEvent(new CustomEvent('navigate-to-device'));
              }}
              className="section-link"
            >
              {t('common.viewAll', '查看全部')} <IoArrowForward />
            </a>
          </div>
          {loading ? (
            <div className="loading-placeholder">{t('common.loading', '加载中...')}</div>
          ) : deviceStats.total > 0 ? (
            <div className="stats-content">
              <div className="stats-number">
                <IoHardwareChip className="stats-icon" />
                <div>
                  <div className="stats-value">{deviceStats.total}</div>
                  <div className="stats-label">{t('dashboard.devices.total', '设备总数')}</div>
                </div>
              </div>
              {Object.keys(deviceStats.byType).length > 0 && renderPieChart(deviceStats.byType, deviceStats.total)}
              <div className="device-status-list">
                <div className="device-status-item status-online">
                  <IoCheckmarkCircle />
                  <span className="device-status-label">
                    {t('dashboard.devices.online', '在线')}
                  </span>
                  <span className="device-status-value">{deviceStats.online}</span>
                </div>
                <div className="device-status-item status-offline">
                  <IoCloseCircle />
                  <span className="device-status-label">
                    {t('dashboard.devices.offline', '离线')}
                  </span>
                  <span className="device-status-value">{deviceStats.offline}</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="empty-state">
              <p>{t('dashboard.devices.noDevices', '暂无设备')}</p>
              <button
                className="empty-state-btn"
                onClick={(e) => {
                  e.preventDefault();
                  window.dispatchEvent(new CustomEvent('navigate-to-device'));
                }}
              >
                {t('dashboard.devices.viewDevices', '前往设备管理')} <IoArrowForward />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
