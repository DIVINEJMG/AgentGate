import type { ReactNode } from 'react';

interface MetricCardProps { label: string; value: string; helper: string; icon: ReactNode; tone?: 'neutral' | 'success' | 'danger'; }

export default function MetricCard({ label, value, helper, icon, tone = 'neutral' }: MetricCardProps) {
    return <article className={`metric-card ${tone}`}><div className="metric-top"><span className="metric-label">{label}</span><span className="metric-icon">{icon}</span></div><div className="metric-value">{value}</div><div className="metric-helper">{helper}</div></article>;
}