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
  csv: { files: [] },
  rerun: { files: [] },
  raw: { files: [] },
};

const FILE_TYPES = Object.keys(state);
const activityState = { selectedDate: null, selectedYear: "auto", vehicleFilter: "all" };

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
        await loadAllFiles();
        const classifications = (data.metrics && data.metrics.classifications) || [];
        const unknownItems = classifications.filter(
          (item) => item && item.vehicle_type === "unknown",
        );
        if (unknownItems.length > 0) {
          openClassifyModal(unknownItems);
        }
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

function canonicalRunId(type, filename) {
  const dotIdx = filename.lastIndexOf(".");
  let stem = dotIdx > 0 ? filename.slice(0, dotIdx) : filename;
  if (type === "raw" && stem.startsWith("raw_")) {
    stem = stem.slice("raw_".length);
  } else if (type === "csv" && stem.endsWith("_unixtime")) {
    stem = stem.slice(0, -"_unixtime".length);
  }
  return stem;
}

function matchesVehicleFilter(file) {
  if (activityState.vehicleFilter === "all") {
    return true;
  }
  const vehicleType = (file && file.vehicle_type ? file.vehicle_type : "unknown").toLowerCase();
  return vehicleType === activityState.vehicleFilter;
}

function attachVehicleFilterListeners() {
  const radios = document.querySelectorAll('input[name="vehicleFilter"]');
  radios.forEach((radio) => {
    if (!radio.dataset.listenerAttached) {
      radio.addEventListener("change", (e) => {
        if (e.target.checked) {
          activityState.vehicleFilter = e.target.value;
          renderHeatmap();
          renderAllFileLists();
        }
      });
      radio.dataset.listenerAttached = "true";
    }
  });
}

function renderAllFileLists() {
  FILE_TYPES.forEach(renderFileList);
}

function selectActivityDate(date) {
  activityState.selectedDate = date;
  if (heatmapTooltip) heatmapTooltip.classList.remove("show");
  renderHeatmap();
  renderAllFileLists();
}

