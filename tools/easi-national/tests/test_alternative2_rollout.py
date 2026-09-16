"""Local rollout gates: stored evidence, resume and completion provenance."""
import json
from pathlib import Path
import socket

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from builder import alternative2_rollout as rollout
from builder.stages import coverage


def bind():
    from easi.national import method_version
    return {"build_id":"test-build", "method_version":method_version(),
            "source_manifest_sha256":"a"*64, "expected_reaches":2}


def prepared(tmp_path):
    from easi.national import records
    root=rollout.RolloutRoot(tmp_path/'review/alternative-2-rollout',tmp_path).ensure()
    huc8='02080204'
    evidence=[]
    for comid in (1,2):
        evidence.append(records.to_row({"comid":comid,"huc4":"0208","huc8":huc8,"vpu":"02",
            "lat":37.8,"lon":-78.5,"totdasqkm":10.,"nars9":"SAP","l3_code":"45",
            "streamcat":{"pctimp2019ws":5.,"pctcrop2019ws":10.,"pcthay2019ws":0.},
            "geomorph":None,"erom":None}))
    rollout.common.write_parquet(pa.Table.from_pylist(evidence),root.huc8_file(huc8,'evidence'))
    rollout.common.write_parquet(pa.Table.from_pylist([{"comid":n,"huc4":"0208","huc8":huc8,"vpu":"02","tier":1} for n in (1,2)]),root.huc8_file(huc8,'source_metadata'))
    return root,huc8,bind()


def complete(tmp_path):
    staging=tmp_path/'staging'; staging.mkdir()
    (staging/'stats.json').write_text('{"reaches":2}')
    artifacts={"stats.json":coverage.sha256_of(staging/'stats.json')}
    binding={"alternative_id":"alternative-2","method_version":"method", "build_id":"build",
             "source_manifest_sha256":"source", "scoring_identity":{"alternative_id":"alternative-2"}}
    receipt={**binding,"status":"complete","validation":{"passed":True,"reaches":2},
             "artifacts":artifacts,"artifact_inventory_sha256":rollout.canonical_digest(artifacts)}
    rollout.write_json(staging/'completion.json',receipt)
    manifest={**binding,"build_status":"complete","reaches_scored":2,
              "assets":{name:coverage._asset(staging/name) for name in ('stats.json','completion.json')}}
    rollout.write_json(staging/'manifest.json',manifest)
    return staging,manifest,receipt


def test_real_shared_scorer_preserves_evidence_and_resumes(tmp_path):
    root,huc8,build=prepared(tmp_path)
    before=coverage.sha256_of(root.huc8_file(huc8,'evidence'))
    result=rollout.score_huc8(str(root.root),str(root.source),huc8,build)
    assert result=={'huc8':huc8,'rows':2,'resumed':False}
    validation=rollout.validate_scores(root,[huc8],build)
    assert validation['passed'] and validation['rating_anchor_pairs_checked']==40
    table=pq.read_table(root.huc8_file(huc8,'scores'))
    assert table['rating_catchment_hydrology'].to_pylist()==['Good','Good']
    assert table['fs_catchment_hydrology'].to_pylist()==[13,13]
    assert before==coverage.sha256_of(root.huc8_file(huc8,'evidence'))
    assert rollout.score_huc8(str(root.root),str(root.source),huc8,build)['resumed']


@pytest.mark.parametrize('field,value',[('method_version','old'),('build_id','old'),('source_manifest_sha256','old'),('alternative_id','alternative-1')])
def test_reject_mixed_score_identity(tmp_path,field,value):
    root,huc8,build=prepared(tmp_path)
    rollout.score_huc8(str(root.root),str(root.source),huc8,build)
    path=root.huc8_file(huc8,'scores'); rows=pq.read_table(path).to_pylist()
    rows[1][field]=value
    rollout.common.write_parquet(pa.Table.from_pylist(rows),path)
    with pytest.raises(rollout.RolloutError,match='Mixed/stale'):
        rollout.validate_scores(root,[huc8],build)
    with pytest.raises(rollout.RolloutError,match='Damaged/mismatched'):
        rollout.score_huc8(str(root.root),str(root.source),huc8,build)


