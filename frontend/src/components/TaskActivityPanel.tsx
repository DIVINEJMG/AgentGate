import { useEffect, useRef, useState } from 'react';
import { loadTaskActivity, type ActivitySnapshot, type TaskActivity } from '../lib/taskActivityApi';
import type { ApiVersion } from '../lib/systemApi';
import { subscribeOrganizationRealtime } from '../platform/realtimeClient';
import { activityElapsed, isActivityActive, isActivityTerminal, phaseLabel } from './taskActivityModel';
import { startActivityFeed } from './taskActivityFeed';

export function useTaskActivity(organizationId?: string, threadId?: string | null, version?: ApiVersion) {
  const [snapshot, setSnapshot] = useState<ActivitySnapshot | null>(null);
  const [snapshotKey, setSnapshotKey] = useState('');
  const [disconnected, setDisconnected] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    setSnapshot(null);
    setDisconnected(false);
    if (!organizationId || !threadId || !version) return;
    const feed = startActivityFeed({
      load: signal => loadTaskActivity(version, organizationId, threadId, signal),
      onSnapshot: value => { setSnapshot(value); setSnapshotKey(`${organizationId}:${threadId}:${version}`); }, onConnectivity: connected => setDisconnected(!connected),
      nextIntervalMs: value => value.tasks.some(isActivityActive) ? 10000 : 60000,
    });
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    const unsubscribe = subscribeOrganizationRealtime({organizationId,
      eventTypes: ['conversation.response.created', 'run.progress', 'run.completed', 'action.proposed', 'run.waiting_approval'],
      onEvent: () => void feed.refresh(),
    });
    return () => { feed.stop(); window.clearInterval(tick); unsubscribe(); };
  }, [organizationId, threadId, version]);
  return { snapshot: snapshotKey === `${organizationId}:${threadId}:${version}` ? snapshot : null, disconnected, now };
}

export function TaskActivityPanel({tasks, observedAt, disconnected = false, now = Date.now(), historical = false}: {
  tasks: TaskActivity[]; observedAt: string; disconnected?: boolean; now?: number; historical?: boolean;
}) {
  if (!tasks.length) return null;
  if (!historical) return <LiveProcess tasks={tasks} observedAt={observedAt} disconnected={disconnected} now={now}/>;
  return <section className="cf-task-activity" aria-label="Accepted work activity">
    <header><span>{historical ? 'PROCESS RECORD' : 'LIVE PROCESS'}</span><small>{disconnected ? 'Updates interrupted · reconnecting; saved activity retained' : historical ? 'Saved activity' : 'Live updates'}</small></header>
    {tasks.map(task => <article key={task.id}>
      <div className="cf-task-phase"><strong>{phaseLabel(task.phase)}</strong><time>{activityElapsed(task, observedAt, disconnected ? Date.parse(observedAt) : now)}</time></div>
      <p role="status">{task.detail}</p>
      <small>Last activity: {new Date(task.updatedAt).toLocaleTimeString()} · {task.completedActions} completed action{task.completedActions === 1 ? '' : 's'}</small>
      <small>Updates checked: {new Date(observedAt).toLocaleTimeString()}</small>
      {!isActivityTerminal(task.status) && task.phase === 'awaiting_planner' && task.responseState === 'awaiting' && <p className="cf-task-wait">Still waiting for the response. No new action has been reported.</p>}
      {task.responseState === 'receiving' && <p>Response data received: {task.responseBytes ?? 0} bytes, last observed at {task.responseReceivedAt ? new Date(task.responseReceivedAt).toLocaleTimeString() : 'the latest check'}. Waiting for a complete, validated action.</p>}
      {task.responseState === 'received' && <p>Response received; checking the proposed action.</p>}
      {task.phase === 'awaiting_planner' && task.deadlineAt && <small>Decision deadline: {new Date(task.deadlineAt).toLocaleTimeString()}</small>}
      {task.retryAt && <p>Next retry: {new Date(task.retryAt).toLocaleTimeString()}</p>}
      {task.preservedWork && task.phase === 'recovery' && <p>Completed work remains saved.</p>}
      {!!task.events.length && <details><summary>Observed actions ({task.events.length})</summary><ul>{task.events.map(event => <li key={event.id}>{event.label} · {event.state.replaceAll('_', ' ')}{event.state === 'completed' ? event.verified ? ' · verified' : ' · outcome unverified' : ''}</li>)}</ul></details>}
      {task.commands.map(command => <details key={command.id}><summary>Command: {command.status}{command.exitCode != null ? ` · exit ${command.exitCode}` : ''}</summary><code>{command.command}</code><small>Output observed: {new Date(command.observedAt).toLocaleTimeString()}</small><pre>{command.stdout || command.stderr ? [command.stdout, command.stderr].filter(Boolean).join('\n') : 'No output recorded yet.'}</pre></details>)}
      {!!task.changedFiles.length && <details><summary>Observed changed files ({task.changedFiles.length})</summary><ul>{task.changedFiles.map(path => <li key={path}>{path}</li>)}</ul></details>}
      {task.links.map(link => <a key={link.url} href={link.url} target="_blank" rel="noreferrer">{link.title}</a>)}
    </article>)}
  </section>;
}

function LiveProcess({tasks, observedAt, disconnected, now}: {
  tasks: TaskActivity[]; observedAt: string; disconnected: boolean; now: number;
}) {
  const [selectedId, setSelectedId] = useState(tasks[0]?.id);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!tasks.some(item => item.id === selectedId)) {
      dialog.current?.close();
      setSelectedId(tasks[0]?.id);
    }
  }, [tasks, selectedId]);
  const task = tasks.find(item => item.id === selectedId) ?? tasks[0];
  if (!task) return null;
  return <section className="cf-live-process" aria-label="Current task process">
    <div className="cf-live-process-main">
      <span className={'cf-process-indicator' + (disconnected ? ' is-disconnected' : '')} aria-hidden="true"/>
      <div className="cf-live-process-copy">
        <div className="cf-live-process-title">
          <span>{disconnected ? 'Last observed' : task.executionState === 'retry_scheduled' ? 'Retry scheduled' : 'Live process'}</span>
          {tasks.length > 1 ? <select aria-label="Select active task" value={task.id} onChange={event => setSelectedId(event.target.value)}>
            {tasks.map((item, index) => <option key={item.id} value={item.id}>Task {index + 1} · {phaseLabel(item.phase)}</option>)}
          </select> : <strong>{phaseLabel(task.phase)}</strong>}
        </div>
        <p role="status" title={task.detail}>{disconnected ? 'Updates interrupted · reconnecting' : task.detail}</p>
      </div>
      <time>{activityElapsed(task, observedAt, disconnected ? Date.parse(observedAt) : now)}</time>
      <button type="button" className="cf-process-details-button" onClick={() => dialog.current?.showModal()}>Details</button>
    </div>
    <dialog ref={dialog} className="cf-process-dialog" aria-label="Task process details" onClick={event => {
      if (event.target === dialog.current) dialog.current?.close();
    }}>
      <div className="cf-process-dialog-content">
        <header><strong>Task process</strong><button type="button" onClick={() => dialog.current?.close()} aria-label="Close process details">Close</button></header>
        <TaskActivityPanel tasks={[task]} observedAt={observedAt} disconnected={disconnected} now={now} historical/>
      </div>
    </dialog>
  </section>;
}
