using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;

namespace Nexus.Control;

internal sealed class CameraController
{
    private readonly string _detectionsDir;
    private readonly double _alpha;
    private readonly double _deadzone;
    private readonly double _maxStepNorm;
    private readonly int _staleMs;
    private readonly string _windowTitle;
    private readonly int _minClickIntervalMs;
    private readonly int _clickHoldMs;
    private readonly int _dragReleaseMs;
    private readonly double _recenterThreshold;
    private readonly double _playerPairThresholdPx;
    private readonly double _structureThresholdPx;
    private readonly double _waypointStepNorm;
    private readonly double _waypointSettleNorm;
    private readonly double _targetReplanNorm;
    private readonly double _targetBlendFactor;

    private double _smoothX = 0.5;
    private double _smoothY = 0.5;
    private string? _lastFrameId;
    private DateTime _lastAction = DateTime.MinValue;
    private DateTime _lastClickTs = DateTime.MinValue;
    private bool _viewportPlaced;
    private bool _isDragging;
    private double _lastAppliedX = 0.5;
    private double _lastAppliedY = 0.5;
    private DateTime _lastMovement = DateTime.MinValue;
    private readonly int _minActionIntervalMs;
    private readonly Queue<(double nx, double ny)> _waypoints = new();
    private (double nx, double ny)? _plannedFinalTarget;
    private int _plannedPathPointCount;
    private (double nx, double ny)? _currentPrimaryTarget;
    private int _simulatedClientX;
    private int _simulatedClientY;
    private bool _hasSimulatedPosition;

    public double SmoothX => _smoothX;
    public double SmoothY => _smoothY;
    public string? LastFrameId => _lastFrameId;

    public CameraController()
    {
        var detectionsEnv = Environment.GetEnvironmentVariable("DETECTIONS_DIR")
            ?? Environment.GetEnvironmentVariable("CAMERA_DETECTIONS_DIR");
        _detectionsDir = detectionsEnv ?? Path.Combine("sessions", "current", "state", "detections");
        _alpha = ParseEnv("CAMERA_ALPHA", 0.25);
        _deadzone = ParseEnv("CAMERA_DEADZONE", 0.01);
        _maxStepNorm = ParseEnv("CAMERA_MAX_STEP_NORM", 0.06);
        _staleMs = (int)ParseEnv("CAMERA_STALE_MS", 3000);
        _minActionIntervalMs = (int)ParseEnv("CAMERA_INTERVAL_MS", 80);
        _minClickIntervalMs = (int)ParseEnv("CAMERA_CLICK_INTERVAL_MS", 750);
        _clickHoldMs = (int)ParseEnv("CAMERA_CLICK_HOLD_MS", 30);
        _dragReleaseMs = (int)ParseEnv("CAMERA_DRAG_RELEASE_MS", 200);
        _recenterThreshold = ParseEnv("CAMERA_RECENTER_THRESHOLD", 0.35);
        _windowTitle = Environment.GetEnvironmentVariable("CAMERA_WINDOW_TITLE") ?? "Heroes of the Storm";
        _playerPairThresholdPx = ParseEnv("CAMERA_PLAYER_PAIR_PX", 120);
        _structureThresholdPx = ParseEnv("CAMERA_STRUCTURE_PAIR_PX", 144);
        _waypointStepNorm = Math.Clamp(ParseEnv("CAMERA_WAYPOINT_STEP_NORM", 0.05), 0.005, 0.25);
        _waypointSettleNorm = Math.Clamp(ParseEnv("CAMERA_WAYPOINT_SETTLE_NORM", 0.01), 0.001, 0.1);
        _targetReplanNorm = Math.Clamp(ParseEnv("CAMERA_TARGET_REPLAN_NORM", 0.02), 0.001, 0.5);
        _targetBlendFactor = Math.Clamp(ParseEnv("CAMERA_TARGET_BLEND", 0.65), 0.05, 1.0);
    }

    private static double ParseEnv(string key, double fallback)
    {
        var v = Environment.GetEnvironmentVariable(key);
        return double.TryParse(v, out var d) ? d : fallback;
    }

