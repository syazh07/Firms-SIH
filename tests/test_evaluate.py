import pandas as pd

from frims.classify.evaluate import coarse_from_label, coarse_from_sp, evaluate, score


def test_coarse_mappings():
    assert coarse_from_label(pd.Series(["gas_flare", "wildfire", "offshore_other", "industrial_fire"])).tolist() == \
        ["industrial", "natural", "other", "natural"]
    assert coarse_from_sp(pd.Series([2, 0, 3, 1, None])).tolist() == ["industrial", "natural", "other", "other", "other"]


def test_score_and_evaluate_window():
    df = pd.DataFrame({
        "acq_date": pd.to_datetime(["2025-11-01", "2025-11-02", "2025-12-01", "2026-02-01"]),
        "sp_type": [2, 0, 2, 2],
        "label_rule": ["industrial_persistent", "wildfire", "agricultural_burn", "gas_flare"],
    })
    res = evaluate(df, "2025-11-01", "2026-01-31")
    assert res["n"] == 3
    ind = res["per_class"]["industrial"]
    assert ind["support"] == 2 and ind["recall"] == 0.5 and ind["precision"] == 1.0
    assert res["confusion"]["matrix"][0] == [1, 1, 0]
    perfect = score(pd.Series(["industrial", "natural"]), pd.Series(["industrial", "natural"]))
    assert perfect["accuracy"] == 1.0
