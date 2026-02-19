import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import AlpacaDashboard from './pages/AlpacaDashboard';
import AlpacaScanner from './pages/AlpacaScanner';
import AlpacaAnalysis from './pages/AlpacaAnalysis';
import AlpacaPortfolio from './pages/AlpacaPortfolio';
import AlpacaCrypto from './pages/AlpacaCrypto';
import KalshiDashboard from './pages/KalshiDashboard';
import KalshiScalper from './pages/KalshiScalper';
import DfsScan from './pages/DfsScan';
import DfsSlipBuilder from './pages/DfsSlipBuilder';
import PolymarketResearch from './pages/PolymarketResearch';
import ConvergenceRadar from './pages/ConvergenceRadar';
import ConfigSettings from './pages/ConfigSettings';
import ConfigHelp from './pages/ConfigHelp';
import './index.css';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/alpaca" element={<AlpacaDashboard />} />
          <Route path="/alpaca/scanner" element={<AlpacaScanner />} />
          <Route path="/alpaca/search/:ticker?" element={<AlpacaAnalysis />} />
          {/* Redirect old /alpaca/analysis to /alpaca/search */}
          <Route path="/alpaca/analysis/:ticker?" element={<Navigate to="/alpaca/search" replace />} />
          <Route path="/alpaca/portfolio" element={<AlpacaPortfolio />} />
          <Route path="/alpaca/crypto" element={<AlpacaCrypto />} />
          <Route path="/kalshi" element={<KalshiDashboard />} />
          <Route path="/kalshi/scalper" element={<KalshiScalper />} />
          <Route path="/dfs" element={<Navigate to="/dfs/scan" replace />} />
          <Route path="/dfs/scan" element={<DfsScan />} />
          <Route path="/dfs/slips" element={<DfsSlipBuilder />} />
          <Route path="/dfs/grind" element={<Navigate to="/dfs/scan" replace />} />
          <Route path="/polymarket" element={<PolymarketResearch />} />
          <Route path="/convergence" element={<ConvergenceRadar />} />
          <Route path="/config/settings" element={<ConfigSettings />} />
          <Route path="/config/help" element={<ConfigHelp />} />
          <Route path="/" element={<Navigate to="/alpaca" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
