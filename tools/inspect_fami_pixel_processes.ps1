$pattern = 'mesen_smb_checkpoint_planner_v(11|12|13|14|15|16|17|18|19|20|21)\.py'

$processes = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -match $pattern
    } |
    Sort-Object ProcessId

if (-not $processes) {
    Write-Host 'No fami-pixel planner Python processes found.'
    exit 0
}

$rows = foreach ($process in $processes) {
    $role = if ($process.CommandLine -match '--shadow-worker') {
        'shadow'
    }
    elseif ($process.CommandLine -match '--authority-worker') {
        'authority'
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

$rows | Format-List
