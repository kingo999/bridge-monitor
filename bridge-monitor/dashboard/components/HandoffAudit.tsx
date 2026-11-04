import { useState } from 'react';
import { useHandoffPage } from '@/lib/useHandoffPage';

const actions: Record<string, string> = {
  submit: 'Brief received', revise: 'Revised brief received', revise_requested: 'Revision requested',
  pause: 'Workflow paused', resume: 'Planning resumed', accept: 'Plan accepted',
  reject: 'Plan rejected', cancel: 'Workflow cancelled', delivery_receipt: 'Delivery acknowledged',
};

export default function HandoffAudit() {
  const [input, setInput] = useState('');
  const [workflow, setWorkflow] = useState('');
  const [cursors, setCursors] = useState<string[]>(['']);
  const query = new URLSearchParams({ workflow });
  const before = cursors[cursors.length - 1];
  if (before) query.set('before', before);
  const { page, error } = useHandoffPage(query.toString());
  return <section aria-label="ChatGPT to Codex handoff history">
    <h3>ChatGPT → Codex handoffs</h3>
    <p>Briefs, decisions and commands recorded by Design Mode. Each row is one recorded handoff.</p>
    <form className="audit-filters" onSubmit={event => {
      event.preventDefault(); setWorkflow(input.trim()); setCursors(['']);
    }}>
      <label>Workflow ID<input value={input} onChange={event => setInput(event.target.value)} /></label>
      <button type="submit">Filter handoffs</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {!page && !error && <p role="status">Loading handoffs…</p>}
    {page && !page.available && <p role="status">Design Mode history is unavailable.</p>}
    {page?.available && <>
      <p>{page.total?.toLocaleString()} recorded handoffs</p>
      <div className="cc-table-wrap"><table className="cc-table"><thead><tr>
        <th>Received</th><th>Handoff outcome</th><th>Workflow</th><th>Iteration</th><th>Audit ID</th>
      </tr></thead><tbody>{page.rows.map(row => <tr key={row.id}>
        <td>{new Date(row.time).toLocaleString()}</td><td>{actions[row.action] || row.action}</td>
        <td>{row.workflow_id}</td><td>{row.iteration}</td><td>{row.id}</td></tr>)}
        {!page.rows.length && <tr><td colSpan={5}>No recorded handoffs match this view.</td></tr>}
      </tbody></table></div>
      <div className="audit-pagination">
        <button disabled={cursors.length < 2} onClick={() => setCursors(c => c.slice(0, -1))}>Previous page</button>
        <button disabled={!page.next} onClick={() => page.next && setCursors([...cursors, page.next])}>Next page</button>
      </div>
    </>}
  </section>;
}
