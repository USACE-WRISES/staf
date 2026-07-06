using System.Diagnostics;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Staf.Desktop.Core;
using Staf.Desktop.Core.Manifest;
using Staf.Desktop.Core.Payload;
using Staf.Desktop.Core.Processes;

namespace Staf.Desktop;

/// <summary>
/// The main window: hosts the launcher page (card grid + first-run setup view) in WebView2 and
/// acts as the hub between the page, the payload manager, the app supervisor, and per-app windows.
/// The supervisor is created only once a payload (or the dev .venv) is resolvable — on a fresh
/// install the page shows the setup view while the payload manager downloads the runtime.
/// </summary>
internal sealed class LauncherForm : Form, IShellHub
{
    private static readonly string LauncherAssetsDir = Path.Combine(AppContext.BaseDirectory, "launcher");
    private const string LauncherOrigin = "https://launcher.staf/";
    private const string SiteHomeUrl = "https://usace-wrises.github.io/staf/";

    private readonly ShellServices _services;
    private readonly WebView2 _webView;
    private readonly Dictionary<string, AppWindowForm> _appWindows = new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> _pendingOpen = new(StringComparer.OrdinalIgnoreCase);

    private CoreWebView2Environment? _environment;
    private AppSupervisor? _supervisor;
    private (LatestManifest Manifest, UpdatePlan Plan)? _pendingUpdate;
    private bool _payloadBusy;
    private bool _shutdownComplete;
    private bool _shuttingDown;

    public ShellConfig Config => _services.Config;

    public LauncherForm(ShellServices services)
    {
        _services = services;

        Text = "STAF Desktop";
        StartPosition = FormStartPosition.CenterScreen;
        // Tall enough that the four cards (two rows at this width) fit without a scrollbar.
        Size = new Size(1120, 830);
        MinimumSize = new Size(780, 500);

        _webView = new WebView2 { Dock = DockStyle.Fill };
        Controls.Add(_webView);

        Load += async (_, _) => await InitializeWebViewAsync();
        FormClosing += OnClosing;
    }

    private async Task InitializeWebViewAsync()
    {
        try
        {
            _environment = await CoreWebView2Environment.CreateAsync(
                browserExecutableFolder: null,
                userDataFolder: Config.WebViewDataDir);
            await _webView.EnsureCoreWebView2Async(_environment);
        }
        catch (Exception ex) when (ex is WebView2RuntimeNotFoundException or InvalidOperationException or System.Runtime.InteropServices.COMException)
        {
            _services.ShellLog.WriteLine($"[shell] webview2 init failed: {ex.Message}");
            MessageBox.Show(this,
                "STAF Desktop needs the Microsoft WebView2 Runtime (installed with Microsoft Edge on Windows 10/11).\n\n" +
                $"Details: {ex.Message}",
                "STAF Desktop", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
            return;
        }

        var core = _webView.CoreWebView2;
        core.Settings.AreDevToolsEnabled = Config.IsDevMode;
        core.Settings.IsStatusBarEnabled = false;
        core.SetVirtualHostNameToFolderMapping(
            "launcher.staf", LauncherAssetsDir, CoreWebView2HostResourceAccessKind.Allow);

        core.WebMessageReceived += OnWebMessage;
        core.NewWindowRequested += (_, e) => e.Handled = true;
        core.NavigationStarting += (_, e) =>
        {
            // The launcher page never leaves its own origin.
            if (!e.Uri.StartsWith(LauncherOrigin, StringComparison.OrdinalIgnoreCase))
            {
                e.Cancel = true;
            }
        };

        core.Navigate(LauncherOrigin + "index.html");
    }

    // ── Launcher page → shell ──────────────────────────────────────────────

    private async void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        string json;
        try
        {
            json = e.TryGetWebMessageAsString();
        }
        catch (ArgumentException)
        {
            return;
        }

        var command = LauncherProtocol.ParseCommand(json);
        if (command is null)
        {
            _services.ShellLog.WriteLine($"[shell] launcher sent unparseable message: {json}");
            return;
        }

        switch (command.Type)
        {
            case "ready":
                await InitializeAppsAsync();
                break;

            case "setupRetry":
                await RunFirstRunSetupAsync();
                break;

            case "installFromFile":
                await InstallFromFileAsync();
                break;

            case "applyUpdate":
                await ApplyPendingUpdateAsync();
                break;

            case "launch" when command.AppId is { } launchId:
                await LaunchOrFocusAsync(launchId);
                break;

            case "stop" when command.AppId is { } stopId && _supervisor is { } supStop:
                CloseAppWindow(stopId);
                await supStop.StopAsync(stopId);
                break;

            case "viewLogs" when command.AppId is { } logId && _supervisor is { } supLog:
                OpenInShell("notepad.exe", supLog.GetLogPath(logId));
                break;

            case "openWeb" when command.AppId is { } webId && _supervisor is { } supWeb:
                var webUrl = supWeb.Apps.FirstOrDefault(a => a.Id.Equals(webId, StringComparison.OrdinalIgnoreCase))?.WebUrl;
                if (!string.IsNullOrEmpty(webUrl))
                {
                    OpenExternal(webUrl);
                }
                break;

            case "openWebsite":
                OpenExternal(SiteHomeUrl);
                break;

            case "openLogsFolder":
                OpenInShell("explorer.exe", Config.LogsDir);
                break;
        }
    }

