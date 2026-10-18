const tokenInput = document.getElementById("token");
const scenarioSelect = document.getElementById("scenario");
const seedInput = document.getElementById("seed");
const statusText = document.getElementById("status");
const runButton = document.getElementById("run");

const labels = {
  old_database_recovery: "Old database recovery",
  delayed_message_after_erasure: "Delayed queue message",
  s3_version_resurrection: "S3 older object version",
  index_reingestion: "Search index re-ingestion",
};

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

async function request(path, options = {}) {
  const token = tokenInput.value;
  const response = await fetch(path, {
    ...options,
    headers: {
      "Authorization": `Bearer ${token}`,
      "X-RGX-Tenant": "t01",
      "X-RGX-Subject": "operator",
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.code || `HTTP ${response.status}`);
  return data;
}

function showResult(id, report) {
  const target = document.getElementById(id);
  const exposure = `${report.forbidden_exposures}/${report.forbidden_attempts}`;
  const className = report.forbidden_exposures ? "fail" : "pass";
  target.className = `card ${className}`;
  target.innerHTML = `
    <h3>${id === "baseline" ? "Baseline" : "EraseLock protected"}</h3>
    <p class="metric">${escapeHtml(exposure)}</p>
    <p>Forbidden results / attempts</p>
    <p><strong>Erasure workflow checkpoint:</strong> ${escapeHtml(report.evidence_decision)}</p>
    <p>${escapeHtml(report.notes.join(" "))}</p>
  `;
}

async function populateScenarios() {
  try {
    const data = await request("/v1/lab/scenarios");
    scenarioSelect.innerHTML = data.scenarios.map((name) =>
      `<option value="${escapeHtml(name)}">${escapeHtml(labels[name] || name)}</option>`
    ).join("");
    statusText.textContent = "Ready. The token stays in this browser tab's memory.";
  } catch (error) {
    statusText.textContent = `Enter the local demo token, then select the token field to load scenarios. ${error.message}`;
  }
}

tokenInput.addEventListener("change", populateScenarios);
runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  statusText.textContent = "Running paired synthetic experiments…";
  try {
    const scenario = scenarioSelect.value;
    const seed = Number(seedInput.value);
    const baseline = await request("/v1/lab/runs", {
      method: "POST", body: JSON.stringify({ scenario, mode: "baseline", seed }),
    });
    const protectedRun = await request("/v1/lab/runs", {
      method: "POST", body: JSON.stringify({ scenario, mode: "protected", seed }),
    });
    showResult("baseline", baseline);
    showResult("protected", protectedRun);
    document.getElementById("evidence").textContent = JSON.stringify({baseline, protected: protectedRun}, null, 2);
    statusText.textContent = "Completed. Review the tested scope and evidence below.";
  } catch (error) {
    statusText.textContent = `Run failed: ${error.message}`;
  } finally {
    runButton.disabled = false;
  }
});
