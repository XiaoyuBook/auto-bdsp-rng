using System.Text;

Console.InputEncoding = Encoding.UTF8;
Console.OutputEncoding = Encoding.UTF8;

var session = new EasyConSession(message =>
{
    JsonLineBridgeServer.WriteLog("info", message);
});
var server = new JsonLineBridgeServer(session, Console.In, Console.Out);
await server.RunAsync();
