# ================================================================
# PORTABLE UNCENSORED AI - AUTOMATED USB SETUP SCRIPT
# ================================================================
# Multi-Model Edition: Choose one or more AI models to install!
# Supports preset models + custom HuggingFace GGUF downloads.
# ================================================================

$ErrorActionPreference = "Continue"
$USB_Drive = (Get-Item $MyInvocation.MyCommand.Path).Directory.Parent.FullName

# -----------------------------------------------------------------
# MODEL CATALOG (shared JSON config)
# -----------------------------------------------------------------
$modelsConfigPath = "$USB_Drive\Shared\config\models.json"
if (-Not (Test-Path $modelsConfigPath)) {
    Write-Host "ERROR: Missing shared model config at $modelsConfigPath" -ForegroundColor Red
    exit 1
}

try {
    $modelsJson = Get-Content -Raw -Path $modelsConfigPath | ConvertFrom-Json
    $ModelCatalog = @()
    foreach ($m in $modelsJson.desktop_models) {
        $ModelCatalog += @{
            Num      = [int]$m.num
            Name     = [string]$m.name
            File     = [string]$m.file
            URL      = [string]$m.url
            Size     = [string]$m.size
            MinBytes = [long]$m.min_bytes
            Local    = [string]$m.local
            Label    = [string]$m.label
            Badge    = [string]$m.badge
            Prompt   = [string]$m.prompt
        }
    }
} catch {
    Write-Host "ERROR: Failed to parse shared model config: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# -----------------------------------------------------------------
# HELPER: Check USB free space (returns GB)
# -----------------------------------------------------------------
function Get-USBFreeSpaceGB {
    try {
        $driveLetter = (Get-Item $USB_Drive).PSDrive.Name
        $drive = Get-PSDrive $driveLetter -ErrorAction SilentlyContinue
        if ($drive) {
            return [math]::Round($drive.Free / 1GB, 1)
        }
    } catch {}
    return -1
}

# -----------------------------------------------------------------
# HELPER: Verify downloaded file size
# -----------------------------------------------------------------
function Test-DownloadedFile {
    param([string]$Path, [long]$MinSize)
    if (-Not (Test-Path $Path)) { return $false }
    $fileSize = (Get-Item $Path).Length
    return $fileSize -gt $MinSize
}

# -----------------------------------------------------------------
# HELPER: Check drive root for existing model files
# -----------------------------------------------------------------
$DriveRoot = (Get-Item $USB_Drive).PSDrive.Root

function Copy-ModelFromDriveRoot {
    param([string]$FileName, [string]$DestPath, [long]$MinSize)
    $src = Join-Path $DriveRoot $FileName
    if (Test-Path $src) {
        $sizeBytes = (Get-Item $src).Length
        if ($sizeBytes -gt $MinSize) {
            $sizeGB = [math]::Round($sizeBytes / 1GB, 2)
            Write-Host ""
            Write-Host "  Found '$FileName' in drive root ($sizeGB GB)." -ForegroundColor Cyan
            $use = Read-Host "  Use this file instead of downloading? (yes/no)"
            if ($use.Trim().ToLower() -eq "yes" -or $use.Trim().ToLower() -eq "y") {
                Copy-Item -Path $src -Destination $DestPath -Force
                Write-Host "      Copied from drive root." -ForegroundColor Green
                return $true
            }
        }
    }
    return $false
}

# ================================================================
# START
# ================================================================
Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   PORTABLE AI USB - Multi-Model Setup                    " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

# Show USB free space
$freeGB = Get-USBFreeSpaceGB
if ($freeGB -gt 0) {
    Write-Host "  USB Free Space: $freeGB GB" -ForegroundColor DarkGray
    Write-Host ""
}

# =================================================================
# STEP 1: MODEL SELECTION MENU
# =================================================================
Write-Host "[1/7] Choose your AI model(s):" -ForegroundColor Yellow
Write-Host ""

foreach ($m in $ModelCatalog) {
    $numStr   = "  [$($m.Num)]"
    $nameStr  = " $($m.Name)"
    $sizeStr  = " (~$($m.Size) GB)"

    if ($m.Label -eq "UNCENSORED") {
        $labelStr   = " [UNCENSORED]"
        $labelColor = "Red"
    } else {
        $labelStr   = " [STANDARD]"
        $labelColor = "DarkCyan"
    }

    $badgeStr = ""
    if ($m.Badge) { $badgeStr = " - $($m.Badge)" }

    Write-Host $numStr  -ForegroundColor Yellow    -NoNewline
    Write-Host $nameStr -ForegroundColor White     -NoNewline
    Write-Host $sizeStr -ForegroundColor DarkGray  -NoNewline
    Write-Host $labelStr -ForegroundColor $labelColor -NoNewline
    Write-Host $badgeStr -ForegroundColor Magenta
}

Write-Host ""
Write-Host "  [C] CUSTOM - Enter your own HuggingFace GGUF URL" -ForegroundColor Green
Write-Host ""
Write-Host "  ------------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Enter number(s) separated by commas  (e.g. 1,3)" -ForegroundColor Gray
Write-Host "  Type 'all' for every preset model" -ForegroundColor Gray
Write-Host "  Type 'c' to add a custom model" -ForegroundColor Gray
Write-Host "  Mix them!  (e.g. 1,3,c)" -ForegroundColor Gray
Write-Host ""

$UserChoice = Read-Host "  Your choice"

if ([string]::IsNullOrWhiteSpace($UserChoice)) {
    Write-Host ""
    Write-Host "  No input! Defaulting to [1] Gemma 2 2B (recommended)..." -ForegroundColor Yellow
    $UserChoice = "1"
}

# -----------------------------------------------------------------
# Parse the user's selection
# -----------------------------------------------------------------
$SelectedModels = @()
$HasCustom = $false

# Check for 'all'
if ($UserChoice.Trim().ToLower() -eq "all") {
    $SelectedModels = @($ModelCatalog)
} else {
    $tokens = $UserChoice -split ","
    foreach ($token in $tokens) {
        $t = $token.Trim().ToLower()
        if ($t -eq "c" -or $t -eq "custom") {
            $HasCustom = $true
        } elseif ($t -match '^\d+$') {
            $num = [int]$t
            $found = $ModelCatalog | Where-Object { $_.Num -eq $num }
            if ($found) {
                # Avoid duplicates
                $alreadyAdded = $SelectedModels | Where-Object { $_.Num -eq $num }
                if (-Not $alreadyAdded) {
                    $SelectedModels += $found
                }
            } else {
                Write-Host "  Invalid number '$num' - skipping (valid: 1-$($ModelCatalog.Count))" -ForegroundColor Red
            }
        } else {
            Write-Host "  Unrecognized input '$t' - skipping" -ForegroundColor Red
        }
    }
}

# -----------------------------------------------------------------
# Handle custom model input
# -----------------------------------------------------------------
if ($HasCustom) {
    Write-Host ""
    Write-Host "  ---- Custom Model Setup ----" -ForegroundColor Green
    Write-Host "  Paste a direct link to a .gguf file from HuggingFace." -ForegroundColor Gray
    Write-Host "  Example: https://huggingface.co/user/model-GGUF/resolve/main/model-Q4_K_M.gguf" -ForegroundColor DarkGray
    Write-Host ""

    $customURL = Read-Host "  GGUF URL"

    if ([string]::IsNullOrWhiteSpace($customURL)) {
        Write-Host "  No URL entered - skipping custom model." -ForegroundColor Red
    } elseif ($customURL -notmatch "\.gguf") {
        Write-Host "  WARNING: URL does not end in .gguf - this may not be a valid model file." -ForegroundColor Red
        $proceed = Read-Host "  Try anyway? (yes/no)"
        if ($proceed.Trim().ToLower() -ne "yes" -and $proceed.Trim().ToLower() -ne "y") {
            Write-Host "  Skipping custom model." -ForegroundColor Yellow
            $customURL = $null
        }
    }

    if ($customURL) {
        # Extract filename from URL
        $customFile = $customURL.Split("/")[-1].Split("?")[0]
        if (-Not $customFile.EndsWith(".gguf")) { $customFile = "$customFile.gguf" }

        $customLocalName = Read-Host "  Give it a short name (e.g. mymodel-local)"
        if ([string]::IsNullOrWhiteSpace($customLocalName)) {
            $customLocalName = "custom-local"
        }
        # Sanitize: lowercase, replace spaces with dashes
        $customLocalName = $customLocalName.Trim().ToLower() -replace '\s+', '-'
        if ($customLocalName -notmatch '-local$') { $customLocalName = "$customLocalName-local" }

        $customPrompt = Read-Host "  System prompt (press Enter for default)"
        if ([string]::IsNullOrWhiteSpace($customPrompt)) {
            $customPrompt = "You are a helpful AI assistant."
        }

        $customModel = @{
            Num      = 99
            Name     = "Custom: $customFile"
            File     = $customFile
            URL      = $customURL.Trim()
            Size     = "?"
            MinBytes = 100000000   # At least 100 MB to be considered valid
            Local    = $customLocalName
            Label    = "CUSTOM"
            Badge    = ""
            Prompt   = $customPrompt
        }

        $SelectedModels += $customModel
        Write-Host "  Custom model added!" -ForegroundColor Green
    }
}

# -----------------------------------------------------------------
# Validate we have at least one model
# -----------------------------------------------------------------
if ($SelectedModels.Count -eq 0) {
    Write-Host ""
    Write-Host "  ERROR: No models selected!" -ForegroundColor Red
    Write-Host "  Please run the installer again and pick at least one model." -ForegroundColor Red
    Write-Host ""
    Write-Host "Press any key to exit..." -ForegroundColor Yellow
    $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") | Out-Null
    exit 1
}

# -----------------------------------------------------------------
# USB space warning (if selecting 3+ models or all)
# -----------------------------------------------------------------
$totalSizeGB = 0
foreach ($m in $SelectedModels) {
    if ($m.Size -ne "?") { $totalSizeGB += [double]$m.Size }
}

if ($SelectedModels.Count -ge 3 -or $UserChoice.Trim().ToLower() -eq "all") {
    Write-Host ""
    Write-Host "  =============================================" -ForegroundColor Red
    Write-Host "  WARNING: You selected $($SelectedModels.Count) models!" -ForegroundColor Red
    Write-Host "  Estimated download: ~$totalSizeGB GB" -ForegroundColor Red
    $neededGB = [math]::Ceiling($totalSizeGB + 4)
    Write-Host "  USB drive needs at least ~$neededGB GB free!" -ForegroundColor Red

    if ($freeGB -gt 0 -and $freeGB -lt $neededGB) {
        Write-Host ""
        Write-Host "  You only have $freeGB GB free - this may NOT fit!" -ForegroundColor Yellow
    }

    Write-Host "  =============================================" -ForegroundColor Red
    Write-Host ""
    $confirm = Read-Host "  Continue? (yes/no)"
    if ($confirm.Trim().ToLower() -ne "yes" -and $confirm.Trim().ToLower() -ne "y") {
        Write-Host "  Cancelled. Run the installer again to choose fewer models." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Press any key to exit..." -ForegroundColor Yellow
        $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") | Out-Null
        exit
    }
}

# -----------------------------------------------------------------
# Show selection summary
# -----------------------------------------------------------------
Write-Host ""
Write-Host "  Selected $($SelectedModels.Count) model(s):" -ForegroundColor Green
foreach ($m in $SelectedModels) {
    $sizeInfo = if ($m.Size -ne "?") { " (~$($m.Size) GB)" } else { "" }
    Write-Host "    + $($m.Name)$sizeInfo" -ForegroundColor White
}
Write-Host ""

# =================================================================
# STEP 2: Create folder structure
# =================================================================
Write-Host "[2/7] Verifying USB folder structure..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "$USB_Drive\Shared\models" | Out-Null
New-Item -ItemType Directory -Force -Path "$USB_Drive\Shared\bin" | Out-Null
New-Item -ItemType Directory -Force -Path "$USB_Drive\Shared\vendor" | Out-Null
Write-Host "      Done." -ForegroundColor Green

# =================================================================
# STEP 2b: Ensure 7-Zip portable extractor is available
# =================================================================
Write-Host ""
Write-Host "[2b/7] Preparing 7-Zip portable extractor..." -ForegroundColor Yellow

$SevenZipDir = "$USB_Drive\Shared\bin\7z"
$SevenZipExe = "$SevenZipDir\7za.exe"

function Expand-ZipArchive {
    param([string]$ZipPath, [string]$DestDir)
    if (Test-Path $SevenZipExe) {
        & $SevenZipExe x $ZipPath -o"$DestDir" -y | Out-Null
        return
    }
    # Fallback to tar (Windows 10+ built-in)
    try {
        $null = & tar.exe -xf $ZipPath -C $DestDir 2>$null
        if ($LASTEXITCODE -eq 0) { return }
    } catch {}
    # Final fallback
    Expand-Archive -Path $ZipPath -DestinationPath $DestDir -Force
}

if (Test-Path $SevenZipExe) {
    Write-Host "      7-Zip portable already available." -ForegroundColor Green
} else {
    New-Item -ItemType Directory -Force -Path $SevenZipDir | Out-Null

    $SevenZipr = "$SevenZipDir\7zr.exe"
    $SevenZipExtra = "$SevenZipDir\7z-extra.7z"

    Write-Host "      Downloading 7zr bootstrap..." -ForegroundColor DarkGray
    curl.exe -L --ssl-no-revoke -o $SevenZipr "https://www.7-zip.org/a/7zr.exe" 2>$null

    if (Test-Path $SevenZipr) {
        $extraUrls = @(
            "https://www.7-zip.org/a/7z2601-extra.7z",
            "https://www.7-zip.org/a/7z2408-extra.7z",
            "https://www.7-zip.org/a/7z2201-extra.7z"
        )
        $downloadedExtra = $false
        foreach ($url in $extraUrls) {
            Write-Host "      Downloading 7-Zip extra package..." -ForegroundColor DarkGray
            curl.exe -L --ssl-no-revoke -o $SevenZipExtra $url 2>$null
            if (Test-Path $SevenZipExtra) {
                $downloadedExtra = $true
                break
            }
        }

        if ($downloadedExtra) {
            & $SevenZipr -y e $SevenZipExtra "x64\7za.exe" -o"$SevenZipDir" | Out-Null
            if (Test-Path $SevenZipExe) {
                Remove-Item $SevenZipr -Force -ErrorAction SilentlyContinue
                Remove-Item $SevenZipExtra -Force -ErrorAction SilentlyContinue
                Write-Host "      7-Zip portable ready!" -ForegroundColor Green
            } else {
                Write-Host "      WARNING: Failed to extract 7za.exe. Will use built-in tools." -ForegroundColor Yellow
            }
        } else {
            Write-Host "      WARNING: Could not download 7-Zip extra. Will use built-in tools." -ForegroundColor Yellow
        }
    } else {
        Write-Host "      WARNING: Could not download 7zr.exe. Will use built-in tools." -ForegroundColor Yellow
    }
}

# =================================================================
# STEP 2c: Install Microsoft Visual C++ Redistributable (required by SD.cpp)
# =================================================================
Write-Host ""
Write-Host "[2c/7] Checking Microsoft Visual C++ Redistributable..." -ForegroundColor Yellow

function Test-VCRedistInstalled {
    $sys32 = "$env:SystemRoot\System32"
    return (Test-Path "$sys32\vcruntime140.dll") -and (Test-Path "$sys32\vcruntime140_1.dll") -and (Test-Path "$sys32\msvcp140.dll")
}

if (Test-VCRedistInstalled) {
    Write-Host "      VC++ Redistributable already installed." -ForegroundColor Green
} else {
    Write-Host "      VC++ Redistributable missing. Downloading installer..." -ForegroundColor DarkGray
    $VCRedistURL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
    $VCRedistDest = "$USB_Drive\Shared\bin\vc_redist.x64.exe"
    curl.exe -L --ssl-no-revoke --progress-bar $VCRedistURL -o $VCRedistDest
    if (Test-Path $VCRedistDest) {
        Write-Host "      Installing VC++ Redistributable (admin required)..." -ForegroundColor Yellow
        try {
            $proc = Start-Process -FilePath $VCRedistDest -ArgumentList "/install","/quiet","/norestart" -Wait -PassThru
            if ($proc.ExitCode -eq 0 -or $proc.ExitCode -eq 3010) {
                Write-Host "      VC++ Redistributable installed successfully!" -ForegroundColor Green
            } else {
                Write-Host "      WARNING: VC++ installer exited with code $($proc.ExitCode). You may need to install it manually." -ForegroundColor Yellow
                Write-Host "      Download from: $VCRedistURL" -ForegroundColor DarkGray
            }
        } catch {
            Write-Host "      WARNING: Could not install VC++ Redistributable (admin rights may be required)." -ForegroundColor Yellow
            Write-Host "      Download from: $VCRedistURL" -ForegroundColor DarkGray
        }
    } else {
        Write-Host "      WARNING: Could not download VC++ Redistributable. Image generation may fail with missing DLL errors." -ForegroundColor Yellow
        Write-Host "      Download from: $VCRedistURL" -ForegroundColor DarkGray
    }
}

# =================================================================
# STEP 3: Download optional UI vendor assets for offline mode
# =================================================================
Write-Host ""
Write-Host "[3/7] Downloading UI assets (offline markdown/pdf/fonts)..." -ForegroundColor Yellow

$vendorDir = "$USB_Drive\Shared\vendor"
$vendorScript = "$USB_Drive\Shared\scripts\download-ui-assets.ps1"
if (Test-Path $vendorScript) {
    powershell -ExecutionPolicy Bypass -File $vendorScript -VendorDir $vendorDir
} else {
    Write-Host "      WARNING: Shared vendor bootstrap script not found. Skipping." -ForegroundColor Yellow
}

# =================================================================
# STEP 4: Download selected AI models
# =================================================================
Write-Host ""
Write-Host "[4/7] Downloading AI Model(s)..." -ForegroundColor Yellow

$downloadErrors = @()
$modelIndex = 0

foreach ($m in $SelectedModels) {
    $modelIndex++
    $dest = "$USB_Drive\Shared\models\$($m.File)"
    $sizeInfo = if ($m.Size -ne "?") { "(~$($m.Size) GB)" } else { "" }

    Write-Host ""
    Write-Host "  ($modelIndex/$($SelectedModels.Count)) $($m.Name) $sizeInfo" -ForegroundColor Yellow

    # Check if already downloaded
    if (Test-DownloadedFile -Path $dest -MinSize $m.MinBytes) {
        Write-Host "      Already downloaded! Skipping..." -ForegroundColor Green
        continue
    }

    # Also check for legacy Dolphin Q5_K_M if downloading Dolphin Q4_K_M
    if ($m.Local -eq "dolphin-local") {
        $legacyFile = "$USB_Drive\Shared\models\dolphin-2.9-llama3-8b-Q5_K_M.gguf"
        if (Test-DownloadedFile -Path $legacyFile -MinSize 4000000000) {
            Write-Host "      Found existing Dolphin Q5_K_M - using that instead!" -ForegroundColor Green
            $m.File = "dolphin-2.9-llama3-8b-Q5_K_M.gguf"
            continue
        }
    }

    # Check drive root for existing model
    if (Copy-ModelFromDriveRoot -FileName $m.File -DestPath $dest -MinSize $m.MinBytes) {
        continue
    }

    Write-Host "      Downloading... This may take a while. Do NOT close this window!" -ForegroundColor Magenta

    # Download with retry (up to 2 attempts)
    $success = $false
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        if ($attempt -gt 1) {
            Write-Host "      Retry attempt $attempt..." -ForegroundColor Yellow
        }

        curl.exe -L --ssl-no-revoke --progress-bar $m.URL -o $dest

        if (Test-DownloadedFile -Path $dest -MinSize $m.MinBytes) {
            $success = $true
            break
        } elseif (Test-Path $dest) {
            $actualSize = [math]::Round((Get-Item $dest).Length / 1GB, 2)
            Write-Host "      File seems too small ($actualSize GB). May be incomplete." -ForegroundColor Red
        }
    }

    if ($success) {
        Write-Host "      Download complete!" -ForegroundColor Green
    } else {
        $downloadErrors += $m.Name
        Write-Host "      ERROR: Download failed for $($m.Name)!" -ForegroundColor Red
        Write-Host "      You can manually download it from:" -ForegroundColor DarkGray
        Write-Host "      $($m.URL)" -ForegroundColor DarkGray
        Write-Host "      Place the file in: $USB_Drive\Shared\models\" -ForegroundColor DarkGray
    }
}

