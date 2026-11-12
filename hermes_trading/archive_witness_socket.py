"""Disabled bounded witness sockets and an explicitly enabled loopback service.

No deployment, key installation, DNS resolution, proxy, redirect or public bind.
TLS verifies the configured server name/CA against an explicit numeric route.
The listener serves one owner thread, preserving prepared SQLite ownership.
"""
import ipaddress
import json
import math
import re
import socket
import socketserver
import ssl
import time

from hermes_trading.archive_witness_transport import MAX_WIRE_BYTES,_envelope,_timeout
from hermes_trading.reflection_supervision import _json,_now

PATH='/archive/witness/v1'
MAX_HEADER_BYTES=4096
MAX_HEADERS=32
TOKEN=re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def _remaining(deadline,clock):
    remaining=deadline-clock()
    if not math.isfinite(remaining) or remaining > 10:raise ValueError('witness socket clock differs')
    if remaining <= 0:raise TimeoutError('witness socket deadline exceeded')
    return remaining


def _receive(connection,deadline,clock,count):
    connection.settimeout(_remaining(deadline,clock))
    value=connection.recv(count)
    if not value:raise ValueError('witness HTTP message truncated')
    return value


def _message(connection,deadline,clock,max_body):
    buffer=bytearray()
    while b'\r\n\r\n' not in buffer:
        if len(buffer)>=MAX_HEADER_BYTES:raise ValueError('witness HTTP headers exceed bound')
        buffer.extend(_receive(connection,deadline,clock,min(4096,MAX_HEADER_BYTES-len(buffer)+1)))
    split=buffer.index(b'\r\n\r\n')+4
    if split > MAX_HEADER_BYTES:raise ValueError('witness HTTP headers exceed bound')
    try:lines=bytes(buffer[:split-4]).decode('ascii').split('\r\n')
    except UnicodeError as error:raise ValueError('witness HTTP headers malformed') from error
    if not lines or len(lines)-1 > MAX_HEADERS:raise ValueError('witness HTTP header count exceeds bound')
    headers={}
    for line in lines[1:]:
        key,separator,value=line.partition(':')
        if not separator or TOKEN.fullmatch(key) is None or any(ord(char)<32 and char!='\t' or ord(char)==127 for char in value):
            raise ValueError('witness HTTP header malformed')
        key=key.lower()
        if key in headers:raise ValueError('witness HTTP duplicate header')
        headers[key]=value.strip()
    if 'transfer-encoding' in headers or headers.get('content-encoding','identity').lower()!='identity':
        raise ValueError('witness HTTP transfer/content encoding unavailable')
    if headers.get('content-type','').split(';',1)[0].strip().lower()!='application/json':
        raise ValueError('witness HTTP content type differs')
    raw_length=headers.get('content-length','')
    if re.fullmatch('[0-9]{1,5}',raw_length) is None:raise ValueError('witness HTTP content length differs')
    length=int(raw_length)
    if str(length)!=raw_length or not 0 < length <= max_body:raise ValueError('witness HTTP body exceeds byte bound')
    body=bytearray(buffer[split:])
    if len(body)>length:raise ValueError('witness HTTP trailing/pipelined data')
    while len(body)<length:body.extend(_receive(connection,deadline,clock,min(4096,length-len(body))))
    _remaining(deadline,clock)
    return lines[0],headers,bytes(body)


def _pairs(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('witness JSON duplicate key')
        value[key]=item
    return value


def _decode(raw):
    try:value=json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs)
    except (UnicodeError,ValueError,RecursionError) as error:raise ValueError('witness wire JSON malformed') from error
    _envelope(value);return value


def _send(connection,deadline,clock,data):
    for offset in range(0,len(data),4096):
        connection.settimeout(_remaining(deadline,clock));connection.sendall(data[offset:offset+4096])
    _remaining(deadline,clock)


