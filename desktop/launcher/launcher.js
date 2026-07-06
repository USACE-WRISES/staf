// STAF Desktop launcher page. Vanilla JS, same idiom as the site's widgets.
// Protocol: the shell pushes full snapshots (card grid) plus setup/update events; we render from
// scratch on every snapshot. Outbound commands: { type, appId? } — see LauncherProtocol.cs.
(function () {
  "use strict";

  var host = window.chrome && window.chrome.webview;
  if (!host) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      '<p style="padding:1rem;color:#c0392b">This page only works inside STAF Desktop.</p>');
    return;
  }

  function send(type, appId) {
    host.postMessage(JSON.stringify(appId ? { type: type, appId: appId } : { type: type }));
  }

  var grid = document.getElementById("apps-grid");
  var footerMode = document.getElementById("footer-mode");
  var appsView = document.getElementById("apps-view");
  var setupView = document.getElementById("setup-view");
  var setupMessage = document.getElementById("setup-message");
  var setupBar = document.getElementById("setup-bar");
  var setupProgress = document.getElementById("setup-progress");
  var setupDetail = document.getElementById("setup-detail");
  var setupActions = document.getElementById("setup-actions");
  var setupRetry = document.getElementById("setup-retry");
  var updateChip = document.getElementById("update-chip");
  var updateText = document.getElementById("update-text");
  var updateInstall = document.getElementById("update-install");

  var STATUS_LABEL = {
    stopped: "Not running",
    starting: "Starting…",
    running: "Running",
    stopping: "Stopping…",
    crashed: "Stopped unexpectedly",
  };

  // ── Card grid ──────────────────────────────────────────────────────────

  function renderSnapshot(snapshot) {
    grid.textContent = "";
    snapshot.apps.forEach(function (app) {
      var card = document.createElement("div");
      card.className = "apps-hub-card";
      card.dataset.app = app.id;

      var head = document.createElement("div");
      head.className = "apps-hub-card-head";
      var h3 = document.createElement("h3");
      h3.textContent = app.name;
      var badge = document.createElement("span");
      badge.className = "tier-badge tier-" + app.tierNum;
      badge.textContent = "Tier " + app.tierNum + " · " + app.tier;
      head.appendChild(h3);
      head.appendChild(badge);

      var fullname = document.createElement("p");
      fullname.className = "apps-hub-fullname";
      fullname.textContent = app.fullName;

      var desc = document.createElement("p");
      desc.className = "apps-hub-desc";
      desc.textContent = app.description;

      var status = document.createElement("div");
      status.className = "app-status";
      var dot = document.createElement("span");
      dot.className = "status-dot " + app.status;
      var statusText = document.createElement("span");
      statusText.className = "status-detail";
      statusText.textContent = STATUS_LABEL[app.status] || app.status;
      if (app.detail && (app.status === "crashed" || app.status === "starting")) {
        statusText.textContent += " — " + app.detail;
        statusText.title = app.detail;
      }
      status.appendChild(dot);
      status.appendChild(statusText);

      var actions = document.createElement("div");
      actions.className = "card-actions";

      var launch = document.createElement("button");
      launch.type = "button";
      launch.className = "btn btn-primary";
      if (app.status === "running") {
        launch.textContent = "Open " + app.name;
      } else if (app.status === "starting" || app.status === "stopping") {
        launch.textContent = app.status === "starting" ? "Starting…" : "Stopping…";
        launch.disabled = true;
      } else {
        launch.textContent = "Launch " + app.name;
      }
      launch.addEventListener("click", function () { send("launch", app.id); });

      var stop = document.createElement("button");
      stop.type = "button";
      stop.className = "btn btn-secondary";
      stop.textContent = "Stop";
      stop.disabled = !(app.status === "running" || app.status === "starting");
      stop.addEventListener("click", function () { send("stop", app.id); });

      var logs = document.createElement("button");
      logs.type = "button";
      logs.className = "btn btn-quiet";
      logs.textContent = "Log";
      logs.title = "Open this app's log file";
      logs.addEventListener("click", function () { send("viewLogs", app.id); });

      actions.appendChild(launch);
      actions.appendChild(stop);
      actions.appendChild(logs);

      if (app.webUrl) {
        var web = document.createElement("a");
        web.href = "#";
        web.className = "card-weblink";
        web.textContent = "web version ↗";
        web.title = "Open the hosted version in your browser: " + app.webUrl;
        web.addEventListener("click", function (e) {
          e.preventDefault();
          send("openWeb", app.id);
        });
        actions.appendChild(web);
      }

      card.appendChild(head);
      card.appendChild(fullname);
      card.appendChild(desc);
      card.appendChild(status);
      card.appendChild(actions);
      grid.appendChild(card);
    });

    footerMode.textContent =
      "STAF Desktop " + snapshot.shell.version +
      (snapshot.shell.mode === "dev" ? "  ·  dev mode (repo .venv)" : "") +
      "  ·  data: " + snapshot.shell.dataRoot;
  }

  // ── First-run setup view ───────────────────────────────────────────────

  function showSetup(msg) {
    appsView.hidden = true;
    setupView.hidden = false;
    setupView.classList.remove("error");
    setupMessage.textContent = msg.message || "";
    setupDetail.textContent = msg.detail || "";
    setupActions.hidden = true;
    if (typeof msg.percent === "number" && msg.percent >= 0) {
      setupProgress.hidden = false;
      setupBar.style.width = msg.percent + "%";
    } else {
      setupProgress.hidden = true;
    }
  }

  function showSetupError(msg) {
    appsView.hidden = true;
    setupView.hidden = false;
    setupView.classList.add("error");
    setupMessage.textContent = msg.message || "Setup failed.";
    setupDetail.textContent = "";
    setupProgress.hidden = true;
    setupActions.hidden = false;
    setupRetry.hidden = msg.canRetry === false;
  }

  function hideSetup() {
    setupView.hidden = true;
    setupView.classList.remove("error");
    appsView.hidden = false;
  }

  // ── Footer update chip ─────────────────────────────────────────────────

  var updateChipTimer = null;
  var chipAction = "applyUpdate"; // which command the chip button sends (payload vs shell update)

  function showUpdateChip(text, opts) {
    opts = opts || {};
    if (updateChipTimer) { clearTimeout(updateChipTimer); updateChipTimer = null; }
    if (opts.action) { chipAction = opts.action; }
    updateChip.hidden = false;
    updateChip.classList.toggle("error", !!opts.error);
    updateText.textContent = text;
    updateInstall.hidden = !!opts.hideButton;
    updateInstall.disabled = !!opts.disableButton;
    updateInstall.textContent = opts.buttonLabel || "Install";
    if (opts.autoHideMs) {
      updateChipTimer = setTimeout(function () { updateChip.hidden = true; }, opts.autoHideMs);
    }
  }

  // ── Shell → page ───────────────────────────────────────────────────────

  host.addEventListener("message", function (event) {
    var msg = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
    if (!msg || !msg.type) { return; }
    switch (msg.type) {
      case "snapshot":
        renderSnapshot(msg);
        break;
      case "setup":
        showSetup(msg);
        break;
      case "setupError":
        showSetupError(msg);
        break;
      case "setupDone":
        hideSetup();
        break;
      case "updateAvailable":
        showUpdateChip(msg.message, { action: "applyUpdate" });
        break;
      case "shellUpdateAvailable":
        showUpdateChip(msg.message, { action: "applyShellUpdate", buttonLabel: "Restart & update" });
        break;
      case "updateProgress":
        showUpdateChip(msg.message + (typeof msg.percent === "number" && msg.percent >= 0 ? " (" + msg.percent + "%)" : ""),
          { disableButton: true, buttonLabel: "Installing…" });
        break;
      case "updateDone":
        showUpdateChip(msg.message, { hideButton: true, autoHideMs: 10000 });
        break;
      case "updateError":
        showUpdateChip(msg.message, { error: true, buttonLabel: "Retry" });
        break;
    }
  });

  // ── Page → shell ───────────────────────────────────────────────────────

  document.getElementById("open-website").addEventListener("click", function () {
    send("openWebsite");
  });
  document.getElementById("open-logs").addEventListener("click", function (e) {
    e.preventDefault();
    send("openLogsFolder");
  });
  document.getElementById("ts-toggle").addEventListener("click", function (e) {
    e.preventDefault();
    var menu = document.getElementById("ts-menu");
    menu.hidden = !menu.hidden;
  });
  document.getElementById("ts-clear").addEventListener("click", function () { send("clearCaches"); });
  document.getElementById("ts-revert").addEventListener("click", function () { send("revertPayload"); });
  document.getElementById("ts-file").addEventListener("click", function () { send("installFromFile"); });
  setupRetry.addEventListener("click", function () {
    showSetup({ message: "Retrying…", percent: -1 });
    send("setupRetry");
  });
  document.getElementById("setup-from-file").addEventListener("click", function () {
    send("installFromFile");
  });
  updateInstall.addEventListener("click", function () {
    send(chipAction);
  });

  send("ready");
})();