def test_reject_score_comid_and_anchor_corruption(tmp_path):
    root,huc8,build=prepared(tmp_path)
    rollout.score_huc8(str(root.root),str(root.source),huc8,build)
    path=root.huc8_file(huc8,'scores'); rows=pq.read_table(path).to_pylist()
    rows[1]['comid']=1
    rollout.common.write_parquet(pa.Table.from_pylist(rows),path)
    with pytest.raises(rollout.RolloutError,match='COMID mismatch'):
        rollout.validate_scores(root,[huc8],build)
    rows[1]['comid']=2;rows[1]['fs_catchment_hydrology']=14
    rollout.common.write_parquet(pa.Table.from_pylist(rows),path)
    with pytest.raises(rollout.RolloutError,match='Invalid rating'):
        rollout.validate_scores(root,[huc8],build)


def test_network_is_blocked_and_restored():
    original=socket.socket.connect
    with rollout.offline(), socket.socket() as connection:
        with pytest.raises(rollout.RolloutError,match='Network access'):
            connection.connect(('127.0.0.1',9))
    assert socket.socket.connect is original


def test_complete_gate_and_damaged_output(tmp_path):
    staging,_,receipt=complete(tmp_path)
    assert rollout.verify_completed(staging,'method')==receipt
    (staging/'stats.json').write_text('{"reaches":3}')
    with pytest.raises(rollout.RolloutError,match='Damaged output'):
        rollout.verify_completed(staging,'method')


@pytest.mark.parametrize('key,value',[('build_status','pending'),('method_version','old'),('alternative_id','alternative-1')])
def test_incomplete_or_wrong_manifest_rejected(tmp_path,key,value):
    staging,manifest,_=complete(tmp_path)
    manifest[key]=value;rollout.write_json(staging/'manifest.json',manifest)
    with pytest.raises(rollout.RolloutError,match='incomplete'):
        rollout.verify_completed(staging,'method')


def test_rehashed_but_mismatched_completion_rejected(tmp_path):
    staging,manifest,receipt=complete(tmp_path)
    receipt['build_id']='other';rollout.write_json(staging/'completion.json',receipt)
    manifest['assets']['completion.json']=coverage._asset(staging/'completion.json')
    rollout.write_json(staging/'manifest.json',manifest)
    with pytest.raises(rollout.RolloutError,match='provenance mismatch'):
        rollout.verify_completed(staging,'method')


@pytest.mark.parametrize('name',['../escape','C:/escape','..\\escape','/escape',''])
def test_unsafe_asset_rejected(tmp_path,name):
    with pytest.raises(rollout.RolloutError):rollout.asset_path(tmp_path,name)


def test_source_stamp_and_hash_guard(tmp_path):
    path=tmp_path/'source';path.write_text('before')
    row=rollout.fingerprint(path);rollout.unchanged([row])
    path.write_text('after!')
    with pytest.raises(rollout.RolloutError,match='Captured input changed'):
        rollout.unchanged([row])


def test_source_read_paths_and_output_paths_are_separate(tmp_path):
    source=tmp_path/'source'
    root=rollout.RolloutRoot(tmp_path/'output',source).ensure()
    assert root.national==source/'national' and root.chunks==source/'chunks'
    assert not source.exists()
    assert root.huc8_file('02080204','scores').is_relative_to(tmp_path/'output')


def test_run_lock_prevents_duplicate_work(tmp_path):
    with rollout.exclusive_run(tmp_path):
        with pytest.raises(FileExistsError):
            with rollout.exclusive_run(tmp_path):pass
    assert not (tmp_path/'run.lock').exists()


def test_preparation_uses_exact_staged_rows_and_rejects_resume_damage(tmp_path):
    root=rollout.RolloutRoot(tmp_path/'review/alternative-2-rollout',tmp_path).ensure()
    source=tmp_path/'staging';source.mkdir()
    raw=[{'comid':1,'huc4':'0208','huc8':'02080204','streamcat':'{"raw":12}'},
         {'comid':2,'huc4':'0208','huc8':'02080205','streamcat':None}]
    pq.write_table(pa.Table.from_pylist(raw),source/'evidence_0208.parquet')
    metadata=[{**{key:None for key in rollout.META},'comid':row['comid'],'huc4':'0208','huc8':row['huc8'],
               'method_version':rollout.BASE_METHOD} for row in raw]
    pq.write_table(pa.Table.from_pylist(metadata),source/'scores_02.parquet')
    rollout.write_json(source/'manifest.json',{'scores':{'02':{'asset':'scores_02.parquet'}},
        'units':{'0208':{'vpu':'02','n_scored':2,'evidence':{'asset':'evidence_0208.parquet'}}}})
    build=bind();huc8s=rollout.prepare(root,build)
    assert huc8s==['02080204','02080205']
    assert [pq.read_table(root.huc8_file(h,'evidence')).to_pylist()[0] for h in huc8s]==raw
    assert rollout.prepare(root,build)==huc8s
    root.huc8_file(huc8s[0],'evidence').write_bytes(b'damaged')
    with pytest.raises(rollout.RolloutError,match='Captured input changed'):
        rollout.prepare(root,build)