class SocketExchange:
    def __init__(self,address,port,*,server_name=None,ca_file=None,allow_loopback_http=False,
                 enabled=False,clock=time.monotonic):
        try:route=ipaddress.ip_address(address)
        except ValueError as error:raise ValueError('witness route must be a numeric address; DNS is unavailable') from error
        if type(port) is not int or not 1 <= port <= 65535:raise ValueError('witness port differs')
        if server_name is None:
            if allow_loopback_http is not True or not route.is_loopback or ca_file is not None:
                raise ValueError('plaintext witness transport is restricted to explicit loopback tests/tunnels')
        elif (not isinstance(server_name,str) or len(server_name)>253 or
                any(re.fullmatch('[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) is None for label in server_name.split('.'))):
            raise ValueError('witness TLS server name differs')
        self.address=str(route);self.family=socket.AF_INET if route.version==4 else socket.AF_INET6
        self.port=port;self.server_name=server_name;self.enabled=enabled;self.clock=clock
        self._tls=ssl.create_default_context(cafile=ca_file) if server_name is not None and enabled is True else None

    def __call__(self,envelope,*,timeout_seconds,max_response_bytes):
        if self.enabled is not True:raise ValueError('witness socket exchange is disabled')
        _timeout(timeout_seconds);_envelope(envelope)
        if type(max_response_bytes) is not int or not 0 < max_response_bytes <= MAX_WIRE_BYTES:
            raise ValueError('witness socket response bound differs')
        body=_json(envelope).encode()
        if len(body)>MAX_WIRE_BYTES:raise ValueError('witness socket request exceeds byte bound')
        host=self.server_name or ('['+self.address+']' if self.family==socket.AF_INET6 else self.address)
        headers=(f'POST {PATH} HTTP/1.1\r\nHost: {host}:{self.port}\r\nContent-Type: application/json\r\n'
                 f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode('ascii')
        deadline=self.clock()+timeout_seconds;connection=socket.socket(self.family,socket.SOCK_STREAM)
        try:
            connection.settimeout(_remaining(deadline,self.clock));connection.connect((self.address,self.port))
            if self.server_name is not None:
                connection=self._tls.wrap_socket(connection,server_hostname=self.server_name,do_handshake_on_connect=False)
                connection.settimeout(_remaining(deadline,self.clock));connection.do_handshake()
            _send(connection,deadline,self.clock,headers+body)
            start,_,raw=_message(connection,deadline,self.clock,max_response_bytes)
            parts=start.split(' ',2)
            if len(parts)!=3 or parts[0] not in {'HTTP/1.0','HTTP/1.1'} or parts[1]!='200':
                raise ValueError('witness HTTP response status rejected')
            return _decode(raw)
        finally:connection.close()


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        server=self.server;deadline=server.request_deadline
        try:
            start,headers,raw=_message(self.request,deadline,server.clock,MAX_WIRE_BYTES)
            if start!=f'POST {PATH} HTTP/1.1' or not headers.get('host'):
                raise ValueError('witness HTTP request scope differs')
            envelope=_decode(raw)
            if _json(envelope).encode()!=raw:raise ValueError('witness wire envelope must be canonical')
            server.handled_requests+=1
            response=server.witness.handle(envelope,now=server.now(),timeout_seconds=_remaining(deadline,server.clock))
            body=_json(response).encode()
            if len(raw)+len(body)>MAX_WIRE_BYTES:raise ValueError('witness combined wire budget exceeded')
            message=(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: '+
                     str(len(body)).encode()+b'\r\n\r\n'+body)
            _send(self.request,deadline,server.clock,message)
        except Exception:
            server.rejected_requests+=1
            # No error details, paths, request bodies or credentials on the wire.
            body=b'{"error":"witness_request_rejected"}'
            message=(b'HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: '+
                     str(len(body)).encode()+b'\r\n\r\n'+body)
            try:_send(self.request,deadline,server.clock,message)
            except (OSError,ValueError):pass


class LoopbackWitnessService(socketserver.TCPServer):
    """Single owner thread only. No remote/public listener or startup side effect."""
    allow_reuse_address=False

    def __init__(self,witness,*,enabled=False,address='127.0.0.1',port=0,
                 timeout_seconds=10,ssl_context=None,now=None,clock=time.monotonic):
        if enabled is not True:raise ValueError('loopback witness service is disabled')
        try:route=ipaddress.ip_address(address)
        except ValueError as error:raise ValueError('loopback listener address differs') from error
        if not route.is_loopback:raise ValueError('public witness bind is unavailable')
        if type(port) is not int or not 0 <= port <= 65535:raise ValueError('loopback listener port differs')
        _timeout(timeout_seconds)
        if ssl_context is not None and (not isinstance(ssl_context,ssl.SSLContext) or ssl_context.protocol!=ssl.PROTOCOL_TLS_SERVER):
            raise ValueError('witness listener TLS context differs')
        self.address_family=socket.AF_INET if route.version==4 else socket.AF_INET6
        self.witness=witness;self.timeout_seconds=timeout_seconds;self.ssl_context=ssl_context
        self.now=now or _now;self.clock=clock;self.handled_requests=0;self.rejected_requests=0
        super().__init__((str(route),port),_Handler)

    def get_request(self):
        connection,address=super().get_request()
        self.request_deadline=self.clock()+self.timeout_seconds
        try:
            if self.ssl_context is not None:
                connection=self.ssl_context.wrap_socket(connection,server_side=True,do_handshake_on_connect=False)
                connection.settimeout(_remaining(self.request_deadline,self.clock));connection.do_handshake()
            return connection,address
        except BaseException:
            connection.close();raise
