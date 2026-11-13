#!/usr/bin/env python3
"""빈자리 예측 모델을 학습하고, 단순 예측(기준선)과 비교한 리포트를 씁니다.

    .venv/bin/python ml/train_eval.py                  # 기록 전체로 학습·평가 → ml/reports/latest.md
    .venv/bin/python ml/train_eval.py --test-days 7    # 마지막 7일을 시험 기간으로

비교하는 방법
  - 기준선 ① 지금 값 유지: 도착할 때도 지금 빈자리가 그대로라고 봐요
  - 기준선 ② 패턴표: 지난 28일 중 같은 요일 유형·시간대(앞뒤 15분)의 날짜별 중앙값. 3일 이상 있어야 써요(앱과 같은 규칙)
  - 참고 지금 앱 방식: 패턴표 → 없으면 최근 30분 추세 → 없으면 지금 값 (앱 로직을 근사)
  - 모델: 결정트리 앙상블(HistGradientBoosting)과 작은 신경망(MLP). 둘 다 '지금부터 도착까지 점유율 변화'를 예측해요
  - 빈자리 있음/없음: 결정트리 앙상블 분류 모델로 '도착했을 때 빈자리가 1면 이상 있을 확률'을 예측해요
  - 만차 판단(대표 지표): 만차 위험 = max(지금 만차면 1, 1 - 빈자리 확률). 학습 기간 마지막 날에서 precision이 '지금 만차만 보기'보다
    5%p 넘게 낮지 않은 값 중 F2(recall을 precision보다 두 배 무겁게 보는 점수)가 가장 높은 기준값을 정하고, 시험 기간의 precision·recall·F1·F2를 재요.
    운전자에게는 자리가 없어 돌아가는 쪽(만차를 놓치는 것)이 더 큰 손해라서요

시험 기간은 시간 순서로 맨 뒤를 떼어 쓰고, 학습에는 도착 시각까지 시험 기간 전에 끝난 샘플만 써요.
"""

import argparse
import datetime
import json
import pathlib
import pickle
import sys

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dataset as ds  # noqa: E402

REPORT_DIR = ds.ROOT / "ml" / "reports"
MODEL_DIR = ds.ROOT / "data" / "ml" / "models"
EVAL_HORIZONS = (10, 20, 30)
SIZE_BUCKETS = ((0, 50, "50면 미만"), (50, 300, "50~300면"), (300, 10**6, "300면 이상"))
GBDT_FEATURES = ["lot_idx", "total", "onstreet", "dow", "weekend", "holiday", "tod", "tod_target", "h",
                 "occ", "d15", "d30", "pat_now", "pat_target", "pat_delta", "pat_days"]
MLP_NUMERIC = ["total", "onstreet", "weekend", "holiday", "h", "occ", "d15", "d30",
               "pat_now", "pat_target", "pat_delta", "pat_days", "tod_sin", "tod_cos", "tgt_sin", "tgt_cos"]
MLP_CATEGORY = ["dow"]  # 주차장 원핫은 넣지 않아요. 기록이 적을 때 주차장별 평일 흐름을 외워 주말에 크게 틀렸어요(10/25)
WEEKDAY_KO = "월화수목금토일"
FULL_BETA = 2.0  # F-beta의 beta. 2면 만차를 놓치는 것(recall)을 헛경보(precision)보다 두 배 무겁게 봐요
FULL_TH_GRID = np.round(np.arange(0.05, 0.951, 0.01), 2)  # 기준값 후보. 너무 낮거나 높은 값은 쓰지 않아요
FULL_PRECISION_MARGIN = 0.05  # 기준값의 precision은 '지금 만차만 보기'보다 이만큼까지만 낮아도 돼요. F2는 낮은 기준값 쪽에서 평평해서
# 하한이 없으면 헛경보가 크게 늘어요. 고정 하한(예: 75%)은 만차를 맞히기 어려운 주에는 아예 못 채워서 그 기간의 '지금 만차만 보기'에 맞춰요


# ── 모델 ────────────────────────────────────────────────

