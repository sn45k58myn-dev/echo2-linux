#!/usr/bin/python3
"""Integration exercise in an empty Docker namespace only. Generates only synthetic data."""
import json
import os
from pathlib import Path
import random
import socket
import struct
import subprocess
import sys
import threading
import time

def exact(fp,n):
    out=fp.read(n)
    assert len(out)==n
    return out

def factory():
    inp,out=sys.stdin.buffer,sys.stdout.buffer
    out.write(b"E2ASRDY\n");out.flush()
    refs=0
    while h:=inp.read(24):
        first,ns,flags,n=struct.unpack("<QQII",h)
        assert n==128 and flags in (0,2)
        raw=exact(inp,27*n)
        if flags:
            expected=struct.pack("<768h",*[x for x in rend[3*first+384:3*first+768] for _ in range(2)])
            assert exact(inp,1536)==expected
            for i in range(128):
                frame=raw[27*i:27*i+27]
                assert frame[:21]==bytes(range(21))
                assert frame[21:24]==frame[24:]==b"\0"+struct.pack("<h",rend[3*(first+i)])
            refs+=1
        out.write(h+struct.pack("<128h",*range(first,first+n)));out.flush()
    assert refs>8

def scorer():
    print(json.dumps(dict(ready=True),separators=(",",":")),flush=True)
    for i in range(hops):
        inp=sys.stdin.buffer
        m,v,flags,first,ns,res=struct.unpack("<4sHHQQQ",exact(inp,32))
        assert m==b"E2SW" and first==i*1280 and flags in (0,2)
        data=struct.pack("<1280h",*range(first,first+1280))
        assert exact(inp,2560)==data and exact(inp,2560)==data
        print(json.dumps(dict(end_sample=first+1280,flags=flags)),flush=True)

def simulation():
    assert Path("/.dockerenv").exists() and os.getuid()==0
    root=Path("/run/libreecho"); stage=root/"groot-paired-diag"; audio=Path("/run/libreecho-audio")
    stage.mkdir(parents=True);audio.mkdir()
    os.mkfifo(stage/"factory-ha.sock",0o600)
    observer=os.open(stage/"factory-ha.sock",os.O_RDWR)
    seen=[]
    def observe():
        for _ in range(hops*10):
            msg=os.read(observer,264)
            first,*pcm=struct.unpack("<Q128h",msg)
            assert first==len(seen)*128
            assert pcm==[x&~1 for x in range(first,first+128)]
            seen.append(first)
    observe_thread=threading.Thread(target=observe,daemon=True)
    observe_thread.start()
    fifo=stage/"raw";os.mkfifo(fifo,0o600)
    wake=socket.socket(socket.AF_UNIX);wake.bind(str(root/"wakeword.sock"));wake.listen()
    gate=threading.Event()
    def baseline():
        s,_=wake.accept()
        assert s.recv(4096).endswith(b"\n")
        s.sendall(b'{"ok":true,"data":{"sample_indexed":true}}\n')
        gate.wait(3)
        for ix in range(0,hops*1280,160):
            s.sendall(struct.pack("<IHHQII160h",0x3153564c,1,0,ix,160,0,*range(ix,ix+160)))
        time.sleep(4)
    t=threading.Thread(target=baseline,daemon=True);t.start()
    target=Path("/tmp/echo2-synth");target.mkdir()
    (target/"session.sh").write_text("exec /usr/bin/python3 /lab/test-router.py factory\n")
    (target/"score").write_text("#!/bin/sh\nexec /usr/bin/python3 /lab/test-router.py score\n")
    (target/"score").chmod(0o700)
    p=subprocess.Popen(sys.argv[1:2]+[str(target),"/unused",str(target/"score"),str(hops)],
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        assert b'"ready":true' in p.stdout.readline()
        (stage/"armed").write_text("1\n")
        gate.set(); start=time.monotonic_ns()
        s=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
        s.connect(str(audio/"prebeam-ref.sock"));n=768
        # The software renderer already has the next blocks. Never fabricate them at the router.
        sent=0
        with fifo.open("wb",buffering=0) as f:
            for b in range(125):
                while sent<(b+4)*384:
                    h=struct.pack("<IHHIIHHIQQ",0x5241454c,1,40,sent//n,48000,1,n,1,sent,start+round(sent*1e9/48000))
                    s.send(h+struct.pack("<768h",*rend[sent:sent+n]));sent+=n
                first=b*128;raw=bytearray()
                for i in range(128):
                    v=rend[3*(first+i)]
                    ceil=(first+i)%3>0
                    invalid=(os.environ.get("ROUTER_TEST_ALIGNMENT_LOSS")=="1" and 40<=b<48)
                    raw.extend(bytes(range(21))+bytes((1 if invalid else 0,))+struct.pack("<h",v-int(ceil))+b"\0\0"+bytes((225 if ceil else 12,)))
                f.write(struct.pack("<IIQQ",0x31574152,128,first,time.monotonic_ns())+raw)
                time.sleep(.008)
        out,err=p.communicate(timeout=8)
        assert p.returncode==0,(p.returncode,err)
        rows=[json.loads(line) for line in out.splitlines()]
        assert len(rows)==hops and rows[-1]["flags"]==2 and all((r["flags"]&1)==0 for r in rows)
        assert b"proven_blocks=2" in err and b"vendor_locked_frames=0" in err
        if os.environ.get("ROUTER_TEST_ALIGNMENT_LOSS")=="1":
            assert b"lost_measured_alignment" in err and err.count(b"proven_blocks=2")==2
        observe_thread.join(5)
        assert len(seen)==hops*10, (len(seen),hops)
        assert b"router assist FIFO sent=120 full=0 unavailable=0" in err,err
        print(f"Router delivered {len(seen)} consecutive indexed genuine factory-output fixture blocks with metadata bit removed.")
        print(err.decode().strip())
    finally:
        if p.poll() is None:
            p.kill();p.wait()

hops=12
rand=random.Random(8134)
rend=[rand.randrange(-3000,3000) for _ in range(hops*1280*3+5000)]
if __name__=="__main__":
    if sys.argv[1]=="factory":factory()
    elif sys.argv[1]=="score":scorer()
    else:simulation()
