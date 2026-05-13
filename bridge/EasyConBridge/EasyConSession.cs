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

    public ScriptRunResult RunScript(string scriptText, string name, bool highResolution, string? requestedAt, CancellationToken token)
    {
        EnsureConnected();
        _log($"run_script [{name}] start, highResolution={highResolution}");

        // ── 诊断：IPC 延迟 ──
        if (requestedAt is not null && DateTime.TryParse(requestedAt, out var pyRequested))
        {
            var ipcDelay = (DateTime.UtcNow - pyRequested.ToUniversalTime()).TotalMilliseconds;
            _log($"IPC delay: Python → Bridge {ipcDelay:F0}ms (~{(int)(ipcDelay * (1 + 0) / 1018)} 帧 @ npc=0)");
        }

        // 原版 EasyCon 只在 HasKeyAction 且 RemoteStop 成功时才继续，否则弹窗让用户手动停止。
        // Bridge 无法检测单片机是否正在运行烧录脚本，无条件 RemoteStop 会在无脚本时
        // 每次阻塞 200ms（SendSync 超时），导致脚本首键延迟和串口状态不一致。
        // 对齐原版行为：不主动 RemoteStop，由用户自行确保无烧录脚本在运行。

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

        // 与原版 EasyCon 一致：传入 highResolution 参数
        var pad = new GamePadAdapter(_switch, highResolution);
        try
        {
            var startedAt = DateTime.Now;
            var parseDuration = (startedAt - (requestedAt is not null && DateTime.TryParse(requestedAt, out var pyReq) ? pyReq.ToUniversalTime() : startedAt)).TotalMilliseconds;
            _log($"script [{name}] Scripter.Run start at {startedAt:HH:mm:ss.fff} (解析+准备耗时 {parseDuration:F0}ms)");

            scripter.Run(output, pad, token);

            var endedAt = DateTime.Now;
            var scriptMs = (endedAt - startedAt).TotalMilliseconds;
            _log($"script [{name}] completed at {endedAt:HH:mm:ss.fff}, script耗时={scriptMs:F0}ms (~{(int)(scriptMs / 1018)} 帧 @ npc=0)");
            output.Info($"script completed in {scriptMs:F0}ms");
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
        finally
        {
            // 脚本结束后释放所有按键/摇杆状态
            ReleaseAllControllerState();
        }
    }

    public void Press(string button, int durationMs)
    {
        RunScript($"{button} {durationMs}", $"press-{button}", highResolution: true, requestedAt: null, CancellationToken.None);
    }

    public void Stick(string side, string direction, int? durationMs)
    {
        var script = durationMs is null ? $"{side} {direction}" : $"{side} {direction} {durationMs.Value}";
        RunScript(script, $"stick-{side}", highResolution: true, requestedAt: null, CancellationToken.None);
    }

    public void KeyDown(string button)
    {
        EnsureConnected();
        _switch!.Down(ECKeyUtil.Button(ParseSwitchButton(button)));
        _log($"key down {button}");
    }

    public void KeyUp(string button)
    {
        EnsureConnected();
        _switch!.Up(ECKeyUtil.Button(ParseSwitchButton(button)));
        _log($"key up {button}");
    }

    public void StickDirection(string side, string direction, bool down)
    {
        EnsureConnected();
        var dkey = ParseDirectionKey(direction);
        if (side.Equals("left", StringComparison.OrdinalIgnoreCase) || side.Equals("LS", StringComparison.OrdinalIgnoreCase))
            _switch!.LeftDirection(dkey, down);
        else if (side.Equals("right", StringComparison.OrdinalIgnoreCase) || side.Equals("RS", StringComparison.OrdinalIgnoreCase))
            _switch!.RightDirection(dkey, down);
        else if (side.Equals("hat", StringComparison.OrdinalIgnoreCase) || side.Equals("dpad", StringComparison.OrdinalIgnoreCase))
            _switch!.HatDirection(dkey, down);
        else
            throw new InvalidOperationException($"unknown stick side: {side}");
        _log($"{side} {direction} {(down ? "down" : "up")}");
    }

    public void Dispose()
    {
        Disconnect();
    }

    private void ReleaseAllControllerState()
    {
        // 释放所有可能的按键，摇杆归中
        foreach (SwitchButton button in Enum.GetValues(typeof(SwitchButton)))
        {
            _switch!.Up(ECKeyUtil.Button(button));
        }
        _switch!.LeftDirection(DirectionKey.Up, false);
        _switch!.LeftDirection(DirectionKey.Down, false);
        _switch!.LeftDirection(DirectionKey.Left, false);
        _switch!.LeftDirection(DirectionKey.Right, false);
        _switch!.RightDirection(DirectionKey.Up, false);
        _switch!.RightDirection(DirectionKey.Down, false);
        _switch!.RightDirection(DirectionKey.Left, false);
        _switch!.RightDirection(DirectionKey.Right, false);
        _switch!.HatDirection(DirectionKey.Up, false);
        _switch!.HatDirection(DirectionKey.Down, false);
        _switch!.HatDirection(DirectionKey.Left, false);
        _switch!.HatDirection(DirectionKey.Right, false);
    }

    private void EnsureConnected()
    {
        if (!IsConnected)
            throw new InvalidOperationException("bridge is not connected");
    }

    private static SwitchButton ParseSwitchButton(string button)
    {
        if (Enum.TryParse<SwitchButton>(button, ignoreCase: true, out var parsed))
            return parsed;
        throw new InvalidOperationException($"unknown button: {button}");
    }

    private static DirectionKey ParseDirectionKey(string direction)
    {
        return direction.ToUpperInvariant() switch
        {
            "UP" or "TOP" => DirectionKey.Up,
            "DOWN" or "BOTTOM" => DirectionKey.Down,
            "LEFT" => DirectionKey.Left,
            "RIGHT" => DirectionKey.Right,
            _ => throw new InvalidOperationException($"unknown direction: {direction}"),
        };
    }
}
