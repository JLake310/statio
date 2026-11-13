#!/usr/bin/env python3
"""빈자리 예측 모델을 기록 전체로 다시 학습해 앱에 넣을 파일과 Kotlin 대조용 정답 사례를 만듭니다.

    .venv/bin/python ml/export_model.py

만드는 것
  - android/app/src/main/assets/predictor.json        결정트리 앙상블 두 개(빈자리 변화, 빈자리 있을 확률), 만차 위험 기준값,
                                                       주차장·공휴일 정보
  - android/app/src/test/resources/predictor_golden.json
        실제 기록에서 고른 상황마다 차 안 입력(device_features)과 scikit-learn 예측값. SpacePredictorTest가 Kotlin 결과와 대조해요

차 안 입력은 학습 입력(dataset.py)과 같은 규칙이에요. 패턴은 앱 DB(assets/statio.db)의 패턴표 한 칸에서,
최근 15·30분 변화는 앱이 1분마다 쌓는 실시간 기록에서 구해요.
"""

import argparse
import datetime
import json
import math
import pathlib
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dataset as ds  # noqa: E402
import train_eval as te  # noqa: E402

ANDROID = ds.ROOT / "android" / "app" / "src"
ASSET_MODEL = ANDROID / "main" / "assets" / "predictor.json"
ASSET_DB = ANDROID / "main" / "assets" / "statio.db"
GOLDEN = ANDROID / "test" / "resources" / "predictor_golden.json"
H_MIN, H_MAX = 5.0, 60.0           # 학습한 도착까지 남은 시간 범위. 벗어나면 끝값으로 맞춰요
TOL_SEC = int(ds.MATCH_TOL.total_seconds())


# ── 모델 내보내기 ────────────────────────────────────────

def export_ensemble(model):
    """scikit-learn HistGradientBoosting 모델을 노드 배열로 바꿔요. 잎 노드는 feature=-1이고 x에 잎 값이 들어가요."""
    known_bitsets, f_idx_map = model._bin_mapper.make_known_categories_bitsets()
    cat_features = [int(f) for f in np.flatnonzero(model._bin_mapper.is_categorical_)]
    known = {str(f): bits_to_list(known_bitsets[f_idx_map[f]]) for f in cat_features}
    trees = []
    for (predictor,) in model._predictors:
        nodes = predictor.nodes
        tree = {"f": [], "x": [], "l": [], "r": [], "m": [], "cat_nodes": [], "cat_left": []}
        for i, n in enumerate(nodes):
            leaf = bool(n["is_leaf"])
            tree["f"].append(-1 if leaf else int(n["feature_idx"]))
            tree["x"].append(float(n["value"]) if leaf else float(n["num_threshold"]))
            tree["l"].append(int(n["left"]))
            tree["r"].append(int(n["right"]))
            tree["m"].append(1 if n["missing_go_to_left"] else 0)
            if not leaf and n["is_categorical"]:
                tree["cat_nodes"].append(i)
                tree["cat_left"].append(bits_to_list(predictor.raw_left_cat_bitsets[n["bitset_idx"]]))
        trees.append(tree)
    return {"baseline": float(model._baseline_prediction.ravel()[0]), "known_categories": known, "trees": trees}