    private DetectionSnapshot? TryLoadLatest()
    {
        if (!Directory.Exists(_detectionsDir))
        {
            return null;
        }

        var files = Directory.EnumerateFiles(_detectionsDir, "*.detections.json")
            .Select(p => new FileInfo(p))
            .OrderBy(f => f.LastWriteTimeUtc)
            .ThenBy(f => f.Name, StringComparer.OrdinalIgnoreCase)
            .ToList();
        if (files.Count == 0)
        {
            return null;
        }

        var last = files[^1];
        var stemStr = Path.GetFileNameWithoutExtension(last.Name);

        try
        {
            var json = File.ReadAllText(last.FullName);
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;

            if (!root.TryGetProperty("version", out var versionEl) || versionEl.GetInt32() != 3)
            {
                return null;
            }

            if (!root.TryGetProperty("width", out var widthEl) || !root.TryGetProperty("height", out var heightEl))
            {
                return null;
            }

            var width = widthEl.GetInt32();
            var height = heightEl.GetInt32();
            if (width <= 0 || height <= 0)
            {
                return null;
            }

            var detections = new List<DetectionInfo>();
            if (root.TryGetProperty("objects", out var objsEl) && objsEl.ValueKind == JsonValueKind.Array)
            {
                foreach (var obj in objsEl.EnumerateArray())
                {
                    if (!obj.TryGetProperty("center", out var centerEl) || centerEl.ValueKind != JsonValueKind.Object)
                    {
                        continue;
                    }

                    if (!centerEl.TryGetProperty("x", out var cxEl) || !centerEl.TryGetProperty("y", out var cyEl))
                    {
                        continue;
                    }

                    var pixelX = cxEl.GetDouble();
                    var pixelY = cyEl.GetDouble();
                    var normX = Math.Clamp(pixelX / width, 0.0, 1.0);
                    var normY = Math.Clamp(pixelY / height, 0.0, 1.0);

                    double confidence = obj.TryGetProperty("conf", out var confEl) && confEl.ValueKind == JsonValueKind.Number
                        ? confEl.GetDouble()
                        : 0.0;

                    DetectionKind kind = DetectionKind.Unknown;
                    if (obj.TryGetProperty("class", out var classEl) && classEl.ValueKind == JsonValueKind.String)
                    {
                        kind = MapDetectionKind(classEl.GetString());
                    }
                    else if (obj.TryGetProperty("class_id", out var classIdEl) && classIdEl.ValueKind == JsonValueKind.Number)
                    {
                        kind = MapDetectionKind(classIdEl.GetInt32());
                    }

                    detections.Add(new DetectionInfo(kind, normX, normY, pixelX, pixelY, confidence));
                }
            }

            string frameId = stemStr;
            if (root.TryGetProperty("frame", out var frameEl) && frameEl.ValueKind == JsonValueKind.String)
            {
                var candidate = frameEl.GetString();
                if (!string.IsNullOrWhiteSpace(candidate))
                {
                    frameId = candidate!;
                }
            }

            return new DetectionSnapshot(frameId, last.FullName, width, height, detections);
        }
        catch
        {
            return null;
        }
    }

