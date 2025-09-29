# GPU-PV

These scripts are for configuring GPU-PV (GPU Partitioning) in a Windows 10/11 VM on a Windows host. GPU-PV allows you to share a single GPU between the host and VM, providing near-native performance for the VM. This is especially useful for gaming, 3D rendering, and other GPU-intensive tasks.

This is required for local development purposes of the Heroes Replay project.

The Heroes Replay project takes control of HID devices such as the Keyboard and Mouse, when debugging or running this software locally without a VM it can cause the host machine to become unresponsive.

To counter-act this, we want a GPU enabled host to be able to launch and run the Heroes Client, but also provide an isolated environment for the Heroes Replay project to run in, so that the host machine is not affected by the Heroes Replay project.

## What This Toolkit Does

This toolkit provides essential scripts for:

1. **GPU Driver File Management** - Copy GPU driver files from host to VM
2. **Drive Letter Assignment** - Automatically assign drive letters to mounted VHDs  
3. **GPU Partition Setup** - Configure GPU partition adapters with proper memory allocation
4. **Driver Updates** - Update VM GPU drivers when host drivers are updated

## Core Scripts

* **`Update-VMGpuPartitionDriver.ps1`** - Main script to update GPU drivers in a VM
* **`Set-VMGpuPartitionAdapter.ps1`** - Configure GPU partition adapter and memory allocation  
* **`Add-VMGpuPartitionAdapterFiles.psm1`** - Module for copying GPU driver files to VM disk
* **`PreChecks.ps1`** - Validate GPU compatibility and list available partitionable GPUs

## Prerequisites

* Windows 10 20H1+ Pro, Enterprise or Education OR Windows 11 Pro, Enterprise or Education  
* Desktop Computer with dedicated NVIDIA/AMD GPU or Integrated Intel GPU that supports GPU-PV
* Latest GPU driver from manufacturer (Intel.com, NVIDIA.com, AMD.com)  
* [Hyper-V fully enabled](https://docs.microsoft.com/en-us/virtualization/hyper-v-on-windows/quick-start/enable-hyper-v) on the Windows OS (requires reboot)
* PowerShell execution policy allowing scripts: `Set-ExecutionPolicy unrestricted` in PowerShell as Administrator

## Quick Start

### 1. Check GPU Compatibility

```powershell
.\PreChecks.ps1
```

### 2. Update VM GPU Drivers

```powershell
# Basic usage - script will handle VHD mounting and drive assignment
.\Update-VMGpuPartitionDriver.ps1 -VMName "YourVM" -GPUName "AUTO"

# Specify exact GPU name
.\Update-VMGpuPartitionDriver.ps1 -VMName "YourVM" -GPUName "NVIDIA GeForce RTX 4080"

# Advanced usage with disk number (for already mounted VHDs)
.\Update-VMGpuPartitionDriver.ps1 -VMName "YourVM" -GPUName "AUTO" -DiskNumber 2
```

### 3. Configure GPU Partition Adapter

```powershell
# Add GPU partition adapter with 50% allocation
.\Set-VMGpuPartitionAdapter.ps1 -VMName "YourVM" -GPUName "AUTO" -GPUResourceAllocationPercentage 50

# Use specific GPU with 75% allocation  
.\Set-VMGpuPartitionAdapter.ps1 -VMName "YourVM" -GPUName "NVIDIA GeForce RTX 4080" -GPUResourceAllocationPercentage 75
```

## Workflow

1. **Run PreChecks.ps1** to validate your GPU supports partitioning
2. **Use Update-VMGpuPartitionDriver.ps1** to copy driver files to your VM
3. **Use Set-VMGpuPartitionAdapter.ps1** to configure the GPU partition adapter
4. **Start your VM** and enjoy hardware-accelerated graphics

## Common Use Cases

### Initial GPU Setup for New VM

```powershell
# 1. Check compatibility
.\PreChecks.ps1

# 2. Copy drivers to VM
.\Update-VMGpuPartitionDriver.ps1 -VMName "MyVM" -GPUName "AUTO"

# 3. Configure GPU partition
.\Set-VMGpuPartitionAdapter.ps1 -VMName "MyVM" -GPUName "AUTO" -GPUResourceAllocationPercentage 50
```

### Update Drivers After Host GPU Driver Update

```powershell
# After updating host GPU drivers, update VM drivers
.\Update-VMGpuPartitionDriver.ps1 -VMName "MyVM" -GPUName "AUTO"
```

### Working with Already Mounted VHDs

```powershell
# If your VM's VHD is already mounted as disk 2
.\Update-VMGpuPartitionDriver.ps1 -VMName "MyVM" -GPUName "AUTO" -DiskNumber 2
```

#### New Parameters and Usage

```powershell
# Basic usage with drive letter
.\Update-VMGpuPartitionDriver.ps1 -VMName "YourVM" -GPUName "NVIDIA GeForce RTX 4080" -DriveLetter "C:"

# Advanced usage with disk number (automatically assigns drive letter)
.\Update-VMGpuPartitionDriver.ps1 -VMName "YourVM" -GPUName "AUTO" -DiskNumber 2

# Example for already mounted VHD scenario
.\Update-VMGpuPartitionDriver.ps1 -VMName "Heroes Replay" -GPUName "NVIDIA GeForce RTX 4080"
```
