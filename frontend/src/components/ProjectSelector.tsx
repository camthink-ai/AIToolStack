import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { API_BASE_URL } from '../config';
import { IoRefresh, IoAdd, IoFolder, IoTrash, IoClose, IoWarning } from 'react-icons/io5';
import './ProjectSelector.css';

// Icon component wrapper to resolve TypeScript type issues
const Icon: React.FC<{ component: React.ComponentType<any> }> = ({ component: Component }) => {
  return <Component />;
};

interface Project {
  id: string;
  name: string;
  description: string;
  created_at?: string;
  updated_at?: string;
}

interface ProjectSelectorProps {
  projects: Project[];
  onSelect: (project: Project) => void;
  onRefresh: () => void;
  onOpenTraining?: (projectId: string) => void;
}

export const ProjectSelector: React.FC<ProjectSelectorProps> = ({
  projects,
  onSelect,
  onRefresh,
  onOpenTraining
}) => {
  const { t, i18n } = useTranslation();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [projectDesc, setProjectDesc] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<{ project: Project | null; show: boolean }>({
    project: null,
    show: false
  });
  const [isDeleting, setIsDeleting] = useState(false);

  const handleCreateProject = async () => {
    if (!projectName.trim()) {
      alert(t('project.nameRequired'));
      return;
    }

    setIsCreating(true);
    try {
      const response = await fetch(`${API_BASE_URL}/projects`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name: projectName.trim(),
          description: projectDesc.trim(),
        }),
      });

      if (response.ok) {
        await response.json();
        setProjectName('');
        setProjectDesc('');
        setShowCreateModal(false);
        onRefresh();
        // Optional: automatically open newly created project
        // onSelect(newProject);
      } else {
        const errorData = await response.json().catch(() => ({ detail: t('project.createFailed') }));
        alert(errorData.detail || t('project.createFailed'));
      }
    } catch (error) {
      console.error('Failed to create project:', error);
      alert(`${t('project.createFailed')}: ${t('common.connectionError', 'Unable to connect to server')}`);
    } finally {
      setIsCreating(false);
    }
  };

  const handleCancelCreate = () => {
    setProjectName('');
    setProjectDesc('');
    setShowCreateModal(false);
  };

  const handleDeleteProject = (e: React.MouseEvent, project: Project) => {
    e.stopPropagation(); // Prevent triggering project selection
    setDeleteConfirm({ project, show: true });
  };

  const confirmDelete = async () => {
    if (!deleteConfirm.project) return;

    setIsDeleting(true);
    try {
      const response = await fetch(`${API_BASE_URL}/projects/${deleteConfirm.project.id}`, {
        method: 'DELETE',
      });

      if (response.ok) {
        setDeleteConfirm({ project: null, show: false });
        onRefresh();
      } else {
        const errorData = await response.json().catch(() => ({ detail: t('project.deleteFailed') }));
        alert(errorData.detail || t('project.deleteFailed'));
      }
    } catch (error) {
      console.error('Failed to delete project:', error);
      alert(`${t('project.deleteFailed')}: ${t('common.connectionError', 'Unable to connect to server')}`);
    } finally {
      setIsDeleting(false);
    }
  };

  const cancelDelete = () => {
    setDeleteConfirm({ project: null, show: false });
  };

  const formatDate = (dateString?: string) => {
    if (!dateString) return '';
    try {
      const date = new Date(dateString);
      const locale = i18n.language === 'zh' ? 'zh-CN' : 'en-US';
      return date.toLocaleDateString(locale, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      });
    } catch {
      return dateString;
    }
  };

  return (
    <div className="project-selector">
      <div className="project-selector-content">
        <div className="project-list-section">
          <div className="section-header">
            <h2>{t('project.title')}</h2>
            <div className="header-actions">
              <button onClick={onRefresh} className="btn-secondary">
                <Icon component={IoRefresh} /> {t('common.refresh')}
              </button>
              <button 
                onClick={() => setShowCreateModal(true)} 
                className="btn-create"
              >
                <Icon component={IoAdd} /> {t('project.createNew')}
              </button>
            </div>
          </div>
        </div>
            {projects.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon"><Icon component={IoFolder} /></div>
            <p>{t('project.noProjects')}</p>
                <button 
                  onClick={() => setShowCreateModal(true)} 
                  className="btn-primary"
                >
              {t('project.create')}
                </button>
              </div>
            ) : (
          <div className="project-list-section">
            <div className="project-grid">
              {projects.map((project) => (
                <div
                  key={project.id}
                  className="project-card"
                  onClick={() => onSelect(project)}
                >
                  <div className="project-card-header">
                    <h3>{project.name}</h3>
                    <div className="project-header-actions">
                      <button
                        className="btn-delete"
                        onClick={(e) => handleDeleteProject(e, project)}
                        title={t('project.delete')}
                      >
                        <Icon component={IoTrash} />
                      </button>
                    </div>
                  </div>
                  <p className="project-description">{project.description || t('common.noDescription', '无描述')}</p>
                  <div className="project-meta">
                    <div className="project-id">
                      {t('project.id', 'ID')}: <code>{project.id}</code>
                    </div>
                    {project.created_at && (
                      <div className="project-date">{t('common.createdAt', '创建时间')}: {formatDate(project.created_at)}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 创建项目弹窗 */}
      {showCreateModal && (
        <div className="modal-overlay" onClick={handleCancelCreate}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{t('project.createNew')}</h2>
              <button className="modal-close" onClick={handleCancelCreate}><Icon component={IoClose} /></button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label>{t('project.name')} <span className="required">*</span></label>
                <input
                  type="text"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder={t('project.name')}
                  disabled={isCreating}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && projectName.trim() && !isCreating) {
                      handleCreateProject();
                    }
                  }}
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label>{t('project.description')}</label>
                <textarea
                  value={projectDesc}
                  onChange={(e) => setProjectDesc(e.target.value)}
                  placeholder={t('project.descriptionOptional', '输入项目描述（可选）')}
                  disabled={isCreating}
                  rows={4}
                />
              </div>
            </div>
            <div className="modal-footer">
              <button
                onClick={handleCreateProject}
                disabled={isCreating || !projectName.trim()}
                className="btn-primary"
              >
                {isCreating ? t('common.loading') : t('project.create')}
              </button>
              <button
                onClick={handleCancelCreate}
                disabled={isCreating}
                className="btn-secondary"
              >
                {t('common.cancel')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认弹窗 */}
      {deleteConfirm.show && deleteConfirm.project && (
        <div className="modal-overlay" onClick={cancelDelete}>
          <div className="modal-content delete-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{t('project.delete')}</h2>
              <button className="modal-close" onClick={cancelDelete} disabled={isDeleting}><Icon component={IoClose} /></button>
            </div>
            <div className="modal-body">
              <div className="delete-warning">
                <div className="warning-icon"><Icon component={IoWarning} /></div>
                <p>
                  {t('project.confirmDelete', { name: deleteConfirm.project?.name || '' })}
                </p>
                <p className="warning-text">
                  {t('project.deleteWarning', '此操作不可恢复，将删除项目中的所有图像、标注和配置。')}
                </p>
              </div>
            </div>
            <div className="modal-footer">
              <button
                onClick={confirmDelete}
                disabled={isDeleting}
                className="btn-danger"
              >
                {isDeleting ? t('common.loading') : t('common.confirm')}
              </button>
              <button
                onClick={cancelDelete}
                disabled={isDeleting}
                className="btn-secondary"
              >
                {t('common.cancel')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

