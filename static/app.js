const form = document.getElementById("form");
const urlInput = document.getElementById("url");
const errorBox = document.getElementById("error");
const preview = document.getElementById("preview");
const thumb = document.getElementById("thumb");
const titleEl = document.getElementById("title");
const subEl = document.getElementById("sub");
const qualitiesEl = document.getElementById("qualities");
const dlAudioBtn = document.getElementById("dl-audio");
const historyList = document.getElementById("history-list");
const goBtn = document.getElementById("go");

let currentUrl = "";
let history = [];

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.remove("hidden");
  preview.classList.add("hidden");
}

function clearError() {
  errorBox.classList.add("hidden");
}

function formatSize(bytes) {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? ` · ${mb.toFixed(0)} MB` : ` · ${(bytes / 1024).toFixed(0)} KB`;
}

function renderQualities(qualities) {
  qualitiesEl.innerHTML = "";

  if (!qualities || qualities.length === 0) {
    const btn = document.createElement("button");
    btn.textContent = "Best available";
    btn.addEventListener("click", () => triggerDownload("video", ""));
    qualitiesEl.appendChild(btn);
    return;
  }

  qualities.forEach((q) => {
    const btn = document.createElement("button");
    btn.textContent = `${q.height}p${formatSize(q.filesize)}`;
    btn.addEventListener("click", () => triggerDownload("video", q.format_id));
    qualitiesEl.appendChild(btn);
  });
}

function renderHistory() {
  historyList.innerHTML = "";
  history.slice(0, 10).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item.title || item.url;
    li.addEventListener("click", () => {
      urlInput.value = item.url;
      form.requestSubmit();
    });
    historyList.appendChild(li);
  });
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();
  const url = urlInput.value.trim();
  if (!url) return;

  goBtn.disabled = true;
  goBtn.textContent = "Fetching...";

  try {
    const res = await fetch("/api/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();

    if (!res.ok) {
      showError(data.error || "Something went wrong.");
      return;
    }

    currentUrl = url;
    thumb.src = data.thumbnail || "";
    titleEl.textContent = data.title || "Untitled";
    subEl.textContent = [data.platform, data.uploader].filter(Boolean).join(" · ");
    renderQualities(data.qualities);
    preview.classList.remove("hidden");

    history = [{ url, title: data.title }, ...history.filter((h) => h.url !== url)];
    renderHistory();
  } catch (err) {
    showError("Could not reach the server.");
  } finally {
    goBtn.disabled = false;
    goBtn.textContent = "Fetch";
  }
});

function triggerDownload(mode, formatId) {
  if (!currentUrl) return;
  let href = `/api/download?url=${encodeURIComponent(currentUrl)}&mode=${mode}`;
  if (formatId) href += `&format_id=${encodeURIComponent(formatId)}`;
  const link = document.createElement("a");
  link.href = href;
  link.click();
}

dlAudioBtn.addEventListener("click", () => triggerDownload("audio", ""));
