import React, { useCallback, useEffect, useMemo, useState } from 'react';

import Container from '../components/Container';
import Layout from '../components/Layout/Layout';

import * as styles from './dashboard.module.css';

const API_BASE = process.env.GATSBY_API_BASE_URL || 'http://localhost:8000';
const isBrowser = typeof window !== 'undefined';

const STATUS_CLASS = {
  safe: styles.statusSafe,
  warning: styles.statusWarning,
  margin_call: styles.statusMargin,
  liquidation_risk: styles.statusLiquidation,
};

const formatUsd = (value) => {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—';
  }
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value);
};

const formatPct = (value) => {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—';
  }
  return `${(value * 100).toFixed(2)}%`;
};

const buildQuery = (params) => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') {
      return;
    }
    search.set(key, value);
  });
  const query = search.toString();
  return query ? `?${query}` : '';
};

const TrendSparkline = ({ data, thresholds }) => {
  if (!data?.length) {
    return <p className={styles.emptyState}>No historical points captured yet.</p>;
  }

  const maxValue = Math.max(1.2, ...data.map((entry) => entry.ltv || 0));
  const minValue = Math.min(0, ...data.map((entry) => entry.ltv || 0));
  const scaleX = (index) => {
    if (data.length === 1) {
      return 95;
    }
    return 5 + (index / (data.length - 1)) * 90;
  };
  const scaleY = (value) => {
    const clamped = Math.min(Math.max(value, minValue), maxValue);
    const range = maxValue - minValue || 1;
    const normalised = (clamped - minValue) / range;
    return 90 - normalised * 80;
  };

  const path = data
    .map((entry, index) => `${index === 0 ? 'M' : 'L'} ${scaleX(index)} ${scaleY(entry.ltv || 0)}`)
    .join(' ');

  const latestPoint = data[data.length - 1];

  const thresholdLines = ['warning', 'margin_call', 'liquidation']
    .filter((key) => thresholds?.[key] !== undefined)
    .map((key) => ({ key, value: thresholds[key] }));

  return (
    <svg className={styles.chart} viewBox="0 0 100 100" preserveAspectRatio="none">
      {thresholdLines.map((line) => (
        <line
          key={line.key}
          x1="5"
          x2="95"
          y1={scaleY(line.value)}
          y2={scaleY(line.value)}
          className={`${styles.referenceLine} ${styles[`reference${line.key.replace('_', '')}`] ?? ''}`}
        />
      ))}
      <path d={path} className={styles.chartPath} />
      {latestPoint ? (
        <circle
          className={styles.chartDot}
          cx={scaleX(data.length - 1)}
          cy={scaleY(latestPoint.ltv || 0)}
          r="2.2"
        />
      ) : null}
    </svg>
  );
};

const RecentEvents = ({ entries }) => {
  if (!entries?.length) {
    return null;
  }
  const recent = entries.slice(-5).reverse();
  return (
    <ul className={styles.historyList}>
      {recent.map((entry, index) => (
        <li key={`${entry.created_at}-${index}`}>
          <span>{new Date(entry.created_at).toLocaleString()}</span>
          <span>{(entry.ltv * 100).toFixed(2)}%</span>
          <span className={styles.historyLevel}>{entry.alert_level}</span>
        </li>
      ))}
    </ul>
  );
};

