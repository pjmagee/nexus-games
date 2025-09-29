
<#
.SYNOPSIS
    Validates system compatibility for GPU partition virtualization.

.DESCRIPTION
    This script checks if your system meets the requirements for GPU-PV (GPU Paravirtualization)
    and lists compatible GPUs that can be partitioned for virtual machines.

.NOTES
    Requires administrative privileges for accurate hardware detection.
    Run this script before setting up GPU partition adapters.
#>

function Test-DesktopPC {
    <#
    .SYNOPSIS
    Checks if the system is a desktop computer suitable for GPU-PV.
    #>
    $isDesktop = $true
    
    # Check chassis type
    $chassis = Get-WmiObject -Class win32_systemenclosure | Where-Object { 
        $_.chassistypes -eq 9 -or $_.chassistypes -eq 10 -or $_.chassistypes -eq 14 
    }
    
    if ($chassis) {
        Write-Warning "LAPTOP DETECTED: Laptop dedicated GPUs may not work reliably with GPU-PV"
        Write-Warning "NOTE: Thunderbolt 3/4 dock-based GPUs may work on laptops"
        $isDesktop = $false 
    }
    
    # Check for battery (additional laptop indicator)
    if (Get-WmiObject -Class win32_battery) {
        $isDesktop = $false
    }
    
    return $isDesktop
}

function Test-WindowsCompatibility {
    <#
    .SYNOPSIS
    Verifies Windows version and edition compatibility.
    #>
    $build = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $isCompatible = $build.CurrentBuild -ge 19041 -and (
        $build.editionid -like 'Professional*' -or 
        $build.editionid -like 'Enterprise*' -or 
        $build.editionid -like 'Education*'
    )
    
    if ($isCompatible) {
        Write-Host "✓ Windows version: $($build.ProductName) Build $($build.CurrentBuild) ($($build.editionid))" -ForegroundColor Green
    } else {
        Write-Warning "UNSUPPORTED: Requires Windows 10 20H1+ or Windows 11 (Pro/Enterprise/Education edition)"
        Write-Host "Current: $($build.ProductName) Build $($build.CurrentBuild) ($($build.editionid))" -ForegroundColor Red
    }
    
    return $isCompatible
}

function Test-HyperVStatus {
    <#
    .SYNOPSIS
    Checks if Hyper-V is properly enabled.
    #>
    # First try to check if Hyper-V service is running (doesn't require elevation)
    $hyperVService = Get-Service -Name "vmms" -ErrorAction SilentlyContinue
    
    if ($hyperVService -and $hyperVService.Status -eq 'Running') {
        Write-Host "✓ Hyper-V is enabled and running" -ForegroundColor Green
        return $true
    }
    
    # If service check fails, try the feature check (requires elevation)
    try {
        $hyperVFeature = Get-WindowsOptionalFeature -Online | Where-Object FeatureName -Like 'Microsoft-Hyper-V-All'
        
        if ($hyperVFeature -and $hyperVFeature.State -eq 'Enabled') {
            Write-Host "✓ Hyper-V is enabled" -ForegroundColor Green
            return $true
        }
    } catch {
        # If we can't check features due to elevation, but can check if Hyper-V cmdlets work
        try {
            $null = Get-VM -ErrorAction Stop
            Write-Host "✓ Hyper-V is enabled (verified via cmdlets)" -ForegroundColor Green
            return $true
        } catch {
            # Hyper-V cmdlets not available
        }
    }
    
    Write-Warning "MISSING REQUIREMENT: Hyper-V is not enabled or not running"
    Write-Host "SOLUTION: Enable virtualization in BIOS, then run as Administrator: Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All" -ForegroundColor Yellow
    return $false
}

function Test-WSLConflict {
    <#
    .SYNOPSIS
    Checks for potential WSL conflicts with GPU-PV.
    #>
    try {
        $wslOutput = wsl -l -v 2>$null
        if ($wslOutput -and $wslOutput.Count -gt 2 -and $wslOutput[2].length -gt 1) {
            Write-Warning "WSL CONFLICT: WSL is enabled and may cause GPU Error 43 in VMs"
            Write-Host "RECOMMENDATION: Consider disabling WSL if you encounter GPU issues" -ForegroundColor Yellow
            return $true
        }
    } catch {
        # WSL not installed or accessible
    }
    
    Write-Host "✓ No WSL conflicts detected" -ForegroundColor Green
    return $false
}

