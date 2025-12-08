import React, { useState, useEffect } from 'react';
import { ProjectSelector } from './components/ProjectSelector';
import { AnnotationWorkbench } from './components/AnnotationWorkbench';
import './App.css';

interface Project {
  id: string;
  name: string;
  description: string;
  created_at?: string;
  updated_at?: string;
}

function App() {
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    // 加载项目列表
    fetchProjects();
  }, []);

  const fetchProjects = async () => {
    try {
      const response = await fetch(`${process.env.REACT_APP_API_URL || 'http://localhost:8000/api'}/projects`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setProjects(data);
    } catch (error) {
      console.error('Failed to fetch projects:', error);
      // 如果后端未启动，显示空列表而不是报错
      setProjects([]);
    }
  };

  const handleProjectSelect = (project: Project) => {
    setSelectedProject(project);
  };

  const handleBackToProjects = () => {
    setSelectedProject(null);
    fetchProjects();
  };

  if (!selectedProject) {
    return (
      <div className="app">
        <ProjectSelector
          projects={projects}
          onSelect={handleProjectSelect}
          onRefresh={fetchProjects}
        />
      </div>
    );
  }

  return (
    <div className="app">
      <AnnotationWorkbench
        project={selectedProject}
        onBack={handleBackToProjects}
      />
    </div>
  );
}

export default App;

