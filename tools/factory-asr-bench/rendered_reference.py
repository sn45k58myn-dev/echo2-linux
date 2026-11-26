"""Generate a private NS6571 speaker-reference profile or an explicitly synthetic fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


CONFIG_SHA256 = '1ce154b23aada643962adaf07c3d139f29e2207d9ef75d6c77bb104a0ebbe53e'


def speaker_passthrough(original):
    if hashlib.sha256(original).hexdigest() != CONFIG_SHA256:
        raise ValueError('Unexpected NS6571 asp.cfg; original profile required')
    cfg=json.loads(original)
    speaker=cfg['Pipelines']['Speaker']
    if speaker['Passthrough'] is not False or cfg['Pipelines']['ASR']['Passthrough'] is not False:
        raise ValueError('Unexpected speaker/ASR profile')
    speaker['Passthrough']=True
    return (json.dumps(cfg,indent=2)+'\n').encode('utf8')


def synthetic_input_loop(mics_i32,ref_s16):
    """Synthesize both 24-bit digital lanes from the preceding 8 ms reference block."""
    block=128*9*4
    blocks,extra=divmod(len(mics_i32),block)
    if not blocks or extra or len(ref_s16)!=blocks*384*4: raise ValueError('incompatible fixture')
    out=bytearray(blocks*128*27)
    for frame in range(blocks*128):
        vals=list(struct.unpack_from('<9i',mics_i32,frame*36))
        if frame<128: vals[7:]=[0,0]
        else: vals[7:]=[256*x for x in struct.unpack_from('<hh',ref_s16,12*(frame-128))]
        for k,v in enumerate(vals): out[frame*27+3*k:frame*27+3*k+3]=v.to_bytes(3,'little',signed=True)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    cfg=commands.add_parser('config'); cfg.add_argument('original',type=Path)
    fx=commands.add_parser('synthetic'); fx.add_argument('mics_i32',type=Path); fx.add_argument('stereo_s16',type=Path)
    for cmd in (cfg,fx): cmd.add_argument('output',type=Path)
    args=parser.parse_args()
    data=(speaker_passthrough(args.original.read_bytes()) if args.command=='config' else
          synthetic_input_loop(args.mics_i32.read_bytes(),args.stereo_s16.read_bytes()))
    with args.output.open('xb') as out: out.write(data)
    print(hashlib.sha256(data).hexdigest())
