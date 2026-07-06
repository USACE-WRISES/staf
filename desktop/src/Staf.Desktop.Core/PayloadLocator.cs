namespace Staf.Desktop.Core;

/// <summary>Where the Python runtime, the apps tree, and the app manifest currently live.</summary>
public sealed record PayloadPaths(string PythonExe, string AppsRoot, string ManifestFile);

/// <summary>
/// Resolves the active payload. Dev mode resolves against the repo checkout; installed mode
/// (M3) will resolve against %LOCALAPPDATA%\STAF\payloads\current.json.
/// </summary>
public interface IPayloadLocator
{
    PayloadPaths Resolve();
}

/// <summary>Runs the apps from the repo's shared .venv — the developer loop, no payload required.</summary>
public sealed class DevPayloadLocator(ShellConfig config) : IPayloadLocator
{
    public PayloadPaths Resolve()
    {
        var repo = config.DevRepoRoot
            ?? throw new ShellException("Dev mode is not active (no repo root found) and no payload is installed yet.");

        var python = Path.Combine(repo, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python))
        {
            throw new ShellException(
                $"Dev python not found at {python}. Create the shared venv first: py -3.12 -m venv .venv && .venv\\Scripts\\pip install -r requirements-dev.txt");
        }

        return new PayloadPaths(
            PythonExe: python,
            AppsRoot: Path.Combine(repo, "apps"),
            ManifestFile: Path.Combine(repo, "desktop", "dev", "dev-manifest.json"));
    }
}
