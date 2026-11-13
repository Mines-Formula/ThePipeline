const POLL_INTERVAL = 2000;

const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const alertContainer = document.getElementById("alertContainer");
const inputFormat = document.getElementById("inputFormat");
const protobufOptions = document.getElementById("protobufOptions");
const schemaInput = document.getElementById("schemaInput");

let pollIntervalId = null;

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
      // An uploaded bundle overrides the bundled MF26/v3 default. Relative
      // paths are required for imports such as MF26/v3/ecu.proto.
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

async function loadFiles(type) {
  const list = document.getElementById(`${type}fileList`);
  list.innerHTML = "";

  try {
    const res = await fetch(`/files?type=${type}`);
    const files = await res.json();

    if (files.length === 0) {
      list.innerHTML = `<li class="list-group-item text-muted">No files found</li>`;
      return;
    }

    for (const file of files) {
      const li = document.createElement("li");
      li.className =
        "list-group-item d-flex justify-content-between align-items-center";

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
      downloadA.href = `/files/download/${encodeURIComponent(file.name)}?type=${encodeURIComponent(type)}`;
      downloadA.setAttribute("download", "");

      li.appendChild(leftDiv);
      li.appendChild(downloadA);
      list.appendChild(li);
    }
  } catch (err) {
    list.innerHTML = `<li class="list-group-item text-danger">Failed to load files</li>`;
  }
}

async function loadAllFiles() {
  loadFiles("csv");
  loadFiles("rerun");
  loadFiles("raw");
}

loadAllFiles();
