public interface IEasyConSession : IDisposable
{
    string? ConnectedPort { get; }
    bool IsConnected { get; }
    string Version();
    IReadOnlyList<string> ListPorts();
    void Connect(string port);
    void Disconnect();
    ScriptRunResult RunScript(string scriptText, string name, bool highResolution, CancellationToken token);
    void Press(string button, int durationMs);
    void Stick(string side, string direction, int? durationMs);
    void KeyDown(string button);
    void KeyUp(string button);
    void StickDirection(string side, string direction, bool down);
}