def gbdt():
    return HistGradientBoostingRegressor(loss="absolute_error", learning_rate=0.05, max_iter=400, max_leaf_nodes=31,
                                         categorical_features=[0], early_stopping=False, random_state=0)


def mlp():
    numeric = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler())
    pre = ColumnTransformer([("num", numeric, MLP_NUMERIC),
                             ("cat", OneHotEncoder(handle_unknown="ignore"), MLP_CATEGORY)])
    return make_pipeline(pre, MLPRegressor(hidden_layer_sizes=(64, 32), alpha=1e-3, early_stopping=True,
                                           max_iter=200, random_state=0))


def classifier():
    # 조기 멈춤은 꺼요. 켜 두면 기록이 조금만 늘어도 학습량이 달라져 결과가 크게 흔들렸어요(10/27)
    return HistGradientBoostingClassifier(learning_rate=0.05, max_iter=300, categorical_features=[0], early_stopping=False, random_state=0)


def add_cyclic(s):
    for col in ("weekend", "holiday"):
        s[col] = s[col].astype(float)
    for col, name in (("tod", "tod"), ("tod_target", "tgt")):
        angle = 2 * np.pi * s[col] / 1440
        s[f"{name}_sin"], s[f"{name}_cos"] = np.sin(angle), np.cos(angle)


def full_risk(p_free, free_now):
    """만차 위험 점수예요. 지금 만차면 1, 아니면 모델이 본 만차 확률(1 - 빈자리 확률)이에요."""
    return np.maximum((np.asarray(free_now) == 0).astype(float), 1 - np.asarray(p_free))


def f_beta(precision, recall, beta=FULL_BETA):
    if not precision or not recall:
        return 0.0
    return (1 + beta**2) * precision * recall / (beta**2 * precision + recall)


def flag_scores(flag, target_full):
    """경고(flag)를 만차(target_full)와 견줘 precision·recall·F1·F2를 돌려줘요."""
    tp = int((flag & target_full).sum())
    precision = tp / flag.sum() if flag.any() else None
    recall = tp / target_full.sum() if target_full.any() else None
    return {"precision": precision, "recall": recall, "f1": f_beta(precision, recall, 1.0), "f2": f_beta(precision, recall, 2.0)}


def full_threshold(train, features):
    """학습 기간 마지막 사흘(기록이 2주보다 짧으면 하루, 3일보다 짧으면 마지막 25%)을 떼어 분류 모델을 다시 학습하고,
    떼어 둔 기간에서 precision이 하한('지금 만차만 보기'의 precision − 5%p) 이상인 값 중 F2가 가장 높은 만차 위험 기준값을 정해요
    (같으면 더 높은 값, 즉 경고를 덜 하는 쪽).
    하한을 채우는 값이 없으면 precision이 가장 높은 값을 써요. 시험 기간은 보지 않아요.
    (기준값, 떼어 둔 기간의 점수)를 돌려줘요."""
    end = train.t_target.max()
    span = (end - train.t.min()).total_seconds() / 86400
    val_start = end - pd.Timedelta(days=3 if span >= 14 else 1 if span >= 3 else span * 0.25)  # 하루만 쓰면 요일에 따라 기준값이 크게 흔들려요
    fit_part, val = train[train.t_target < val_start], train[train.t >= val_start]
    y = (fit_part.target_free >= 1).astype(int)
    if y.nunique() < 2 or val.empty:
        return None, None
    risk = full_risk(classifier().fit(fit_part[features], y).predict_proba(val[features])[:, 1], val.free.to_numpy())
    target_full = val.target_free.to_numpy() == 0
    scored = [(th, flag_scores(risk > th, target_full)) for th in FULL_TH_GRID]
    now_precision = flag_scores(val.free.to_numpy() == 0, target_full)["precision"]
    floor = (now_precision or 0) - FULL_PRECISION_MARGIN
    allowed = [x for x in scored if (x[1]["precision"] or 0) >= floor]
    th, score = (max(allowed, key=lambda x: (x[1]["f2"], x[0])) if allowed
                 else max(scored, key=lambda x: (x[1]["precision"] or 0, x[0])))
    return float(th), {"days": f"{val_start:%m-%d %H:%M}~", "precision_floor": float(floor), **score}


