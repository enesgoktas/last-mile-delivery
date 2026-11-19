from app.workers import optimization_tasks


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, _script, _num_keys, key, owner):
        if self.values.get(key) == owner:
            del self.values[key]
            return 1
        return 0


def test_try_acquire_reoptimization_guard_respects_cooldown():
    fake = _FakeRedis()
    fake.values["optimization:reopt:c-1:cooldown"] = "1"
    allowed, reason, _lock_key = optimization_tasks._try_acquire_reoptimization_guard(
        fake,
        courier_id="c-1",
        lock_owner="owner-1",
    )
    assert allowed is False
    assert reason == "COOLDOWN_ACTIVE"


def test_try_acquire_reoptimization_guard_respects_lock():
    fake = _FakeRedis()
    fake.values["optimization:reopt:c-1:lock"] = "another-owner"
    allowed, reason, _lock_key = optimization_tasks._try_acquire_reoptimization_guard(
        fake,
        courier_id="c-1",
        lock_owner="owner-1",
    )
    assert allowed is False
    assert reason == "LOCKED"


def test_try_acquire_reoptimization_guard_acquires_when_available():
    fake = _FakeRedis()
    allowed, reason, lock_key = optimization_tasks._try_acquire_reoptimization_guard(
        fake,
        courier_id="c-1",
        lock_owner="owner-1",
    )
    assert allowed is True
    assert reason is None
    assert fake.values[lock_key] == "owner-1"
