function riskBadge(level) {
  const normalized = (level || '').toString().toLowerCase();
  const labelMap = {
    high: '高风险',
    medium: '中风险',
    low: '低风险',
  };
  const badgeMap = {
    high: 'badge-high',
    medium: 'badge-mid',
    low: 'badge-low',
  };
  const label = labelMap[normalized] || level || '低风险';
  const badgeClass = badgeMap[normalized] || 'badge-low';
  return `<span class="badge ${badgeClass}">${label}</span>`;
}
