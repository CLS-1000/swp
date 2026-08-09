-- curated.sql — hand-verified engine-family clusters
-- Applied AFTER parse. Parser never touches these rows.
-- To add a new engine family: append INSERT statements below and re-run `spw build`.

-- Honda J37 3.7L V6
INSERT OR REPLACE INTO clusters VALUES('J37-LOOP','engine_family','Honda J37 3.7L V6 engine loop','curated');
INSERT OR REPLACE INTO clusters VALUES('J35-ADJ','engine_family','Honda J35 3.5L V6 adjacent (accessories only)','curated');

-- BMW N52 3.0L NA I6
INSERT OR REPLACE INTO clusters VALUES('N52-LOOP','engine_family','BMW N52 3.0L NA I6 engine loop, 2006-2012 US — NA 6-cyl badges only','curated');

-- J37-LOOP memberships
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'J37-LOOP','engine-loop','curated','exact J37 match'
  FROM vehicles WHERE make='Acura' AND model='RL' AND year_start<=2012 AND year_end>=2009;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'J37-LOOP','engine-loop','curated','exact J37A4 match'
  FROM vehicles WHERE make='Acura' AND model='TL' AND year_start<=2012 AND year_end>=2009;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'J37-LOOP','engine-loop','curated','shares 3.7L block'
  FROM vehicles WHERE make='Acura' AND model='MDX' AND year_start<=2012 AND year_end>=2007;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'J37-LOOP','engine-loop','curated','shares 3.7L block'
  FROM vehicles WHERE make='Acura' AND model='ZDX' AND year_start<=2012 AND year_end>=2010;

-- J35-ADJ memberships
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'J35-ADJ','engine-loop-partial','curated','3.5L variant — structural accessories only'
  FROM vehicles WHERE make='Honda' AND model='Pilot';
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'J35-ADJ','engine-loop-partial','curated','3.5L variant — structural accessories only'
  FROM vehicles WHERE make='Honda' AND model='Ridgeline';
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'J35-ADJ','engine-loop-partial','curated','3.5L variant — structural accessories only'
  FROM vehicles WHERE make='Honda' AND model='Odyssey';

-- N52-LOOP memberships (badge qualifiers critical — turbo/V8 models excluded)
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','525i/528i/530i badges only — V8/545 excluded'
  FROM vehicles WHERE make='BMW' AND model LIKE '525,530,545%' AND year_start<=2010 AND year_end>=2004;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','525i/528i/530i badges only — V8 excluded'
  FROM vehicles WHERE make='BMW' AND model LIKE '525,530,550%' AND year_start<=2010 AND year_end>=2004;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','525i/528i/530i badges only — V8/550 excluded'
  FROM vehicles WHERE make='BMW' AND model LIKE '528,535,550%' AND year_start<=2010 AND year_end>=2004;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','E90 325i/330i'
  FROM vehicles WHERE make='BMW' AND model LIKE '325,330%' AND year_start<=2012 AND year_end>=2006;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','328i only — 335i turbo (N54/N55) excluded'
  FROM vehicles WHERE make='BMW' AND model LIKE '328,335%' AND year_start<=2012 AND year_end>=2006;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','N52 NA badges only'
  FROM vehicles WHERE make='BMW' AND model='3-Series' AND year_start<=2012 AND year_end>=2006;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','128i only — 135i turbo excluded'
  FROM vehicles WHERE make='BMW' AND (model LIKE '1-Series%' OR model LIKE '128,135%') AND year_start<=2012 AND year_end>=2008;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','X3 3.0si 2007-2010 only — pre-2007 M54'
  FROM vehicles WHERE make='BMW' AND model='X3' AND year_start<=2010 AND year_end>=2004;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','X3 xDrive28i 2011-2012 (N52)'
  FROM vehicles WHERE make='BMW' AND model='X3' AND year_start>=2011;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','X5 3.0si/xDrive30i 2007-2010 only — later N55'
  FROM vehicles WHERE make='BMW' AND model='X5' AND year_start<=2012 AND year_end>=2007;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','Z4 sDrive30i (E89)'
  FROM vehicles WHERE make='BMW' AND model='Z4' AND year_start>=2009;
INSERT OR IGNORE INTO membership(vehicle_id,cluster_id,scope,confidence,note)
  SELECT id,'N52-LOOP','engine-loop','curated','Z4 3.0si 2006-2008 only — earlier M54'
  FROM vehicles WHERE make='BMW' AND model='Z4' AND year_end<=2008 AND year_start>=2003;
