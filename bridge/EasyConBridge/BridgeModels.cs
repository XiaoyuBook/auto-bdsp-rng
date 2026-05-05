using System.Text.Json;
using System.Text.Json.Serialization;

public sealed record BridgeRequest(
    [property: JsonPropertyName("id")] int Id,
    [property: JsonPropertyName("command")] string Command,
    [property: JsonPropertyName("payload")] JsonElement Payload);

public sealed record ScriptRunResult(int ExitCode, string Stdout, string Stderr);

public static class BridgeJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };
}
