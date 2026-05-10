using System.Text.Json;

public sealed class JsonLineBridgeServer
{
    private static readonly object StdoutLock = new();
    private readonly IEasyConSession _session;
    private readonly TextReader _input;
    private readonly TextWriter _output;
    private readonly object _runLock = new();
    private CancellationTokenSource? _currentRunCts;
    private bool _isRunning;

    public JsonLineBridgeServer(IEasyConSession session, TextReader input, TextWriter output)
    {
        _session = session;
        _input = input;
        _output = output;
    }

    public static void WriteLog(string level, string message)
    {
        lock (StdoutLock)
        {
            Console.Out.WriteLine(JsonSerializer.Serialize(new
            {
                type = "log",
                level,
                message,
            }, BridgeJson.Options));
            Console.Out.Flush();
        }
    }

    public async Task RunAsync()
    {
        string? line;
        while ((line = await _input.ReadLineAsync()) is not null)
        {
            if (string.IsNullOrWhiteSpace(line))
                continue;
            line = line.TrimStart('\uFEFF');

            BridgeRequest request;
            try
            {
                request = JsonSerializer.Deserialize<BridgeRequest>(line, BridgeJson.Options)
                    ?? throw new InvalidOperationException("empty request");
            }
            catch (Exception ex)
            {
                await WriteResponseAsync(0, false, null, $"invalid request: {ex.Message}");
                continue;
            }

            try
            {
                if (request.Command == "run_script")
                {
                    StartRunScript(request);
                    continue;
                }

                var payload = HandleRequest(request.Command, request.Payload);
                await WriteResponseAsync(request.Id, true, payload);
            }
            catch (Exception ex)
            {
                await WriteResponseAsync(request.Id, false, null, ex.Message);
            }
        }
    }

    private object HandleRequest(string command, JsonElement payload)
    {
        switch (command)
        {
            case "version":
                return new { version = _session.Version() };
            case "list_ports":
                return new { ports = _session.ListPorts() };
            case "connect":
                _session.Connect(RequiredString(payload, "port"));
                return new { status = "connected", port = _session.ConnectedPort };
            case "disconnect":
                StopCurrentRun();
                _session.Disconnect();
                return new { status = "disconnected" };
            case "status":
                var statusPayload = new Dictionary<string, object?>
                {
                    ["status"] = _session.IsConnected ? (_isRunning ? "running" : "connected") : "disconnected",
                    ["port"] = _session.ConnectedPort,
                };
                if (_session is IPersistentSessionDiagnostics diagnostics)
                {
                    statusPayload["connect_count"] = diagnostics.ConnectCount;
                    statusPayload["disconnect_count"] = diagnostics.DisconnectCount;
                    statusPayload["run_count"] = diagnostics.RunCount;
                    statusPayload["action_count"] = diagnostics.ActionCount;
                }
                return statusPayload;
            case "stop":
            case "stop_current_script":
                StopCurrentRun();
                return new { status = _session.IsConnected ? "connected" : "disconnected" };
            case "press":
                _session.Press(RequiredString(payload, "button"), OptionalInt(payload, "duration_ms") ?? 100);
                return new { status = "connected" };
            case "stick":
                _session.Stick(
                    RequiredString(payload, "side"),
                    RequiredString(payload, "direction"),
                    OptionalInt(payload, "duration_ms"));
                return new { status = "connected" };
            case "key_down":
                _session.KeyDown(RequiredString(payload, "button"));
                return new { status = "connected" };
            case "key_up":
                _session.KeyUp(RequiredString(payload, "button"));
                return new { status = "connected" };
            case "stick_direction":
                _session.StickDirection(
                    RequiredString(payload, "side"),
                    RequiredString(payload, "direction"),
                    RequiredBool(payload, "down"));
                return new { status = "connected" };
            default:
                throw new InvalidOperationException($"unknown command: {command}");
        }
    }

    private void StartRunScript(BridgeRequest request)
    {
        lock (_runLock)
        {
            if (_isRunning)
                throw new InvalidOperationException("another script is already running");
            _isRunning = true;
            _currentRunCts = new CancellationTokenSource();
        }

        var scriptText = RequiredString(request.Payload, "script_text");
        var name = OptionalString(request.Payload, "name") ?? "script";
        var highResolution = OptionalBool(request.Payload, "high_resolution") ?? true;
        var token = _currentRunCts.Token;

        _ = Task.Run(async () =>
        {
            try
            {
                var result = _session.RunScript(scriptText, name, highResolution, token);
                await WriteResponseAsync(request.Id, true, new
                {
                    exit_code = result.ExitCode,
                    stdout = result.Stdout,
                    stderr = result.Stderr,
                });
            }
            catch (Exception ex)
            {
                await WriteResponseAsync(request.Id, false, null, ex.Message);
            }
            finally
            {
                lock (_runLock)
                {
                    _isRunning = false;
                    _currentRunCts?.Dispose();
                    _currentRunCts = null;
                }
            }
        });
    }

    private void StopCurrentRun()
    {
        lock (_runLock)
        {
            _currentRunCts?.Cancel();
        }
    }

    private async Task WriteResponseAsync(int id, bool ok, object? payload = null, string? error = null)
    {
        var response = JsonSerializer.Serialize(new
        {
            id,
            ok,
            payload,
            error,
        }, BridgeJson.Options);

        lock (StdoutLock)
        {
            _output.WriteLine(response);
            _output.Flush();
        }
        await Task.CompletedTask;
    }

    private static string RequiredString(JsonElement payload, string name)
    {
        var value = OptionalString(payload, name);
        if (string.IsNullOrWhiteSpace(value))
            throw new InvalidOperationException($"missing payload field: {name}");
        return value;
    }

    private static string? OptionalString(JsonElement payload, string name)
    {
        return payload.ValueKind == JsonValueKind.Object
            && payload.TryGetProperty(name, out var value)
            && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;
    }

    private static int? OptionalInt(JsonElement payload, string name)
    {
        return payload.ValueKind == JsonValueKind.Object
            && payload.TryGetProperty(name, out var value)
            && value.ValueKind == JsonValueKind.Number
            && value.TryGetInt32(out var parsed)
            ? parsed
            : null;
    }

    private static bool RequiredBool(JsonElement payload, string name)
    {
        var value = OptionalBool(payload, name);
        if (value is null)
            throw new InvalidOperationException($"missing payload field: {name}");
        return value.Value;
    }

    private static bool? OptionalBool(JsonElement payload, string name)
    {
        if (payload.ValueKind != JsonValueKind.Object)
            return null;
        if (!payload.TryGetProperty(name, out var value))
            return null;
        if (value.ValueKind == JsonValueKind.True)
            return true;
        if (value.ValueKind == JsonValueKind.False)
            return false;
        return null;
    }
}
