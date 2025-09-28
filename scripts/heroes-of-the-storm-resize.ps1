$processName   = "HeroesOfTheStorm_x64"
$targetWidth   = 1920
$targetHeight  = 1080

Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class Win32 {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    public static readonly IntPtr HWND_TOP = IntPtr.Zero;
    public const uint SWP_NOZORDER      = 0x0004;
    public const uint SWP_NOOWNERZORDER = 0x0200;
    public const uint SWP_NOACTIVATE    = 0x0010;

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }
}
"@

function Get-HeroesWindow {
    param([string]$Name)

    Get-Process -Name $Name -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } |
        Select-Object -First 1
}

$proc = Get-HeroesWindow -Name $processName
if (-not $proc) {
    Write-Host "Waiting for $processName process..."
    while (-not $proc) {
        Start-Sleep -Seconds 1
        $proc = Get-HeroesWindow -Name $processName
    }
}

$handle = $proc.MainWindowHandle
if ($handle -eq 0) {
    throw "Process '$processName' does not have a main window yet."
}

# Ensure the window is visible/normal.
[Win32]::ShowWindow($handle, 1) | Out-Null  # SW_SHOWNORMAL

# Keep the current top-left corner.
$rect = New-Object Win32+RECT
[Win32]::GetWindowRect($handle, [ref]$rect) | Out-Null
$x = $rect.Left
$y = $rect.Top

$flags = [Win32]::SWP_NOZORDER -bor [Win32]::SWP_NOOWNERZORDER -bor [Win32]::SWP_NOACTIVATE
if (-not [Win32]::SetWindowPos($handle, [Win32]::HWND_TOP, $x, $y, $targetWidth, $targetHeight, $flags)) {
    $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    throw "SetWindowPos failed with Win32 error $err."
}

Write-Host "Resized Heroes window to ${targetWidth}x${targetHeight}."
