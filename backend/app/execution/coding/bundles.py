"""Deterministic source bundles and a trusted, independent sandbox extractor."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import tarfile

from .snapshot import validated_snapshot

BUNDLE_BYTES = 4 * 1024 * 1024


def source_bytes(value):
    return (
        base64.b64decode(value["binaryBase64"], validate=True)
        if isinstance(value, dict)
        else value.encode()
    )


def source_manifest(files):
    result = {}
    for path, value in sorted(files.items()):
        raw = source_bytes(value)
        result[path] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
    return result


def manifest_fingerprint(manifest):
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_bundles(files):
    groups, current, size = [], [], 0
    for path, value in sorted(files.items()):
        raw = source_bytes(value)
        if current and size + len(raw) > BUNDLE_BYTES:
            groups.append(current)
            current, size = [], 0
        current.append((path, raw))
        size += len(raw)
    if current:
        groups.append(current)
    result = []
    for group in groups:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for path, raw in group:
                info = tarfile.TarInfo("source/" + path)
                info.size, info.mode, info.mtime = len(raw), 0o644, 0
                archive.addfile(info, io.BytesIO(raw))
        # Fast deterministic compression reduces upload bytes without expensive
        # maximum-compression work or executing repository-controlled tooling.
        payload = gzip.compress(buffer.getvalue(), compresslevel=1, mtime=0)
        # Independent backend validation; never package arbitrary archive entries.
        validated_snapshot(payload, include_binary=True)
        result.append((payload, [path for path, _ in group]))
    return result


# This helper is backend-controlled, never imported from repository content.
# /tmp inputs contain only validated source/manifest; no token is sent to E2B.
INITIALIZE_HELPER = r"""import os,sys,json,hashlib,tarfile,stat,tempfile
root='/workspace'
with open('/tmp/audoryn-source-manifest.json') as f: spec=json.load(f)
files=spec['files']; bundles=spec['bundles']
def destination(path):
 if not path or path.startswith('/') or any(ord(c)<32 or c==chr(92) for c in path) or any(p in ('','.', '..','.git') for p in path.split('/')): raise ValueError('Unsafe source path')
 full=os.path.join(root,path)
 if os.path.commonpath([os.path.realpath(full),root])!=root: raise ValueError('Unsafe destination')
 parent=root
 for component in path.split('/')[:-1]:
  parent=os.path.join(parent,component)
  if os.path.lexists(parent) and (os.path.islink(parent) or not os.path.isdir(parent)): raise ValueError('Unsafe parent')
 if os.path.lexists(full) and not stat.S_ISREG(os.lstat(full).st_mode): raise ValueError('Unsafe destination type')
 return full
def matches(paths):
 for path in paths:
  full=destination(path); expected=files[path]
  if not os.path.isfile(full) or os.path.getsize(full)!=expected['size']: return False
  with open(full,'rb') as f:
   if hashlib.sha256(f.read(2000001)).hexdigest()!=expected['sha256']: return False
 return True
mode=sys.argv[1]; index=int(sys.argv[2]) if len(sys.argv)>2 else None
paths=bundles[index]['paths'] if index is not None else list(files)
os.makedirs(root,exist_ok=True)
if mode=='apply':
 bundle=bundles[index]; archive_path='/tmp/audoryn-source-'+str(index)+'.tar'
 with open(archive_path,'rb') as f:
  if hashlib.sha256(f.read()).hexdigest()!=bundle['sha256']: raise ValueError('Bundle checksum mismatch')
 seen=set(); total=0
 with tarfile.open(archive_path,'r:*') as archive:
  for entry in archive:
   path=entry.name.partition('/')[2]
   if not entry.name.startswith('source/') or path not in paths or path in seen or not entry.isfile() or entry.issym() or entry.islnk(): raise ValueError('Unexpected bundle entry')
   expected=files[path]; total+=entry.size
   if entry.size!=expected['size'] or entry.size>2000000 or total>4194304: raise ValueError('Bundle size mismatch')
   raw=archive.extractfile(entry).read(entry.size+1)
   if hashlib.sha256(raw).hexdigest()!=expected['sha256']: raise ValueError('Source checksum mismatch')
   full=destination(path); os.makedirs(os.path.dirname(full),exist_ok=True)
   fd,tmp=tempfile.mkstemp(dir=os.path.dirname(full))
   try:
    with os.fdopen(fd,'wb') as out: out.write(raw)
    os.chmod(tmp,0o644); os.replace(tmp,full)
   finally:
    if os.path.exists(tmp): os.unlink(tmp)
   seen.add(path)
 if seen!=set(paths): raise ValueError('Incomplete bundle')
if mode=='verify-all':
 observed=set()
 for directory,dirs,names in os.walk(root,followlinks=False):
  if any(os.path.islink(os.path.join(directory,d)) for d in dirs): raise ValueError('Unsafe source directory')
  for name in names: observed.add(os.path.relpath(os.path.join(directory,name),root).replace(os.sep,'/'))
 if observed!=set(files): raise ValueError('Unexpected workspace content')
available=False
if index is not None:
 archive_path='/tmp/audoryn-source-'+str(index)+'.tar'
 if os.path.isfile(archive_path) and not os.path.islink(archive_path) and os.path.getsize(archive_path)<=16000000:
  with open(archive_path,'rb') as f: available=hashlib.sha256(f.read()).hexdigest()==bundles[index]['sha256']
print(json.dumps({'complete':matches(paths),'files':len(paths),'bundleAvailable':available}))
"""
