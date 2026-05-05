# EasyCon Bridge Protocol

`EasyConBridge.exe` is the required persistent backend for the EasyCon module.
The CLI backend remains a diagnostic fallback only.

## Process Model

- The Python/PySide app starts one `EasyConBridge.exe` process.
- IPC uses UTF-8 JSON Lines over stdin/stdout.
- One request is one JSON object plus `\n`.
- One response is one JSON object plus `\n`.
- The bridge owns the serial port after `connect`.
- A script finishing must not close the serial port.
- Only `disconnect` or bridge process exit may release the serial port.

## Request

```json
{"id":1,"command":"connect","payload":{"port":"COM7"}}
```

Fields:

- `id`: client-generated integer.
- `command`: command name.
- `payload`: object, omitted fields are command-specific defaults.

## Response

```json
{"id":1,"ok":true,"payload":{"status":"connected"}}
```

Failure:

```json
{"id":1,"ok":false,"error":"serial port busy"}
```

Log event:

```json
{"type":"log","level":"stdout","message":"脚本运行完成"}
```

Log events are asynchronous and do not complete a request.

## Commands

### version

Request payload: `{}`.

Response payload:

```json
{"version":"1.6.3+bridge"}
```

### list_ports

Request payload: `{}`.

Response payload:

```json
{"ports":["COM7","COM9"]}
```

### connect

Request payload:

```json
{"port":"COM7"}
```

Response payload:

```json
{"status":"connected","port":"COM7"}
```

Contract:

- Opens the serial port once.
- Leaves the port open until `disconnect`.
- Repeated `connect` to the same port may be a no-op.
- Repeated `connect` to another port should fail unless disconnected first.

### disconnect

Request payload: `{}`.

Response payload:

```json
{"status":"disconnected"}
```

Contract:

- Stops any current script safely.
- Releases the serial port.

### run_script

Request payload:

```json
{"name":"玫瑰公园.ecs","script_text":"A 100\n"}
```

Response payload:

```json
{"exit_code":0,"stdout":"脚本运行完成","stderr":""}
```

Contract:

- Requires an existing `connect`.
- Executes the script against the already-open serial connection.
- Must not close or reopen the serial port.
- After completion, bridge status returns to connected/idle.
- A second and third `run_script` call must reuse the same connection.

### stop_current_script

Request payload: `{}`.

Response payload:

```json
{"status":"connected"}
```

Contract:

- Stops the current script.
- Keeps the serial connection open.

### press

Request payload:

```json
{"button":"A","duration_ms":100}
```

Contract:

- Sends the button action through the already-open connection.
- Does not start a CLI process.

### stick

Request payload:

```json
{"side":"LS","direction":"RESET","duration_ms":null}
```

Contract:

- Sends the stick action through the already-open connection.
- Does not start a CLI process.

## Acceptance Scenario

1. Start `EasyConBridge.exe`.
2. Send `connect(COMx)`.
3. Send `run_script` with script A.
4. Confirm bridge reports connected/idle after script A.
5. Send `run_script` with script B.
6. Confirm the serial port was not closed or reopened.
7. Send `run_script` with script C.
8. Confirm no reset was required between A/B/C.
9. Send `press(A, 100)` and confirm it uses the same connection.
10. Send `disconnect` and confirm the port is released.
