using System.Text.Json;
using System.Diagnostics;
using Nexus.Control;

record Heartbeat(string service, double ts, object info);

static class HeartbeatWriter
{
    private static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.General) { WriteIndented = true };
    public static void Write(string path, Heartbeat hb)
    {
        var tmp = path + ".tmp";
        var json = JsonSerializer.Serialize(hb, Options);
        File.WriteAllText(tmp, json);
        File.Move(tmp, path, true);
    }
}


class Program
{
    static void Main()
    {
        Directory.CreateDirectory("sessions/current/state");
        var controller = new CameraController();
        var hbPath = "sessions/current/state/heartbeat_control.json";
        var sw = Stopwatch.StartNew();
        int loop = 0;
        while (true)
        {
            loop++;
            controller.Tick();
            if (loop % 5 == 0)
            {
                var hb = new Heartbeat("control", DateTimeOffset.UtcNow.ToUnixTimeSeconds(), new
                {
                    loops = loop,
                        up_seconds = (int)sw.Elapsed.TotalSeconds,
                    camera = new {
                        smooth_x = Math.Round(controller.SmoothX, 4),
                        smooth_y = Math.Round(controller.SmoothY, 4),
                        last_frame = controller.LastFrameIndex
                    }
                });
                HeartbeatWriter.Write(hbPath, hb);
            }
            Thread.Sleep(200);
        }
    }
}
