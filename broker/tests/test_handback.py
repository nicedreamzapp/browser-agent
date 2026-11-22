"""Matt says he is done in a tab: his earlier input stops blocking the agent (2026-11-21)."""
import time
import proxy


def test_handback_clears_and_blocks_old_input_from_coming_back():
    h = proxy.Humans()
    tid = "T" * 32
    t0 = time.time() - 5
    assert h.note(tid, t0, "keydown")
    assert h.active(tid)
    assert h.handback(tid) == 1
    assert not h.active(tid)
    # a later peek re-reads the page's own record of the same old typing: still not his
    h.merge(tid, [[t0, "keydown"], [t0 + 1, "pointerdown"]])
    assert not h.active(tid)
    # new input after the handback protects the tab again
    assert h.note(tid, time.time() + 0.5, "keydown")
    assert h.active(tid)
