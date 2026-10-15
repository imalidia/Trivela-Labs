"""Tests for the match-analytics section of app.py (run: python -m pytest tests).
The section is pulled out of the real file (between its markers) and run without cv2/torch."""
import json, math, os
from collections import defaultdict, deque, Counter
import pytest

SRC = os.environ.get("TRIVELA_SRC", os.path.join(os.path.dirname(__file__), "..", "app.py"))

def _load():
    app = open(SRC, encoding="utf-8").read()
    a = app.index("# MATCH ANALYTICS"); b = app.index("# I/O: threaded decoding")
    text = app[app.rfind("\n", 0, a) + 1:app.rfind("\n# ----", 0, b)]
    ns = {"json": json, "math": math, "defaultdict": defaultdict, "deque": deque, "Counter": Counter, "PLAYER_H_M": 1.78}
    exec(compile(text, "analytics", "exec"), ns)
    return ns

A = _load()
FPS = 25.0
ID = lambda c: c

# ---------------------------------------------------------------- helpers
class FlatPM:
    """Identity mapper: pixels are already metres. Lets the event logic be tested on exact positions."""
    def __init__(self): self.frame = {}
    def to_pitch(self, f, u, y): return (u, y)

def box(cid, x, y, team):
    return [cid, x - 0.4, y - 1.8, x + 0.4, y, team]

def run_flat(frames, events=(), gains=(), team_of=None, keep=()):
    """frames: list of (f, persons[(cid,x,y,team)], carrier, ball(x,y) or None)"""
    pm = FlatPM()
    for f, *_ in frames: pm.frame[f] = (0, 0.0, (40.0, 0.0), 0)
    team_of = team_of or {}
    ma = A["MatchAnalytics"](pm, FPS, ID, list(events), list(gains), team_of)
    for f, ps, c, b in frames:
        ma.frame(f, {"p": [box(*p) for p in ps], "c": c, "b": None if b is None else [b[0], b[1], 0]})
    ma.finish()
    return ma, ma.result(list(keep))

# ---------------------------------------------------------------- xG
def test_xg_from_penalty_spot_is_about_a_third():
    assert 0.25 <= A["shot_xg"](94.0, 34.0) <= 0.35

def test_xg_falls_with_distance_and_angle():
    near, far, wide = A["shot_xg"](99, 34), A["shot_xg"](80, 34), A["shot_xg"](99, 10)
    assert near > far and near > wide and 0.01 <= far < 0.1

# ---------------------------------------------------------------- possession events
TEAM = {1: 0, 2: 0, 3: 0, 11: 1, 12: 1, 99: 2}

def test_pass_is_mapped_and_long_ball_counted():
    frames = [(0, [(1, 20, 30, 0), (2, 60, 30, 0)], 1, (20, 30)), (5, [(1, 20, 30, 0), (2, 60, 30, 0)], 2, (60, 30))]
    _, res = run_flat(frames, events=[[5, "pass", 2, 0, 1]], team_of=TEAM)
    assert res["maps"]["passes"] == [[20.0, 30.0, 60.0, 30.0, 0]]
    assert res["team"]["long_balls_a"] == 1 and res["team"]["long_balls_att_a"] == 1

def test_close_quick_regain_is_a_tackle_and_far_one_an_interception():
    tackle = [(0, [(1, 50, 30, 0), (11, 51, 30, 1)], 1, (50, 30)), (8, [(1, 50, 30, 0), (11, 51, 30, 1)], 11, (51, 30))]
    _, res = run_flat(tackle, events=[[8, "won", 11, 1, 1]], team_of=TEAM)
    assert (res["team"]["tackles_b"], res["team"]["dispossessed_a"], res["team"]["interceptions_b"]) == (1, 1, 0)
    inter = [(0, [(1, 50, 30, 0), (11, 70, 30, 1)], 1, (50, 30)), (40, [(1, 50, 30, 0), (11, 70, 30, 1)], 11, (70, 30))]
    _, res = run_flat(inter, events=[[40, "won", 11, 1, 1]], team_of=TEAM)
    assert (res["team"]["tackles_b"], res["team"]["interceptions_b"]) == (0, 1)
    assert res["team"]["def_actions_b"] == 1

def test_cross_from_the_wing_into_the_box():
    frames = [(0, [(1, 92, 4, 0), (2, 97, 33, 0)], 1, (92, 4)), (20, [(1, 92, 4, 0), (2, 97, 33, 0)], 2, (97, 33))]
    _, res = run_flat(frames, events=[[20, "pass", 2, 0, 1]], team_of=TEAM)
    assert res["team"]["crosses_a"] == 1 and res["team"]["crosses_ok_a"] == 1

