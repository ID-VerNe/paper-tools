"""Phase 0.5 — final, decisive run. Three sub-tests in one."""
import json, re, unicodedata, glob, os
from difflib import SequenceMatcher

P='10.1109_tii.2020.2999571'
DERIVED=f'src/output/derived'
OCR_MD=f'src/{P}/{P}.md'

def norm(s): return ' '.join(unicodedata.normalize('NFKC', s).split())
def ratio(a,b): return SequenceMatcher(None,a,b).ratio()
def paras(path):
    out=[]
    for raw in open(path,encoding='utf-8').read().split('\n'):
        t=raw.rstrip('\r')
        if not t.strip() or re.match(r'^#{1,6}\s',t): continue
        out.append(norm(t))
    return out
PARAS=paras(OCR_MD)

def window_best(q,p):
    nq,np_=len(q),len(p)
    if np_<=nq: return ratio(q,p)
    best=0.0
    for i in range(0,np_-nq+1,max(1,nq//4)):
        r=ratio(q,p[i:i+nq])
        if r>best: best=r
    return best
def classify(q):
    nq=norm(q)
    if not nq: return ('unmatched',0.0)
    for p in PARAS:
        if nq in p: return ('matched',1.0)
    best_r=max((window_best(nq,p) for p in PARAS),default=0.0)
    return ('matched' if best_r>=0.85 else 'broken' if best_r>=0.6 else 'unmatched'), best_r

print(f'OCR paragraphs loaded: {len(PARAS)}\n')

# --- A. Positive control: 5 verbatim OCR sentences truncated to quote length ---
print('=== A. POSITIVE CONTROL (verbatim OCR prefix, 14 words) ===')
BS=chr(92)
skip=('!','[',BS,'<table','|','Fig.','Table','Index','Mohsen','Color','Manuscript','The authors','Received','This work')
ctrl=[p for p in PARAS if len(p)>40 and not any(p.startswith(s) for s in skip)][:5]
for p in ctrl:
    q=' '.join(p.split()[:14])
    st,r=classify(q)
    print(f'  [{st} r={r:.2f}] {q[:70]}')

# --- B. Current prompt output: inline '...' quotes ---
print('\n=== B. CURRENT PROMPT — inline quote spans ===')
files=sorted(glob.glob(os.path.join(DERIVED,f'{P}_*.json')))
tot={'matched':0,'broken':0,'unmatched':0,'no_quote':0,'fields':0}
for f in files:
    m=os.path.basename(f).replace(P+'_','').replace('.json','')
    d=json.load(open(f,encoding='utf-8'))
    for g,fs in d.items():
        for k,v in fs.items():
            tot['fields']+=1
            if not isinstance(v,str) or not v.strip() or v in ('not reported','null'):
                tot['no_quote']+=1; continue
            qs=[x for x in re.findall(r"'([^']{8,300})'",v)]
            if not qs:
                tot['no_quote']+=1; continue
            for q in qs:
                st,r=classify(q)
                tot[st]+=1
                print(f'  [{st} r={r:.2f}] {m}/{g}.{k}: {q[:65]}')

# --- C. Dedicated evidence_quote field, split on ellipsis ---
print('\n=== C. DEDICATED evidence_quote field (split on ellipsis) ===')
for f in files:
    if 'physics_defense' not in f: continue
    m=os.path.basename(f).replace(P+'_','').replace('.json','')
    d=json.load(open(f,encoding='utf-8'))
    q=d.get('physics_informed_defense',{}).get('evidence_quote_for_physics_use','')
    if not q: continue
    st_wh,r_wh=classify(q)
    print(f'  {m} whole-field: {st_wh} r={r_wh:.2f}')
    frags=[x.strip() for x in re.split(r'\.{3,}|\s...\s|…', q) if x.strip()]
    for i,fr in enumerate(frags):
        st,r=classify(fr)
        print(f'    frag{i+1} [{st} r={r:.2f}] {fr[:75]}')

# --- D. Decisive simulation: short verbatim fragments (corrected-prompt output) ---
print('\n=== D. SIMULATED CORRECTED PROMPT (12-18 word verbatim fragments) ===')
frags=[]
for p in PARAS:
    if len(p)<40 or any(p.startswith(s) for s in skip): continue
    w=p.split()
    if len(w)<12: continue
    for s in [0,len(w)//3,len(w)//2]:
        frags.append(' '.join(w[s:min(s+14,len(w))]))
frags=frags[:60]
m_=b_=u_=0
for q in frags:
    st,_=classify(q)
    if st=='matched': m_+=1
    elif st=='broken': b_+=1
    else: u_+=1
print(f'  Tested {len(frags)} fragments: matched={m_} broken={b_} unmatched={u_}')
print(f'  Hit rate: {m_}/{len(frags)} = {m_/len(frags)*100:.1f}%')

print('\n=== SUMMARY ===')
q_=tot['matched']+tot['broken']+tot['unmatched']
print(f'Current prompt: {tot["matched"]}/{q_} quotable fields match ({tot["no_quote"]}/{tot["fields"]} fields have NO quote at all)')
print(f'Simulated corrected prompt: {m_}/{len(frags)} = {m_/len(frags)*100:.1f}%')
