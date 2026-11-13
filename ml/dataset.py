"""수집한 실시간 주차 기록으로 빈자리 예측 학습 데이터를 만듭니다.

샘플 하나 = (주차장, 지금 시각 t, 도착까지 남은 시간 h) → 도착 시각 t+h의 주차 대수예요.
입력은 차 안에서도 구할 수 있는 값만 써요: 주차장 정보, 시각·요일, 지금 값, 최근 15·30분 변화, 패턴표.
패턴은 앱 패턴표(datapipe/export_app_assets.py)와 같은 칸·규칙으로 계산하되, t가 속한 날짜보다 앞선 날짜의 기록만 써서
미래 정보가 섞이지 않아요. 차 안에서 같은 입력을 만드는 방법은 ml/export_model.py의 device_features를 보세요.
"""

import datetime
import json
import os
import pathlib
import re
import sqlite3
import sys
import warnings

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE_DB = ROOT / "data" / "seoul_parking.db"
META_CACHE = ROOT / "data" / "ml" / "lots_meta.json"
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "collector"))
import parking_core as core  # noqa: E402  앱과 같은 패턴 규칙(앞뒤 15분, 지난 28일, 최소 3일)

HORIZONS = (5, 10, 15, 20, 30, 45, 60)       # 학습에 쓰는 도착까지 남은 시간(분)
MATCH_TOL = pd.Timedelta(seconds=150)        # 수집 간격이 5분이라 앞뒤 2분 30초 안의 기록을 같은 시각으로 봐요
SLOT_MIN = 15                                # 앱 패턴표 칸 크기(분), 하루 96칸
FROZEN_CHANGES_PER_DAY = core.FROZEN_CHANGES_PER_DAY  # 하루 평균 이보다 적게 바뀐 주차장은 값이 멈춘 것으로 봐요(앱과 같은 기준)
BUS_ONLY = re.compile(r"관광버스|버스전용")    # 승용차가 못 쓰는 주차장(앱 안내에서도 빼요)
# 수집 기간 앞뒤 공휴일 (대체공휴일 포함)
HOLIDAYS = {datetime.date(2026, 10, 3), datetime.date(2026, 10, 5), datetime.date(2026, 10, 9),
            datetime.date(2026, 12, 25), datetime.date(2027, 1, 1)}


def load_snapshots(db=LIVE_DB):
    """수집 DB의 기록을 (lot, t, total, parked, occ) 표로 읽어요. t는 수집 시각이에요."""
    with sqlite3.connect(db) as conn:
        df = pd.read_sql_query("SELECT pklt_cd AS lot, collected_at, total, parked FROM snapshots", conn)
        names = dict(conn.execute("SELECT pklt_cd, name FROM lots"))
    df["t"] = pd.to_datetime(df.pop("collected_at"))
    df = df[df.total > 0].copy()
    df["parked"] = df.parked.clip(lower=0, upper=df.total)
    df["occ"] = df.parked / df.total
    return df.sort_values(["lot", "t"]).reset_index(drop=True), names


def lot_meta(lots):
    """주차장별 노상 여부를 돌려줘요. 처음 한 번 서울시 API(GetParkingInfo)로 받아 data/ml/에 저장해 둬요."""
    meta = json.loads(META_CACHE.read_text(encoding="utf-8")) if META_CACHE.exists() else {}
    if any(lot not in meta for lot in lots):
        try:
            import seoul_parking_collector as collector
            collector.load_env(ROOT / ".env")
            rows = collector.fetch_all(os.environ["SEOUL_OPEN_API_KEY"], "GetParkingInfo")
            meta.update({r["PKLT_CD"]: {"onstreet": r.get("PKLT_TYPE") == "NS"} for r in rows})
            META_CACHE.parent.mkdir(parents=True, exist_ok=True)
            META_CACHE.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:  # 키나 통신이 없으면 노상 여부 없이 진행해요
            print(f"주차장 종류를 받지 못했어요({e}). 노상 여부는 비워 둘게요")
    return {lot: meta.get(lot, {}).get("onstreet") for lot in lots}


def excluded_lots(df, names):
    """학습·평가에서 뺄 주차장과 이유예요: 값이 멈춘 곳, 버스 전용."""
    days = max((df.t.max() - df.t.min()).total_seconds() / 86400, 1.0)
    changes = df.groupby("lot").parked.apply(lambda s: int((s.diff().fillna(0) != 0).sum()))
    out = {}
    for lot, n in changes.items():
        name = names.get(lot, lot)
        if BUS_ONLY.search(name):
            out[lot] = (name, "버스 전용", n)
        elif n / days < FROZEN_CHANGES_PER_DAY:
            out[lot] = (name, f"값이 멈춤({n}번 바뀜)", n)
    return out


def _at(base, df, minutes, cols):
    """각 기록에서 minutes분 뒤(음수면 앞) 시각에 가장 가까운 같은 주차장 기록을 붙여요. 없으면 NaN이에요."""
    query = base[["lot", "t"]].assign(tq=base.t + pd.Timedelta(minutes=minutes)).sort_values("tq")
    ref = df[["lot", "t"] + cols].rename(columns={"t": "tq"}).sort_values("tq")
    got = pd.merge_asof(query, ref, on="tq", by="lot", tolerance=MATCH_TOL, direction="nearest")
    return got.set_index(["lot", "t"])[cols].reindex(pd.MultiIndex.from_frame(base[["lot", "t"]]))


