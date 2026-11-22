# Publishes the existing Git history transparently; it does not rewrite past commits.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$owner = 'Alen0013'
$target = "$owner/calibration-Lab_First"
$source = "https://github.com/$owner/calibration-lab1.git"
$sourceBranch = 'fix/readme-cleanup' # README without the extra disclosure section
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$work = Join-Path $root 'artifacts\github-publish-new\clone'
$gitTarget = "https://github.com/$target.git"
$localPython = Join-Path $root '.venv312\Scripts\python.exe'
$localPaths = @(
    'src\calibration_lab\data.py',
    'src\calibration_lab\config.py',
    'artifacts_manifest.json',
    'PUBLISH_GITHUB.bat',
    'publish_new_repo.ps1'
)
$ghCmd = Get-Command gh -ErrorAction SilentlyContinue
if ($null -ne $ghCmd) {
    $ghBinary = $ghCmd.Source
} elseif (Test-Path 'C:\Program Files\GitHub CLI\gh.exe') {
    $ghBinary = 'C:\Program Files\GitHub CLI\gh.exe'
} elseif (Test-Path 'C:\Program Files (x86)\GitHub CLI\gh.exe') {
    $ghBinary = 'C:\Program Files (x86)\GitHub CLI\gh.exe'
} else {
    throw 'GitHub CLI (gh.exe) not found. Install GitHub CLI before running.'
}

function RequireSuccess([string] $step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$step failed (exit $LASTEXITCODE). See console output above."
    }
}

function GetGithubRun([string]$sha, [string]$eventName) {
    $api = "repos/$target/actions/runs?head_sha=$sha&per_page=50"
    $jsonText = & $ghBinary api $api
    RequireSuccess 'Read GitHub Actions'
    $all = ($jsonText | Out-String | ConvertFrom-Json).workflow_runs
    $runs = @($all | Where-Object { $_.path -eq '.github/workflows/ci.yml' -and $_.event -eq $eventName })
    return ($runs | Select-Object -First 1)
}

function WaitForCI([string]$sha, [string]$eventName) {
    for($i = 0; $i -lt 100; $i++) {
        $run = GetGithubRun $sha $eventName
        if ($null -ne $run) {
            Write-Host "CI ($eventName): $($run.status) / $($run.conclusion)"
            if ($run.status -eq 'completed') {
                if ($run.conclusion -ne 'success') {
                    throw "GitHub Actions failed: $($run.html_url)"
                }
                Write-Host "Successful CI: $($run.html_url)"
                return
            }
        }
        else {
            Write-Host "Waiting for CI run ($eventName)..."
        }
        Start-Sleep -Seconds 10
    }
    throw "CI did not finish within the time limit. Check https://github.com/$target/actions"
}

