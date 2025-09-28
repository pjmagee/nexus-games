[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string]$SessionPath = (Join-Path (Join-Path (Split-Path $PSScriptRoot -Parent) "sessions") "current")
)

if (-not (Test-Path -LiteralPath $SessionPath)) {
    throw "Session path '$SessionPath' was not found."
}

$targets = @(
    @{ Path = Join-Path $SessionPath "frames"; Label = "frame captures" },
    @{ Path = Join-Path (Join-Path $SessionPath "state") "detections"; Label = "detection JSON" },
    @{ Path = Join-Path (Join-Path $SessionPath "state") "annotated"; Label = "annotated previews" }
)

foreach ($target in $targets) {
    $path = [System.IO.Path]::GetFullPath($target.Path)

    if (-not (Test-Path -LiteralPath $path)) {
        Write-Verbose ("Skipping missing {0} at {1}" -f $target.Label, $path)
        continue
    }

    $children = Get-ChildItem -LiteralPath $path -Force
    if (-not $children) {
        Write-Host ("No files to remove in {0} ({1})" -f $target.Label, $path)
        continue
    }

    $action = "Remove {0} item(s)" -f $children.Count
    if ($PSCmdlet.ShouldProcess($path, $action)) {
        try {
            $children | Remove-Item -Recurse -Force -ErrorAction Stop
            Write-Host ("Cleared {0} from {1}" -f $target.Label, $path)
        }
        catch {
            Write-Warning ("Failed clearing {0} at {1}: {2}" -f $target.Label, $path, $_.Exception.Message)
        }
    }
}