    // ── Startup: resolve payload → build supervisor, or run first-run setup ─

    private async Task InitializeAppsAsync()
    {
        if (_supervisor is not null)
        {
            PostSnapshot();
            return;
        }

        try
        {
            var payload = _services.Locator.Resolve();
            var manifest = DesktopManifest.Load(payload.ManifestFile);
            _supervisor = _services.SupervisorFactory(manifest.Apps);
            _supervisor.StateChanged += OnAppStateChanged;
            Post(new { type = "setupDone" });
            PostSnapshot();
            _services.ShellLog.WriteLine($"[shell] apps ready ({manifest.Apps.Count} apps, payload manifest {manifest.Version})");

            _ = BackgroundUpdateCheckAsync();
        }
        catch (ShellException ex) when (_services.PayloadManager is not null)
        {
            _services.ShellLog.WriteLine($"[shell] payload not ready ({ex.Message}) — starting first-run setup");
            await RunFirstRunSetupAsync();
        }
        catch (ShellException ex)
        {
            // Dev mode with a broken venv — nothing to download; explain instead.
            Post(new { type = "setupError", message = ex.Message, canRetry = false });
        }
    }

    private async Task RunFirstRunSetupAsync()
    {
        if (_services.PayloadManager is not { } manager || _services.ManifestUrl is not { } manifestUrl || _payloadBusy)
        {
            return;
        }
        _payloadBusy = true;
        manager.Progress += OnPayloadProgress;
        try
        {
            Post(new { type = "setup", message = "Checking what needs to be installed…", percent = -1, detail = "" });
            using var source = _services.SourceFactory();
            var result = await Task.Run(() => manager.CheckAsync(source, manifestUrl, CancellationToken.None));
            switch (result)
            {
                case CheckResult.ShellTooOld tooOld:
                    Post(new
                    {
                        type = "setupError",
                        message = $"This version of STAF Desktop is too old for the current runtime (needs {tooOld.RequiredShellVersion}). Please install the latest STAF Desktop from the STAF website.",
                        canRetry = false,
                    });
                    break;

                case CheckResult.UpToDate:
                    // Pointer said installed but resolve failed earlier → something is missing on disk.
                    Post(new
                    {
                        type = "setupError",
                        message = "The installed runtime looks damaged. Use 'Install from file…' with an offline bundle, or contact support.",
                        canRetry = true,
                    });
                    break;

                case CheckResult.UpdateAvailable update:
                    await Task.Run(() => manager.ApplyAsync(source, update.Manifest, update.Plan, CancellationToken.None));
                    await InitializeAppsAsync();
                    break;
            }
        }
        catch (Exception ex) when (ex is ShellException or HttpRequestException or IOException or TaskCanceledException)
        {
            _services.ShellLog.WriteLine($"[shell] first-run setup failed: {ex.Message}");
            Post(new
            {
                type = "setupError",
                message = $"Could not download the STAF runtime: {ex.Message}",
                canRetry = true,
            });
        }
        finally
        {
            manager.Progress -= OnPayloadProgress;
            _payloadBusy = false;
        }
    }

