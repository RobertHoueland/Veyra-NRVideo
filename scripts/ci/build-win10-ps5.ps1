[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('vcpkg','libraries','sdk','sdl','native','configure','build','test','package')][string]$Phase)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path "$PSScriptRoot/../..").Path
$Work = Join-Path $Root '.ci-work'
$Lock = Get-Content "$PSScriptRoot/win10-ps5-lock.json" -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force $Work | Out-Null
function Exec([string]$File, [string[]]$Arguments) {
    Write-Host "+ $File $($Arguments -join ' ')"
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed ($LASTEXITCODE)" }
}
$VcRoot = "$Work/vcpkg"
$Prefix = "$VcRoot/installed/x64-windows-static"
$Build = "$Work/build"
$CommonPrefix = "$Work/sdl-prefix;$Prefix;$env:QT_ROOT_DIR"
$env:VCPKG_MAX_CONCURRENCY = '2'
$env:CMAKE_BUILD_PARALLEL_LEVEL = '2'
$env:PKG_CONFIG_PATH = "$Prefix/lib/pkgconfig;$Prefix/share/pkgconfig"
$env:PKG_CONFIG_LIBDIR = $env:PKG_CONFIG_PATH
$PkgConfig = "$Prefix/tools/pkgconf/pkgconf.exe"
$Protoc = "$Work/protoc/bin/protoc.exe"
$env:PATH = "$Work/protoc/bin;$Prefix/tools/pkgconf;$env:PATH"
Set-Location $Root
switch ($Phase) {
    'vcpkg' {
        if (-not (Test-Path "$VcRoot/.git")) {
            Exec git @('init',$VcRoot)
            Exec git @('-C',$VcRoot,'remote','add','origin','https://github.com/microsoft/vcpkg.git')
        }
        Exec git @('-C',$VcRoot,'fetch','--depth','1','origin',$Lock.vcpkg_commit)
        Exec git @('-C',$VcRoot,'checkout','--detach',$Lock.vcpkg_commit)
        # Bootstrap occasionally times out fetching its pinned vcpkg.exe from
        # GitHub. Retry this network setup only, never compiler/test failures.
        for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
            try {
                Exec "$VcRoot/bootstrap-vcpkg.bat" @('-disableMetrics')
                break
            } catch {
                if ($Attempt -eq 3) { throw }
                Write-Warning "vcpkg bootstrap attempt $Attempt failed; retrying"
                Start-Sleep -Seconds (5 * $Attempt)
            }
        }
        # Classic mode, never the application's intentionally minimal manifest.
        Exec "$VcRoot/vcpkg.exe" @('install','--classic','--triplet=x64-windows-static',
            'opus','openssl','json-c','libevent','miniupnpc','pkgconf','nlohmann-json')
    }
    'libraries' {
        Exec "$VcRoot/vcpkg.exe" @('install','--classic','--triplet=x64-windows-static','libdatachannel[srtp,ws]')
    }
    'sdk' {
        Exec cmake @('-S',"$Work/ffx1/sdk",'-B',"$Work/ffx1/sdk/build",'-G','Visual Studio 17 2022','-A','x64',
            '-DFFX_API_BACKEND=DX12_X64','-DFFX_OF=ON','-DFFX_AUTO_COMPILE_SHADERS=1',
            '-DCMAKE_POLICY_DEFAULT_CMP0091=NEW','-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded')
        Exec cmake @('--build',"$Work/ffx1/sdk/build",'--config','Release','--parallel','2')
        foreach ($Name in @('ffx_opticalflow_x64.lib','ffx_backend_dx12_x64.lib')) {
            if (-not (Test-Path "$Work/ffx1/sdk/bin/ffx_sdk/$Name")) { throw "Missing AMD library: $Name" }
        }
    }
    'sdl' {
        Exec cmake @('-S',"$Work/sdl",'-B',"$Work/sdl-build",'-G','Ninja','-DCMAKE_BUILD_TYPE=Release',
            "-DCMAKE_INSTALL_PREFIX=$Work/sdl-prefix",'-DSDL_SHARED=OFF','-DSDL_STATIC=ON',
            '-DSDL_TESTS=OFF','-DSDL_TEST_LIBRARY=OFF','-DSDL_LIBC=ON','-DSDL_FORCE_STATIC_VCRT=ON',
            '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded')
        Exec cmake @('--build',"$Work/sdl-build",'--parallel','2')
        Exec cmake @('--install',"$Work/sdl-build")
    }
    'native' {
        & "$Root/scripts/remoteplay/build-native.ps1" -ChiakiCheckout "$Work/chiaki-clean" -Root $Root `
            -StageDirectory "$Work/chiaki-stage" -BuildDirectory "$Work/native-build" `
            -PrefixPath $CommonPrefix -ProtocPath $Protoc -PkgConfigPath $PkgConfig
        if ($LASTEXITCODE -ne 0) { throw 'Native Remote Play gate failed' }
    }
    'configure' {
        $Args = @('-S',$Root,'-B',$Build,'-G','Ninja','-DCMAKE_BUILD_TYPE=Release',
            '-DVEYRA_BUILD_QML_UI=ON','-DVEYRA_DISPLAY_VERSION=2.2.0-win10-fsr4-ps5-test',
            '-DVEYRA_ENABLE_D3D12_DEBUG=OFF','-DVEYRA_ENABLE_EXPERIMENTAL_DLSSNR=ON',
            '-DVEYRA_ENABLE_REMOTEPLAY=ON','-DVEYRA_ENABLE_MOONLIGHT=ON','-DVEYRA_ENABLE_XBOX=ON',
            '-DVEYRA_ENABLE_DEINTERLACE=ON','-DVEYRA_ENABLE_LIBDOVI=ON','-DVEYRA_UPDATE_PUBLIC_KEY=',
            "-DVEYRA_RP_CHIAKI_SOURCE_DIR=$Work/chiaki-stage","-DVEYRA_RP_CHIAKI_VERIFY_DIR=$Work/chiaki-clean",
            "-DVEYRA_MOONLIGHT_SOURCE_DIR=$Work/moonlight","-DVEYRA_FFMPEG_ROOT=$Work/ffmpeg-prefix",
            "-DVEYRA_LIBDOVI_ROOT=$Work/dovi-prefix","-DVEYRA_DLSS_SDK_ROOT=$Work/dlss",
            "-DVEYRA_FIDELITYFX_ROOT=$Work/ffx1/sdk","-DVEYRA_FIDELITYFX2_ROOT=$Work/ffx2/Kits/FidelityFX",
            "-DVEYRA_AMF_SDK_ROOT=$Work/amf/amf/public","-DVEYRA_NVENC_HEADERS_ROOT=$Work/nvenc",
            "-DCMAKE_PREFIX_PATH=$CommonPrefix","-DPROTOC=$Protoc","-DPKG_CONFIG_EXECUTABLE=$PkgConfig")
        Exec cmake $Args
    }
    'build' {
        Exec cmake @('--build',$Build,'--parallel','2','--target','veyra_qml_ui',
            'veyra_remoteplay_feedback_wire_tests','veyra_remoteplay_boundary_tests')
    }
    'test' {
        Exec cmake @('--build',"$Work/native-build",'--parallel','2','--target','veyra_remoteplay_core_tests')
        Exec ctest @('--test-dir',"$Work/native-build",'--output-on-failure','-L','offline-core|native-core','--timeout','60')
        Exec ctest @('--test-dir',$Build,'--output-on-failure','-R','^remoteplay-feedback-wire$','--timeout','60')
        Exec "$Build/veyra_remoteplay_boundary_tests.exe" @()
    }
    'package' {
        Exec python @('-B',"$Root/scripts/ci/prepare-win10-ps5.py",'package','--work',$Work,'--build',$Build,'--output',"$Root/dist")
        $Signature = Get-AuthenticodeSignature "$Root/dist/agility/D3D12Core.dll"
        if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
            throw "Agility signature not valid: $($Signature.Status)"
        }
        # Inspect real imported symbols against the exact official donor DLLs.
        Exec python @('-B',"$Root/scripts/ci/verify-win10-ps5.py",'--work',$Work,'--dist',"$Root/dist")
    }
}