    public void Tick()
    {
        var now = DateTime.UtcNow;

        var snapshot = TryLoadLatest();
        if (snapshot is null)
        {
            ClearPlannedPath();
            _currentPrimaryTarget = null;
            TryReleaseDrag(now);
            return;
        }

        try
        {
            var age = now - File.GetLastWriteTimeUtc(snapshot.SourcePath);
            if (age.TotalMilliseconds > _staleMs)
            {
                ClearPlannedPath();
                _currentPrimaryTarget = null;
                TryReleaseDrag(now);
                return;
            }
        }
        catch
        {
            ClearPlannedPath();
            _currentPrimaryTarget = null;
            TryReleaseDrag(now);
            return;
        }

        if (string.IsNullOrEmpty(snapshot.FrameId))
        {
            ClearPlannedPath();
            _currentPrimaryTarget = null;
            TryReleaseDrag(now);
            return;
        }

        var targetPath = SelectPriorityTargets(snapshot);
        if (targetPath is null || targetPath.Points.Count == 0)
        {
            ClearPlannedPath();
            _currentPrimaryTarget = null;
            TryReleaseDrag(now, force: true);
            _lastFrameId = snapshot.FrameId;
            return;
        }

        _lastFrameId = snapshot.FrameId;
        _currentPrimaryTarget = targetPath.Points[0];
        UpdatePath(targetPath);

        var waypoint = GetCurrentWaypoint(_currentPrimaryTarget.Value);
        _smoothX = _alpha * waypoint.nx + (1 - _alpha) * _smoothX;
        _smoothY = _alpha * waypoint.ny + (1 - _alpha) * _smoothY;

        if (NormalizedDistance((_smoothX, _smoothY), waypoint) <= _waypointSettleNorm)
        {
            AdvanceWaypoint();
            waypoint = GetCurrentWaypoint(_currentPrimaryTarget.Value);
        }

        var offset = Math.Max(Math.Abs(_currentPrimaryTarget.Value.nx - 0.5), Math.Abs(_currentPrimaryTarget.Value.ny - 0.5));

        if (ShouldPerformMinimapClick(now, offset))
        {
            TryReleaseDrag(now, force: true);
            if (TryPerformMinimapClick(_currentPrimaryTarget.Value.nx, _currentPrimaryTarget.Value.ny))
            {
                _viewportPlaced = true;
                _lastClickTs = now;
                _smoothX = _currentPrimaryTarget.Value.nx;
                _smoothY = _currentPrimaryTarget.Value.ny;
                _lastAppliedX = _smoothX;
                _lastAppliedY = _smoothY;
                _lastAction = now;
                ClearPlannedPath();
                if (targetPath.Points.Count > 1)
                {
                    var remainder = targetPath.Points.Skip(1).ToList();
                    _currentPrimaryTarget = remainder[0];
                    UpdatePath(new TargetPath(remainder));
                }
                return;
            }
        }

        if (!_viewportPlaced)
        {
            return;
        }

        if ((now - _lastAction).TotalMilliseconds < _minActionIntervalMs)
        {
            TryReleaseDrag(now);
            return;
        }

        if (HandleDrag(now))
        {
            _lastAction = now;
            var appliedTarget = GetCurrentWaypoint(_currentPrimaryTarget ?? (_smoothX, _smoothY));
            if (NormalizedDistance((_lastAppliedX, _lastAppliedY), appliedTarget) <= _waypointSettleNorm)
            {
                AdvanceWaypoint();
            }
        }
        else
        {
            TryReleaseDrag(now);
        }
    }

    private TargetPath? SelectPriorityTargets(DetectionSnapshot snapshot)
    {
        const int MaxPriorityTargets = 3;

        var rawPoints = ComputePriorityPoints(snapshot, MaxPriorityTargets);
        if (rawPoints.Count == 0)
        {
            return null;
        }

        var blended = BlendPath(rawPoints);
        if (blended.Count == 0)
        {
            return null;
        }

        return new TargetPath(blended);
    }

    private List<(double nx, double ny)> ComputePriorityPoints(DetectionSnapshot snapshot, int maxTargets)
    {
        var engagements = FindEngagementCentroids(snapshot, maxTargets);
        if (engagements.Count > 0)
        {
            return engagements;
        }

        var structurePressure = FindStructurePressureCentroids(snapshot, maxTargets);
        if (structurePressure.Count > 0)
        {
            return structurePressure;
        }

        var teamClusters = FindTeamClusters(snapshot, maxTargets);
        if (teamClusters.Count > 0)
        {
            return teamClusters;
        }

        return ComputeFallbackCentroid(snapshot);
    }

    private List<(double nx, double ny)> FindEngagementCentroids(DetectionSnapshot snapshot, int maxTargets)
    {
        var players = snapshot.Detections.Where(IsPlayer).ToList();
        if (players.Count == 0)
        {
            return new List<(double nx, double ny)>();
        }

        var clusters = BuildClusters(players, (a, b) => ArePlayersLinked(a, b));
        var engagements = clusters
            .Where(cluster => ContainsBothTeams(cluster))
            .OrderByDescending(cluster => cluster.Count)
            .ThenBy(cluster => ComputeClusterSpread(cluster, snapshot))
            .Take(maxTargets)
            .Select(cluster => ComputeCentroidNormalized(cluster, snapshot))
            .ToList();

        return engagements;
    }

