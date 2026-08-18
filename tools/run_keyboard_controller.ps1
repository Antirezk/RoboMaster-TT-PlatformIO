param(
    [string]$Port = "COM8",
    [int]$HttpPort = 8765
)

$PlatformIoPython = Join-Path $env:USERPROFILE ".platformio\penv\Scripts\python.exe"
$Controller = Join-Path $PSScriptRoot "keyboard_controller.py"

if (-not (Test-Path -LiteralPath $PlatformIoPython)) {
    Write-Error "PlatformIO Python was not found: $PlatformIoPython"
    exit 1
}

& $PlatformIoPython $Controller --port $Port --http-port $HttpPort
