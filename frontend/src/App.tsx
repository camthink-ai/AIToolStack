import React, { useState, useEffect } from 'react';
import { TopNavigation } from './components/TopNavigation';
import { Dashboard } from './components/Dashboard';
import { ProjectSelector } from './components/ProjectSelector';
import { AnnotationWorkbench } from './components/AnnotationWorkbench';
import { TrainingPanel } from './components/TrainingPanel';
import { API_BASE_URL } from './config';
import './App.css';

interface Project {
  id: string;
  name: string;
  description: string;
  created_at?: string;
  updated_at?: string;
}

type MenuItem = 'dashboard' | 'projects' | 'models';

function App() {
  const [activeMenu, setActiveMenu] = useState<MenuItem>('dashboard');
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [showTrainingPanel, setShowTrainingPanel] = useState(false);
  const [trainingProjectId, setTrainingProjectId] = useState<string | null>(null);
  const [trainingInitialId, setTrainingInitialId] = useState<string | null>(null);

  useEffect(() => {
    // Load project list
    fetchProjects();
  }, []);

  const fetchProjects = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/projects`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setProjects(data);
    } catch (error) {
      console.error('Failed to fetch projects:', error);
      // If backend is not started, show empty list instead of error
      setProjects([]);
    }
  };

  const handleProjectSelect = (project: Project) => {
    setSelectedProject(project);
  };

  const handleBackToProjects = () => {
    setSelectedProject(null);
    setActiveMenu('projects'); // Return to project management menu
    fetchProjects();
  };

  const handleMenuChange = (menu: MenuItem) => {
    setActiveMenu(menu);
    setSelectedProject(null);
    setShowTrainingPanel(false);
    setTrainingProjectId(null);
    setTrainingInitialId(null);
  };

  const handleOpenTrainingPanel = (projectId: string, trainingId?: string) => {
    setTrainingProjectId(projectId);
    setTrainingInitialId(trainingId ?? null);
    setShowTrainingPanel(true);
  };

  const handleCloseTrainingPanel = () => {
    setShowTrainingPanel(false);
    setTrainingProjectId(null);
    setTrainingInitialId(null);
  };

  // Render main content
  const renderMainContent = () => {
    // If training panel is being displayed, show training panel
    if (showTrainingPanel && trainingProjectId) {
      return (
        <TrainingPanel
          projectId={trainingProjectId}
          initialTrainingId={trainingInitialId ?? undefined}
          onClose={handleCloseTrainingPanel}
        />
      );
    }

    // If project is selected, show annotation interface
    if (selectedProject) {
      return (
        <AnnotationWorkbench
          project={selectedProject}
          onBack={handleBackToProjects}
          onOpenTraining={handleOpenTrainingPanel}
        />
      );
    }

    // Display different content based on current menu
    switch (activeMenu) {
      case 'dashboard':
        return (
          <Dashboard
            onStartProject={() => {
              setActiveMenu('projects');
              fetchProjects();
            }}
          />
        );

      case 'projects':
        return (
          <ProjectSelector
            projects={projects}
            onSelect={handleProjectSelect}
            onRefresh={fetchProjects}
            onOpenTraining={handleOpenTrainingPanel}
          />
        );

      case 'models':
        // Lazy import to avoid circular dependency issues
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const { ModelSpace } = require('./components/ModelSpace');
        return (
          <ModelSpace
            onOpenTraining={handleOpenTrainingPanel}
          />
        );

      default:
        return null;
  }
  };

  // Determine if annotation interface is currently displayed, if so remove padding
  const isAnnotationView = selectedProject !== null;

  return (
    <div className="app">
      <TopNavigation activeMenu={activeMenu} onMenuChange={handleMenuChange} />
      <div 
        className="app-content" 
        style={{ 
          marginTop: '64px',
          padding: isAnnotationView ? '0' : '24px'
        }}
      >
        {renderMainContent()}
      </div>
    </div>
  );
}

export default App;
