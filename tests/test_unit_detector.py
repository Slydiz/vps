import numpy as np
from clipper import detector as d


def test_robust_z_flat_signal_does_not_explode():
    z = d.robust_z(np.full(100, -40.0))
    assert np.all(np.isfinite(z)) and np.abs(z).max() < 1e-6


def test_robust_z_spike_is_large():
    x = np.full(200, -40.0) + np.random.default_rng(0).normal(0, 0.5, 200)
    x[100] = -10
    assert d.robust_z(x)[100] > 10


def test_loudness_per_second_length_and_order():
    sr = d.SR
    quiet = np.full(sr * 3, 0.01, dtype=np.float32)
    loud = np.full(sr * 2, 0.5, dtype=np.float32)
    db = d.loudness_per_second(np.concatenate([quiet, loud]))
    assert len(db) == 5 and db[4] > db[0] + 20


def test_pick_peaks_respects_min_gap_and_threshold():
    s = np.zeros(600); s[100] = 9; s[110] = 8; s[300] = 7; s[500] = 2
    z = np.zeros(600)
    peaks = d.pick_peaks(s, z, z, min_score=3, min_gap=90, max_peaks=5)
    assert [int(p.peak_s) for p in peaks] == [100, 300]      # 110 too close, 500 below threshold


def test_pick_peaks_max_peaks():
    s = np.zeros(1000); s[[100, 300, 500, 700]] = [5, 6, 7, 8]
    z = np.zeros(1000)
    peaks = d.pick_peaks(s, z, z, min_score=3, min_gap=90, max_peaks=2)
    assert sorted(int(p.peak_s) for p in peaks) == [500, 700]


def test_clip_window_pre_roll_and_clamping():
    assert d.clip_window(120, 600, 40, 20) == (80, 140)
    assert d.clip_window(10, 600, 40, 20) == (0, 30)       # cannot start before 0
    assert d.clip_window(595, 600, 40, 20) == (555, 600)   # cannot run past the end


def test_chat_per_second_counts_hype_tokens():
    msgs = [{"t": 1000.2, "text": "gg"}, {"t": 1000.5, "text": "KEKW"}, {"t": 1002.0, "text": "😂 LUL"}, {"t": 5000, "text": "x"}]
    arr = d.chat_per_second(msgs, 1000, 5)
    assert arr[0] == 1 + 1.5 and arr[1] == 0 and arr[2] == 1 + 1.0   # 2nd msg: 1 + 0.5; 3rd: 1 + 2*0.5


def test_score_segment_audio_only_when_no_chat():
    db = np.full(100, -40.0); db[50] = -10
    score, az, cz = d.score_segment(db, None)
    assert score.argmax() in (49, 50, 51) and not cz.any()


def test_score_segment_chat_boosts_same_moment():
    db = np.full(100, -40.0) + np.random.default_rng(1).normal(0, .3, 100); db[50] = -25
    chat = np.zeros(100); chat[48:54] = 15
    s_audio, _, _ = d.score_segment(db, None)
    s_both, _, _ = d.score_segment(db, chat)
    assert s_both[50] > 3 and s_both.argmax() in range(48, 54)
