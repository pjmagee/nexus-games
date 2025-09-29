<#
.SYNOPSIS
    Updates GPU partition drivers for a Hyper-V virtual machine.

.DESCRIPTION
    This script updates GPU partition drivers for a specified VM by mounting the VM's VHD,
    copying necessary GPU driver files from the host system, and ensuring proper driver
    installation. The script handles both running and stopped VMs automatically.

.PARAMETER VMName
    The name of the virtual machine to update GPU drivers for.

.PARAMETER GPUName
    The name of the GPU as it appears in Device Manager. Use "AUTO" to automatically
    detect the first available partitionable GPU.

.PARAMETER Hostname
    The hostname of the Hyper-V host. Defaults to the current computer name.

.EXAMPLE
    .\Update-VMGpuPartitionDriver.ps1 -VMName "MyVM" -GPUName "NVIDIA GeForce RTX 4080"

.EXAMPLE
    .\Update-VMGpuPartitionDriver.ps1 -VMName "MyVM" -GPUName "AUTO"

.NOTES
    - Requires administrative privileges
    - VM will be stopped if running, then restarted if it was originally running
    - Ensure the VM has a GPU partition adapter configured before running this script
#>

Param (
    [Parameter(Mandatory=$true)]
    [string]$VMName = "Heroes Replay",

    [Parameter(Mandatory=$true)]
    [string]$GPUName = "NVIDIA GeForce RTX 4080",

    [string]$Hostname = $ENV:COMPUTERNAME
)

# Import required module
Import-Module $PSScriptRoot\Add-VMGpuPartitionAdapterFiles.psm1

# Validate VM exists
try {
    $VM = Get-VM -VMName $VMName -ErrorAction Stop
    Write-Host "Found VM: $VMName" -ForegroundColor Green
} catch {
    Write-Host "ERROR: VM '$VMName' not found. Please check the VM name." -ForegroundColor Red
    exit 1
}

# Get VHD information
try {
    $VHD = Get-VHD -VMId $VM.VMId -ErrorAction Stop
    Write-Host "VHD Path: $($VHD.Path)" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Could not retrieve VHD information for VM '$VMName'." -ForegroundColor Red
    exit 1
}

# Check if VM was running
$state_was_running = ($VM.State -eq "Running")
Write-Host "VM initial state: $($VM.State)"

# Stop VM if not already stopped
if ($VM.State -ne "Off") {
    Write-Host "Stopping VM..." -ForegroundColor Yellow
    Stop-VM -Name $VMName -Force

    # Wait for VM to stop
    do {
        Start-Sleep -Seconds 3
        $VM = Get-VM -VMName $VMName
        Write-Host "Waiting for VM to stop... Current state: $($VM.State)"
    } while ($VM.State -ne "Off")

    Write-Host "VM stopped successfully." -ForegroundColor Green
}

# Mount VHD and assign drive letter
Write-Host "Mounting VHD..." -ForegroundColor Yellow
$DriveLetter = $null
$DiskNumber = $null

try {
    # Check if VHD is already mounted
    $VHDInfo = Get-VHD -Path $VHD.Path
    if ($VHDInfo.Attached) {
        Write-Host "VHD is already mounted (Disk Number: $($VHDInfo.DiskNumber))" -ForegroundColor Green
        $DiskNumber = $VHDInfo.DiskNumber
    } else {
        # Mount the VHD
        $MountResult = Mount-VHD -Path $VHD.Path -PassThru
        $DiskNumber = $MountResult.DiskNumber
        Write-Host "Successfully mounted VHD. Disk Number: $DiskNumber" -ForegroundColor Green
    }

    # Ensure we have a drive letter assigned
    $MainPartition = Get-Disk -Number $DiskNumber | Get-Partition |
                     Where-Object {$_.Type -eq "Basic" -and $_.Size -gt 1GB} |
                     Sort-Object Size -Descending | Select-Object -First 1

    if (-not $MainPartition) {
        throw "Could not find main Windows partition on the VHD"
    }

    if ($MainPartition.DriveLetter) {
        $DriveLetter = $MainPartition.DriveLetter
        Write-Host "Found existing drive letter: $DriveLetter" -ForegroundColor Green
    } else {
        # Assign a drive letter
        Write-Host "No drive letter found. Assigning one..." -ForegroundColor Yellow
        $UsedLetters = Get-WmiObject Win32_LogicalDisk | Select-Object -ExpandProperty DeviceID
        $AvailableLetters = 'Z','Y','X','W','V','U','T','S','R','Q','P','O','N','M','L','K','J','I','H','G','F','E' |
                           Where-Object { "$($_):" -notin $UsedLetters }

        if (-not $AvailableLetters) {
            throw "No available drive letters found"
        }

        $NewLetter = $AvailableLetters[0]
        Write-Host "Assigning drive letter $NewLetter..." -ForegroundColor Yellow
        $MainPartition | Set-Partition -NewDriveLetter $NewLetter
        Start-Sleep -Seconds 2

        # Verify assignment
        $UpdatedPartition = Get-Partition -DiskNumber $DiskNumber -PartitionNumber $MainPartition.PartitionNumber
        if ($UpdatedPartition.DriveLetter) {
            $DriveLetter = $UpdatedPartition.DriveLetter
            Write-Host "Successfully assigned drive letter: $DriveLetter" -ForegroundColor Green
        } else {
            throw "Failed to assign drive letter"
        }
    }
} catch {
    Write-Host "ERROR: Failed to mount VHD or assign drive letter: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

"Copying GPU Files - this could take a while..."
if ($DriveLetter) {
    Write-Host "Using drive letter: $DriveLetter"
    Add-VMGPUPartitionAdapterFiles -hostname $Hostname -DriveLetter $DriveLetter -GPUName $GPUName
} else {
    Write-Host "ERROR: No drive letter available. Cannot proceed without proper drive access."
    exit 1
}

"Dismounting Drive..."
Dismount-VHD -Path $VHD.Path

If ($state_was_running){
    "Previous State was running so starting VM..."
    Start-VM $VMName
    }

"Done..."
