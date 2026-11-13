const POLL_INTERVAL = 2000;

const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const alertContainer = document.getElementById("alertContainer");
const inputFormat = document.getElementById("inputFormat");
const protobufOptions = document.getElementById("protobufOptions");
const schemaInput = document.getElementById("schemaInput");
const heatmapTooltip = document.getElementById("heatmapTooltip");

let pollIntervalId = null;

// File state per type
const state = {
  csv: { files: [], selectedDate: null, selectedYear: "auto" },
  rerun: { files: [], selectedDate: null, selectedYear: "auto" },
  raw: { files: [], selectedDate: null, selectedYear: "auto" },
};

const LEVEL_COLORS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"];
const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
];
const DAY_LABELS = [
  { row: 1, label: "Mon", y: 29 },
  { row: 3, label: "Wed", y: 55 },
  { row: 5, label: "Fri", y: 81 },
];

inputFormat.addEventListener("change", () => {
  protobufOptions.hidden = inputFormat.value === "formula_binary";
});

dropZone.addEventListener("click", () => fileInput.click());

dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.remove("invalid");
  dropZone.classList.add("dragover");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("dragover", "invalid");
});

dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover", "invalid");

  if (!e.dataTransfer.files || e.dataTransfer.files.length === 0) {
    showAlert("No files dropped.", "warning");
    return;
  }

  handleFiles(e.dataTransfer.files);
});

fileInput.addEventListener("change", (e) => {
  if (!e.target.files || e.target.files.length === 0) {
    return;
  }
  handleFiles(e.target.files);
});

function handleFiles(files) {
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }

  formData.append("input_format", inputFormat.value);

  if (inputFormat.value !== "formula_binary") {
    const schemaFiles = [...schemaInput.files].filter((file) =>
      file.name.toLowerCase().endsWith(".proto"),
    );
    for (const schema of schemaFiles) {
      formData.append("schema_files", schema, schema.webkitRelativePath || schema.name);
    }
    const messageType = document.getElementById("messageType").value.trim();
    if (messageType) {
      formData.append("message_type", messageType);
    }
  }

  const debugMode = document.getElementById("debugToggle").checked;
  formData.append("debug", debugMode);

  // Clear previous alerts
  alertContainer.innerHTML = "";

  // Stop any previous polling loop
  if (pollIntervalId !== null) {
    clearInterval(pollIntervalId);
    pollIntervalId = null;
  }

  showAlert("Uploading file(s) and starting conversion…", "info");

  fetch("/upload", {
    method: "POST",
    body: formData,
  })
    .then((response) => {
      if (!response.ok) {
        throw new Error(`??? Yo ${response.statusText.toLowerCase()}`);
      }
      return response.json();
    })
    .then((data) => {
      const taskName = data.name;
      if (!taskName) {
        throw new Error("Server did not return a task name.");
      }

      startPolling(taskName);
    })
    .catch((error) => {
      console.error(error);
      showAlert(error.message || "Upload failed or server error.", "danger");
    });
}

function startPolling(taskName) {
  if (pollIntervalId !== null) {
    clearInterval(pollIntervalId);
  }

  updateProgressDisplay(0);

  pollIntervalId = setInterval(async () => {
    try {
      const response = await fetch(
        `/progress?name=${encodeURIComponent(taskName)}`,
      );

      if (!response.ok) {
        if (response.status === 404) {
          throw new Error("Task not found on server.");
        }
        throw new Error("Failed to get progress from server.");
      }

      const data = await response.json();

      if (data.exception.present) {
        clearInterval(pollIntervalId);
        pollIntervalId = null;
        updateProgressDisplay(0);
        showAlert(`Error: ${data.exception.type}`, "danger");
        return;
      }

      const progress = data.progress;

      updateProgressDisplay(progress);

      if (response.status == 200) {
        clearInterval(pollIntervalId);
        pollIntervalId = null;
        showAlert("Upload and processing complete!", "success");
        loadAllFiles();
      }
    } catch (err) {
      console.error(err);
      clearInterval(pollIntervalId);
      pollIntervalId = null;
      updateProgressDisplay(0);
      showAlert("Error while checking progress.", "danger");
    }
  }, POLL_INTERVAL);
}