def test_missing_completion_rejected(tmp_path):
    staging,manifest,_=complete(tmp_path)
    del manifest['assets']['completion.json']
    rollout.write_json(staging/'manifest.json',manifest)
    with pytest.raises(rollout.RolloutError,match='Completion asset missing'):
        rollout.verify_completed(staging,'method')


def test_incorrect_inventory_and_failed_validation_rejected(tmp_path):
    staging,manifest,receipt=complete(tmp_path)
    for patch in ({'artifact_inventory_sha256':'wrong'},{'validation':{'passed':False,'reaches':2}}):
        rollout.write_json(staging/'completion.json',{**receipt,**patch})
        manifest['assets']['completion.json']=coverage._asset(staging/'completion.json')
        rollout.write_json(staging/'manifest.json',manifest)
        with pytest.raises(rollout.RolloutError,match='validation/artifact binding'):
            rollout.verify_completed(staging,'method')


def test_ordinary_staging_rejects_missing_mixed_and_stale_methods(tmp_path):
    root=rollout.RolloutRoot(tmp_path/'output',tmp_path/'source').ensure()
    path=root.huc8_file('02080204','scores')
    for methods in (None,['current','old'],['old','old'],['current',None]):
        data={'comid':[1,2]}
        if methods is not None:data['method_version']=methods
        rollout.common.write_parquet(pa.table(data),path)
        with pytest.raises(RuntimeError,match='score method'):
            coverage.validate_score_methods(root,['02080204'],'current')
    rollout.common.write_parquet(pa.table({'comid':[1,2],'method_version':['current','current']}),path)
    coverage.validate_score_methods(root,['02080204'],'current')


def test_tile_inputs_global_union_and_values(tmp_path,monkeypatch):
    import pandas as pd
    import pyogrio
    root=rollout.RolloutRoot(tmp_path/'output',tmp_path/'source').ensure()
    columns={'comid':[1,2],'eci':[.31,.62],'phys':[.51,.42],'chem':[.61,.32],
             'bio':[.41,.22],'band':['Non-Functioning','At-Risk']}
    rollout.common.write_parquet(pa.table(columns),root.huc8_file('02080204','scores'))
    (root.tiles/'02').mkdir(); (root.tiles/'04').mkdir()
    frames={'02':pd.DataFrame(columns).iloc[[0]],'04':pd.DataFrame(columns).iloc[[1]]}
    monkeypatch.setattr(pyogrio,'read_dataframe',lambda path,**kw:frames[path.parent.name].copy())
    assert rollout.validate_tile_inputs(root,['02080204'],{'expected_reaches':2})['scored_comids']==2
    frames['04'].loc[1,'eci']=.63
    with pytest.raises(rollout.RolloutError,match='eci differs'):
        rollout.validate_tile_inputs(root,['02080204'],{'expected_reaches':2})
    frames['04']=frames['02']
    with pytest.raises(rollout.RolloutError,match='COMIDs duplicated'):
        rollout.validate_tile_inputs(root,['02080204'],{'expected_reaches':2})


def test_partial_completion_resumes_until_final_manifest(tmp_path,monkeypatch):
    source=tmp_path/'source';destination=source/'review/alternative-2-rollout'
    root=rollout.RolloutRoot(destination,source).ensure()
    (root.staging/'completion.json').write_text('{"status":"complete"}')
    (root.staging/'manifest.json').write_text('{"method_version":"method"}')
    monkeypatch.setattr(rollout,'capture_inputs',lambda *a:{})
    monkeypatch.setattr(rollout.tiles,'docker_ready',lambda:(False,'stop at resumed preparation'))
    with pytest.raises(rollout.RolloutError,match='stop at resumed preparation'):
        rollout.run(source,destination,'method')
