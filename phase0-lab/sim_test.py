import json, re, unicodedata
from difflib import SequenceMatcher
P='10.1109_tii.2020.2999571'
def norm(s): return ' '.join(unicodedata.normalize('NFKC', s).split())
paras=[norm(t.rstrip('\r')) for t in open(f'src/{P}/{P}.md',encoding='utf-8').read().split('\n') if t.strip() and not re.match(r'^#{1,6}\s',t)]

def window_best(q,para):
    nq,np_=len(q),len(para)
    if np_<=nq: return SequenceMatcher(None,q,para).ratio()
    best=0.0
    for i in range(0,np_-nq+1,max(1,nq//4)):
        r=SequenceMatcher(None,q,para[i:i+nq]).ratio()
        if r>best: best=r
    return best
def classify(q):
    nq=norm(q)
    for p in paras:
        if nq in p: return ('matched',1.0)
    best_r=max((window_best(nq,p) for p in paras),default=0.0)
    return ('matched' if best_r>=0.85 else 'broken' if best_r>=0.6 else 'unmatched'), best_r

# Skip non-body lines (backslash is a LaTeX macro start - use BS char variable)
BS=chr(92)
skip_prefixes=('!','[',BS,'<table','|','Fig.','Table','Index Terms','Mohsen','Color','Manuscript','The authors','Received','This work')
print('=== SIMULATED CORRECTED PROMPT (short verbatim fragments) ===')
fragments=[]
for p in paras:
    if len(p)<40: continue
    if any(p.startswith(s) for s in skip_prefixes): continue
    words=p.split()
    if len(words)<12: continue
    for start in [0, len(words)//3, len(words)//2]:
        end=min(start+14,len(words))
        fragments.append(' '.join(words[start:end]))
fragments=fragments[:30]
m=b=u=0
for q in fragments:
    st,r=classify(q)
    if st=='matched': m+=1
    elif st=='broken': b+=1
    else: u+=1
print(f'Tested {len(fragments)} simulated fragments')
print(f'Simulated short-fragment hit rate: {m}/{m+b+u} = {m/(m+b+u)*100:.1f}% (broken={b} unmatched={u})')