function updateProgressDisplay(percent) {
  showAlert(`Processing: ${percent}`, "info");
}

function showAlert(message, type) {
  alertContainer.innerHTML = `
    <div class="d-flex justify-content-center mt-3">
      <div class="alert alert-${type} shadow-sm px-4" 
           style="max-width: 500px; width: auto; border-radius: 10px;" 
           role="alert">
        ${message}
      </div>
    </div>
  `;
}

function formatSize(bytes) {
  if (bytes === 0) {
    return "0 B";
  }

  const unitSize = 1024;
  const units = ["B", "KB", "MB", "GB"];
  const unitIndex = Math.floor(Math.log(bytes) / Math.log(unitSize));

  return (
    parseFloat((bytes / Math.pow(unitSize, unitIndex)).toFixed(2)) +
    " " +
    units[unitIndex]
  );
}

// Extract YYYY-MM-DD from file timestamp or fallback filename
function extractFileDate(file) {
  if (file && file.timestamp) {
    const m = file.timestamp.match(/^(\d{4}-\d{2}-\d{2})/);
    if (m) return m[1];
  }
  if (file && file.name) {
    const m = file.name.match(/(\d{4})[-_](\d{2})[-_](\d{2})/);
    if (m) return `${m[1]}-${m[2]}-${m[3]}`;
  }
  return null;
}

