import React, { useState } from 'react';
import { API_BASE_URL } from '../config';
import { IoRefresh, IoAdd, IoFolder, IoTrash, IoClose, IoWarning } from 'react-icons/io5';
import './ProjectSelector.css';

// 图标组件包装器，解决 TypeScript 类型问题
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
}

export const ProjectSelector: React.FC<ProjectSelectorProps> = ({
  projects,
  onSelect,
  onRefresh
}) => {
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
      alert('请输入项目名称');
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
        const newProject = await response.json();
        setProjectName('');
        setProjectDesc('');
        setShowCreateModal(false);
        onRefresh();
        // 可选：自动打开新创建的项目
        // onSelect(newProject);
      } else {
        const errorData = await response.json().catch(() => ({ detail: '创建项目失败' }));
        alert(errorData.detail || '创建项目失败');
      }
    } catch (error) {
      console.error('Failed to create project:', error);
      alert('创建项目失败：无法连接到服务器');
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
    e.stopPropagation(); // 防止触发项目选择
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
        const errorData = await response.json().catch(() => ({ detail: '删除项目失败' }));
        alert(errorData.detail || '删除项目失败');
      }
    } catch (error) {
      console.error('Failed to delete project:', error);
      alert('删除项目失败：无法连接到服务器');
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
      return date.toLocaleDateString('zh-CN', {
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
      <div className="project-selector-header">
        <h1>NeoEyesTool</h1>
        <p>图像标注工具与 IoT 集成系统</p>
      </div>

      <div className="project-selector-content">
        <div className="project-list-section">
          <div className="section-header">
            <h2>项目列表</h2>
            <div className="header-actions">
              <button onClick={onRefresh} className="btn-secondary">
                <Icon component={IoRefresh} /> 刷新
              </button>
              <button 
                onClick={() => setShowCreateModal(true)} 
                className="btn-create"
              >
                <Icon component={IoAdd} /> 创建新项目
              </button>
            </div>
          </div>
          <div className="project-grid">
            {projects.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon"><Icon component={IoFolder} /></div>
                <p>暂无项目</p>
                <button 
                  onClick={() => setShowCreateModal(true)} 
                  className="btn-primary"
                >
                  创建第一个项目
                </button>
              </div>
            ) : (
              projects.map((project) => (
                <div
                  key={project.id}
                  className="project-card"
                  onClick={() => onSelect(project)}
                >
                  <div className="project-card-header">
                    <h3>{project.name}</h3>
                    <div className="project-header-actions">
                      <span className="project-status">未开始</span>
                      <button
                        className="btn-delete"
                        onClick={(e) => handleDeleteProject(e, project)}
                        title="删除项目"
                      >
                        <Icon component={IoTrash} />
                      </button>
                    </div>
                  </div>
                  <p className="project-description">{project.description || '无描述'}</p>
                  <div className="project-meta">
                    <div className="project-id">ID: {project.id.substring(0, 8)}...</div>
                    {project.created_at && (
                      <div className="project-date">创建时间: {formatDate(project.created_at)}</div>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* 创建项目弹窗 */}
      {showCreateModal && (
        <div className="modal-overlay" onClick={handleCancelCreate}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>创建新项目</h2>
              <button className="modal-close" onClick={handleCancelCreate}><Icon component={IoClose} /></button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label>项目名称 <span className="required">*</span></label>
                <input
                  type="text"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder="输入项目名称"
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
                <label>项目描述</label>
                <textarea
                  value={projectDesc}
                  onChange={(e) => setProjectDesc(e.target.value)}
                  placeholder="输入项目描述（可选）"
                  disabled={isCreating}
                  rows={4}
                />
              </div>
            </div>
            <div className="modal-footer">
              <button
                onClick={handleCancelCreate}
                className="btn-secondary"
                disabled={isCreating}
              >
                取消
              </button>
              <button
                onClick={handleCreateProject}
                disabled={isCreating || !projectName.trim()}
                className="btn-primary"
              >
                {isCreating ? '创建中...' : '创建项目'}
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
              <h2>确认删除项目</h2>
              <button className="modal-close" onClick={cancelDelete} disabled={isDeleting}><Icon component={IoClose} /></button>
            </div>
            <div className="modal-body">
              <div className="delete-warning">
                <div className="warning-icon"><Icon component={IoWarning} /></div>
                <p>
                  确定要删除项目 <strong>"{deleteConfirm.project.name}"</strong> 吗？
                </p>
                <p className="warning-text">
                  此操作不可恢复，将删除项目中的所有图像、标注和配置。
                </p>
              </div>
            </div>
            <div className="modal-footer">
              <button
                onClick={cancelDelete}
                className="btn-secondary"
                disabled={isDeleting}
              >
                取消
              </button>
              <button
                onClick={confirmDelete}
                disabled={isDeleting}
                className="btn-danger"
              >
                {isDeleting ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