    private List<(double nx, double ny)> FindStructurePressureCentroids(DetectionSnapshot snapshot, int maxTargets)
    {
        var candidates = snapshot.Detections
            .Where(d => IsPlayer(d) || IsStructure(d))
            .ToList();

        if (candidates.Count == 0)
        {
            return new List<(double nx, double ny)>();
        }

        var clusters = BuildClusters(candidates, ShouldLinkPlayerStructure);
        var pressureClusters = clusters
            .Where(cluster => HasPlayerPressuringStructure(cluster))
            .OrderByDescending(cluster => cluster.Count(IsPlayer))
            .ThenBy(cluster => ComputeClusterSpread(cluster, snapshot))
            .Take(maxTargets)
            .Select(cluster => ComputeCentroidNormalized(cluster, snapshot))
            .ToList();

        return pressureClusters;
    }

    private List<(double nx, double ny)> FindTeamClusters(DetectionSnapshot snapshot, int maxTargets)
    {
        var prioritized = new List<(double nx, double ny)>();

        foreach (var team in Enum.GetValues<DetectionTeam>())
        {
            var teamPlayers = snapshot.Detections
                .Where(d => IsPlayer(d) && GetTeam(d) == team)
                .ToList();

            if (teamPlayers.Count == 0)
            {
                continue;
            }

            var clusters = BuildClusters(teamPlayers, (a, b) => ArePlayersLinked(a, b))
                .OrderByDescending(cluster => cluster.Count)
                .ThenBy(cluster => ComputeClusterSpread(cluster, snapshot));

            foreach (var cluster in clusters)
            {
                prioritized.Add(ComputeCentroidNormalized(cluster, snapshot));
                if (prioritized.Count >= maxTargets)
                {
                    return prioritized;
                }
            }
        }

        return prioritized;
    }

    private List<(double nx, double ny)> ComputeFallbackCentroid(DetectionSnapshot snapshot)
    {
        var players = snapshot.Detections.Where(IsPlayer).ToList();
        if (players.Count > 0)
        {
            return new List<(double nx, double ny)>
            {
                ComputeCentroidNormalized(players, snapshot)
            };
        }

        if (snapshot.Detections.Count > 0)
        {
            return new List<(double nx, double ny)>
            {
                ComputeCentroidNormalized(snapshot.Detections, snapshot)
            };
        }

        return new List<(double nx, double ny)>
        {
            (0.5, 0.5)
        };
    }

    private List<(double nx, double ny)> BlendPath(IReadOnlyList<(double nx, double ny)> rawPoints)
    {
        var result = new List<(double nx, double ny)>();
        if (rawPoints.Count == 0)
        {
            return result;
        }

        var anchor = _plannedFinalTarget ?? _currentPrimaryTarget ?? (_smoothX, _smoothY);
        double anchorX = anchor.nx;
        double anchorY = anchor.ny;

        foreach (var point in rawPoints)
        {
            var blendedX = anchorX + (point.nx - anchorX) * _targetBlendFactor;
            var blendedY = anchorY + (point.ny - anchorY) * _targetBlendFactor;
            blendedX = Math.Clamp(blendedX, 0.0, 1.0);
            blendedY = Math.Clamp(blendedY, 0.0, 1.0);
            result.Add((blendedX, blendedY));
            anchorX = blendedX;
            anchorY = blendedY;
        }

        return result;
    }

    private (double nx, double ny) ComputeCentroidNormalized(IReadOnlyCollection<DetectionInfo> cluster, DetectionSnapshot snapshot)
    {
        var centroidPx = ComputeCentroidPixels(cluster);
        var nx = Math.Clamp(centroidPx.px / snapshot.Width, 0.0, 1.0);
        var ny = Math.Clamp(centroidPx.py / snapshot.Height, 0.0, 1.0);
        return (nx, ny);
    }

    private static (double px, double py) ComputeCentroidPixels(IReadOnlyCollection<DetectionInfo> cluster)
    {
        if (cluster.Count == 0)
        {
            return (0.0, 0.0);
        }

        double sumX = 0;
        double sumY = 0;
        foreach (var detection in cluster)
        {
            sumX += detection.PixelX;
            sumY += detection.PixelY;
        }

        return (sumX / cluster.Count, sumY / cluster.Count);
    }