def to_free(s, delta):
    """점유율 변화 예측을 도착 시 빈자리(면)로 바꿔요. 화면처럼 정수로 반올림해요."""
    occ = np.clip(s.occ.to_numpy() + delta, 0, 1)
    return np.round(s.total.to_numpy() * (1 - occ))


# ── 기준선 ──────────────────────────────────────────────

def baselines(s):
    free_now = s.free.to_numpy().astype(float)
    total = s.total.to_numpy()
    pattern = np.where(s.pat_days >= ds.core.PATTERN_MIN_DAYS, np.round(total * (1 - s.pat_target)), np.nan)
    # 앱의 추세: 최근 30분(없으면 15분) 변화 속도를 도착 시간만큼 이어 붙여요
    slope = np.where(s.d30.notna(), -s.d30 * total / 30, np.where(s.d15.notna(), -s.d15 * total / 15, np.nan))
    trend = np.clip(np.round(free_now + slope * s.h.to_numpy()), 0, total)
    app = np.where(~np.isnan(pattern), pattern, np.where(~np.isnan(trend), trend, free_now))
    return {"now": free_now, "pattern": pattern, "app": app}


# ── 평가 ────────────────────────────────────────────────

def mae(pred, truth, mask=None):
    ok = ~np.isnan(pred) if mask is None else mask & ~np.isnan(pred)
    return (float(np.mean(np.abs(pred[ok] - truth[ok]))), int(ok.sum())) if ok.any() else (None, 0)


def evaluate(test, preds):
    truth = test.target_free.to_numpy().astype(float)
    h = test.h.to_numpy()
    total = test.total.to_numpy()
    has_pattern = ~np.isnan(preds["pattern"])  # 기준선 ②와 같은 샘플에서 비교하려고 따로 재요
    out = {}
    for name, pred in preds.items():
        row = {f"h{x}": mae(pred, truth, h == x) for x in EVAL_HORIZONS}
        row["all"] = mae(pred, truth)
        row["on_pattern"] = mae(pred, truth, has_pattern)
        row["on_pattern30"] = mae(pred, truth, has_pattern & (h == 30))
        row["occ_pp_all"] = (float(np.nanmean(np.abs(pred - truth) / total)) * 100) if not np.isnan(pred).all() else None
        # 적중률: 오차가 2면 또는 총 주차면의 5% 중 큰 값 안에 든 샘플 비율이에요(예측 가능한 샘플 기준)
        ok = ~np.isnan(pred)
        row["hit_all"] = float(np.mean(np.abs(pred[ok] - truth[ok]) <= np.maximum(2, 0.05 * total[ok])) * 100) if ok.any() else None
        for lo, hi, label in SIZE_BUCKETS:
            row[f"size30_{label}"] = mae(pred, truth, (h == 30) & (total >= lo) & (total < hi))
        out[name] = row
    return out


def evaluate_full(test, prob, threshold):
    """도착 시 만차를 '만차 위험'으로 잡았는지예요.

    - 만차 포착: 도착 때 만차인 샘플 중 경고한 비율 (지금도 만차인 것과 도착 전에 차는 '전환'을 함께 봐요)
    - 헛경보: 도착 때 자리가 있는데 경고한 비율
    - 자리 남 맞힘: 지금 만차인데 도착 때 자리가 나는 샘플 중 경고하지 않은 비율
    """
    target_full = test.target_free.to_numpy() == 0
    free_now = test.free.to_numpy()
    now_full = free_now == 0
    trans, frees = target_full & ~now_full, ~target_full & now_full
    flags = {"now_full": now_full}
    if prob is not None:
        flags["p50"] = prob < 0.5
        if threshold is not None:
            flags["rule"] = full_risk(prob, free_now) > threshold
    rate = lambda a: float(np.mean(a)) if a.size else None  # noqa: E731
    out = {"rows": len(target_full), "full_rows": int(target_full.sum()), "trans_rows": int(trans.sum()),
           "frees_rows": int(frees.sum()), "threshold": threshold}
    for name, f in flags.items():
        out[name] = {**flag_scores(f, target_full), "trans": rate(f[trans]), "false_alarm": rate(f[~target_full]),
                     "frees_ok": rate(~f[frees])}
    if prob is not None:
        out["brier"] = float(np.mean((prob - ~target_full) ** 2))
    return out


