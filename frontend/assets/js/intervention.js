function formatBeijingTime(value) {
  if (!value) return "-";
  const raw = String(value);
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(raw) ? raw : `${raw}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date).replace(/\//g, "-");
}
let interventionQueue = [];
let currentInterventionId = null;

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function statusLabel(status) {
  const map = {
    not_generated: '未生成',
    fallback: '本地草稿',
    success: 'AI生成',
    draft_ready: '草稿待审核',
    approved: '审核通过',
    rejected: '已驳回',
    not_reviewed: '未审核',
    sent: '已处理',
    not_sent: '未处理',
    waiting_reply: '待生成',
  };
  return map[status] || status || '-';
}

function toPercent(score) {
  const raw = Number(score || 0);
  return Math.max(0, Math.min(100, Math.round(raw <= 1 ? raw * 100 : raw)));
}

function riskLabel(level, score) {
  const levelMap = { high: '高风险', medium: '中风险', low: '低风险' };
  return `${levelMap[level] || level || '-'} / ${toPercent(score)}分`;
}

function splitLabels(labels) {
  return String(labels || '')
    .split(/[、,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function setReplyStatus(message, tone = 'muted') {
  const target = document.getElementById('replyStatus');
  if (!target) return;
  const colors = {
    muted: '#667085',
    success: '#13785a',
    warning: '#a86413',
    danger: '#b94141',
  };
  target.style.color = colors[tone] || colors.muted;
  target.textContent = message;
}

async function checkedGet(url) {
  const data = await apiGet(url);
  if (data?.detail) throw new Error(data.detail);
  return data;
}

async function checkedPost(url, body = {}) {
  const data = await apiPost(url, body);
  if (data?.detail) throw new Error(data.detail);
  return data;
}

async function checkedPut(url, body) {
  const data = await apiPut(url, body);
  if (data?.detail) throw new Error(data.detail);
  return data;
}

function renderMetrics(items) {
  document.getElementById('metricTotal').textContent = items.length;
  document.getElementById('metricDraft').textContent = items.filter((item) => item.reply_status === 'draft_ready').length;
  document.getElementById('metricApproved').textContent = items.filter((item) => item.reply_status === 'approved' && item.send_status !== 'sent').length;
  document.getElementById('metricSent').textContent = items.filter((item) => item.send_status === 'sent').length;
}

function renderRiskList(items) {
  const target = document.getElementById('riskList');
  if (!items.length) {
    target.innerHTML = '<div class="text-center text-muted py-4">暂无达到阈值的高风险帖子</div>';
    return;
  }

  target.innerHTML = items.map((item) => {
    const active = item.id === currentInterventionId ? 'active' : '';
    const status = item.send_status === 'sent' ? item.send_status : item.reply_status;
    const tags = splitLabels(item.labels).slice(0, 3).map((tag) => `<span>${escapeHtml(tag)}</span>`).join('');
    return `
      <article class="risk-card ${active}" onclick="openReplyWorkbench(${item.id})">
        <div class="d-flex justify-content-between gap-2 flex-wrap">
          <span class="badge-soft badge-danger"><i class="fa-solid fa-triangle-exclamation"></i>${escapeHtml(riskLabel(item.risk_level, item.risk_score))}</span>
          <span class="badge-soft ${item.send_status === 'sent' ? 'badge-success' : ''}">${escapeHtml(statusLabel(status))}</span>
        </div>
        <div class="risk-card-title">${escapeHtml(item.title || '无标题帖子')}</div>
        <div class="risk-card-summary">${escapeHtml(item.excerpt || item.content || '暂无摘要')}</div>
        <div class="risk-meta">
          <span><i class="fa-solid fa-user me-1"></i>${escapeHtml(item.author_name || '-')}</span>
          <span><i class="fa-solid fa-bell me-1"></i>预警 ${escapeHtml(item.alert_id)}</span>
          ${tags}
        </div>
      </article>
    `;
  }).join('');
}

function renderRiskVisual(data) {
  const percent = toPercent(data.risk.risk_score);
  document.getElementById('riskScoreValue').textContent = percent;
  document.getElementById('scoreRing').style.setProperty('--score', `${percent}%`);

  const tags = splitLabels(data.risk.labels);
  document.getElementById('riskTagCloud').innerHTML = tags.length
    ? tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join('')
    : '<span>暂无标签</span>';
}

async function loadInterventions() {
  interventionQueue = await checkedGet('/api/interventions');
  renderMetrics(interventionQueue);
  renderRiskList(interventionQueue);
  if (!currentInterventionId && interventionQueue.length) {
    await openReplyWorkbench(interventionQueue[0].id);
  }
}

function renderReplyWorkbench(data) {
  document.getElementById('replyEmptyState').classList.add('d-none');
  document.getElementById('replyPanel').classList.remove('d-none');

  document.getElementById('replyPostTitle').textContent = data.post.title || '无标题帖子';
  document.getElementById('replyPostMeta').textContent =
    `作者：${data.post.author_name || '-'} | 发布时间：${formatBeijingTime(data.post.publish_time)} | 平台：${data.post.source_platform || '-'}`;
  document.getElementById('replyPostExcerpt').textContent = data.post.excerpt || data.post.content || '-';
  document.getElementById('replyPostUrl').href = data.post.post_url || '#';
  document.getElementById('replyRiskInfo').textContent =
    `风险等级：${data.risk.risk_level || '-'} | 风险分：${data.risk.risk_score ?? '-'} | 建议：${data.risk.suggestion || '暂无'}`;
  document.getElementById('replyFinalContent').value = data.reply?.final_content || data.reply?.draft_content || '';
  document.getElementById('replyReviewNote').value = data.reply?.review_note || '';
  document.getElementById('externalReplyUrl').value = data.reply?.external_reply_url || '';
  renderRiskVisual(data);

  const statusText = data.reply
    ? `生成：${statusLabel(data.reply.generate_status)}；审核：${statusLabel(data.reply.review_status)}；处理：${statusLabel(data.reply.send_status)}`
    : '尚未生成干预建议。';
  setReplyStatus(statusText);
}

async function openReplyWorkbench(interventionId) {
  currentInterventionId = interventionId;
  renderRiskList(interventionQueue);
  setReplyStatus('正在加载干预记录...', 'warning');
  try {
    const data = await checkedGet(`/api/interventions/${interventionId}/reply`);
    renderReplyWorkbench(data);
  } catch (error) {
    setReplyStatus(`加载失败：${error.message || '未知错误'}`, 'danger');
  }
}

async function generateReplyDraft() {
  if (!currentInterventionId) return;
  const button = document.getElementById('generateReplyBtn');
  button.disabled = true;
  button.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-1"></i>生成中';
  setReplyStatus('正在调用模型生成支持性干预建议...', 'warning');
  try {
    const result = await checkedPost(`/api/interventions/${currentInterventionId}/reply/generate`);
    document.getElementById('replyFinalContent').value = result.reply.final_content || result.reply.draft_content || '';
    const usedModel = result.reply.generate_status === 'success';
    setReplyStatus(usedModel ? 'AI草稿已生成，请人工审核后使用。' : '已生成本地兜底草稿；配置模型密钥后可调用在线模型。', usedModel ? 'success' : 'warning');
    await openReplyWorkbench(currentInterventionId);
    await loadInterventions();
  } catch (error) {
    setReplyStatus(`生成失败：${error.message || '未知错误'}`, 'danger');
  } finally {
    button.disabled = false;
    button.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles me-1"></i>生成干预建议';
  }
}

async function reviewReply(action) {
  if (!currentInterventionId) return;
  const finalContent = document.getElementById('replyFinalContent').value.trim();
  if (!finalContent && action !== 'reject') {
    setReplyStatus('请先填写或生成干预建议。', 'danger');
    return;
  }

  try {
    const result = await checkedPut(`/api/interventions/${currentInterventionId}/reply/review`, {
      final_content: finalContent,
      review_action: action,
      review_note: document.getElementById('replyReviewNote').value,
      reviewer: document.getElementById('replyReviewer').value || '管理员',
      is_public_comment_allowed: document.getElementById('publicAllowed').checked,
    });
    setReplyStatus(`已保存：${statusLabel(result.reply.review_status)}`, 'success');
    await openReplyWorkbench(currentInterventionId);
    await loadInterventions();
  } catch (error) {
    setReplyStatus(`审核保存失败：${error.message || '未知错误'}`, 'danger');
  }
}

async function markReplySent() {
  if (!currentInterventionId) return;
  try {
    const result = await checkedPost(`/api/interventions/${currentInterventionId}/reply/mark-sent`, {
      operator: document.getElementById('replyReviewer').value || '管理员',
      external_reply_url: document.getElementById('externalReplyUrl').value.trim(),
      send_result_message: '人工审核后已记录处理状态',
    });
    setReplyStatus(`处理状态已记录：${statusLabel(result.reply.send_status)}`, 'success');
    await openReplyWorkbench(currentInterventionId);
    await loadInterventions();
  } catch (error) {
    setReplyStatus(`标记失败：${error.message || '未知错误'}`, 'danger');
  }
}

async function copyReplyContent() {
  const text = document.getElementById('replyFinalContent').value.trim();
  if (!text) {
    setReplyStatus('没有可复制的建议内容。', 'danger');
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    setReplyStatus('建议内容已复制。', 'success');
  } catch {
    document.getElementById('replyFinalContent').select();
    setReplyStatus('浏览器未允许自动复制，已选中文案，可手动复制。', 'warning');
  }
}

loadInterventions().catch((error) => {
  document.getElementById('riskList').innerHTML = `<div class="text-danger py-3">干预队列加载失败：${escapeHtml(error.message || '未知错误')}</div>`;
});