function formatDateISO(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function getLevel(count, maxCount) {
  if (count <= 0) return 0;
  if (maxCount <= 1) return 2;
  if (maxCount <= 4) return Math.min(4, count);
  const ratio = count / maxCount;
  if (ratio <= 0.25) return 1;
  if (ratio <= 0.5) return 2;
  if (ratio <= 0.75) return 3;
  return 4;
}

function formatTooltipDate(dateStr) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dateObj = new Date(y, m - 1, d);
  return dateObj.toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function renderHeatmap(type) {
  const container = document.getElementById(`${type}Heatmap`);
  const yearSelect = document.getElementById(`${type}YearSelect`);
  const summarySpan = document.getElementById(`${type}HeatmapSummary`);
  if (!container) return;

  const files = state[type].files || [];

  // Group files by date
  const counts = {};
  const fileDates = [];
  for (const file of files) {
    const dateStr = extractFileDate(file);
    if (dateStr) {
      counts[dateStr] = (counts[dateStr] || 0) + 1;
      fileDates.push(dateStr);
    }
  }

  // Determine available years
  const currentYear = new Date().getFullYear();
  const yearsSet = new Set([currentYear]);
  fileDates.forEach((d) => yearsSet.add(parseInt(d.slice(0, 4), 10)));
  const years = Array.from(yearsSet).sort((a, b) => b - a);

  // If selectedYear is 'auto', choose smart default
  if (!state[type].selectedYear || state[type].selectedYear === "auto") {
    if (fileDates.length > 0) {
      const sortedDates = [...fileDates].sort();
      const latestDateStr = sortedDates[sortedDates.length - 1];
      const latestYear = parseInt(latestDateStr.slice(0, 4), 10);
      const [ly, lm, ld] = latestDateStr.split("-").map(Number);
      const latestDateObj = new Date(ly, lm - 1, ld);
      const diffDays = (new Date() - latestDateObj) / (1000 * 60 * 60 * 24);

      if (diffDays > 365) {
        state[type].selectedYear = String(latestYear);
      } else {
        state[type].selectedYear = "last12";
      }
    } else {
      state[type].selectedYear = "last12";
    }
  }

  // Populate year selector
  if (yearSelect) {
    const previousValue = state[type].selectedYear;
    yearSelect.innerHTML = `<option value="last12">Last 12 Months</option>`;
    for (const yr of years) {
      yearSelect.innerHTML += `<option value="${yr}">${yr}</option>`;
    }
    yearSelect.value = previousValue;

    if (!yearSelect.dataset.listenerAttached) {
      yearSelect.addEventListener("change", (e) => {
        state[type].selectedYear = e.target.value;
        state[type].selectedDate = null;
        renderHeatmap(type);
        renderFileList(type);
      });
      yearSelect.dataset.listenerAttached = "true";
    }
  }

  // Determine range
  let startSun, endSat, totalWeeks, isYearMode = false, targetYear = null;

  if (state[type].selectedYear === "last12") {
    let targetEnd = new Date();
    // If files are in future or recent
    if (fileDates.length > 0) {
      const latestDateStr = [...fileDates].sort().pop();
      const [ly, lm, ld] = latestDateStr.split("-").map(Number);
      const latestDateObj = new Date(ly, lm - 1, ld);
      if (latestDateObj > targetEnd) {
        targetEnd = latestDateObj;
      }
    }
    endSat = new Date(targetEnd.getFullYear(), targetEnd.getMonth(), targetEnd.getDate() + (6 - targetEnd.getDay()));
    startSun = new Date(endSat.getFullYear(), endSat.getMonth(), endSat.getDate() - (52 * 7));
    totalWeeks = 53;
  } else {
    isYearMode = true;
    targetYear = parseInt(state[type].selectedYear, 10);
    const jan1 = new Date(targetYear, 0, 1);
    startSun = new Date(targetYear, 0, 1 - jan1.getDay());
    const dec31 = new Date(targetYear, 11, 31);
    endSat = new Date(targetYear, 11, 31 + (6 - dec31.getDay()));
    totalWeeks = Math.round((endSat - startSun) / (7 * 86400000)) + 1;
  }

  // Calculate maxCount in view
  let maxCount = 0;
  let totalFilesInRange = 0;

  for (let w = 0; w < totalWeeks; w++) {
    for (let d = 0; d < 7; d++) {
      const cur = new Date(startSun.getFullYear(), startSun.getMonth(), startSun.getDate() + (w * 7 + d));
      if (isYearMode && cur.getFullYear() !== targetYear) continue;
      const dStr = formatDateISO(cur);
      const c = counts[dStr] || 0;
      if (c > maxCount) maxCount = c;
      totalFilesInRange += c;
    }
  }

  // SVG dimensions
  const cellSize = 10;
  const cellGap = 3;
  const leftMargin = 28;
  const topMargin = 16;
  const svgWidth = leftMargin + totalWeeks * 13 + 6;
  const svgHeight = 110;

  // Build SVG XML
  let svg = `<svg width="${svgWidth}" height="${svgHeight}" viewBox="0 0 ${svgWidth} ${svgHeight}" class="activity-heatmap-svg" role="img" aria-label="Activity Heatmap">`;

  // Day of week labels (Mon, Wed, Fri)
  for (const dayLabel of DAY_LABELS) {
    svg += `<text x="2" y="${dayLabel.y}" font-size="9" fill="#656d76" text-anchor="start" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif">${dayLabel.label}</text>`;
  }

  // Month labels along top
  let lastMonthPlacedCol = -5;
  for (let w = 0; w < totalWeeks; w++) {
    // Check if month changes in this week
    const sunDate = new Date(startSun.getFullYear(), startSun.getMonth(), startSun.getDate() + w * 7);
    const satDate = new Date(startSun.getFullYear(), startSun.getMonth(), startSun.getDate() + w * 7 + 6);
    
    // Find if 1st of month occurs in this week
    let monthLabel = null;
    for (let d = 0; d < 7; d++) {
      const dt = new Date(startSun.getFullYear(), startSun.getMonth(), startSun.getDate() + w * 7 + d);
      if (isYearMode && dt.getFullYear() !== targetYear) continue;
      if (dt.getDate() === 1 || (w === 0 && d === 0)) {
        monthLabel = MONTH_NAMES[dt.getMonth()];
        break;
      }
    }

    if (monthLabel && (w - lastMonthPlacedCol >= 2)) {
      const x = leftMargin + w * 13;
      svg += `<text x="${x}" y="11" font-size="9" fill="#656d76" text-anchor="start" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif">${monthLabel}</text>`;
      lastMonthPlacedCol = w;
    }
  }

  // Heatmap day cells
  for (let w = 0; w < totalWeeks; w++) {
    for (let d = 0; d < 7; d++) {
      const cur = new Date(startSun.getFullYear(), startSun.getMonth(), startSun.getDate() + (w * 7 + d));
      if (isYearMode && cur.getFullYear() !== targetYear) {
        continue;
      }

      const dStr = formatDateISO(cur);
      const c = counts[dStr] || 0;
      const level = getLevel(c, maxCount);
      const fillColor = LEVEL_COLORS[level];
      const x = leftMargin + w * 13;
      const y = topMargin + d * 13;
      const isSelected = state[type].selectedDate === dStr;
      const selectedClass = isSelected ? " is-selected" : "";
      const strokeColor = isSelected ? "#0969da" : level === 0 ? "rgba(27, 31, 35, 0.08)" : "none";
      const strokeWidth = isSelected ? "2" : "1";

      svg += `<rect class="heatmap-day${selectedClass}" data-type="${type}" data-date="${dStr}" data-count="${c}" data-level="${level}" x="${x}" y="${y}" width="${cellSize}" height="${cellSize}" rx="2" ry="2" fill="${fillColor}" stroke="${strokeColor}" stroke-width="${strokeWidth}"></rect>`;
    }
  }

  svg += `</svg>`;
  container.innerHTML = svg;

  // Scroll to the right end so most recent activity is in view
  container.scrollLeft = container.scrollWidth;

  // Enable mouse wheel horizontal scrolling
  if (!container.dataset.wheelAttached) {
    container.addEventListener(
      "wheel",
      (e) => {
        if (e.deltaY !== 0 && container.scrollWidth > container.clientWidth) {
          e.preventDefault();
          container.scrollLeft += e.deltaY;
        }
      },
      { passive: false },
    );
    container.dataset.wheelAttached = "true";
  }

  // Attach hover and click listeners to day cells
  const dayRects = container.querySelectorAll(".heatmap-day");
  dayRects.forEach((rect) => {
    rect.addEventListener("mouseenter", (e) => {
      const dateStr = rect.getAttribute("data-date");
      const count = parseInt(rect.getAttribute("data-count"), 10);
      const formattedDate = formatTooltipDate(dateStr);
      const message =
        count === 0
          ? `No files on ${formattedDate}`
          : `${count} file${count > 1 ? "s" : ""} on ${formattedDate}`;

      if (heatmapTooltip) {
        const r = rect.getBoundingClientRect();
        heatmapTooltip.textContent = message;
        heatmapTooltip.style.left = `${r.left + r.width / 2}px`;
        heatmapTooltip.style.top = `${r.top}px`;
        heatmapTooltip.classList.add("show");
      }
    });

    rect.addEventListener("mouseleave", () => {
      if (heatmapTooltip) heatmapTooltip.classList.remove("show");
    });

    rect.addEventListener("click", () => {
      const dateStr = rect.getAttribute("data-date");
      if (state[type].selectedDate === dateStr) {
        state[type].selectedDate = null;
      } else {
        state[type].selectedDate = dateStr;
      }
      renderHeatmap(type);
      renderFileList(type);
    });
  });

  container.addEventListener("scroll", () => {
    if (heatmapTooltip) heatmapTooltip.classList.remove("show");
  });

  // Update summary text
  if (summarySpan) {
    const periodLabel = state[type].selectedYear === "last12" ? "the past year" : state[type].selectedYear;
    summarySpan.textContent = `${totalFilesInRange} file${totalFilesInRange === 1 ? "" : "s"} in ${periodLabel}`;
  }
}

function renderFileList(type) {
  const list = document.getElementById(`${type}fileList`);
  const filterBanner = document.getElementById(`${type}FilterBanner`);
  const filteredDateSpan = document.getElementById(`${type}FilteredDate`);
  const filteredCountSpan = document.getElementById(`${type}FilteredCount`);
  const fileCountBadge = document.getElementById(`${type}FileCount`);
  const clearBtn = document.getElementById(`${type}ClearFilterBtn`);

  if (!list) return;

  const allFiles = state[type].files || [];
  const selectedDate = state[type].selectedDate;

  // Set header file count badge
  if (fileCountBadge) {
    fileCountBadge.textContent = `${allFiles.length} file${allFiles.length === 1 ? "" : "s"}`;
  }

  // Configure clear button
  if (clearBtn && !clearBtn.dataset.listenerAttached) {
    clearBtn.addEventListener("click", () => {
      state[type].selectedDate = null;
      renderHeatmap(type);
      renderFileList(type);
    });
    clearBtn.dataset.listenerAttached = "true";
  }

  // Filter files if selectedDate is set
  let displayFiles = allFiles;
  if (selectedDate) {
    displayFiles = allFiles.filter((f) => extractFileDate(f) === selectedDate);
    if (filterBanner) {
      filterBanner.style.setProperty("display", "flex", "important");
      if (filteredDateSpan) filteredDateSpan.textContent = selectedDate;
      if (filteredCountSpan) filteredCountSpan.textContent = displayFiles.length;
    }
  } else {
    if (filterBanner) {
      filterBanner.style.setProperty("display", "none", "important");
    }
  }

  list.innerHTML = "";

  if (allFiles.length === 0) {
    list.innerHTML = `<li class="list-group-item text-muted">No files found</li>`;
    return;
  }

  if (displayFiles.length === 0) {
    list.innerHTML = `<li class="list-group-item text-muted">No files found for ${selectedDate}</li>`;
    return;
  }

  for (const file of displayFiles) {
    const li = document.createElement("li");
    li.className = "list-group-item d-flex justify-content-between align-items-center";

    const leftDiv = document.createElement("div");
    leftDiv.className = "d-flex flex-column me-2 overflow-hidden";

    const nameSpan = document.createElement("span");
    nameSpan.textContent = file.name;
    nameSpan.className = "me-2 text-truncate";
    nameSpan.style.maxWidth = "240px";
    nameSpan.title = file.name;

    const timeSpan = document.createElement("small");
    timeSpan.textContent = `${file.timestamp} • ${formatSize(file.size)}`;
    timeSpan.className = "text-muted";

    leftDiv.appendChild(nameSpan);
    leftDiv.appendChild(timeSpan);

    const downloadA = document.createElement("a");
    downloadA.className = "btn btn-sm btn-outline-primary";
    downloadA.textContent = "Download";
    downloadA.href = `/files/download/${encodeURIComponent(file.name)}`;
    downloadA.setAttribute("download", "");

    li.appendChild(leftDiv);
    li.appendChild(downloadA);
    list.appendChild(li);
  }
}

async function loadFiles(type) {
  const list = document.getElementById(`${type}fileList`);
  list.innerHTML = "";

  try {
    const res = await fetch(`/files?type=${type}`);
    if (!res.ok) {
      throw new Error(`Failed to fetch files: ${res.statusText}`);
    }
    const files = await res.json();
    state[type].files = files;

    renderHeatmap(type);
    renderFileList(type);
  } catch (err) {
    console.error(`Error loading ${type} files:`, err);
    list.innerHTML = `<li class="list-group-item text-danger">Failed to load files</li>`;
  }
}

async function loadAllFiles() {
  loadFiles("csv");
  loadFiles("rerun");
  loadFiles("raw");
}

loadAllFiles();
