import { useState, useRef, useCallback, useEffect } from 'react';
import './App.css';
import Header from './components/Header';
import Sidebar from './components/Sidebar';
import BotStatus from './components/BotStatus';
import EdgeValidation from './components/EdgeValidation';
import MissedTrades from './components/MissedTrades';
import AnalyticsBySection from './components/AnalyticsBySection';
import ByUTC from './components/ByUTC';
import RiskMetrics from './components/RiskMetrics';
import MLProgress from './components/MLProgress';
import MLStatus from './components/MLStatus';
import SystemAlerts from './components/SystemAlerts';
import RegimeIndicator from './components/RegimeIndicator';
import ToastTest from './components/ToastTest';
import Positions from './components/Positions';
import SignalPipeline from './components/SignalPipeline';
import EquityChart from './components/EquityChart';
import SignalQueue from './components/SignalQueue';
import PerformanceCard from './components/PerformanceCard';
import LiveTradeFeed from './components/LiveTradeFeed';

export default function App() {
  const [activeTab, setActiveTab] = useState('overview');
  const [dashboardData, setDashboardData] = useState({});
  const refreshRef = useRef(null);

  const handleRefresh = useCallback(async () => {
    const res = await fetch('/api/health');
    const data = await res.json();
    setDashboardData(data);
    return data;
  }, []);

  useEffect(() => {
    handleRefresh();
    const id = setInterval(handleRefresh, 15_000);
    return () => clearInterval(id);
  }, [handleRefresh]);

  return (
    <div className="app-container">
      <Sidebar activeTab={activeTab} onTabChange={setActiveTab} />

      <main className="app-main">
        <Header onRefresh={handleRefresh} metrics={dashboardData} />

        <div className="app-content">
          {activeTab === 'overview' && (
            <>
              {/* Row 1: Compact command strip (status + key metrics + pause) */}
              <BotStatus />

              {/* Row 2: System alerts (health monitor output) */}
              <SystemAlerts />

              {/* Row 3: Regime pill */}
              {dashboardData.regime && (
                <RegimeIndicator regime={dashboardData.regime} />
              )}

              {/* Row 4: Equity curve */}
              <EquityChart />

              {/* Row 4b: Entity performance comparison */}
              <PerformanceCard />

              {/* Row 4c: Live trade feed (SSE-powered, real-time) */}
              <LiveTradeFeed />

              {/* Row 5: Open/closed positions */}
              <Positions />

              {/* Row 6: Exchange performance cards + Phase 1 progress */}
              <MissedTrades data={dashboardData} />

              {/* Row 7: Signal pipeline funnel */}
              <SignalPipeline data={dashboardData} />
            </>
          )}

          {activeTab === 'analytics' && (
            <>
              <h1>Advanced Analytics</h1>
              <EdgeValidation data={dashboardData} />
              <div className="analytics-sections">
                <ByUTC data={dashboardData} />
                <RiskMetrics data={dashboardData} />
              </div>
              <AnalyticsBySection />
              <div className="analytics-sections">
                <MLProgress data={dashboardData} />
                <MLStatus />
              </div>
              <SignalQueue />
            </>
          )}

          {activeTab === 'settings' && (
            <div className="card mt-xl">
              <h2>Settings</h2>
              <p className="text-muted" style={{ marginBottom: '32px' }}>
                Settings configuration coming soon.
              </p>
              <ToastTest />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
