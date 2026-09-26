const statusElement = document.querySelector("#api-status");
const detailElement = document.querySelector("#connection-detail");

async function checkApiConnection() {
  try {
    const response = await fetch("/api/health", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`Health check failed: ${response.status}`);
    const health = await response.json();
    if (health.status !== "ok") throw new Error("API reported an unhealthy state");
    statusElement.textContent = "API connected";
    statusElement.classList.add("connected");
    detailElement.textContent = `Connected to ${health.service} (${health.api_version}).`;
  } catch (error) {
    statusElement.textContent = "API unavailable";
    statusElement.classList.add("unavailable");
    detailElement.textContent = "Start the local API service and refresh this page.";
    console.error(error);
  }
}

checkApiConnection();
