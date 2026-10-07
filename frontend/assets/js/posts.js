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
let currentSkip = 0;
const pageSize = 20;

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function riskBadge(level) {
  const map = {
    high: ["高风险", "badge-high"],
    medium: ["中风险", "badge-mid"],
    low: ["低风险", "badge-low"],
  };
  const [label, cls] = map[String(level || "low").toLowerCase()] || ["未知", "text-bg-secondary"];
  return `<span class="badge ${cls}">${label}</span>`;
}

function buildPostsUrl() {
  const params = new URLSearchParams({ skip: String(currentSkip), limit: String(pageSize) });
  const keyword = document.getElementById("keywordInput")?.value.trim();
  const riskLevel = document.getElementById("riskLevelSelect")?.value;
  if (keyword) params.set("keyword", keyword);
  if (riskLevel) params.set("risk_level", riskLevel);
  return `/api/posts?${params.toString()}`;
}

function renderRows(items) {
  const tbody = document.getElementById("postTableBody");
  if (!tbody) return;
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-4">暂无帖子数据</td></tr>';
    return;
  }
  tbody.innerHTML = items.map((item) => {
    const labels = Array.isArray(item.labels_display) ? item.labels_display.join(" / ") : (item.labels_display || item.labels || "");
    const keywords = Array.isArray(item.matched_keywords) ? item.matched_keywords.join(" / ") : (item.matched_keywords || "");
    const publishTime = formatBeijingTime(item.publish_time);
    const postUrl = item.post_url ? `<a href="${escapeHtml(item.post_url)}" target="_blank" rel="noopener noreferrer">查看原帖</a>` : "-";
    return `
      <tr>
        <td>${escapeHtml(item.post_id || item.id)}</td>
        <td>${escapeHtml(item.title || "-")}</td>
        <td>${escapeHtml(item.author_name || "-")}</td>
        <td><div class="post-excerpt">${escapeHtml(item.excerpt || item.content || "")}</div></td>
        <td>${escapeHtml(labels || "正常")}</td>
        <td>${riskBadge(item.risk_level || "low")}</td>
        <td>${escapeHtml(item.risk_score ?? 0)}</td>
        <td>${escapeHtml(keywords || "-")}</td>
        <td><div>${escapeHtml(publishTime)}</div><div>${postUrl}</div></td>
      </tr>
    `;
  }).join("");
}

async function loadOverviewCards() {
  const overview = await apiGet("/api/dashboard/overview");
  const distribution = await apiGet("/api/dashboard/risk-distribution");
  const byKey = Object.fromEntries((distribution || []).map((item) => [item.key, item.value]));
  document.getElementById("totalPostsValue").textContent = overview.total_posts ?? 0;
  document.getElementById("highRiskValue").textContent = byKey.high ?? 0;
  document.getElementById("mediumRiskValue").textContent = byKey.medium ?? 0;
  document.getElementById("lowRiskValue").textContent = byKey.low ?? 0;
}

async function loadPosts() {
  const data = await apiGet(buildPostsUrl());
  const items = data.items || [];
  renderRows(items);
  const total = data.total || 0;
  const currentPage = Math.floor(currentSkip / pageSize) + 1;
  const totalPages = Math.max(Math.ceil(total / pageSize), 1);
  document.getElementById("pageInfo").textContent = `第 ${currentPage} / ${totalPages} 页，共 ${total} 条`;
  document.getElementById("prevBtn").disabled = currentSkip === 0;
  document.getElementById("nextBtn").disabled = currentSkip + pageSize >= total;
}

function searchPosts() {
  currentSkip = 0;
  loadPosts().catch(() => renderRows([]));
}

function resetFilters() {
  document.getElementById("keywordInput").value = "";
  document.getElementById("riskLevelSelect").value = "";
  currentSkip = 0;
  searchPosts();
}

function prevPage() {
  if (currentSkip === 0) return;
  currentSkip = Math.max(0, currentSkip - pageSize);
  loadPosts().catch(() => renderRows([]));
}

function nextPage() {
  currentSkip += pageSize;
  loadPosts().catch(() => renderRows([]));
}

Promise.all([loadOverviewCards(), loadPosts()]).catch((error) => {
  renderRows([]);
  const pageInfo = document.getElementById("pageInfo");
  if (pageInfo) pageInfo.textContent = `页面数据加载失败：${error.message || "未知错误"}`;
});
