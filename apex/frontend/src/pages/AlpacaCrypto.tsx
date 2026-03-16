import { Outlet, NavLink } from 'react-router-dom';
import { Coins, Radar, BrainCircuit, Activity, FlaskConical } from 'lucide-react';
import './TradingStocks.css';

export default function AlpacaCrypto() {
    return (
        <div className="trading-stocks-page" style={{ height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div className="ts-panel-header" style={{ padding: '16px 16px 0 16px', marginBottom: 0 }}>
                <div className="ts-panel-title">
                    <Coins size={16} /> Crypto Terminal
                </div>
                
                <div className="ts-scan-tabs" style={{ gap: '8px', borderBottom: 'none' }}>
                    <NavLink to="/crypto/dashboard" className={({isActive}) => `ts-tab ${isActive ? 'active' : ''}`}>
                        <Coins size={13} /> Dashboard
                    </NavLink>
                    <NavLink to="/crypto/hub" className={({isActive}) => `ts-tab ${isActive ? 'active' : ''}`}>
                        <Radar size={13} /> Hub
                    </NavLink>
                    <NavLink to="/crypto/learning" className={({isActive}) => `ts-tab ${isActive ? 'active' : ''}`}>
                        <BrainCircuit size={13} /> Learning
                    </NavLink>
                    <NavLink to="/crypto/simulation" className={({isActive}) => `ts-tab ${isActive ? 'active' : ''}`}>
                        <FlaskConical size={13} /> Simulation
                    </NavLink>
                    <NavLink to="/crypto/activity" className={({isActive}) => `ts-tab ${isActive ? 'active' : ''}`}>
                        <Activity size={13} /> Activity
                    </NavLink>
                </div>
            </div>

            <div className="crypto-content-wrapper" style={{ flex: 1, overflowY: 'auto' }}>
                <Outlet />
            </div>
        </div>
    );
}
