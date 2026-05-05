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

The current development machine has a .NET 10 SDK available through `dotnet`.

## Mock Session Smoke Test

`--mock-session` keeps the JSON Lines protocol and connection lifecycle intact while replacing the
physical serial device with an in-process fake session. It is only for automated lifecycle smoke tests.

```powershell
.\bridge\EasyConBridge\bin\Debug\net10.0\EasyConBridge.exe --mock-session
```

## Protocol

See `docs/easycon_bridge_protocol.md`.
