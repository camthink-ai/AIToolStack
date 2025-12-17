import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { API_BASE_URL } from '../config';
import { IoRefresh, IoAdd, IoFolder, IoTrash, IoClose } from 'react-icons/io5';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import './ProjectSelector.css';
import { Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogBody, DialogFooter, DialogClose } from '../ui/Dialog';
import { Button } from '../ui/Button';
import { FormField } from '../ui/FormField';
import { Input } from '../ui/Input';
import { Textarea } from '../ui/Textarea';
import { Alert } from '../ui/Alert';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { useAlert } from '../hooks/useAlert';
import { useConfirm } from '../hooks/useConfirm';

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
  onOpenTraining?: (projectId: string, trainingId?: string) => void;
}

export const ProjectSelector: React.FC<ProjectSelectorProps> = ({
  projects,
  onSelect,
  onRefresh,
  onOpenTraining
}) => {
  const { t, i18n } = useTranslation();
  const { alertState, showError, closeAlert } = useAlert();
  const { confirmState, showConfirm, closeConfirm } = useConfirm();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  type CreateProjectForm = {
    name: string;
    description?: string;
  };

  const createProjectSchema = useMemo(
    () =>
      z.object({
        name: z.string().trim().min(1, { message: t('project.nameRequired') }),
        description: z.string().trim().optional()
      }),
    [t]
  );

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting }
  } = useForm<CreateProjectForm>({
    resolver: zodResolver(createProjectSchema),
    defaultValues: {
      name: '',
      description: ''
    }
  });

  const handleCreateProject = async (values: CreateProjectForm) => {
    try {
      const response = await fetch(`${API_BASE_URL}/projects`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name: values.name.trim(),
          description: (values.description || '').trim(),
        }),
      });

      if (response.ok) {
        await response.json();
        setShowCreateModal(false);
        reset();
        onRefresh();
        // Optional: automatically open newly created project
        // onSelect(newProject);
      } else {
        const errorData = await response.json().catch(() => ({ detail: t('project.createFailed') }));
        showError(errorData.detail || t('project.createFailed'));
      }
    } catch (error) {
      console.error('Failed to create project:', error);
      showError(`${t('project.createFailed')}: ${t('common.connectionError', 'Unable to connect to server')}`);
    }
  };

  const handleCancelCreate = () => {
    setShowCreateModal(false);
    reset();
  };

  const handleDeleteProject = (e: React.MouseEvent, project: Project) => {
    e.stopPropagation(); // Prevent triggering project selection
    
    const deleteMessage = t('project.confirmDelete', { name: project.name || '' }) + '\n\n' + t('project.deleteWarning', '此操作不可恢复，将删除项目中的所有图像、标注和配置。');
    
    showConfirm(
      deleteMessage,
      async () => {
        setIsDeleting(true);
        try {
          const response = await fetch(`${API_BASE_URL}/projects/${project.id}`, {
            method: 'DELETE',
          });

          if (response.ok) {
            onRefresh();
          } else {
            const errorData = await response.json().catch(() => ({ detail: t('project.deleteFailed') }));
            showError(errorData.detail || t('project.deleteFailed'));
          }
        } catch (error) {
          console.error('Failed to delete project:', error);
          showError(`${t('project.deleteFailed')}: ${t('common.connectionError', 'Unable to connect to server')}`);
        } finally {
          setIsDeleting(false);
        }
      },
      {
        title: t('project.delete', '删除项目'),
        variant: 'danger',
      }
    );
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
              <Button variant="secondary" size="md" onClick={onRefresh}>
                <Icon component={IoRefresh} /> {t('common.refresh')}
              </Button>
              <Button variant="primary" size="md" onClick={() => setShowCreateModal(true)}>
                <Icon component={IoAdd} /> {t('project.createNew')}
              </Button>
            </div>
          </div>
        </div>
        {projects.length === 0 ? (
          <div className="training-empty">
            <p className="training-empty-desc">
              {t('project.noProjects')}
            </p>
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
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="project-delete-btn"
                        onClick={(e) => handleDeleteProject(e, project)}
                        title={t('project.delete')}
                      >
                        <Icon component={IoTrash} />
                      </Button>
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

      {/* 创建项目弹窗（Radix Dialog + React Hook Form） */}
      <Dialog
        open={showCreateModal}
        onOpenChange={(open) => {
          setShowCreateModal(open);
          if (!open) reset();
        }}
      >
        <DialogContent className="config-modal create-project-modal">
          <DialogHeader className="config-modal-header">
            <DialogTitle asChild>
              <h3>{t('project.createNew')}</h3>
            </DialogTitle>
            <DialogClose className="close-btn" onClick={handleCancelCreate}>
              <Icon component={IoClose} />
            </DialogClose>
          </DialogHeader>
          <form onSubmit={handleSubmit(handleCreateProject)}>
            <DialogBody className="config-modal-content ui-form-stack">
              <FormField
                label={t('project.name')}
                required
                error={errors.name?.message}
              >
                <Input
                  type="text"
                  {...register('name')}
                  placeholder={t('project.name')}
                  disabled={isSubmitting}
                  autoFocus
                />
              </FormField>
              <FormField
                label={t('project.description')}
                error={errors.description?.message}
              >
                <Textarea
                  {...register('description')}
                  placeholder={t('project.descriptionOptional', '输入项目描述（可选）')}
                  disabled={isSubmitting}
                  rows={4}
                />
              </FormField>
            </DialogBody>
            <DialogFooter className="config-modal-actions">
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? t('common.loading') : t('project.create')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Confirm Dialog */}
      <ConfirmDialog
        open={confirmState.open}
        onOpenChange={(open) => {
          if (!open && !isDeleting) {
            closeConfirm();
          }
        }}
        title={confirmState.title}
        message={confirmState.message}
        confirmText={isDeleting ? t('common.loading', '加载中...') : confirmState.confirmText}
        cancelText={confirmState.cancelText}
        onConfirm={confirmState.onConfirm || (() => {})}
        onCancel={confirmState.onCancel}
        variant={confirmState.variant}
        disabled={isDeleting}
      />

      {/* Alert Dialog */}
      <Alert
        open={alertState.open}
        onOpenChange={closeAlert}
        title={alertState.title}
        message={alertState.message}
        type={alertState.type}
        confirmText={alertState.confirmText || t('common.confirm', '确定')}
        onConfirm={alertState.onConfirm}
      />
    </div>
  );
};

