using StreamCurves.Desktop.Core;
using StreamCurves.Desktop.Core.Logging;
using StreamCurves.Desktop.Core.Manifest;
using StreamCurves.Desktop.Core.Payload;
using StreamCurves.Desktop.Core.Processes;

namespace StreamCurves.Desktop;

/// <summary>Everything Program wires up for the launcher window.</summary>
internal sealed record ShellServices
{
    public required ShellConfig Config { get; init; }
    public required ILineLog ShellLog { get; init; }
    public required IPayloadLocator Locator { get; init; }
    public required Func<IReadOnlyList<AppDescriptor>, AppSupervisor> SupervisorFactory { get; init; }

    /// <summary>Null in dev mode — apps run straight from the repo .venv, no payload involved.</summary>
    public PayloadManager? PayloadManager { get; init; }

    /// <summary>Where CheckAsync fetches latest-desktop.json (null in dev mode).</summary>
    public string? ManifestUrl { get; init; }

    public required Func<IPayloadSource> SourceFactory { get; init; }

    public Version ShellVersion { get; init; } = typeof(ShellServices).Assembly.GetName().Version ?? new Version(0, 0, 0);
}
