param(
    [switch]$Kill,
    [switch]$AllPlannerProcesses
)

$pattern = 'mesen_smb_checkpoint_planner_v(11|12|13|14|15|16|17|18|19|20|21|22)\.py'
$shadowToken = '(?:^|\s)--shadow-worker(?:\s|$)'
$authorityToken = '(?:^|\s)--authority-worker(?:\s|$)'

$processes = @(Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -match $pattern -and
        ($AllPlannerProcesses -or $_.CommandLine -match $shadowToken)
    } |
    Sort-Object ProcessId)

if (-not $processes) {
    if ($AllPlannerProcesses) {
        Write-Host 'No fami-pixel planner Python processes found.'
    }
    else {
        Write-Host 'No fami-pixel shadow workers found.'
    }
    exit 0
}

$rows = foreach ($process in $processes) {
    $role = if ($process.CommandLine -match $authorityToken) {
        'authority'
    }
    elseif ($process.CommandLine -match $shadowToken) {
        'shadow'
    }
    else {
        'supervisor'
    }

    [PSCustomObject]@{
        ProcessId       = $process.ProcessId
        ParentProcessId = $process.ParentProcessId
        Role            = $role
        CreationDate    = $process.CreationDate
        CommandLine     = $process.CommandLine
    }
}

$rows | Format-Table -AutoSize

if (-not $Kill) {
    Write-Host ''
    if ($AllPlannerProcesses) {
        Write-Host 'Dry run only. Stop any active fami-pixel planner, then re-run with -Kill -AllPlannerProcesses to remove these processes.'
    }
    else {
        Write-Host 'Dry run only. Stop any active fami-pixel planner, then re-run with -Kill to remove these workers.'
    }
    exit 0
}

Write-Host ''
Write-Host "Stopping $($processes.Count) fami-pixel planner process(es)..."
foreach ($process in $processes) {
    try {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
        Start-Sleep -Milliseconds 100
        $stillThere = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ProcessId)" -ErrorAction SilentlyContinue
        if ($stillThere) {
            Write-Warning "PID $($process.ProcessId) accepted Stop-Process but is still present; try taskkill /PID $($process.ProcessId) /T /F and inspect its role/parent."
        }
        else {
            Write-Host "stopped PID $($process.ProcessId)"
        }
    }
    catch {
        Write-Warning "failed to stop PID $($process.ProcessId): $($_.Exception.Message)"
    }
}
