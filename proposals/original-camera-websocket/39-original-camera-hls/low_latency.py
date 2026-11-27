"""FLV AVC to raw Annex B WebSocket, no decoding and no unbounded queues."""
import base64, hashlib, queue, select, struct, threading, time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit
MAX_TAG=2*1024*1024
START=b'\x00\x00\x00\x01'

def exact(f,n):
    if n<0 or n>MAX_TAG: raise ValueError('FLV size limit')
    out=bytearray()
    while len(out)<n:
        part=f.read(n-len(out))
        if not part: raise EOFError('FLV ended')
        out.extend(part)
    return bytes(out)

def avcc(data):
    if len(data)<7 or data[0]!=1: raise ValueError('Invalid AVC configuration')
    width=(data[4]&3)+1; i=6; units=[]
    for count_type in range(2):
        if count_type==0: count=data[5]&31
        else:
            if i>=len(data): raise ValueError('Missing PPS')
            count=data[i]; i+=1
        for _ in range(count):
            if i+2>len(data): raise ValueError('Truncated AVC configuration')
            n=int.from_bytes(data[i:i+2],'big'); i+=2
            if not n or i+n>len(data): raise ValueError('Invalid AVC parameter set')
            units.append(data[i:i+n]); i+=n
    if not any(x[0]&31==7 for x in units) or not any(x[0]&31==8 for x in units): raise ValueError('Missing SPS/PPS')
    return width,b''.join(START+x for x in units)

def access_units(f):
    head=exact(f,9)
    if head[:3]!=b'FLV': raise ValueError('Not FLV')
    offset=int.from_bytes(head[5:9],'big')
    if offset<9 or offset>4096: raise ValueError('Invalid FLV header')
    exact(f,offset-9); exact(f,4)
    width=0; params=b''
    while True:
        header=exact(f,11); n=int.from_bytes(header[1:4],'big'); payload=exact(f,n)
        if int.from_bytes(exact(f,4),'big')!=n+11: raise ValueError('Invalid FLV tag size')
        if header[0]!=9 or len(payload)<5: continue
        if payload[0]&15!=7: raise ValueError('Expected H.264 AVC')
        if payload[1]==0: width,params=avcc(payload[5:]); continue
        if payload[1]!=1 or not width: continue
        data=payload[5:]; pos=0; units=[]
        while pos<len(data):
            if pos+width>len(data): raise ValueError('Truncated NAL length')
            size=int.from_bytes(data[pos:pos+width],'big'); pos+=width
            if not size or pos+size>len(data): raise ValueError('Invalid NAL length')
            units.append(data[pos:pos+size]); pos+=size
        key=any(n[0]&31==5 for n in units)
        if units: yield key,(params if key else b'')+b''.join(START+n for n in units)

class Hub:
    def __init__(self): self.lock=threading.Lock(); self.clients=set(); self.last=0; self.frames=0
    def subscribe(self):
        with self.lock:
            if len(self.clients)>=3: return None
            c=queue.Queue(4); self.clients.add(c); return c
    def unsubscribe(self,c):
        with self.lock: self.clients.discard(c)
    def send(self,key,data):
        with self.lock:
            self.last=time.monotonic(); self.frames+=1
            for c in self.clients:
                try: c.put_nowait((key,data))
                except queue.Full:
                    # Drop stale GOP instead of adding latency; next IDR resynchronizes.
                    while True:
                        try: c.get_nowait()
                        except queue.Empty: break
                    c.put_nowait(None)
    def reset(self):
        with self.lock:
            self.last=0
            for c in self.clients:
                while True:
                    try: c.get_nowait()
                    except queue.Empty: break
                c.put_nowait(None)
    def status(self):
        with self.lock: return len(self.clients),self.frames,self.last

HUB=Hub()

def wsframe(data,opcode=2):
    n=len(data)
    return bytes([0x80|opcode])+(bytes([n]) if n<126 else b'\x7e'+struct.pack('!H',n) if n<65536 else b'\x7f'+struct.pack('!Q',n))+data

class WSHandler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*_): pass
    def control(self):
        # Browser control frames are masked; do not accept data or large frames.
        if not select.select([self.connection],[],[],0)[0]: return True
        def recv_exact(n):
            b=bytearray()
            while len(b)<n:
                x=self.connection.recv(n-len(b))
                if not x: raise EOFError()
                b.extend(x)
            return bytes(b)
        a,b=recv_exact(2); opcode=a&15; n=b&127
        if (a&0xf0)!=0x80 or not b&128 or n>125 or opcode not in (8,9,10): return False
        mask=recv_exact(4); payload=bytes(x^mask[i%4] for i,x in enumerate(recv_exact(n)))
        if opcode==8:
            self.connection.sendall(wsframe(payload,8)); return False
        if opcode==9: self.connection.sendall(wsframe(payload,10))
        return True
    def do_GET(self):
        self.close_connection=True
        if self.path!='/h264': self.send_error(404); return
        # Only same printer browser origin, or local/native clients without Origin.
        try:
            origin=self.headers.get('Origin'); host=self.server.server_address[0]
            if origin and (urlsplit(origin).scheme not in ('http','https') or urlsplit(origin).hostname!=host): raise ValueError()
            if self.headers.get('Upgrade','').lower()!='websocket' or 'upgrade' not in [v.strip().lower() for v in self.headers.get('Connection','').split(',')]: raise ValueError()
            if self.headers.get('Sec-WebSocket-Version')!='13': raise ValueError()
            key=self.headers['Sec-WebSocket-Key']; assert len(base64.b64decode(key,validate=True))==16
        except (ValueError,TypeError,AssertionError): self.send_error(400); return
        c=HUB.subscribe()
        if c is None: self.send_error(503); return
        try:
            self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade')
            self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()); self.end_headers()
            self.connection.settimeout(2); synced=False
            last=time.monotonic()
            while self.control():
                try: item=c.get(timeout=.25)
                except queue.Empty:
                    if time.monotonic()-last>8: break
                    continue
                last=time.monotonic()
                if item is None: synced=False; continue
                key,data=item
                if key: synced=True
                if synced: self.connection.sendall(wsframe(data))
        except (OSError,EOFError): pass
        finally: HUB.unsubscribe(c)