# =================================================================
# STEP 5: Create Modelfile configuration for each model
# =================================================================
Write-Host ""
Write-Host "[5/7] Creating AI model configurations..." -ForegroundColor Yellow

foreach ($m in $SelectedModels) {
    $modelfilePath = "$USB_Drive\Shared\models\Modelfile-$($m.Local)"
    $modelfileContent = @"
FROM ./$($m.File)
PARAMETER temperature 0.7
PARAMETER top_p 0.9
SYSTEM $($m.Prompt)
"@
    Set-Content -Path $modelfilePath -Value $modelfileContent -Force -Encoding UTF8
    Write-Host "      Config: $($m.Name) -> $($m.Local)" -ForegroundColor Green
}

# Also create a legacy "Modelfile" pointing to the first selected model (backward compat)
$firstModel = $SelectedModels[0]
$legacyModelfile = @"
FROM ./$($firstModel.File)
PARAMETER temperature 0.7
PARAMETER top_p 0.9
SYSTEM $($firstModel.Prompt)
"@
Set-Content -Path "$USB_Drive\Shared\models\Modelfile" -Value $legacyModelfile -Force -Encoding UTF8

# Save installed models list for reference
$installedList = $SelectedModels | ForEach-Object { "$($_.Local)|$($_.Name)|$($_.Label)" }
Set-Content -Path "$USB_Drive\Shared\models\installed-models.txt" -Value ($installedList -join "`n") -Force -Encoding UTF8
Write-Host "      Saved model list to installed-models.txt" -ForegroundColor DarkGray