def test_team_b_attacks_towards_x_zero():
    # B's wing cross: from near (13, 64) into B's attacking box at x < 16.5
    frames = [(0, [(11, 13, 64, 1), (12, 8, 34, 1)], 11, (13, 64)), (20, [(11, 13, 64, 1), (12, 8, 34, 1)], 12, (8, 34))]
    _, res = run_flat(frames, events=[[20, "pass", 12, 1, 11]], gains=[[20, 12, 1]], team_of=TEAM)
    assert res["team"]["crosses_b"] == 1 and res["team"]["box_touches_b"] == 1 and res["team"]["box_touches_a"] == 0

def test_box_touch_needs_the_player_in_the_opponents_box():
    frames = [(0, [(1, 95, 34, 0), (2, 10, 34, 0)], 1, (95, 34))]
    _, res = run_flat(frames, gains=[[0, 1, 0], [0, 2, 0]], team_of=TEAM, keep=[1])
    assert res["team"]["box_touches_a"] == 1 and res["players"][1]["box_touches"] == 1

# ---------------------------------------------------------------- dribbles
def test_carry_past_a_close_defender_is_a_dribble():
    frames = []
    for i in range(30):                     # player 1 runs 9 m, defender 11 stands at x = 53
        x = 50 + i * 0.3
        frames.append((i, [(1, x, 30, 0), (11, 53, 31, 1)], 1, (x, 30)))
    frames.append((40, [(1, 59, 30, 0), (2, 70, 30, 0), (11, 53, 31, 1)], 2, (70, 30)))
    _, res = run_flat(frames, team_of=TEAM, keep=[1])
    assert res["team"]["dribbles_a"] == 1 and res["players"][1]["dribbles"] == 1

def test_carry_lost_straight_to_the_defender_is_not_a_dribble():
    frames = [(i, [(1, 50 + i * 0.3, 30, 0), (11, 53, 31, 1)], 1, (50 + i * 0.3, 30)) for i in range(30)]
    frames.append((33, [(1, 59, 30, 0), (11, 59, 31, 1)], 11, (59, 31)))
    _, res = run_flat(frames, team_of=TEAM)
    assert res["team"]["dribbles_a"] == 0

# ---------------------------------------------------------------- kicks
def _kick_frames(start, end, carrier, persons, f0=0, still=0, speed_mps=25.0):
    """ball still at `start` for `still` frames (carried by `carrier`), then flies to `end` at speed_mps"""
    frames = [(f0 + i, persons, carrier if not still else -1, start) for i in range(max(1, still))]
    if not still: frames = [(f0 + i, persons, carrier, start) for i in range(5)]
    f = frames[-1][0]
    d = math.hypot(end[0] - start[0], end[1] - start[1]); n = max(2, int(d / speed_mps * FPS))
    for i in range(1, n + 1):
        frames.append((f + i, persons, -1, (start[0] + (end[0] - start[0]) * i / n, start[1] + (end[1] - start[1]) * i / n)))
    last = frames[-1][0]
    frames += [(last + i, persons, -1, end) for i in range(1, 40)]
    return frames

def test_shot_on_target_with_keeper_save_and_xg():
    persons = [(1, 88, 34, 0), (99, 103, 34, 2), (11, 80, 30, 1)]
    frames = _kick_frames((88, 34), (103.5, 34.5), 1, persons)
    _, res = run_flat(frames, team_of=TEAM, keep=[1])
    t = res["team"]
    assert (t["shots_a"], t["shots_on_a"], t["saves_b"]) == (1, 1, 1)
    assert 0.1 < t["xg_a"] < 0.4 and res["players"][1]["shots"] == 1
    assert t["fastest_strike_a"] > 60
    assert res["maps"]["shots"][0][:4] == [88.0, 34.0, 0, 1]

def test_shot_counts_when_the_ball_is_lost_right_after_the_strike():
    # broadcast footage often loses a struck ball: one sighting in flight is enough to see where it was going
    persons = [(1, 88, 34, 0), (11, 80, 30, 1)]
    frames = [(i, persons, 1, (88, 34)) for i in range(5)] + [(5, persons, -1, (88, 34))]
    frames += [(8, persons, -1, (95, 34.5))] + [(30 + i, persons, -1, None) for i in range(30)]
    _, res = run_flat(frames, team_of=TEAM, keep=[1])
    assert res["team"]["shots_a"] == 1 and res["team"]["shots_on_a"] == 1 and res["players"][1]["shots"] == 1

