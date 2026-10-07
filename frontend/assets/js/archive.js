function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setArchiveStatus(message, tone = "muted") {
  const target = document.getElementById("archiveStatus");
  if (!target) return;
  const colors = { muted: "#667085", success: "#13785a", warning: "#a86413", danger: "#b94141" };
  target.style.color = colors[tone] || colors.muted;
  target.textContent = message;
}

function archiveRiskBadge(level) {
  const map = {
    high: ["高风险", "badge-high"],
    medium: ["中风险", "badge-mid"],
    low: ["低风险", "badge-low"],
  };
  const [label, cls] = map[String(level || "low").toLowerCase()] || ["未知", "text-bg-secondary"];
  return `<span class="badge ${cls}">${label}</span>`;
}

function buildArchiveRow(item) {
  const followUpParts = [
    item.campus_process_no ? `工单号：${item.campus_process_no}` : "",
    item.handler ? `处理人：${item.handler}` : "",
    item.process_status ? `状态：${item.process_status}` : "",
    item.follow_up_note ? `备注：${item.follow_up_note}` : "",
  ].filter(Boolean);
  return `
    <tr>
      <td>${escapeHtml(item.id)}</td>
      <td>${escapeHtml(item.alert_id)}</td>
      <td>${archiveRiskBadge(item.risk_level || "low")}</td>
      <td>
        <div class="fw-semibold text-dark">${escapeHtml(item.title || "-")}</div>
        <div class="small text-muted mt-1">${escapeHtml(item.excerpt || "暂无摘要")}</div>
      </td>
      <td>${escapeHtml(item.archive_reason || "-")}</td>
      <td>${escapeHtml(item.archived_by || "-")}</td>
      <td>${escapeHtml(item.archived_at_display || item.archived_at || "-")}</td>
      <td>${escapeHtml(followUpParts.join("；") || "-")}</td>
    </tr>
  `;
}

async function loadArchives() {
  const tbody = document.getElementById("archiveTableBody");
  if (!tbody) return;
  const data = await apiGet("/api/archive");
  if (!Array.isArray(data) || !data.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4">暂无归档记录</td></tr>';
    setArchiveStatus("当前没有可导出的归档记录。");
    return;
  }
  tbody.innerHTML = data.map(buildArchiveRow).join("");
  setArchiveStatus(`已加载 ${data.length} 条归档记录，可一键导出为 Excel。`, "success");
}

function exportArchives() {
  setArchiveStatus("正在生成 Excel，浏览器下载列表中会出现该文件。", "warning");
  const link = document.createElement("a");
  link.href = `${BASE_URL}/api/archive/export?t=${Date.now()}`;
  link.download = "";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setArchiveStatus("已触发 Excel 下载，请在浏览器下载列表中查看。", "success");
}

document.getElementById("exportArchiveBtn")?.addEventListener("click", exportArchives);
loadArchives().catch((error) => {
  const tbody = document.getElementById("archiveTableBody");
  if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="text-center text-danger py-4">${escapeHtml(error.message)}</td></tr>`;
  setArchiveStatus(`归档记录加载失败：${error.message || "未知错误"}`, "danger");
});
