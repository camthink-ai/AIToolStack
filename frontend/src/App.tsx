import React, { useState, useEffect } from 'react';
import { TopNavigation } from './components/TopNavigation';
import { Dashboard } from './components/Dashboard';
import { ProjectSelector } from './components/ProjectSelector';
import { AnnotationWorkbench } from './components/AnnotationWorkbench';
import { TrainingPanel } from './components/TrainingPanel';
import { SystemSettings } from './components/SystemSettings';
import { DeviceManager } from './components/DeviceManager';
import { API_BASE_URL } from './config';
import './App.css';

interface Project {
  id: string;
  name: string;
  description: string;
  created_at?: string;
  updated_at?: string;
}

type MenuItem = 'dashboard' | 'projects' | 'models' | 'device' | 'settings';

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
    
    // Listen for navigation events
    const handleNavigateToSettings = () => {
      setActiveMenu('settings');
      setSelectedProject(null);
      setShowTrainingPanel(false);
      setTrainingProjectId(null);
      setTrainingInitialId(null);
    };
    
    const handleNavigateToProjects = () => {
      setActiveMenu('projects');
      setSelectedProject(null);
      setShowTrainingPanel(false);
      setTrainingProjectId(null);
      setTrainingInitialId(null);
      fetchProjects();
    };
    
    const handleNavigateToModels = () => {
      setActiveMenu('models');
      setSelectedProject(null);
      setShowTrainingPanel(false);
      setTrainingProjectId(null);
      setTrainingInitialId(null);
    };
    
    const handleNavigateToDevice = () => {
      setActiveMenu('device');
      setSelectedProject(null);
      setShowTrainingPanel(false);
      setTrainingProjectId(null);
      setTrainingInitialId(null);
    };
    
    const handleOpenProject = (e: CustomEvent) => {
      setSelectedProject(e.detail);
    };
    
    window.addEventListener('navigate-to-settings', handleNavigateToSettings);
    window.addEventListener('navigate-to-projects', handleNavigateToProjects);
    window.addEventListener('navigate-to-models', handleNavigateToModels);
    window.addEventListener('navigate-to-device', handleNavigateToDevice);
    window.addEventListener('open-project', handleOpenProject as EventListener);
    
    return () => {
      window.removeEventListener('navigate-to-settings', handleNavigateToSettings);
      window.removeEventListener('navigate-to-projects', handleNavigateToProjects);
      window.removeEventListener('navigate-to-models', handleNavigateToModels);
      window.removeEventListener('navigate-to-device', handleNavigateToDevice);
      window.removeEventListener('open-project', handleOpenProject as EventListener);
    };
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

      case 'settings':
        return <SystemSettings />;

      case 'device':
        return <DeviceManager />;

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