# =================================================================
# STEP 6: Download Ollama (the AI engine)
# =================================================================
Write-Host ""
Write-Host "[6/7] Downloading Ollama AI Engine (Windows)..." -ForegroundColor Yellow
$OllamaURL  = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"
$OllamaDest = "$USB_Drive\Shared\bin\ollama-windows-amd64.zip"
$TempOllamaDir = "$USB_Drive\Shared\bin\temp_ollama"

# Check if Ollama is healthy (has both the exe AND the runners)
$isHealthy = $false
if (Test-Path "$USB_Drive\Shared\bin\ollama-windows.exe") {
    $runnerPaths = @(
        "$USB_Drive\Shared\bin\lib\ollama\llama-server.exe",
        "$USB_Drive\Shared\bin\lib\ollama\cpu\llama-server.exe"
    )
    foreach ($rp in $runnerPaths) {
        if (Test-Path $rp) { $isHealthy = $true; break }
    }
}

if ($isHealthy) {
    Write-Host "      Ollama AI engine is healthy! Skipping..." -ForegroundColor Green
} else {
    Write-Host "      Ollama engine is missing runners or is incomplete. Repairing..." -ForegroundColor Yellow
    # We need the engine (exe + lib). Prefer extraction from ZIP to get both.
    $gotZip = $false
    
    # 1. Check if ZIP exists in Shared/bin
    if (Test-Path $OllamaDest) { $gotZip = $true }
    
    # 2. Check drive root for ZIP
    if (-not $gotZip) {
        $driveRootZip = Join-Path $DriveRoot "ollama-windows-amd64.zip"
        if (Test-Path $driveRootZip) {
            Write-Host "      Found Ollama ZIP in drive root. Using it..." -ForegroundColor Cyan
            Copy-Item -Path $driveRootZip -Destination $OllamaDest -Force
            $gotZip = $true
        }
    }
    
    # 3. Download if still missing
    if (-not $gotZip) {
        Write-Host "      Downloading Ollama ZIP (1.2GB) using BITS..." -ForegroundColor Magenta
        Write-Host "      This is more reliable for large files. Please wait..." -ForegroundColor Gray
        try {
            Start-BitsTransfer -Source $OllamaURL -Destination $OllamaDest -ErrorAction Stop
            if (Test-Path $OllamaDest) { $gotZip = $true }
        } catch {
            Write-Host "      BITS failed. Falling back to curl..." -ForegroundColor Yellow
            curl.exe -L --ssl-no-revoke --progress-bar $OllamaURL -o $OllamaDest
            if (Test-Path $OllamaDest) { $gotZip = $true }
        }
    }
    
    # 4. Extract if we have a ZIP
    if ($gotZip) {
        Write-Host "      Verifying ZIP integrity..." -ForegroundColor Yellow
        $zipValid = $false
        if (Test-Path $SevenZipExe) {
            # Kill any process that might be locking the ZIP
            Stop-Process -Name "ollama-windows" -Force -ErrorAction SilentlyContinue
            
            & $SevenZipExe t $OllamaDest | Out-Null
            if ($LASTEXITCODE -eq 0) { $zipValid = $true }
        } else {
            $zipValid = $true 
        }

        if (-not $zipValid) {
            Write-Host "      ERROR: Ollama ZIP is corrupted. Deleting and retrying..." -ForegroundColor Red
            Remove-Item $OllamaDest -Force
            Write-Host "      Please run the installer again to re-download the engine." -ForegroundColor Yellow
            return
        }

        Write-Host "      Extracting Ollama engine (exe + lib)..." -ForegroundColor Yellow
        try {
            # FORCE CLEAN the lib folder to prevent "llama-server not found" due to partial extraction
            if (Test-Path "$USB_Drive\Shared\bin\lib") { 
                Write-Host "      Cleaning old engine files..." -ForegroundColor DarkGray
                Remove-Item "$USB_Drive\Shared\bin\lib" -Recurse -Force -ErrorAction SilentlyContinue
            }

            if (Test-Path $TempOllamaDir) { Remove-Item $TempOllamaDir -Recurse -Force }
            New-Item -ItemType Directory -Force -Path $TempOllamaDir | Out-Null
            Expand-ZipArchive -ZipPath $OllamaDest -DestDir $TempOllamaDir
            
            # Move ollama.exe
            if (Test-Path "$TempOllamaDir\ollama.exe") {
                Move-Item -Path "$TempOllamaDir\ollama.exe" -Destination "$USB_Drive\Shared\bin\ollama-windows.exe" -Force
            }
            
            # Move the 'lib' folder (contains runners like llama-server.exe)
            if (Test-Path "$TempOllamaDir\lib") {
                Move-Item -Path "$TempOllamaDir\lib" -Destination "$USB_Drive\Shared\bin\lib" -Force
            }

            # Verify extraction success
            $runnerFound = $false
            $checkPaths = @(
                "$USB_Drive\Shared\bin\lib\ollama\llama-server.exe",
                "$USB_Drive\Shared\bin\lib\ollama\cpu\llama-server.exe",
                "$USB_Drive\Shared\bin\lib\ollama\cuda_v12\llama-server.exe"
            )
            foreach ($cp in $checkPaths) {
                if (Test-Path $cp) { $runnerFound = $true; break }
            }

            if (-not $runnerFound) {
                Write-Host "      WARNING: Extraction finished but llama-server.exe was not found." -ForegroundColor Yellow
                Write-Host "      The engine might be incomplete." -ForegroundColor Red
            } else {
                Write-Host "      Ollama Engine Setup Complete!" -ForegroundColor Green
            }

            # Cleanup
            Remove-Item $TempOllamaDir -Force -Recurse -ErrorAction SilentlyContinue
        } catch {
            Write-Host "      ERROR: Failed to extract Ollama: $($_.Exception.Message)" -ForegroundColor Red
        }
    } else {
        # Fallback: if we can't get the zip, try to at least get the exe from root
        if (-not (Test-Path "$USB_Drive\Shared\bin\ollama-windows.exe")) {
            if (Copy-ModelFromDriveRoot -FileName "ollama-windows.exe" -DestPath "$USB_Drive\Shared\bin\ollama-windows.exe" -MinSize 10000000) {
                Write-Host "      WARNING: Only extracted ollama.exe. 'lib' folder (GPU support) may be missing." -ForegroundColor Yellow
            } else {
                Write-Host "      ERROR: Could not download or find Ollama engine!" -ForegroundColor Red
                $downloadErrors += "Ollama Engine"
            }
        }
    }
}

