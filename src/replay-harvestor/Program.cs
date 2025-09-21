using System.Text.Json;
using System.Diagnostics;

record Heartbeat(string service, double ts, object info);

static class HeartbeatWriter {
    private static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.General) { WriteIndented = true };
    public static void Write(string path, Heartbeat hb) {
        var tmp = path + ".tmp";
        File.WriteAllText(tmp, JsonSerializer.Serialize(hb, Options));
        File.Move(tmp, path, true);
    }
}

class ReplayHarvester {
    private readonly string _sourceRoot;
    private readonly string _queueDir;
    private readonly int _queueCap;
    private readonly HashSet<string> _seen = new();
    public int Copied {get; private set;} = 0;
    public int Scanned {get; private set;} = 0;

    public ReplayHarvester(string sourceRoot, string queueDir, int queueCap) {
        _sourceRoot = sourceRoot; _queueDir = queueDir; _queueCap = queueCap;
        Directory.CreateDirectory(_queueDir);
    }

    public void ScanOnce() {
        if(!Directory.Exists(_sourceRoot)) return;
        var files = Directory.EnumerateFiles(_sourceRoot, "*.StormReplay", SearchOption.AllDirectories);
        foreach(var f in files) {
            Scanned++;
            if(_seen.Contains(f)) continue;
            _seen.Add(f);
            if(Directory.GetFiles(_queueDir, "*.StormReplay").Length >= _queueCap) break;
            var dest = Path.Combine(_queueDir, Path.GetFileName(f));
            if(File.Exists(dest)) continue;
            try {
                File.Copy(f, dest);
                Copied++;
            } catch { /* ignore */ }
        }
    }
}

class Program {
    static void Main() {

        string source = Environment.GetEnvironmentVariable("HARVEST_SOURCE") ?? @"C:\\Users\\patri\\OneDrive\\Documents\\Heroes of the Storm\\Accounts";
        string queueDir = "replays/queue";
        Directory.CreateDirectory(queueDir);
        int cap = 10;
        var harvester = new ReplayHarvester(source, queueDir, cap);
        var hbPath = "sessions/current/state/heartbeat_harvester.json";
        Directory.CreateDirectory("sessions/current/state");
        var sw = Stopwatch.StartNew();
        int loops = 0;

        while (true)
        {
            loops++;
            harvester.ScanOnce();
            if (loops % 10 == 0)
            {
                var hb = new Heartbeat("harvester", DateTimeOffset.UtcNow.ToUnixTimeSeconds(), new
                {
                    loops,
                    up_seconds = (int)sw.Elapsed.TotalSeconds,
                    scanned = harvester.Scanned,
                    copied = harvester.Copied
                });
                HeartbeatWriter.Write(hbPath, hb);
            }
            Thread.Sleep(1000);
        }
    }
}