def daily_medians(df):
    """주차장·날짜마다 패턴표 칸(15분, 하루 96칸)별로 칸 시작 시각 앞뒤 15분 안 빈자리의 중앙값을 구해요."""
    grid = np.arange(0, 1440, SLOT_MIN)
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # 기록이 없는 칸은 NaN으로 남겨요
        for lot, g in df.groupby("lot"):
            rows = {}
            for day, gd in g.groupby(g.t.dt.date):
                minute = (gd.t.dt.hour * 60 + gd.t.dt.minute).to_numpy()
                diff = np.abs(grid[:, None] - minute[None, :])
                diff = np.minimum(diff, 1440 - diff)
                free = (gd.total - gd.parked).to_numpy().astype(float)
                vals = np.where(diff <= core.PATTERN_WINDOW_MIN, free[None, :], np.nan)
                rows[day] = np.nanmedian(vals, axis=1)
            out[lot] = (np.array(list(rows)), np.vstack(list(rows.values())))
    return out


def attach_patterns(samples, medians):
    """패턴 값을 붙여요. 기준 날짜보다 앞선 지난 28일 중 같은 요일 유형(평일/주말) 날짜들의 중앙값을 반올림한 빈자리를
    지금 총 주차면으로 나눠 점유율로 바꿔요. 앱이 패턴표 한 칸(free, days)으로 계산하는 값과 같아요."""
    pat_now = np.full(len(samples), np.nan)
    pat_target = np.full(len(samples), np.nan)
    pat_days = np.zeros(len(samples), dtype=int)
    lookback = datetime.timedelta(days=core.PATTERN_DAYS)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for lot, idx in samples.groupby("lot").indices.items():
            if lot not in medians:
                continue
            dates, values = medians[lot]
            weekend_of = np.array([d.weekday() >= 5 for d in dates])
            cache = {}

            def vector(anchor, weekend):
                if (anchor, weekend) not in cache:
                    ok = (dates < anchor) & (dates >= anchor - lookback) & (weekend_of == weekend)
                    sub = values[ok] if ok.any() else np.full((1, values.shape[1]), np.nan)
                    cache[anchor, weekend] = (np.round(np.nanmedian(sub, axis=0)), (~np.isnan(sub)).sum(axis=0))
                return cache[anchor, weekend]

            sub = samples.iloc[idx]
            for (anchor, weekend), rows in sub.groupby(["anchor_date", "weekend"]).indices.items():
                med, cnt = vector(anchor, weekend)
                slots = sub.slot_target.to_numpy()[rows]
                pat_target[idx[rows]] = 1 - med[slots] / sub.total.to_numpy()[rows]
                pat_days[idx[rows]] = cnt[slots]
            for (anchor, weekend), rows in sub.groupby(["anchor_date", "weekend_now"]).indices.items():
                med, _ = vector(anchor, weekend)
                pat_now[idx[rows]] = 1 - med[sub.slot_now.to_numpy()[rows]] / sub.total.to_numpy()[rows]
    samples["pat_now"], samples["pat_target"], samples["pat_days"] = pat_now, pat_target, pat_days
    samples["pat_delta"] = samples.pat_target - samples.pat_now


def build_samples(df, horizons=HORIZONS):
    """기록마다 도착까지 남은 시간 h별 샘플을 만들어요. 도착 시각의 기록이 없는 샘플(수집이 빈 구간)은 빼요."""
    base = df.copy()
    with_free = df.assign(free=df.total - df.parked)
    for back in (15, 30):
        # 앱은 지난 기록의 빈자리만 갖고 있어서, 지금 총 주차면으로 점유율을 계산해요
        past_free = _at(base, with_free, -back, ["free"]).free.to_numpy()
        base[f"occ_m{back}"] = np.clip(1 - past_free / base.total.to_numpy(), 0, 1)
    parts = []
    for h in horizons:
        target = _at(base, df, h, ["total", "parked"])
        part = base.assign(h=h, target_total=target.total.to_numpy(), target_parked=target.parked.to_numpy())
        parts.append(part.dropna(subset=["target_parked"]))
    s = pd.concat(parts, ignore_index=True)
    s["t_target"] = s.t + pd.to_timedelta(s.h, unit="min")
    s["free"] = s.total - s.parked
    s["target_free"] = (s.target_total - s.target_parked).astype(int)
    s["d15"] = s.occ - s.occ_m15
    s["d30"] = s.occ - s.occ_m30
    s["tod"] = s.t.dt.hour * 60 + s.t.dt.minute
    s["tod_target"] = s.t_target.dt.hour * 60 + s.t_target.dt.minute
    s["dow"] = s.t_target.dt.weekday
    s["weekend"] = s.dow >= 5                    # 앱 패턴 규칙처럼 도착 시각의 요일로 정해요
    s["weekend_now"] = s.t.dt.weekday >= 5
    s["holiday"] = s.t_target.dt.date.isin(HOLIDAYS)
    s["anchor_date"] = s.t.dt.date
    s["slot_now"] = (np.round(s.tod / SLOT_MIN).astype(int)) % (1440 // SLOT_MIN)
    s["slot_target"] = (np.round(s.tod_target / SLOT_MIN).astype(int)) % (1440 // SLOT_MIN)
    attach_patterns(s, daily_medians(df))
    return s


def load(db=LIVE_DB, horizons=HORIZONS):
    """기록을 읽고, 뺄 주차장을 거르고, 샘플을 만들어 (samples, 제외 목록, 원본 기록)을 돌려줘요."""
    df, names = load_snapshots(db)
    excluded = excluded_lots(df, names)
    kept = df[~df.lot.isin(excluded)]
    samples = build_samples(kept, horizons)
    lots = sorted(kept.lot.unique())
    onstreet = lot_meta(lots)
    samples["lot_idx"] = samples.lot.map({lot: i for i, lot in enumerate(lots)})
    samples["onstreet"] = samples.lot.map(onstreet).astype(float)
    samples["name"] = samples.lot.map(names)
    return samples, excluded, df
