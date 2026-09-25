import { useEffect, useState } from 'react';
import { Activity, Bot, Eye, Gauge, TriangleAlert } from 'lucide-react';
import { loadAIOperations, type AIOperationsSnapshot } from '../lib/aiOperationsApi';
import type { ApiVersion } from '../lib/systemApi';

function value(number: number | null, suffix = '') {
  return number === null ? '—' : `${number}${suffix}`;
}

export default function AIOperationsPanel({
  organizationId,
  apiVersion,
}: {
  organizationId: string;
  apiVersion: ApiVersion;
}) {
  const [data, setData] = useState<AIOperationsSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    loadAIOperations(apiVersion, organizationId)
      .then((next) => {
        if (active) {
          setData(next);
          setError(null);
        }
      })
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : 'AI diagnostics unavailable.');
      });
    return () => { active = false; };
  }, [apiVersion, organizationId]);

  if (error) return <div className="inline-error">{error}</div>;
  if (!data) return <div className="audit-empty">Loading AI operations…</div>;

  const errors = Object.entries(data.invocations.errorsByCategory);
  const routes = Object.entries(data.invocations.roleRouting);

  return (
    <section className="ai-ops-section">
      <div className="section-title">
        <div>
          <p className="panel-kicker">AI OPERATIONS</p>
          <h2>Provider & model diagnostics</h2>
        </div>
        <span className={`status-chip ${data.configuration.providerHealth === 'ready' ? 'success' : 'neutral'}`}>
          {data.configuration.providerHealth.toUpperCase()}
        </span>
      </div>

      <div className="ai-ops-grid">
        <article className="panel ai-ops-card">
          <div className="panel-heading">
            <div><p className="panel-kicker">CONFIGURATION</p><h3>{data.configuration.provider}</h3></div>
            <Bot size={17} />
          </div>
          <dl className="product-stats">
            <div><dt>Enabled</dt><dd>{data.configuration.enabled ? 'Yes' : 'No'}</dd></div>
            <div><dt>Configured</dt><dd>{data.configuration.configured ? 'Yes' : 'No'}</dd></div>
            <div><dt>Coordinator</dt><dd>{data.configuration.coordinatorModel}</dd></div>
            <div><dt>Vision</dt><dd>{data.configuration.visionModel}</dd></div>
          </dl>
          <p className="panel-note">Credentials are intentionally never returned by this diagnostics surface.</p>
        </article>

        <article className="panel ai-ops-card">
          <div className="panel-heading">
            <div><p className="panel-kicker">RECENT INVOCATIONS</p><h3>{data.invocations.sampleSize} sampled</h3></div>
            <Activity size={17} />
          </div>
          <dl className="product-stats">
            <div><dt>Success rate</dt><dd>{value(data.invocations.successRate, '%')}</dd></div>
            <div><dt>Average latency</dt><dd>{value(data.invocations.averageLatencyMs, ' ms')}</dd></div>
            <div><dt>Failures</dt><dd>{data.invocations.failures}</dd></div>
            <div><dt>Planner waiting</dt><dd>{data.planner.waitingCount}</dd></div>
          </dl>
        </article>
      </div>

      <div className="ai-ops-grid">
        <article className="panel ai-ops-card">
          <div className="panel-heading">
            <div><p className="panel-kicker">MODEL ROLE ROUTING</p><h3>Active routes</h3></div>
            <Gauge size={17} />
          </div>
          {routes.length ? (
            <div className="ai-route-list">
              {routes.map(([role, route]) => (
                <div key={role}>
                  <span>{role}</span>
                  <strong>{route.model}</strong>
                  <small>{route.provider} · {route.invocations} calls</small>
                </div>
              ))}
            </div>
          ) : <p className="panel-note">No AI invocations have been recorded for this workspace yet.</p>}
        </article>

        <article className="panel ai-ops-card">
          <div className="panel-heading">
            <div><p className="panel-kicker">RECOVERY</p><h3>Provider errors</h3></div>
            {errors.length ? <TriangleAlert size={17} /> : <Eye size={17} />}
          </div>
          {errors.length ? (
            <div className="ai-error-list">
              {errors.map(([category, count]) => (
                <div key={category}><span>{category.replaceAll('_', ' ')}</span><strong>{count}</strong></div>
              ))}
            </div>
          ) : <p className="panel-note">No provider errors are present in the recent invocation window.</p>}
          <p className="panel-note">
            Waiting for provider: {data.planner.waitingProvider} · Waiting for configuration: {data.planner.waitingConfiguration}
          </p>
        </article>
      </div>
    </section>
  );
}
