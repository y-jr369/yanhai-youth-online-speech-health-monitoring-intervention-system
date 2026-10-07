function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setExportStatus(message, tone = "muted") {
  const target = document.getElementById("exportAlertStatus");
  if (!target) return;
  const colors = { muted: "#667085", success: "#13785a", warning: "#a86413", danger: "#b94141" };
  target.style.color = colors[tone] || colors.muted;
  target.textContent = message;
}

function alertRiskBadge(level) {
  const map = {
    high: ["高风险", "badge-high"],
    medium: ["中风险", "badge-mid"],
    low: ["低风险", "badge-low"],
  };
  const [label, cls] = map[String(level || "low").toLowerCase()] || ["未知", "text-bg-secondary"];
  return `<span class="badge ${cls}">${label}</span>`;
}

async function loadAlerts() {
  const data = await apiGet("/api/alerts");
  const tbody = document.getElementById("alertTableBody");
  if (!tbody) return;
  if (!Array.isArray(data) || !data.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="text-center text-muted py-4">暂无预警数据</td></tr>';
    return;
  }
  tbody.innerHTML = data.map((item) => {
    const excerpt = item.excerpt || item.content || "";
    const postUrl = item.post_url ? `<a href="${escapeHtml(item.post_url)}" target="_blank" rel="noopener noreferrer">查看原帖</a>` : "-";
    return `
      <tr>
        <td>${escapeHtml(item.alert_id)}</td>
        <td>${escapeHtml(item.post_id)}</td>
        <td>${escapeHtml(item.source || item.source_platform || "-")}</td>
        <td>${escapeHtml(excerpt)}</td>
        <td>${escapeHtml(item.labels || "-")}</td>
        <td>${alertRiskBadge(item.risk_level_key || item.risk_level)}</td>
        <td>${escapeHtml(item.risk_score ?? 0)}</td>
        <td>${postUrl}</td>
        <td>${escapeHtml(item.alert_status || "-")}</td>
        <td>
          <button class="btn btn-sm btn-success" onclick="handleReview(${Number(item.alert_id)})">已处理</button>
          <button class="btn btn-sm btn-warning" onclick="handleArchive(${Number(item.alert_id)})">归档</button>
        </td>
      </tr>
    `;
  }).join("");
}

async function handleReview(alertId) {
  const note = prompt("请输入处理备注：", "人工复核完成");
  if (note === null) return;
  await apiPost(`/api/alerts/${alertId}/review`, { alert_status: "reviewed", review_note: note });
  await loadAlerts();
}

async function handleArchive(alertId) {
  await apiPost(`/api/alerts/${alertId}/archive`, {});
  await loadAlerts();
}

function exportAlertsExcel() {
  setExportStatus("正在准备 Excel 风险名单下载...", "warning");
  const link = document.createElement("a");
  link.href = `${BASE_URL}/api/alerts/export?ts=${Date.now()}`;
  link.download = "risk_alert_list.xlsx";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setExportStatus("已触发浏览器下载，请留意下载栏。", "success");
}

setExportStatus("点击右上角按钮即可导出当前风险名单 Excel。");
loadAlerts().catch((error) => {
  const tbody = document.getElementById("alertTableBody");
  if (tbody) tbody.innerHTML = `<tr><td colspan="10" class="text-center text-danger py-4">${escapeHtml(error.message)}</td></tr>`;
  setExportStatus(`预警数据加载失败：${error.message || "未知错误"}`, "danger");
});
