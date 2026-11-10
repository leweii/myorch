"""A sandbox's supervisor log is kept in the step's logs/ before the sandbox is deleted."""

from types import SimpleNamespace

from myorch.core.types import StepKey
from myorch.runner.openshell import redact
from myorch.runner.runner import StepRunner, sandbox_name


class FakeOpenShell:
    def __init__(self, log="", boom=False):
        self.log, self.boom, self.deleted = log, boom, []

    async def supervisor_log(self, name):
        if self.boom:
            raise RuntimeError("docker is down")
        return self.log

    async def delete(self, name):
        self.deleted.append(name)


class FakeStore:
    def __init__(self):
        self.files = {}

    def write_run_file(self, run_id, rel, text):
        self.files[(run_id, rel)] = text


KEY = StepKey(run_id="blog-development-1", step_id="implement", attempt=2, turn=1)


async def cleanup(openshell):
    store = FakeStore()
    runner = SimpleNamespace(openshell=openshell, store=store)
    runner._keep_supervisor_log = lambda key, name: StepRunner._keep_supervisor_log(runner, key, name)
    runner._remove = lambda key, name: StepRunner._remove(runner, key, name)
    await StepRunner.cleanup(runner, KEY)
    return store


async def test_the_log_is_saved_next_to_the_turn_logs_then_the_sandbox_goes():
    os_ = FakeOpenShell("OCSF NET:FAIL 198.18.0.2:443")
    store = await cleanup(os_)
    assert store.files == {("blog-development-1", ".myorch/steps/implement/logs/supervisor-2-1.log"): "OCSF NET:FAIL 198.18.0.2:443"}
    assert os_.deleted == [sandbox_name(KEY)]


async def test_no_log_or_an_error_never_stops_the_cleanup():
    for os_ in (FakeOpenShell(""), FakeOpenShell(boom=True)):
        store = await cleanup(os_)
        assert store.files == {} and os_.deleted == [sandbox_name(KEY)]


def test_token_shapes_are_masked_and_placeholders_kept():
    line = ("Authorization: Bearer ghp_" + "A" * 30 + " ?access_token=abc&x=1 openshell:resolve:env:v5_GITHUB_TOKEN "
            "xoxb-1234567890-abcdef sk-ant-oat01-" + "B" * 30)
    out = redact(line)
    for secret in ("A" * 30, "abc&", "xoxb-1234567890", "B" * 30):
        assert secret not in out
    assert "openshell:resolve:env:v5_GITHUB_TOKEN" in out and "x=1" in out
