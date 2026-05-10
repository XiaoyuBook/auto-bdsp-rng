public sealed class FakeEasyConSession : IEasyConSession, IPersistentSessionDiagnostics
{
    private readonly Action<string> _log;
    private string? _connectedPort;

    public FakeEasyConSession(Action<string> log)
    {
        _log = log;
    }

    public string? ConnectedPort => _connectedPort;
    public bool IsConnected => _connectedPort is not null;
    public int ConnectCount { get; private set; }
    public int DisconnectCount { get; private set; }
    public int RunCount { get; private set; }
    public int ActionCount { get; private set; }

    public string Version()
    {
        return "bridge-mock";
    }

    public IReadOnlyList<string> ListPorts()
    {
        return ["mock"];
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

        _connectedPort = port;
        ConnectCount++;
        _log($"connected to {port}");
    }

    public void Disconnect()
    {
        if (IsConnected)
            DisconnectCount++;
        _connectedPort = null;
        _log("disconnected");
    }

    public ScriptRunResult RunScript(string scriptText, string name, bool highResolution, CancellationToken token)
    {
        EnsureConnected();
        RunCount++;
        _log($"running {name} highResolution={highResolution}");
        try
        {
            if (scriptText.Contains("WAIT_LONG", StringComparison.OrdinalIgnoreCase))
                Task.Delay(TimeSpan.FromSeconds(30), token).GetAwaiter().GetResult();
            token.ThrowIfCancellationRequested();
            _log($"completed {name}");
            return new ScriptRunResult(0, $"ran {name}\n", string.Empty);
        }
        catch (OperationCanceledException)
        {
            _log($"cancelled {name}");
            return new ScriptRunResult(130, string.Empty, "script cancelled\n");
        }
    }

    public void Press(string button, int durationMs)
    {
        EnsureConnected();
        ActionCount++;
        _log($"press {button} {durationMs}");
    }

    public void Stick(string side, string direction, int? durationMs)
    {
        EnsureConnected();
        ActionCount++;
        _log(durationMs is null ? $"stick {side} {direction}" : $"stick {side} {direction} {durationMs.Value}");
    }

    public void KeyDown(string button)
    {
        EnsureConnected();
        ActionCount++;
        _log($"key down {button}");
    }

    public void KeyUp(string button)
    {
        EnsureConnected();
        ActionCount++;
        _log($"key up {button}");
    }

    public void StickDirection(string side, string direction, bool down)
    {
        EnsureConnected();
        ActionCount++;
        _log($"{side} {direction} {(down ? "down" : "up")}");
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
