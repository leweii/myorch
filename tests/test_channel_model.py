"""channels.yaml identities: the person behind an account, and a person's other accounts."""

from pathlib import Path

from myorch.channels.model import ChannelsConfig, load_config


def test_identities():
    cfg = ChannelsConfig(identities={"jakobhe": {"github": "leweii", "slack": "U01"}})
    assert cfg.person("github", "LEWEII") == "jakobhe" and cfg.person("github", "other") == "other"
    assert cfg.aliases("jakobhe") == ["U01", "leweii"]


def test_missing_file_means_no_identities(tmp_path: Path):
    assert load_config(tmp_path / "none.yaml").identities == {}
    (tmp_path / "c.yaml").write_text("identities:\n  a: {web: alice}\n")
    assert load_config(tmp_path / "c.yaml").person("web", "alice") == "a"
