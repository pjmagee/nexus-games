<#
.SYNOPSIS
    Copies GPU partition adapter driver files to a VM's disk.

.DESCRIPTION
    This function copies the necessary GPU driver files from the host system to a virtual machine's disk
    to enable GPU partition adapter functionality. It handles both volume paths and disk number scenarios
    with automatic drive letter assignment.

.PARAMETER Path
    The path to the VM's disk. Can be a volume path (\\?\Volume{guid}\) or drive letter path (C:\).

.PARAMETER DiskNumber
    The disk number of the mounted VHD. When provided, the function will assign a drive letter automatically.

.EXAMPLE
    Add-VMGpuPartitionAdapterFiles -Path "C:\"
    Copies GPU files to the C: drive of a mounted VM disk.

.EXAMPLE
    Add-VMGpuPartitionAdapterFiles -DiskNumber 2
    Copies GPU files to disk number 2, automatically assigning a drive letter.

.NOTES
    Requires administrative privileges and a compatible NVIDIA GPU.
    The VM must be shut down before running this function.
#>
function Add-VMGpuPartitionAdapterFiles() {
    param(
        [Parameter(Mandatory=$false)]
        [string]$hostname = $ENV:COMPUTERNAME,
        
        [Parameter(Mandatory=$false)]
        [string]$DriveLetter,
        
        [Parameter(Mandatory=$false)]
        [string]$GPUName,
        
        [Parameter(Mandatory=$false)]
        [int]$DiskNumber
    )

    # Determine volume path based on provided parameters
    $VolumePath = $null
    
    if ($DriveLetter) {
        # Handle drive letter parameter
        if (!($DriveLetter -like "*:*")) {
            $DriveLetter = $DriveLetter + ":"
        }
        $VolumePath = $DriveLetter
        Write-Host "Using provided drive letter: $VolumePath" -ForegroundColor Green
        
    } elseif ($PSBoundParameters.ContainsKey('DiskNumber')) {
        # Handle disk number parameter - assign drive letter
        Write-Host "Processing disk number: $DiskNumber" -ForegroundColor Yellow
        
        try {
            $Partition = Get-Disk -Number $DiskNumber | Get-Partition | 
                        Where-Object {$_.Type -eq "Basic" -and $_.Size -gt 1GB} | 
                        Sort-Object Size -Descending | Select-Object -First 1
            
            if (-not $Partition) {
                Write-Host "ERROR: Could not find main Windows partition on disk $DiskNumber" -ForegroundColor Red
                return $false
            }
            
            if (-not $Partition.DriveLetter) {
                # Find an available drive letter
                $UsedLetters = Get-WmiObject Win32_LogicalDisk | Select-Object -ExpandProperty DeviceID
                $AvailableLetters = 'Z','Y','X','W','V','U','T','S','R','Q','P','O','N','M','L','K','J','I','H','G','F','E' | 
                                   Where-Object { "$($_):" -notin $UsedLetters }
                
                if (-not $AvailableLetters) {
                    Write-Host "ERROR: No available drive letters found" -ForegroundColor Red
                    return $false
                }
                
                $NewLetter = $AvailableLetters[0]
                Write-Host "Assigning drive letter $NewLetter to partition..." -ForegroundColor Yellow
                $Partition | Set-Partition -NewDriveLetter $NewLetter
                Start-Sleep -Seconds 2  # Wait for assignment
                
                # Refresh partition info
                $Partition = Get-Partition -DiskNumber $DiskNumber -PartitionNumber $Partition.PartitionNumber
            }
            
            if ($Partition.DriveLetter) {
                $VolumePath = "$($Partition.DriveLetter):"
                Write-Host "Using drive letter: $VolumePath" -ForegroundColor Green
            } else {
                Write-Host "ERROR: Unable to assign or find drive letter for disk $DiskNumber" -ForegroundColor Red
                return $false
            }
        } catch {
            Write-Host "ERROR: Failed to process disk number $DiskNumber`: $($_.Exception.Message)" -ForegroundColor Red
            return $false
        }
    } else {
        Write-Host "ERROR: Either DriveLetter or DiskNumber parameter must be provided" -ForegroundColor Red
        return $false
    }

    # GPU detection and driver enumeration
    if ($GPUName -eq "AUTO") {
        $PartitionableGPUList = Get-WmiObject -Class "Msvm_PartitionableGpu" -ComputerName $env:COMPUTERNAME -Namespace "ROOT\virtualization\v2"
        $DevicePathName = $PartitionableGPUList.Name | Select-Object -First 1
        $GPU = Get-PnpDevice | Where-Object {($_.DeviceID -like "*$($DevicePathName.Substring(8,16))*") -and ($_.Status -eq "OK")} | Select-Object -First 1
        $GPUName = $GPU.Friendlyname
        $GPUServiceName = $GPU.Service 
    } else {
        $GPU = Get-PnpDevice | Where-Object {($_.Name -eq "$GPUName") -and ($_.Status -eq "OK")} | Select-Object -First 1
        $GPUServiceName = $GPU.Service
    }
    
    # Get Third Party drivers used, that are not provided by Microsoft and presumably included in the OS
    Write-Host "INFO   : Finding and copying driver files for $GPUName to VM. This could take a while..." -ForegroundColor Yellow

    $Drivers = Get-WmiObject Win32_PNPSignedDriver | Where-Object {$_.DeviceName -eq "$GPUName"}

    New-Item -ItemType Directory -Path "$VolumePath\windows\system32\HostDriverStore" -Force | Out-Null

    # Copy directory associated with sys file 
    $servicePath = (Get-WmiObject Win32_SystemDriver | Where-Object {$_.Name -eq "$GPUServiceName"}).Pathname
    $ServiceDriverDir = $servicepath.split('\')[0..5] -join('\')
    $ServicedriverDest = ("$VolumePath" + "\" + $($servicepath.split('\')[1..5] -join('\'))).Replace("DriverStore","HostDriverStore")
    if (!(Test-Path $ServicedriverDest)) {
        Copy-item -path "$ServiceDriverDir" -Destination "$ServicedriverDest" -Recurse
    }

    # Process each driver
    foreach ($d in $drivers) {
        $DriverFiles = @()
        $ModifiedDeviceID = $d.DeviceID -replace "\\", "\\"
        $Antecedent = "\\" + $hostname + "\ROOT\cimv2:Win32_PNPSignedDriver.DeviceID=""$ModifiedDeviceID"""
        $DriverFiles += Get-WmiObject Win32_PNPSignedDriverCIMDataFile | Where-Object {$_.Antecedent -eq $Antecedent}
        $DriverName = $d.DeviceName
        # Note: DriverID available for future use: $d.DeviceID
        
        if ($DriverName -like "NVIDIA*") {
            New-Item -ItemType Directory -Path "$VolumePath\Windows\System32\drivers\Nvidia Corporation\" -Force | Out-Null
        }
        foreach ($i in $DriverFiles) {
            $path = $i.Dependent.Split("=")[1] -replace '\\\\', '\'
            $path2 = $path.Substring(1,$path.Length-2)
            # Note: File version info available via: (Get-Item -Path $path2).VersionInfo.FileVersion
            
            if ($path2 -like "c:\windows\system32\driverstore\*") {
                $DriverDir = $path2.split('\')[0..5] -join('\')
                $driverDest = ("$VolumePath" + "\" + $($path2.split('\')[1..5] -join('\'))).Replace("driverstore","HostDriverStore")
                if (!(Test-Path $driverDest)) {
                    Copy-item -path "$DriverDir" -Destination "$driverDest" -Recurse
                }
            } else {
                $ParseDestination = $path2.Replace("c:", "$VolumePath")
                $Destination = $ParseDestination.Substring(0, $ParseDestination.LastIndexOf('\'))
                if (!$(Test-Path -Path $Destination)) {
                    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
                }
                Copy-Item $path2 -Destination $Destination -Force
            }
        }
    }
    
    Write-Host "SUCCESS: GPU driver files copied successfully to $VolumePath" -ForegroundColor Green
    return $true
}