    private async Task InstallFromFileAsync()
    {
        if (_services.PayloadManager is not { } manager || _payloadBusy)
        {
            return;
        }
        using var dialog = new FolderBrowserDialog
        {
            Description = "Select the folder containing the STAF offline bundle (latest-desktop.json + zip files)",
            UseDescriptionForTitle = true,
        };
        if (dialog.ShowDialog(this) != DialogResult.OK)
        {
            return;
        }

        _payloadBusy = true;
        manager.Progress += OnPayloadProgress;
        try
        {
            await Task.Run(() => manager.InstallFromDirectoryAsync(dialog.SelectedPath, CancellationToken.None));
            await InitializeAppsAsync();
        }
        catch (ShellException ex)
        {
            Post(new { type = "setupError", message = ex.Message, canRetry = true });
        }
        finally
        {
            manager.Progress -= OnPayloadProgress;
            _payloadBusy = false;
        }
    }

    // ── Routine update check (shell already usable) ────────────────────────

    private async Task BackgroundUpdateCheckAsync()
    {
        if (_services.PayloadManager is not { } manager || _services.ManifestUrl is not { } manifestUrl || _payloadBusy)
        {
            return;
        }
        try
        {
            using var source = _services.SourceFactory();
            var result = await Task.Run(() => manager.CheckAsync(source, manifestUrl, CancellationToken.None));
            if (result is CheckResult.UpdateAvailable update)
            {
                _pendingUpdate = (update.Manifest, update.Plan);
                var mb = Math.Max(1, update.Plan.DownloadBytes / 1_000_000);
                Post(new { type = "updateAvailable", message = $"Update available ({mb} MB)" });
            }
        }
        catch (Exception ex) when (ex is ShellException or HttpRequestException or IOException or TaskCanceledException)
        {
            // Offline or blocked — routine checks fail silently; the footer just doesn't show a chip.
            _services.ShellLog.WriteLine($"[shell] update check failed quietly: {ex.Message}");
        }
    }

    private async Task ApplyPendingUpdateAsync()
    {
        if (_services.PayloadManager is not { } manager || _pendingUpdate is not { } pending || _payloadBusy)
        {
            return;
        }
        _payloadBusy = true;
        try
        {
            using var source = _services.SourceFactory();
            var progressHandler = new Action<PayloadProgress>(p => RunOnUi(() =>
                Post(new { type = "updateProgress", message = p.Message, percent = PercentOf(p) })));
            manager.Progress += progressHandler;
            try
            {
                await Task.Run(() => manager.ApplyAsync(source, pending.Manifest, pending.Plan, CancellationToken.None));
            }
            finally
            {
                manager.Progress -= progressHandler;
            }
            _pendingUpdate = null;
            Post(new
            {
                type = "updateDone",
                message = "Update installed — apps use it the next time they start.",
            });
        }
        catch (ShellException ex)
        {
            Post(new { type = "updateError", message = ex.Message });
        }
        finally
        {
            _payloadBusy = false;
        }
    }

    private void OnPayloadProgress(PayloadProgress progress) => RunOnUi(() =>
        Post(new
        {
            type = "setup",
            message = progress.Message,
            percent = PercentOf(progress),
            detail = progress.Component is null ? "" : $"{progress.Component} · {progress.BytesDone / 1_000_000} / {Math.Max(1, progress.BytesTotal / 1_000_000)} MB",
        }));

    private static int PercentOf(PayloadProgress p) =>
        p.BytesTotal > 0 ? (int)Math.Clamp(p.BytesDone * 100 / p.BytesTotal, 0, 100) : -1;

    // ── Supervisor → UI ────────────────────────────────────────────────────

    private async Task LaunchOrFocusAsync(string appId)
    {
        if (_supervisor is not { } supervisor)
        {
            return;
        }
        var state = supervisor.GetState(appId);
        if (state.Status == AppStatus.Running && state.Port is { } port)
        {
            OpenOrFocusAppWindow(appId, port);
            return;
        }
        lock (_pendingOpen)
        {
            _pendingOpen.Add(appId);
        }
        await supervisor.StartAsync(appId);
    }

    private void OnAppStateChanged(string appId, AppRuntimeState state)
    {
        if (IsDisposed || _shuttingDown)
        {
            return;
        }
        RunOnUi(() =>
        {
            PostSnapshot();
            if (state.Status == AppStatus.Running && state.Port is { } port)
            {
                bool shouldOpen;
                lock (_pendingOpen)
                {
                    shouldOpen = _pendingOpen.Remove(appId);
                }
                if (shouldOpen)
                {
                    OpenOrFocusAppWindow(appId, port);
                }
            }
        });
    }

