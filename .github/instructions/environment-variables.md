# Environment Variables

This document describes the environment variables used across the Nexus Games project for consistent path resolution.

## Core Variables

### `NEXUS_BASE_DIR`

**Purpose**: Defines the root directory for all Nexus services to ensure they operate on the same data directories.

**Default Behavior**:

- **C++ (game-capture)**: Falls back to `std::filesystem::current_path()`
- **Python services**: Falls back to `Path.cwd()` or inferred repository root  
- **Docker**: Set to `/workspace` (container root)

**Directory Structure** (relative to `NEXUS_BASE_DIR`):

```text
${NEXUS_BASE_DIR}/
├── sessions/current/
│   ├── frames/           # Game capture screenshots
│   ├── state/           # Service state & detections
│   ├── capture.log      # Capture service logs
│   └── session.json     # Session metadata
├── replays/
│   ├── queue/           # Pending replays
│   ├── active/          # Currently processing
│   └── completed/       # Finished replays
└── training/            # Training datasets
```

## VS Code Integration

All VS Code tasks and launch configurations automatically set:

```json
"env": {
    "NEXUS_BASE_DIR": "${workspaceFolder}/../.."
}
```

This ensures that regardless of which project workspace you're in, all services coordinate around the same repository root directory.

## Docker Integration

Docker Compose sets `NEXUS_BASE_DIR=/workspace` and mounts:

- `./sessions:/workspace/sessions`
- `./replays:/workspace/replays`

## Usage Examples

### Development (VS Code)

- **From any project workspace**: Environment automatically set
- **Manual terminal**: `$env:NEXUS_BASE_DIR = "D:\path\to\nexus-games"`

### Production/Docker

- **Docker Compose**: Automatically configured
- **Manual Docker**: `-e NEXUS_BASE_DIR=/workspace`

### Testing

```powershell
# Test with custom directory
$env:NEXUS_BASE_DIR = "C:\temp\test-nexus"
uv run python -m detection.service --run-once
```

all services use env `NEXUS_BASE_DIR` for consistent coordination.
