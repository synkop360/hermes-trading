"""Lossless bounded packet compression with transactional shared objects."""
from copy import deepcopy
import hashlib,json,zlib
from hermes_trading.reflection_supervision import _hash,_json

MAGIC=b'PCP1'
MAX_OBJECT_BYTES=16*1024*1024
MAX_PACKET_BYTES=64*1024*1024


def _inflate(data,limit):
    try:
        if not isinstance(data,bytes) or len(data)>limit+131072:raise ValueError('compressed packet exceeds bound')
        decoder=zlib.decompressobj();raw=decoder.decompress(data,limit+1)
        if len(raw)>limit or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('compressed evidence is oversized, incomplete or trailing')
        return raw
    except zlib.error as error:raise ValueError('compressed evidence is corrupt') from error


def _valid_path(path):
    return isinstance(path,list) and all(isinstance(part,str) for part in path) and (
        len(path)==1 and path[0] in {'release','archives','strategies','bindings'} or
        len(path)==4 and path[0]=='snapshots' and path[2]=='price' and path[3] in {'closes','candles'})


def store_record(db,record):
    """Compress a journal dictionary without packet-specific shared paths."""
    if not db.in_transaction:raise ValueError('record storage requires its journal transaction')
    if not isinstance(record,dict):raise ValueError('journal record must be an object')
    raw=_json({'schema_version':1,'inline':record,'refs':[],'input_hash':_hash(record)}).encode()
    if len(raw)>MAX_PACKET_BYTES:raise ValueError('journal record exceeds decoded bound')
    return MAGIC+zlib.compress(raw,6)


def store_packet(db,packet):
    """Caller must commit objects and financial packet in one transaction."""
    if not db.in_transaction:raise ValueError('packet storage requires the financial transaction')
    original=_json(packet).encode()
    if len(original)>MAX_PACKET_BYTES:raise ValueError('packet exceeds decoded bound')
    db.execute('CREATE TABLE IF NOT EXISTS paired_packet_objects (sha256 TEXT PRIMARY KEY,data BLOB NOT NULL,raw_bytes INTEGER NOT NULL)')
    inline=deepcopy(packet);refs=[];referenced_bytes=0
    paths=[[key] for key in ('release','archives','strategies','bindings') if key in packet]
    for asset,snapshot in packet.get('snapshots',{}).items():
        for key in ('closes','candles'):
            if key in (snapshot.get('price') or {}):paths.append(['snapshots',asset,'price',key])
    for path in paths:
        parent=inline
        for part in path[:-1]:parent=parent[part]
        raw=_json(parent[path[-1]]).encode()
        if len(raw)>MAX_OBJECT_BYTES:raise ValueError('packet object exceeds decoded bound')
        if len(raw)<128:continue
        digest=hashlib.sha256(raw).hexdigest()
        db.execute('INSERT OR IGNORE INTO paired_packet_objects VALUES (?,?,?)',(digest,zlib.compress(raw,6),len(raw)))
        stored=db.execute('SELECT data,raw_bytes FROM paired_packet_objects WHERE sha256=?',(digest,)).fetchone()
        if stored[1]!=len(raw) or _inflate(stored[0],MAX_OBJECT_BYTES)!=raw:raise ValueError('existing packet object is corrupt')
        parent[path[-1]]=None;refs.append({'path':path,'sha256':digest})
        referenced_bytes+=len(raw)
    envelope={'schema_version':1,'inline':inline,'refs':refs,'input_hash':_hash(packet)}
    envelope_raw=_json(envelope).encode()
    if len(envelope_raw)+referenced_bytes>MAX_PACKET_BYTES:
        raise ValueError('packet reconstruction exceeds decoded bound')
    return MAGIC+zlib.compress(envelope_raw,6)


def load_packet(db,payload):
    if isinstance(payload,str):
        if len(payload.encode())>MAX_PACKET_BYTES:raise ValueError('legacy packet exceeds decoded bound')
        packet=json.loads(payload)
        if not isinstance(packet,dict):raise ValueError('legacy packet must be an object')
        return packet
    if not isinstance(payload,bytes) or not payload.startswith(MAGIC):raise ValueError('unknown packet codec')
    envelope_raw=_inflate(payload[len(MAGIC):],MAX_PACKET_BYTES)
    envelope=json.loads(envelope_raw)
    if not isinstance(envelope,dict) or set(envelope)!={'schema_version','inline','refs','input_hash'} or type(envelope['schema_version']) is not int or envelope['schema_version']!=1:
        raise ValueError('packet envelope is invalid')
    packet=envelope['inline'];refs=envelope['refs'];seen=set();total=len(envelope_raw)
    if not isinstance(packet,dict) or not isinstance(refs,list):raise ValueError('packet envelope records are invalid')
    try:
        for reference in refs:
            if not isinstance(reference,dict) or set(reference)!={'path','sha256'} or not _valid_path(reference['path']):
                raise ValueError('packet reference path is invalid')
            path=reference['path'];digest=reference['sha256']
            if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('packet object digest is invalid')
            if tuple(path) in seen:raise ValueError('duplicate packet reference path')
            seen.add(tuple(path));parent=packet
            for part in path[:-1]:parent=parent[part]
            if parent[path[-1]] is not None:raise ValueError('packet reference overwrites inline evidence')
            row=db.execute('SELECT data,raw_bytes FROM paired_packet_objects WHERE sha256=?',(digest,)).fetchone()
            if row is None:raise ValueError('packet object is missing')
            if type(row[1]) is not int or not 0<=row[1]<=MAX_OBJECT_BYTES:raise ValueError('packet object length is invalid')
            total+=row[1]
            if total>MAX_PACKET_BYTES:raise ValueError('packet references exceed decoded bound')
            raw=_inflate(row[0],MAX_OBJECT_BYTES)
            if len(raw)!=row[1] or hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('packet object hash/length differs')
            parent[path[-1]]=json.loads(raw)
    except (KeyError,TypeError,AttributeError) as error:raise ValueError('packet reference cannot be reconstructed') from error
    if len(_json(packet).encode())>MAX_PACKET_BYTES or _hash(packet)!=envelope['input_hash']:
        raise ValueError('reconstructed packet hash/size differs')
    return packet
