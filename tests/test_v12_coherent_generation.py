from pathlib import Path
import importlib.util


MODULE_PATH = Path(__file__).resolve().parents[1] / "examples" / "mesen_smb_checkpoint_planner_v12.py"
spec = importlib.util.spec_from_file_location("planner_v12", MODULE_PATH)
v12 = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(v12)


def _write(path: Path, *, generation: int, worker: int, root: int, score, candidate: str):
    assert v12.v11._atomic_json(
        path,
        {
            "generation": generation,
            "worker": worker,
            "root_frame": root,
            "candidate": candidate,
            "schedule": [{"buttons": worker + 1, "frames": 8}],
            "score": list(score),
            "compute_ms": 100.0 + worker,
        },
    )


def test_partial_newer_generation_does_not_beat_complete_older_generation(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v12.reset_response_cache()

    for worker in range(4):
        _write(
            paths[worker],
            generation=10,
            worker=worker,
            root=100,
            score=(1, 0, 10 + worker, 110 + worker),
            candidate=f"g10-w{worker}",
        )
    plan = v12.best_coherent_fresh_plan(paths, 108, 16, -1)
    assert plan is not None
    assert plan["candidate"] == "g10-w3"
    assert plan["cohort_size"] == 4

    # Faster worker 0 advances alone. The controller must not treat that one
    # newer response as if it had compared the full generation-11 action set.
    _write(paths[0], generation=11, worker=0, root=104, score=(1, 0, 99, 203), candidate="g11-w0")
    plan = v12.best_coherent_fresh_plan(paths, 112, 16, -1)
    assert plan is not None
    assert plan["cohort_generation"] == 10
    assert plan["candidate"] == "g10-w3"


def test_cache_reconstructs_generation_after_fast_workers_overwrite(tmp_path):
    paths = [tmp_path / f"r{i}.json" for i in range(4)]
    v12.reset_response_cache()

    # First polling instant sees only workers 0/1 from generation 20.
    _write(paths[0], generation=20, worker=0, root=200, score=(1, 0, 20, 220), candidate="run")
    _write(paths[1], generation=20, worker=1, root=200, score=(1, 0, 30, 230), candidate="jump8")
    _write(paths[2], generation=19, worker=2, root=196, score=(1, 0, 40, 236), candidate="old2")
    _write(paths[3], generation=19, worker=3, root=196, score=(1, 0, 50, 246), candidate="old3")
    assert v12.best_coherent_fresh_plan(paths, 208, 16, 19) is None

    # Later the slow workers publish generation 20 while fast workers have
    # already overwritten their files with generation 21. Cached 20/0 and 20/1
    # must still be available to form the coherent generation-20 cohort.
    _write(paths[0], generation=21, worker=0, root=204, score=(1, 0, 60, 264), candidate="new0")
    _write(paths[1], generation=21, worker=1, root=204, score=(1, 0, 65, 269), candidate="new1")
    _write(paths[2], generation=20, worker=2, root=200, score=(1, 0, 80, 280), candidate="jump12")
    _write(paths[3], generation=20, worker=3, root=200, score=(1, 0, 25, 225), candidate="right")

    plan = v12.best_coherent_fresh_plan(paths, 212, 16, 19)
    assert plan is not None
    assert plan["cohort_generation"] == 20
    assert plan["candidate"] == "jump12"
    assert plan["age"] == 12