function renderHeatmap() {
  const container = document.getElementById("activityHeatmap");
  const yearSelect = document.getElementById("activityYearSelect");
  if (!container) return;

  const allFiles = FILE_TYPES.flatMap((type) => state[type].files);
  const filteredByType = {};
  FILE_TYPES.forEach((type) => {
    filteredByType[type] = state[type].files.filter(matchesVehicleFilter);
  });
  const files = FILE_TYPES.flatMap((type) => filteredByType[type]);

  // Group files by date
  const counts = {};
  const countsByType = {};
  for (const type of FILE_TYPES) {
    for (const file of filteredByType[type]) {
      const date = extractFileDate(file);
      if (!date) continue;
      countsByType[date] ??= { csv: 0, rerun: 0, raw: 0 };
      countsByType[date][type]++;
    }
  }
  const fileDates = [];
  for (const file of files) {
    const dateStr = extractFileDate(file);
    if (dateStr) {
      counts[dateStr] = (counts[dateStr] || 0) + 1;
      fileDates.push(dateStr);
    }
  }

  // Determine available years from all loaded files so year choices stay stable
  const allFileDates = allFiles.map(extractFileDate).filter(Boolean);
  const currentYear = new Date().getFullYear();
  const yearsSet = new Set([currentYear]);
  allFileDates.forEach((d) => yearsSet.add(parseInt(d.slice(0, 4), 10)));
  const years = Array.from(yearsSet).sort((a, b) => b - a);

  // If selectedYear is 'auto', choose smart default
  if (!activityState.selectedYear || activityState.selectedYear === "auto") {
    if (allFileDates.length > 0) {
      const sortedDates = [...allFileDates].sort();
      const latestDateStr = sortedDates[sortedDates.length - 1];
      const latestYear = parseInt(latestDateStr.slice(0, 4), 10);
      const [ly, lm, ld] = latestDateStr.split("-").map(Number);
      const latestDateObj = new Date(ly, lm - 1, ld);
      const diffDays = (new Date() - latestDateObj) / (1000 * 60 * 60 * 24);

      if (diffDays > 365) {
        activityState.selectedYear = String(latestYear);
      } else {
        activityState.selectedYear = "last12";
      }
    } else {
      activityState.selectedYear = "last12";
    }
  }

  // Populate year selector
  if (yearSelect) {
    const previousValue = activityState.selectedYear;
    yearSelect.innerHTML = `<option value="last12">Last 12 Months</option>`;
    for (const yr of years) {
      yearSelect.innerHTML += `<option value="${yr}">${yr}</option>`;
    }
    yearSelect.value = previousValue;

    if (!yearSelect.dataset.listenerAttached) {
      yearSelect.addEventListener("change", (e) => {
        activityState.selectedYear = e.target.value;
        activityState.selectedDate = null;
        renderHeatmap();
        renderAllFileLists();
      });
      yearSelect.dataset.listenerAttached = "true";
    }
  }

  // Determine range
  let startSun, endSat, totalWeeks, isYearMode = false, targetYear = null;

  if (activityState.selectedYear === "last12") {
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
    targetYear = parseInt(activityState.selectedYear, 10);
    const jan1 = new Date(targetYear, 0, 1);
    startSun = new Date(targetYear, 0, 1 - jan1.getDay());
    const dec31 = new Date(targetYear, 11, 31);
    endSat = new Date(targetYear, 11, 31 + (6 - dec31.getDay()));
    totalWeeks = Math.round((endSat - startSun) / (7 * 86400000)) + 1;
  }

  // Calculate maxCount in view
  let maxCount = 0;

  for (let w = 0; w < totalWeeks; w++) {
    for (let d = 0; d < 7; d++) {
      const cur = new Date(startSun.getFullYear(), startSun.getMonth(), startSun.getDate() + (w * 7 + d));
      if (isYearMode && cur.getFullYear() !== targetYear) continue;
      const dStr = formatDateISO(cur);
      const c = counts[dStr] || 0;
      if (c > maxCount) maxCount = c;
    }
  }

  // SVG dimensions
  const cellSize = 10;
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
      const isSelected = activityState.selectedDate === dStr;
      const selectedClass = isSelected ? " is-selected" : "";
      const strokeColor = isSelected ? "#0969da" : level === 0 ? "rgba(27, 31, 35, 0.08)" : "none";
      const strokeWidth = isSelected ? "2" : "1";

      svg += `<rect class="heatmap-day${selectedClass}" data-date="${dStr}" data-count="${c}" data-level="${level}" x="${x}" y="${y}" width="${cellSize}" height="${cellSize}" rx="2" ry="2" fill="${fillColor}" stroke="${strokeColor}" stroke-width="${strokeWidth}"></rect>`;
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
      const breakdown = countsByType[dateStr] || { csv: 0, rerun: 0, raw: 0 };
      const message =
        count === 0
          ? `No files on ${formattedDate}`
          : `${count} file${count > 1 ? "s" : ""} on ${formattedDate} · CSV: ${breakdown.csv}, Rerun: ${breakdown.rerun}, Raw: ${breakdown.raw}`;

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
      selectActivityDate(activityState.selectedDate === dateStr ? null : dateStr);
    });
  });

  if (!container.dataset.scrollAttached) {
    container.addEventListener("scroll", () => {
      if (heatmapTooltip) heatmapTooltip.classList.remove("show");
    });
    container.dataset.scrollAttached = "true";
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
  const vehicleFiles = allFiles.filter(matchesVehicleFilter);
  const selectedDate = activityState.selectedDate;

  // Set header file count badge
  if (fileCountBadge) {
    fileCountBadge.textContent = `${vehicleFiles.length} file${vehicleFiles.length === 1 ? "" : "s"}`;
  }

  // Configure clear button
  if (clearBtn && !clearBtn.dataset.listenerAttached) {
    clearBtn.addEventListener("click", () => {
      selectActivityDate(null);
    });
    clearBtn.dataset.listenerAttached = "true";
  }

  // Filter files if selectedDate is set
  let displayFiles = vehicleFiles;
  if (selectedDate) {
    displayFiles = vehicleFiles.filter((f) => extractFileDate(f) === selectedDate);
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

  if (vehicleFiles.length === 0) {
    const label = activityState.vehicleFilter.toUpperCase();
    list.innerHTML = `<li class="list-group-item text-muted">No ${label} files found</li>`;
    return;
  }

  if (displayFiles.length === 0) {
    const prefix =
      activityState.vehicleFilter === "all"
        ? ""
        : `${activityState.vehicleFilter.toUpperCase()} `;
    list.innerHTML = `<li class="list-group-item text-muted">No ${prefix}files found for ${selectedDate}</li>`;
    return;
  }

  for (const file of displayFiles) {
    const li = document.createElement("li");
    li.className = "list-group-item d-flex justify-content-between align-items-center";

    const leftDiv = document.createElement("div");
    leftDiv.className = "d-flex flex-column me-2 overflow-hidden";

    const nameRow = document.createElement("div");
    nameRow.className = "d-flex align-items-center gap-1 min-w-0";

    const nameSpan = document.createElement("span");
    nameSpan.textContent = file.name;
    nameSpan.className = "me-1 text-truncate";
    nameSpan.style.maxWidth = "210px";
    nameSpan.title = file.name;
    nameRow.appendChild(nameSpan);

    const vehicleBadge = createVehicleBadge(type, file);
    if (vehicleBadge) {
      nameRow.appendChild(vehicleBadge);
    }

    const timeSpan = document.createElement("small");
    timeSpan.textContent = `${file.timestamp} • ${formatSize(file.size)}`;
    timeSpan.className = "text-muted";

    leftDiv.appendChild(nameRow);
    leftDiv.appendChild(timeSpan);

    const downloadA = document.createElement("a");
    downloadA.className = "btn btn-sm btn-outline-primary";
    downloadA.textContent = "Download";
    downloadA.href = `/files/download/${encodeURIComponent(file.name)}?type=${encodeURIComponent(type)}`;
    downloadA.setAttribute("download", "");

    li.appendChild(leftDiv);
    li.appendChild(downloadA);
    list.appendChild(li);
  }
}

function createVehicleBadge(type, file) {
  if (!file || !file.vehicle_type) {
    return null;
  }
  const vehicle = file.vehicle_type.toLowerCase();
  if (vehicle === "ev") {
    const span = document.createElement("span");
    span.className = "badge rounded-pill vehicle-badge vehicle-badge-ev";
    span.textContent = "EV";
    return span;
  }
  if (vehicle === "ic") {
    const span = document.createElement("span");
    span.className = "badge rounded-pill vehicle-badge vehicle-badge-ic";
    span.textContent = "IC";
    return span;
  }
  if (vehicle === "unknown") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "badge rounded-pill border-0 vehicle-badge vehicle-badge-unknown";
    btn.textContent = "Unknown";
    btn.title = "Click to classify as IC or EV";
    const runId = file.run_id || canonicalRunId(type, file.name);
    btn.addEventListener("click", () => {
      openClassifyModal([
        {
          run_id: runId,
          filename: file.name,
          reason: "Could not automatically determine IC or EV",
        },
      ]);
    });
    return btn;
  }
  return null;
}

