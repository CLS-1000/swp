#!/usr/bin/env python3
"""Gate 1 v3: EDC-1057 -> clone_clusters.db. Regex anchor + indent-based make detection."""
import re, sqlite3

ROW = re.compile(
    r'^(?P<head>.*?)\s+(?P<body>[fu](?:/[fu])?)\s+'
    r'(?P<drv>,?[fr4a](?:\s*[,/]\s*[fr4a])*)\s+'
    r'(?P<y0>\d{4})\s+(?P<y1>\d{4})(?P<tail>.*)$')
WB  = re.compile(r'^[\d.]+(,[\d.]+)*$')
sec = re.compile(r'^\s+\d{4}\s+All Cars'); pg = re.compile(r'^\s*Page \d+\s*$')
MAKE_MAX_INDENT = 10

rows, skipped = [], []
rem_off = None
for page in open('edc1057.txt', encoding='utf-8', errors='replace').read().split('\f'):
    for line in page.splitlines():
        if not line.strip() or pg.match(line) or sec.match(line): continue
        if 'MAKE' in line and 'MODEL' in line and 'STYLES' in line:
            rem_off = line.find('REMARKS'); continue
        m = ROW.match(line)
        if not m:
            if len(line.strip()) > 25 and re.search(r'\d{4}\s+\d{4}', line): skipped.append(line.rstrip())
            continue
        head = m.group('head'); indent = len(line) - len(line.lstrip())
        toks = re.split(r'\s{2,}', head.strip())
        make = ''
        if indent <= MAKE_MAX_INDENT and len(toks) >= 2:
            make = toks[0]; toks = toks[1:]
        model = toks[0] if toks else ''
        wb, styles = '', ''
        if len(toks) >= 2:
            if WB.match(toks[-1]): wb = toks[-1]; styles = ' '.join(toks[1:-1])
            else: styles = ' '.join(toks[1:])
        tail = m.group('tail')
        cm = re.search(r'(\d{6})\s*$', tail)
        clone = cm.group(1) if cm else None
        tb = tail[:cm.start()] if cm else tail
        abs_start = m.start('tail'); alt = remarks = ''
        if rem_off and rem_off > abs_start and rem_off - abs_start < len(tb):
            cut = rem_off - abs_start
            alt, remarks = tb[:cut].strip(), tb[cut:].strip()
        else:
            alt = tb.strip()
        rows.append(dict(make=make, model=model, styles=styles, wb=wb,
            body=m.group('body'), drive=re.sub(r'\s+','',m.group('drv')),
            y0=int(m.group('y0')), y1=int(m.group('y1')), alt=alt, remarks=remarks, clone=clone))

cur=None
for r in rows:
    if r['make']: cur=r['make']
    else: r['make']=cur

seen,uniq=set(),[]
for r in rows:
    k=(r['make'],r['model'],r['styles'],r['y0'],r['y1'],r['clone'])
    if k in seen: continue
    seen.add(k); uniq.append(r)

con=sqlite3.connect('clone_clusters.db'); c=con.cursor()
c.executescript("""
DROP TABLE IF EXISTS vehicles; DROP TABLE IF EXISTS clusters;
DROP TABLE IF EXISTS membership; DROP TABLE IF EXISTS verdicts;
CREATE TABLE vehicles(id INTEGER PRIMARY KEY, make TEXT, model TEXT, styles TEXT,
  wheelbase TEXT, construction TEXT, drive TEXT, year_start INT, year_end INT,
  alt_models TEXT, remarks TEXT, source TEXT DEFAULT 'EDC-1057');
CREATE TABLE clusters(cluster_id TEXT PRIMARY KEY, type TEXT, label TEXT, source TEXT);
CREATE TABLE membership(vehicle_id INT, cluster_id TEXT, scope TEXT, confidence TEXT);
CREATE TABLE verdicts(id INTEGER PRIMARY KEY, date TEXT, part TEXT, method TEXT,
  cluster_id TEXT, result TEXT, notes TEXT);
CREATE INDEX idx_v ON vehicles(make,model); CREATE INDEX idx_m ON membership(cluster_id);""")
for r in uniq:
    c.execute("""INSERT INTO vehicles(make,model,styles,wheelbase,construction,drive,
      year_start,year_end,alt_models,remarks) VALUES(?,?,?,?,?,?,?,?,?,?)""",
      (r['make'],r['model'],r['styles'],r['wb'],r['body'],r['drive'],r['y0'],r['y1'],r['alt'],r['remarks']))
    vid=c.lastrowid
    if r['clone']:
        c.execute("INSERT OR IGNORE INTO clusters VALUES(?,?,?,?)",(r['clone'],'structural',None,'EDC-1057'))
        c.execute("INSERT INTO membership VALUES(?,?,?,?)",(vid,r['clone'],'structural','edc'))
