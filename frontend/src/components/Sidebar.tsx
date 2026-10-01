import { Activity, Bot, Command, House, LogOut, Network, PanelLeftClose, PanelLeftOpen, PanelsTopLeft, Settings2, Shield, X } from 'lucide-react';
import { PRIMARY_SECTIONS, sectionForView, type AppSection, type AppView } from '../navigation';

interface SidebarProps {
  open: boolean;
  collapsed: boolean;
  onClose: () => void;
  onToggleCollapse: () => void;
  activeView: AppView;
  onNavigate: (view: AppView) => void;
  organizationName: string;
  accountName: string;
  onSignOut?: () => void;
}

const sectionIcons: Record<AppSection, typeof House> = {
  home: House,
  workforce: Bot,
  operations: Activity,
  governance: Shield,
  connections: Network,
  organization: PanelsTopLeft,
};

export default function Sidebar({ open, collapsed, onClose, onToggleCollapse, activeView, onNavigate, organizationName, accountName, onSignOut }: SidebarProps) {
  const activeSection = sectionForView(activeView);
  const initials = organizationName.trim().slice(0, 1).toUpperCase() || 'A';

  function navigate(view: AppView) {
    onNavigate(view);
    onClose();
  }

  return <>
    <aside id='workspace-primary-sidebar' className={`sidebar workspace-sidebar${open ? ' open' : ''}${collapsed ? ' is-collapsed' : ''}`} aria-label='Primary navigation' onClick={(event) => {
      if (!collapsed || window.matchMedia('(max-width: 760px)').matches) return;
      if (event.target instanceof Element && event.target.closest('button, a, [role="button"], [role="link"]')) return;
      onToggleCollapse();
    }}>
      <div className='workspace-sidebar-brand'>
        <button type='button' className='workspace-sidebar-logo' aria-label='Go to overview' title='Overview' onClick={() => navigate('overview')}><img src='/audoryn-mark.png' alt='' /></button>
        <div className='workspace-sidebar-brand-copy'><strong>Audoryn</strong><span>Workspace</span></div>
        <button type='button' className='workspace-sidebar-mobile-close' aria-label='Close navigation' onClick={onClose}><X size={17} /></button>
      </div>
      <div className='workspace-sidebar-main'>
        <div className='workspace-sidebar-section-label'>WORKSPACE</div>
        <nav className='workspace-sidebar-nav' aria-label='Workspace sections'>
          {PRIMARY_SECTIONS.map((section) => {
            const Icon = sectionIcons[section.id];
            return <button type='button' key={section.id} className={`workspace-sidebar-item${activeSection === section.id ? ' is-active' : ''}`} onClick={() => navigate(section.defaultView)} aria-current={activeSection === section.id ? 'page' : undefined} aria-label={section.label} title={collapsed ? section.label : undefined}><Icon size={18} strokeWidth={1.7} aria-hidden='true' /><span>{section.label}</span></button>;
          })}
        </nav>
      </div>
      <div className='workspace-sidebar-bottom'>
        <button type='button' className='workspace-sidebar-item workspace-sidebar-help' onClick={() => navigate('settings')} aria-label='Workspace settings' title={collapsed ? 'Workspace settings' : undefined}><Settings2 size={18} strokeWidth={1.7} aria-hidden='true' /><span>Workspace settings</span></button>
        <div className='workspace-sidebar-mobile-account'><span className='workspace-sidebar-mobile-account-name'>{accountName}</span>{onSignOut && <button type='button' onClick={onSignOut}><LogOut size={16} aria-hidden='true' /><span>Sign out</span></button>}</div>
        <button type='button' className='workspace-sidebar-collapse' aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} aria-expanded={!collapsed} onClick={onToggleCollapse}>{collapsed ? <PanelLeftOpen size={17} aria-hidden='true' /> : <><PanelLeftClose size={17} aria-hidden='true' /><span>Collapse sidebar</span></>}</button>
        <div className='workspace-sidebar-account' title={organizationName}><span className='workspace-sidebar-avatar'>{initials}</span><span className='workspace-sidebar-account-copy'><strong>{organizationName}</strong><small>Organization</small></span><Command size={14} aria-hidden='true' /></div>
      </div>
    </aside>
    {open && <button type='button' className='sidebar-overlay' aria-label='Close navigation overlay' onClick={onClose} />}
  </>;
}
