"""QQ Open Platform client using Qt's asynchronous network APIs.

Protocol reference: BetterGI QqNotifier / QqWebSocketHelper, commit
f29966868c6e2d5b8798bb6a4f3df201ec4a5f95 (GPL-3.0).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote

from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket


class QQError(Exception):
    pass


@dataclass
class Request:
    method: str
    url: str
    body: object = None
    token: str = ""
    raw: bool = False


@dataclass
class Gateway:
    url: str
    token: str
    kind: str


class QQClient(QObject):
    log = Signal(str)
    status = Signal(str)
    busy_changed = Signal(bool)
    bound = Signal(str, str)
    finished = Signal(bool, str)
    binding_changed = Signal(str, str, int, int)
    delivery = Signal(str, bool, str)

    def __init__(self, parent=None, *, api_base="https://api.sgroup.qq.com",
                 token_url="https://bots.qq.com/app/getAppAccessToken"):
        super().__init__(parent)
        self.api_base = api_base.rstrip("/")
        self.token_url = token_url
        self.network = QNetworkAccessManager(self)
        self.app_id = ""
        self.secret = ""
        self._token = ""
        self._expires = 0.0
        self._flow = None
        self._operation = 0
        self._reply = None
        self._socket = None
        self._seq = None
        self._ready = False
        self._awaiting_ack = False
        self._code = ""
        self._kind = ""
        self._auto_refresh = True
        self._code_expires = 0.0
        self._generation = 0
        self._last_countdown = None
        self.request_timeout_ms = 20000
        self.bind_timeout_ms = 60000
        self._deadline = QTimer(self)
        self._deadline.setTimerType(Qt.TimerType.PreciseTimer)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._binding_deadline)
        self._countdown = QTimer(self)
        self._countdown.setInterval(200)
        self._countdown.timeout.connect(self._tick_binding)
        self._heartbeat = QTimer(self)
        self._heartbeat.timeout.connect(self._send_heartbeat)

    @property
    def busy(self):
        return self._flow is not None

    def configure(self, app_id: str, secret: str):
        if self.busy:
            raise QQError("请先等待当前操作结束或取消。")
        app_id, secret = app_id.strip(), secret.strip()
        if not app_id or not secret:
            raise QQError("请填写 AppID 和 AppSecret。")
        if (app_id, secret) != (self.app_id, self.secret):
            self._token, self._expires = "", 0
        self.app_id, self.secret = app_id, secret

    def _redact(self, text):
        text = str(text)
        for value in (self.secret, self._token):
            if value:
                text = text.replace(value, "[已隐藏]")
        return text

    def check_credentials(self):
        self._start(self._check(), "正在验证机器人凭据…")

    def bind(self, kind: str, *, auto_refresh: bool = True):
        if kind not in ("user", "group"):
            raise QQError("不支持的绑定类型。")
        if self.busy:
            raise QQError("已有操作正在进行。")
        self._auto_refresh = auto_refresh
        self._start(self._bind(kind), "正在连接 QQ 网关…")

    def send(self, targets: list[tuple[str, str]], text: str, image: bytes = b""):
        if not targets or any(kind not in ("user", "group") or not oid.strip() for kind, oid in targets):
            raise QQError("请为所选目标填写或绑定 OpenID。")
        if not text.strip() and not image:
            raise QQError("请填写通知文字或选择图片。")
        self._start(self._send(targets, text.strip(), image), "正在发送通知…")

    def _start(self, flow, status):
        if self.busy:
            flow.close()
            raise QQError("已有操作正在进行。")
        if not self.app_id or not self.secret:
            flow.close()
            raise QQError("请填写 AppID 和 AppSecret。")
        self._operation += 1
        self._flow = flow
        self.busy_changed.emit(True)
        self.status.emit(status)
        self.log.emit(status)
        self._advance()

    def _advance(self, value=None, error=None):
        if self._flow is None:
            return
        try:
            step = self._flow.throw(error) if error else self._flow.send(value)
            if isinstance(step, Request):
                self._request(step)
            elif isinstance(step, Gateway):
                self._connect_gateway(step)
            else:
                raise QQError("未知的网络操作。")
        except StopIteration as done:
            self._finish(True, done.value or "操作完成。")
        except Exception as exc:
            self._finish(False, self._redact(exc))

    def _request(self, step: Request):
        url = QUrl(step.url)
        if url.scheme() not in ("http", "https") or not url.host():
            raise QQError("服务器返回了无效的 HTTP 地址。")
        request = QNetworkRequest(url)
        request.setTransferTimeout(self.request_timeout_ms)
        # Do not forward a bearer token to a redirected host.
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        if step.token:
            request.setRawHeader(b"Authorization", f"QQBot {step.token}".encode())
        if step.raw:
            payload = step.body
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/octet-stream")
        else:
            payload = json.dumps(step.body, ensure_ascii=False).encode("utf-8") if step.body is not None else b""
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        reply = self.network.sendCustomRequest(request, step.method.encode(), payload)
        self._reply = reply
        operation = self._operation

        def completed():
            try:
                if operation != self._operation or not self.busy:
                    return
                self._reply = None
                status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                data = bytes(reply.readAll()).decode("utf-8", errors="replace")
                if reply.error() != QNetworkReply.NetworkError.NoError or not status or not 200 <= int(status) < 300:
                    detail = self._redact(data or reply.errorString())[:1000]
                    if status == 401:
                        self._token, self._expires = "", 0
                    raise QQError(f"HTTP {status or '连接失败'}：{detail}")
                result = {} if step.raw or not data else json.loads(data)
                if not isinstance(result, dict):
                    raise QQError("QQ 接口返回了非对象数据。")
                if result.get("code") not in (None, 0, "0"):
                    raise QQError(f"QQ API 错误：{self._redact(json.dumps(result, ensure_ascii=False))[:1000]}")
            except Exception as exc:
                self._advance(error=exc)
            else:
                self._advance(result)
            finally:
                reply.deleteLater()

        reply.finished.connect(completed)

    def _access_token(self):
        if self._token and time.monotonic() < self._expires:
            return self._token
        self.log.emit("获取访问令牌…")
        result = yield Request("POST", self.token_url,
                               {"appId": self.app_id, "clientSecret": self.secret})
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise QQError("QQ 接口未返回 access_token。")
        self._token = token
        lifetime = float(result.get("expires_in", 0))
        self._expires = time.monotonic() + max(0, lifetime - min(60, lifetime / 2))
        self.log.emit("已获取访问令牌（不显示令牌内容）。")
        return token

    def _check(self):
        self._expires = 0
        yield from self._access_token()
        return "凭据验证成功。可以继续绑定接收方。"

    def _bind(self, kind):
        token = yield from self._access_token()
        response = yield Request("GET", self.api_base + "/gateway", token=token)
        url = response.get("url", "")
        if QUrl(url).scheme() not in ("ws", "wss"):
            raise QQError("QQ 接口未返回有效的 WebSocket 网关地址。")
        open_id = yield Gateway(url, token, kind)
        self.bound.emit(kind, open_id)
        return "私聊绑定成功。" if kind == "user" else "群聊绑定成功。"

    def _connect_gateway(self, gateway):
        self._kind = gateway.kind
        self._code = ""
        self._generation = 0
        self._last_countdown = None
        self._seq, self._ready, self._awaiting_ack = None, False, False
        socket = QWebSocket(parent=self)
        self._socket = socket
        operation = self._operation

        def current():
            return operation == self._operation and socket is self._socket

        def message(payload):
            if not current():
                return
            try:
                self._gateway_message(json.loads(payload), gateway.token)
            except Exception as exc:
                self._gateway_fail(self._redact(exc))

        socket.textMessageReceived.connect(message)
        socket.errorOccurred.connect(lambda _: self._gateway_fail(socket.errorString()) if current() else None)
        socket.disconnected.connect(lambda: self._gateway_fail("QQ 网关连接已断开，请重新绑定。") if current() else None)
        self._deadline.start(self.request_timeout_ms)
        socket.open(QUrl(gateway.url))

    def _gateway_message(self, payload, token):
        op = payload.get("op")
        data = payload.get("d") or {}
        if payload.get("s") is not None:
            self._seq = payload["s"]
        if op == 10:
            interval = int(data["heartbeat_interval"])
            if interval <= 0:
                raise QQError("网关心跳间隔无效。")
            self._socket.sendTextMessage(json.dumps({"op": 2, "d": {
                "token": "QQBot " + token, "intents": 1 << 25, "shard": [0, 1]}}))
            self._heartbeat.start(interval)
        elif op == 11:
            self._awaiting_ack = False
        elif op == 1:
            self._send_heartbeat(check_ack=False)
        elif op in (7, 9):
            raise QQError("QQ 网关要求重新连接或鉴权失败，请检查机器人权限后重新绑定。")
        elif op == 0:
            event = payload.get("t")
            if event == "READY":
                if self._ready:
                    return
                self._ready = True
                self._renew_binding()
                self._countdown.start()
                return
            if not self._ready:
                return
            # Require the code even for new friends/groups, so an unrelated add
            # event cannot accidentally bind a different recipient.
            expected = ("C2C_MESSAGE_CREATE",) if self._kind == "user" else (
                "GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE")
            if event not in expected:
                return
            # Check the deadline here too: a message may beat the timer event.
            self._tick_binding()
            if not self._socket:
                return
            if not re.search(r"(?<!\d)" + self._code + r"(?!\d)", str(data.get("content", ""))):
                self.log.emit("收到消息，但未匹配当前验证码；继续等待。")
                return
            open_id = ((data.get("author") or {}).get("user_openid") if self._kind == "user"
                       else data.get("group_openid"))
            if not isinstance(open_id, str) or not open_id.strip():
                raise QQError("收到验证码，但事件中缺少 OpenID。")
            self._close_gateway()
            self._advance(open_id)

    def _renew_binding(self):
        previous = int(self._code or 100000)
        self._code = str(100000 + (previous - 100000 + secrets.randbelow(899999) + 1) % 900000)
        self._generation += 1
        self._code_expires = time.monotonic() + self.bind_timeout_ms / 1000
        self._deadline.start(self.bind_timeout_ms)
        instruction = "私聊机器人" if self._kind == "user" else "在目标群 @机器人"
        self.status.emit(f"请{instruction}，发送验证码：{self._code}")
        self.log.emit("已更新绑定码，旧码失效。" if self._generation > 1 else "已准备好绑定码，等待 QQ 消息。")
        self._emit_countdown()

    def _binding_deadline(self):
        if self._socket and self._ready and self._auto_refresh:
            self._renew_binding()
        else:
            self._gateway_fail("绑定超时，请重新绑定并发送当前验证码。")

    def _emit_countdown(self):
        remaining = max(0, math.ceil(self._code_expires - time.monotonic()))
        current = (self._kind, self._code, remaining, self._generation)
        if current != self._last_countdown:
            self._last_countdown = current
            self.binding_changed.emit(*current)

    def _tick_binding(self):
        if not self._socket or not self._ready:
            return
        if time.monotonic() >= self._code_expires:
            self._binding_deadline()
        if self._socket:
            self._emit_countdown()

    def _send_heartbeat(self, check_ack=True):
        if not self._socket:
            return
        if check_ack and self._awaiting_ack:
            self._gateway_fail("QQ 网关心跳未响应，请重新绑定。")
            return
        self._socket.sendTextMessage(json.dumps({"op": 1, "d": self._seq}))
        self._awaiting_ack = True

    def _gateway_fail(self, message):
        if self._socket:
            self._close_gateway()
            self._advance(error=QQError(message))

    def _close_gateway(self):
        self._deadline.stop()
        self._heartbeat.stop()
        self._countdown.stop()
        self._ready = False
        self._code = ""
        socket, self._socket = self._socket, None
        if socket:
            socket.abort()
            socket.deleteLater()
        self.binding_changed.emit("", "", 0, 0)

    def _send(self, targets, text, image):
        failures, successes = [], []
        for kind, open_id in targets:
            label = "私聊" if kind == "user" else "群聊"
            base = self.api_base + ("/v2/users/" if kind == "user" else "/v2/groups/") + quote(open_id.strip(), safe="")
            submitted = []
            try:
                token = yield from self._access_token()
                if text:
                    yield Request("POST", base + "/messages", {"msg_type": 0, "content": text}, token)
                    self.log.emit(f"{label}文字发送成功。")
                    submitted.append("文字已提交")
                if image:
                    self.status.emit(f"正在上传{label}图片…")
                    info = yield from self._upload_image(base, token, image)
                    yield Request("POST", base + "/messages", {"msg_type": 7, "media": {"file_info": info}}, token)
                    self.log.emit(f"{label}图片发送成功。")
                    submitted.append("图片已提交")
                successes.append(label)
                self.delivery.emit(kind, True, "、".join(submitted))
            except Exception as exc:
                error = self._redact(exc)
                failures.append(f"{label}：{error}")
                self.log.emit(f"{label}发送未全部完成：{error}")
                self.delivery.emit(kind, False, ("、".join(submitted) + "；" if submitted else "") + error)
        if failures:
            prefix = f"{'、'.join(successes)}发送成功；" if successes else ""
            raise QQError(prefix + "；".join(failures) + "。已成功发送的消息不会撤回；请先查看日志再决定是否重发。")
        return "、".join(successes) + "发送成功。"

    def _upload_image(self, base, token, image):
        prepared = yield Request("POST", base + "/upload_prepare", {
            "file_type": 1, "file_size": str(len(image)), "file_name": "notification.jpg",
            "md5": hashlib.md5(image).hexdigest(), "sha1": hashlib.sha1(image).hexdigest(),
            "md5_10m": hashlib.md5(image[:10002432]).hexdigest()}, token)
        upload_id, block_size = prepared["upload_id"], int(prepared["block_size"])
        parts = prepared["parts"]
        if block_size <= 0 or not isinstance(parts, list) or not parts:
            raise QQError("上传接口返回了无效的分片信息。")
        indexes = [int(part["index"]) for part in parts]
        if sorted(indexes) != list(range(1, (len(image) + block_size - 1) // block_size + 1)):
            raise QQError("上传接口返回的分片列表不完整。")
        for part, index in zip(parts, indexes):
            chunk = image[(index - 1) * block_size:index * block_size]
            yield Request("PUT", part["presigned_url"], chunk, raw=True)
            yield Request("POST", base + "/upload_part_finish", {
                "upload_id": upload_id, "part_index": index, "block_size": str(len(chunk)),
                "md5": hashlib.md5(chunk).hexdigest()}, token)
            self.log.emit(f"图片分片 {index}/{len(parts)} 上传完成。")
        result = yield Request("POST", base + "/files", {"file_type": 1, "upload_id": upload_id}, token)
        if not result.get("file_info"):
            raise QQError("图片合并接口未返回 file_info。")
        return result["file_info"]

    def cancel(self):
        if self.busy:
            self._finish(False, "操作已取消。已提交的消息可能已经送达，请在 QQ 中核对。")

    def _finish(self, success, message):
        self._operation += 1
        self._close_gateway()
        reply, self._reply = self._reply, None
        if reply:
            reply.abort()
        flow, self._flow = self._flow, None
        if flow:
            flow.close()
        message = self._redact(message)
        self.status.emit(message)
        self.log.emit(message)
        self.busy_changed.emit(False)
        self.finished.emit(success, message)
