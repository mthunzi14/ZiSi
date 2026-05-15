import './Sidebar.css';

export default function Sidebar({ activeTab, onTabChange }) {
  const tabs = [
    { id: 'overview', label: 'Overview', icon: '📊' },
    { id: 'analytics', label: 'Analytics', icon: '📈' },
    { id: 'settings', label: 'Settings', icon: '⚙️' },
  ];

  return (
    <aside className="sidebar">
      <nav className="sidebar-nav">
        {tabs.map(tab => (
          <button
            key={tab.id}
            className={`nav-button ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => onTabChange(tab.id)}
            title={tab.label}
          >
            <span className="nav-icon">{tab.icon}</span>
            <span className="nav-label">{tab.label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-spacer"></div>

      <div className="sidebar-info">
        <p className="info-label">Current Status</p>
        <p className="info-text">Collecting trades for Phase 1 validation</p>
      </div>
    </aside>
  );
}
