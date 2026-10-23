"""Scan every file for certainty; transfer only content changed from the baseline."""

from __future__ import annotations

SCAN_SCRIPT = r"""import os,json,stat,base64,fnmatch,hashlib
root='/workspace'; manifest={}; changed={}; total=0
with open('/tmp/audoryn-baseline-manifest.json') as f: baseline=json.load(f)
generated={'.pytest_cache','.mypy_cache','.ruff_cache','node_modules','.venv','__pycache__'}
for directory,dirs,files in os.walk(root,followlinks=False):
 def keep(d):
  full=os.path.join(directory,d)
  if os.path.islink(full): raise ValueError('Unsafe workspace directory')
  prefix=os.path.relpath(full,root).replace(os.sep,'/')+'/'
  return d!='.git' and (d not in generated or any(p.startswith(prefix) for p in baseline))
 dirs[:]=[d for d in dirs if keep(d)]
 for name in files:
  path=os.path.join(directory,name); relative=os.path.relpath(path,root).replace(os.sep,'/')
  if relative not in baseline and (fnmatch.fnmatch(name,'*.pyc') or name=='.coverage' or name.startswith('.coverage.')): continue
  if not stat.S_ISREG(os.lstat(path).st_mode) or os.path.commonpath([os.path.realpath(path),root])!=root: raise ValueError('Unsafe workspace path')
  size=os.path.getsize(path)
  if size>2000000: raise ValueError('Workspace file exceeds export budget')
  with open(path,'rb') as f: raw=f.read(2000001)
  if len(raw)!=size: raise ValueError('Workspace changed during export')
  total+=len(raw)
  if total>40000000 or len(manifest)>=10000: raise ValueError('Workspace export exceeds budget')
  item={'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}; manifest[relative]=item
  if item!=baseline.get(relative):
   try: changed[relative]=raw.decode('utf-8')
   except UnicodeDecodeError: changed[relative]={'binaryBase64':base64.b64encode(raw).decode()}
print(json.dumps({'manifest':manifest,'changed':changed}))"""
