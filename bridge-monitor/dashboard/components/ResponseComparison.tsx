import { useState } from 'react';

function ResponsePane({ title, model, content, state }: { title: string; model: string; content: string | null; state: string }) {
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState(false);
  const lines = content?.split('\n') || [];
  const matching = search ? lines.filter(line => line.toLowerCase().includes(search.toLowerCase())) : lines;
  return <section className="response-pane"><div className="response-title"><strong>{title}</strong><span className="pill model">{model}</span></div>
    <div className="body-tools"><input aria-label={`Search ${title}`} type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Find in response" />
      <button type="button" onClick={() => content && navigator.clipboard.writeText(content)} disabled={!content}>Copy</button>
      <button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? 'Collapse' : 'Expand'}</button></div>
    <pre className={`response-body ${expanded ? 'expanded' : ''}`}>{content == null ? state : matching.join('\n')}</pre>
  </section>;
}

export default function ResponseComparison({ local, downstream, localModel, downstreamModel,
  localState, downstreamState }: { local: string | null; downstream: string | null;
  localModel: string; downstreamModel: string; localState: string; downstreamState: string;
}) {
  return <div className="comparison"><ResponsePane title="Qwen original" model={localModel} content={local} state={localState} />
    <ResponsePane title="Paid downstream response" model={downstreamModel} content={downstream} state={downstreamState} /></div>;
}
