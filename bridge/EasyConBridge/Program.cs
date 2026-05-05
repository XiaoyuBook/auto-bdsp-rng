using System.Text;

Console.InputEncoding = Encoding.UTF8;
Console.OutputEncoding = Encoding.UTF8;

var useMockSession = args.Any(arg => string.Equals(arg, "--mock-session", StringComparison.OrdinalIgnoreCase))
    || string.Equals(Environment.GetEnvironmentVariable("EASYCON_BRIDGE_MOCK_SESSION"), "1", StringComparison.Ordinal);

IEasyConSession session = useMockSession ? new FakeEasyConSession(WriteSessionLog) : new EasyConSession(WriteSessionLog);

static void WriteSessionLog(string message)
{
    JsonLineBridgeServer.WriteLog("info", message);
}

var server = new JsonLineBridgeServer(session, Console.In, Console.Out);
await server.RunAsync();