function showClassifyModalElement(modalEl) {
  if (window.bootstrap && window.bootstrap.Modal) {
    window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
    return;
  }
  modalEl.classList.add("show");
  modalEl.style.display = "block";
  modalEl.removeAttribute("aria-hidden");
  modalEl.setAttribute("aria-modal", "true");
}

function hideClassifyModalElement(modalEl) {
  if (window.bootstrap && window.bootstrap.Modal) {
    window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
    return;
  }
  modalEl.classList.remove("show");
  modalEl.style.display = "none";
  modalEl.setAttribute("aria-hidden", "true");
  modalEl.removeAttribute("aria-modal");
}

function openClassifyModal(items) {
  const modalEl = document.getElementById("classifyModal");
  const listEl = document.getElementById("classifyModalList");
  const errorEl = document.getElementById("classifyModalError");
  if (!modalEl || !listEl || !items || items.length === 0) {
    return;
  }

  if (errorEl) {
    errorEl.classList.add("d-none");
    errorEl.textContent = "";
  }

  listEl.innerHTML = "";
  const seenRuns = new Set();
  for (const item of items) {
    if (!item || !item.run_id || seenRuns.has(item.run_id)) {
      continue;
    }
    seenRuns.add(item.run_id);

    const li = document.createElement("li");
    li.className = "list-group-item d-flex justify-content-between align-items-center gap-2";
    li.dataset.runId = item.run_id;
    li.dataset.resolved = "false";

    const infoDiv = document.createElement("div");
    infoDiv.className = "overflow-hidden";

    const titleDiv = document.createElement("div");
    titleDiv.className = "fw-semibold text-truncate";
    titleDiv.textContent = item.filename || item.run_id;

    const reasonSmall = document.createElement("small");
    reasonSmall.className = "text-muted d-block";
    reasonSmall.textContent = item.reason || "No EV or IC signals detected";

    infoDiv.appendChild(titleDiv);
    infoDiv.appendChild(reasonSmall);

    const actionDiv = document.createElement("div");
    actionDiv.className = "btn-group btn-group-sm flex-shrink-0";

    const icBtn = document.createElement("button");
    icBtn.type = "button";
    icBtn.className = "btn btn-outline-warning classify-ic-btn";
    icBtn.textContent = "IC";

    const evBtn = document.createElement("button");
    evBtn.type = "button";
    evBtn.className = "btn btn-outline-success classify-ev-btn";
    evBtn.textContent = "EV";

    icBtn.addEventListener("click", () =>
      submitClassification(item.run_id, "ic", li, actionDiv, modalEl),
    );
    evBtn.addEventListener("click", () =>
      submitClassification(item.run_id, "ev", li, actionDiv, modalEl),
    );

    actionDiv.appendChild(icBtn);
    actionDiv.appendChild(evBtn);
    li.appendChild(infoDiv);
    li.appendChild(actionDiv);
    listEl.appendChild(li);
  }

  [document.getElementById("classifyModalCloseBtn"), document.getElementById("classifyModalCloseX")].forEach(
    (btn) => {
      if (btn && !btn.dataset.fallbackAttached) {
        btn.addEventListener("click", () => {
          if (!(window.bootstrap && window.bootstrap.Modal)) {
            hideClassifyModalElement(modalEl);
          }
        });
        btn.dataset.fallbackAttached = "true";
      }
    },
  );

  showClassifyModalElement(modalEl);
}

