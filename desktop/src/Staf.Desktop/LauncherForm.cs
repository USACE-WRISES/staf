using System.Diagnostics;
using Staf.Desktop.Core;
using Staf.Desktop.Core.Processes;

namespace Staf.Desktop;

/// <summary>
/// M1 placeholder launcher: one row per app with start/stop/open controls. Replaced in M2 by the
/// WebView2 card-grid launcher; the supervisor wiring below is the part that carries forward.
/// </summary>
internal sealed class LauncherForm : Form
{
    private sealed record Row(Label Status, Button Start, Button Stop, Button Open);

    private readonly ShellConfig _config;
    private readonly AppSupervisor _supervisor;
    private readonly Dictionary<string, Row> _rows = new(StringComparer.OrdinalIgnoreCase);
    private bool _shutdownComplete;

    public LauncherForm(ShellConfig config, AppSupervisor supervisor)
    {
        _config = config;
        _supervisor = supervisor;

        Text = "STAF Desktop (dev preview)";
        MinimumSize = new Size(760, 320);
        StartPosition = FormStartPosition.CenterScreen;

        var layout = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            Padding = new Padding(12),
            ColumnCount = 5,
            RowCount = supervisor.Apps.Count + 2,
            AutoSize = true,
        };
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 32));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 34));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 12));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 12));
        layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 10));

        var rowIndex = 0;
        foreach (var app in supervisor.Apps)
        {
            var name = new Label
            {
                Text = $"{app.Name} — {app.Tier}",
                AutoSize = true,
                Font = new Font(Font, FontStyle.Bold),
                Anchor = AnchorStyles.Left,
            };
            var status = new Label { Text = "stopped", AutoSize = true, Anchor = AnchorStyles.Left };
            var start = new Button { Text = "Start", Anchor = AnchorStyles.Left };
            var stop = new Button { Text = "Stop", Enabled = false, Anchor = AnchorStyles.Left };
            var open = new Button { Text = "Open", Enabled = false, Anchor = AnchorStyles.Left };

            var id = app.Id;
            start.Click += async (_, _) => await _supervisor.StartAsync(id);
            stop.Click += async (_, _) => await _supervisor.StopAsync(id);
            open.Click += (_, _) =>
            {
                if (_supervisor.GetState(id).Port is { } port)
                {
                    Process.Start(new ProcessStartInfo($"http://127.0.0.1:{port}/") { UseShellExecute = true });
                }
            };

            layout.Controls.Add(name, 0, rowIndex);
            layout.Controls.Add(status, 1, rowIndex);
            layout.Controls.Add(start, 2, rowIndex);
            layout.Controls.Add(stop, 3, rowIndex);
            layout.Controls.Add(open, 4, rowIndex);
            _rows[id] = new Row(status, start, stop, open);
            rowIndex++;
        }

        var footer = new Label
        {
            Text = $"mode: {(config.IsDevMode ? "dev — " + config.DevRepoRoot : "installed")}   |   data: {config.DataRoot}",
            AutoSize = true,
            ForeColor = SystemColors.GrayText,
            Anchor = AnchorStyles.Left,
        };
        layout.Controls.Add(footer, 0, rowIndex + 1);
        layout.SetColumnSpan(footer, 5);

        Controls.Add(layout);

        _supervisor.StateChanged += OnStateChanged;
        FormClosing += OnClosing;
    }

    private void OnStateChanged(string appId, AppRuntimeState state)
    {
        if (IsDisposed)
        {
            return;
        }
        try
        {
            BeginInvoke(() =>
            {
                if (!_rows.TryGetValue(appId, out var row))
                {
                    return;
                }
                row.Status.Text = state.Status switch
                {
                    AppStatus.Running => $"running on :{state.Port}",
                    AppStatus.Crashed => $"crashed — {Truncate(state.Detail, 60)} (see logs)",
                    _ => $"{state.Status.ToString().ToLowerInvariant()}{(state.Detail is null ? "" : " — " + Truncate(state.Detail, 60))}",
                };
                row.Start.Enabled = state.Status is AppStatus.Stopped or AppStatus.Crashed;
                row.Stop.Enabled = state.Status is AppStatus.Starting or AppStatus.Running;
                row.Open.Enabled = state.Status is AppStatus.Running;
            });
        }
        catch (InvalidOperationException)
        {
            // Form handle torn down mid-update during shutdown — nothing to render.
        }
    }

    private async void OnClosing(object? sender, FormClosingEventArgs e)
    {
        if (_shutdownComplete)
        {
            return;
        }
        e.Cancel = true;
        Enabled = false;
        Text = "STAF Desktop — stopping apps…";
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

    private static string Truncate(string? value, int max) =>
        value is null ? "" : value.Length <= max ? value : value[..max] + "…";
}
