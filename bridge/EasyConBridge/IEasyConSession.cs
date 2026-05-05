public interface IEasyConSession : IDisposable
{
    string? ConnectedPort { get; }
    bool IsConnected { get; }
    string Version();
    IReadOnlyList<string> ListPorts();
    void Connect(string port);
    void Disconnect();
    ScriptRunResult RunScript(string scriptText, string name, CancellationToken token);
    void Press(string button, int durationMs);
    void Stick(string side, string direction, int? durationMs);
}
