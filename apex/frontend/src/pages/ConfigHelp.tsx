import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import './ConfigHelp.css';

interface AccordionProps {
    title: string;
    defaultOpen?: boolean;
    children: React.ReactNode;
}

function Accordion({ title, defaultOpen = false, children }: AccordionProps) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <section className={`help-accordion ${open ? 'open' : ''}`}>
            <button className="accordion-header" onClick={() => setOpen(!open)}>
                <span>{title}</span>
                <ChevronDown size={16} className={`accordion-chevron ${open ? 'open' : ''}`} />
            </button>
            {open && <div className="accordion-body">{children}</div>}
        </section>
    );
}

export default function ConfigHelp() {
    return (
        <div className="config-help">
            {/* ── AI Brain ── */}
            <Accordion title="🧠 AI Brain (Dual-Model System)" defaultOpen>
                <p>
                    Apex uses a <strong>two-model XGBoost classifier</strong> trained on S&P 500 historical data to predict trade outcomes.
                    Each model evaluates the same 6 features but is trained on a different definition of "winning":
                </p>
                <div className="help-card dual-brain">
                    <div className="brain-col">
                        <h4>🛡️ Clean Win Brain</h4>
                        <p>Predicts whether a trade will hit its target <em>without ever dropping below the stop loss</em>. Prioritizes low-drawdown setups.</p>
                    </div>
                    <div className="brain-col">
                        <h4>🔄 Eventual Win Brain</h4>
                        <p>Predicts whether a trade will <em>eventually</em> hit its target, even if it temporarily goes red. Captures mean-reversion plays.</p>
                    </div>
                </div>
                <div className="help-formula">
                    <strong>Composite Score</strong> = <code>0.70 × Clean Score</code> + <code>0.30 × Eventual Score</code>
                </div>
                <h3>Features Used</h3>
                <table className="help-table">
                    <thead><tr><th>Feature</th><th>What It Measures</th></tr></thead>
                    <tbody>
                        <tr><td>RSI (14)</td><td>Momentum — is the stock overbought or oversold?</td></tr>
                        <tr><td>SMA Distance</td><td>Trend extension — how far price has deviated from the 20-day moving average</td></tr>
                        <tr><td>ATR %</td><td>Volatility — average daily range as a percentage of price</td></tr>
                        <tr><td>Relative Volume</td><td>Interest — today's volume compared to the 20-day average</td></tr>
                        <tr><td>ADX (14)</td><td>Trend strength — how strongly the stock is trending (any direction)</td></tr>
                        <tr><td>Sector RS</td><td>Rotation — stock's daily return minus SPY's daily return</td></tr>
                    </tbody>
                </table>
            </Accordion>

            {/* ── Technical Indicators ── */}
            <Accordion title="📊 Technical Indicators">
                <div className="help-block">
                    <h3>ATR (Average True Range)</h3>
                    <p>
                        Measures how much a stock moves on average per day. Used to set <strong>dynamic stop losses</strong> that adapt to the stock's volatility.
                        A stock with ATR of $5 needs a wider stop than one with ATR of $0.50.
                    </p>
                    <div className="help-formula">
                        <strong>Stop Loss</strong> = Entry Price − (ATR × Multiplier)
                    </div>
                    <p className="help-note">Multiplier defaults: Aggressive = 2.0×, Conservative = 2.5×, Trend = 3.0×. Adjust in Settings → Stocks.</p>
                </div>

                <div className="help-block">
                    <h3>RSI (Relative Strength Index)</h3>
                    <p>Oscillator from 0–100 measuring recent momentum. Default period: 14 days.</p>
                    <ul className="help-list">
                        <li><strong>&gt; 70</strong> — Overbought. Price may be extended. Caution for new longs.</li>
                        <li><strong>50–70</strong> — Bullish momentum zone. Ideal for trend entries.</li>
                        <li><strong>30–50</strong> — Neutral/bearish lean. Watch for reversal signals.</li>
                        <li><strong>&lt; 30</strong> — Oversold. Potential bounce candidate.</li>
                    </ul>
                </div>

                <div className="help-block">
                    <h3>Moving Averages (SMA / EMA)</h3>
                    <p>
                        <strong>SMA</strong> (Simple Moving Average) treats all days equally. Used for support/resistance levels.
                        <strong>EMA</strong> (Exponential Moving Average) weights recent days more heavily. Used for fast crossover signals.
                    </p>
                    <ul className="help-list">
                        <li><strong>SMA 20</strong> — Short-term trend. Pullback entry level.</li>
                        <li><strong>SMA 50</strong> — Medium-term trend. Trend-following entry.</li>
                        <li><strong>SMA 200</strong> — Long-term trend. Bull/Bear market divider.</li>
                        <li><strong>EMA 9 × EMA 21</strong> — Crossover signal for the Trend Surfer strategy.</li>
                    </ul>
                </div>
            </Accordion>

            {/* ── Trading Strategies ── */}
            <Accordion title="🎯 Trading Strategies">
                <div className="strategy-cards">
                    <div className="strategy-card aggressive">
                        <h4>Aggressive (Momentum)</h4>
                        <div className="strategy-rules">
                            <div><span className="rule-label">Entry</span> RSI &gt; 50 AND Price &gt; SMA 20</div>
                            <div><span className="rule-label">Target</span> +6%</div>
                            <div><span className="rule-label">Stop</span> −3% (or 2.0× ATR)</div>
                            <div><span className="rule-label">Best For</span> Strong momentum breakouts</div>
                        </div>
                    </div>
                    <div className="strategy-card conservative">
                        <h4>Conservative (Pullback)</h4>
                        <div className="strategy-rules">
                            <div><span className="rule-label">Entry</span> Price touches SMA 20 AND RSI &lt; 60</div>
                            <div><span className="rule-label">Target</span> +10%</div>
                            <div><span className="rule-label">Stop</span> −5% (or 2.5× ATR)</div>
                            <div><span className="rule-label">Best For</span> Buying dips in uptrends</div>
                        </div>
                    </div>
                    <div className="strategy-card trend">
                        <h4>Trend Follower</h4>
                        <div className="strategy-rules">
                            <div><span className="rule-label">Entry</span> Price &gt; SMA 50</div>
                            <div><span className="rule-label">Target</span> 3× Risk (Risk/Reward)</div>
                            <div><span className="rule-label">Stop</span> SMA 50 (or 3.0× ATR)</div>
                            <div><span className="rule-label">Best For</span> Riding big moves, lower win-rate but high payoff</div>
                        </div>
                    </div>
                    <div className="strategy-card surfer">
                        <h4>Trend Surfer (MA Crossover)</h4>
                        <div className="strategy-rules">
                            <div><span className="rule-label">Entry</span> EMA 9 crosses above EMA 21</div>
                            <div><span className="rule-label">Target</span> 2.5× Risk</div>
                            <div><span className="rule-label">Stop</span> EMA 21 (trailing)</div>
                            <div><span className="rule-label">Best For</span> Catching trend starts and riding them</div>
                        </div>
                    </div>
                </div>
            </Accordion>

            {/* ── DFS Concepts ── */}
            <Accordion title="🏆 DFS & Sports Betting Concepts">
                <div className="help-block">
                    <h3>Expected Value (EV)</h3>
                    <p>
                        The average profit or loss per bet over infinite repetitions.
                        <strong> +EV bets are profitable long-term.</strong>
                    </p>
                    <div className="help-formula">
                        <strong>EV</strong> = (Win Probability × Payout) − 1
                    </div>
                    <p className="help-note">Example: A bet that wins 55% of the time at 2:1 payout → EV = (0.55 × 2) − 1 = +0.10 → <strong>+10% edge</strong>.</p>
                </div>

                <div className="help-block">
                    <h3>Implied Probability</h3>
                    <p>What the odds "imply" the true probability is, assuming no vig (bookmaker margin).</p>
                    <div className="help-formula">
                        <strong>Positive odds</strong> (+150): <code>100 / (150 + 100)</code> = 40%<br />
                        <strong>Negative odds</strong> (−110): <code>110 / (110 + 100)</code> = 52.4%
                    </div>
                </div>

                <div className="help-block">
                    <h3>Kelly Criterion</h3>
                    <p>A formula for optimal bet sizing that maximizes long-term growth while managing risk.</p>
                    <div className="help-formula">
                        <strong>Kelly %</strong> = (bp − q) / b<br />
                        <em>b</em> = decimal odds − 1, <em>p</em> = win probability, <em>q</em> = 1 − p
                    </div>
                    <p className="help-note">In practice, most sharps use <strong>¼ Kelly</strong> (25% of the calculated amount) to reduce variance. Adjustable in Settings → DFS.</p>
                </div>

                <div className="help-block">
                    <h3>Board Lag (Sniper)</h3>
                    <p>
                        When a sharp sportsbook (like Pinnacle) moves a line, DFS platforms (PrizePicks, Sleeper) are slow to update.
                        The <strong>Sniper</strong> detects this gap and alerts you to take the now-mispriced DFS line before it catches up.
                    </p>
                    <ul className="help-list">
                        <li><strong>Sharp Line</strong> — The "true" market price from a professional book.</li>
                        <li><strong>DFS Line</strong> — The stale line still posted on a DFS platform.</li>
                        <li><strong>Gap</strong> — Difference between the two. Larger gap = stronger signal.</li>
                        <li><strong>Stale Window</strong> — How long the DFS line has been outdated. Longer = more confidence.</li>
                    </ul>
                </div>

                <div className="help-block">
                    <h3>Slip Builder</h3>
                    <p>Auto-generates the best parlay combinations from scanned props. Ranks all possible pick combos by EV, considering:</p>
                    <ul className="help-list">
                        <li><strong>No-Vig Probability</strong> — Raw probability with bookmaker margin removed.</li>
                        <li><strong>Confidence Weighting</strong> — Noisy probability estimates are shrunk toward 50% to avoid overconfidence.</li>
                        <li><strong>Platform Payouts</strong> — Uses platform-specific multipliers (3×, 5×, 10×, 20×, 40×).</li>
                        <li><strong>Constraints</strong> — No duplicate players, max 6 picks per slip.</li>
                    </ul>
                </div>
            </Accordion>

            {/* ── Events & Kalshi ── */}
            <Accordion title="⚡ Events & Kalshi">
                <div className="help-block">
                    <h3>Arbitrage Bot</h3>
                    <p>
                        Scans Kalshi markets for binary event contracts where <strong>Yes + No prices &lt; $1.00</strong>.
                        This creates a guaranteed profit regardless of outcome.
                    </p>
                    <div className="help-formula">
                        <strong>Arb Profit</strong> = $1.00 − (Yes Price + No Price)
                    </div>
                    <p className="help-note">Min profit threshold is configurable in Settings → Events. Default: $0.02 per contract.</p>
                </div>

                <div className="help-block">
                    <h3>Market Maker</h3>
                    <p>
                        Places simultaneous buy and sell orders around the current price, profiting from the spread.
                        Higher spread = more profit per trade, but fewer fills.
                    </p>
                </div>

                <div className="help-block">
                    <h3>Convergence Engine</h3>
                    <p>
                        Compares Polymarket prices (global, unrestricted) against Kalshi prices (US-regulated) for the same events.
                        When the two diverge significantly, it signals a potential trading opportunity on the lagging platform.
                    </p>
                    <ul className="help-list">
                        <li><strong>Polymarket</strong> — Read-only "sharp" indicator. Higher liquidity, global access.</li>
                        <li><strong>Kalshi</strong> — Executable US-legal platform. Prices may lag Polymarket.</li>
                        <li><strong>Delta</strong> — Price difference between the two. Positive delta = Kalshi is lagging.</li>
                    </ul>
                </div>

                <div className="help-block">
                    <h3>Bot Detection</h3>
                    <p>Analyzes Kalshi order flow patterns to identify automated wallets. Used to follow "smart money" or avoid competing with bots.</p>
                    <ul className="help-list">
                        <li><strong>Score &gt; 0.7</strong> — Likely automated. Consistent timing, round lot sizes.</li>
                        <li><strong>Score &lt; 0.3</strong> — Likely human. Variable timing, irregular sizes.</li>
                    </ul>
                </div>
            </Accordion>

            {/* ── Backtesting ── */}
            <Accordion title="🔬 Backtesting">
                <p>
                    The backtester simulates a strategy over <strong>1 year of historical data</strong> to measure its performance.
                    It iterates day-by-day, checking entry and exit conditions, and tracks an equity curve.
                </p>
                <h3>Key Metrics</h3>
                <table className="help-table">
                    <thead><tr><th>Metric</th><th>Meaning</th></tr></thead>
                    <tbody>
                        <tr><td>Win Rate</td><td>Percentage of trades that were profitable. &gt;50% is good for Aggressive; 30–40% is fine for Trend Follower.</td></tr>
                        <tr><td>Profit Factor</td><td>Gross Wins / Gross Losses. &gt;1.5 is good, &gt;2.0 is excellent.</td></tr>
                        <tr><td>Total Return</td><td>Net profit/loss in dollars over the test period.</td></tr>
                        <tr><td>Equity Curve</td><td>Visual chart of portfolio value over time. Smooth upward = consistent strategy.</td></tr>
                    </tbody>
                </table>
                <p className="help-note">Backtests use <strong>full position sizing</strong> (100% of balance per trade). Real trading should use fractions (1–5% risk per trade).</p>
            </Accordion>

            {/* ── General Features ── */}
            <Accordion title="🛠️ General Features">
                <div className="help-block">
                    <h3>Paper vs Live Mode</h3>
                    <p>
                        <strong>Paper</strong> mode connects to Alpaca's sandbox environment. All trades are simulated — no real money at risk.
                        <strong>Live</strong> mode connects to Alpaca's production API with real funds.
                    </p>
                    <p className="help-note">⚠️ Live mode executes real orders. Toggle is in the top bar.</p>
                </div>

                <div className="help-block">
                    <h3>Global Search (⌘K)</h3>
                    <p>
                        Press <code>⌘K</code> (or <code>Ctrl+K</code>) to open the global search overlay.
                        Jump to any page, search stocks, or navigate quickly.
                    </p>
                </div>

                <div className="help-block">
                    <h3>WebSocket (Live / Offline)</h3>
                    <p>
                        The bottom-left indicator shows whether the real-time data connection is active.
                        <strong>Live</strong> = streaming updates. <strong>Offline</strong> = reconnecting (data may be stale).
                    </p>
                </div>
            </Accordion>
        </div>
    );
}
