(() => {
  "use strict";

  const FALLBACK_STYLES = [
    ["classic", "Classic Topographic", "#F2EFE6", "#17332B", "#E4431B", "opentopo"],
    ["muted-alpine", "Muted Alpine", "#F3F0E8", "#19372F", "#ED5B2A", "opentopo"],
    ["monochrome-relief", "Monochrome Relief", "#F4F2EC", "#202724", "#E44A24", "opentopo"],
    ["vintage-expedition", "Vintage Expedition", "#E9DDC2", "#273D31", "#B8422D", "opentopo"],
    ["cool-minimal", "Cool Minimal", "#E8EFED", "#17384A", "#153F63", "opentopo"],
    ["dark-topographic", "Dark Topographic", "#101918", "#F1EEE4", "#FF6338", "opentopo"],
    ["high-contrast-hiking", "High-Contrast Hiking", "#EFF1E8", "#153A2B", "#E22E1B", "opentopo"],
    ["esri-topographic", "Esri World Topographic", "#F0F1EE", "#213B42", "#E84B22", "esri"],
    ["stamen-terrain", "Stamen Terrain", "#F1EEE5", "#263A32", "#E34B25", "stadia"],
    ["thunderforest-outdoors", "Thunderforest Outdoors", "#EEF1E8", "#173A2B", "#E43F20", "thunderforest"],
  ].map(([id, label, paper, ink, route, default_provider]) => ({
    id,
    label,
    paper,
    ink,
    route,
    default_provider,
  }));

  const state = {
    apiConfig: null,
    defaults: null,
    selectedFiles: [],
    upload: null,
    job: null,
    resultJob: null,
    eventSource: null,
    pollTimer: null,
    previewTimer: null,
    previewReady: false,
    busy: false,
  };

  const byId = (id) => document.getElementById(id);
  const form = byId("poster-form");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;

  function requestHeaders(json = false) {
    const headers = { "X-Requested-With": "XMLHttpRequest" };
    if (json) headers["Content-Type"] = "application/json";
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    return headers;
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: { ...requestHeaders(Boolean(options.body && typeof options.body === "string")), ...options.headers },
    });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("json") ? await response.json() : null;
    if (!response.ok) {
      const apiError = payload?.error || {};
      const error = new Error(apiError.message || `Request failed (${response.status})`);
      error.code = apiError.code || "request_failed";
      error.details = apiError.details || [];
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function showError(error, fallbackTitle = "Something needs attention") {
    const panel = byId("error-panel");
    byId("error-title").textContent = fallbackTitle;
    byId("error-message").textContent = error?.message || String(error);
    const details = byId("error-details");
    details.replaceChildren();
    for (const detail of error?.details || []) {
      const item = document.createElement("li");
      item.textContent =
        typeof detail === "string"
          ? detail
          : detail.message || detail.msg || JSON.stringify(detail);
      details.append(item);
    }
    panel.hidden = false;
    panel.focus();
  }

  function clearError() {
    byId("error-panel").hidden = true;
    byId("error-details").replaceChildren();
  }

  function formatBytes(value) {
    const bytes = Number(value) || 0;
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  }

  function formatNumber(value, maximumFractionDigits = 1) {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits }).format(Number(value) || 0);
  }

  function configStyles(config) {
    if (Array.isArray(config?.styles) && config.styles.length) return config.styles;
    if (config?.styles && typeof config.styles === "object") {
      return Object.entries(config.styles).map(([id, style]) => ({ id, ...style }));
    }
    return FALLBACK_STYLES;
  }

  function createStyleCards(styles, selected) {
    const container = byId("style-cards");
    container.replaceChildren();
    styles.forEach((style, index) => {
      const label = document.createElement("label");
      label.className = "style-card";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "style_name";
      radio.value = style.id;
      radio.checked = style.id === selected || (!selected && index === 0);
      const body = document.createElement("span");
      body.className = "style-card__body";
      const swatch = document.createElement("span");
      swatch.className = "style-swatch";
      swatch.style.setProperty("--swatch-paper", style.paper || "#F2EFE6");
      swatch.style.setProperty("--swatch-ink", style.ink || "#17332B");
      swatch.style.setProperty("--swatch-route", style.route || "#E4431B");
      const words = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = style.label || style.id;
      const provider = document.createElement("small");
      provider.textContent = `${style.default_provider || style.provider || "map"} provider`;
      words.append(title, provider);
      body.append(swatch, words);
      label.append(radio, body);
      container.append(label);
    });
  }

  function setSelectOptions(select, choices, selected, labelFor = (item) => item) {
    select.replaceChildren();
    for (const choice of choices) {
      const option = document.createElement("option");
      option.value = typeof choice === "string" ? choice : choice.value;
      option.textContent = labelFor(choice);
      option.selected = option.value === selected;
      select.append(option);
    }
  }

  function applySchemaBounds(schema) {
    const mapping = {
      title: "title",
      subtitle: "subtitle",
      zoom: "zoom",
      padding_percent: "padding-percent",
      margin_mm: "margin-mm",
      basemap_width: "basemap-width",
      max_tiles: "max-tiles",
      simplify_points: "simplify-points",
      route_width: "route-width",
      dpi: "dpi",
      jpeg_quality: "jpeg-quality",
    };
    const properties = schema?.properties || {};
    Object.entries(mapping).forEach(([field, id]) => {
      const input = byId(id);
      const metadata = properties[field] || {};
      if (metadata.minimum !== undefined) input.min = metadata.minimum;
      if (metadata.maximum !== undefined) input.max = metadata.maximum;
      if (metadata.minLength !== undefined) input.minLength = metadata.minLength;
      if (metadata.maxLength !== undefined) input.maxLength = metadata.maxLength;
    });
  }

  function applyDefaults(defaults) {
    if (!defaults) return;
    Object.entries(defaults).forEach(([name, value]) => {
      const inputs = form.elements.namedItem(name);
      if (!inputs) return;
      if (inputs instanceof RadioNodeList) {
        inputs.value = value ?? "";
      } else {
        inputs.value = value ?? "";
      }
    });
    const paper = String(defaults.paper_size || "A3").toUpperCase();
    const paperSelect = byId("paper-size");
    const hasPaper = [...paperSelect.options].some((option) => option.value === paper);
    paperSelect.value = hasPaper ? paper : "custom";
    if (!hasPaper) parseCustomPaper(paper);
    createStyleCards(configStyles(state.apiConfig), defaults.style_name);
    syncControls();
  }

  async function loadConfiguration() {
    try {
      const config = await api("/api/config");
      state.apiConfig = config;
      state.defaults = config.defaults || {};
      const paperSizes = config.paper_sizes || ["A3", "A4", "A2", "LETTER"];
      setSelectOptions(
        byId("paper-size"),
        [...paperSizes.map((size) => ({ value: size, label: titleCase(size) })), { value: "custom", label: "Custom size" }],
        state.defaults.paper_size || "A3",
        (item) => item.label,
      );
      const providerOptions = [{ value: "", label: "Use style default" }];
      for (const provider of config.providers || []) {
        const id = typeof provider === "string" ? provider : provider.id;
        const configured = typeof provider === "string" ? true : provider.configured;
        providerOptions.push({
          value: id,
          label: `${titleCase(id)}${configured ? "" : " · credential required"}`,
        });
      }
      setSelectOptions(byId("provider"), providerOptions, state.defaults.provider || "", (item) => item.label);
      applySchemaBounds(config.poster_schema);
      const maxTiles = config.limits?.max_tiles;
      if (maxTiles) byId("max-tiles").max = maxTiles;
      const maxFiles = Math.min(Number(config.limits?.max_files || 15), 15);
      byId("max-files-label").textContent = maxFiles;
      const maxFileBytes = config.limits?.max_file_bytes;
      if (maxFileBytes) {
        byId("drop-zone").querySelector("small").textContent =
          `Up to ${maxFiles} files · ${formatBytes(maxFileBytes)} each`;
      }
      applyDefaults(state.defaults);
    } catch (error) {
      state.defaults = collectConfig();
      createStyleCards(FALLBACK_STYLES, "classic");
      showError(error, "Could not load server settings");
    }
  }

  function titleCase(value) {
    return String(value)
      .toLowerCase()
      .replace(/(^|[-_ ])\w/g, (letter) => letter.toUpperCase());
  }

  function addFiles(fileList) {
    clearError();
    const limits = state.apiConfig?.limits || {};
    const maxFiles = Math.min(Number(limits.max_files || 15), 15);
    const maxBytes = Number(limits.max_file_bytes || 25 * 1024 ** 2);
    const totalMax = Number(limits.max_upload_bytes || 100 * 1024 ** 2);
    const incoming = [...fileList];
    const invalid = incoming.find((file) => !file.name.toLowerCase().endsWith(".gpx"));
    if (invalid) {
      showError(new Error(`${invalid.name} is not a GPX file.`), "Choose GPX tracks");
      return;
    }
    const oversized = incoming.find((file) => file.size > maxBytes);
    if (oversized) {
      showError(
        new Error(`${oversized.name} is ${formatBytes(oversized.size)}; the limit is ${formatBytes(maxBytes)}.`),
        "File is too large",
      );
      return;
    }
    const unique = [...state.selectedFiles];
    for (const file of incoming) {
      const duplicate = unique.some(
        (item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified,
      );
      if (!duplicate) unique.push(file);
    }
    if (unique.length > maxFiles) {
      showError(new Error(`Select no more than ${maxFiles} GPX files at once.`), "Too many files");
      return;
    }
    const total = unique.reduce((sum, file) => sum + file.size, 0);
    if (total > totalMax) {
      showError(new Error(`The selected files total ${formatBytes(total)}; the limit is ${formatBytes(totalMax)}.`), "Upload is too large");
      return;
    }
    state.selectedFiles = unique;
    renderFileQueue();
  }

  function renderFileQueue() {
    const wrap = byId("file-queue-wrap");
    const list = byId("file-queue");
    list.replaceChildren();
    state.selectedFiles.forEach((file, index) => {
      const item = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = file.name;
      const size = document.createElement("small");
      size.textContent = formatBytes(file.size);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${file.name}`);
      remove.textContent = "×";
      remove.addEventListener("click", () => {
        state.selectedFiles.splice(index, 1);
        renderFileQueue();
      });
      item.append(name, size, remove);
      list.append(item);
    });
    wrap.hidden = state.selectedFiles.length === 0;
    byId("upload-button").textContent = state.selectedFiles.length === 1
      ? "Read selected track"
      : `Read ${state.selectedFiles.length} selected tracks`;
  }

  async function uploadFiles() {
    if (!state.selectedFiles.length || state.busy) return;
    clearError();
    setBusy(true);
    const formData = new FormData();
    state.selectedFiles.forEach((file) => formData.append("files", file, file.name));
    const oldUploadId = state.upload?.upload_id;
    try {
      const upload = await api("/api/uploads", {
        method: "POST",
        body: formData,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      state.upload = upload;
      state.selectedFiles = [];
      state.resultJob = null;
      byId("render-result").hidden = true;
      renderFileQueue();
      renderUploadSummary(upload);
      updateReadyState();
      if (oldUploadId && oldUploadId !== upload.upload_id) {
        api(`/api/uploads/${encodeURIComponent(oldUploadId)}`, { method: "DELETE" }).catch(() => {});
      }
    } catch (error) {
      showError(error, "Tracks could not be read");
    } finally {
      setBusy(false);
    }
  }

  function renderUploadSummary(upload) {
    byId("summary-empty").hidden = true;
    byId("summary-content").hidden = false;
    byId("stat-routes").textContent = formatNumber(upload.summary?.route_count, 0);
    byId("stat-distance").textContent = formatNumber(upload.summary?.distance_km);
    byId("stat-ascent").textContent = formatNumber(upload.summary?.ascent_m, 0);
    byId("stat-points").textContent = formatNumber(upload.summary?.point_count, 0);
    const routes = upload.routes || [];
    byId("stage-count").textContent = `(${routes.length})`;
    const list = byId("route-list");
    list.replaceChildren();
    routes.forEach((route) => {
      const item = document.createElement("li");
      const name = document.createElement("strong");
      name.textContent = route.name || "Unnamed route";
      const stats = document.createElement("small");
      stats.textContent = `${formatNumber(route.distance_km)} km · ${formatNumber(route.ascent_m, 0)} m ascent · ${formatNumber(route.point_count, 0)} points`;
      item.append(name, stats);
      list.append(item);
    });
    const expiry = upload.expires_at ? new Date(upload.expires_at) : null;
    byId("expiry-note").textContent =
      expiry && !Number.isNaN(expiry.valueOf())
        ? `This upload expires ${expiry.toLocaleString()}.`
        : "This upload is temporary and will expire automatically.";
  }

  function clearUploadUI() {
    state.upload = null;
    state.previewReady = false;
    state.resultJob = null;
    byId("summary-empty").hidden = false;
    byId("summary-content").hidden = true;
    byId("route-list").replaceChildren();
    byId("preview-image").hidden = true;
    byId("preview-image").removeAttribute("src");
    byId("preview-empty").hidden = false;
    byId("render-result").hidden = true;
    updateReadyState();
  }

  async function deleteUpload() {
    if (!state.upload) return;
    clearError();
    const id = state.upload.upload_id;
    try {
      await api(`/api/uploads/${encodeURIComponent(id)}`, { method: "DELETE" });
      stopJobTracking();
      state.job = null;
      clearUploadUI();
    } catch (error) {
      showError(error, "Upload could not be deleted");
    }
  }

  function parseCustomPaper(value) {
    const match = String(value).match(/^([\d.]+)\s*[x×]\s*([\d.]+)\s*(mm|cm|in)$/i);
    if (!match) return;
    byId("paper-width").value = match[1];
    byId("paper-height").value = match[2];
    byId("paper-unit").value = match[3].toLowerCase();
  }

  function collectConfig() {
    const data = new FormData(form);
    const paper = byId("paper-size").value === "custom"
      ? `${byId("paper-width").value}x${byId("paper-height").value}${byId("paper-unit").value}`
      : byId("paper-size").value;
    return {
      title: String(data.get("title") || "").trim(),
      subtitle: String(data.get("subtitle") || "").trim(),
      paper_size: paper,
      orientation: data.get("orientation") || "landscape",
      style_name: data.get("style_name") || "classic",
      provider: data.get("provider") || null,
      zoom: Number(data.get("zoom")),
      padding_percent: Number(data.get("padding_percent")),
      margin_mm: Number(data.get("margin_mm")),
      basemap_width: Number(data.get("basemap_width")),
      max_tiles: Number(data.get("max_tiles")),
      simplify_points: Number(data.get("simplify_points")),
      route_width: Number(data.get("route_width")),
      route_order: data.get("route_order") || "auto",
      output_format: data.get("output_format") || "pdf",
      dpi: Number(data.get("dpi")),
      jpeg_quality: Number(data.get("jpeg_quality")),
    };
  }

  function validateConfig() {
    clearFieldErrors();
    const invalid = [...form.querySelectorAll(":invalid")];
    if (byId("paper-size").value === "custom") {
      for (const input of [byId("paper-width"), byId("paper-height")]) {
        if (!input.value || Number(input.value) <= 0) invalid.push(input);
      }
    }
    const unique = [...new Set(invalid)];
    if (!unique.length) {
      byId("validation-summary").hidden = true;
      return true;
    }
    const list = byId("validation-summary").querySelector("ul");
    list.replaceChildren();
    unique.forEach((input) => {
      input.setAttribute("aria-invalid", "true");
      const label = form.querySelector(`label[for="${input.id}"]`)?.textContent?.trim() || input.name;
      const message = input.validationMessage || `${label} needs a valid value.`;
      const item = document.createElement("li");
      item.textContent = `${label}: ${message}`;
      list.append(item);
    });
    byId("validation-summary").hidden = false;
    byId("validation-summary").focus();
    return false;
  }

  function clearFieldErrors() {
    form.querySelectorAll('[aria-invalid="true"]').forEach((input) => input.removeAttribute("aria-invalid"));
    form.querySelectorAll(".field-error").forEach((error) => { error.textContent = ""; });
  }

  function paperRatio(paper, orientation) {
    const ratios = {
      A0: 841 / 1189,
      A1: 594 / 841,
      A2: 420 / 594,
      A3: 297 / 420,
      A4: 210 / 297,
      A5: 148 / 210,
      LETTER: 8.5 / 11,
      LEGAL: 8.5 / 14,
      TABLOID: 11 / 17,
    };
    let ratio = ratios[paper];
    if (!ratio && paper === "custom") {
      ratio = Number(byId("paper-width").value) / Number(byId("paper-height").value);
    }
    if (!Number.isFinite(ratio) || ratio <= 0) ratio = ratios.A3;
    return orientation === "landscape"
      ? Math.max(ratio, 1 / ratio)
      : Math.min(ratio, 1 / ratio);
  }

  function syncControls() {
    const config = collectConfig();
    const custom = byId("paper-size").value === "custom";
    byId("custom-paper").hidden = !custom;
    const raster = config.output_format !== "pdf";
    byId("dpi-field").hidden = !raster;
    byId("jpeg-quality-field").hidden = config.output_format !== "jpeg";
    byId("route-width-output").textContent = `${config.route_width} pt`;
    byId("padding-output").textContent = `${config.padding_percent}%`;
    byId("jpeg-quality-output").textContent = config.jpeg_quality;
    const frame = byId("poster-frame");
    frame.classList.toggle("is-portrait", config.orientation === "portrait");
    frame.classList.toggle("is-landscape", config.orientation === "landscape");
    frame.style.aspectRatio = String(paperRatio(byId("paper-size").value, config.orientation));
    frame.dataset.paper = config.paper_size;
    byId("preview-meta").textContent =
      `${config.paper_size} · ${titleCase(config.orientation)} · ${config.output_format.toUpperCase()}`;
    const provider = config.provider;
    if (provider) {
      const info = (state.apiConfig?.providers || []).find((item) => item.id === provider);
      byId("provider-status").textContent =
        info?.configured === false
          ? `${titleCase(provider)} requires a server-side credential.`
          : `${titleCase(provider)} is available.`;
    } else {
      byId("provider-status").textContent = "The selected style chooses its default provider.";
    }
  }

  function schedulePreview() {
    if (!state.previewReady || !state.upload || state.busy) return;
    byId("preview-stale").hidden = false;
    clearTimeout(state.previewTimer);
    state.previewTimer = window.setTimeout(() => createRender("preview"), 900);
  }

  function setBusy(busy) {
    state.busy = busy;
    byId("upload-button").disabled = busy;
    byId("preview-button").disabled = busy || !state.upload;
    byId("render-button").disabled = busy || !state.upload;
    byId("drop-zone").classList.toggle("is-busy", busy);
    updateReadyState();
  }

  function updateReadyState() {
    const ready = Boolean(state.upload);
    byId("preview-button").disabled = state.busy || !ready;
    byId("render-button").disabled = state.busy || !ready;
    document.querySelector(".final-bar").classList.toggle("is-ready", ready && !state.busy);
    byId("ready-label").textContent = state.busy ? "Poster render in progress" : ready ? "Routes ready to plot" : "Add routes to begin";
    byId("ready-detail").textContent = state.busy
      ? "You can safely leave this tab open."
      : ready
        ? "Preview first, or generate the print-ready poster."
        : "Your settings will stay here.";
  }

  async function createRender(mode) {
    if (!state.upload || state.busy || !validateConfig()) return;
    clearError();
    clearTimeout(state.previewTimer);
    byId("preview-stale").hidden = true;
    setBusy(true);
    showProgress({ phase: "queued", percent: 0, message: "Waiting for a render worker…" }, mode);
    try {
      const accepted = await api("/api/renders", {
        method: "POST",
        body: JSON.stringify({
          upload_id: state.upload.upload_id,
          mode,
          config: collectConfig(),
        }),
      });
      state.job = { ...accepted, mode };
      trackJob(accepted);
    } catch (error) {
      setBusy(false);
      byId("job-progress").hidden = true;
      showError(error, mode === "preview" ? "Preview could not start" : "Poster could not start");
    }
  }

  function trackJob(accepted) {
    stopJobTracking(false);
    if ("EventSource" in window && accepted.events_url) {
      const source = new EventSource(accepted.events_url);
      state.eventSource = source;
      source.addEventListener("progress", (event) => {
        try {
          const payload = JSON.parse(event.data);
          handleJobUpdate(payload);
        } catch {
          startPolling(accepted.status_url);
        }
      });
      source.addEventListener("error", () => {
        source.close();
        if (state.eventSource === source) state.eventSource = null;
        startPolling(accepted.status_url);
      });
    } else {
      startPolling(accepted.status_url);
    }
  }

  function startPolling(statusUrl) {
    if (state.pollTimer) return;
    const poll = async () => {
      try {
        const status = await api(statusUrl);
        handleJobUpdate(status);
      } catch (error) {
        stopJobTracking();
        setBusy(false);
        byId("job-progress").hidden = true;
        showError(error, "Render status was lost");
      }
    };
    poll();
    state.pollTimer = window.setInterval(poll, 2000);
  }

  function handleJobUpdate(payload) {
    const progress = payload.progress || payload;
    showProgress(progress, state.job?.mode);
    if (!["succeeded", "failed", "cancelled", "expired"].includes(payload.status)) return;
    const statusUrl = state.job?.status_url;
    if (payload.status === "succeeded" && !payload.artifact && statusUrl) {
      api(statusUrl)
        .then((fullStatus) => completeJob(fullStatus))
        .catch((error) => failJob(error));
      return;
    }
    completeJob(payload);
  }

  function showProgress(progress, mode) {
    const section = byId("job-progress");
    const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0));
    const phase = String(progress.phase || "queued").replaceAll("_", " ");
    section.hidden = false;
    byId("progress-label").textContent =
      mode === "preview" ? `Preview · ${titleCase(phase)}` : `Poster · ${titleCase(phase)}`;
    byId("progress-message").textContent = progress.message || "Working…";
    byId("progress-percent").textContent = `${percent}%`;
    byId("progress-bar").style.width = `${percent}%`;
    const track = section.querySelector('[role="progressbar"]');
    track.setAttribute("aria-valuenow", percent);
    byId("progress-live").textContent =
      percent % 10 === 0 ? `${titleCase(phase)}, ${percent} percent` : "";
  }

  function completeJob(status) {
    stopJobTracking();
    setBusy(false);
    byId("job-progress").hidden = true;
    if (status.status === "succeeded" && status.artifact) {
      state.job = { ...state.job, ...status };
      if (status.mode === "preview" || state.job.mode === "preview") {
        const image = byId("preview-image");
        image.src = `${status.artifact.download_url}?preview=${Date.now()}`;
        image.hidden = false;
        byId("preview-empty").hidden = true;
        state.previewReady = true;
      } else {
        showResult(status);
      }
      return;
    }
    if (status.status === "cancelled") {
      showError(new Error("The render was cancelled. Your upload and settings are unchanged."), "Render cancelled");
      return;
    }
    const error = status.error || new Error(
      status.status === "expired" ? "The render expired before it could be downloaded." : "The render failed.",
    );
    failJob(error);
  }

  function failJob(error) {
    stopJobTracking();
    setBusy(false);
    byId("job-progress").hidden = true;
    showError(error, "Poster could not be generated");
  }

  function showResult(status) {
    const artifact = status.artifact;
    state.resultJob = status;
    byId("result-filename").textContent = artifact.filename || "poster";
    byId("result-format").textContent = (artifact.media_type?.split("/").pop() || collectConfig().output_format).toUpperCase();
    byId("result-size").textContent = formatBytes(artifact.size_bytes);
    byId("result-dimensions").textContent =
      artifact.dimensions || `${collectConfig().paper_size} · ${titleCase(collectConfig().orientation)}`;
    byId("download-result").href = artifact.download_url;
    byId("render-result").hidden = false;
    byId("render-result").focus();
  }

  function stopJobTracking(clearJob = false) {
    if (state.eventSource) state.eventSource.close();
    state.eventSource = null;
    if (state.pollTimer) window.clearInterval(state.pollTimer);
    state.pollTimer = null;
    if (clearJob) state.job = null;
  }

  async function cancelJob() {
    if (!state.job?.job_id) return;
    try {
      await api(`/api/renders/${encodeURIComponent(state.job.job_id)}`, { method: "DELETE" });
      stopJobTracking();
      setBusy(false);
      byId("job-progress").hidden = true;
    } catch (error) {
      showError(error, "Render could not be cancelled");
    }
  }

  async function deleteResult() {
    if (!state.resultJob?.job_id) return;
    try {
      await api(`/api/renders/${encodeURIComponent(state.resultJob.job_id)}`, { method: "DELETE" });
      byId("render-result").hidden = true;
      state.resultJob = null;
    } catch (error) {
      showError(error, "Poster could not be deleted");
    }
  }

  function resetOptions() {
    form.reset();
    applyDefaults(state.defaults || {
      title: "My GPX Adventure",
      subtitle: "",
      paper_size: "A3",
      orientation: "landscape",
      style_name: "classic",
      output_format: "pdf",
    });
    clearFieldErrors();
    byId("validation-summary").hidden = true;
    schedulePreview();
  }

  function bindEvents() {
    const input = byId("gpx-files");
    const dropZone = byId("drop-zone");
    input.addEventListener("change", () => {
      addFiles(input.files);
      input.value = "";
    });
    dropZone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });
    ["dragenter", "dragover"].forEach((name) => {
      dropZone.addEventListener(name, (event) => {
        event.preventDefault();
        dropZone.classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach((name) => {
      dropZone.addEventListener(name, (event) => {
        event.preventDefault();
        dropZone.classList.remove("is-dragging");
      });
    });
    dropZone.addEventListener("drop", (event) => addFiles(event.dataTransfer.files));
    byId("clear-files").addEventListener("click", () => {
      state.selectedFiles = [];
      renderFileQueue();
    });
    byId("upload-button").addEventListener("click", uploadFiles);
    byId("delete-upload").addEventListener("click", deleteUpload);
    byId("clear-errors").addEventListener("click", clearError);
    byId("preview-button").addEventListener("click", () => createRender("preview"));
    byId("render-button").addEventListener("click", () => createRender("final"));
    byId("cancel-job").addEventListener("click", cancelJob);
    byId("delete-result").addEventListener("click", deleteResult);
    byId("reset-options").addEventListener("click", resetOptions);
    form.addEventListener("input", () => {
      syncControls();
      schedulePreview();
    });
    form.addEventListener("change", () => {
      syncControls();
      schedulePreview();
    });
    window.addEventListener("beforeunload", () => stopJobTracking());
  }

  bindEvents();
  createStyleCards(FALLBACK_STYLES, "classic");
  syncControls();
  loadConfiguration();
})();
