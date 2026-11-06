"""The reference plate: automatic supports and rafts on dental-like parts that once went wrong, with every
support shape and the raft types that differ most. Nothing may stick out of a part or run through it,
(almost) every support point keeps its support, and a raft is there when one is asked for."""
import pytest

from unislicer import profiles, reference


@pytest.mark.parametrize("shape,raft", reference.CONFIGS)
def test_reference_plate(shape, raft):
    base = profiles.build_config("phrozen_sonic_ls_plus", None, None, {"support_enable": True})
    faults = []
    for name, mesh, model_type in reference.synthetic_plate():
        cfg = reference.config_for(base, shape, raft, model_type)
        r = reference.check_part(mesh, cfg)
        if r["sticking_out"] or r["through"]:
            faults.append(f"{name}: {r['sticking_out']} sticking out, {r['through']} through the part")
        if r["used"] < 0.9 * r["points"]:
            faults.append(f"{name}: only {r['used']} of {r['points']} points supported")
        if raft != "none" and r["used"] and r["raft_area"] <= 0:
            faults.append(f"{name}: no raft")
    assert not faults, faults