def bits_to_list(bitset):
    """8칸짜리 32비트 비트셋(0~255)을 켜진 값 목록으로 바꿔요."""
    return [i for i in range(256) if (int(bitset[i // 32]) >> (i % 32)) & 1]


# ── 차 안 입력 (Kotlin SpacePredictor.features와 같은 계산) ──────────────

def device_features(meta, table, lot, now, eta_min, total, parked, updated_at, recent):
    """앱이 가진 값만으로 모델 입력을 만들어요. 모델이 모르는 주차장이면 None이에요."""
    info = meta["lots"].get(lot)
    if info is None or total <= 0:
        return None
    h = min(max(eta_min, H_MIN), H_MAX)
    target = now + datetime.timedelta(seconds=int(h * 60))
    occ = min(max(parked, 0), total) / total

    def past(minutes):
        ref = updated_at - datetime.timedelta(minutes=minutes)
        best = None
        for at, free in recent:
            d = abs((at - ref).total_seconds())
            if d <= TOL_SEC and (best is None or d < best[0]):
                best = (d, free)
        return math.nan if best is None else min(max(1 - best[1] / total, 0.0), 1.0)

    def cell(t):
        return table.get((lot, 1 if t.weekday() >= 5 else 0, round((t.hour * 60 + t.minute) / 15) % 96))

    holidays = {datetime.date.fromisoformat(d) for d in meta["holidays"]}
    ct, cn = cell(target), cell(now)
    pat_target = 1 - ct[0] / total if ct else math.nan
    pat_now = 1 - cn[0] / total if cn else math.nan
    onstreet = info["onstreet"]
    values = {
        "lot_idx": info["idx"], "total": total, "onstreet": math.nan if onstreet is None else float(onstreet),
        "dow": target.weekday(), "weekend": float(target.weekday() >= 5), "holiday": float(target.date() in holidays),
        "tod": now.hour * 60 + now.minute, "tod_target": target.hour * 60 + target.minute, "h": h,
        "occ": occ, "d15": occ - past(15), "d30": occ - past(30),
        "pat_now": pat_now, "pat_target": pat_target, "pat_delta": pat_target - pat_now, "pat_days": ct[1] if ct else 0,
    }
    return [float(values[name]) for name in meta["features"]]


def load_table():
    with sqlite3.connect(ASSET_DB) as conn:
        return {(lot, d, s): (free, days) for lot, d, s, free, days in conn.execute("SELECT lot_id, day_type, slot, free, days FROM patterns")}


# ── 정답 사례 ────────────────────────────────────────────

def golden_cases(raw, kept_lots, excluded, meta, table, reg, clf, n=240, seed=7):
    """실제 기록에서 상황을 골라 차 안 입력과 예측값을 계산해요. 공휴일·짧은/긴 도착 시간·최근 기록 없음도 섞어요."""
    rng = np.random.default_rng(seed)
    by_lot = {lot: g.sort_values("t") for lot, g in raw.groupby("lot")}
    kept = raw[raw.lot.isin(kept_lots)]
    almost_full = kept[kept.total - kept.parked <= 2]                   # 만차 위험 경고가 나오는 상황도 충분히 넣어요
    picks = pd.concat([kept.sample(n, random_state=seed), almost_full.sample(min(40, len(almost_full)), random_state=seed)])
    extra = raw[raw.lot.isin(list(excluded))].sample(min(10, len(excluded)), random_state=seed)
    etas = [2.5, 5.0, 7.3, 10.0, 15.0, 20.0, 30.0, 44.5, 60.0, 75.0]
    cases = []
    for k, row in enumerate(pd.concat([picks, extra]).itertuples()):
        now = row.t.to_pydatetime().replace(microsecond=0)
        hist = by_lot[row.lot]
        window = hist[(hist.t >= row.t - pd.Timedelta(minutes=40)) & (hist.t <= row.t)]
        recent = [(t.to_pydatetime().replace(microsecond=0), int(max(0, tot - p))) for t, tot, p in zip(window.t, window.total, window.parked)]
        roll = rng.random()
        if roll < 0.12:
            recent = []                                   # 앱을 막 켜서 지난 기록이 없을 때
        elif roll < 0.22:
            recent = [r for r in recent if (now - r[0]).total_seconds() < 20 * 60]  # 30분 전 기록만 없을 때
        shift = datetime.timedelta(0)
        if k % 40 == 0:                                   # 공휴일(성탄절) 같은 시각으로 옮겨 봐요
            shift = datetime.datetime.combine(datetime.date(2026, 12, 25), now.time()) - now
        now, recent = now + shift, [(t + shift, f) for t, f in recent]
        eta = float(etas[k % len(etas)])
        total, parked = int(row.total), int(row.parked)
        x = device_features(meta, table, row.lot, now, eta, total, parked, now, recent)
        case = {"lot": row.lot, "now": now.isoformat(), "eta_min": eta, "total": total, "parked": parked, "updated_at": now.isoformat(),
                "recent": [{"at": t.isoformat(), "free": f} for t, f in recent], "features": None, "delta": None, "free": None, "prob_free": None,
                "full_risk": None, "full_warning": None}
        if x is not None:
            frame = pd.DataFrame([x], columns=meta["features"])
            delta = float(reg.predict(frame)[0])
            occ = min(max(parked, 0), total) / total
            prob = float(clf.predict_proba(frame)[0, 1]) if clf is not None else None
            case.update(features=[None if math.isnan(v) else v for v in x], delta=delta,
                        free=int(np.rint(total * (1 - min(max(occ + delta, 0.0), 1.0)))), prob_free=prob)
            th = (meta.get("full_risk") or {}).get("threshold")
            if prob is not None and th is not None:
                risk = float(te.full_risk(np.array([prob]), np.array([max(0, total - parked)]))[0])
                case.update(full_risk=risk, full_warning=bool(risk > th))
        cases.append(case)
    return cases


def main():
    parser = argparse.ArgumentParser(description="빈자리 예측 모델 내보내기")
    parser.add_argument("--db", type=pathlib.Path, default=ds.LIVE_DB)
    parser.add_argument("--max-train", type=int, default=600_000)
    args = parser.parse_args()

    samples, excluded, raw = ds.load(args.db)
    te.add_cyclic(samples)
    train = samples.sample(args.max_train, random_state=0) if len(samples) > args.max_train else samples
    x = train[te.GBDT_FEATURES].astype(float)
    reg = te.gbdt().fit(x, train.target_parked / train.target_total - train.occ)
    has_free = (train.target_free >= 1).astype(int)
    clf = te.classifier().fit(x, has_free) if has_free.nunique() == 2 else None
    threshold, threshold_val = te.full_threshold(train, te.GBDT_FEATURES) if clf is not None else (None, None)

    lots = sorted(samples.lot.unique())
    onstreet = ds.lot_meta(lots)
    meta = {
        "format": 1, "made": f"{datetime.datetime.now():%Y-%m-%d %H:%M}",
        "data": {"from": f"{raw.t.min():%Y-%m-%d %H:%M}", "to": f"{raw.t.max():%Y-%m-%d %H:%M}", "samples": len(train)},
        "features": te.GBDT_FEATURES, "horizon_min": [H_MIN, H_MAX], "match_tolerance_sec": TOL_SEC,
        "lots": {lot: {"idx": i, "onstreet": onstreet.get(lot)} for i, lot in enumerate(lots)},
        "holidays": sorted(d.isoformat() for d in ds.HOLIDAYS),
        "regressor": export_ensemble(reg), "classifier": export_ensemble(clf) if clf is not None else None,
        # 만차 위험 = max(지금 만차면 1, 1 - 빈자리 확률). 이 값이 기준값보다 크면 '만차 위험'으로 경고해요(ml/train_eval.py)
        "full_risk": None if threshold is None else {"threshold": threshold, "beta": te.FULL_BETA, "validation": threshold_val},
    }
    ASSET_MODEL.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    cases = golden_cases(raw, lots, excluded, meta, load_table(), reg, clf)
    GOLDEN.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    n_nodes = sum(len(t["f"]) for m in (meta["regressor"], meta["classifier"]) if m for t in m["trees"])
    print(f"모델: {ASSET_MODEL.relative_to(ds.ROOT)} ({ASSET_MODEL.stat().st_size / 1_048_576:.2f}MB, 노드 {n_nodes:,}개, 주차장 {len(lots)}곳)")
    print(f"정답 사례: {GOLDEN.relative_to(ds.ROOT)} ({len(cases)}개, 예측 {sum(c['free'] is not None for c in cases)}개, "
          f"만차 위험 {sum(bool(c['full_warning']) for c in cases)}개)")
    print(f"만차 위험 기준값: {threshold} (검증 기간 {threshold_val})")


if __name__ == "__main__":
    main()
