const API_ORIGIN = window.location.port === "8096"
  ? "http://localhost:8000"
  : window.location.origin;
const BASE_URL = API_ORIGIN;

async function apiGet(url) {
  const res = await fetch(API_ORIGIN + url);
  return await res.json();
}

async function apiPost(url, data) {
  const res = await fetch(API_ORIGIN + url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return await res.json();
}

async function apiPut(url, data) {
  const res = await fetch(API_ORIGIN + url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return await res.json();
}
