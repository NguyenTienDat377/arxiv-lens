from pipeline.snapshots import latest_extracted


def test_latest_extracted_is_none_when_the_root_does_not_exist(tmp_path):
    assert latest_extracted(str(tmp_path / "missing")) is None


def test_latest_extracted_is_none_when_the_root_is_empty(tmp_path):
    assert latest_extracted(str(tmp_path)) is None


def test_latest_extracted_picks_the_newest_and_skips_staging_dirs(tmp_path):
    for name in ["2026-08-14T15-48-50Z", "2026-08-16T04-37-21Z", "tmpabc123"]:
        (tmp_path / name).mkdir()

    assert latest_extracted(str(tmp_path)) == "2026-08-16T04-37-21Z"