def test_shot_after_the_carrier_was_briefly_lost():
    # the shooter dropped out of tracking for over a second before shooting: the nearest player is credited, even with
    # the few metres of error the estimated pitch positions carry
    persons = [(1, 88, 29, 0), (11, 70, 30, 1)]
    frames = [(i, persons, 1, (84, 34)) for i in range(5)] + [(5 + i, persons, -1, (84 + 4 * i / 34, 34)) for i in range(35)]
    frames += _kick_frames((88, 34), (104, 36), -1, persons, f0=40)[5:]
    _, res = run_flat(frames, team_of=TEAM, keep=[1])
    assert res["team"]["shots_a"] == 1 and res["players"][1]["shots"] == 1

def test_save_is_credited_to_the_keeper_who_gets_a_side():
    persons = [(1, 88, 34, 0), (99, 103, 34, 2), (11, 80, 30, 1)]     # keeper 99 stands in front of the right goal (B's)
    frames = _kick_frames((88, 34), (103.5, 34.5), 1, persons)
    _, res = run_flat(frames, team_of=TEAM, keep=[1, 99])
    gk = res["players"][99]
    assert gk["saves"] == 1 and gk["gk_side"] == 1
    assert "gk_side" not in res["players"][1] and res["players"][1]["saves"] == 0

def test_keeper_side_follows_the_goal_he_stands_in_most():
    frames = [(i, [(98, 4 if i < 30 else 60, 34, 2)], -1, None) for i in range(40)]
    _, res = run_flat(frames, team_of={98: 2}, keep=[98])
    assert res["players"][98]["gk_side"] == 0

def test_corner_is_credited_to_its_taker():
    persons = [(1, 104, 1.5, 0), (2, 95, 34, 0), (11, 96, 30, 1)]
    _, res = run_flat(_kick_frames((104, 1), (95, 30), 1, persons, still=40), team_of=TEAM, keep=[1])
    assert res["players"][1]["corners"] == 1

def test_shot_wide_is_off_target():
    persons = [(1, 88, 20, 0), (11, 60, 30, 1)]
    frames = _kick_frames((88, 20), (105, 26), 1, persons)   # ~4 m wide of the post
    _, res = run_flat(frames, team_of=TEAM)
    assert (res["team"]["shots_a"], res["team"]["shots_on_a"]) == (1, 0)

def test_long_kick_from_own_third_is_a_clearance():
    persons = [(11, 95, 30, 1), (1, 80, 30, 0)]          # B defends the right goal
    frames = _kick_frames((95, 30), (60, 30), 11, persons)
    _, res = run_flat(frames, team_of=TEAM)
    assert res["team"]["clearances_b"] == 1 and res["team"]["shots_b"] == 0

def test_dead_ball_at_a_corner_counts_as_a_corner():
    persons = [(1, 104, 1.5, 0), (2, 95, 34, 0), (11, 96, 30, 1)]
    frames = _kick_frames((104, 1), (95, 30), 1, persons, still=40)
    _, res = run_flat(frames, team_of=TEAM)
    assert res["team"]["corners_a"] == 1 and res["team"]["free_kicks_a"] == 0

def test_dead_ball_in_open_play_counts_as_a_free_kick():
    persons = [(1, 70, 30, 0), (11, 80, 30, 1)]
    frames = _kick_frames((70, 30), (90, 40), 1, persons, still=40)
    _, res = run_flat(frames, team_of=TEAM)
    assert res["team"]["free_kicks_a"] == 1 and res["team"]["corners_a"] == 0

def test_final_third_time_follows_the_ball():
    frames = [(i, [(1, 50, 30, 0)], -1, (90, 30)) for i in range(50)] + [(50 + i, [(1, 50, 30, 0)], -1, (10, 30)) for i in range(25)]
    _, res = run_flat(frames, team_of=TEAM)
    assert res["team"]["final_third_s_a"] == 2.0 and res["team"]["final_third_s_b"] == 1.0

def test_heatmaps_split_on_and_off_the_ball():
    frames = [(i, [(1, 10, 10, 0), (2, 90, 60, 0)], 1, (10, 10)) for i in range(10)]
    _, res = run_flat(frames, team_of=TEAM, keep=[1, 2])
    heat, (nx, ny) = res["maps"]["heat"], res["maps"]["heat_size"]
    cell = lambda x, y: int(x / 105 * nx) + nx * int(y / 68 * ny)
    assert heat["a_on"][cell(10, 10)] == 99 and heat["a_on"][cell(90, 60)] == 0
    assert heat["a_off"][cell(90, 60)] == 99 and sum(heat["b_on"]) == 0
    assert len(res["players"][1]["heat"]) == 12 * 8 and max(res["players"][1]["heat"]) == 99