    private double ComputeClusterSpread(IReadOnlyCollection<DetectionInfo> cluster, DetectionSnapshot snapshot)
    {
        if (cluster.Count == 0)
        {
            return double.MaxValue;
        }

        var centroid = ComputeCentroidPixels(cluster);
        double total = 0;
        foreach (var detection in cluster)
        {
            var dx = (detection.PixelX - centroid.px) / snapshot.Width;
            var dy = (detection.PixelY - centroid.py) / snapshot.Height;
            total += Math.Sqrt(dx * dx + dy * dy);
        }

        return total / cluster.Count;
    }

    private List<List<DetectionInfo>> BuildClusters(IReadOnlyList<DetectionInfo> items, Func<DetectionInfo, DetectionInfo, bool> shouldLink)
    {
        var clusters = new List<List<DetectionInfo>>();
        if (items.Count == 0)
        {
            return clusters;
        }

        var visited = new bool[items.Count];
        for (int i = 0; i < items.Count; i++)
        {
            if (visited[i])
            {
                continue;
            }

            var cluster = new List<DetectionInfo>();
            var queue = new Queue<int>();
            queue.Enqueue(i);
            visited[i] = true;

            while (queue.Count > 0)
            {
                var index = queue.Dequeue();
                var current = items[index];
                cluster.Add(current);

                for (int j = 0; j < items.Count; j++)
                {
                    if (visited[j])
                    {
                        continue;
                    }

                    if (!shouldLink(current, items[j]) && !shouldLink(items[j], current))
                    {
                        continue;
                    }

                    visited[j] = true;
                    queue.Enqueue(j);
                }
            }

            clusters.Add(cluster);
        }

        return clusters;
    }

    private bool ArePlayersLinked(DetectionInfo a, DetectionInfo b)
    {
        if (!IsPlayer(a) || !IsPlayer(b))
        {
            return false;
        }

        return DistancePixels(a, b) <= _playerPairThresholdPx;
    }

    private bool ShouldLinkPlayerStructure(DetectionInfo a, DetectionInfo b)
    {
        var distance = DistancePixels(a, b);

        if (IsPlayer(a) && IsPlayer(b))
        {
            return distance <= _playerPairThresholdPx;
        }

        if (IsPlayer(a) && IsStructure(b))
        {
            return AreEnemies(a, b) && distance <= _structureThresholdPx;
        }

        if (IsStructure(a) && IsPlayer(b))
        {
            return AreEnemies(a, b) && distance <= _structureThresholdPx;
        }

        return false;
    }

    private static bool ContainsBothTeams(IReadOnlyCollection<DetectionInfo> cluster)
    {
        bool hasRed = cluster.Any(d => d.Kind == DetectionKind.RedPlayer);
        bool hasBlue = cluster.Any(d => d.Kind == DetectionKind.BluePlayer);
        return hasRed && hasBlue;
    }

    private static bool HasPlayerPressuringStructure(IReadOnlyCollection<DetectionInfo> cluster)
    {
        bool hasRedPlayer = cluster.Any(d => d.Kind == DetectionKind.RedPlayer);
        bool hasBluePlayer = cluster.Any(d => d.Kind == DetectionKind.BluePlayer);
        bool hasRedStructure = cluster.Any(d => d.Kind == DetectionKind.RedTower || d.Kind == DetectionKind.RedNexus);
        bool hasBlueStructure = cluster.Any(d => d.Kind == DetectionKind.BlueTower || d.Kind == DetectionKind.BlueNexus);

        return (hasRedPlayer && hasBlueStructure) || (hasBluePlayer && hasRedStructure);
    }

    private static bool IsPlayer(DetectionInfo detection)
    {
        return detection.Kind is DetectionKind.RedPlayer or DetectionKind.BluePlayer;
    }

    private static bool IsStructure(DetectionInfo detection)
    {
        return detection.Kind is DetectionKind.RedTower or DetectionKind.BlueTower or DetectionKind.RedNexus or DetectionKind.BlueNexus;
    }

    private static DetectionTeam? GetTeam(DetectionInfo detection)
    {
        return detection.Kind switch
        {
            DetectionKind.BluePlayer or DetectionKind.BlueTower or DetectionKind.BlueNexus => DetectionTeam.Blue,
            DetectionKind.RedPlayer or DetectionKind.RedTower or DetectionKind.RedNexus => DetectionTeam.Red,
            _ => null
        };
    }

