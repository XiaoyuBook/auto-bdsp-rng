using System.Drawing;
using System.Text;
using EasyScript;

public sealed class BridgeOutputAdapter : IOutputAdapter
{
    private readonly StringBuilder _stdout = new();
    private readonly StringBuilder _stderr = new();
    private readonly Action<string, string> _log;

    public BridgeOutputAdapter(Action<string, string> log)
    {
        _log = log;
    }

    public string Stdout => _stdout.ToString();
    public string Stderr => _stderr.ToString();

    public void Print(string message, bool newline = true)
    {
        Append("stdout", message, newline);
    }

    public void Info(string message, bool timestamp = false)
    {
        Append("info", message, true);
    }

    public void Log(string message, bool timestamp = false)
    {
        Append("stdout", message, true);
    }

    public void Warn(string message, bool timestamp = false)
    {
        Append("warn", message, true);
    }

    public void Error(string message, bool timestamp = false)
    {
        Append("stderr", message, true);
    }

    public void Alert(string message)
    {
        Append("info", message, true);
    }

    private void Append(string level, string message, bool newline)
    {
        if (level == "stderr")
        {
            _stderr.Append(message);
            if (newline)
                _stderr.AppendLine();
        }
        else
        {
            _stdout.Append(message);
            if (newline)
                _stdout.AppendLine();
        }
        _log(level, message);
    }
}
