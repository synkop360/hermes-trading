"""Real loopback/TLS transport tests; disposable keys/state, no external hosts."""
from contextlib import closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json,queue,secrets,socket,ssl,tempfile,threading,time,unittest
from unittest.mock import patch

import test_archive_witness_worker as native
from hermes_trading.archive_witness_socket import SocketExchange,LoopbackWitnessService,PATH
from hermes_trading.archive_witness_transport import WitnessServer,WitnessClient
from hermes_trading.archive_receiver import VerifiedReceiver
from hermes_trading.control_auth import SQLiteNonceReplayStore
from hermes_trading.persistence.repository import StateRepository
from hermes_trading.reflection_supervision import _json
from hermes_trading.paired_paper import run_paired_cycle,process_paper_account

NOW=datetime(2026,10,16,tzinfo=timezone.utc)
ENVELOPE={'body':'{}','nonce':'a'*32,'timestamp':NOW.isoformat(),'signature':'b'*64}


def certificates(root):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key=ec.generate_private_key(ec.SECP256R1());name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    at=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(at-timedelta(minutes=1))
        .not_valid_after(at+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False).sign(key,hashes.SHA256()))
    pem=root/'test-ca.pem';private=root/'test-key.pem'
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(pem,private)
    return pem,context


class SocketTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ca,self.context=certificates(Path(self.tmp.name))

    def peer(self,response,*,tls=False,drip=False):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(2)
        port=listener.getsockname()[1]
        def run():
            try:
                with closing(listener.accept()[0]) as raw:
                    raw.settimeout(2)
                    connection=self.context.wrap_socket(raw,server_side=True) if tls else raw
                    with closing(connection):
                        connection.recv(20000)
                        if drip:
                            for value in response:connection.sendall(bytes([value]));time.sleep(.015)
                        else:connection.sendall(response)
            except (OSError,TimeoutError):pass
            finally:listener.close()
        thread=threading.Thread(target=run,daemon=True);thread.start()
        def cleanup():
            listener.close();thread.join(3);self.assertFalse(thread.is_alive(),'temporary peer must terminate')
        self.addCleanup(cleanup);return port

    def reply(self,body=None,extra=b''):
        body=_json(ENVELOPE).encode() if body is None else body
        return b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\n'+extra+b'Connection: close\r\n\r\n'+body

    def invoke(self,port,**kwargs):
        exchange=SocketExchange('127.0.0.1',port,enabled=True,allow_loopback_http=True,**kwargs)
        return exchange(ENVELOPE,timeout_seconds=1,max_response_bytes=4096)

    def test_real_loopback_http_roundtrip_and_default_disabled(self):
        port=self.peer(self.reply());self.assertEqual(self.invoke(port),ENVELOPE)
        disabled=SocketExchange('127.0.0.1',port,allow_loopback_http=True)
        with self.assertRaisesRegex(ValueError,'disabled'):disabled(ENVELOPE,timeout_seconds=1,max_response_bytes=4096)

    def test_tls_validates_ca_and_server_name_without_plaintext_fallback(self):
        port=self.peer(self.reply(),tls=True)
        exchange=SocketExchange('127.0.0.1',port,server_name='localhost',ca_file=self.ca,enabled=True)
        self.assertEqual(exchange(ENVELOPE,timeout_seconds=1,max_response_bytes=4096),ENVELOPE)
        for hostname,ca in [('wrong.test',self.ca),('localhost',None)]:
            port=self.peer(self.reply(),tls=True)
            exchange=SocketExchange('127.0.0.1',port,server_name=hostname,ca_file=ca,enabled=True)
            with self.assertRaises(ssl.SSLCertVerificationError):exchange(ENVELOPE,timeout_seconds=1,max_response_bytes=4096)

    def test_plaintext_nonloopback_dns_and_public_listener_are_rejected(self):
        for address in ('203.0.113.1','example.com'):
            with self.assertRaises(ValueError):SocketExchange(address,443,allow_loopback_http=True,enabled=True)
        with self.assertRaises(ValueError):LoopbackWitnessService(None,address='0.0.0.0',enabled=True)
        with self.assertRaisesRegex(ValueError,'disabled'):LoopbackWitnessService(None)

    def test_header_body_and_framing_bounds_reject_before_unbounded_reads(self):
        cases=[b'HTTP/1.1 200 OK\r\nX-Large: '+b'x'*5000,
            b'HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\n\r\n',
            self.reply(extra=b'Content-Length: 1\r\n'),
            self.reply(extra=b'Transfer-Encoding: chunked\r\n'),
            self.reply(extra=b'Content-Encoding: gzip\r\n'),
            b'HTTP/1.1 302 Found\r\nContent-Length: 0\r\nLocation: https://example.com\r\n\r\n',
            self.reply(body=b'{"body":1,"body":2}'),self.reply(body=b'not json')]
        for response in cases:
            with self.subTest(response=response[:50]),self.assertRaises(ValueError):self.invoke(self.peer(response))

    def test_drip_response_cannot_extend_the_absolute_deadline(self):
        port=self.peer(self.reply(),drip=True)
        exchange=SocketExchange('127.0.0.1',port,enabled=True,allow_loopback_http=True)
        started=time.monotonic()
        with self.assertRaises(TimeoutError):exchange(ENVELOPE,timeout_seconds=.12,max_response_bytes=4096)
        self.assertLess(time.monotonic()-started,1)


class WireNativeWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f=native.NativeWitnessWorkerTests();self.f.setUp()
        for function,args,kwargs in self.f._cleanups:self.addCleanup(function,*args,**kwargs)
        self.f._cleanups.clear()
        self.ca,self.context=certificates(self.f.path.parent);self.secret=secrets.token_hex(32)
        ready=queue.Queue();self.failure=[];self.service=None
        def serve():
            receiver=None;repo=None
            try:
                receiver=VerifiedReceiver(self.f.path,self.f.binding,self.f.identity,enabled=True);receiver.prepare(timeout_seconds=10)
                repo=StateRepository.open(self.f.path.parent/'nonce.sqlite');repo.migrate()
                server=WitnessServer(self.f.path,self.f.binding,self.f.identity,self.secret,SQLiteNonceReplayStore(repo),enabled=True,receiver=receiver)
                self.service=LoopbackWitnessService(server,enabled=True,ssl_context=self.context,now=self.f.f.clock)
                ready.put(self.service.server_address);self.service.serve_forever(poll_interval=.02)
            except BaseException as error:self.failure.append(error);ready.put(error)
            finally:
                if self.service is not None:self.service.server_close()
                if receiver is not None:receiver.close()
                if repo is not None:repo.close()
        self.thread=threading.Thread(target=serve,daemon=True);self.thread.start()
        def cleanup():
            if self.service is not None and self.thread.is_alive():self.service.shutdown()
            self.thread.join(3);self.assertFalse(self.thread.is_alive());self.assertEqual(self.failure,[])
        self.addCleanup(cleanup)
        address=ready.get(timeout=5)
        if isinstance(address,BaseException):raise address
        self.exchange=SocketExchange(address[0],address[1],server_name='localhost',ca_file=self.ca,enabled=True)
        self.f.client=WitnessClient(self.f.binding,self.f.identity,self.secret,self.exchange,enabled=True,now=self.f.f.clock)
        self.f.head=lambda:self.f.client.read(binding=self.f.binding,identity=self.f.identity,timeout=10)

    async def test_native_signed_tls_worker_retains_all_eight_wallets_and_original_bound(self):
        result=await self.f.invoke();await self.f.invoke()
        self.assertEqual(result['completed_batches'],2);self.assertEqual(self.f.head()['state']['point']['complete'],2)
        self.assertEqual(self.f.f.market.collect.await_count,2)
        for profile in self.f.f.manifest['profiles']:self.assertEqual(len(self.f.f.positions(profile['directory'])),1)

    async def test_original_pending_batch_replays_over_tls_without_recollection(self):
        async def crash_account(*args,**kwargs):
            await process_paper_account(*args,**kwargs);raise RuntimeError('after original account')
        async def crash_cycle(*args,**kwargs):return await run_paired_cycle(*args,**kwargs,process_account=crash_account)
        with patch('hermes_trading.paired_worker.run_paired_cycle',side_effect=crash_cycle):
            with self.assertRaisesRegex(RuntimeError,'original account'):await self.f.invoke()
        self.assertIsNotNone(self.f.head()['state']['reservation'])
        result=await self.f.invoke();self.assertEqual(result['completed_batches'],2)
        self.assertEqual(self.f.f.market.collect.await_count,2)

    async def test_lost_wire_reservation_reply_never_allows_unfrozen_collection(self):
        def lost(envelope,**kwargs):
            result=self.exchange(envelope,**kwargs)
            if json.loads(envelope['body'])['action']=='reserve':raise RuntimeError('lost wire reply')
            return result
        self.f.client.exchange=lost
        with self.assertRaisesRegex(RuntimeError,'lost wire reply'):await self.f.invoke()
        self.f.client.exchange=self.exchange
        self.assertIsNotNone(self.f.head()['state']['reservation'])
        with self.assertRaises(ValueError):await self.f.invoke()
        self.assertEqual(self.f.f.market.collect.await_count,0)


class LoopbackServiceBoundsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ca,self.context=certificates(Path(self.tmp.name));self.calls=[]
        class Stub:
            def handle(inner,envelope,**kwargs):self.calls.append(envelope);return ENVELOPE
        self.service=LoopbackWitnessService(Stub(),enabled=True,timeout_seconds=.25)
        self.thread=threading.Thread(target=lambda:self.service.serve_forever(poll_interval=.02),daemon=True);self.thread.start()
        def cleanup():
            self.service.shutdown();self.thread.join(2);self.service.server_close();self.assertFalse(self.thread.is_alive())
        self.addCleanup(cleanup)

    def test_bad_headers_body_length_and_path_never_reach_witness(self):
        body=_json(ENVELOPE).encode()
        valid=(f'POST {PATH} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n').encode()
        cases=[valid+b'Content-Length: 1\r\n\r\n'+body,
            valid+b'X-Large: '+b'x'*5000+b'\r\n\r\n'+body,
            valid.replace(PATH.encode(),b'/other')+b'\r\n'+body,
            b'POST '+PATH.encode()+b' HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 999999999\r\n\r\n']
        for request in cases:
            with self.subTest(request=request[:60]),closing(socket.create_connection(self.service.server_address,timeout=1)) as connection:
                try:connection.sendall(request);response=connection.recv(4096)
                except OSError:response=b''
                self.assertNotIn(b'X-Large',response)
        self.assertEqual(self.calls,[])

    def test_slow_request_is_closed_and_next_request_can_be_served(self):
        with closing(socket.create_connection(self.service.server_address,timeout=1)) as connection:
            connection.sendall(b'P');time.sleep(.35)
            try:response=connection.recv(4096)
            except OSError:response=b''
            self.assertEqual(response,b'')
        self.assertEqual(self.calls,[])
        exchange=SocketExchange(*self.service.server_address,allow_loopback_http=True,enabled=True)
        self.assertEqual(exchange(ENVELOPE,timeout_seconds=1,max_response_bytes=4096),ENVELOPE)
        self.assertEqual(len(self.calls),1)


if __name__=='__main__':unittest.main()