const DashboardPage = () => {
  const [token, setToken] = useState('');
  const [username, setUsername] = useState('');
  const [totp, setTotp] = useState('');
  const [profiles, setProfiles] = useState([]);
  const [profile, setProfile] = useState('');
  const [snapshot, setSnapshot] = useState(null);
  const [history, setHistory] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [error, setError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [repayAmount, setRepayAmount] = useState('');
  const [transferAmount, setTransferAmount] = useState('');
  const [transferAsset, setTransferAsset] = useState('btc');
  const [transferDirection, setTransferDirection] = useState('to_collateral');
  const [lastUpdated, setLastUpdated] = useState(null);

  useEffect(() => {
    if (!isBrowser) {
      return;
    }
    const storedToken = window.localStorage.getItem('loanMonitorToken');
    const storedProfile = window.localStorage.getItem('loanMonitorProfile');
    if (storedToken) {
      setToken(storedToken);
    }
    if (storedProfile) {
      setProfile(storedProfile);
    }
  }, []);

  useEffect(() => {
    if (!isBrowser) {
      return;
    }
    if (token) {
      window.localStorage.setItem('loanMonitorToken', token);
    } else {
      window.localStorage.removeItem('loanMonitorToken');
    }
  }, [token]);

  useEffect(() => {
    if (!isBrowser) {
      return;
    }
    if (profile) {
      window.localStorage.setItem('loanMonitorProfile', profile);
    }
  }, [profile]);

  const fetchWithAuth = useCallback(
    async (path, options = {}) => {
      if (!token) {
        throw new Error('Authenticate to load dashboard data.');
      }
      const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: {
          'Content-Type': 'application/json',
          ...(options.headers || {}),
          Authorization: `Bearer ${token}`,
        },
      });
      const text = await response.text();
      if (response.status === 401) {
        setToken('');
        if (isBrowser) {
          window.localStorage.removeItem('loanMonitorToken');
        }
        throw new Error('Session expired. Please authenticate again.');
      }
      if (!response.ok) {
        let message = text || `Request failed (${response.status})`;
        try {
          const payload = text ? JSON.parse(text) : {};
          if (payload.detail) {
            message = payload.detail;
          } else if (payload.error) {
            message = payload.error;
          }
        } catch (err) {
          // ignore JSON parsing issues
        }
        throw new Error(message);
      }
      if (!text) {
        return {};
      }
      try {
        return JSON.parse(text);
      } catch (err) {
        return {};
      }
    },
    [token],
  );

  useEffect(() => {
    if (!token) {
      return;
    }
    let cancelled = false;
    const loadProfiles = async () => {
      try {
        const data = await fetchWithAuth('/api/profiles');
        if (cancelled) {
          return;
        }
        setProfiles(data.profiles || []);
        const available = (data.profiles || []).map((p) => p.id);
        const fallback = data.default || available[0] || '';
        if (!profile || !available.includes(profile)) {
          setProfile(fallback);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message);
        }
      }
    };
    loadProfiles();
    return () => {
      cancelled = true;
    };
  }, [token, fetchWithAuth, profile]);

  const fetchAll = useCallback(async () => {
    if (!token || !profile) {
      return;
    }
    setLoading(true);
    setError('');
    try {
      const [snapshotData, historyData, summaryData] = await Promise.all([
        fetchWithAuth(`/api/dashboard${buildQuery({ profile })}`),
        fetchWithAuth(`/api/history${buildQuery({ profile, limit: 180 })}`),
        fetchWithAuth(`/api/history/summary${buildQuery({ profile, limit: 720 })}`),
      ]);
      setSnapshot(snapshotData);
      setHistory(historyData.entries || []);
      setSummary(summaryData);
      setLastUpdated(new Date());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth, profile, token]);

  useEffect(() => {
    if (!token || !profile) {
      return;
    }
    let cancelled = false;
    const run = async () => {
      await fetchAll();
      if (cancelled) {
        return;
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [token, profile, fetchAll]);

  useEffect(() => {
    if (!autoRefresh || !token || !profile) {
      return undefined;
    }
    const interval = setInterval(() => {
      fetchAll();
    }, 15000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchAll, profile, token]);

  const handleLogin = async (event) => {
    event.preventDefault();
    setError('');
    setActionMessage('');
    try {
      const response = await fetch(`${API_BASE}/auth/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, totp }),
      });
      const text = await response.text();
      if (!response.ok) {
        let message = text || 'Authentication failed';
        try {
          const payload = text ? JSON.parse(text) : {};
          if (payload.detail) {
            message = payload.detail;
          }
        } catch (err) {
          // ignore JSON parsing failures
        }
        throw new Error(message);
      }
      const payload = text ? JSON.parse(text) : {};
      setToken(payload.token || '');
      setUsername('');
      setTotp('');
    } catch (err) {
      setError(err.message);
    }
  };

  const handleLogout = () => {
    setToken('');
    setSnapshot(null);
    setHistory([]);
    setSummary(null);
    setAutoRefresh(false);
  };

  const handleRepay = async (event) => {
    event.preventDefault();
    setError('');
    setActionMessage('');
    const amount = parseFloat(repayAmount);
    if (!Number.isFinite(amount) || amount <= 0) {
      setError('Enter a positive repayment amount.');
      return;
    }
    try {
      const payload = await fetchWithAuth('/api/actions/repay', {
        method: 'POST',
        body: JSON.stringify({ amount, profile }),
      });
      setActionMessage(
        `Repaid ${formatUsd(amount)}. New principal: ${formatUsd(payload.principal)}.`,
      );
      setRepayAmount('');
      fetchAll();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleTransfer = async (event) => {
    event.preventDefault();
    setError('');
    setActionMessage('');
    const amount = parseFloat(transferAmount);
    if (!Number.isFinite(amount) || amount < 0) {
      setError('Enter a non-negative transfer amount.');
      return;
    }
    try {
      const payload = await fetchWithAuth('/api/actions/reserves/transfer', {
        method: 'POST',
        body: JSON.stringify({
          asset: transferAsset,
          amount,
          direction: transferDirection,
          profile,
        }),
      });
      const balances = payload.balances?.[transferAsset];
      if (balances) {
        setActionMessage(
          `Updated ${transferAsset.toUpperCase()} balances — pledged: ${balances.pledged.toFixed(
            4,
          )}, reserve: ${balances.unpledged.toFixed(4)}.`,
        );
      } else {
        setActionMessage('Transfer completed.');
      }
      setTransferAmount('');
      fetchAll();
    } catch (err) {
      setError(err.message);
    }
  };

  const collateralCards = useMemo(() => {
    if (!snapshot) {
      return [];
    }
    const breakdown = snapshot.collateral_breakdown || {};
    return [
      {
        title: 'BTC Collateral',
        value: `${(breakdown.btc?.amount ?? 0).toFixed(6)} BTC`,
        subtitle: formatUsd(breakdown.btc?.usd_value ?? 0),
      },
      {
        title: 'USDT Collateral',
        value: formatUsd(breakdown.usdt?.usd_value ?? 0),
        subtitle: `${(breakdown.usdt?.amount ?? 0).toFixed(2)} USDT`,
      },
      {
        title: 'Total Collateral',
        value: formatUsd(snapshot.collateral_value),
        subtitle: `Debt: ${formatUsd(snapshot.debt)}`,
      },
    ];
  }, [snapshot]);

  const levelSummary = useMemo(() => {
    if (!summary) {
      return [];
    }
    const levels = summary.levels || {};
    return [
      { label: 'Observations', value: summary.count },
      { label: 'Average LTV', value: formatPct(summary.average ?? 0) },
      { label: 'Max LTV', value: formatPct(summary.max ?? 0) },
      { label: 'Margin / Liquidation Events', value: `${summary.margin_events} / ${summary.liquidation_events}` },
      {
        label: 'Recent Trend',
        value: `${(summary.recent_trend * 100).toFixed(2)}% over window`,
      },
      {
        label: 'Alerts (Warning/Margin/Liquidation)',
        value: `${levels.warning || 0} / ${levels.margin_call || 0} / ${levels.liquidation || 0}`,
      },
    ];
  }, [summary]);

  return (
    <Layout disablePaddingBottom>
      <Container size="large">
        <div className={styles.page}>
          <h1 className={styles.heading}>Loan Health Dashboard</h1>
          <p className={styles.intro}>
            Monitor live LTV metrics, collateral buffers, and recent history. Set up alerts in the CLI and
            use this dashboard for a quick situational overview.
          </p>

          {!token ? (
            <form className={styles.loginPanel} onSubmit={handleLogin}>
              <h2>Authenticate</h2>
              <p>
                Enter your username and current TOTP code to request a viewer or trader token. The API
                base URL defaults to <code>{API_BASE}</code> and can be overridden with the
                <code>GATSBY_API_BASE_URL</code> environment variable.
              </p>
              <label>
                Username
                <input value={username} onChange={(event) => setUsername(event.target.value)} required />
              </label>
              <label>
                TOTP Code
                <input
                  value={totp}
                  onChange={(event) => setTotp(event.target.value)}
                  required
                  inputMode="numeric"
                  pattern="[0-9]*"
                />
              </label>
              <button type="submit" className={styles.primaryButton}>
                Sign in
              </button>
              {error ? <div className={styles.error}>{error}</div> : null}
            </form>
          ) : (
            <>
              <div className={styles.toolbar}>
                <div className={styles.toolbarGroup}>
                  <label>
                    Profile
                    <select
                      value={profile}
                      onChange={(event) => setProfile(event.target.value)}
                      className={styles.profileSelect}
                    >
                      {profiles.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name || item.id}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className={styles.checkbox}>
                    <input
                      type="checkbox"
                      checked={autoRefresh}
                      onChange={(event) => setAutoRefresh(event.target.checked)}
                    />
                    Auto refresh (15s)
                  </label>
                </div>
                <div className={styles.toolbarGroup}>
                  <button type="button" onClick={fetchAll} className={styles.secondaryButton}>
                    Refresh
                  </button>
                  <button type="button" onClick={handleLogout} className={styles.linkButton}>
                    Sign out
                  </button>
                </div>
              </div>
              {error ? <div className={styles.error}>{error}</div> : null}
              {actionMessage ? <div className={styles.success}>{actionMessage}</div> : null}
              {loading ? <div className={styles.loading}>Loading latest metrics…</div> : null}

              {snapshot ? (
                <section className={styles.snapshotSection}>
                  <div className={styles.statusCard}>
                    <h2>Status</h2>
                    <p className={`${styles.statusValue} ${STATUS_CLASS[snapshot.status] || ''}`}>
                      {snapshot.status.replace('_', ' ')}
                    </p>
                    <p className={styles.ltvValue}>{(snapshot.ltv * 100).toFixed(2)}% LTV</p>
                    <p className={styles.statusDetail}>{snapshot.recommended_action}</p>
                    <div className={styles.bufferRow}>
                      <span>Margin buffer</span>
                      <strong>
                        {snapshot.margin_buffer_pct?.toFixed(2)}% (
                        {snapshot.margin_buffer_drop_pct !== null && snapshot.margin_buffer_drop_pct !== undefined
                          ? `${snapshot.margin_buffer_drop_pct.toFixed(2)}% drop`
                          : 'stable'}
                        )
                      </strong>
                    </div>
                    <div className={styles.bufferRow}>
                      <span>BTC Margin Price</span>
                      <strong>
                        {snapshot.margin_price ? formatUsd(snapshot.margin_price) : 'n/a'}
                      </strong>
                    </div>
                    <p className={styles.timestamp}>
                      Last updated {lastUpdated ? lastUpdated.toLocaleTimeString() : '—'} · BTC price{' '}
                      {formatUsd(snapshot.btc_price)}
                    </p>
                  </div>

                  <div className={styles.statsGrid}>
                    {collateralCards.map((card) => (
                      <div key={card.title} className={styles.statCard}>
                        <h3>{card.title}</h3>
                        <p className={styles.statValue}>{card.value}</p>
                        <p className={styles.statSubtitle}>{card.subtitle}</p>
                      </div>
                    ))}
                    <div className={styles.statCard}>
                      <h3>Thresholds</h3>
                      <p className={styles.statValue}>{(snapshot.thresholds.warning * 100).toFixed(1)}% · Warning</p>
                      <p className={styles.statValue}>{(snapshot.thresholds.margin_call * 100).toFixed(1)}% · Margin</p>
                      <p className={styles.statValue}>
                        {(snapshot.thresholds.liquidation * 100).toFixed(1)}% · Liquidation
                      </p>
                    </div>
                    <div className={styles.statCard}>
                      <h3>Notes</h3>
                      {snapshot.notes?.length ? (
                        <ul>
                          {snapshot.notes.map((note) => (
                            <li key={note}>{note}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className={styles.emptyState}>No additional notes logged.</p>
                      )}
                    </div>
                  </div>
                </section>
              ) : null}

              <section className={styles.historySection}>
                <div className={styles.historyHeader}>
                  <h2>LTV History</h2>
                  <span>
                    {history.length} points · window {summary?.hours_covered?.toFixed(1) || 0} hours
                  </span>
                </div>
                <TrendSparkline data={history} thresholds={snapshot?.thresholds} />
                <RecentEvents entries={history} />
                <div className={styles.summaryGrid}>
                  {levelSummary.map((item) => (
                    <div key={item.label} className={styles.summaryCard}>
                      <span>{item.label}</span>
                      <strong>{item.value}</strong>
                    </div>
                  ))}
                </div>
              </section>

              <section className={styles.actionsSection}>
                <div className={styles.actionCard}>
                  <h2>Repay Loan</h2>
                  <form onSubmit={handleRepay} className={styles.actionForm}>
                    <label>
                      Amount (USDT)
                      <input
                        value={repayAmount}
                        onChange={(event) => setRepayAmount(event.target.value)}
                        inputMode='decimal'
                        placeholder='100'
                      />
                    </label>
                    <button type='submit' className={styles.primaryButton}>
                      Submit Repayment
                    </button>
                  </form>
                </div>
                <div className={styles.actionCard}>
                  <h2>Transfer Reserves</h2>
                  <form onSubmit={handleTransfer} className={styles.actionForm}>
                    <label>
                      Asset
                      <select
                        value={transferAsset}
                        onChange={(event) => setTransferAsset(event.target.value)}
                      >
                        <option value='btc'>BTC</option>
                        <option value='usdt'>USDT</option>
                      </select>
                    </label>
                    <label>
                      Direction
                      <select
                        value={transferDirection}
                        onChange={(event) => setTransferDirection(event.target.value)}
                      >
                        <option value='to_collateral'>To pledged collateral</option>
                        <option value='to_reserve'>Back to reserves</option>
                      </select>
                    </label>
                    <label>
                      Amount
                      <input
                        value={transferAmount}
                        onChange={(event) => setTransferAmount(event.target.value)}
                        inputMode='decimal'
                        placeholder='0.05'
                      />
                    </label>
                    <button type='submit' className={styles.secondaryButton}>
                      Execute Transfer
                    </button>
                  </form>
                </div>
              </section>
            </>
          )}
        </div>
      </Container>
    </Layout>
  );
};

export default DashboardPage;
