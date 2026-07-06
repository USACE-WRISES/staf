// DEV-ONLY mock of the WebView2 host (window.chrome.webview) so launcher.js can be exercised in
// a plain browser. Mirrors LauncherProtocol: snapshot pushes in, {type, appId} commands out.
// Never shipped — lives outside desktop/launcher/, which is what the shell packages.
(function () {
  "use strict";

  var apps = [
    card("easi", "EASI", "Ecosystem Assessment Screening Index", "Screening", 1,
      "Automated desktop screening using commonly used, nationally applicable metrics.",
      "https://gtmenichino-easi.share.connect.posit.cloud/", "stopped", null, null),
    card("sfari", "SFARI", "Stream Functions Assessment and Rapid Index", "Rapid", 2,
      "Nationally applicable, function-based rapid field assessment with desktop evidence support.",
      "https://gtmenichino-sfari.share.connect.posit.cloud/", "stopped", null, null),
    card("deep", "DEEP", "Detailed Evaluation of Ecosystem Processes", "Detailed", 3,
      "Runs detailed, curve-based site assessments. Comes with a limited number of predefined assessments out of the box.",
      "https://gtmenichino-deep.share.connect.posit.cloud/", "crashed", null,
      "exited unexpectedly (code 3)\nFatal Python error: Aborted"),
    card("curves", "stream-curves", "Reference and Regional Curve Builder", "Detailed", 3,
      "Builds the reference-curve assessments that DEEP runs. For assessment developers rather than day-to-day users.",
      "https://gtmenichino-stream-curves.share.connect.posit.cloud/", "stopped", null, null),
  ];

  function card(id, name, fullName, tier, tierNum, description, webUrl, status, port, detail) {
    return { id: id, name: name, fullName: fullName, tier: tier, tierNum: tierNum,
             description: description, webUrl: webUrl, status: status, port: port, detail: detail };
  }

  function find(id) {
    for (var i = 0; i < apps.length; i++) {
      if (apps[i].id === id) { return apps[i]; }
    }
    return null;
  }

  var listeners = [];

  function snapshot() {
    return {
      type: "snapshot",
      shell: { version: "0.1.0", mode: "dev", dataRoot: "C:\\Users\\demo\\AppData\\Local\\STAF" },
      apps: apps,
    };
  }

  function emit() {
    var json = JSON.stringify(snapshot());
    listeners.forEach(function (cb) { cb({ data: json }); });
  }

  function emitRaw(msg) {
    var json = JSON.stringify(msg);
    listeners.forEach(function (cb) { cb({ data: json }); });
  }

  // Dev-banner buttons: drive the setup/update UI states without a shell.
  window.__mockScenario = function (name) {
    if (name === "firstRun") {
      emitRaw({ type: "setup", message: "Downloading the assessment runtime (431 MB)…", percent: 0, detail: "env · 0 / 431 MB" });
      var pct = 0;
      var timer = setInterval(function () {
        pct += 7;
        if (pct >= 100) {
          clearInterval(timer);
          emitRaw({ type: "setupDone" });
          emitRaw(snapshot());
          return;
        }
        emitRaw({ type: "setup", message: "Downloading the assessment runtime (431 MB)…", percent: pct, detail: "env · " + Math.round(431 * pct / 100) + " / 431 MB" });
      }, 250);
    } else if (name === "setupError") {
      emitRaw({ type: "setupError", message: "Could not download the STAF runtime: the update server is unreachable.", canRetry: true });
    } else if (name === "updateAvailable") {
      emitRaw({ type: "updateAvailable", message: "Update available (26 MB)" });
    } else if (name === "shellUpdate") {
      emitRaw({ type: "shellUpdateAvailable", message: "New STAF Desktop 0.2.0 available" });
    }
  };

  window.__mockCommands = [];

  window.chrome = window.chrome || {};
  window.chrome.webview = {
    addEventListener: function (type, cb) {
      if (type === "message") { listeners.push(cb); }
    },
    postMessage: function (raw) {
      var cmd = JSON.parse(raw);
      window.__mockCommands.push(cmd);
      console.log("[mock-host] command:", JSON.stringify(cmd));
      var app = cmd.appId ? find(cmd.appId) : null;
      switch (cmd.type) {
        case "ready":
          emit();
          break;
        case "setupRetry":
          window.__mockScenario("firstRun");
          break;
        case "applyUpdate":
          emitRaw({ type: "updateProgress", message: "Downloading the STAF apps (26 MB)…", percent: 40 });
          setTimeout(function () {
            emitRaw({ type: "updateDone", message: "Update installed. Apps use it the next time they start." });
          }, 900);
          break;
        case "applyShellUpdate":
          emitRaw({ type: "updateProgress", message: "Downloading STAF Desktop update…", percent: 55 });
          break;
        case "launch":
          if (!app) { return; }
          if (app.status === "running") {
            console.log("[mock-host] would focus window for", app.id);
            return;
          }
          app.status = "starting";
          app.detail = "starting…";
          emit();
          setTimeout(function () {
            app.status = "running";
            app.port = 8100 + apps.indexOf(app);
            app.detail = null;
            emit();
          }, 700);
          break;
        case "stop":
          if (!app) { return; }
          app.status = "stopping";
          emit();
          setTimeout(function () {
            app.status = "stopped";
            app.port = null;
            app.detail = "stopped (exit code 0)";
            emit();
          }, 400);
          break;
        default:
          // viewLogs / openWeb / openWebsite / openLogsFolder — recorded only.
          break;
      }
    },
  };
})();
