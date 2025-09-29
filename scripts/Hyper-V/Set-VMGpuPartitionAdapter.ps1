<#
.SYNOPSIS
    Assigns and configures GPU partition adapter for a Hyper-V VM.

.DESCRIPTION
    This script adds a GPU partition adapter to a VM and configures the memory allocation
    for VRAM, encode, decode, and compute resources.

.PARAMETER VMName
    Name of the Hyper-V virtual machine.

.PARAMETER GPUName
    Name of the GPU to partition. Use "AUTO" to automatically select the first available GPU.

.PARAMETER GPUResourceAllocationPercentage
    Percentage of GPU resources to allocate to the VM (default: 50%).

.EXAMPLE
    .\Set-VMGpuPartitionAdapter.ps1 -VMName "MyVM" -GPUName "AUTO" -GPUResourceAllocationPercentage 50

.EXAMPLE
    .\Set-VMGpuPartitionAdapter.ps1 -VMName "MyVM" -GPUName "NVIDIA GeForce RTX 4080" -GPUResourceAllocationPercentage 75

.NOTES
    Requires administrative privileges and Hyper-V PowerShell module.
    VM should be in stopped state when adding GPU partition adapter.
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$VMName,

    [Parameter(Mandatory=$false)]
    [string]$GPUName = "AUTO",

    [Parameter(Mandatory=$false)]
    [ValidateRange(1,100)]
    [decimal]$GPUResourceAllocationPercentage = 50
)

# Check if running as administrator
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Host "ERROR: This script must be run as Administrator" -ForegroundColor Red
    exit 1
}

# Validate VM exists
try {
    $VM = Get-VM -Name $VMName -ErrorAction Stop
    Write-Host "Found VM: $VMName" -ForegroundColor Green
} catch {
    Write-Host "ERROR: VM '$VMName' not found: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Check if VM is stopped
if ($VM.State -ne "Off") {
    Write-Host "WARNING: VM is not stopped. Stopping VM..." -ForegroundColor Yellow
    try {
        Stop-VM -Name $VMName -Force
        Write-Host "VM stopped successfully" -ForegroundColor Green
    } catch {
        Write-Host "ERROR: Failed to stop VM: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}

Write-Host "Configuring GPU partition adapter for VM: $VMName" -ForegroundColor Yellow

try {
    # Get available partitionable GPUs
    $PartitionableGPUList = Get-WmiObject -Class "Msvm_PartitionableGpu" -ComputerName $env:COMPUTERNAME -Namespace "ROOT\virtualization\v2"

    if (-not $PartitionableGPUList) {
        Write-Host "ERROR: No partitionable GPUs found. Ensure your GPU supports GPU-PV" -ForegroundColor Red
        exit 1
    }

    # Determine which GPU to use
    if ($GPUName -eq "AUTO") {
        $DevicePathName = $PartitionableGPUList.Name[0]
        Write-Host "AUTO mode: Using first available GPU" -ForegroundColor Green
        Add-VMGpuPartitionAdapter -VMName $VMName
    } else {
        # Find specific GPU by name
        $DeviceID = ((Get-WmiObject Win32_PNPSignedDriver | Where-Object {($_.Devicename -eq "$GPUName")}).hardwareid).split('\')[1]
        if (-not $DeviceID) {
            Write-Host "ERROR: GPU '$GPUName' not found" -ForegroundColor Red
            exit 1
        }

        $DevicePathName = ($PartitionableGPUList | Where-Object name -like "*$deviceid*").Name
        if (-not $DevicePathName) {
            Write-Host "ERROR: GPU '$GPUName' is not partitionable" -ForegroundColor Red
            exit 1
        }

        Write-Host "Using specified GPU: $GPUName" -ForegroundColor Green
        Add-VMGpuPartitionAdapter -VMName $VMName -InstancePath $DevicePathName
    }

    # Calculate resource allocation
    [float]$divider = [math]::round($(100 / $GPUResourceAllocationPercentage), 2)

    Write-Host "Setting GPU resource allocation to $GPUResourceAllocationPercentage%" -ForegroundColor Yellow

    # Configure VRAM allocation
    $vramAllocation = [math]::round($(1000000000 / $divider))
    Set-VMGpuPartitionAdapter -VMName $VMName -MinPartitionVRAM $vramAllocation -MaxPartitionVRAM $vramAllocation -OptimalPartitionVRAM $vramAllocation
    Write-Host "VRAM allocation set to: $([math]::round($vramAllocation / 1MB))MB" -ForegroundColor Green

    # Configure encode allocation
    $encodeAllocation = [math]::round($(18446744073709551615 / $divider))
    Set-VMGPUPartitionAdapter -VMName $VMName -MinPartitionEncode $encodeAllocation -MaxPartitionEncode $encodeAllocation -OptimalPartitionEncode $encodeAllocation
    Write-Host "Encode allocation configured" -ForegroundColor Green

    # Configure decode allocation
    $decodeAllocation = [math]::round($(1000000000 / $divider))
    Set-VMGpuPartitionAdapter -VMName $VMName -MinPartitionDecode $decodeAllocation -MaxPartitionDecode $decodeAllocation -OptimalPartitionDecode $decodeAllocation
    Write-Host "Decode allocation set to: $([math]::round($decodeAllocation / 1MB))MB" -ForegroundColor Green

    # Configure compute allocation
    $computeAllocation = [math]::round($(1000000000 / $divider))
    Set-VMGpuPartitionAdapter -VMName $VMName -MinPartitionCompute $computeAllocation -MaxPartitionCompute $computeAllocation -OptimalPartitionCompute $computeAllocation
    Write-Host "Compute allocation set to: $([math]::round($computeAllocation / 1MB))MB" -ForegroundColor Green

    Write-Host "SUCCESS: GPU partition adapter configured successfully!" -ForegroundColor Green
    Write-Host "You can now start the VM and GPU acceleration should be available" -ForegroundColor Cyan

} catch {
    Write-Host "ERROR: Failed to configure GPU partition adapter: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
