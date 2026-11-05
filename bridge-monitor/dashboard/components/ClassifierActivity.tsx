import type { ClassifierActivity as Activity } from '@/lib/api';
import type { FlowModelSetting } from '@/lib/contracts.generated';
import { formatFlowModel } from '@/lib/flowActivity';
import NodeActivityStatus, { nodeActivityClass, nodeActivityState } from './NodeActivityStatus';

export default function ClassifierActivity({ activity, failed, paused, modelSetting }: {
  activity: Activity | null; failed: boolean; paused: boolean; modelSetting?: FlowModelSetting;
}) {
  const state = nodeActivityState({ active: activity?.classifying ?? 0, queued: activity?.queued ?? 0,
    uncertain: activity?.uncertain ?? 0, available: !!activity?.available && !failed, paused });
  const latest = activity?.latest;
  return <div className={`flow-node gemma-activity${nodeActivityClass(state)}`}
    role="region" aria-label="Gemma advisory activity">
    <div className="node-orb gemma">◈</div><strong>Gemma classifier</strong><small>Advisory only · local inference</small>
    <small className="flow-model-details">{formatFlowModel(modelSetting?.model, modelSetting?.effort, 'Configured')}</small>
    <NodeActivityStatus state={state} queued={activity?.queued ?? 0}
      label={!paused && !failed && !activity ? 'Loading activity…' : undefined} />
    {!paused && !failed && activity?.available && <>
      <small>{activity.classifying} classifying · {activity.queued} queued</small>
      {!!activity.uncertain && <small>{activity.uncertain} uncertain · no automatic resend</small>}
      {latest ? <small>Last: {latest.state}{latest.rec_route ? ` · ${latest.rec_route}` : ''}<br />
        {new Date(latest.completed_at || latest.dispatched_at || latest.captured_at).toLocaleTimeString()} · {latest.origin_kind}</small>
        : <small>No observations yet</small>}
      <small>Checked {new Date(activity.checked_at).toLocaleTimeString()}</small>
    </>}
  </div>;
}
