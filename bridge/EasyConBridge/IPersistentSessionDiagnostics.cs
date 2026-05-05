public interface IPersistentSessionDiagnostics
{
    int ConnectCount { get; }
    int DisconnectCount { get; }
    int RunCount { get; }
    int ActionCount { get; }
}
