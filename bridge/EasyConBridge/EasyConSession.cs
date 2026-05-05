using EasyCon.Core;
using EasyDevice;

public sealed class EasyConSession : IEasyConSession
{
    private readonly Action<string> _log;
    private NintendoSwitch? _switch;
    private string? _connectedPort;

    public EasyConSession(Action<string> log)
    {
        _log = log;
    }

    public string? ConnectedPort => _connectedPort;
    public bool IsConnected => _switch?.IsConnected() == true;

    public string Version()
    {
        return typeof(ECCore).Assembly.GetName().Version?.ToString() ?? "unknown";
    }

    public IReadOnlyList<string> ListPorts()
    {
        return ECCore.GetDeviceNames();
    }

    public void Connect(string port)
    {
        if (IsConnected && string.Equals(_connectedPort, port, StringComparison.OrdinalIgnoreCase))
        {
            _log($"already connected to {port}");
            return;
        }
        if (IsConnected)
            throw new InvalidOperationException("disconnect before connecting another port");

        var nextSwitch = new NintendoSwitch();
        var result = nextSwitch.TryConnect(port);
        if (result != NintendoSwitch.ConnectResult.Success)
            throw new InvalidOperationException($"connect failed: {result}");

        _switch = nextSwitch;
        _connectedPort = port;
        _log($"connected to {port}");
    }

    public void Disconnect()
    {
        _switch?.Disconnect();
        _switch = null;
        _connectedPort = null;
        _log("disconnected");
    }

    public ScriptRunResult RunScript(string scriptText, string name, CancellationToken token)
    {
        EnsureConnected();
        var scripter = new Scripter();
        var output = new BridgeOutputAdapter(JsonLineBridgeServer.WriteLog);
        var diagnostics = scripter.Parse(scriptText, fileName: null!, externalGetters: []);
        if (diagnostics.Any(d => d.IsError))
        {
            foreach (var diagnostic in diagnostics)
            {
                output.Error($"{diagnostic.Message}: line {diagnostic.Location.StartLine + 1}");
            }
            return new ScriptRunResult(1, output.Stdout, output.Stderr);
        }

        var pad = new GamePadAdapter(_switch!);
        try
        {
            scripter.Run(output, pad, token);
            output.Info("script completed");
            return new ScriptRunResult(0, output.Stdout, output.Stderr);
        }
        catch (OperationCanceledException)
        {
            output.Warn("script cancelled");
            return new ScriptRunResult(130, output.Stdout, output.Stderr);
        }
        catch (Exception ex)
        {
            output.Error(ex.Message);
            return new ScriptRunResult(1, output.Stdout, output.Stderr);
        }
    }

    public void Press(string button, int durationMs)
    {
        RunScript($"{button} {durationMs}", $"press-{button}", CancellationToken.None);
    }

    public void Stick(string side, string direction, int? durationMs)
    {
        var script = durationMs is null ? $"{side} {direction}" : $"{side} {direction} {durationMs.Value}";
        RunScript(script, $"stick-{side}", CancellationToken.None);
    }

    public void Dispose()
    {
        Disconnect();
    }

    private void EnsureConnected()
    {
        if (!IsConnected)
            throw new InvalidOperationException("bridge is not connected");
    }
}
