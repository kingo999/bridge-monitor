from bridge.outbound_audit import Policy, Observation

def test_missing_evidence_and_deny_precedence():
    policy = Policy(default="deny", rules=[
        {"id":"vendor","effect":"allow","hostname":"Example.NET"},
        {"id":"restricted","effect":"deny","agent":"restricted"}])
    assert policy.evaluate(Observation(kind="transfer", ip="192.0.2.1", agent="ok"))[0] == "insufficient evidence"
    assert policy.evaluate(Observation(kind="attempt", hostname="example.net", agent="ok")) == ("would allow","vendor")
    assert policy.evaluate(Observation(kind="attempt", hostname="example.net", agent="restricted")) == ("would deny","restricted")