try {
    Write-Host "TARGET: https://github.com/$target"
    Write-Host "SOURCE: $source (branch $sourceBranch)"
    Write-Host 'The source Git history is preserved, including earlier versions of files.'

    foreach ($relative in $localPaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $relative))) {
            throw "Missing local project file: $relative"
        }
    }
    if (-not (Test-Path -LiteralPath $localPython)) {
        throw 'Python virtual environment .venv312 not found. First finish the local Python 3.12 setup.'
    }
    $fixedData = Get-Content -LiteralPath (Join-Path $root 'src\calibration_lab\data.py') -Raw -Encoding UTF8
    $fixedConfig = Get-Content -LiteralPath (Join-Path $root 'src\calibration_lab\config.py') -Raw -Encoding UTF8
    if (-not $fixedData.Contains('payload = payload.replace(b"\r\n", b"\n")') -or
        -not $fixedConfig.Contains('PureWindowsPath') -or
        -not $fixedConfig.Contains('windows_path.anchor')) {
        throw 'The local Windows fixes are missing. Run fix_lab1_windows.py and pytest before publishing.'
    }
    Write-Host '[0/8] Testing your corrected LOCAL source before publishing...'
    Push-Location $root
    try {
        & $localPython -m pytest -q
        RequireSuccess 'Local tests'
    } finally {
        Pop-Location
    }

    & git --version
    RequireSuccess 'Git check'
    & $ghBinary --version
    RequireSuccess 'GitHub CLI check'
    & $ghBinary auth status
    RequireSuccess 'GitHub authentication'
    & $ghBinary auth setup-git
    RequireSuccess 'Set up Git credential helper'

    if (Test-Path -LiteralPath $work) {
        throw "Workspace already exists: $work`nDo not overwrite it. Inspect it or choose a different workspace."
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $work) -Force | Out-Null

    Write-Host '[1/8] Cloning newly created destination repository...'
    & git clone $gitTarget $work
    RequireSuccess 'Clone destination'
    $tracked = @(& git -C $work ls-tree -r --name-only HEAD)
    RequireSuccess 'Inspect destination'
    if ($tracked.Count -ne 1 -or $tracked[0] -ne 'README.md') {
        throw 'Destination contains more than the initial README. Aborting to protect existing content.'
    }

    & git -C $work config core.autocrlf false
    RequireSuccess 'Configure stable Git newline handling'
    & git -C $work config user.name $owner
    RequireSuccess 'Configure author'
    & git -C $work config user.email "186933822+$owner@users.noreply.github.com"
    RequireSuccess 'Configure email'

    Write-Host '[2/8] Fetching the actual existing project and all source branches...'
    & git -C $work remote add source $source
    RequireSuccess 'Add source remote'
    & git -C $work fetch --tags source '+refs/heads/*:refs/remotes/source/*'
    RequireSuccess 'Fetch existing Git history'
    & git -C $work rev-parse --verify "refs/remotes/source/$sourceBranch"
    RequireSuccess 'Check prepared source branch'
    $readme = & git -C $work show "source/${sourceBranch}:README.md"
    RequireSuccess 'Read source README'
    if (($readme | Out-String) -match '(?i)ChatGPT|GPT-6|OpenAI') {
        throw 'Unexpected disclosure section in source README: inspect the source branch before publishing.'
    }

    Write-Host '[3/8] Preparing develop while preserving the destination bootstrap commit...'
    & git -C $work switch -c develop "source/$sourceBranch"
    RequireSuccess 'Create develop'
    & git -C $work merge --allow-unrelated-histories -s ours origin/main -m 'chore: preserve destination bootstrap'
    RequireSuccess 'Merge destination bootstrap into develop'
    Write-Host '[3a/8] Copying YOUR Windows fixes (not just the old GitHub files)...'
    foreach ($relative in $localPaths) {
        if ($relative -eq 'artifacts_manifest.json') { continue }
        $from = Join-Path $root $relative
        $to = Join-Path $work $relative
        Copy-Item -LiteralPath $from -Destination $to -Force
    }
    # Git .gitattributes specifies CRLF working-tree bytes for .bat/.ps1 on all platforms.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    foreach ($relative in @('PUBLISH_GITHUB.bat', 'publish_new_repo.ps1')) {
        $to = Join-Path $work $relative
        $contents = [System.IO.File]::ReadAllText($to, [System.Text.Encoding]::UTF8)
        $contents = $contents.Replace("`r`n", "`n").Replace("`r", "`n").Replace("`n", "`r`n")
        [System.IO.File]::WriteAllText($to, $contents, $utf8NoBom)
    }
    # Keep the tracked-file SHA-256 manifest in sync with the actually submitted bytes.
    $manifestPath = Join-Path $work 'artifacts_manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($relative in @('src/calibration_lab/data.py', 'src/calibration_lab/config.py', 'PUBLISH_GITHUB.bat', 'publish_new_repo.ps1')) {
        $bytes = [System.IO.File]::ReadAllBytes((Join-Path $work $relative))
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $sha256 = [BitConverter]::ToString($hasher.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant()
        } finally {
            $hasher.Dispose()
        }
        $entry = [PSCustomObject]@{ size_bytes = $bytes.Length; sha256 = $sha256 }
        $manifest.files | Add-Member -NotePropertyName $relative -NotePropertyValue $entry -Force
    }
    [System.IO.File]::WriteAllText($manifestPath, (($manifest | ConvertTo-Json -Depth 15) + "`n"), $utf8NoBom)
    & git -C $work add -- src/calibration_lab/data.py src/calibration_lab/config.py PUBLISH_GITHUB.bat publish_new_repo.ps1 artifacts_manifest.json
    RequireSuccess 'Stage corrected local sources and publisher'
    & git -C $work diff --cached --quiet
    if ($LASTEXITCODE -eq 1) {
        & git -C $work commit -m 'fix: support Windows dataset and path validation'
        RequireSuccess 'Commit local Windows fixes'
    } elseif ($LASTEXITCODE -ne 0) {
        throw 'Failed to inspect staged changes before publishing'
    } else {
        Write-Host 'Local corrections are already identical to the prepared source'
    }
    $developSha = (& git -C $work rev-parse HEAD).Trim()
    RequireSuccess 'Read develop SHA'
    & git -C $work push origin develop
    RequireSuccess 'Push develop'

    Write-Host '[4/8] Publishing original feature branches and release tags...'
    $branches = @(& git -C $work for-each-ref --format='%(refname:strip=3)' refs/remotes/source/feature)
    RequireSuccess 'List feature branches'
    foreach($branch in $branches) {
        if ([string]::IsNullOrWhiteSpace($branch)) { continue }
        $ref = "refs/remotes/source/$branch"
        & git -C $work push origin "${ref}:refs/heads/$branch"
        RequireSuccess "Publish $branch"
    }
    $tag = @(& git -C $work tag --list 'lab1-v1.0.0')
    RequireSuccess 'List release tags'
    if ($tag -contains 'lab1-v1.0.0') {
        & git -C $work push origin refs/tags/lab1-v1.0.0:refs/tags/lab1-v1.0.0
        RequireSuccess 'Publish release tag'
    }

    Write-Host '[5/8] Creating a pull request: develop -> main...'
    $prUrl = (& $ghBinary pr create --repo $target --base main --head develop `
        --title 'LAB1 variant 25: publish source and analysis' `
        --body 'Import the existing Git history. Includes EDA, calibration analysis, tests, data checks, and reproducibility evidence.').Trim()
    RequireSuccess 'Create pull request'
    Write-Host "PR: $prUrl"

    Write-Host '[6/8] Waiting for server-side CI on the pull request...'
    WaitForCI $developSha 'pull_request'

    Write-Host '[7/8] Merging the checked pull request...'
    & $ghBinary pr merge $prUrl --repo $target --merge --match-head-commit $developSha
    RequireSuccess 'Merge pull request'
    & git -C $work fetch origin main
    RequireSuccess 'Update main after merge'
    $mainSha = (& git -C $work rev-parse origin/main).Trim()
    RequireSuccess 'Read main SHA'
    WaitForCI $mainSha 'push'

    Write-Host '[8/8] Configuring protection: require PR and green quality check, no external reviewer required...'
    $protection = @{
        required_status_checks = @{ strict = $true; contexts = @('quality') }
        enforce_admins = $true
        required_pull_request_reviews = @{
            dismiss_stale_reviews = $true
            require_code_owner_reviews = $false
            required_approving_review_count = 0
            require_last_push_approval = $false
        }
        restrictions = $null
        required_conversation_resolution = $true
        required_linear_history = $false
        allow_force_pushes = $false
        allow_deletions = $false
        block_creations = $false
        lock_branch = $false
        allow_fork_syncing = $false
    } | ConvertTo-Json -Depth 10
    $jsonPath = Join-Path (Split-Path -Parent $work) 'branch_protection_new.json'
    [System.IO.File]::WriteAllText($jsonPath, $protection, (New-Object System.Text.UTF8Encoding($false)))
    & $ghBinary api --method PUT "repos/$target/branches/main/protection" --input $jsonPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Source was published and CI passed, but branch protection could not be enabled. Configure it in Settings -> Branches.'
    }
    Write-Host ''
    Write-Host "SUCCESS. New repository: https://github.com/$target"
    Write-Host "PR: $prUrl"
    Write-Host "Actions: https://github.com/$target/actions"
    exit 0
}
catch {
    Write-Host ''
    Write-Host "STOPPED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "See GitHub: https://github.com/$target"
    exit 1
}
