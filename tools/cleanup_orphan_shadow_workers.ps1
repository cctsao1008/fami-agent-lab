param(
    [switch]$Kill
)

$pattern = 'mesen_smb_checkpoint_planner_v(11|12|13|14|15|16|17|18|19)\.py'

$workers = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -match $pattern -and
        $_.CommandLine -match '--shadow-worker'
    } |
    Sort-Object ProcessId

if (-not $workers) {
    Write-Host 'No fami-pixel shadow workers found.'
    exit 0
}

$workers | Select-Object ProcessId, ParentProcessId, Name, CommandLine | Format-Table -AutoSize

if (-not $Kill) {
    Write-Host ''
    Write-Host 'Dry run only. Stop any active fami-pixel planner, then re-run with -Kill to remove these workers.'
    exit 0
}

Write-Host ''
Write-Host "Stopping $($workers.Count) fami-pixel shadow worker(s)..."
foreach ($worker in $workers) {
    try {
        Stop-Process -Id $worker.ProcessId -Force -ErrorAction Stop
        Write-Host "stopped PID $($worker.ProcessId)"
    }
    catch {
        Write-Warning "failed to stop PID $($worker.ProcessId): $($_.Exception.Message)"
    }
}
