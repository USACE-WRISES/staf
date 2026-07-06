using System.Diagnostics;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Staf.Desktop.Core;
using Staf.Desktop.Core.Logging;
using Staf.Desktop.Core.Manifest;
using Staf.Desktop.Core.Processes;

namespace Staf.Desktop;

/// <summary>
/// The main window: hosts the launcher page (card grid) in WebView2 and acts as the hub between
/// the launcher page, the app supervisor, and the per-app windows.
/// </summary>
internal sealed class LauncherForm : Form, IShellHub
{
    private static readonly string LauncherAssetsDir = Path.Combine(AppContext.BaseDirectory, "launcher");
    private const string LauncherOrigin = "https://launcher.staf/";
    private const string SiteHomeUrl = "https://usace-wrises.github.io/staf/";

    private readonly AppSupervisor _supervisor;
    private readonly ILineLog _shellLog;
    private readonly WebView2 _webView;
    private readonly Dictionary<string, AppWindowForm> _appWindows = new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> _pendingOpen = new(StringComparer.OrdinalIgnoreCase);
    private CoreWebView2Environment? _environment;
    private bool _shutdownComplete;
    private bool _shuttingDown;

    public ShellConfig Config { get; }

    public LauncherForm(ShellConfig config, AppSupervisor supervisor, ILineLog shellLog)
    {
        Config = config;
        _supervisor = supervisor;
        _shellLog = shellLog;

        Text = "STAF Desktop";
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(1060, 680);
        MinimumSize = new Size(780, 500);

        _webView = new WebView2 { Dock = DockStyle.Fill };
        Controls.Add(_webView);

        _supervisor.StateChanged += OnAppStateChanged;
        Load += async (_, _) => await InitializeAsync();
        FormClosing += OnClosing;
    }

    private async Task InitializeAsync()
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
            _shellLog.WriteLine($"[shell] webview2 init failed: {ex.Message}");
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
            _shellLog.WriteLine($"[shell] launcher sent unparseable message: {json}");
            return;
        }

        switch (command.Type)
        {
            case "ready":
                PostSnapshot();
                break;

            case "launch" when command.AppId is { } launchId:
                await LaunchOrFocusAsync(launchId);
                break;

            case "stop" when command.AppId is { } stopId:
                CloseAppWindow(stopId);
                await _supervisor.StopAsync(stopId);
                break;

            case "viewLogs" when command.AppId is { } logId:
                OpenInShell("notepad.exe", _supervisor.GetLogPath(logId));
                break;

            case "openWeb" when command.AppId is { } webId:
                var webUrl = _supervisor.Apps.FirstOrDefault(a => a.Id.Equals(webId, StringComparison.OrdinalIgnoreCase))?.WebUrl;
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

    private async Task LaunchOrFocusAsync(string appId)
    {
        var state = _supervisor.GetState(appId);
        if (state.Status == AppStatus.Running && state.Port is { } port)
        {
            OpenOrFocusAppWindow(appId, port);
            return;
        }
        lock (_pendingOpen)
        {
            _pendingOpen.Add(appId);
        }
        await _supervisor.StartAsync(appId);
    }

    // ── Supervisor → UI ────────────────────────────────────────────────────

    private void OnAppStateChanged(string appId, AppRuntimeState state)
    {
        if (IsDisposed || _shuttingDown)
        {
            return;
        }
        try
        {
            BeginInvoke(() =>
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
        catch (InvalidOperationException)
        {
            // Window handle already torn down during shutdown.
        }
    }

    private void PostSnapshot()
    {
        if (_webView.CoreWebView2 is not { } core)
        {
            return;
        }
        var shell = new LauncherProtocol.ShellInfo(
            Version: typeof(Program).Assembly.GetName().Version?.ToString(3) ?? "0.0.0",
            Mode: Config.IsDevMode ? "dev" : "installed",
            DataRoot: Config.DataRoot);
        var json = LauncherProtocol.BuildSnapshotJson(shell, _supervisor.Apps, _supervisor.GetState);
        core.PostWebMessageAsJson(json);
    }

    // ── App windows ────────────────────────────────────────────────────────

    private void OpenOrFocusAppWindow(string appId, int port)
    {
        if (_appWindows.TryGetValue(appId, out var existing) && !existing.IsDisposed)
        {
            existing.Activate();
            existing.BringToFront();
            return;
        }

        var descriptor = _supervisor.Apps.First(a => a.Id.Equals(appId, StringComparison.OrdinalIgnoreCase));
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
        foreach (var app in _supervisor.Apps)
        {
            if (_supervisor.GetState(app.Id) is { Status: AppStatus.Running, Port: { } port })
            {
                map[port] = app.Id;
            }
        }
        return map;
    }

    public void RequestOpenApp(string appId)
    {
        if (!_supervisor.Apps.Any(a => a.Id.Equals(appId, StringComparison.OrdinalIgnoreCase)))
        {
            return;
        }
        BeginInvoke(async () => await LaunchOrFocusAsync(appId));
    }

    public void FocusLauncher()
    {
        BeginInvoke(() =>
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
        BeginInvoke(async () =>
        {
            _appWindows.Remove(appId);
            if (stopServer && !_shuttingDown)
            {
                await _supervisor.StopAsync(appId);
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
            await _supervisor.StopAllAsync();
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
            _shellLog.WriteLine($"[shell] failed to open '{fileName}': {ex.Message}");
        }
    }
}
