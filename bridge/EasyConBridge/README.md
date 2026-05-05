# EasyConBridge

Persistent EasyCon backend for `auto_bdsp_rng`.

This bridge is the service-side counterpart to `auto_bdsp_rng.automation.easycon.BridgeEasyConBackend`.
It keeps the EasyCon serial connection open after `connect` and reuses that connection for every
`run_script` request until the app explicitly sends `disconnect`.

## Build

This project references EasyCon source through the `EasyConSourceRoot` MSBuild property.

```powershell
dotnet build .\bridge\EasyConBridge\EasyConBridge.csproj -p:EasyConSourceRoot=D:\path\to\EasyCon
```

The current development machine has only the .NET runtime installed and no SDK, so this project cannot
be compiled here yet. Install a .NET SDK compatible with EasyCon 1.6.3 (`net10.0`) before building.

## Protocol

See `docs/easycon_bridge_protocol.md`.