function Get-PartitionableGPUs {
    <#
    .SYNOPSIS
    Retrieves and displays GPUs that support partitioning.
    #>
    Write-Host "`nScanning for partitionable GPUs..." -ForegroundColor Yellow
    
    try {
        $partitionableDevices = Get-WmiObject -Class "Msvm_PartitionableGpu" -ComputerName $env:COMPUTERNAME -Namespace "ROOT\virtualization\v2"
        
        if (-not $partitionableDevices) {
            Write-Host "❌ No partitionable GPUs found" -ForegroundColor Red
            Write-Host "POSSIBLE CAUSES:" -ForegroundColor Yellow
            Write-Host "  • GPU doesn't support GPU-PV (check manufacturer documentation)" -ForegroundColor Yellow
            Write-Host "  • GPU drivers not properly installed" -ForegroundColor Yellow
            Write-Host "  • Hyper-V GPU partition feature not enabled" -ForegroundColor Yellow
            return $null
        }
        
        $gpuList = @()
        foreach ($device in $partitionableDevices) {
            $gpuParse = $device.Name.Split('#')[1]
            $gpuInfo = Get-WmiObject Win32_PNPSignedDriver | Where-Object {$_.HardwareID -eq "PCI\$gpuParse"}
            
            if ($gpuInfo) {
                $gpuList += $gpuInfo.DeviceName
            }
        }
        
        if ($gpuList.Count -gt 0) {
            Write-Host "✓ Found $($gpuList.Count) partitionable GPU(s):" -ForegroundColor Green
            for ($i = 0; $i -lt $gpuList.Count; $i++) {
                Write-Host "  [$($i + 1)] $($gpuList[$i])" -ForegroundColor Cyan
            }
            Write-Host "`nNOTE: Copy the exact GPU name for use with Update-VMGpuPartitionDriver.ps1" -ForegroundColor Yellow
        } else {
            Write-Host "❌ No compatible GPU drivers found for partitionable devices" -ForegroundColor Red
        }
        
        return $gpuList
        
    } catch {
        Write-Host "❌ Error scanning for GPUs: $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }
}

Write-Host "=== GPU-PV Compatibility Check ===" -ForegroundColor Cyan
Write-Host "Validating system requirements for GPU partition virtualization`n" -ForegroundColor White

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "NOTE: Not running as Administrator - some checks may be limited" -ForegroundColor Yellow
    Write-Host "For most accurate results, run PowerShell as Administrator`n" -ForegroundColor Yellow
}

$isDesktop = Test-DesktopPC
$isWindowsCompatible = Test-WindowsCompatibility  
$isHyperVEnabled = Test-HyperVStatus
$hasWSLConflict = Test-WSLConflict

Write-Host "`n=== SYSTEM COMPATIBILITY SUMMARY ===" -ForegroundColor Cyan

if ($isDesktop -and $isWindowsCompatible -and $isHyperVEnabled) {
    Write-Host "✓ SYSTEM IS COMPATIBLE with GPU-PV" -ForegroundColor Green
    
    $gpuList = Get-PartitionableGPUs
    
    if ($gpuList -and $gpuList.Count -gt 0) {
        Write-Host "`n✓ READY TO PROCEED with GPU partition setup" -ForegroundColor Green
        Write-Host "NEXT STEPS:" -ForegroundColor Yellow
        Write-Host "  1. Use Update-VMGpuPartitionDriver.ps1 to copy GPU drivers to your VM" -ForegroundColor White
        Write-Host "  2. Use Set-VMGpuPartitionAdapter.ps1 to configure GPU partition" -ForegroundColor White
    } else {
        Write-Host "`n❌ NO COMPATIBLE GPUS FOUND" -ForegroundColor Red
        Write-Host "Cannot proceed without a partitionable GPU" -ForegroundColor Red
    }
    
} else {
    Write-Host "❌ SYSTEM NOT COMPATIBLE - Fix the issues above before proceeding" -ForegroundColor Red
    
    if (-not $isDesktop) {
        Write-Host "  • Desktop computer recommended" -ForegroundColor Red
    }
    if (-not $isWindowsCompatible) {
        Write-Host "  • Upgrade Windows version/edition" -ForegroundColor Red  
    }
    if (-not $isHyperVEnabled) {
        Write-Host "  • Enable Hyper-V feature" -ForegroundColor Red
    }
}

Write-Host "`nPress Enter to exit..." -ForegroundColor Gray
Read-Host