    private void PostSnapshot()
    {
        if (_supervisor is not { } supervisor)
        {
            return;
        }
        var shell = new LauncherProtocol.ShellInfo(
            Version: _services.ShellVersion.ToString(3),
            Mode: Config.IsDevMode && _services.PayloadManager is null ? "dev" : "installed",
            DataRoot: Config.DataRoot);
        Post(LauncherProtocol.BuildSnapshotJson(shell, supervisor.Apps, supervisor.GetState), raw: true);
    }

    private void Post(object message, bool raw = false)
    {
        if (_webView.CoreWebView2 is not { } core)
        {
            return;
        }
        var json = raw && message is string s ? s : JsonSerializer.Serialize(message, DesktopJson.Options);
        core.PostWebMessageAsJson(json);
    }

    private void RunOnUi(Action action)
    {
        if (IsDisposed)
        {
            return;
        }
        try
        {
            if (InvokeRequired)
            {
                BeginInvoke(action);
            }
            else
            {
                action();
            }
        }
        catch (InvalidOperationException)
        {
            // Window handle torn down during shutdown.
        }
    }

    // ── App windows ────────────────────────────────────────────────────────

    private void OpenOrFocusAppWindow(string appId, int port)
    {
        if (_supervisor is not { } supervisor)
        {
            return;
        }
        if (_appWindows.TryGetValue(appId, out var existing) && !existing.IsDisposed)
        {
            existing.Activate();
            existing.BringToFront();
            return;
        }

        var descriptor = supervisor.Apps.First(a => a.Id.Equals(appId, StringComparison.OrdinalIgnoreCase));
        var window = new AppWindowForm(this, descriptor, port, _environment!);
        _appWindows[appId] = window;
        window.Show();
    }

    private void CloseAppWindow(string appId)
    {
        if (_appWindows.TryGetValue(appId, out var window) && !window.IsDisposed)
        {
            window.SuppressStopOnClose = true;
            window.Close();
        }
        _appWindows.Remove(appId);
    }

    // ── IShellHub (called from app windows, any thread) ────────────────────

    public IReadOnlyDictionary<int, string> GetPortMap()
    {
        var map = new Dictionary<int, string>();
        if (_supervisor is not { } supervisor)
        {
            return map;
        }
        foreach (var app in supervisor.Apps)
        {
            if (supervisor.GetState(app.Id) is { Status: AppStatus.Running, Port: { } port })
            {
                map[port] = app.Id;
            }
        }
        return map;
    }

    public void RequestOpenApp(string appId)
    {
        if (_supervisor is not { } supervisor
            || !supervisor.Apps.Any(a => a.Id.Equals(appId, StringComparison.OrdinalIgnoreCase)))
        {
            return;
        }
        RunOnUi(async () => await LaunchOrFocusAsync(appId));
    }

    public void FocusLauncher()
    {
        RunOnUi(() =>
        {
            if (WindowState == FormWindowState.Minimized)
            {
                WindowState = FormWindowState.Normal;
            }
            Activate();
            BringToFront();
        });
    }

    public void OpenExternal(string url)
    {
        if (url.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
            || url.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
        {
            OpenInShell(url, arguments: null);
        }
    }

    public void NotifyAppWindowClosed(string appId, bool stopServer)
    {
        RunOnUi(async () =>
        {
            _appWindows.Remove(appId);
            if (stopServer && !_shuttingDown && _supervisor is { } supervisor)
            {
                await supervisor.StopAsync(appId);
            }
        });
    }

    // ── Shutdown ───────────────────────────────────────────────────────────

    private async void OnClosing(object? sender, FormClosingEventArgs e)
    {
        if (_shutdownComplete)
        {
            return;
        }
        e.Cancel = true;
        _shuttingDown = true;
        Enabled = false;
        Text = "STAF Desktop — stopping apps…";

        foreach (var window in _appWindows.Values.ToList())
        {
            if (!window.IsDisposed)
            {
                window.SuppressStopOnClose = true;
                window.Close();
            }
        }
        _appWindows.Clear();

        try
        {
            if (_supervisor is { } supervisor)
            {
                await supervisor.StopAllAsync();
            }
        }
        finally
        {
            _shutdownComplete = true;
            Close();
        }
    }

    private void OpenInShell(string fileName, string? arguments)
    {
        try
        {
            var psi = arguments is null
                ? new ProcessStartInfo(fileName) { UseShellExecute = true }
                : new ProcessStartInfo(fileName, arguments) { UseShellExecute = true };
            Process.Start(psi);
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException or FileNotFoundException)
        {
            _services.ShellLog.WriteLine($"[shell] failed to open '{fileName}': {ex.Message}");
        }
    }
}