# ── 리포트 ──────────────────────────────────────────────

LABELS = {"now": "기준선 ① 지금 값 유지", "pattern": "기준선 ② 패턴표", "app": "참고: 지금 앱 방식",
          "gbdt": "모델: 결정트리 앙상블", "mlp": "모델: 작은 신경망", "clf": "모델: 빈자리 확률(결정트리 앙상블)"}
SHORT = {"gbdt": "결정트리 앙상블", "mlp": "작은 신경망"}  # 문장 안에서 쓰는 이름
FULL_LABELS = {"now_full": "지금 만차만 보기", "p50": "빈자리 확률 50% 미만 (예전 앱 기준)",
               "rule": "새 규칙: 지금 만차 + 모델 위험 (기준값 {th:.3f})"}


def fmt(v):
    return "—" if v is None or v[0] is None else f"{v[0]:.2f}"


def days_text(dates):
    weekday = sum(1 for d in dates if d.weekday() < 5)
    return f"{len(dates)}일(평일 {weekday}일, 주말 {len(dates) - weekday}일)"


def write_report(path, info, metrics, full, excluded):
    names = [k for k in ("now", "pattern", "app", "gbdt", "mlp") if k in metrics]
    best = min((k for k in ("gbdt", "mlp") if k in metrics), key=lambda k: metrics[k]["all"][0])
    base_now = metrics["now"]["all"][0]
    gain = (base_now - metrics[best]["all"][0]) / base_now * 100 if base_now else 0
    L = []
    L.append("# 빈자리 예측 평가 리포트\n")
    L.append(f"> {info['made']}에 `ml/train_eval.py`로 만들었어요. 숫자는 시험 기간의 실제 기록과 비교한 **도착 시 빈자리 평균 오차(면)** 이고, 작을수록 좋아요.")
    if info["span_days"] < 21:
        L.append(f">\n> **예비 결과예요.** 기록이 {info['span_days']:.1f}일뿐이라 학습 {info['train_days']}, 시험 {info['test_days']}이에요. "
                 "같은 요일 유형의 지난 기록이 3일 미만이라 기준선 ② 패턴표는 아직 계산할 수 없어요. 3주 이상 쌓이면 다시 돌려 주세요.")
    L.append("\n## 데이터")
    L.append(f"- 기록: {info['first']} ~ {info['last']}, {info['records']:,}건 (5분 간격)")
    L.append(f"- 주차장: 실시간 {info['lots_all']}곳 중 {info['lots_used']}곳 사용. 값이 멈춘 곳 {info['n_frozen']}곳과 버스 전용 {info['n_bus']}곳은 뺐어요(맨 아래 목록)")
    L.append(f"- 샘플: 학습 {info['n_train']:,}개, 시험 {info['n_test']:,}개. 샘플 하나는 (주차장, 지금 시각, 도착까지 남은 시간 5~60분)이에요")
    L.append(f"- 시험 기간: {info['test_start']}부터 끝까지 ({info['test_days']}). 학습에는 도착 시각이 이 시각 전인 샘플만 썼어요")
    L.append("- 수집이 빈 구간(맥 잠자기 등)은 도착 시각의 기록이 없어서 샘플에서 빠져요")
    L.append(f"- 패턴 값이 있는 샘플: 학습 {info['pat_train']:.0f}%, 시험 {info['pat_test']:.0f}%. 지난 같은 요일 유형의 기록이 있어야 생겨요")
    L.append("\n## 만차 판단 (대표 지표)")
    L.append("운전자에게는 자리가 없어 돌아가는 쪽이 더 큰 손해라서, 도착 때 만차인 주차장을 '만차 위험'으로 잡는지를 대표 지표로 봐요.")
    th = full["threshold"]
    L.append(f"시험 샘플 {full['rows']:,}개 중 도착 때 만차는 {full['full_rows']:,}개예요(지금도 만차 {full['full_rows'] - full['trans_rows']:,}개, "
             f"도착 전에 차는 전환 {full['trans_rows']:,}개). 지금 만차인데 도착 때 자리가 나는 샘플은 {full['frees_rows']:,}개예요.\n")
    L.append("만차를 놓치는 손해가 더 커서 **recall(만차 포착)을 precision보다 두 배 무겁게 보는 F2**를 대표 점수로 써요.\n")
    L.append("| 방법 | precision | recall (만차 포착) | F1 | **F2** | 전환 포착 | 헛경보 | 자리 남 맞힘 |")
    L.append("|---|---|---|---|---|---|---|---|")
    pct = lambda v: "—" if v is None else f"{v * 100:.1f}%"  # noqa: E731
    for k in ("now_full", "p50", "rule"):
        if k in full:
            r = full[k]
            L.append(f"| {FULL_LABELS[k].format(th=th)} | {pct(r['precision'])} | {pct(r['recall'])} | {r['f1']:.3f} | **{r['f2']:.3f}** | "
                     f"{pct(r['trans'])} | {pct(r['false_alarm'])} | {pct(r['frees_ok'])} |")
    val = info.get("full_val") or {}
    val_text = (f" 그 기간({val['days']})의 점수는 precision {val['precision'] * 100:.1f}%, recall {val['recall'] * 100:.1f}%, F2 {val['f2']:.3f}였어요."
                if val.get("precision") is not None and val.get("recall") is not None else "")
    L.append(f"\n새 규칙의 기준값은 학습 기간 마지막 부분을 떼어 precision이 '지금 만차만 보기'보다 {FULL_PRECISION_MARGIN * 100:.0f}%p 넘게 낮지 않은 값 중 F2가 가장 높은 값({FULL_TH_GRID[0]}~{FULL_TH_GRID[-1]}, 0.01 간격)으로 정했고, "
             f"시험 기간은 보지 않았어요.{val_text}"
             " 새 규칙은 지금 만차인 곳을 늘 경고해서 '자리 남 맞힘'은 0%예요(돌아가는 위험을 줄이는 쪽을 골랐어요)."
             + (f" 빈자리 확률 모델의 확률 오차(Brier)는 {full['brier']:.4f}예요." if "brier" in full else ""))
    L.append("\n## 도착 시 빈자리 평균 오차 (면)")
    L.append("| 방법 | 10분 뒤 | 20분 뒤 | 30분 뒤 | 5~60분 전체 | 점유율 기준(%p) | 적중률 |")
    L.append("|---|---|---|---|---|---|---|")
    for k in names:
        m = metrics[k]
        pp = "—" if m["occ_pp_all"] is None else f"{m['occ_pp_all']:.2f}"
        hit = "—" if m["hit_all"] is None else f"{m['hit_all']:.1f}%"
        L.append(f"| {LABELS[k]} | {fmt(m['h10'])} | {fmt(m['h20'])} | {fmt(m['h30'])} | {fmt(m['all'])} | {pp} | {hit} |")
    L.append("\n적중률은 5~60분 전체 샘플 중 오차가 '2면 또는 총 주차면의 5% 중 큰 값' 안에 든 비율이에요. "
             "기준선 ② 패턴표는 계산할 수 있는 샘플에서만 쟀어요.")
    covered = metrics["pattern"]["on_pattern"][1]
    if covered:
        L.append(f"\n### 기준선 ② 패턴표를 계산할 수 있는 샘플만 ({covered:,}개, 시험 샘플의 {covered / info['n_test'] * 100:.1f}%)")
        L.append("| 방법 | 30분 뒤 | 5~60분 전체 |")
        L.append("|---|---|---|")
        for k in names:
            L.append(f"| {LABELS[k]} | {fmt(metrics[k]['on_pattern30'])} | {fmt(metrics[k]['on_pattern'])} |")
    L.append("\n## 주차장 크기별 (30분 뒤 예측, 면)")
    L.append("| 방법 | " + " | ".join(label for _, _, label in SIZE_BUCKETS) + " |")
    L.append("|---|" + "---|" * len(SIZE_BUCKETS))
    for k in names:
        L.append(f"| {LABELS[k]} | " + " | ".join(fmt(metrics[k][f'size30_{label}']) for _, _, label in SIZE_BUCKETS) + " |")
    L.append("\n## 해석")
    if "rule" in full:
        r, n0 = full["rule"], full["now_full"]
        L.append(f"- 만차 판단: 새 규칙은 F2 {r['f2']:.3f}(지금 만차만 보면 {n0['f2']:.3f})이고, 도착 때 만차를 {r['recall'] * 100:.1f}% 잡아요"
                 f"(지금 만차만 보면 {n0['recall'] * 100:.1f}%). 경고 중 실제 만차는 {r['precision'] * 100:.1f}%, 도착 전에 차는 전환은 {r['trans'] * 100:.1f}%예요.")
    if gain > 0:
        L.append(f"- 5~60분 전체에서 {SHORT[best]} 모델이 기준선 ① 지금 값 유지보다 오차가 {gain:.1f}% 작아요.")
    else:
        L.append(f"- 아직 {SHORT[best]} 모델도 기준선 ① 지금 값 유지보다 낫지 않아요(차이 {gain:.1f}%). 이 상태로는 'AI 예측'이라고 부르지 않아요.")
    for k in ("gbdt", "mlp"):
        if k != best and metrics[k]["all"][0] >= base_now:
            reason = (f" 학습과 시험에서 패턴 값이 있는 비율이 크게 달라(학습 {info['pat_train']:.0f}%, 시험 {info['pat_test']:.0f}%) "
                      "패턴 값에 기댄 예측이 빗나간 것으로 보여요." if abs(info["pat_train"] - info["pat_test"]) > 30 else "")
            L.append(f"- {SHORT[k]} 모델은 아직 기준선 ① 지금 값 유지보다 나빠요.{reason} 기록이 쌓이면 다시 봐요.")
    if metrics["pattern"]["on_pattern"][1]:
        mp, mb = metrics["pattern"]["on_pattern"][0], metrics[best]["on_pattern"][0]
        L.append(f"- 기준선 ② 패턴표를 계산할 수 있는 샘플({metrics['pattern']['on_pattern'][1]:,}개)에서는 {SHORT[best]} 모델 {mb:.2f}면, "
                 f"패턴표 {mp:.2f}면이에요({'모델이 더 작아요' if mb < mp else '아직 패턴표가 더 작거나 같아요'}). 샘플이 적으면 참고만 해요.")
    if metrics["pattern"]["all"][0] is None:
        L.append("- 기준선 ② 패턴표와의 비교는 기록이 3주 이상 쌓인 뒤에 할 수 있어요.")
    L.append("- 'AI' 표현은 시험 기간에서 두 기준선을 모두 이길 때만 써요(이 프로젝트의 기준).")
    L.append("\n## 뺀 주차장")
    L.append("| 주차장 | 이유 |")
    L.append("|---|---|")
    for lot, (name, reason, _) in sorted(excluded.items(), key=lambda x: x[1][1]):
        L.append(f"| {name} | {reason} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


# ── 실행 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="빈자리 예측 학습·평가")
    parser.add_argument("--db", type=pathlib.Path, default=ds.LIVE_DB)
    parser.add_argument("--test-days", type=float, help="시험 기간(일). 기본: 기록 3주 이상이면 7, 아니면 1")
    parser.add_argument("--max-train", type=int, default=600_000, help="학습 샘플 상한(넘으면 무작위로 골라요)")
    parser.add_argument("--out", type=pathlib.Path, default=REPORT_DIR / "latest.md")
    args = parser.parse_args()

    samples, excluded, raw = ds.load(args.db)
    add_cyclic(samples)
    first, last = raw.t.min(), raw.t.max()
    span = (last - first).total_seconds() / 86400
    test_days = args.test_days or (7 if span >= 21 else 1)
    test_start = samples.t.max() - pd.Timedelta(days=test_days)
    train = samples[samples.t_target < test_start]
    test = samples[samples.t >= test_start].reset_index(drop=True)
    if len(train) > args.max_train:
        train = train.sample(args.max_train, random_state=0)
    print(f"학습 {len(train):,}개, 시험 {len(test):,}개 (시험 시작 {test_start:%m-%d %H:%M})")

    y = train.target_parked / train.target_total - train.occ  # 지금부터 도착까지 점유율 변화
    models = {"gbdt": gbdt(), "mlp": mlp()}
    models["gbdt"].fit(train[GBDT_FEATURES], y)
    models["mlp"].fit(train[MLP_NUMERIC + MLP_CATEGORY], y * 100)
    has_free = (train.target_free >= 1).astype(int)
    clf = classifier().fit(train[GBDT_FEATURES], has_free) if has_free.nunique() == 2 else None

    preds = baselines(test)
    preds["gbdt"] = to_free(test, models["gbdt"].predict(test[GBDT_FEATURES]))
    preds["mlp"] = to_free(test, models["mlp"].predict(test[MLP_NUMERIC + MLP_CATEGORY]) / 100)
    prob = clf.predict_proba(test[GBDT_FEATURES])[:, 1] if clf is not None else None
    threshold, threshold_val = full_threshold(train, GBDT_FEATURES) if clf is not None else (None, None)
    metrics = evaluate(test, preds)
    full = evaluate_full(test, prob, threshold)

    dates = lambda frame: sorted(set(frame.t.dt.date))  # noqa: E731
    info = {
        "made": f"{datetime.datetime.now():%Y-%m-%d %H:%M}", "first": f"{first:%Y-%m-%d %H:%M}", "last": f"{last:%Y-%m-%d %H:%M}",
        "span_days": span, "records": len(raw), "lots_all": raw.lot.nunique(), "lots_used": samples.lot.nunique(),
        "n_frozen": sum(1 for v in excluded.values() if v[1] != "버스 전용"),
        "n_bus": sum(1 for v in excluded.values() if v[1] == "버스 전용"),
        "n_train": len(train), "n_test": len(test), "test_start": f"{test_start:%Y-%m-%d %H:%M}",
        "pat_train": train.pat_target.notna().mean() * 100, "pat_test": test.pat_target.notna().mean() * 100,
        "full_val": threshold_val,
        "train_days": days_text(dates(train)),
        "test_days": days_text(dates(test)) + ", " + "·".join(sorted({WEEKDAY_KO[d.weekday()] for d in dates(test)}, key=WEEKDAY_KO.index)) + "요일",
    }
    write_report(args.out, info, metrics, full, excluded)
    (args.out.with_suffix(".json")).write_text(json.dumps({"info": info, "mae": metrics, "full": full}, ensure_ascii=False, indent=1,
                                                          default=str), encoding="utf-8")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_DIR / "models.pkl", "wb") as f:
        pickle.dump({"models": models, "classifier": clf, "features": {"gbdt": GBDT_FEATURES, "mlp": MLP_NUMERIC + MLP_CATEGORY},
                     "lots": sorted(samples.lot.unique()), "made": info["made"]}, f)

    for k in ("now", "pattern", "app", "gbdt", "mlp"):
        print(f"  {LABELS[k]:<24} 30분 뒤 {fmt(metrics[k]['h30']):>6}면 · 전체 {fmt(metrics[k]['all']):>6}면")
    print(f"리포트: {args.out.relative_to(ds.ROOT)}")


if __name__ == "__main__":
    main()
