using System.Text.Json;
using System.Runtime.InteropServices;

namespace Nexus.Control;

internal sealed class CameraController
{
    private readonly string _framesDir;
    private readonly double _alpha;
    private readonly double _deadzone;
    private readonly double _maxStepNorm;
    private readonly double _pixelsPerNormX;
    private readonly double _pixelsPerNormY;
    private readonly int _staleMs;
    private readonly string _windowTitle;

    private double _smoothX = 0.5;
    private double _smoothY = 0.5;
    private int _lastFrameIndex = -1;
    private DateTime _lastAction = DateTime.MinValue;
    private readonly int _minActionIntervalMs;

    public double SmoothX => _smoothX; // exposed for heartbeat
    public double SmoothY => _smoothY;
    public int LastFrameIndex => _lastFrameIndex;

    public CameraController()
    {
        _framesDir = Environment.GetEnvironmentVariable("FRAMES_DIR") ?? Path.Combine("sessions", "current", "frames");
        _alpha = ParseEnv("CAMERA_ALPHA", 0.25);
        _deadzone = ParseEnv("CAMERA_DEADZONE", 0.01);
        _maxStepNorm = ParseEnv("CAMERA_MAX_STEP_NORM", 0.06);
        _pixelsPerNormX = ParseEnv("CAMERA_PIXELS_PER_NORM_X", 2200);
        _pixelsPerNormY = ParseEnv("CAMERA_PIXELS_PER_NORM_Y", 2200);
        _staleMs = (int)ParseEnv("CAMERA_STALE_MS", 3000);
        _minActionIntervalMs = (int)ParseEnv("CAMERA_INTERVAL_MS", 200);
        _windowTitle = Environment.GetEnvironmentVariable("CAMERA_WINDOW_TITLE") ?? "Heroes of the Storm";
    }

    private static double ParseEnv(string key, double fallback)
    {
        var v = Environment.GetEnvironmentVariable(key);
        return double.TryParse(v, out var d) ? d : fallback;
    }

    private record Sidecar(int version, string frame, double ts, int width, int height, List<DetObj> objects);
    private record DetObj(int id, int class_id, string @class, double conf, BBox bbox, Center center);
    private record BBox(int x, int y, int w, int h);
    private record Center(int x, int y);

    public (bool ok, int frameIndex, List<(double nx, double ny)> points, int w, int h) TryLoadLatest()
    {
        if (!Directory.Exists(_framesDir)) return (false, -1, new(), 0, 0);
        var files = Directory.EnumerateFiles(_framesDir, "*.detections.json")
            .Select(p => new FileInfo(p))
            .Where(f => int.TryParse(Path.GetFileNameWithoutExtension(f.Name).Split('.')[0], out _))
            .OrderBy(f => f.Name)
            .ToList();
        if (files.Count == 0) return (false, -1, new(), 0, 0);
        var last = files[^1];
        var stemStr = Path.GetFileNameWithoutExtension(last.Name);
        if (!int.TryParse(stemStr, out var frameIdx)) return (false, -1, new(), 0, 0);
        try
        {
            var json = File.ReadAllText(last.FullName);
            var sc = JsonSerializer.Deserialize<Sidecar>(json, new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true
            });
            if (sc == null || sc.version != 3) return (false, -1, new(), 0, 0);
            if (sc.width <= 0 || sc.height <= 0) return (false, -1, new(), 0, 0);
            var pts = sc.objects?.Select(o => ((double)o.center.x / sc.width, (double)o.center.y / sc.height)).ToList() ?? new();
            return (true, frameIdx, pts, sc.width, sc.height);
        }
        catch
        {
            return (false, -1, new(), 0, 0);
        }
    }

    public void Tick()
    {
        var (ok, frameIndex, pts, w, h) = TryLoadLatest();
        if (!ok)
            return; // nothing to do

        // Simple staleness check by file write time
        var sidecarPath = Path.Combine(_framesDir, frameIndex.ToString("D5") + ".detections.json");
        try
        {
            var age = DateTime.UtcNow - File.GetLastWriteTimeUtc(sidecarPath);
            if (age.TotalMilliseconds > _staleMs)
                return; // stale
        }
        catch { }

        if (frameIndex == _lastFrameIndex)
            return; // already acted on this frame

        _lastFrameIndex = frameIndex;

        if (pts.Count == 0)
            return; // exploration mode could go here later

        var targetX = pts.Average(p => p.nx);
        var targetY = pts.Average(p => p.ny);
        _smoothX = _alpha * targetX + (1 - _alpha) * _smoothX;
        _smoothY = _alpha * targetY + (1 - _alpha) * _smoothY;

        var dx = _smoothX - 0.5;
        var dy = _smoothY - 0.5;
        if (Math.Abs(dx) < _deadzone && Math.Abs(dy) < _deadzone)
            return;

        // Clamp per tick
        dx = Math.Clamp(dx, -_maxStepNorm, _maxStepNorm);
        dy = Math.Clamp(dy, -_maxStepNorm, _maxStepNorm);

        var now = DateTime.UtcNow;
        if ((now - _lastAction).TotalMilliseconds < _minActionIntervalMs)
            return; // too soon

        int dragPxX = (int)(dx * _pixelsPerNormX);
        int dragPxY = (int)(dy * _pixelsPerNormY);
        PerformMiddleMouseDrag(dragPxX, dragPxY);
        _lastAction = now;
    }

    #region Win32 Input

    [DllImport("user32.dll")]
    static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    static extern int GetWindowTextLength(IntPtr hWnd);

    [DllImport("user32.dll")]
    static extern bool GetCursorPos(out POINT lpPoint);

    [DllImport("user32.dll")]
    static extern bool SetCursorPos(int X, int Y);

    [DllImport("user32.dll")]
    static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);

    private const uint MOUSEEVENTF_MIDDLEDOWN = 0x0020;
    private const uint MOUSEEVENTF_MIDDLEUP = 0x0040;
    private const uint MOUSEEVENTF_MOVE = 0x0001; // Relative move (if using SendInput would be better)

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int X; public int Y; }

    private void PerformMiddleMouseDrag(int dx, int dy)
    {
        // Find game window (simple heuristic: foreground title contains expected substring)
        var hFore = GetForegroundWindow();
        if (!WindowTitleMatches(hFore))
        {
            // Best-effort: leave camera alone if not focused to avoid hijacking user input
            return;
        }
        if (!GetCursorPos(out var start)) return;
        // Middle down, move, up
        mouse_event(MOUSEEVENTF_MIDDLEDOWN, (uint)start.X, (uint)start.Y, 0, UIntPtr.Zero);
        // Apply relative move via SetCursorPos (safer for large deltas)
        SetCursorPos(start.X + dx, start.Y + dy);
        mouse_event(MOUSEEVENTF_MOVE, (uint)(start.X + dx), (uint)(start.Y + dy), 0, UIntPtr.Zero);
        mouse_event(MOUSEEVENTF_MIDDLEUP, (uint)(start.X + dx), (uint)(start.Y + dy), 0, UIntPtr.Zero);
    }

    private bool WindowTitleMatches(IntPtr hWnd)
    {
        try
        {
            int len = GetWindowTextLength(hWnd);
            if (len <= 0) return false;
            var sb = new System.Text.StringBuilder(len + 1);
            GetWindowText(hWnd, sb, sb.Capacity);
            return sb.ToString().Contains(_windowTitle, StringComparison.OrdinalIgnoreCase);
        }
        catch { return false; }
    }

    #endregion
}