def test_player_heatmaps_split_on_and_off_the_ball():
    frames = [(i, [(1, 10 if i < 6 else 90, 10, 0)], 1 if i < 6 else -1, (10, 10)) for i in range(10)]
    _, res = run_flat(frames, team_of=TEAM, keep=[1])
    p, nx = res["players"][1], 12
    cell = lambda x, y: int(x / 105 * nx) + nx * int(y / 68 * 8)
    assert p["heat_on"][cell(10, 10)] == 99 and p["heat_on"][cell(90, 10)] == 0
    assert p["heat_off"][cell(90, 10)] == 99 and p["heat_off"][cell(10, 10)] == 0
    assert p["heat"][cell(10, 10)] == 99 and p["heat"][cell(90, 10)] > 0

# ---------------------------------------------------------------- pitch mapping on a synthetic broadcast camera
W, H, GTOP = 1280, 720, 90

def project(X, Y, x0, pan):
    """pitch metres -> (foot px x, foot px y, height px) for a camera that rotated `pan` pixels from centring x0"""
    t = (Y / 68.0) ** (1 / 0.8)
    fy = GTOP + (H - GTOP) * t
    h = 30 + 50 * t
    return W / 2 + (X - x0) / (1.78 / h) - pan, fy, h

def synth_log(path, a_defends_left=True, segments=((0, 120, 20, 1150), (130, 250, 85, -570))):
    """two wide segments, each a camera rotating from centring x0 by `pan` pixels; returns ground truth"""
    truth = {}
    lines = []
    base_a = [(15 + 7 * i, 8 + 6 * i) for i in range(8)]
    base_b = [(25 + 7 * i, 12 + 6 * i) for i in range(8)] + [(88, 20), (90, 34), (88, 48)]   # B's back line
    if not a_defends_left: base_a, base_b = base_b, base_a
    for seg, (f0, f1, x0, pan_end) in enumerate(segments):
        for f in range(f0, f1):
            pan = pan_end * (f - f0) / (f1 - f0)
            ps = []
            for team, base in ((0, base_a), (1, base_b)):
                for i, (X, Y) in enumerate(base):
                    X2 = X + 3 * math.sin(f / 30 + i)
                    u, fy, h = project(X2, Y, x0, pan)
                    if 0 < u < W:
                        cid = team * 100 + i; truth[(f, cid)] = (X2, Y)
                        ps.append([cid, round(u - h / 6), round(fy - h), round(u + h / 6), round(fy), team])
            for cid, X in ((200, 7.0), (201, 98.0)):
                u, fy, h = project(X, 34, x0, pan)
                if 0 < u < W:
                    truth[(f, cid)] = (X, 34)
                    ps.append([cid, round(u - h / 6), round(fy - h), round(u + h / 6), round(fy), 2])
            # cam.cum follows the image content, which moves opposite to the pan
            lines.append(f"{f}\t" + json.dumps({"p": ps, "c": -1, "b": None, "k": -pan, "g": GTOP, "s": seg}))
    with open(path, "w") as fo: fo.write("\n".join(lines) + "\n")
    return truth

def test_mapper_recovers_positions_within_a_few_metres(tmp_path):
    p = str(tmp_path / "log.jsonl"); truth = synth_log(p)
    pm = A["PitchMapper"](W, H); pm.build(p)
    assert pm.flip is False
    errs = []
    with open(p) as fin:
        for line in fin:
            f, _, body = line.partition("\t"); f = int(f)
            for q in json.loads(body)["p"]:
                x, y = pm.to_pitch(f, (q[1] + q[3]) / 2, q[4])
                X, Y = truth[(f, q[0])]
                errs.append(math.hypot(x - X, y - Y))
    errs.sort()
    assert errs[len(errs) // 2] < 3.0, errs[len(errs) // 2]
    assert errs[int(len(errs) * 0.9)] < 6.0, errs[int(len(errs) * 0.9)]

def test_mapper_rotates_when_team_a_defends_the_right_goal(tmp_path):
    p = str(tmp_path / "log.jsonl"); synth_log(p, a_defends_left=False)
    pm = A["PitchMapper"](W, H); pm.build(p)
    assert pm.flip is True

def test_end_to_end_runs_on_the_log(tmp_path):
    p = str(tmp_path / "log.jsonl"); synth_log(p)
    out = A["match_analytics"](p, ID, FPS, W, H, [], [], {i: 0 for i in range(8)}, [0, 1])
    assert set(out) == {"team", "players", "maps", "pitch"}
    assert out["pitch"]["anchored_pct"] >= 50
    assert sum(out["maps"]["heat"]["a_off"]) > 0 and 0 in out["players"]
    json.dumps(out)
