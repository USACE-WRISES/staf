// STAF Desktop launcher page. Vanilla JS, same idiom as the site's widgets.
// Protocol: the shell pushes full snapshots; we render from scratch each time.
// Outbound commands: { type: "launch"|"stop"|"viewLogs"|"openWebsite"|"openWeb"|"openLogsFolder"|"ready", appId? }
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

  var STATUS_LABEL = {
    stopped: "Not running",
    starting: "Starting…",
    running: "Running",
    stopping: "Stopping…",
    crashed: "Stopped unexpectedly",
  };

  function render(snapshot) {
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

  host.addEventListener("message", function (event) {
    var msg = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
    if (msg && msg.type === "snapshot") {
      render(msg);
    }
  });

  document.getElementById("open-website").addEventListener("click", function () {
    send("openWebsite");
  });
  document.getElementById("open-logs").addEventListener("click", function (e) {
    e.preventDefault();
    send("openLogsFolder");
  });

  send("ready");
})();