# =================================================================
# STEP 6b: Download Stable Diffusion Image Engine
# =================================================================
Write-Host ""
Write-Host "[6b/7] Downloading Stable Diffusion Image Engine (Windows)..." -ForegroundColor Yellow
$SDZipURL = "https://github.com/leejet/stable-diffusion.cpp/releases/download/master-656-0e4ee04/sd-master-0e4ee04-bin-win-avx2-x64.zip"
$SDZipDest = "$USB_Drive\Shared\bin\sd-windows.zip"
$SDDir = "$USB_Drive\Shared\bin\sd-windows"

if (Test-Path "$SDDir\sd.exe") {
    Write-Host "      Stable Diffusion engine already installed! Skipping..." -ForegroundColor Green
} else {
    curl.exe -L --ssl-no-revoke --progress-bar $SDZipURL -o $SDZipDest
    if (Test-Path $SDZipDest) {
        Write-Host "      Extracting Stable Diffusion engine..." -ForegroundColor Yellow
        try {
            New-Item -ItemType Directory -Force -Path $SDDir | Out-Null
            Expand-ZipArchive -ZipPath $SDZipDest -DestDir $SDDir
            # If the archive had a top-level folder, flatten it
            $subDirs = Get-ChildItem -Path $SDDir -Directory -ErrorAction SilentlyContinue
            if ($subDirs.Count -eq 1) {
                $subDir = $subDirs[0].FullName
                Get-ChildItem -Path $subDir | Move-Item -Destination $SDDir -Force
                Remove-Item -Path $subDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            Remove-Item $SDZipDest -Force -ErrorAction SilentlyContinue
            Write-Host "      Stable Diffusion engine installed!" -ForegroundColor Green
        } catch {
            Write-Host "      ERROR: Failed to extract SD engine." -ForegroundColor Red
            $downloadErrors += "Stable Diffusion Engine"
        }
    } else {
        Write-Host "      ERROR: Stable Diffusion engine download failed!" -ForegroundColor Red
        $downloadErrors += "Stable Diffusion Engine"
    }
}

# =================================================================
# STEP 6c: Download CyberRealistic Image Model
# =================================================================
Write-Host ""
Write-Host "[6c/7] Downloading CyberRealistic Image Model (~1.99 GB)..." -ForegroundColor Yellow
$ImageModelURL = "https://huggingface.co/cyberdelia/CyberRealistic/resolve/main/CyberRealistic_V3.3_FP16.safetensors"
$ImageModelDest = "$USB_Drive\Shared\models\CyberRealistic_V3.3_FP16.safetensors"
$ImageModelMinBytes = 2000000000

if (Test-DownloadedFile -Path $ImageModelDest -MinSize $ImageModelMinBytes) {
    Write-Host "      CyberRealistic model already downloaded! Skipping..." -ForegroundColor Green
} elseif (Copy-ModelFromDriveRoot -FileName "CyberRealistic_V3.3_FP16.safetensors" -DestPath $ImageModelDest -MinSize $ImageModelMinBytes) {
    # copied from drive root
} else {
    Write-Host "      Downloading... This may take a while. Do NOT close this window!" -ForegroundColor Magenta
    curl.exe -L --ssl-no-revoke --progress-bar $ImageModelURL -o $ImageModelDest
    if (Test-DownloadedFile -Path $ImageModelDest -MinSize $ImageModelMinBytes) {
        Write-Host "      CyberRealistic model downloaded successfully!" -ForegroundColor Green
    } else {
        Write-Host "      ERROR: CyberRealistic model download failed or is incomplete!" -ForegroundColor Red
        $downloadErrors += "CyberRealistic Image Model"
    }
}

# =================================================================
# STEP 6d: Download Piper TTS Engine
# =================================================================
Write-Host ""
Write-Host "[6d/7] Downloading Piper TTS Engine (Windows)..." -ForegroundColor Yellow
# Fixed correct release tag
$PiperZipURL = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip"
$PiperZipDest = "$USB_Drive\Shared\bin\piper-windows.zip"
$PiperDir = "$USB_Drive\Shared\bin\piper"

if (Test-Path "$PiperDir\piper.exe") {
    Write-Host "      Piper TTS engine already installed! Skipping..." -ForegroundColor Green
} else {
    # Ensure any corrupted/old download is removed first
    if (Test-Path $PiperZipDest) { Remove-Item $PiperZipDest -Force }
    
    Write-Host "      Downloading Piper engine (via PowerShell)..." -ForegroundColor DarkGray
    try {
        # Using Invoke-WebRequest for better redirect handling on some systems
        Invoke-WebRequest -Uri $PiperZipURL -OutFile $PiperZipDest -ErrorAction Stop
    } catch {
        Write-Host "      PowerShell download failed. Trying curl..." -ForegroundColor DarkGray
        curl.exe -fL --ssl-no-revoke --progress-bar $PiperZipURL -o $PiperZipDest
    }
    
    if (Test-Path $PiperZipDest) {
        $zipSize = (Get-Item $PiperZipDest).Length
        if ($zipSize -lt 1000000) { # Smaller than 1MB is likely a 404 or error page
            Write-Host "      ERROR: Downloaded file is too small ($zipSize bytes). It might be corrupted." -ForegroundColor Red
            Remove-Item $PiperZipDest -Force
            $downloadErrors += "Piper TTS Engine Download (File Corrupt)"
        } else {
            Write-Host "      Extracting Piper TTS engine..." -ForegroundColor Yellow
            try {
                $tempExtract = "$PiperDir\temp_extract"
                if (Test-Path $tempExtract) { Remove-Item $tempExtract -Recurse -Force }
                New-Item -ItemType Directory -Force -Path $tempExtract | Out-Null
                
                # Use 7-Zip portable (the project's included tool)
                if (Test-Path $SevenZipExe) {
                    & $SevenZipExe x "$PiperZipDest" "-o$tempExtract" -y | Out-Null
                } else {
                    Expand-Archive -Path $PiperZipDest -DestinationPath $tempExtract -Force
                }
                
                # Move all files from the nested 'piper' folder to the main $PiperDir
                $nestedPiper = Get-ChildItem -Path $tempExtract -Filter "piper" -Directory | Select-Object -First 1
                if ($nestedPiper) {
                    Get-ChildItem -Path $nestedPiper.FullName | Move-Item -Destination $PiperDir -Force
                } else {
                    Get-ChildItem -Path $tempExtract | Move-Item -Destination $PiperDir -Force
                }
                
                # Final verification
                if (Test-Path "$PiperDir\piper.exe") {
                    Write-Host "      Piper TTS engine installed successfully!" -ForegroundColor Green
                    Remove-Item $PiperZipDest -Force -ErrorAction SilentlyContinue
                } else {
                    throw "piper.exe not found after extraction"
                }

                # Cleanup
                Remove-Item $tempExtract -Recurse -Force -ErrorAction SilentlyContinue
            } catch {
                Write-Host "      ERROR: Failed to extract Piper TTS engine. Details: $($_.Exception.Message)" -ForegroundColor Red
                $downloadErrors += "Piper TTS Engine Extraction"
            }
        }
    } else {
        Write-Host "      ERROR: Piper TTS engine download failed!" -ForegroundColor Red
        $downloadErrors += "Piper TTS Engine Download"
    }
}

# =================================================================
# STEP 6e: Download Piper Voice Models
# =================================================================
Write-Host ""
Write-Host "[6e/7] Downloading TTS Voice Models..." -ForegroundColor Yellow
$TtsModelsDir = "$USB_Drive\Shared\models\tts"
if (-not (Test-Path $TtsModelsDir)) { New-Item -ItemType Directory -Force -Path $TtsModelsDir | Out-Null }

$Voices = @(
    @{ name="Amy (US Female - Clear)"; id="en_US-amy-medium"; url="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium" },
    @{ name="Lili (US Female - Soft)"; id="en_US-lili-medium"; url="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lili/medium" },
    @{ name="Kusal (US Female - Natural)"; id="en_US-kusal-medium"; url="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/kusal/medium" },
    @{ name="Arctic (US Female - Smooth)"; id="en_US-arctic-medium"; url="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/arctic/medium" },
    @{ name="Lessac (US Female - High Quality)"; id="en_US-lessac-medium"; url="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium" },
    @{ name="Alan (UK Male - British)"; id="en_GB-alan-medium"; url="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_GB/alan/medium" }
)

foreach ($v in $Voices) {
    $vId = $v.id
    $vName = $v.name
    $onnxDest = "$TtsModelsDir\$vId.onnx"
    $jsonDest = "$TtsModelsDir\$vId.onnx.json"
    
    if (Test-Path $onnxDest) {
        Write-Host "      Voice '$vName' already exists. Skipping..." -ForegroundColor Green
    } else {
        Write-Host "      Downloading voice: $vName..." -ForegroundColor DarkGray
        curl.exe -L --ssl-no-revoke --progress-bar "$($v.url)/$vId.onnx" -o $onnxDest
        curl.exe -L --ssl-no-revoke --progress-bar "$($v.url)/$vId.onnx.json" -o $jsonDest
        
        if (Test-Path $onnxDest) {
            Write-Host "      '$vName' downloaded successfully!" -ForegroundColor Green
        } else {
            Write-Host "      ERROR: Failed to download '$vName'." -ForegroundColor Red
            $downloadErrors += "TTS Voice: $vName"
        }
    }
}

# =================================================================
# STEP 7: IMPORT ALL SELECTED MODELS INTO OLLAMA ENGINE
# =================================================================
Write-Host ""
Write-Host "[7/7] Importing AI models into the Ollama engine..." -ForegroundColor Yellow

if (-Not (Test-Path "$USB_Drive\Shared\bin\ollama-windows.exe")) {
    Write-Host "      ERROR: Ollama not found! Cannot import models." -ForegroundColor Red
    Write-Host "      Please re-run the installer to download Ollama." -ForegroundColor Red
} else {
    $env:OLLAMA_MODELS = "$USB_Drive\Shared\models\ollama_data"
    $env:OLLAMA_HOST = "127.0.0.1:11435"
    New-Item -ItemType Directory -Force -Path $env:OLLAMA_MODELS | Out-Null
    Set-Location "$USB_Drive\Shared\models"

    # Kill any dangling/unresponsive Ollama processes that cause hangs
    Stop-Process -Name "ollama-windows" -Force -ErrorAction SilentlyContinue
    Stop-Process -Name "ollama" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    $modelsToImport = @()
    foreach ($m in $SelectedModels) {
        $ggufPath = "$USB_Drive\Shared\models\$($m.File)"
        if (Test-Path $ggufPath) {
            $modelsToImport += $m
        } else {
            Write-Host "      Skipping $($m.Name) - GGUF file not found (download may have failed)" -ForegroundColor Red
        }
    }

    if ($modelsToImport.Count -gt 0) {
        Write-Host "      Starting Ollama temporarily to perform import..." -ForegroundColor DarkGray
        
        # Explicitly set the library path so it finds the runners (lib/ollama/...)
        $env:OLLAMA_MODELS = "$USB_Drive\Shared\models\ollama_data"
        $env:OLLAMA_HOST = "127.0.0.1:11435"
        $env:OLLAMA_LIBRARY_PATH = "$USB_Drive\Shared\bin\lib\ollama"
        
        # Kill any existing ones first
        Stop-Process -Name "ollama-windows" -Force -ErrorAction SilentlyContinue
        
        $ServerProcess = Start-Process -FilePath "$USB_Drive\Shared\bin\ollama-windows.exe" -ArgumentList "serve" -WindowStyle Hidden -PassThru
        
        # Wait for server to respond
        $retry = 0
        $maxRetries = 20
        $serverReady = $false
        while (-not $serverReady -and $retry -lt $maxRetries) {
            $retry++
            try {
                # Use curl.exe directly for more reliable check
                $response = curl.exe -s http://127.0.0.1:11435/api/tags
                if ($LASTEXITCODE -eq 0) { $serverReady = $true }
            } catch {}
            if (-not $serverReady) { Start-Sleep -Seconds 2 }
        }

        if ($serverReady) {
            foreach ($m in $modelsToImport) {
                Write-Host "      Importing $($m.Name)..." -ForegroundColor Yellow
                # Ensure we are in the models folder so relative FROM paths work
                Push-Location "$USB_Drive\Shared\models"
                $importResult = & "$USB_Drive\Shared\bin\ollama-windows.exe" create $m.Local -f "Modelfile-$($m.Local)" 2>&1
                Pop-Location
                
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "      $($m.Name) imported successfully!" -ForegroundColor Green
                } else {
                    Write-Host "      ERROR: Failed to import $($m.Name)" -ForegroundColor Red
                    $errClean = $importResult -join " "
                    Write-Host "      Details: $errClean" -ForegroundColor DarkGray
                }
            }
        } else {
            Write-Host "      ERROR: Temporary Ollama server failed to start for import (Port 11435)." -ForegroundColor Red
        }

        Write-Host "      Finalizing engine state..." -ForegroundColor DarkGray
        Stop-Process -Name "ollama-windows" -Force -ErrorAction SilentlyContinue
        Stop-Process -Name "ollama" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    } else {
        Write-Host "      No models to import!" -ForegroundColor Yellow
    }
}



