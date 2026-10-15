#!/usr/bin/env python3
"""Shared fingerprint and archive utilities for version-pinned native patches."""
import hashlib, json, pathlib, plistlib, struct, subprocess
ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = ROOT / 'work'
APP = WORK / 'Claude Native Unlock Test.app'
BASELINE = WORK / 'baseline' / 'Claude.app'
PROFILE = WORK / 'profile'
VERSION = '2.31226.1'
ASAR_HASH = 'e0f0a70911d2d206f17572e82636c80a15173281db4966a44c2558e8f8ae6bb8'
FILES = {
 '.vite/build/index.chunk-iZtWjlXf.js': '93ac55f6bfc656fa774aa0029b03a326b86a6e0c265fad1fc402965d0f6eaa77',
 '.vite/build/index.pre.js': '37215841b4fa87e16f78b17f98895c2fcf72c62ee46d079db4f739c797567e2d',
 '.vite/build/index.chunk-Cyt2WTko.js': '29738ec09f4bf44d511caae628b0f786e35956848e6a18c3159c9928d6ac1ad9',
}
SOURCE_FILES = {
 'Contents/Info.plist': '0b0076d395dcff8e5b47c819d7fb1c0ce733e1bdcb3d2d4969bc83978b61b218',
 'Contents/MacOS/Claude': '76415d30fd41e3bcf55545a4a031ddd8ebac9806368ed5e76b2421e908d4b5a0',
 'Contents/Resources/app.asar.unpacked/node_modules/@ant/claude-swift/build/Release/computer_use.node': '5e4ec2a51641599a3c385f66421f7096730deec0d023f4c98029272a98142c57',
}
def sha(b): return hashlib.sha256(b).hexdigest()
def run(*args): return subprocess.run(args, check=True, capture_output=True)
def read_asar(path):
 b = path.read_bytes(); size = struct.unpack_from('<I', b, 4)[0]
 length = struct.unpack_from('<I', b, 12)[0]
 return b, json.loads(b[16:16+length]), 8+size

def node(header, path):
 n = header
 for part in path.split('/'): n = n['files'][part]
 return n

def content(b, h, base, path):
 n=node(h,path)
 if n.get('unpacked') or 'offset' not in n: raise ValueError('Target is not packed')
 off=base+int(n['offset']); return b[off:off+n['size']]

ADAPTER_37337 = {
 'version': '2.37337.1',
 'asarHash': '82a54170115e3e20c9600a257a5b2679cbadb83f38baf5111235f123af484163',
 'main': '.vite/build/index.chunk-BWk6pVUt.js',
 'featureAnchor': 'var E3t={',
 'webFeatureAnchor': 'D3t={',
 'webAvailability': '3264789062:{rule:"always",value:!0},3335190042:{rule:"always",value:!0},3858164079:{rule:"always",value:!0},',
 'files': {
  '.vite/build/index.pre.js': '1c1b002f474fcafc6b3a62d4d5a21ca573367eccc1541bd2e30308a6009c1453',
  '.vite/build/index.chunk-BWk6pVUt.js': 'f7d74e113f34761a675816fe2abf65b3ddc190327ee7623d9c822f7b59cceaca',
  '.vite/build/index.chunk-DWqiEHEK.js': 'bab1dcc6034f06cd4476d151ed63b42f5f7875aea0ba3a9e3664e40e679b8b04',
 },
 'sourceFiles': {
  'Contents/Info.plist': '642b028bf871166b5f0004c8d6936986d5e9e1850ce8cfb9ffb76fb049e03318',
  'Contents/MacOS/Claude': 'ff556d6959554d2b52f729dd2c90247d8206f503525b9c523197268b171c70a9',
  'Contents/Resources/app.asar.unpacked/node_modules/@ant/claude-swift/build/Release/computer_use.node': 'fc0664606674f7762bfcbe2dc7c2660bf9cc32b049e4ebd93e2de0c000c1cb60',
 }
}

def source_adapter(app):
 info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
 version=info['CFBundleShortVersionString']
 if version == VERSION:
  return {'version': VERSION, 'asarHash': ASAR_HASH, 'files': FILES, 'sourceFiles': SOURCE_FILES,
          'main': '.vite/build/index.chunk-Cyt2WTko.js', 'featureAnchor': 'var d2t={'}
 if version == ADAPTER_37337['version']:
  return ADAPTER_37337
 raise ValueError('Unknown version; no patch applied')

def verify_source(app):
 adapter=source_adapter(app)
 b,h,base=read_asar(app/'Contents/Resources/app.asar')
 if sha(b) != adapter['asarHash']: raise ValueError('ASAR fingerprint mismatch; no patch applied')
 for p,digest in adapter['files'].items():
  if sha(content(b,h,base,p)) != digest: raise ValueError('File fingerprint mismatch: '+p)
 for path,digest in adapter['sourceFiles'].items():
  if sha((app/path).read_bytes())!=digest: raise ValueError('Source component fingerprint mismatch: '+path)
 run('codesign','--verify','--deep','--strict',str(app))
 signature=run('codesign','-dvv',str(app)).stderr.decode()
 if 'TeamIdentifier=Q6L2SF6YDW' not in signature: raise ValueError('Source is not signed by the expected official team')
 return b,h,base

def replace_once(s, old, new):
 if s.count(old) != 1: raise ValueError('Patch match count != 1: '+old[:90])
 return s.replace(old,new,1)