async function submitClassification(runId, vehicleType, rowEl, actionDiv, modalEl) {
  const errorEl = document.getElementById("classifyModalError");
  const buttons = actionDiv.querySelectorAll("button");
  buttons.forEach((btn) => {
    btn.disabled = true;
  });
  if (errorEl) {
    errorEl.classList.add("d-none");
    errorEl.textContent = "";
  }

  try {
    const res = await fetch("/files/classify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId, vehicle_type: vehicleType }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.error || "Failed to save classification.");
    }

    rowEl.dataset.resolved = "true";
    actionDiv.innerHTML = `<span class="badge rounded-pill vehicle-badge vehicle-badge-${vehicleType}">${vehicleType.toUpperCase()} saved</span>`;

    await loadAllFiles();

    const listEl = document.getElementById("classifyModalList");
    const pending = listEl
      ? listEl.querySelectorAll('li[data-resolved="false"]').length
      : 0;
    if (pending === 0) {
      hideClassifyModalElement(modalEl);
    }
  } catch (err) {
    buttons.forEach((btn) => {
      btn.disabled = false;
    });
    if (errorEl) {
      errorEl.textContent = err.message || "Failed to save classification.";
      errorEl.classList.remove("d-none");
    }
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

    renderFileList(type);
  } catch (err) {
    console.error(`Error loading ${type} files:`, err);
    list.innerHTML = `<li class="list-group-item text-danger">Failed to load files</li>`;
  }
}

async function loadAllFiles() {
  // Choose the initial period only after all three datasets have loaded.
  await Promise.all(FILE_TYPES.map(loadFiles));
  renderHeatmap();
}

attachVehicleFilterListeners();
loadAllFiles();