    private static bool AreEnemies(DetectionInfo a, DetectionInfo b)
    {
        var teamA = GetTeam(a);
        var teamB = GetTeam(b);
        return teamA.HasValue && teamB.HasValue && teamA.Value != teamB.Value;
    }

    private void UpdatePath(TargetPath path)
    {
        if (path.Points.Count == 0)
        {
            ClearPlannedPath();
            return;
        }

        var final = path.Points[^1];
        var needsRebuild = _plannedFinalTarget is null
            || _waypoints.Count == 0
            || _plannedPathPointCount != path.Points.Count
            || NormalizedDistance(_plannedFinalTarget.Value, final) > _targetReplanNorm;

        if (needsRebuild)
        {
            RebuildPath(path.Points);
        }
    }

    private void RebuildPath(IReadOnlyList<(double nx, double ny)> points)
    {
        _waypoints.Clear();
        _plannedFinalTarget = null;
        _plannedPathPointCount = 0;

        if (points.Count == 0)
        {
            return;
        }

        var startX = _smoothX;
        var startY = _smoothY;

        foreach (var point in points)
        {
            foreach (var waypoint in GenerateSegmentWaypoints(startX, startY, point.nx, point.ny))
            {
                _waypoints.Enqueue(waypoint);
            }

            startX = point.nx;
            startY = point.ny;
        }

        if (_waypoints.Count == 0)
        {
            _waypoints.Enqueue(points[^1]);
        }

        _plannedFinalTarget = points[^1];
        _plannedPathPointCount = points.Count;
    }

    private (double nx, double ny) GetCurrentWaypoint((double nx, double ny) fallback)
    {
        return _waypoints.Count > 0 ? _waypoints.Peek() : fallback;
    }

    private void AdvanceWaypoint()
    {
        if (_waypoints.Count == 0)
        {
            return;
        }

        _waypoints.Dequeue();
        if (_waypoints.Count == 0)
        {
            _plannedFinalTarget = null;
            _plannedPathPointCount = 0;
        }
    }

    private IEnumerable<(double nx, double ny)> GenerateSegmentWaypoints(double startX, double startY, double endX, double endY)
    {
        var dx = endX - startX;
        var dy = endY - startY;
        var distance = Math.Sqrt(dx * dx + dy * dy);
        if (distance <= double.Epsilon)
        {
            yield break;
        }

        var step = Math.Max(_waypointStepNorm, 1e-6);
        var segments = Math.Max(1, (int)Math.Ceiling(distance / step));

        for (int i = 1; i <= segments; i++)
        {
            var t = (double)i / segments;
            var nx = Math.Clamp(startX + dx * t, 0.0, 1.0);
            var ny = Math.Clamp(startY + dy * t, 0.0, 1.0);
            yield return (nx, ny);
        }
    }

    private void ClearPlannedPath()
    {
        _waypoints.Clear();
        _plannedFinalTarget = null;
        _plannedPathPointCount = 0;
    }

    private static double NormalizedDistance((double nx, double ny) a, (double nx, double ny) b)
    {
        var dx = a.nx - b.nx;
        var dy = a.ny - b.ny;
        return Math.Sqrt(dx * dx + dy * dy);
    }

    private static double DistancePixels(DetectionInfo a, DetectionInfo b)
    {
        var dx = a.PixelX - b.PixelX;
        var dy = a.PixelY - b.PixelY;
        return Math.Sqrt(dx * dx + dy * dy);
    }

    private DetectionKind MapDetectionKind(string? className)
    {
        if (string.IsNullOrWhiteSpace(className))
        {
            return DetectionKind.Unknown;
        }

        return className.Trim().ToLowerInvariant() switch
        {
            "blue player" => DetectionKind.BluePlayer,
            "red player" => DetectionKind.RedPlayer,
            "blue tower" => DetectionKind.BlueTower,
            "red tower" => DetectionKind.RedTower,
            "blue nexus" => DetectionKind.BlueNexus,
            "red nexus" => DetectionKind.RedNexus,
            _ => DetectionKind.Unknown
        };
    }

    private DetectionKind MapDetectionKind(int classId)
    {
        return classId switch
        {
            0 => DetectionKind.BlueNexus,
            1 => DetectionKind.BluePlayer,
            2 => DetectionKind.BlueTower,
            5 => DetectionKind.RedNexus,
            6 => DetectionKind.RedPlayer,
            7 => DetectionKind.RedTower,
            _ => DetectionKind.Unknown
        };
    }

