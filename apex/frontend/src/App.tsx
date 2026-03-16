import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import AlpacaCrypto from './pages/AlpacaCrypto';
import FeatureParked from './pages/FeatureParked';
import ConfigSettings from './pages/ConfigSettings';
import ConfigHelp from './pages/ConfigHelp';
import CryptoDashboard from './pages/crypto/CryptoDashboard';
import CryptoHub from './pages/crypto/CryptoHub';
import CryptoLearning from './pages/crypto/CryptoLearning';
import CryptoActivity from './pages/crypto/CryptoActivity';
import CryptoSimulation from './pages/crypto/CryptoSimulation';
import CryptoSymbolWorkspace from './pages/crypto/CryptoSymbolWorkspace';
import './index.css';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/crypto" element={<AlpacaCrypto />}>
            <Route index element={<Navigate to="dashboard" replace />} />
            <Route path="dashboard" element={<CryptoDashboard />} />
            <Route path="hub" element={<CryptoHub />} />
            <Route path="learning" element={<CryptoLearning />} />
            <Route path="simulation" element={<CryptoSimulation />} />
            <Route path="activity" element={<CryptoActivity />} />
            <Route path="symbol/:symbol" element={<CryptoSymbolWorkspace />} />
          </Route>

          <Route path="/alpaca/crypto" element={<Navigate to="/crypto/dashboard" replace />} />
          <Route path="/alpaca/crypto/dashboard" element={<Navigate to="/crypto/dashboard" replace />} />
          <Route path="/alpaca/crypto/hub" element={<Navigate to="/crypto/hub" replace />} />
          <Route path="/alpaca/crypto/learning" element={<Navigate to="/crypto/learning" replace />} />
          <Route path="/alpaca/crypto/simulation" element={<Navigate to="/crypto/simulation" replace />} />
          <Route path="/alpaca/crypto/activity" element={<Navigate to="/crypto/activity" replace />} />
          <Route path="/alpaca/crypto/symbol/:symbol" element={<Navigate to="/crypto/dashboard" replace />} />

          <Route
            path="/alpaca"
            element={
              <FeatureParked
                title="Stocks Parked"
                detail="The crypto-first profile is active. Legacy Alpaca stock workflows are not mounted on the default runtime."
              />
            }
          />
          <Route path="/alpaca/scanner" element={<Navigate to="/alpaca" replace />} />
          <Route path="/alpaca/search/:ticker?" element={<Navigate to="/alpaca" replace />} />
          <Route path="/alpaca/analysis/:ticker?" element={<Navigate to="/alpaca" replace />} />
          <Route path="/alpaca/portfolio" element={<Navigate to="/alpaca" replace />} />

          <Route
            path="/kalshi"
            element={
              <FeatureParked
                title="Kalshi Parked"
                detail="Kalshi stays in the repo, but it is intentionally excluded from the crypto startup-critical path."
              />
            }
          />
          <Route path="/kalshi/scalper" element={<Navigate to="/kalshi" replace />} />

          <Route
            path="/dfs"
            element={
              <FeatureParked
                title="DFS Parked"
                detail="DFS is available to restore later, but it is currently parked outside the crypto-first runtime profile."
              />
            }
          />
          <Route path="/dfs/scan" element={<Navigate to="/dfs" replace />} />
          <Route path="/dfs/slips" element={<Navigate to="/dfs" replace />} />
          <Route path="/dfs/grind" element={<Navigate to="/dfs" replace />} />

          <Route
            path="/polymarket"
            element={
              <FeatureParked
                title="Polymarket Parked"
                detail="Polymarket is not loaded by default while the crypto terminal revamp is active."
              />
            }
          />
          <Route path="/convergence" element={<Navigate to="/polymarket" replace />} />

          <Route path="/config/settings" element={<ConfigSettings />} />
          <Route path="/config/help" element={<ConfigHelp />} />
          <Route path="/" element={<Navigate to="/crypto/dashboard" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
