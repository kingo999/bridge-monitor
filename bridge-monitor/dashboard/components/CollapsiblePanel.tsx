import type { ReactNode } from 'react';

/** Native details/summary panel. Closed by default; open state is uncontrolled so React refreshes never reset it, and children stay mounted. */
export default function CollapsiblePanel({ label, title, subtitle, badge, className = '', children }: {
  label: string; title: string; subtitle?: ReactNode; badge?: ReactNode; className?: string; children: ReactNode;
}) {
  return <section className={`panel collapsible-panel ${className}`} aria-label={label}>
    <details>
      <summary><div className='summary-text'><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
        {badge && <span className='pill'>{badge}</span>}</summary>
      <div className='collapsible-body'>{children}</div>
    </details>
  </section>;
}