    private sealed class TargetPath
    {
        public TargetPath(List<(double nx, double ny)> points)
        {
            Points = points;
        }

        public List<(double nx, double ny)> Points { get; }
    }

    private sealed class DetectionSnapshot
    {
        public DetectionSnapshot(string frameId, string sourcePath, int width, int height, List<DetectionInfo> detections)
        {
            FrameId = frameId;
            SourcePath = sourcePath;
            Width = width;
            Height = height;
            Detections = detections;
        }

        public string FrameId { get; }
        public string SourcePath { get; }
        public int Width { get; }
        public int Height { get; }
        public List<DetectionInfo> Detections { get; }
    }

    private sealed record DetectionInfo(DetectionKind Kind, double NormX, double NormY, double PixelX, double PixelY, double Confidence);

    private enum DetectionKind
    {
        Unknown = 0,
        BluePlayer,
        RedPlayer,
        BlueTower,
        RedTower,
        BlueNexus,
        RedNexus
    }

    private enum DetectionTeam
    {
        Blue,
        Red
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
    static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll")]
    static extern bool PostMessage(IntPtr hWnd, uint Msg, UIntPtr wParam, IntPtr lParam);

    private const uint WM_MOUSEMOVE = 0x0200;
    private const uint WM_LBUTTONDOWN = 0x0201;
    private const uint WM_LBUTTONUP = 0x0202;
    private const uint WM_MBUTTONDOWN = 0x0207;
    private const uint WM_MBUTTONUP = 0x0208;

    private const uint MK_LBUTTON = 0x0001;
    private const uint MK_MBUTTON = 0x0010;

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    private bool HandleDrag(DateTime now)
    {
        if (!TryPrepareWindow(out var hWnd, out var rect))
        {
            return false;
        }

        int width = rect.Right - rect.Left;
        int height = rect.Bottom - rect.Top;
        if (width <= 0 || height <= 0)
        {
            return false;
        }

        var deltaXNorm = Math.Clamp(_smoothX - _lastAppliedX, -_maxStepNorm, _maxStepNorm);
        var deltaYNorm = Math.Clamp(_smoothY - _lastAppliedY, -_maxStepNorm, _maxStepNorm);

        if (Math.Abs(deltaXNorm) < _deadzone && Math.Abs(deltaYNorm) < _deadzone)
        {
            return false;
        }

        var nextXNorm = Math.Clamp(_lastAppliedX + deltaXNorm, 0.0, 1.0);
        var nextYNorm = Math.Clamp(_lastAppliedY + deltaYNorm, 0.0, 1.0);

        var clientX = ToClientCoordinate(nextXNorm, width);
        var clientY = ToClientCoordinate(nextYNorm, height);

        if (!_hasSimulatedPosition)
        {
            _simulatedClientX = clientX;
            _simulatedClientY = clientY;
            _hasSimulatedPosition = true;
        }

        if (!EnsureDragStarted(now, hWnd))
        {
            return false;
        }

        SendClientMouseMove(hWnd, clientX, clientY, middleDown: true);
        _simulatedClientX = clientX;
        _simulatedClientY = clientY;
        _lastAppliedX = nextXNorm;
        _lastAppliedY = nextYNorm;
        _lastMovement = now;
        return true;
    }

    private bool EnsureDragStarted(DateTime now, IntPtr hWnd)
    {
        if (_isDragging)
        {
            return true;
        }

        int clientX = _hasSimulatedPosition ? _simulatedClientX : 0;
        int clientY = _hasSimulatedPosition ? _simulatedClientY : 0;
        SendClientMouseMove(hWnd, clientX, clientY, middleDown: false);
        SendClientButton(hWnd, WM_MBUTTONDOWN, MK_MBUTTON, clientX, clientY);
        _isDragging = true;
        _lastMovement = now;
        return true;
    }

    private void TryReleaseDrag(DateTime now, bool force = false)
    {
        if (!_isDragging)
        {
            return;
        }

        if (!force && (now - _lastMovement).TotalMilliseconds < _dragReleaseMs)
        {
            return;
        }

        if (TryGetGameWindow(out var hWnd))
        {
            SendClientButton(hWnd, WM_MBUTTONUP, 0, _simulatedClientX, _simulatedClientY);
        }

        _isDragging = false;
    }

