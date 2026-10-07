let riskChartInstance;

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value ?? "-";
}

function formatDateTime(value) {
  if (!value) return "暂无";
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

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "暂无";
  const value = Number(seconds);
  if (value < 60) return `${value.toFixed(value >= 10 ? 0 : 1)} 秒`;
  return `${Math.floor(value / 60)} 分 ${Math.round(value % 60)} 秒`;
}

function statusMeta(status) {
  const map = {
    success: ["成功", "text-bg-success"],
    failed: ["失败", "text-bg-danger"],
    running: ["进行中", "text-bg-warning"],
  };
  return map[status] || ["未执行", "text-bg-secondary"];
}

function setCrawlHealthNote(message, tone = "muted") {
  const target = document.getElementById("crawlHealthNote");
  if (!target) return;
  const colors = { muted: "#667085", success: "#13785a", warning: "#a86413", danger: "#b94141" };
  target.style.color = colors[tone] || colors.muted;
  target.textContent = message;
}

async function loadOverview() {
  const data = await apiGet("/api/dashboard/overview");
  setText("totalPosts", data.total_posts ?? 0);
  setText("totalAlerts", data.total_alerts ?? 0);
  setText("highRisk", data.high_risk ?? 0);
  setText("pending", data.pending ?? 0);
  setText("activeSourcesValue", data.active_sources ?? 0);
  setText("jobsLast24hValue", data.jobs_last_24h ?? 0);
}

async function loadRiskChart() {
  const rawData = await apiGet("/api/dashboard/risk-distribution");
  const chartDom = document.getElementById("riskChart");
  if (!chartDom || !window.echarts) return;

  const riskMeta = {
    low: { name: "低风险", color: "#24b36b" },
    medium: { name: "中风险", color: "#f59e0b" },
    high: { name: "高风险", color: "#dc2626" },
  };
  const byKey = Object.fromEntries((rawData || []).map((item) => [item.key, item]));
  const data = ["low", "medium", "high"].map((key) => {
    const item = byKey[key] || {};
    return {
      key,
      name: item.name || riskMeta[key].name,
      value: item.value ?? 0,
      itemStyle: { color: riskMeta[key].color },
    };
  });

  riskChartInstance = echarts.getInstanceByDom(chartDom) || echarts.init(chartDom);
  riskChartInstance.setOption({
    backgroundColor: "transparent",
    title: { text: "风险等级分布", left: "center", top: 8, textStyle: { color: "#122033", fontSize: 24, fontWeight: 800 } },
    tooltip: { trigger: "item" },
    legend: { bottom: 8, left: "center", icon: "circle", textStyle: { color: "#334155", fontSize: 13 } },
    grid: { left: "58%", right: "8%", top: 96, bottom: 76, containLabel: true },
    xAxis: { type: "value", axisLabel: { color: "#667085" }, splitLine: { lineStyle: { color: "rgba(148, 163, 184, 0.2)" } } },
    yAxis: { type: "category", data: data.map((item) => item.name), axisLabel: { color: "#334155", fontWeight: 700 }, axisTick: { show: false }, axisLine: { show: false } },
    series: [
      {
        name: "风险等级",
        type: "pie",
        radius: ["42%", "66%"],
        center: ["28%", "56%"],
        data,
        label: { color: "#334155", fontSize: 14, fontWeight: 700, formatter: "{b}\n{c} 条" },
      },
      {
        name: "帖子数量",
        type: "bar",
        barWidth: 18,
        data: data.map((item) => ({ value: item.value, itemStyle: item.itemStyle })),
        label: { show: true, position: "right", color: "#122033", fontWeight: 800 },
      },
    ],
  });
}

function renderLatestCrawl(health) {
  const latestJob = Array.isArray(health?.jobs) ? health.jobs[0] : null;
  if (!latestJob) {
    setText("crawlJobTitle", "暂无抓取任务记录");
    setText("crawlJobMeta", "你可以点击右侧按钮手动更新抓取，系统会自动去重并保留已有帖子。");
    setText("crawlInsertedValue", "0");
    setText("crawlDuplicatedValue", "0");
    setText("crawlFailedValue", "0");
    setText("crawlDurationValue", "暂无");
    setText("crawlJobSummary", "最近一次抓取摘要将在这里显示。");
    return;
  }

  const sources = Array.isArray(health?.sources) ? health.sources : [];
  const source = sources.find((item) => item.id === latestJob.source_id);
  const sourceName = source?.source_label || source?.name || source?.forum_name || "抓取任务";
  const [label, className] = statusMeta(latestJob.status);
  const statusEl = document.getElementById("crawlJobStatus");

  setText("crawlJobTitle", `${sourceName} · 最近一次抓取`);
  setText("crawlJobMeta", `开始：${formatDateTime(latestJob.started_at)}  结束：${formatDateTime(latestJob.finished_at)}`);
  setText("crawlInsertedValue", latestJob.inserted_count ?? latestJob.inserted ?? 0);
  setText("crawlDuplicatedValue", latestJob.duplicated_count ?? latestJob.duplicated ?? 0);
  setText("crawlFailedValue", latestJob.failed_count ?? latestJob.failed ?? 0);
  setText("crawlDurationValue", formatDuration(latestJob.duration_seconds));
  setText("crawlJobSummary", latestJob.summary || "暂无任务摘要。");
  if (statusEl) {
    statusEl.className = `badge rounded-pill ${className}`;
    statusEl.textContent = label;
  }
}

async function loadCrawlHealth(updateNote = true) {
  const health = await apiGet("/api/dashboard/crawl-health");
  renderLatestCrawl(health);
  if (updateNote) setCrawlHealthNote("展示最近一次抓取任务结果，手动抓取后会自动刷新。");
}

async function manualRefreshFromDashboard() {
  const button = document.getElementById("dashboardManualRefreshBtn");
  if (button) button.disabled = true;
  setCrawlHealthNote("正在提交抓取任务，请稍候...", "warning");
  try {
    const result = await apiPost("/api/crawler/manual-refresh", {});
    setCrawlHealthNote(result.message || "抓取任务已提交。", result.ok ? "success" : "danger");
    await Promise.all([loadOverview(), loadRiskChart(), loadCrawlHealth(false)]);
  } catch (error) {
    setCrawlHealthNote(`抓取失败：${error.message || "接口调用失败"}`, "danger");
  } finally {
    if (button) button.disabled = false;
  }
}

window.addEventListener("resize", () => riskChartInstance?.resize());
document.getElementById("dashboardManualRefreshBtn")?.addEventListener("click", manualRefreshFromDashboard);

Promise.all([loadOverview(), loadRiskChart(), loadCrawlHealth()]).catch((error) => {
  setCrawlHealthNote(`页面数据加载失败：${error.message || "未知错误"}`, "danger");
});

