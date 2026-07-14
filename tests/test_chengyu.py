from telefire.util.chengyu import load_chengyu_dict


def test_chengyu_loader_ignores_unsafe_pickle_cache(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "chengyu_dict.pkl").write_bytes(b"not a trusted pickle")
    (tmp_path / "chengyu_list.txt").write_text(
        "安居乐业\n业精于勤\n",
        encoding="utf-8",
    )

    assert load_chengyu_dict() == {
        "安": ["安居乐业"],
        "业": ["业精于勤"],
    }
