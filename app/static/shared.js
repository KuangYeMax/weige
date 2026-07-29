window._badgeClass = function(status) {
  const map = {
    'pending': 'badge-pending',
    'generating': 'badge-generating',
    'ready': 'badge-ready',
    'sending': 'badge-sending',
    'awaiting_confirmation': 'badge-awaiting_confirmation',
    'needs_review': 'badge-needs_review',
    'dry_run_complete': 'badge-dry_run_complete',
    'sent': 'badge-sent',
    'failed': 'badge-failed',
    'abandoned': 'badge-abandoned',
  };
  return 'status-badge ' + (map[status] || 'badge-pending');
};

window._statusLabel = function(status) {
  const map = {
    'pending': '待发送',
    'generating': '生成中',
    'ready': '就绪',
    'sending': '发送中',
    'awaiting_confirmation': '待人工确认',
    'needs_review': '需要复核',
    'dry_run_complete': '模拟完成',
    'sent': '已发送',
    'failed': '发送失败',
    'abandoned': '已放弃',
  };
  return map[status] || status;
};

window._formatTime = function(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  } catch { return iso; }
};

window._navHTML = function(activePage) {
  const links = [
    { href: '/dashboard', label: '告警台', key: 'dashboard' },
    { href: '/products', label: '产品库', key: 'products' },
    { href: '/dispatch', label: '待发列表', key: 'dispatch' },
    { href: '/logs', label: '发送日志', key: 'logs' },
    { href: '/settings', label: '设置', key: 'settings' },
    { href: '/workbench', label: '生图工作台', key: 'workbench' },
  ];
  const navItems = links.map(l =>
    `<a href="${l.href}" class="nav-link${l.key === activePage ? ' active' : ''}">${l.label}</a>`
  ).join('\n      ');
  return `<aside class="sidebar">
    <a href="/dashboard" class="sidebar-brand">
      <span class="brand-icon">同</span>
      <span>同物景</span>
    </a>
    <nav class="sidebar-nav">
      ${navItems}
    </nav>
  </aside>`;
};