    private bool TryPerformMinimapClick(double normX, double normY)
    {
        if (!TryPrepareWindow(out var hWnd, out var rect))
        {
            return false;
        }

        var (clientX, clientY) = ToClientCoordinates(normX, normY, rect);
        SendClientMouseMove(hWnd, clientX, clientY, middleDown: false);
        SendClientButton(hWnd, WM_LBUTTONDOWN, MK_LBUTTON, clientX, clientY);
        if (_clickHoldMs > 0)
        {
            Thread.Sleep(_clickHoldMs);
        }

        SendClientButton(hWnd, WM_LBUTTONUP, 0, clientX, clientY);
        _simulatedClientX = clientX;
        _simulatedClientY = clientY;
        _hasSimulatedPosition = true;
        _lastAppliedX = normX;
        _lastAppliedY = normY;
        return true;
    }

    private bool TryPrepareWindow(out IntPtr hWnd, out RECT rect)
    {
        rect = default;

        if (!TryGetGameWindow(out hWnd))
        {
            return false;
        }

        if (hWnd != IntPtr.Zero)
        {
            SetForegroundWindow(hWnd);
        }

        return GetClientRect(hWnd, out rect);
    }

    private (int x, int y) ToClientCoordinates(double normX, double normY, RECT rect)
    {
        int width = rect.Right - rect.Left;
        int height = rect.Bottom - rect.Top;
        return (ToClientCoordinate(normX, width), ToClientCoordinate(normY, height));
    }

    private static int ToClientCoordinate(double norm, int size)
    {
        if (size <= 0)
        {
            return 0;
        }

        var clamped = Math.Clamp(norm, 0.0, 1.0);
        var coordinate = (int)Math.Round(clamped * Math.Max(size - 1, 0), MidpointRounding.AwayFromZero);
        return Math.Clamp(coordinate, 0, Math.Max(size - 1, 0));
    }

    private void SendClientMouseMove(IntPtr hWnd, int clientX, int clientY, bool middleDown)
    {
        var keyFlags = middleDown ? MK_MBUTTON : 0u;
        var lParam = MakeLParam(clientX, clientY);
        PostMessage(hWnd, WM_MOUSEMOVE, (UIntPtr)keyFlags, lParam);
    }

    private void SendClientButton(IntPtr hWnd, uint message, uint keyFlags, int clientX, int clientY)
    {
        var lParam = MakeLParam(clientX, clientY);
        PostMessage(hWnd, message, (UIntPtr)keyFlags, lParam);
    }

    private static IntPtr MakeLParam(int x, int y)
    {
        uint ux = (uint)(x & 0xFFFF);
        uint uy = (uint)(y & 0xFFFF);
        uint value = ux | (uy << 16);
        return (IntPtr)(int)value;
    }

    private bool TryGetGameWindow(out IntPtr hWnd)
    {
        var candidate = GetForegroundWindow();
        if (WindowTitleMatches(candidate))
        {
            hWnd = candidate;
            return true;
        }

        var found = IntPtr.Zero;

        EnumWindows((handle, _) =>
        {
            if (WindowTitleMatches(handle))
            {
                found = handle;
                return false;
            }

            return true;
        }, IntPtr.Zero);

        hWnd = found;
        return hWnd != IntPtr.Zero;
    }

    private bool ShouldPerformMinimapClick(DateTime now, double offsetFromCenter)
    {
        if (!_viewportPlaced)
        {
            return true;
        }

        if (_minClickIntervalMs > 0 && (now - _lastClickTs).TotalMilliseconds < _minClickIntervalMs)
        {
            return false;
        }

        return offsetFromCenter >= _recenterThreshold && !_isDragging;
    }

    private bool WindowTitleMatches(IntPtr hWnd)
    {
        try
        {
            int len = GetWindowTextLength(hWnd);
            if (len <= 0)
            {
                return false;
            }
            var sb = new System.Text.StringBuilder(len + 1);
            GetWindowText(hWnd, sb, sb.Capacity);
            return sb.ToString().Contains(_windowTitle, StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return false;
        }
    }

    #endregion
}