# =================================================================
# FINAL SUMMARY
# =================================================================
Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan

if ($downloadErrors.Count -gt 0) {
    Write-Host "   SETUP COMPLETE (with some errors)                      " -ForegroundColor Yellow
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  The following had issues:" -ForegroundColor Red
    foreach ($err in $downloadErrors) {
        Write-Host "    ! $err" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  You can re-run install.bat to retry failed downloads." -ForegroundColor Yellow
} else {
    Write-Host "   SETUP COMPLETE! YOUR PORTABLE AI IS READY!             " -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "  Installed LLM models:" -ForegroundColor White
foreach ($m in $SelectedModels) {
    if ($m.Label -eq "UNCENSORED") {
        $tag = "[UNCENSORED]"
        $tagColor = "Red"
    } elseif ($m.Label -eq "CUSTOM") {
        $tag = "[CUSTOM]"
        $tagColor = "Green"
    } else {
        $tag = "[STANDARD]"
        $tagColor = "DarkCyan"
    }
    Write-Host "    - $($m.Name) " -ForegroundColor Gray -NoNewline
    Write-Host $tag -ForegroundColor $tagColor
}

if (Test-Path "$USB_Drive\Shared\models\CyberRealistic_V3.3_FP16.safetensors") {
    Write-Host ""
    Write-Host "  Installed Image model:" -ForegroundColor White
    Write-Host "    - CyberRealistic v3.3 FP16 " -ForegroundColor Gray -NoNewline
    Write-Host "[UNCENSORED]" -ForegroundColor Red
}

Write-Host ""
Write-Host "  To start your AI: Double-click  Windows\start-fast-chat.bat" -ForegroundColor White
Write-Host "  On a Mac/Linux:   Run  start-fast-chat.sh from their folders" -ForegroundColor White
Write-Host ""
Write-Host "Press any key to close this installer..." -ForegroundColor Yellow
$Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") | Out-Null
