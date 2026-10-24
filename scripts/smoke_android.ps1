param(
    [string]$Serial
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sdk = $env:ANDROID_HOME
if (-not $sdk) { $sdk = $env:ANDROID_SDK_ROOT }
if (-not $sdk) { throw 'Set ANDROID_HOME to the Android SDK root.' }

$adb = Join-Path $sdk 'platform-tools\adb.exe'
if (-not (Test-Path -LiteralPath $adb -PathType Leaf)) {
    throw "adb.exe not found under Android SDK: $sdk"
}

$repository = Split-Path -Parent $PSScriptRoot
$apk = Join-Path $repository 'android\app\build\outputs\apk\debug\app-debug.apk'
if (-not (Test-Path -LiteralPath $apk -PathType Leaf)) {
    throw "Debug APK not found: $apk. Build :app:assembleDebug first."
}

function Invoke-Adb([string[]]$Arguments) {
    $output = & $adb -s $Serial @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "adb $($Arguments -join ' ') failed: $($output -join ' ')"
    }
    return ($output -join "`n").Trim()
}

if (-not $Serial) {
    $listing = & $adb devices -l 2>&1
    if ($LASTEXITCODE -ne 0) { throw "adb devices failed: $($listing -join ' ')" }
    $connected = @($listing | ForEach-Object {
        if ($_ -match '^([^\s]+)\s+device(?:\s|$)') { $Matches[1] }
    })
    if ($connected.Count -ne 1) {
        throw "Expected exactly one connected device; found $($connected.Count). Pass -Serial to select one."
    }
    $Serial = $connected[0]
}
if ($Serial -notmatch '^[A-Za-z0-9._:-]+$') { throw 'Invalid adb serial.' }

$api = Invoke-Adb -Arguments @('shell', 'getprop', 'ro.build.version.sdk')
$abi = Invoke-Adb -Arguments @('shell', 'getprop', 'ro.product.cpu.abi')
$install = Invoke-Adb -Arguments @('install', '-r', $apk)
if ($install -notmatch '(?m)^Success$') { throw "APK install did not report success: $install" }

$launch = Invoke-Adb -Arguments @('shell', 'am', 'start', '-W', '-n', 'dev.runvori.app/.MainActivity')
if ($launch -notmatch '(?m)^Status: ok$') { throw "Activity launch did not report success: $launch" }

$processId = ''
for ($attempt = 0; $attempt -lt 10; $attempt++) {
    $processId = Invoke-Adb -Arguments @('shell', 'pidof', 'dev.runvori.app')
    if ($processId -match '^\d+$') { break }
    Start-Sleep -Milliseconds 500
}
if ($processId -notmatch '^\d+$') { throw 'Runvori process was not running after launch.' }

$activities = Invoke-Adb -Arguments @('shell', 'dumpsys', 'activity', 'activities')
if ($activities -notmatch 'topResumedActivity=.*dev\.runvori\.app/\.MainActivity') {
    throw 'Runvori MainActivity is not the top resumed activity.'
}

Write-Output "PASS serial=$Serial api=$api abi=$abi package=dev.runvori.app activity=.MainActivity pid=$processId"