c.execute("INSERT INTO clusters VALUES('J37-LOOP','engine_family','Honda J37 3.7L V6 engine loop','curated')")
c.execute("INSERT INTO clusters VALUES('J35-ADJ','engine_family','Honda J35 3.5L V6 adjacent (accessories only)','curated')")
def link(pool,cid,scope):
    for mk,md,y0,y1 in pool:
        q="SELECT id FROM vehicles WHERE make=? AND model=?"; p=[mk,md]
        if y0: q+=" AND year_start<=? AND year_end>=?"; p+=[y1,y0]
        for (vid,) in c.execute(q,p).fetchall():
            c.execute("INSERT INTO membership VALUES(?,?,?,?)",(vid,cid,scope,'curated'))
link([('Acura','RL',2009,2012),('Acura','TL',2009,2012),('Acura','MDX',2007,2012),('Acura','ZDX',2010,2012)],'J37-LOOP','engine-loop')
link([('Honda','Pilot',None,None),('Honda','Ridgeline',None,None),('Honda','Odyssey',None,None)],'J35-ADJ','engine-loop-partial')
con.commit()

print(f"raw:{len(rows)} unique:{len(uniq)} clusters:{c.execute('SELECT COUNT(*) FROM clusters').fetchone()[0]} skipped:{len(skipped)}")
print("\n-- RL 006510 --")
for r in c.execute("SELECT v.make,v.model,v.year_start,v.year_end FROM vehicles v JOIN membership m ON m.vehicle_id=v.id WHERE m.cluster_id='006510'"): print(" ",r)
print("-- FR-S/BRZ 007710 --")
for r in c.execute("SELECT v.make,v.model FROM vehicles v JOIN membership m ON m.vehicle_id=v.id WHERE m.cluster_id='007710'"): print(" ",r)
print("-- Escalade rows --")
for r in c.execute("SELECT m.cluster_id,v.make,v.model,v.alt_models FROM vehicles v JOIN membership m ON m.vehicle_id=v.id WHERE v.model LIKE 'Escalade%' LIMIT 4"): print(" ",r)
print("-- Tahoe cluster peers --")
for r in c.execute("""SELECT DISTINCT v.make,v.model FROM vehicles v JOIN membership m ON m.vehicle_id=v.id
  WHERE m.cluster_id IN (SELECT m2.cluster_id FROM vehicles v2 JOIN membership m2 ON m2.vehicle_id=v2.id
  WHERE v2.model='Tahoe' AND v2.year_start>=2007) LIMIT 8"""): print(" ",r)
print("-- J37-LOOP --")
for r in c.execute("SELECT DISTINCT v.make,v.model,v.year_start,v.year_end FROM vehicles v JOIN membership m ON m.vehicle_id=v.id WHERE m.cluster_id='J37-LOOP'"): print(" ",r)
print("-- year coverage --")
print(" ",c.execute("SELECT MIN(year_start),MAX(year_end) FROM vehicles").fetchone())
if skipped:
    print("-- sample skipped --")
    for s in skipped[:5]: print(" ",s[:100])
