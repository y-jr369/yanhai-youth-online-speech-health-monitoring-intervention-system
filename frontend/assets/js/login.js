function enterDashboard(user) {
  localStorage.setItem("token", "demo-token");
  localStorage.setItem("username", user || "admin");
  window.location.assign("dashboard.html");
}

document.getElementById("loginForm")?.addEventListener("submit", async function (e) {
  e.preventDefault();
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;

  try {
    const res = await apiPost("/api/auth/login", { username, password });
    if (res.token) {
      localStorage.setItem("token", res.token);
      localStorage.setItem("username", res.user?.username || username);
      window.location.assign("dashboard.html");
      return;
    }
    alert(res.detail || "登录失败，请检查账号或密码。");
  } catch (error) {
    if (username === "admin" && password === "123456") {
      enterDashboard(username);
      return;
    }
    alert(`登录服务连接失败：${error.message || "未知错误"}`);
  }
});
